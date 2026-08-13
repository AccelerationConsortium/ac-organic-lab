# Device PC Setup

**Audience:** anyone bringing up a Windows PC that hosts one or more lab equipment REST services (plateloc, platereader, platestacker, fume hood actuator, etc.).
**Goal:** get every device service running as an auto-starting Windows Service, isolated from every other service on the same PC, with a uniform install / update / log story across the lab.

This document is the **canonical install recipe** for any device repo that conforms to [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) (v1.0 baseline or v1.1 with claims). Each device repo's README links here rather than duplicating the recipe.

## TL;DR

- One **uv** binary, copied to `C:\SDL_Tools\uv.exe`, manages every device repo's Python environment. (`SDL_Tools` is namespaced to the Self-Driving Lab so it does not collide with arbitrary other software dropped into `C:\Tools` by IT or third-party installers.)
- Each device repo has its own `.venv\` and its own pinned `uv.lock`, so dependency upgrades on one service cannot break another.
- **NSSM** (the Non-Sucking Service Manager) wraps each service into a real Windows Service that auto-starts on boot, restarts on crash, and writes rotated log files. We keep `nssm.exe` next to `uv.exe` in `C:\SDL_Tools\`.
- One PowerShell script (`update_all.ps1`) pulls + re-syncs + restarts every service in a single pass.

If you only ever read one section, read [§3 Install a single device service](#3-install-a-single-device-service).

## 1. Prerequisites

- **Windows 10 or 11** (Pro recommended; required if you want NSSM to run as a non-Administrator account).
- A **lab user account** with "Log on as a service" rights. NSSM grants this automatically when you set `ObjectName` (see §3 step 6). Do **not** run device services as `LocalSystem` — see [§5 Run-as user choice](#5-run-as-user-choice) for why.
- **Tailscale** enrolled with the same tailnet as the dashboard. Device services bind to `0.0.0.0:<port>`; access is gated by Tailscale ACLs, not by Windows firewall rules.
- **PowerShell 5.1 or later** (ships with Windows; no install needed).
- **Per-device pre-reqs** (when applicable):
  - 32-bit Python + `pywin32` for ActiveX-backed drivers (`agilent-plateloc-server`, future Agilent devices). The device repo's README documents the exact `py -X.Y-32` setup; the `.venv` managed by uv is unaffected by it.
  - A serial-port profile created in the vendor's diagnostics dialog (PlateLoc, etc.). Profile creation requires **Administrator** the first time; normal operation does not.

## 2. One-time host setup

These three steps run **once per PC**, not per service. Run from an elevated PowerShell.

### 2.1 Install uv to a system-wide path

```powershell
# Run from an elevated PowerShell.
New-Item -ItemType Directory -Force C:\SDL_Tools | Out-Null

# Install uv via the official installer (drops it under %USERPROFILE%\.local\bin).
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Copy to a stable service path so the Windows Service account can find it.
# Services do NOT inherit the interactive user's PATH, so a per-user install
# alone is not enough. The fallback handles shells where PATH has already
# been refreshed and uv is discoverable via Get-Command.
$uvUser = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (!(Test-Path $uvUser)) {
    $uvUser = (Get-Command uv -ErrorAction Stop).Source
}
Copy-Item $uvUser C:\SDL_Tools\uv.exe -Force

# Verify the exact binary NSSM will call later.
C:\SDL_Tools\uv.exe --version
```

### 2.2 Install NSSM

```powershell
# Preferred: install via winget (built into modern Windows).
winget install -e --id NSSM.NSSM

# Alternative: install via Chocolatey.
choco install nssm -y

# Or download nssm.exe from https://nssm.cc/download and drop it on PATH
# (typically C:\SDL_Tools\nssm.exe)
```

`nssm --version` should print `NSSM 2.24-101-g897c7ad` or similar.

### 2.3 Create the lab directory layout

```powershell
New-Item -ItemType Directory -Force C:\Users\sdl2\Projects | Out-Null
New-Item -ItemType Directory -Force C:\SDL_Logs            | Out-Null
```

Convention: every device repo lives at `C:\Users\sdl2\Projects\<repo-name>\` and writes its logs to `C:\SDL_Logs\<service-name>.{out,err}.log`. Stick to this layout — the troubleshooting and update scripts below assume it. If the service runs under a different lab user, replace `sdl2` with that Windows username consistently.

### 2.4 SSH access from the central server (agent ops)

Device PCs may grant the central server's ops agent SSH access by **key
trust**, per [`HERMES_ACCESS_DESIGN.md`](HERMES_ACCESS_DESIGN.md) Phase 3.
(That doc prefers Tailscale SSH, but Tailscale's SSH *server* is unavailable
on Windows — `authorized_keys` is the mechanism here.) For an admin account
the operative file is `C:\ProgramData\ssh\administrators_authorized_keys`,
which must carry a restricted ACL or sshd ignores it:

```powershell
Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value "<pubkey>"
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F"
```

Granted keys (one per line, keep this list current):

| Key comment | Purpose | Granted on |
|---|---|---|
| `lab-ops@sdl2-server-gaia` (ed25519) | central ops agent — deploy/maintain `sdl-lab-hostops`, incident diagnosis | `sdl2-pc-03-cytation`, 2026-08-11; `sdl2-pc-06-uplc`, 2026-08-11 |

Routine host operations should go through the `sdl-lab-hostops` MCP surface
(whitelisted, audited — see [`AGENTIC_LAB_DESIGN.md`](AGENTIC_LAB_DESIGN.md)); SSH is the
maintenance/deploy path, not the everyday one.

## 3. Install a single device service

Run from an elevated PowerShell. Replace `<repo>`, `<svc>`, and `<port>` with the device-specific values from the table in [§7 Conventions](#7-conventions).

```powershell
# 3.1 Clone the device repo into the lab directory
cd C:\Users\sdl2\Projects
git clone https://github.com/cyrilcaoyang/<repo>.git
cd C:\Users\sdl2\Projects\<repo>

# 3.2 Copy the example config and edit for this PC
Copy-Item config.example.toml config.toml
notepad config.toml      # set com_port, profile, port, enforce_claims, etc.

# 3.3 Create the .venv and install runtime deps
C:\SDL_Tools\uv.exe sync --extra api

# 3.4 Smoke-test in the foreground (Ctrl-C to stop)
C:\SDL_Tools\uv.exe run --extra api <svc>-serve --port <port>
# In another shell:  curl http://127.0.0.1:<port>/   -> protocol_version field

# 3.5 Install the Windows Service
nssm install <svc> C:\SDL_Tools\uv.exe `
    run --project C:\Users\sdl2\Projects\<repo> --extra api <svc>-serve

# 3.6 Configure it
nssm set <svc> AppDirectory       C:\Users\sdl2\Projects\<repo>
nssm set <svc> DisplayName        "<Human-readable device name> REST service"
nssm set <svc> Description        "STATUS_SPEC service for <device>"
nssm set <svc> Start              SERVICE_AUTO_START
nssm set <svc> AppStdout          C:\SDL_Logs\<svc>.out.log
nssm set <svc> AppStderr          C:\SDL_Logs\<svc>.err.log
nssm set <svc> AppRotateFiles     1
nssm set <svc> AppRotateBytes     10485760           # 10 MB
nssm set <svc> AppExit Default    Restart            # like Restart=on-failure
nssm set <svc> AppRestartDelay    5000               # 5 s backoff

# 3.7 Run as the lab user (NOT LocalSystem -- see §5)
nssm set <svc> ObjectName         ".\labuser" "<password>"

# 3.8 Start it
nssm start <svc>
```

Verify:

```powershell
sc query <svc>                                       # STATE = RUNNING
Get-Content C:\SDL_Logs\<svc>.out.log -Tail 20       # uvicorn startup messages
curl http://127.0.0.1:<port>/                        # protocol_version field
curl http://127.0.0.1:<port>/status | ConvertFrom-Json | Format-List
```

## 4. Update workflow

Per-service update (run any time after a `git push` to the device repo):

```powershell
# C:\Users\sdl2\Projects\update.ps1
param([string]$Service, [string]$RepoDir)
Push-Location $RepoDir
git pull
C:\SDL_Tools\uv.exe sync --extra api
nssm restart $Service
Pop-Location
```

```powershell
.\update.ps1 plateloc C:\Users\sdl2\Projects\agilent-plateloc-server
```

Multi-service "update everything" (run nightly via Task Scheduler, or manually after an SDK release):

```powershell
# C:\Users\sdl2\Projects\update_all.ps1
$services = @(
    @{ Name = "plateloc";     Repo = "C:\Users\sdl2\Projects\agilent-plateloc-server" },
    @{ Name = "platereader";  Repo = "C:\Users\sdl2\Projects\bmg_platereader"      },
    @{ Name = "platestacker"; Repo = "C:\Users\sdl2\Projects\agilent_platestacker" }
)
foreach ($s in $services) {
    Write-Host "=== $($s.Name) ===" -ForegroundColor Cyan
    Push-Location $s.Repo
    git pull
    C:\SDL_Tools\uv.exe sync --extra api
    nssm restart $s.Name
    Pop-Location
}
```

The shape is uniform across every device because `uv run --project <path>` makes the supervisor config trivial. Conda equivalents need `cmd.exe /c "conda activate ..."` wrappers and have caused service-startup races in practice.

## 5. Run-as user choice

Always set `nssm set <svc> ObjectName ".\<labuser>" "<password>"` to a real local user account. **Do not run device services as `LocalSystem`** for two reasons:

1. **`HKCU` registry hive.** Vendor drivers (PlateLoc ActiveX, BMG Reader Control, etc.) store profiles in the user's `HKCU\Software\<vendor>` hive. `LocalSystem` has its own hive and will not see the profile you created interactively in the diagnostics dialog. Symptom: the service starts but every `/control/startup` returns 503 with a "profile not found" or "could not open device" message.
2. **COM / serial port permissions.** Some COM ports are bound to the interactive user's session. `LocalSystem` may fail to open them.

The lab account also needs the "Log on as a service" right. NSSM grants this automatically when you set `ObjectName` and gives a clear error message if it cannot.

## 6. Logs, health checks, and boot ordering

- **Logs.** NSSM appends to `AppStdout` / `AppStderr`. `AppRotateFiles=1` + `AppRotateBytes=10485760` rotates at 10 MB. For richer rotation, send uvicorn's logs to the Windows Event Log via `nxlog` or similar — but for the small log volume of a device service, NSSM's built-in is sufficient.
- **Health check.** Every device exposes `GET /health` (always 200 unless the process itself is broken) and `GET /status` (always 200 unless the process is broken; reports `requires_init` / `degraded` / `error` in-band). The dashboard's polling already shows you which services are up; you do not need a separate Nagios-style monitor.
- **Boot ordering.** Almost never needed on modern Windows — and **do not
  solve it with `DependOnService`**. This document used to recommend
  `nssm set <svc> DependOnService Tailscale`; that advice caused a 12.5 h
  outage and is withdrawn (2026-08-11):
  - An SCM dependency is a **stop cascade, not just a start ordering**: when
    the dependency stops, SCM stops every dependent — and it **never restarts
    dependents** when the dependency comes back.
  - Tailscale's MSI **auto-updater stops the Tailscale service on every
    release**. On 2026-08-10 the 1.102.2 update stopped `cytation` (the one
    service carrying the dependency) mid-update; the stop is *clean* (exit
    code 0), so NSSM's `AppExit Default Restart` does not fire — that setting
    governs the *application* exiting, not NSSM itself receiving a STOP
    control. The reader sat down ~12.5 h until restarted by hand.
  - The dependency also bought nothing: device services bind `0.0.0.0:<port>`
    directly, so they start fine before Tailscale is up — clients simply
    can't reach them until the tailnet interface exists, which is equally
    true with or without the dependency.
  - If a service genuinely cannot *start* without another service, prefer a
    startup retry loop in the service itself (the pattern shaker v0.2.2 /
    plateloc v1.5.0 ship for USB enumeration) over an SCM dependency.
  - Removing an existing dependency: `sc config <svc> depend= ""` (note the
    space after `=`). **`nssm reset <svc> DependOnService` reports success
    but does not clear it** — `DependOnService` is native SCM config, not an
    NSSM app parameter; verify with `sc qc <svc>`.

## 7. Conventions

| Repo                      | Service name   | Port | Entry point / NSSM AppParameters          | Tailnet host        |
|---------------------------|----------------|------|-------------------------------------------|---------------------|
| `xarm-translocation`      | `xarm`         | 8000 | `run pyxarm web` (superset of `pyxarm api`; starts the API server **and** the `/web/` control panel operators use) | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-plateloc-server` | `plateloc`     | 8010 | `run --extra api agilent-plateloc-serve`  | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-cytation-server` | `cytation`     | 8040 | `run agilent-cytation-serve`              | `sdl2-pc-03-cytation.<tailnet>` |
| `opentrons-server`        | `ot2-gateway-hte` | 8020 | `run --extra labware uvicorn opentrons_server.gateway.api:app --host 0.0.0.0 --port 8020` (renamed from `ot2-gateway` on 2026-08-08) | `sdl2-pc-03-cytation.<tailnet>` |
| `opentrons-server`        | `ot2-gateway-complexation` | 8021 | same, `--port 8021` — **one clone and one `.venv` serve both robots**; the instances differ only by `AppEnvironmentExtra` (id, robot, and three distinct state paths). `--extra labware` is load-bearing: `uv run` self-syncs at every start and without it prunes `opentrons-shared-data`, silently emptying the panel's labware catalog | `sdl2-pc-03-cytation.<tailnet>` |
| `torry-pines-shaker-server` | `torry-pines-shaker` | 8030 | `run --extra api torry-pines-shaker-serve` | `sdl2-pc-03-cytation.<tailnet>` |
| `agilent-biostack4-standalone` | `biostack4`     | 8050 | `run --extra api agilent-biostack4-serve --dry-run` (set `[service].port = 8050` in `config.toml`: the 8030 default is taken by `torry-pines-shaker` on this shared PC) | `sdl2-pc-03-cytation.<tailnet>` |
| `ac-organic-lab`          | `ac-organic-lab-api` | 8001 | `run uvicorn app.main:app --host 0.0.0.0 --port 8001` (AppDirectory=`api/`) | `sdl2-pc-03-cytation.<tailnet>` |
| `sdl-lab-hostops`         | `sdl-lab-hostops` | 8060 | `run --extra serial lab-hostops-serve --transport http` — whitelisted host-ops MCP server (service status/logs/restart, serial enumeration, local `/status` probes) consumed by the central agent; see [`AGENTIC_LAB_DESIGN.md`](AGENTIC_LAB_DESIGN.md). **Documented §5 exception:** runs as `LocalSystem` — it needs service-control rights over its NSSM neighbours and touches no vendor `HKCU` profile or COM port, so neither reason behind §5 applies. Requires `HOSTOPS_TOKEN` in the service env (non-loopback bind refuses to start without it). | `sdl2-pc-03-cytation.<tailnet>` (deployed + verified 2026-08-11; one per device PC as rolled out) |
| `fume_hood_actuator`      | `fume-hood`    | 5000 | —                                         | `fume-hood-pc.<tailnet>` |
| `filter_every_well`       | `press`        | 8000 | —                                         | `press-pc.<tailnet>` |
| `dose_every_well`         | `solid-doser`  | 8000 | —                                         | `solid-doser-pc.<tailnet>` |

Planned, not yet deployed (placeholder names/ports — confirm at install time):

| Repo                      | Service name   | Port | Tailnet host        |
|---------------------------|----------------|------|---------------------|
| `bmg_platereader`         | `platereader`  | 8001 | `platereader-pc.<tailnet>` |
| `agilent_platestacker`    | `platestacker` | 8002 | `platestacker-pc.<tailnet>` |

> **Windows quirk:** after every `nssm start <svc>`, run `sc continue <svc>` to clear
> the unexpected `SERVICE_PAUSED` state. This is an NSSM quirk; the service otherwise works correctly.
> (`sc resume` is not a real `sc` verb — it errors with "Unrecognized command"; `continue` is the resume verb.)

When multiple services share one PC, every service gets a distinct port; when each service has its own PC, the same port is fine across PCs (Tailscale's hostname is what disambiguates).

After install + smoke, register the service in the monorepo's `equipment.yaml` with the matching `id`, `base_url`, and `protocol` fields. See [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) for the registry shape (v1.0 and v1.1).

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `sc query <svc>` says `STOPPED` immediately after `nssm start` | wrong `AppDirectory` or `.venv` not synced | Run `C:\SDL_Tools\uv.exe sync --extra api` from the repo dir; check `<svc>.err.log`. |
| `/control/startup` returns 503 with "profile not found" | service is running as `LocalSystem` | Reset `ObjectName` to `.\labuser` (see §5). |
| `/control/seal/start` returns 423 | strict claim enforcement, no `X-Claim-Token` | The SDK's `ClaimManager` handles this automatically. For manual `curl` debugging, acquire a claim first via `POST /control/claim`. |
| Logs grow without bound | `AppRotateBytes` not set | `nssm set <svc> AppRotateFiles 1; nssm set <svc> AppRotateBytes 10485760`. |
| Service starts but `curl http://127.0.0.1:<port>/` hangs | Windows Defender Firewall blocking loopback (rare) | `New-NetFirewallRule -DisplayName "<svc>" -Direction Inbound -Action Allow -LocalPort <port> -Protocol TCP`. |
| `uv run` reports "no project found" under NSSM | `--project <path>` not specified | NSSM does not change directory unless `AppDirectory` is set; either set it (preferred) or always pass `--project`. |
| Service crash-loops with `error: Project virtual environment directory ...\.venv cannot be used ... (no Python executable was found)`; the `.venv` has only a `Lib\` folder (no `Scripts\python.exe` / `pyvenv.cfg`) | A `uv` venv rebuild (Python upgrade or a fleet-wide `uv sync`) **aborted mid-delete** on a hardlinked package file. uv hardlinks wheels from its global cache into every venv, so a shared `.pyd` (e.g. `httptools\...\parser.cp3XX-win_amd64.pyd`) is one physical file across many venvs; when another running uvicorn service has it memory-mapped, the delete hits a sharing violation and leaves the venv with no interpreter. | Rename or remove the corrupt `.venv`, then re-`sync`. See the note below — `Remove-Item`/`takeown` will **not** clear the image-locked hardlink. |
| `uv sync` exits non-zero with `error: failed to remove file ...\.venv\Lib\site-packages\../../Scripts\<svc>-serve.exe: The process cannot access the file because it is being used by another process. (os error 32)` — while `Scripts\python.exe` and `pyvenv.cfg` are both still present | The **running service holds its own console-script `.exe`**, and the release changed a dependency (or the project version), so uv had to replace that shim. Distinct from the row above: this is the service's *own* lock, not a hardlinked `.pyd` shared with another service. | Stop just that service, re-`sync`, start it: `nssm stop <svc>; C:\SDL_Tools\uv.exe sync --extra api; nssm start <svc>; sc continue <svc>`. Do **not** rename/delete the `.venv` — it is intact. See the note below: **verify the new dependency actually installed.** |
| One service is `STOPPED` with exit code 0 while every sibling on the PC runs fine; its log ends with a *clean* uvicorn shutdown (`Application shutdown complete`), no error | Something sent SCM a STOP control — a clean stop is **not** a crash, so NSSM's `AppExit Default Restart` never fires. Prime suspect: an SCM **dependency cascade** (`DependOnService`) — e.g. the Tailscale MSI auto-updater stopping its service and SCM stopping dependents with it, then restarting nobody (the 2026-08-10 cytation outage). | Correlate the Application event log: `nssm` event 1040 (`received STOP control`) against `MsiInstaller` events at the same timestamp. Check `sc qc <svc>` for `DEPENDENCIES`; clear with `sc config <svc> depend= ""` (**not** `nssm reset` — see §6), then `sc start <svc>`. |
| A service that mutates hardware/services fails with `'<tool>' is not available on this host` under NSSM but works fine in your shell | Windows services do **not** inherit the interactive user's `PATH` — `C:\SDL_Tools` (uv, nssm) is not on the machine `PATH`, so a bare `nssm`/`uv` argv raises `FileNotFoundError` under `LocalSystem` or the lab account. | Invoke by absolute path (`C:\SDL_Tools\<tool>.exe`) or resolve with `shutil.which()` + an explicit fallback, as `sdl-lab-hostops` does since v0.1.1 (`5484fca`). This is the same reasoning §2.1 gives for copying `uv.exe` to a stable path. |

> **Corrupt-venv / hardlink-lock recovery (all uv device PCs).** The trap:
> the corrupt `.venv` can't be deleted because its `.pyd` files are uv
> hardlinks to the same inode a *different* running service still has loaded,
> and Windows refuses to delete an image-mapped file through *any* of its
> hardlinks. Elevated PowerShell + `takeown` does **not** help (it's a lock,
> not ownership). Two things that work:
>
> - **`Rename-Item .venv .venv.broken-<stamp>`** — NTFS lets you rename a
>   directory that contains a loaded DLL; you just can't delete the DLL.
> - **Git Bash `rm -rf .venv`** — POSIX-semantics `unlink` removes just that
>   hardlink (decrementing the link count) while the inode stays alive via the
>   other service's open link. This cleans even the leftover `.venv.broken`.
>
> Then `C:\SDL_Tools\uv.exe sync` rebuilds a fresh venv (re-hardlinking from
> the unlocked cache) and `sc start <svc>; sc continue <svc>` brings it back.
> Because the trigger is fleet-wide, a bulk `uv sync` / Python upgrade can
> corrupt several device venvs at once — check every service, not just the one
> that paged you. (First hit live 2026-07-13: `xarm` and `opentrons-server`
> both landed in this state after a Python 3.14 rebuild.)

> **Own-service `.exe` lock — the silent-partial-sync trap (all uv device
> PCs).** Do not confuse this with the hardlink trap above. Here the service
> locks **its own** `Scripts\<svc>-serve.exe` console-script shim, so the
> `.venv` stays perfectly healthy (`Scripts\python.exe` + `pyvenv.cfg` both
> present) — the rename-`.venv` recovery is the **wrong** tool and must not be
> applied.
>
> **The dangerous part is not the error, it's what it leaves behind.** uv
> aborts the *entire* install transaction at the failed removal, so a genuinely
> new dependency can end up **not installed at all** while the old service
> keeps running happily off code already resident in memory. The service looks
> fine; the on-disk environment can no longer start the new build. After any
> `os error 32` sync failure, check the package actually landed rather than
> trusting a retry:
>
> ```powershell
> C:\SDL_Tools\uv.exe pip list | Select-String '<new-dep>'
> # or: ls .venv\Lib\site-packages\<new_dep>*
> ```
>
> **When to stop first.** Syncing *while the service runs* is fine and keeps it
> up — the shim is only replaced when a dependency or the project version
> changes, which is why §4's `update.ps1` ordering (`git pull` → `uv sync` →
> `nssm restart`) works for the common no-dependency-change update. Stop the
> service first when a release adds or bumps a dependency, or retry stop-first
> after hitting `os error 32`. Stop **only** the service being updated; leave
> the other services on the PC alone.
>
> A bare `nssm restart` will often self-heal this, since NSSM launches these
> services via `uv run --project ...`, which self-syncs at startup. Prefer the
> explicit stop → sync → start anyway: it surfaces a GitHub-unreachable or
> build failure in your terminal instead of burying it in service startup,
> where it becomes a crash-loop. (First hit live 2026-07-25 deploying
> `torry-pines-shaker` STATUS_SPEC v1.2, whose release added the
> `sdl-lab-contract` git dependency: sync failed on
> `torry-pines-shaker-serve.exe`, and `sdl_lab_contract` was absent from
> `site-packages` afterwards even though it had resolved and built fine.)

## 9. Uninstall

```powershell
nssm stop <svc>
nssm remove <svc> confirm
Remove-Item -Recurse -Force C:\Users\sdl2\Projects\<repo>
```

The lab user account, uv binary, and NSSM stay; uninstalling one device does not affect the others.

## See also

- [`docs/STATUS_SPEC.md`](STATUS_SPEC.md) — combined v1.0 + v1.1 + v1.2 contract every device REST API implements (claim protocol, `allowed_actions`, `activity`, SiLA comparison appendix).
- [`docs/ROADMAP.md`](ROADMAP.md) — per-device migration status.
- [`docs/EQUIP_GUIDE.md`](EQUIP_GUIDE.md) — registry / maintenance runbook on the dashboard side.
- `equipment.yaml` — the monorepo's source of truth for "which devices exist and where to reach them".
