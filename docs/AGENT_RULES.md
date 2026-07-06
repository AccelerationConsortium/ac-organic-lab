# Agent rules — lab-wide

**Status:** draft (2026-07-03). Canonical copy — every project repo's root
`AGENT_RULES.md` links here and adds only project-specific rules; nothing in
a project file may weaken a rule in this one.

These rules apply to **every agent** (and every human using an agent)
operating on lab infrastructure: proposing protocols, driving workflows,
reading or writing records. They are guidance an agent must read and plan
around — **not enforcement**. Everything safety-critical also exists as a
hard check (interlocks, claims, CI validation, human approval gates,
protected branches). The absence of a rule is not permission; when a
situation isn't covered, stop and ask a human.

## 1. Safety and hardware

1. **Never drive hardware directly.** All equipment use goes through the
   `lab-skills` SDK (skill catalog, claims, preconditions — interlock
   layer 3), never raw device `/control/*` endpoints. The SDK refusing a
   call *is* the safety system working.
2. **Never bypass, weaken, or work around an interlock** at any layer
   (hardware limits, device state machines, skill preconditions, project
   plan interlocks — see `INTERLOCKS.md`). If an interlock blocks an
   action, stop and report the violation; do not retry with adjusted
   parameters to get past the check.
3. **Only validated, human-approved plans execute.** A run requires a
   protocol merged to its project repo's `main` (the human sign-off), a
   registered `Plan` in AnaliticaDB, and a passing `validate_plan()`
   (interlock layer 4). No ad-hoc command sequences against live hardware.
4. **Respect claims.** Acquire equipment through the SDK's claim mechanism;
   never operate equipment claimed by another session, and release claims
   when done.
5. **Anything physically unexpected → stop and escalate.** Spills, stuck
   plates, sensor readings that contradict expected state: halt the
   workflow and notify a human. Do not improvise recovery that involves
   hardware motion.

## 2. Records and data integrity

1. **Every run is recorded in AnaliticaDB** through its REST API: the
   `Plan` at start, notes and measurements during, analyses after. If it
   isn't recorded, it didn't happen — and unrecorded work may not be used
   to justify decisions.
2. **Never fabricate, backfill, or edit records.** Observations are
   immutable; corrections and re-analyses are *new* records referencing
   the old (`corrects`, `supersedes`). Failed runs are recorded as failed,
   never deleted or retried into silence.
3. **Report truthfully.** Errors, deviations, and partial results are
   reported as such — to the human and to the record layer — not smoothed
   over in summaries.
4. **Identity is not negotiable.** Agents carry their own `agent_id` /
   `session_id` (OTel baggage); never write records or acquire claims
   under another identity, and never put secrets, credentials, or personal
   data in baggage, records, or metadata.
5. **Run data never goes into git repos.** Measurements, summary tables,
   images → AnaliticaDB. Git holds authored artifacts only (protocols,
   analysis code, rules).

## 3. Protocols and change control

1. **Only `main` executes.** Protocol changes arrive by pull request; a
   human CODEOWNER's merge is the approval. Never push directly to a
   protected branch, never rewrite published history.
2. **`step_id`s are permanent** once a protocol has merged and executed:
   add steps, never rename or reuse ids — records anchor to them.
3. **Comments are for humans, fields are for machines.** Anything the
   executor needs must be a schema-validated field; YAML comments are
   dropped at render time.
4. **Local configs stay local.** Machine paths, hostnames, and credentials
   live in gitignored `*.local.json` files, never in commits.

## 4. Chemicals and materials

*(Enforced tooling lands with the AnaliticaDB LIMS phase; the rules apply
now.)*

1. Use only substances and lots registered in the lab inventory; record
   consumption against the lot actually used, not a name string.
2. User-supplied chemicals are registered before use (owner's project,
   `source=user_provided`) — same rules, same records.
3. Never instruct a human to handle material in ways that conflict with
   its safety data; when a protocol touches a substance outside its
   project's declared scope, escalate rather than proceed.

## 5. Escalation

When rules conflict, when a check fails for unclear reasons, or when an
action is irreversible and not explicitly covered: **stop, preserve state,
ask a human.** A blocked run is recoverable; a wrong physical action or a
corrupted record may not be.

---

Project-specific rules live in each project repo's root `AGENT_RULES.md`
(see the `organic-hte-template` starter), which links back to this file.
