"""Sample lineage — which wells fed which, filed as `transfer` rows when a run
closes (PLATE_TRACKING.md D11; the first slice of Phase F).

Custody answers *where a plate is*; lineage answers *what went into which
well*. Two questions, one append-only ledger: a `move` row per handoff
(``custody.py``), a `transfer` row per (source well → destination well) pair.
Both anchor to the same ``plan_id`` + ``step_id``, so the two halves of a run's
provenance join without a convention.

**Why the executor derives nothing.** A compiled package carries no
``mapping`` / ``source`` / ``dest`` — ``package["protocol"]`` is a name string —
so for a long while the executor simply could not know which wells fed which.
Re-deriving bitácora's mapping semantics here was rejected on the same grounds
the location design rejects a central transition table: it would be a second
copy of a graph that already exists, wrong the first time the original
changes. So the compiler expands the pairs (one ``lineage`` entry per (source
plate, destination well), covered by the package digest by construction) and
this module does two much smaller things:

* :func:`transfers_from` — **pure**: of the pairs the package *declares*,
  which ones actually happened? Decided from the status of the compiled steps
  that realize each pair, never from the entry itself.
* :func:`post_transfers` — post one ledger row per surviving pair, sequentially
  and best-effort, in the same never-raises posture as every other
  record-layer write (``record.py`` property 1).

**Amounts are carried only where the package settles them.** A row's first job
is topology — A1 of the acid plate fed A1 of the reaction plate — and that part
is always derivable. ``amount_commanded`` is filled in only for the pairs where
the package leaves no room to guess: one declared source into that well, and
one gating step naming a volume (:func:`commanded_amount`). Everything else
keeps the null, because a wrong number in an append-only ledger is worse than
no number, and ``amount_commanded`` / ``amount_observed`` are separate nullable
columns precisely so the second one can arrive later without moving the join.
``amount_observed`` stays null throughout: no device reports it yet.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

logger = logging.getLogger("lineage")

#: What the compiler puts between a protocol step id and everything it expands
#: into: ``{step}__{sub}`` for a sequence, ``{step}__{well}__{sub}`` per well.
#: Matching on the separator rather than the bare prefix is what keeps `dose`
#: from gating `dose_acids`.
SEP = "__"

#: Reasons a row could not be written because the record layer does not model
#: the containers it would point at — the plate is unregistered, or was
#: registered without its 96 positional children. Nothing was attempted and no
#: retry would help, which is why these are *skipped* rather than *failed*: a
#: failure is a write the ledger saw and refused.
SKIP_REASONS = frozenset(
    {"unbound_plate", "unknown_container", "no_child_containers", "unknown_well"}
)

#: Compiled-step argument names that state a commanded amount, mapped to the
#: UCUM code the record layer's ``Unit`` enum accepts (PLATE_TRACKING.md §5:
#: ``uL mL mg g umol mmol``). Exactly one entry, and that is the point: the
#: OT-2's ``volume_ul`` (``skill_catalog.liquid_handler.LiquidMoveArgs``) is the
#: one argument in the catalog whose *name* carries its unit, so reading it
#: converts nothing and assumes nothing. An argument this table does not know
#: leaves the amount null rather than inviting a guess about its unit.
AMOUNT_ARGS: dict[str, str] = {"volume_ul": "uL"}

#: The skill whose volume is the amount that *landed* in the destination well.
#: A per-well transfer compiles to an aspirate and a dispense both carrying the
#: same ``volume_ul``, and this row is about the dispensing half.
DISPENSING_SKILL = "dispense"


def gating_steps(compiled: Iterable[str], entry: dict) -> list[str]:
    """The compiled step ids whose success means this pair actually transferred.

    A protocol step becomes one compiled step (a single-skill action), a flat
    sequence (``{step}__{sub}``), or a per-well expansion
    (``{step}__{well}__{sub}``) — so a `lineage` entry names a protocol step and
    the gate has to find whatever that step turned into.

    Two rules, both load-bearing:

    * The prefix match carries the separator. ``dose`` must not gate
      ``dose_acids``: those are different steps that happen to share a stem, and
      a bare ``startswith`` would file a transfer for wells the step never
      touched.
    * When the step expanded **per well**, the gate narrows to this entry's own
      destination well. Otherwise one failed well would suppress the transfers
      of the other 95, which is precisely backwards — the ledger would be
      silent about liquid that demonstrably moved.

    Narrowing drops any ``before_wells`` / ``after_wells`` sub-steps from the
    gate, which costs nothing: ``execute_plan`` is fail-fast, so a failed
    bracket step leaves every per-well step ``skipped`` and the gate fails on
    those instead.
    """
    step_id = entry.get("step_id")
    if not step_id:
        return []
    gate = [sid for sid in compiled
            if sid == step_id or sid.startswith(f"{step_id}{SEP}")]
    dest_well = entry.get("dest_well")
    if dest_well:
        prefix = f"{step_id}{SEP}{dest_well}{SEP}"
        per_well = [sid for sid in gate if sid.startswith(prefix)]
        if per_well:
            return per_well
    return gate


def commanded_amount(gate: list[str], steps: dict[str, dict]) -> tuple[float, str] | None:
    """``(value, unit)`` for the gate, or ``None`` when the package does not
    settle it. Pure.

    Two filters, both refusals rather than heuristics:

    * **Only arguments whose name states their unit** (:data:`AMOUNT_ARGS`).
      A number under an unrecognised key could be µL or mL and the difference
      is a thousandfold; the ledger takes the null instead.
    * **The dispense wins, and only if it is alone.** A per-well transfer
      compiles to aspirate + dispense carrying the same ``volume_ul``, so
      preferring the dispensing half resolves the ordinary case to one value.
      When two dispenses feed the same well the total is their *sum*, and a
      sum is an inference about what the run did rather than a reading of what
      it was told — so that case, and any other that leaves more than one
      candidate, returns ``None``.
    """
    candidates: list[tuple[str | None, float, str]] = []
    for sid in gate:
        step = steps.get(sid) or {}
        args = step.get("args") or {}
        if not isinstance(args, dict):
            continue
        for name, unit in AMOUNT_ARGS.items():
            value = args.get(name)
            # `bool` is an `int`; a flag is not a volume.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value <= 0:
                continue
            candidates.append((step.get("skill"), float(value), unit))
    dispensed = [c for c in candidates if c[0] == DISPENSING_SKILL]
    chosen = dispensed or candidates
    if len(chosen) != 1:
        return None
    _, value, unit = chosen[0]
    return value, unit


def spec_from(entry: dict, gate: list[str],
              amount: tuple[float, str] | None = None) -> dict[str, Any]:
    """One entry, flattened into what the poster needs: both ends of the pair,
    the protocol step the ledger row anchors to, and the compiled steps that
    vouched for it (which is also how the poster attributes the row to a
    machine — a compiled step knows its equipment, a protocol step does not).

    ``amount`` is the ``(value, unit)`` :func:`transfers_from` resolved, or
    ``None`` — which is a real answer here, not a missing one."""
    source = entry.get("source") or {}
    dest = entry.get("dest") or {}
    return {
        "amount_commanded": amount[0] if amount else None,
        "unit": amount[1] if amount else None,
        # The *protocol* step id, not a compiled one: a per-well expansion has
        # 96 compiled ids and the row belongs to the step a human authored.
        "step_id": entry.get("step_id"),
        "mapping": entry.get("mapping"),
        "source_plate": source.get("plate"),
        "source_hid": source.get("hid"),
        "source_well": entry.get("source_well"),
        "dest_plate": dest.get("plate"),
        "dest_hid": dest.get("hid"),
        "dest_well": entry.get("dest_well"),
        "gated_by": gate,
    }


def transfers_from(package: dict, statuses: dict[str, str]) -> list[dict]:
    """The declared well pairs that actually ran, as transfer specs. **Pure.**

    ``statuses`` is compiled ``step_id`` → the executor's final status for it.
    A pair is emitted iff its gate (see :func:`gating_steps`) is non-empty and
    **every** gating step ``succeeded``. Both halves of that matter:

    * A pair no compiled step realizes is dropped rather than filed. An entry
      with nothing behind it describes liquid that was never moved, and a
      ledger row is a claim about the physical world.
    * ``succeeded`` is the only status that counts. ``unknown`` in particular
      does not — a step that was sent and never answered may have moved the
      liquid, and the honest record of that is the ``outcome_unknown`` note
      custody already files, not a transfer row asserting a pour that may not
      have happened.

    Amounts are resolved here too, and only where the package settles them.
    A pair gets an ``amount_commanded`` when it is the **only** pair declared
    into its destination well for that step *and* :func:`commanded_amount`
    finds a single volume in the gate. Both halves are needed: a pairwise merge
    declares two sources into one well and the package says how much arrived,
    never how much each source contributed — that split lives in the protocol's
    mapping, not in any compiled step, so attributing the well's volume to
    either source would be a fabrication.
    """
    entries = [e for e in (package.get("lineage") or []) if isinstance(e, dict)]
    steps = {s["step_id"]: s for s in (package.get("steps") or [])
             if isinstance(s, dict) and s.get("step_id")}
    compiled = list(steps)
    # How many pairs the package declares into each (protocol step, dest well).
    declared: dict[tuple[Any, Any], int] = {}
    for entry in entries:
        key = (entry.get("step_id"), entry.get("dest_well"))
        declared[key] = declared.get(key, 0) + 1
    specs: list[dict] = []
    for entry in entries:
        gate = gating_steps(compiled, entry)
        if not gate:
            continue
        if any(statuses.get(sid) != "succeeded" for sid in gate):
            continue
        amount = None
        if declared[(entry.get("step_id"), entry.get("dest_well"))] == 1:
            amount = commanded_amount(gate, steps)
        specs.append(spec_from(entry, gate, amount))
    return specs


async def post_transfers(
    recorder, specs: list[dict], *, plan_id: str | None, operator: str,
    project: str | None, run_id: str, authorization_id: str,
    performed_by_lookup: Callable[[str], str | None],
) -> dict[str, Any]:
    """Post one ``transfer`` row per spec, sequentially. Never raises.

    Sequential rather than gathered on purpose: a 96-well plate is 96 rows and
    the record layer is a single Postgres behind one edge — filing them one at
    a time takes a second and cannot be the reason a finished run looks like it
    failed. A bulk endpoint is the obvious follow-up; the shape here does not
    change when it lands.

    ``performed_by_lookup`` maps a *compiled* step id to the equipment that ran
    it, so the row is attributed to the machine that did the pouring rather than
    to the human who pressed Run (who is the ``creator``, exactly as in
    ``record_move``). The launcher is the fallback for a step whose equipment
    the report did not resolve.

    Returns ``{"emitted", "failed", "skipped"}``. The split is deliberate:
    *skipped* is the record layer not modelling these containers (see
    :data:`SKIP_REASONS`) — a gap to fix by registering plates, not by retrying
    — while *failed* is a write the ledger saw and refused.
    """
    emitted = 0
    failed: list[dict] = []
    skipped: list[dict] = []
    for spec in specs:
        where = {"step_id": spec.get("step_id"),
                 "source": f"{spec.get('source_hid')}:{spec.get('source_well')}",
                 "dest": f"{spec.get('dest_hid')}:{spec.get('dest_well')}"}
        if not spec.get("source_hid") or not spec.get("dest_hid"):
            # Every named plate is bound at authorization, so this is a package
            # that was compiled without bindings — carried, not guessed at.
            skipped.append({**where, "reason": "unbound_plate"})
            continue
        performed_by = None
        for sid in spec.get("gated_by") or []:
            performed_by = performed_by_lookup(sid)
            if performed_by:
                break
        try:
            result = await recorder.record_transfer(
                source_hid=spec["source_hid"], source_well=spec.get("source_well"),
                dest_hid=spec["dest_hid"], dest_well=spec.get("dest_well"),
                amount_commanded=spec.get("amount_commanded"), unit=spec.get("unit"),
                performed_by=performed_by or operator, recorder=operator,
                project=project, plan_id=plan_id, step_id=spec.get("step_id"),
                params={"authorization_id": authorization_id, "run_id": run_id,
                        "mapping": spec.get("mapping"),
                        "protocol_step_id": spec.get("step_id"),
                        "via": "executor"},
            )
        except Exception as exc:  # noqa: BLE001 — the run already happened
            logger.warning("transfer row not written (%s): %s", where, exc)
            failed.append({**where, "reason": "raised", "detail": str(exc)[:200]})
            continue
        if result.get("recorded"):
            emitted += 1
        elif result.get("reason") in SKIP_REASONS:
            skipped.append({**where, **result})
        else:
            failed.append({**where, **result})
    return {"emitted": emitted, "failed": failed, "skipped": skipped}
