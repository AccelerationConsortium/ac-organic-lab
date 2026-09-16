"""Authenticated, bounded viewing; no camera control or arbitrary relay URLs.

One API worker owns admission, single-use tickets and renewable leases. Tickets
travel in the first WebSocket message, never a URL or log. Restart ends leases.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)
TICKET_SECONDS, LEASE_SECONDS, HEARTBEAT_SECONDS = 30, 60, 20


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream: str = Field(min_length=1, max_length=160, pattern=r"^[\w-]+$")
    grant_id: str | None = Field(default=None, max_length=100)


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: str = Field(min_length=1, max_length=254)
    streams: list[str] = Field(min_length=1, max_length=8)
    purpose: str = Field(min_length=1, max_length=300)
    duration_seconds: int = Field(default=3600, ge=60, le=86400)
    run_id: str | None = Field(default=None, max_length=100)


@dataclass
class Grant:
    id: str
    principal: str
    streams: list[str]
    purpose: str
    expires: float
    run_id: str | None
    approved_by: str


@dataclass
class ViewingSession:
    id: str
    user: str
    stream: str
    camera: str
    mode: str
    created: float
    expires: float
    ticket: str
    ticket_expires: float
    transport: str = "go2rtc"
    source_url: str | None = None
    grant_id: str | None = None
    connected: bool = False
    bytes_sent: int = 0
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "Viewing session ended"


class ViewingBroker:
    def __init__(self):
        self.sessions: dict[str, ViewingSession] = {}
        self.grants: dict[str, Grant] = {}
        self.max_sources = int(os.getenv("CAMERA_MAX_SOURCES", "1"))
        self.max_viewers = int(os.getenv("CAMERA_MAX_VIEWERS", "6"))
        self.max_per_user = int(os.getenv("CAMERA_MAX_VIEWERS_PER_USER", "3"))
        self.feed_mbps = float(os.getenv("CAMERA_FEED_BUDGET_MBPS", "1.5"))
        self.max_mbps = float(os.getenv("CAMERA_NETWORK_BUDGET_MBPS", "6"))
        if (
            min(
                self.max_sources, self.max_viewers, self.max_per_user, self.feed_mbps, self.max_mbps
            )
            <= 0
        ):
            raise ValueError("Camera capacity limits must be positive")

    def end(self, sid: str, reason: str) -> None:
        s = self.sessions.pop(sid, None)
        if s:
            s.reason = reason
            s.stopped.set()
            log.info(
                "camera_view_end user=%s stream=%s session=%s reason=%s bytes=%d",
                s.user,
                s.stream,
                s.id,
                reason,
                s.bytes_sent,
            )

    def grant_valid(self, g: Grant) -> bool:
        if g.expires <= time.monotonic():
            return False
        if g.run_id:
            from .workflow import _RUNS

            run = _RUNS.get(g.run_id)
            return bool(run and run.status == "running" and not run.abort_requested)
        return True

    def reap(self) -> None:
        now = time.monotonic()
        for gid, g in list(self.grants.items()):
            if not self.grant_valid(g):
                del self.grants[gid]
        for sid, s in list(self.sessions.items()):
            if s.expires <= now or (not s.connected and s.ticket_expires <= now):
                self.end(sid, "Viewing session expired")
            elif s.grant_id and s.grant_id not in self.grants:
                self.end(sid, "Monitoring authorization ended")

    def mint(
        self,
        user: str,
        camera: str,
        body: SessionIn,
        machine: bool,
        *,
        transport: str = "go2rtc",
        source_url: str | None = None,
    ) -> ViewingSession:
        self.reap()
        g = self.grants.get(body.grant_id or "")
        if body.grant_id and (not g or g.principal != user or body.stream not in g.streams):
            raise HTTPException(403, "No monitoring approval for this account and camera")
        if machine and (not g or not g.run_id):
            raise HTTPException(
                403, "Agents need an administrator-approved, run-scoped viewing grant"
            )
        sources = {s.stream for s in self.sessions.values()} | {body.stream}
        # Admission reservation, NOT measured QoS: conservatively budget one
        # camera input and one outgoing copy per viewer as crossing Wi-Fi.
        estimate = (len(sources) + len(self.sessions) + 1) * self.feed_mbps
        if (
            len(sources) > self.max_sources
            or len(self.sessions) >= self.max_viewers
            or sum(s.user == user for s in self.sessions.values()) >= self.max_per_user
            or estimate > self.max_mbps
        ):
            raise HTTPException(
                429,
                "Camera capacity is in use. Close another view and try again.",
                headers={"Retry-After": "20"},
            )
        now = time.monotonic()
        s = ViewingSession(
            id=secrets.token_urlsafe(18),
            user=user,
            stream=body.stream,
            camera=camera,
            mode="monitoring" if g else "interactive",
            created=now,
            expires=now + LEASE_SECONDS,
            ticket=secrets.token_urlsafe(32),
            ticket_expires=now + TICKET_SECONDS,
            transport=transport,
            source_url=source_url,
            grant_id=body.grant_id,
        )
        self.sessions[s.id] = s
        log.info(
            "camera_view_start user=%s stream=%s session=%s mode=%s", user, s.stream, s.id, s.mode
        )
        return s

    def redeem(self, ticket: str) -> ViewingSession:
        self.reap()
        for s in self.sessions.values():
            if s.ticket and secrets.compare_digest(s.ticket, ticket):
                s.ticket = ""
                s.connected = True
                return s
        raise HTTPException(403, "Viewing ticket is invalid, used, or expired")

    async def watch(self) -> None:
        try:
            while True:
                self.reap()
                await asyncio.sleep(1)
        finally:
            for sid in list(self.sessions):
                self.end(sid, "Camera viewing service restarting")


def broker(request: Request | WebSocket) -> ViewingBroker:
    return request.app.state.camera_viewing


def camera_definition(
    request: Request | WebSocket, stream: str
) -> tuple[str, str, str | None]:
    for entry in request.app.state.registry.equipment:
        # A camera may be a viewing component of another instrument (the
        # Gibbie Flex is the first), not only a standalone kind=camera entry.
        # The registry camera block is the allow-list; arbitrary relay names
        # remain impossible to request through this broker.
        if entry.enabled and entry.camera:
            for lens in entry.camera.lenses:
                if stream != f"{entry.id}_{lens.id}":
                    continue
                transport = getattr(entry.camera, "transport", "go2rtc")
                if transport == "mjpeg":
                    path = getattr(lens, "stream_path", None)
                    base = getattr(entry, "base_url", None)
                    if not base or not path or not path.startswith("/") or path.startswith("//"):
                        raise HTTPException(503, "Registered MJPEG camera source is incomplete")
                    parsed = urlsplit(base)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        raise HTTPException(503, "Registered MJPEG camera base URL is invalid")
                    return entry.id, transport, base.rstrip("/") + path
                return entry.id, "go2rtc", None
    raise HTTPException(404, "Unknown or disabled camera feed")


def camera_for(request: Request | WebSocket, stream: str) -> str:
    return camera_definition(request, stream)[0]


def same_origin(request: Request | WebSocket) -> None:
    origin = request.headers.get("origin")
    if origin and (
        urlsplit(origin).scheme not in ("http", "https")
        or urlsplit(origin).netloc != request.headers.get("host")
    ):
        raise HTTPException(403, "Cross-origin camera access is not permitted")


async def identity(request: Request, camera: str | None = None, *, admin=False) -> tuple[str, bool]:
    same_origin(request)
    auth = os.getenv("AUTH_SERVICE_BASE", "http://127.0.0.1:8009").rstrip("/")
    headers = {
        "cookie": request.headers.get("cookie", ""),
        "x-api-key": "" if admin else request.headers.get("x-api-key", ""),
        "x-forwarded-uri": request.url.path,
        "accept": "application/json",
    }
    client = request.app.state.control_client
    try:
        v = await client.get(auth + "/auth/verify", headers=headers, timeout=5)
        if v.status_code != 200:
            raise HTTPException(403 if v.status_code == 403 else 401, "Sign in to view this camera")
        user = v.headers.get("x-auth-user", "")
        if not user:
            raise HTTPException(401, "No verified identity")
        if admin and v.headers.get("x-auth-role") != "admin":
            raise HTTPException(403, "A signed-in human administrator is required")
        machine = bool(request.headers.get("x-api-key"))
        if camera:
            a = await client.get(
                auth + "/authz/check", params={"user": user, "equipment": camera}, timeout=5
            )
            if a.status_code != 200 or a.json().get("allowed") is not True:
                raise HTTPException(403, "Your account has no access to this camera")
            machine = machine or a.json().get("central_role") == "automation"
        return user, machine
    except httpx.HTTPError:
        raise HTTPException(503, "Camera authorization service unavailable") from None


def build_camera_streams_router() -> APIRouter:
    router = APIRouter(prefix="/api/camera-streams", tags=["camera viewing"])

    @router.post("/sessions", status_code=201)
    async def create(body: SessionIn, request: Request, response: Response):
        camera, transport, source_url = camera_definition(request, body.stream)
        user, machine = await identity(request, camera)
        s = broker(request).mint(
            user, camera, body, machine, transport=transport, source_url=source_url
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            "id": s.id,
            "ticket": s.ticket,
            "ws_path": "/api/camera-streams/ws",
            "heartbeat_seconds": HEARTBEAT_SECONDS,
            "lease_seconds": LEASE_SECONDS,
            "mode": s.mode,
            "transport": s.transport,
        }

    async def owned(sid: str, request: Request):
        b = broker(request)
        b.reap()
        s = b.sessions.get(sid)
        if not s:
            raise HTTPException(410, "Viewing session ended")
        user, _ = await identity(request, s.camera)
        if user != s.user:
            raise HTTPException(403, "This viewing session belongs to another account")
        return s

    @router.post("/sessions/{sid}/heartbeat")
    async def heartbeat(sid: str, request: Request, response: Response):
        s = await owned(sid, request)
        b = broker(request)
        b.reap()
        if b.sessions.get(sid) is not s:
            raise HTTPException(410, "Viewing session ended")
        s.expires = time.monotonic() + LEASE_SECONDS
        response.headers["Cache-Control"] = "no-store"
        return {"lease_seconds": LEASE_SECONDS}

    @router.delete("/sessions/{sid}", status_code=204)
    async def close(sid: str, request: Request):
        await owned(sid, request)
        broker(request).end(sid, "Viewer closed")

    @router.get("/sessions/{sid}/mjpeg")
    async def mjpeg(sid: str, request: Request):
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            raise HTTPException(403, "Cross-origin camera access is not permitted")
        referer = request.headers.get("referer")
        if referer and urlsplit(referer).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin camera access is not permitted")
        s = await owned(sid, request)
        if s.transport != "mjpeg" or not s.source_url:
            raise HTTPException(404, "This viewing session is not an MJPEG feed")
        if s.connected:
            raise HTTPException(409, "This viewing session is already connected")
        s.connected = True
        s.ticket = ""
        client = request.app.state.control_client
        try:
            upstream = await client.send(
                client.build_request("GET", s.source_url),
                stream=True,
            )
        except httpx.HTTPError:
            broker(request).end(s.id, "MJPEG source unavailable")
            raise HTTPException(502, "Camera source unavailable") from None
        content_type = upstream.headers.get("content-type", "")
        if upstream.status_code != 200 or not content_type.lower().startswith(
            "multipart/x-mixed-replace"
        ):
            await upstream.aclose()
            broker(request).end(s.id, "MJPEG source returned an invalid response")
            raise HTTPException(502, "Camera source returned an invalid stream")

        async def chunks():
            try:
                iterator = upstream.aiter_bytes()
                while True:
                    read = asyncio.create_task(anext(iterator))
                    stopped = asyncio.create_task(s.stopped.wait())
                    done, pending = await asyncio.wait(
                        (read, stopped), return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if stopped in done:
                        read.cancel()
                        await asyncio.gather(read, return_exceptions=True)
                        break
                    try:
                        chunk = read.result()
                    except StopAsyncIteration:
                        break
                    s.bytes_sent += len(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                broker(request).end(s.id, "MJPEG viewer disconnected")

        return StreamingResponse(
            chunks(),
            media_type=content_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/sessions")
    async def sessions(request: Request, response: Response):
        await identity(request, admin=True)
        b = broker(request)
        b.reap()
        response.headers["Cache-Control"] = "no-store"
        return {
            "sessions": [
                {
                    "id": s.id,
                    "user": s.user,
                    "stream": s.stream,
                    "mode": s.mode,
                    "connected": s.connected,
                    "seconds": round(time.monotonic() - s.created),
                    "bytes_sent": s.bytes_sent,
                    "grant_id": s.grant_id,
                }
                for s in b.sessions.values()
            ],
            "limits": {
                "sources": b.max_sources,
                "viewers": b.max_viewers,
                "per_account": b.max_per_user,
                "network_budget_mbps": b.max_mbps,
            },
        }

    @router.delete("/admin/sessions/{sid}", status_code=204)
    async def revoke_session(sid: str, request: Request):
        user, _ = await identity(request, admin=True)
        broker(request).end(sid, f"Disconnected by administrator {user}")

    @router.post("/grants", status_code=201)
    async def grant(body: GrantIn, request: Request):
        user, _ = await identity(request, admin=True)
        for stream in body.streams:
            camera_for(request, stream)
        b = broker(request)
        b.reap()
        if len(b.grants) >= 100:
            raise HTTPException(429, "Too many monitoring approvals")
        g = Grant(
            secrets.token_urlsafe(18),
            body.principal.strip().lower(),
            body.streams,
            body.purpose,
            time.monotonic() + body.duration_seconds,
            body.run_id,
            user,
        )
        if not b.grant_valid(g):
            raise HTTPException(409, "Workflow must be running in the approved dashboard executor")
        b.grants[g.id] = g
        log.info(
            "camera_monitor_approved grant=%s principal=%s by=%s run=%s",
            g.id,
            g.principal,
            user,
            g.run_id,
        )
        return {"id": g.id, "expires_in_seconds": body.duration_seconds}

    @router.get("/grants")
    async def grants(request: Request, response: Response):
        await identity(request, admin=True)
        b = broker(request)
        b.reap()
        response.headers["Cache-Control"] = "no-store"
        return [
            {
                "id": g.id,
                "principal": g.principal,
                "streams": g.streams,
                "purpose": g.purpose,
                "run_id": g.run_id,
                "expires_in_seconds": max(0, int(g.expires - time.monotonic())),
            }
            for g in b.grants.values()
        ]

    @router.delete("/grants/{gid}", status_code=204)
    async def revoke_grant(gid: str, request: Request):
        user, _ = await identity(request, admin=True)
        b = broker(request)
        b.grants.pop(gid, None)
        b.reap()
        log.info("camera_monitor_revoked grant=%s by=%s", gid, user)

    @router.websocket("/ws")
    async def video(ws: WebSocket):
        try:
            same_origin(ws)
        except HTTPException:
            await ws.close(code=1008)
            return
        await ws.accept()
        s = None
        tasks = []
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if (
                not isinstance(hello, dict)
                or hello.get("type") != "session"
                or not isinstance(hello.get("value"), str)
            ):
                raise HTTPException(403, "A viewing ticket is required")
            s = broker(ws).redeem(hello["value"])
            if s.transport != "go2rtc":
                raise HTTPException(403, "Use the registered MJPEG viewing path")
            camera_for(ws, s.stream)
            base = os.getenv("GO2RTC_BASE", "http://127.0.0.1:1984").rstrip("/")
            url = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
            url += "/api/ws?" + urlencode({"src": s.stream})
            async with connect(
                url,
                proxy=None,
                open_timeout=5,
                close_timeout=2,
                max_size=4 * 1024 * 1024,
                max_queue=4,
            ) as upstream:

                async def receive():
                    started = False
                    while True:
                        raw = await ws.receive_text()
                        if len(raw) > 65536:
                            raise HTTPException(400, "Camera message too large")
                        msg = json.loads(raw)
                        if not isinstance(msg, dict):
                            raise HTTPException(400, "Invalid camera message")
                        kind = msg.get("type")
                        if kind in ("mse", "webrtc/offer") and not started:
                            if not isinstance(msg.get("value"), str):
                                raise HTTPException(400, "Invalid camera handshake")
                            if kind == "webrtc/offer":
                                info = await ws.app.state.control_client.get(
                                    base + "/api", timeout=3
                                )
                                if info.status_code != 200 or "-lab-lease1" not in info.json().get(
                                    "version", ""
                                ):
                                    raise HTTPException(
                                        403, "WebRTC awaits lease-safe relay deployment"
                                    )
                                # Receive-only video/audio. Never permit an offer
                                # that sends media to a camera's speaker.
                                media = msg["value"].replace("\r", "").split("\nm=")[1:]
                                if not media or any(
                                    "\na=recvonly\n" not in "\n" + m + "\n" for m in media
                                ):
                                    raise HTTPException(
                                        403, "Camera viewing requires receive-only media"
                                    )
                            started = True
                        elif (
                            kind != "webrtc/candidate"
                            or not started
                            or not isinstance(msg.get("value"), str)
                        ):
                            raise HTTPException(403, "Only camera playback messages are allowed")
                        await upstream.send(raw)

                async def send():
                    async for data in upstream:
                        if isinstance(data, bytes):
                            await asyncio.wait_for(ws.send_bytes(data), 5)
                            s.bytes_sent += len(data)
                        else:
                            await asyncio.wait_for(ws.send_text(data), 5)

                tasks = [
                    asyncio.create_task(receive()),
                    asyncio.create_task(send()),
                    asyncio.create_task(s.stopped.wait()),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except HTTPException as exc:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await ws.send_json({"type": "session/error", "value": exc.detail})
        except (WebSocketDisconnect, ConnectionClosed):
            pass
        except (TimeoutError, ValueError):
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await ws.send_json(
                    {"type": "session/error", "value": "Camera connection stalled or invalid"}
                )
        except Exception:
            log.exception("Camera relay connection failed")
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await ws.send_json({"type": "session/error", "value": "Camera relay unavailable"})
        finally:
            for task in tasks:
                task.cancel()
            if s:
                broker(ws).end(s.id, s.reason)
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await ws.close(
                        code=4000, reason=s.reason[:100] if s else "Viewing session refused"
                    )

    return router
