"""Spec-compliant HTTP adapter.

Used when an equipment repo conforms to docs/STATUS_SPEC.md. Just GETs the
status URL and validates the body.
"""

from __future__ import annotations

import httpx

from .base import AdapterResult, EquipmentAdapter, coerce_envelope, get_json, now_utc


class HttpStatusAdapter(EquipmentAdapter):
    async def fetch(self, client: httpx.AsyncClient) -> AdapterResult:
        if not self.entry.base_url:
            return self.fail(
                "No base_url configured in equipment.yaml",
                kind="unconfigured",
            )

        url = self.entry.base_url.rstrip("/") + self.entry.status_path
        try:
            http_status, body, elapsed_ms = await get_json(
                client, url, timeout=self.entry.poll_timeout_seconds
            )
        except httpx.TimeoutException:
            return self.fail(f"Timeout calling {url}", kind="timeout")
        except httpx.ConnectError as exc:
            return self.fail(f"Cannot connect to {url}: {exc}", kind="connection_refused")
        except httpx.HTTPError as exc:
            return self.fail(f"HTTP error calling {url}: {exc}", kind="unknown")

        if http_status >= 500:
            return self.fail(
                f"{url} returned HTTP {http_status}",
                kind="http_5xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )
        if http_status >= 400:
            return self.fail(
                f"{url} returned HTTP {http_status}",
                kind="http_4xx",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )

        if not isinstance(body, dict):
            return self.fail(
                f"{url} did not return a JSON object",
                kind="parse_error",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )

        envelope = coerce_envelope(body)
        if envelope is None:
            return self.fail(
                f"{url} body does not match EquipmentStatus spec",
                kind="parse_error",
                http_status=http_status,
                elapsed_ms=elapsed_ms,
            )

        return AdapterResult(
            status=envelope,
            fetched_at=now_utc(),
            latency_ms=elapsed_ms,
            error=None,
        )
