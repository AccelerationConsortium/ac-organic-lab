"""Adapter base class and shared utilities."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from ..models import (
    EquipmentStatus,
    FetchError,
    FetchErrorKind,
)
from ..registry import EquipmentEntry


@dataclass
class AdapterResult:
    """Result of a single status fetch."""

    status: EquipmentStatus
    fetched_at: datetime
    latency_ms: int | None
    error: FetchError | None


class EquipmentAdapter(ABC):
    """Adapter interface. Implementations must never raise from `fetch()`."""

    def __init__(self, entry: EquipmentEntry) -> None:
        self.entry = entry

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        """Return the latest equipment status, or a synthetic `unknown` envelope."""

    def synthetic_unknown(
        self,
        message: str,
        error_kind: FetchErrorKind,
        http_status: int | None = None,
    ) -> EquipmentStatus:
        """Build an `unknown`-state envelope for the case where we cannot reach the device."""

        return EquipmentStatus(
            equipment_id=self.entry.id,
            equipment_name=self.entry.name,
            equipment_kind=self.entry.kind,
            equipment_status="unknown",
            message=message,
            device_time=datetime.now(timezone.utc),
        )

    def fail(
        self,
        message: str,
        kind: FetchErrorKind,
        http_status: int | None = None,
        elapsed_ms: int | None = None,
    ) -> AdapterResult:
        return AdapterResult(
            status=self.synthetic_unknown(message, kind, http_status),
            fetched_at=datetime.now(timezone.utc),
            latency_ms=elapsed_ms,
            error=FetchError(kind=kind, message=message, http_status=http_status),
        )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    timeout: float,
) -> tuple[int, Any, int]:
    """GET `url` and parse JSON. Returns (http_status, json_body, elapsed_ms).

    Raises `httpx.HTTPError` on transport failure. Does not raise on non-2xx.
    `json_body` is `None` if the response body is not valid JSON.
    """

    start = time.perf_counter()
    response = await client.get(url, timeout=timeout)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body, elapsed_ms


def coerce_envelope(payload: dict) -> EquipmentStatus | None:
    """Validate `payload` against the unified envelope, or return `None`."""

    try:
        return EquipmentStatus.model_validate(payload)
    except ValidationError:
        return None
