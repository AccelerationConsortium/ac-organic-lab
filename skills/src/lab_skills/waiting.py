"""Polling helpers used by workflow code.

``wait_until_state(client, "ready", timeout=...)`` is the v0.1 helper. Once
STATUS_SPEC v1.1 lands (claims + ``allowed_actions``) the implementation may
gain richer predicates, but the public signature is intended to be stable.
"""

from __future__ import annotations

import asyncio
import time
from typing import Iterable

from .client import EquipmentClient
from .exceptions import EquipmentUnreachable, WaitTimeout
from .models import EquipmentState


async def wait_until_state(
    client: EquipmentClient,
    expected: EquipmentState | Iterable[EquipmentState],
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> EquipmentState:
    """Poll ``client.status()`` until ``equipment_status`` matches.

    ``expected`` may be a single state name or any iterable of acceptable
    states. Returns the matched state on success. Raises :class:`WaitTimeout`
    if the timeout elapses before any expected state is observed; transport
    failures while polling do not raise immediately - they are remembered as
    the "last observed" state and re-attempted on the next tick.
    """

    if isinstance(expected, str):
        targets: tuple[str, ...] = (expected,)
    else:
        targets = tuple(expected)

    deadline = time.monotonic() + timeout
    last_state: str | None = None

    while True:
        try:
            envelope = await client.status()
            last_state = envelope.equipment_status
            if envelope.equipment_status in targets:
                return envelope.equipment_status
        except EquipmentUnreachable as exc:
            last_state = f"unreachable ({exc.message})"

        if time.monotonic() >= deadline:
            raise WaitTimeout(
                client.equipment_id,
                expected_state=" | ".join(targets),
                last_state=last_state,
                timeout_seconds=timeout,
            )

        await asyncio.sleep(poll_interval)


__all__ = ["wait_until_state"]
