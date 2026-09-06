"""Browser SSH console — admin-only shells into the lab's host machines.

The dashboard already answers "is the Cytation PC up?"; this answers "let me
look at it". Each host tile under *Utils → Computers and Servers* links to a
page that shows the connection banner and, beneath it, a real terminal wired
to ``ssh`` running on the dashboard host.

Shape (three endpoints under ``/api/ssh``)::

    GET  /api/ssh/hosts      the whitelist + banner facts (admin)
    POST /api/ssh/session    mint a single-use, 30 s ticket for one host (admin)
    WS   /api/ssh/ws?ticket= redeem the ticket, spawn ssh in a PTY, pump bytes

**Why a ticket instead of the session cookie.** Next.js does not run
``middleware.ts`` reliably on a WebSocket upgrade, and Caddy's ``forward_auth``
famously answers an upgrade request with a bare 403 before any cookie check
(see the ``/xarm5/ws`` and ``/hermes/api/ws`` exemptions in
``deploy/Caddyfile.single-edge``). So the identity check happens over plain
HTTP on ``/session`` — where the middleware *does* run and inject a verified
``X-Auth-User`` / ``X-Auth-Role`` — and the socket presents a short-lived
bearer that is bound to (user, host) and dies on first use. Same pattern
GraphChat's ``ws_token`` and the Hermes dashboard already use on this edge.

**Who may open a shell: human admins only, never a machine principal.**
``web/src/middleware.ts`` verifies these paths with the session *cookie only*
(it does not forward ``X-Api-Key``), so an API-key principal cannot reach this
router at all, and :func:`_require_admin` re-checks the role here. This is
deliberate and load-bearing, not belt-and-braces:
``docs/AGENTIC_LAB_DESIGN.md`` Part II keeps the unattended ``lab-runner``
principal free of any terminal toolset precisely because it ingests
attacker-influenceable text (Slack), and its trust-tier table forbids the
host-ops fleet from running arbitrary shell. A shell sits *below* every safety
layer the lab has — claims, ``allowed_actions``, the four interlock layers,
the propose-only assistant — so it stays a human affordance. The attended
``lab-ops`` Hermes profile already has a local terminal under its own OS user
(``sdl2``, which holds the lab SSH keys); nothing here widens that.

**Credentials never live in this app.** The argv target is an alias from the
service user's ``~/.ssh/config`` (``cytation-pc``, ``uplc-pc``, …), so the key
file, login user and hostname stay in ssh's own config — this module only
names which alias an admin may reach. Host keys must already be in
``known_hosts``: we pass ``StrictHostKeyChecking=yes`` and ``BatchMode=yes``,
so an unknown host or a missing key fails fast and visibly instead of
prompting into a terminal nobody can answer.

Every session writes two ``ssh_session`` rows to ``equipment_events`` (mint,
end) with the actor, host, duration and exit code.

Environment:

``SSH_CONSOLE_ENABLED``          ``false`` disables the whole surface (404).
``SSH_CONSOLE_AUTHZ_ENFORCE``    ``false`` skips the admin check (local dev only).
``SSH_CONSOLE_MAX_SESSIONS``     concurrent shells across all admins (default 4).
``SSH_CONSOLE_IDLE_TIMEOUT_S``   silence before the shell is reaped (default 1800).
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import fcntl
import functools
import json
import logging
import os
import pty
import secrets
import shutil
import struct
import termios
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel

from .events import SSH_SESSION

logger = logging.getLogger("ac_dashboard.api.ssh_console")

# A ticket is redeemed by the browser within one round trip; 30 s is generous
# and short enough that a leaked URL in a log is worthless by the time anyone
# reads it. Single-use besides.
_TICKET_TTL_S = 30.0
_CONNECT_TIMEOUT_S = 10
_READ_CHUNK = 65536
# xterm.js will not ask for more than this; clamp anyway so a hostile client
# can't hand TIOCSWINSZ a nonsense struct.
_MAX_COLS, _MAX_ROWS = 1000, 500


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _max_sessions() -> int:
    try:
        return max(1, int(os.environ.get("SSH_CONSOLE_MAX_SESSIONS", "4")))
    except ValueError:
        return 4


def _idle_timeout_s() -> float:
    try:
        return max(60.0, float(os.environ.get("SSH_CONSOLE_IDLE_TIMEOUT_S", "1800")))
    except ValueError:
        return 1800.0


# --------------------------------------------------------------------------
# The host whitelist
# --------------------------------------------------------------------------
#
# Hand-maintained, exactly like the tile inventory it pairs with
# (`web/src/app/utils/computers/HostsPanel.tsx`) — a host machine is not
# equipment, so it has no `equipment.yaml` entry to read this from. Keep the
# ids identical on both sides: the tile's "SSH terminal" link is
# /utils/computers/ssh/<id>, and that <id> is looked up here.
#
# `target` MUST be an alias in the *service user's* ~/.ssh/config (or a bare
# user@host it can reach with an agent-less key). Adding a host here without
# the matching ssh_config stanza + known_hosts entry produces an honest
# connection failure in the terminal, not a silent fallback.


@dataclass(frozen=True)
class SshProfile:
    """One way to open a session on a host — the default login shell, a tmux
    attach-or-create, a WSL shell on a Windows PC. ``args`` is the remote
    command appended after the ssh target; the browser only ever names a
    profile ``id``, so the command surface stays a server-side whitelist
    exactly like the host list itself."""

    id: str
    label: str
    args: tuple[str, ...]
    description: str

    def public(self) -> dict[str, str]:
        # `args` deliberately withheld: the page renders buttons, it does not
        # need (and must never grow to trust) command strings.
        return {"id": self.id, "label": self.label, "description": self.description}


# Shared attach-or-create tmux session. One fixed name, deliberately: a
# second admin attaching lands in the SAME session (tmux mirrors the screen),
# which for a lab console is a feature — pair-debugging — not a leak; the
# whole surface is admins-only anyway.
_TMUX_ARGS = ("tmux", "new-session", "-A", "-s", "console")

_PROFILE_TMUX = SshProfile(
    id="tmux",
    label="tmux",
    args=_TMUX_ARGS,
    description=(
        "Attach-or-create the shared 'console' tmux session. Survives a "
        "closed tab or dropped connection — reconnect and reattach. A second "
        "admin attaching sees the same screen."
    ),
)

# Windows: the default shell over OpenSSH is cmd.exe; wsl.exe drops into the
# Ubuntu (WSL2) distro instead, cold-booting it on demand. tmux inside WSL
# gives the same persistence as on Linux — the detached session keeps the WSL
# VM alive, so it survives the browser going away.
_PROFILE_WSL = SshProfile(
    id="wsl",
    label="WSL",
    args=("wsl.exe",),
    description="Ubuntu (WSL2) shell instead of cmd.exe. Boots the distro on demand.",
)
_PROFILE_WSL_TMUX = SshProfile(
    id="wsl-tmux",
    label="WSL tmux",
    args=("wsl.exe", "-e", *_TMUX_ARGS),
    description=(
        "Attach-or-create the shared 'console' tmux session inside WSL. "
        "Survives disconnects (the detached session keeps the WSL VM alive)."
    ),
)


@dataclass(frozen=True)
class SshHost:
    id: str
    label: str
    kind: str
    hostname: str
    user: str
    target: str
    shell: str
    note: str
    #: First entry is the default (plain login shell).
    profiles: tuple[SshProfile, ...] = ()

    def profile(self, profile_id: str | None) -> SshProfile | None:
        if not profile_id:
            return self.profiles[0]
        for candidate in self.profiles:
            if candidate.id == profile_id:
                return candidate
        return None

    def public(self) -> dict[str, Any]:
        """Banner facts for the page. No secrets — the key file and any
        per-host ssh options stay in the service user's ~/.ssh/config."""
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "hostname": self.hostname,
            "user": self.user,
            "target": self.target,
            "shell": self.shell,
            "note": self.note,
            "profiles": [profile.public() for profile in self.profiles],
            # What an operator would type themselves, from the dashboard host…
            "ssh_command": f"ssh {self.target}",
            # …and the form that needs no ~/.ssh/config alias.
            "ssh_command_explicit": f"ssh {self.user}@{self.hostname}",
        }


SSH_HOSTS: tuple[SshHost, ...] = (
    SshHost(
        id="gaia",
        label="Central Server (gaia)",
        kind="Linux server",
        hostname="sdl2-server-gaia",
        user="sdl2",
        target="localhost",
        shell="bash",
        note=(
            "The dashboard's own host — the session loops back over ssh rather "
            "than inheriting the API service's systemd sandbox, so you get a "
            "normal login shell."
        ),
        profiles=(
            SshProfile(id="shell", label="Shell", args=(), description="Plain bash login shell."),
            _PROFILE_TMUX,
        ),
    ),
    SshHost(
        id="cytation-pc",
        label="Cytation PC",
        kind="Windows PC",
        hostname="sdl2-pc-03-cytation.tail6a1dd7.ts.net",
        user="sdl2",
        target="cytation-pc",
        shell="cmd.exe (Windows OpenSSH)",
        note=(
            "Hosts xarm, plateloc, both OT-2 gateways, the shaker, the Cytation "
            "and the BioStack. Service control is `C:\\SDL_Tools\\nssm.exe` — "
            "prefer the whitelisted host-ops surface for routine restarts."
        ),
        profiles=(
            SshProfile(id="cmd", label="cmd", args=(), description="Windows cmd.exe (the OpenSSH default shell)."),
            _PROFILE_WSL,
            _PROFILE_WSL_TMUX,
        ),
    ),
    SshHost(
        id="uplc-pc",
        label="UPLC PC",
        kind="Windows PC",
        hostname="sdl2-pc-06-uplc.tail6a1dd7.ts.net",
        user="sdl2",
        target="uplc-pc",
        shell="cmd.exe (Windows OpenSSH)",
        note=(
            "Hosts the UPLC-MS sidecar. The sidecar owns the run queue — do "
            "not restart it mid-campaign. The OT-2 complexation robot's USB-B "
            "cable stays plugged into this PC as a physical network fallback "
            "(the portproxy bridge itself was retired 2026-08-27; see ROADMAP)."
        ),
        profiles=(
            SshProfile(id="cmd", label="cmd", args=(), description="Windows cmd.exe (the OpenSSH default shell)."),
            _PROFILE_WSL,
            _PROFILE_WSL_TMUX,
        ),
    ),
    SshHost(
        id="gibbie-pc",
        label="Gibbie PC",
        kind="Windows PC",
        hostname="sdl2-pc-04.tail6a1dd7.ts.net",
        user="sdl2",
        target="gibbie-pc",
        shell="cmd.exe (Windows OpenSSH)",
        note=(
            "Drives the Gibbie multi-phase reaction bench (UR-3e arm, XPR "
            "balance, Opentrons Flex, IKA hotplate) and hosts the read-only "
            "sdl2-gibbie-server monitor (:8070). On the lab switch at "
            "192.168.254.79. The arm and balance are addressed on 192.168.1.x, "
            "a segment this PC has no interface on yet (see ROADMAP). Only "
            "cmd is offered until WSL is confirmed present."
        ),
        profiles=(
            SshProfile(id="cmd", label="cmd", args=(), description="Windows cmd.exe (the OpenSSH default shell)."),
        ),
    ),
    SshHost(
        id="lle-pc",
        label="LLE PC (Process Chemistry)",
        kind="Windows PC",
        hostname="sdl2-pc-00-lle.tail6a1dd7.ts.net",
        user="sdl2",
        target="lle-pc",
        shell="cmd.exe (Windows OpenSSH)",
        note=(
            "Drives the Process Chemistry platform: the HPLC through the "
            "Agilent software on this PC, plus the EasyMax, the MT balance and "
            "the UR5-CB3 on the lab switch (192.168.254.5 here; campus "
            "172.31.35.241). Lab-ops key not yet authorized on it, so the "
            "console cannot open until it is (DEVICE_PC_SETUP §2.4). Only cmd "
            "is offered until WSL is confirmed present."
        ),
        profiles=(
            SshProfile(id="cmd", label="cmd", args=(), description="Windows cmd.exe (the OpenSSH default shell)."),
        ),
    ),
)

HOSTS_BY_ID: dict[str, SshHost] = {h.id: h for h in SSH_HOSTS}


# --------------------------------------------------------------------------
# Authorization + tickets
# --------------------------------------------------------------------------


def _require_admin(request: Request) -> str:
    """Return the verified admin's identity, or raise.

    ``X-Auth-User`` / ``X-Auth-Role`` are injected by ``web/src/middleware.ts``
    after it validates the session against the ac_auth sidecar; it deletes any
    client-supplied copy first, so trusting them here is safe for the same
    reason ``control.py`` trusts them. The middleware verifies with the cookie
    only (no ``X-Api-Key`` forwarded), which is what keeps machine principals
    out; this check is the second, independent layer.
    """
    user = request.headers.get("x-auth-user")
    role = (request.headers.get("x-auth-role") or "").strip().lower()
    if not _env_flag("SSH_CONSOLE_AUTHZ_ENFORCE"):
        return user or "dev@localhost"
    if not user:
        raise HTTPException(status_code=401, detail="Sign in as an admin to open an SSH session.")
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"The SSH console is restricted to admins; {user} is '{role or 'unknown'}'.",
        )
    return user


@dataclass
class _Ticket:
    host_id: str
    user: str
    profile_id: str
    issued_at: float


_TICKETS: dict[str, _Ticket] = {}
_ACTIVE: set[str] = set()


def _mint_ticket(host_id: str, user: str, profile_id: str) -> str:
    now = time.monotonic()
    for token in [t for t, tk in _TICKETS.items() if now - tk.issued_at > _TICKET_TTL_S]:
        _TICKETS.pop(token, None)
    token = secrets.token_urlsafe(32)
    _TICKETS[token] = _Ticket(host_id=host_id, user=user, profile_id=profile_id, issued_at=now)
    return token


def _redeem_ticket(token: str) -> _Ticket | None:
    """Consume a ticket. Single use: a replay of the same URL finds nothing."""
    if not token:
        return None
    ticket = _TICKETS.pop(token, None)
    if ticket is None or time.monotonic() - ticket.issued_at > _TICKET_TTL_S:
        return None
    return ticket


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


async def _audit(app: Any, host_id: str, message: str, payload: dict[str, Any]) -> None:
    """One ``ssh_session`` row. Best-effort: auditing must never break (or
    hold up) a session, and the sqlite write goes to a worker thread."""
    db = getattr(app.state, "db", None)
    if db is None:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                host_id,
                SSH_SESSION,
                message=message,
                payload=payload,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break a session
        logger.warning("ssh audit write failed %s: %s", host_id, exc)


# --------------------------------------------------------------------------
# PTY plumbing
# --------------------------------------------------------------------------


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows or 24), _MAX_ROWS))
    cols = max(1, min(int(cols or 80), _MAX_COLS))
    with contextlib.suppress(OSError, ValueError, struct.error):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _ssh_argv(ssh_bin: str, host: SshHost, profile: SshProfile) -> list[str]:
    return [
        ssh_bin,
        # Force a PTY even though our stdin is already one: without -tt ssh
        # decides per-invocation and a non-interactive decision here would give
        # the browser a pipe with no echo and no line editing.
        "-tt",
        # Keys only. A password/passphrase prompt would stall behind a terminal
        # the operator can see but the server can never satisfy.
        "-o", "BatchMode=yes",
        # Never learn a new host key from a web request. Every host in
        # SSH_HOSTS is already in the service user's known_hosts.
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={_CONNECT_TIMEOUT_S}",
        # A half-open tailnet path otherwise leaves the shell hanging forever.
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        host.target,
        # The profile's remote command (tmux attach, wsl.exe, …) — a fixed
        # server-side tuple, never client text. Empty for the login shell.
        *profile.args,
    ]


def _child_env() -> dict[str, str]:
    """Minimal env for ssh: HOME (finds ~/.ssh/config + known_hosts), PATH,
    and a TERM xterm.js actually implements."""
    return {
        "HOME": os.path.expanduser("~"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "TERM": "xterm-256color",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


async def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        view = view[written:]


async def _send(websocket: WebSocket, frame: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(frame))


async def _fail(websocket: WebSocket, message: str) -> None:
    """Report a refusal on an accepted socket, then close.

    Closing *before* accept would give the browser only an opaque handshake
    failure; accepting costs nothing (no shell has been spawned yet) and lets
    the page say why.
    """
    with contextlib.suppress(Exception):
        await _send(websocket, {"t": "e", "d": message})
    with contextlib.suppress(Exception):
        await websocket.close(code=1008)


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


class SessionRequest(BaseModel):
    host_id: str
    #: Profile id from the host's `profiles` list; omitted → the default.
    profile: str | None = None


def build_ssh_router() -> APIRouter:
    router = APIRouter(prefix="/api/ssh", tags=["ssh-console"])

    @router.get("/hosts")
    async def list_hosts(request: Request) -> dict[str, Any]:
        """The whitelist an admin may open a shell on."""
        if not _env_flag("SSH_CONSOLE_ENABLED"):
            raise HTTPException(status_code=404, detail="The SSH console is disabled.")
        _require_admin(request)
        return {"hosts": [h.public() for h in SSH_HOSTS]}

    @router.post("/session")
    async def open_session(body: SessionRequest, request: Request) -> dict[str, Any]:
        """Mint a single-use ticket the WebSocket will redeem."""
        if not _env_flag("SSH_CONSOLE_ENABLED"):
            raise HTTPException(status_code=404, detail="The SSH console is disabled.")
        user = _require_admin(request)
        host = HOSTS_BY_ID.get(body.host_id)
        if host is None:
            raise HTTPException(status_code=404, detail=f"Unknown SSH host: {body.host_id!r}")
        profile = host.profile(body.profile)
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown session profile for {host.id}: {body.profile!r}",
            )
        token = _mint_ticket(host.id, user, profile.id)
        await _audit(
            request.app,
            host.id,
            f"{user} requested an SSH session on {host.label} ({profile.label})",
            {"user": user, "outcome": "ticket_issued", "target": host.target, "profile": profile.id},
        )
        logger.info("ssh ticket issued: %s -> %s (%s)", user, host.id, profile.id)
        return {
            "ticket": token,
            "expires_in_s": _TICKET_TTL_S,
            "host": host.public(),
            "profile": profile.public(),
        }

    @router.websocket("/ws")
    async def ssh_ws(
        websocket: WebSocket,
        ticket: str = Query(default=""),
        cols: int = Query(default=80),
        rows: int = Query(default=24),
    ) -> None:
        await websocket.accept()

        if not _env_flag("SSH_CONSOLE_ENABLED"):
            await _fail(websocket, "The SSH console is disabled on this dashboard.")
            return
        redeemed = _redeem_ticket(ticket)
        if redeemed is None:
            await _fail(
                websocket,
                "SSH ticket missing, expired, or already used — reload the page to get a new one.",
            )
            return
        host = HOSTS_BY_ID.get(redeemed.host_id)
        if host is None:  # pragma: no cover - ticket ids come from the whitelist
            await _fail(websocket, f"Unknown SSH host: {redeemed.host_id}")
            return
        profile = host.profile(redeemed.profile_id)
        if profile is None:  # pragma: no cover - minted from the same whitelist
            await _fail(websocket, f"Unknown session profile: {redeemed.profile_id}")
            return
        if len(_ACTIVE) >= _max_sessions():
            await _fail(
                websocket,
                f"Too many SSH sessions open ({_max_sessions()}). Close one and try again.",
            )
            return
        ssh_bin = shutil.which("ssh")
        if ssh_bin is None:
            await _fail(websocket, "No `ssh` binary on the dashboard host's PATH.")
            return

        await _run_session(websocket, host, profile, redeemed.user, ssh_bin, cols, rows)

    return router


async def _run_session(
    websocket: WebSocket,
    host: SshHost,
    profile: SshProfile,
    user: str,
    ssh_bin: str,
    cols: int,
    rows: int,
) -> None:
    """Spawn ssh on a PTY and shuttle bytes until either end hangs up."""
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError as exc:
        await _fail(
            websocket,
            "Could not allocate a pseudo-terminal on the dashboard host "
            f"({exc}). If the API runs under systemd, check that the unit's "
            "PrivateDevices= sandbox still provides /dev/pts.",
        )
        return

    session_id = secrets.token_hex(8)
    _ACTIVE.add(session_id)
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    last_seen = [time.monotonic()]
    reader_attached = False
    proc: asyncio.subprocess.Process | None = None
    outcome = "closed"
    detail: str | None = None

    def _on_readable() -> None:
        try:
            chunk = os.read(master_fd, _READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            # EIO on Linux is the normal "the slave side went away" signal.
            chunk = b""
        if chunk:
            out_queue.put_nowait(chunk)
        else:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_reader(master_fd)
            out_queue.put_nowait(None)

    try:
        os.set_blocking(master_fd, False)
        _set_winsize(master_fd, rows, cols)
        proc = await asyncio.create_subprocess_exec(
            *_ssh_argv(ssh_bin, host, profile),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            env=_child_env(),
        )
        # Our copy of the slave has done its job (the child holds its own).
        # Closing it now is what makes the master read EOF/EIO when ssh exits
        # — hold it and pump_out would wait forever on a live-looking pty.
        os.close(slave_fd)
        slave_fd = -1

        loop.add_reader(master_fd, _on_readable)
        reader_attached = True

        await _send(
            websocket,
            {
                "t": "o",
                "d": (
                    f"\x1b[2m· connecting to {host.label} — ssh {host.target}"
                    + (f" {' '.join(profile.args)}" if profile.args else "")
                    + "\x1b[0m\r\n"
                ),
            },
        )

        async def pump_out() -> str:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = await out_queue.get()
                if chunk is None:
                    return "exited"
                last_seen[0] = time.monotonic()
                text = decoder.decode(chunk)
                if text:
                    await _send(websocket, {"t": "o", "d": text})

        async def pump_in() -> str:
            while True:
                raw = await websocket.receive_text()
                last_seen[0] = time.monotonic()
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                kind = message.get("t")
                if kind == "i":
                    data = message.get("d")
                    if isinstance(data, str) and data:
                        await _write_all(master_fd, data.encode("utf-8"))
                elif kind == "r":
                    _set_winsize(master_fd, message.get("rows", 24), message.get("cols", 80))

        async def watchdog() -> str:
            timeout = _idle_timeout_s()
            while True:
                await asyncio.sleep(15)
                if time.monotonic() - last_seen[0] > timeout:
                    return "idle_timeout"

        tasks = [
            asyncio.create_task(pump_out(), name="ssh-out"),
            asyncio.create_task(pump_in(), name="ssh-in"),
            asyncio.create_task(watchdog(), name="ssh-idle"),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*pending, return_exceptions=True)

        first = done.pop()
        try:
            outcome = first.result()
        except Exception:  # noqa: BLE001 - a disconnect is the ordinary ending
            outcome = "disconnected"

    except Exception as exc:  # noqa: BLE001 - report, audit, then clean up
        outcome, detail = "error", str(exc)
        logger.warning("ssh session failed %s -> %s: %s", user, host.id, exc)
        with contextlib.suppress(Exception):
            await _send(websocket, {"t": "e", "d": f"SSH session failed: {exc}"})
    finally:
        if reader_attached:
            with contextlib.suppress(ValueError, OSError):
                loop.remove_reader(master_fd)
        exit_code = await _reap(proc)
        # slave_fd is -1 once handed to the child; it is still open here only
        # if we failed before the spawn, which would otherwise leak an fd per
        # attempt.
        if slave_fd != -1:
            with contextlib.suppress(OSError):
                os.close(slave_fd)
        with contextlib.suppress(OSError):
            os.close(master_fd)
        _ACTIVE.discard(session_id)

        duration_s = round(time.monotonic() - started, 1)
        with contextlib.suppress(Exception):
            await _send(
                websocket,
                {"t": "x", "code": exit_code, "outcome": outcome, "duration_s": duration_s},
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1000)

        payload: dict[str, Any] = {
            "user": user,
            "outcome": outcome,
            "target": host.target,
            "profile": profile.id,
            "duration_s": duration_s,
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        if detail:
            payload["detail"] = detail[:500]
        await _audit(
            websocket.app,
            host.id,
            f"{user} SSH session on {host.label} ended ({outcome}, {duration_s}s)",
            payload,
        )
        logger.info(
            "ssh session ended: %s -> %s (%s, %ss, rc=%s)",
            user, host.id, outcome, duration_s, exit_code,
        )


async def _reap(proc: asyncio.subprocess.Process | None) -> int | None:
    """Terminate the ssh child and return its exit code.

    ``start_new_session=True`` put it in its own process group, so a stray
    child cannot outlive the socket by hiding behind the API's group.
    """
    if proc is None:
        return None
    if proc.returncode is not None:
        return proc.returncode
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        return await asyncio.wait_for(proc.wait(), timeout=3)
    except (asyncio.TimeoutError, TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            return await asyncio.wait_for(proc.wait(), timeout=3)
    return proc.returncode
