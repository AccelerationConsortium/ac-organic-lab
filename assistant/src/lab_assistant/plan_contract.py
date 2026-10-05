"""The plan vocabulary the chat engine shares with the control-proposal tools.

Moved out of ``app.assistant_control`` so the engine does not import the
dashboard: the dashboard's ``lab-control`` server re-exports these names.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# A plan card takes longer to read than a single action's — N steps with
# arguments — and the operator approves it, then runs it, then watches it
# finish; the dashboard keeps the plan record for this long from proposal
# (extended by the same amount on approval) before dropping it.
PLAN_TTL_S = 600
# Review-ability bound. A card nobody can read end to end is a rubber stamp;
# past this the model is told to split the work or recommend a workflow plan.
MAX_PLAN_STEPS = 256

# Every machine code a propose_action / propose_plan refusal can carry (the
# ``_err`` calls in the propose paths). assistant.py matches tool-result
# payloads against this set to emit a visible ``proposal_refused`` frame:
# without one, a refused proposal is indistinguishable from the model never
# proposing at all — the operator sees the request "understood" and no
# authorize button, and the why lives only in prose the model may not write.
REFUSAL_CODES = frozenset(
    {
        "no_actor",
        "unknown_equipment",
        "disabled",
        "unreachable",
        "not_allowed",
        "unmappable_action",
        "invalid_args",
        "forbidden_field",
        "not_authorized",
        "empty_plan",
        "too_many_steps",
        "invalid_step",
        # Step 1m: a slot argument naming another device's place, or a
        # registry place with several keys on this device.
        "wrong_device_location",
        "ambiguous_location",
        "identity_mismatch",
        "capability_unknown",
        # Step 2: a panel-scoped assistant asked about another device.
        "out_of_scope",
    }
)

def plan_step_hash(steps: list[dict[str, Any]]) -> str:
    """Stable digest of a plan's ``(action, args)`` list.

    Canonical JSON (sorted keys, no incidental whitespace) so a re-ordered dict
    or reformatting cannot change it while any change to an action or an
    argument value does. The operator approves THIS value, and the dashboard
    refuses an approval whose hash differs from the plan it cached (409) —
    which is what makes the approval a review of exactly what was shown rather
    than a rubber stamp. Same construction as opentrons-server's
    ``compute_step_hash``, so the two review surfaces agree on what "the same
    plan" means.
    """

    payload = json.dumps(
        [{"action": s["action"], "args": s.get("args") or {}} for s in steps],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
