"""Skill catalog entries for ``kind=power_strip``.

Reference devices: the two HTE bench Kasa HS300 strips (``plug_hte_strip_left``
/ ``plug_hte_strip_right``), fronted by the ``kasa-tapo-services`` gateway
(STATUS_SPEC v1.0, gateway-fronted per §2.1). Endpoints, mirrored from
``kasa_tapo_services.routes.plugs`` / ``kasa_tapo_services.models``:

* ``POST /plugs/{plug_id}/control/on``     body ``PlugSwitchRequest``
* ``POST /plugs/{plug_id}/control/off``    body ``PlugSwitchRequest``
* ``POST /plugs/{plug_id}/control/toggle`` body ``PlugSwitchRequest``

As with ``camera``, the ``endpoint`` recorded here is the device-relative
``/control/<action>`` path. The ``/plugs/<id>`` gateway prefix is composed by
the dashboard passthrough (``api/app/control.py::_control_url``), which
replaces the entry's ``status_path`` ``/status`` suffix with
``/control/<action>``.

Names
-----
``on`` / ``off`` / ``toggle`` are exactly what the gateway puts in
``allowed_actions`` — it is a hard-coded triple in ``routes/plugs.py`` and is
the same on both strips (verified live 2026-09-20, see
``tests/test_skill_catalog.py::test_power_strip_names_match_device_allowed_actions``).
Availability is a ``def.name in allowed_actions`` membership test, so these
names are not free to prettify.

``requires_states=["ready"]``
-----------------------------
The gateway only ever emits two states for a plug: ``ready`` (reachable, with
the action triple advertised) or ``unknown`` with no ``allowed_actions`` when
the Kasa device did not answer — it never reports ``degraded`` for a strip,
because a strip it can read is a strip it can switch. ``unknown`` on a
gateway-fronted kind is already interpreted as unreachable upstream
(STATUS_SPEC §2.1), so the v1.0 fallback gate is just ``ready``.

``outlet`` is required here, unlike on the device
-------------------------------------------------
The device's ``PlugSwitchRequest.outlet`` is optional, and omitting it switches
**the whole strip at once**. On these two strips that is a mass power cut: the
right-hand strip carries the xArm, the Echotherm shaker and the Waters press
motor, the left-hand one the MiniCNC motors and the Sartorius balance. A
whole-strip ``off`` is therefore exactly the kind of irreversible, ambiguous
action the lab contract says to escalate rather than express as a routine
plan step, so the catalog does not offer a spelling for it: SDK callers must
name one outlet. The device and the operator-facing ``PowerStripTile`` keep
the un-scoped form; this is a narrowing of the SDK surface, not of the device.

Note that registering these skills does **not** make them assistant-proposable.
That is a separate allowlist (``api/app/assistant_control._PROPOSABLE``), which
``power_strip`` is deliberately not in.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import SkillDef
from .registry import register


class PlugSwitchArgs(BaseModel):
    """Body for ``POST /control/{on,off,toggle}`` on a multi-outlet plug.

    Mirrors ``kasa_tapo_services.models.PlugSwitchRequest`` except that
    ``outlet`` is required (see the module docstring). The bound 0-31 is the
    device's own; the authority on how many outlets a given strip really has
    is its ``components`` (``outlet_0`` ... ``outlet_N``) — both HTE strips
    report six, indices 0-5.
    """

    model_config = ConfigDict(extra="forbid")

    outlet: int = Field(ge=0, le=31, strict=True)
    """Zero-indexed outlet to switch. Read the label off the live status
    envelope's ``components["outlet_<index>"].message`` before acting — the
    index/label mapping lives in the gateway's ``devices.yaml`` and differs
    between the two strips."""


register(
    "power_strip",
    [
        SkillDef(
            name="on",
            kind="power_strip",
            description="Energize one outlet of the strip.",
            endpoint="/control/on",
            args_schema=PlugSwitchArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="off",
            kind="power_strip",
            description="De-energize one outlet of the strip, cutting power to whatever is plugged into it.",
            endpoint="/control/off",
            args_schema=PlugSwitchArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="toggle",
            kind="power_strip",
            description="Invert one outlet's current on/off state; read the outlet's component state first.",
            endpoint="/control/toggle",
            args_schema=PlugSwitchArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
        ),
    ],
)


__all__ = ["PlugSwitchArgs"]
