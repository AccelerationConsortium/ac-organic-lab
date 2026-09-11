"""Attended Lumastir controls through the SDK, with browser-owned device leases.

The generic per-click release would stop every output. Only this equipment's
allowlisted actions accept an explicit claim token; no generic proxy behavior changes.
"""

from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from lab_skills.client import EquipmentClient
from lab_skills.exceptions import LabError, CommandOutcomeUnknown
from lab_skills.skill_catalog.lumastir import MotorSet, LedSet


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


SCHEMAS = {
    "claim": ClaimBody,
    "heartbeat": EmptyBody,
    "release": EmptyBody,
    "stop": EmptyBody,
    "motor/set": MotorSet,
    "led/set": LedSet,
}


async def proxy(request: Request, entry, action: str, method: str, body: dict | None):
    from .control import (
        _authorize_control,
        _claim_owner,
        _get_control_client,
        _record_control_event,
    )

    http = _get_control_client(request)
    owner = _claim_owner(request)
    if not request.headers.get("x-auth-user"):
        raise HTTPException(401, "Sign in to control Lumastir")
    await _authorize_control(request, http, entry.id, action, method, owner)
    code, outcome, detail = 200, "ok", None
    try:
        if method != "POST" or action not in SCHEMAS:
            raise HTTPException(404, "Unknown Lumastir control action")
        if action not in {"stop", "release"} and (not entry.enabled or entry.maintenance):
            raise HTTPException(409, "Lumastir is disabled or in maintenance")
        try:
            args = SCHEMAS[action].model_validate(body or {}).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(422, "Invalid Lumastir command arguments") from exc
        token = request.headers.get("x-claim-token")
        if action not in {"claim", "stop"} and not token:
            raise HTTPException(423, "A live Lumastir claim is required")
        device = EquipmentClient(entry, http)
        if action in {"motor/set", "led/set"}:
            status = await device.status()
            capability = "lumastir." + action.replace("/", ".")
            if capability not in status.allowed_actions:
                raise HTTPException(412, "Device does not currently allow this action")
        if action == "claim":
            args.update(owner=owner, ttl_s=30)
        return await device.command(
            "/control/" + action, args, claim_token=token if action != "claim" else None
        )
    except HTTPException as exc:
        code, outcome, detail = exc.status_code, "refused", exc.detail
        raise
    except LabError as exc:
        code = (
            504
            if isinstance(exc, CommandOutcomeUnknown)
            else getattr(exc, "http_status", None) or 502
        )
        outcome = "outcome_unknown" if isinstance(exc, CommandOutcomeUnknown) else "refused"
        detail = str(exc)
        raise HTTPException(code, detail) from exc
    finally:
        # Never record the claim token, claim response, or request headers.
        await _record_control_event(
            request,
            entry.id,
            action,
            method,
            owner=owner,
            status_code=code,
            outcome=outcome,
            detail=detail,
        )
