"""Scopes: which toolset a chat surface gets, fixed by the server.

The dashboard bubble is the unscoped surface (today's Ask / Control / Plan).
A device panel is an *equipment scope*: the same engine with a toolset that
can only read that one device (docs/ASSISTANT_CONSOLIDATION_PLAN.md, step 2).

The scope is enforced where the tools run, not only where the tool list is
built: the panel scope spawns ``lab-control`` alone, with
``LAB_SCOPE_EQUIPMENT`` (every equipment-taking tool refuses any other
device, at invocation, whichever backend called it) and ``LAB_CONTROL_TOOLS``
(only the read tools are registered). The lab-wide ``lab-history`` and
``lab-inventory`` servers are not spawned at all: they read the whole fleet
and write observations. The route fixes the scope from its own path, so a
request cannot widen it.

Step 2 is Ask-only. Drafting plans on a device (step 3) waits on the rules
amendment in the consolidation plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The lab-control tools a panel scope registers: reads of one device only.
SCOPED_TOOLS: tuple[str, ...] = (
    "get_equipment_docs",
    "list_available_actions",
    "lookup_custom_labware",
)

_EQUIPMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


@dataclass(frozen=True)
class EquipmentScope:
    equipment_id: str

    def __post_init__(self) -> None:
        if not _EQUIPMENT_ID.match(self.equipment_id):
            raise ValueError(f"invalid equipment id {self.equipment_id!r}")

    @property
    def name(self) -> str:
        return f"equipment:{self.equipment_id}"


def scoped_control_env(base: dict[str, str], scope: EquipmentScope) -> dict[str, str]:
    """``lab-control``'s environment for a panel turn: the caller's binding
    (actor, registry, authz) plus the scope and the read-only tool filter."""

    env = dict(base)
    env["LAB_SCOPE_EQUIPMENT"] = scope.equipment_id
    env["LAB_CONTROL_TOOLS"] = ",".join(SCOPED_TOOLS)
    return env


def scope_system_prompt(scope: EquipmentScope) -> str:
    return f"""You are the assistant for ONE instrument, `{scope.equipment_id}`, \
embedded in its own control panel. You help its operator understand the \
instrument's live state, its deck and consumables, and what it can do.

Your tools (lab-control, scoped to this instrument):

* get_equipment_docs -- its live status envelope plus its self-published \
equipment guide, action catalog and API description. Read this first.
* list_available_actions -- what it allows right now, and each action's \
argument schema.
* lookup_custom_labware -- one custom labware definition from the lab's store.

Every tool refuses any other equipment id. You cannot see other instruments, \
the lab's history, or the chemical inventory: for those, tell the operator to \
ask the dashboard assistant. You cannot operate this instrument here; the \
operator uses the panel's own controls.

Report only what the instrument reports. If it does not report something, \
say so rather than guess.

Be terse: 1-3 sentences by default, the answer first, no preamble. Use a \
short list only for 3+ genuine items."""
