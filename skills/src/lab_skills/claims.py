"""Claim/heartbeat/release for STATUS_SPEC v1.1 devices.

A :class:`ClaimManager` is an async context manager that holds a cooperative
lock on one device for the duration of a critical section. It implements the
client side of the protocol defined in the v1.1 section of
``docs/STATUS_SPEC.md``:

* ``__aenter__`` -> ``POST /control/claim`` -> spin up a heartbeat task.
* While inside the ``async with``: heartbeat keeps the claim alive.
* ``__aexit__`` -> stop the heartbeat -> ``POST /control/release`` (best
  effort, swallowed if it fails - the device's TTL will eventually free the
  claim).

Graceful degradation
--------------------
v1.0 devices do not implement ``/control/claim`` and respond with HTTP 404
(or 405). When that happens the manager enters **degraded mode** silently:
no token is held, no heartbeat task is started, no release is sent. The
``async with`` still works - workflow code that wraps every step in a
``ClaimManager`` is therefore safe to run against a mixed-protocol fleet.

Heartbeat resilience
--------------------
The heartbeat task tolerates two transient failures and self-cancels on the
third consecutive failure (matching the read-side aggregator's resilience
budget). On self-cancellation the manager records an
:class:`EquipmentUnreachable` and re-raises it from
:meth:`ClaimManager.assert_alive` and on ``__aexit__``. Workflow code that
wants to short-circuit on a dead claim mid-cycle calls ``assert_alive()``
between control commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx

from .client import EquipmentClient
from .exceptions import ClaimRejected, EquipmentUnreachable

# When the device does not specify a heartbeat interval we use this default.
# Half the typical TTL is conservative enough that one missed heartbeat does
# not lose the claim.
_DEFAULT_HEARTBEAT_INTERVAL_S = 5.0

# Number of consecutive heartbeat HTTP failures before the heartbeat task
# self-cancels and surfaces ``EquipmentUnreachable``. Mirrors the read-side
# resilience budget. Single failures are common (transient network blips).
_HEARTBEAT_FAILURE_BUDGET = 3


class ClaimManager:
    """Async context manager that acquires + holds + releases one device claim.

    Construct with an :class:`EquipmentClient`. ``owner`` is a free-form
    string surfaced in ``details.claimed_by`` for ops; ``session_id`` is an
    opaque per-session id (auto-generated UUID4 if not supplied) that the
    device uses to make repeat claims from the same session idempotent.

    Usage::

        async with ClaimManager(client, owner="alice") as claim:
            await client.command("/control/seal/start", body)
            claim.assert_alive()
            await client.command("/control/seal/stop")
    """

    def __init__(
        self,
        client: EquipmentClient,
        *,
        owner: str,
        session_id: str | None = None,
        ttl_s: float = 30.0,
        heartbeat_interval_s: float | None = None,
    ) -> None:
        self._client = client
        self._owner = owner
        self._session_id = session_id or str(uuid.uuid4())
        self._ttl_s = ttl_s
        self._heartbeat_interval_override = heartbeat_interval_s

        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._degraded: bool = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_failure: EquipmentUnreachable | None = None

    # -- Public surface ------------------------------------------------------

    @property
    def equipment_id(self) -> str:
        return self._client.equipment_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def token(self) -> str | None:
        """The active claim token, or ``None`` for v1.0 (degraded) devices."""

        return self._token

    @property
    def degraded(self) -> bool:
        """True when the device is v1.0 and the manager is operating as a no-op."""

        return self._degraded

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at

    def assert_alive(self) -> None:
        """Raise :class:`EquipmentUnreachable` if the heartbeat task has died.

        Always returns ``None`` for degraded (v1.0) managers - they have no
        heartbeat to lose.
        """

        if self._heartbeat_failure is not None:
            raise self._heartbeat_failure

    # -- Context manager hooks ----------------------------------------------

    async def __aenter__(self) -> "ClaimManager":
        # Fast path: registry-declared v1.0 device skips the network round
        # trip entirely. Devices that lie about their protocol version still
        # get the runtime 404 fallback below.
        if self._client.entry.protocol == "1.0":
            self._degraded = True
            return self

        try:
            payload = await self._post_claim()
        except _ClaimEndpointMissing:
            # Device returned 404/405 - it is on v1.0 even if the registry
            # says otherwise. Degrade silently.
            self._degraded = True
            return self

        self._token = payload["claim_token"]
        self._expires_at = _parse_datetime(payload.get("expires_at"))
        interval = self._heartbeat_interval_override or float(
            payload.get("heartbeat_interval_s") or _DEFAULT_HEARTBEAT_INTERVAL_S
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(interval), name=f"claim-heartbeat-{self.equipment_id}"
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Stop heartbeating first so a release in flight does not race against
        # a heartbeat from the same task.
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._heartbeat_task
            self._heartbeat_task = None

        # Best-effort release. If the heartbeat already died the device has
        # almost certainly let the claim expire, but call /control/release
        # anyway - it is idempotent and getting it right matters for ops.
        if self._token is not None:
            await self._post_release_quietly()
            self._token = None

        # If the heartbeat surfaced unreachability and the with-block did not
        # raise its own exception, propagate it now so workflow code does not
        # silently believe the claim was released cleanly. If the with-block
        # *did* raise we let that error win - debugging is easier when the
        # original cause is preserved.
        if self._heartbeat_failure is not None and exc is None:
            raise self._heartbeat_failure

    # -- HTTP wrappers -------------------------------------------------------

    async def _post_claim(self) -> dict[str, Any]:
        body = {
            "owner": self._owner,
            "session_id": self._session_id,
            "ttl_s": self._ttl_s,
        }
        url = self._client._url("/control/claim")
        response = await self._post(url, json=body)
        if response.status_code == 404 or response.status_code == 405:
            raise _ClaimEndpointMissing()
        if response.status_code in (409, 423):
            self._raise_rejected(response)
        if response.status_code >= 400:
            raise EquipmentUnreachable(
                self.equipment_id,
                f"{url} returned HTTP {response.status_code}: {response.text[:200]}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise EquipmentUnreachable(
                self.equipment_id, f"{url} did not return JSON: {exc}"
            ) from exc

    async def _post_heartbeat(self) -> None:
        if self._token is None:
            return
        url = self._client._url("/control/heartbeat")
        response = await self._post(
            url, headers={"X-Claim-Token": self._token}, json=None
        )
        if response.status_code in (401, 404):
            # Claim was revoked or forgotten by the device. Treat as a
            # heartbeat failure (counted by the loop) so we can self-cancel.
            raise EquipmentUnreachable(
                self.equipment_id,
                f"heartbeat for token rejected (HTTP {response.status_code})",
            )
        if response.status_code >= 400:
            raise EquipmentUnreachable(
                self.equipment_id,
                f"heartbeat returned HTTP {response.status_code}",
            )

    async def _post_release_quietly(self) -> None:
        if self._token is None:
            return
        url = self._client._url("/control/release")
        try:
            await self._post(url, headers={"X-Claim-Token": self._token}, json=None)
        except (EquipmentUnreachable, httpx.HTTPError):
            # Release is best-effort; swallow so __aexit__ never raises from
            # the cleanup path.
            pass

    async def _post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client._http.post(
                url,
                json=json,
                headers=headers or None,
                timeout=self._client.entry.poll_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise EquipmentUnreachable(
                self.equipment_id, f"timeout calling {url}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise EquipmentUnreachable(
                self.equipment_id, f"cannot connect to {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise EquipmentUnreachable(
                self.equipment_id, f"HTTP error calling {url}: {exc}"
            ) from exc

    # -- Heartbeat loop ------------------------------------------------------

    async def _heartbeat_loop(self, interval_s: float) -> None:
        consecutive_failures = 0
        try:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self._post_heartbeat()
                    consecutive_failures = 0
                except EquipmentUnreachable as exc:
                    consecutive_failures += 1
                    if consecutive_failures >= _HEARTBEAT_FAILURE_BUDGET:
                        self._heartbeat_failure = exc
                        return
        except asyncio.CancelledError:
            raise

    # -- Helpers -------------------------------------------------------------

    def _raise_rejected(self, response: httpx.Response) -> None:
        retry_after = _parse_retry_after(response)
        claimed_by: dict[str, Any] | None = None
        detail = ""
        try:
            payload = response.json()
        except ValueError:
            detail = response.text[:200]
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or response.text[:200] or "claim refused")
            cb = payload.get("claimed_by")
            if isinstance(cb, dict):
                claimed_by = cb
        raise ClaimRejected(
            self.equipment_id,
            detail or f"claim refused (HTTP {response.status_code})",
            http_status=response.status_code,
            retry_after_s=retry_after,
            claimed_by=claimed_by,
        )


class _ClaimEndpointMissing(Exception):
    """Internal signal: device returned 404/405 from /control/claim."""


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # datetime.fromisoformat accepts the ``Z`` suffix as of 3.11.
            # We support older runtimes by normalising it manually.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Pull a retry-after hint from the JSON body, falling back to the header."""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        val = payload.get("retry_after_s")
        if isinstance(val, (int, float)):
            return float(val)
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        # The HTTP spec also allows an HTTP-date here, but devices in this
        # fleet always emit numeric seconds. If a device starts emitting
        # HTTP-dates we can extend this without breaking callers.
        return None


__all__ = ["ClaimManager"]
