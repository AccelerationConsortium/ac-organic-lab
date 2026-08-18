"""Skill catalog entries for ``kind=camera``.

Reference device: the ``kasa-tapo-services`` gateway fronting Tapo PTZ
cameras (STATUS_SPEC v1.0, gateway-fronted per §2.1). Endpoints (mirrored
from ``kasa_tapo_services.models`` / ``routes/cameras.py``):

* ``POST /control/ptz``          body ``PtzNudgeArgs``   - discrete pan/tilt/
  zoom nudge (mousedown -> mouseup pattern).
* ``POST /control/preset/save``  body ``PresetSaveArgs`` - save the current
  pose as a named preset.
* ``POST /control/preset/goto``  body ``PresetGotoArgs`` - drive to a saved
  preset by id.
* ``POST /control/privacy``      body ``PrivacyArgs``    - toggle the lens
  privacy shutter.
* ``POST /control/streaming``    body ``StreamingArgs``  - toggle whether the
  gateway keeps a live stream open.

The gateway only advertises ``ptz`` / ``preset/*`` in ``allowed_actions`` when
the camera's ONVIF PTZ service is actually present (fixed cameras like the
C100/C110 omit them - STATUS_SPEC §6.2, never advertise an action the
hardware would refuse). ``preset/{id}`` (delete a preset, ``DELETE
/control/preset/{id}``) is deliberately not cataloged: the device advertises
it as a literal ``"{id}"`` template, not a concrete id, so there is nothing a
generic name-match skill lookup can resolve it to.

No ``requires_states`` gate: STATUS_SPEC's reference camera envelope has no
concept of ``busy`` (a camera is always ready to accept a pose command), and
gateway-fronted-unreachable is already caught upstream as a transport
``fetch_error`` / synthetic ``unknown`` (§2.1) before any skill lookup runs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import SkillDef
from .registry import register

PtzDirection = Literal[
    "up", "down", "left", "right",
    "up_left", "up_right", "down_left", "down_right",
    "stop",
]


class PtzNudgeArgs(BaseModel):
    """Body for ``POST /control/ptz`` (discrete nudge; mirrors
    ``kasa_tapo_services.models.PtzNudgeRequest``, the shape a chat-driven
    "pan left" / "zoom in" instruction maps onto directly. The gateway also
    accepts a continuous pan/tilt/zoom vector body, not modeled here — that
    shape is for a joystick UI, not a discrete proposal)."""

    direction: PtzDirection
    speed: float = Field(default=0.5, ge=0.0, le=1.0)
    duration_ms: int = Field(default=400, ge=0, le=5000)


class PresetSaveArgs(BaseModel):
    """Body for ``POST /control/preset/save``."""

    name: str = Field(min_length=1, max_length=64)


class PresetGotoArgs(BaseModel):
    """Body for ``POST /control/preset/goto``."""

    preset_id: str


class PrivacyArgs(BaseModel):
    """Body for ``POST /control/privacy``."""

    enabled: bool


class StreamingArgs(BaseModel):
    """Body for ``POST /control/streaming``."""

    enabled: bool


register(
    "camera",
    [
        SkillDef(
            name="ptz",
            kind="camera",
            description="Nudge pan/tilt/zoom one discrete step in a direction.",
            endpoint="/control/ptz",
            args_schema=PtzNudgeArgs,
            requires_states=["ready", "degraded"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="preset/save",
            kind="camera",
            description="Save the camera's current pose as a named preset.",
            endpoint="/control/preset/save",
            args_schema=PresetSaveArgs,
            requires_states=["ready", "degraded"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="preset/goto",
            kind="camera",
            description="Drive the camera to a previously saved preset.",
            endpoint="/control/preset/goto",
            args_schema=PresetGotoArgs,
            requires_states=["ready", "degraded"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="privacy",
            kind="camera",
            description="Toggle the lens privacy shutter on or off.",
            endpoint="/control/privacy",
            args_schema=PrivacyArgs,
            requires_states=["ready", "degraded"],
            estimated_duration_s=0.5,
        ),
        SkillDef(
            name="streaming",
            kind="camera",
            description="Toggle whether the gateway keeps a live stream open for this camera.",
            endpoint="/control/streaming",
            args_schema=StreamingArgs,
            requires_states=["ready", "degraded"],
            estimated_duration_s=0.5,
        ),
    ],
)


__all__ = [
    "PtzDirection",
    "PtzNudgeArgs",
    "PresetSaveArgs",
    "PresetGotoArgs",
    "PrivacyArgs",
    "StreamingArgs",
]
