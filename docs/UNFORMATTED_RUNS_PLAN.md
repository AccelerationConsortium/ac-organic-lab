# Unformatted runs → the ELN: as-run protocols, design skeletons, attach-to-design

**Status:** proposed 2026-10-06, for the operator's review (Codex review
pending — credits). Follows [`ASSISTANT_CONSOLIDATION_PLAN.md`](ASSISTANT_CONSOLIDATION_PLAN.md)
(steps 1–7 shipped) and the filing described in
[`ARCHITECTURE.md`](ARCHITECTURE.md) → *OT-2 plan results → ELN*. Spans three
repos: this one (the filing), `bitacora` (the notebook UI and its record
client), `BitacoraDB` (no schema change expected).

## The problem, in one paragraph

An OT-2 panel run is filed into BitacoraDB as an **UNFORMATTED Experiment**
(`hid` `<equipment>-plan-<id>`, `meta.unformatted: true`) with one
`observation` note carrying the plate report and every step result. That is
the right record for a device-local step approval (AGENTIC_LAB_DESIGN Part I
rule 3), but Bitácora shows none of it: the design rail lists *designs* and
*protocols* from the room's repo (its "No design" section is protocols that
claim no design — files, not runs), and the Runs tab lists *Run
Authorizations* (BitacoraDB Plans), which such a run never has. The data is
in the database and invisible in the notebook — the failure mode
`record.py`'s loose-match comment already names for imported runs. Today the
only ways to reach it are the record API through the edge, the panel's plate
report, and the panel chat's `query_run_results`.

## What a run can and cannot tell the notebook

| from the plan record (truthful, deterministic) | only a human knows |
|---|---|
| the plate and the wells actually used, in order | the **objective**, hypothesis, decision criteria |
| per-well dispensed volumes, sources, overrides → the *realized levels* | the **factor names** those levels mean ("acid equivalents", not "50 µL") |
| replicate count (identical wells), readout kind (`mass` from the balance) | which wells were **controls** |
| every action, argument, outcome and reading, with timestamps | substances beyond what `plate.load` / `well.update` recorded |
| the pattern it was proposed as (`prelude` / `for_each_well` / `epilogue`) | whether this run *was* the design, or a mechanical test |

Hence: a **protocol** can be generated from a run (it is "what happened");
a **design** can only be a *skeleton* with the intent fields left for the
human. The agent must not fill objective, hypothesis or factor names — the
panel prompt's "you do not decide chemistry" applies to the notebook too.

## Design

### 1. The filing writes an as-run protocol, a design skeleton and measurements

`api/app/plan_results.py` (this repo), at `file_one`, deterministically, no
model call:

- **As-run protocol** — Bitácora protocol YAML (`templates/hte/schema/protocol.schema.json`)
  derived from the plan: pattern provenance → `for_each_well: {steps: …}`
  with `{well}`, `prelude` → `before_wells`, `epilogue` → `after_wells`; a
  flat plan → one step per action. `step_id`s `s001`… assigned once and kept
  (they are permanent once merged). Actions keep their lab-skills names;
  gateway-local actions without a skill (`platebalance.read` / `tare` /
  `zero`) are emitted as `manual` instruction steps naming the balance until
  a skill exists. Numbers from the run become fixed `params`; the plate
  becomes a `plate` parameter of type `container` bound to the run's labware.
  Stored on the Experiment as `meta.as_run_protocol` (text) and in the note's
  `data`; **never** committed to a project repo by the filing.
- **Design skeleton** — design YAML (`schema/design.schema.json`) with what
  the run supports: `design: <hid>`, `replicates`, `readout: mass`,
  `substances` from deck records when present, and a `plate_map` of realized
  per-well levels; `objective`, `hypothesis`, `factors[*].name` and
  `controls` as explicit `TODO` placeholders. Stored as
  `meta.design_skeleton`. A skeleton with TODOs is not a valid design and is
  never offered as one.
- **Per-well measurements** — one `Sample` per weighed well (`hid` = well
  name) and one `Measurement` (kind `mass`, grams, deviation in `meta`,
  timestamp) per reading, through BitacoraDB's existing `POST /samples` and
  `POST /measurements`. This is what makes a later-attached run show its
  numbers in the design's well view (`record.py::_well_data_for_experiment`
  reads samples/measurements, not notes). Idempotent on retry via the
  per-experiment `hid` uniqueness.
- **Back-fill**: a one-off command re-files the journal's already-`filed`
  bundles with the new artefacts (same `hid`, PATCH + POSTs), so the runs
  collected since 2026-10-05 are not second-class.

### 2. Bitácora: an *Unformatted runs* section in the Runs tab

`bitacora/web` + `bitacora/app` (`main.py` runs routes, `record.py`):

- `GET /projects/{id}/runs` gains `unformatted: [...]` — Experiments of the
  project with `meta.unformatted`, newest first, with the note's summary
  line, plate stats, `started_at`, approver, device, and links to the panel's
  plate report (`/ot2/<name>/plans/plate-report.html?plan_id=…`). Same
  `viewed_as` honesty as `runs`; BitacoraDB's `can_read` still filters.
- The Runs tab renders them under a separate **Unformatted runs** heading
  (never mixed with authorized runs; a one-line note says why they exist).
- Each row has two actions:
  - **Attach to design…** — pick a design in the room. Opens a PR in the
    project repo adding `protocols/<name>.yaml` from `meta.as_run_protocol`
    with `design_ref: <design>`, and PATCHes the Experiment
    (`meta.protocol: <name>`, `title`, `description`; `meta.unformatted`
    stays until the PR merges). The merge is the human sign-off — rule 1 —
    so nothing lands on `main` by machine. On merge, a webhook/poll flips
    `meta.unformatted` off and the run moves to the normal view.
  - **Make a design from this run…** — opens the design skeleton in the
    room's design editor (and its chat) for the human to name the factors
    and write the objective; the same PR then carries `designs/<name>.yaml`
    and the protocol.
- `record.py::_well_experiment` matches `meta.protocol == <stem>` in addition
  to `hid == <stem>`, so **several** runs can hang under one protocol (the
  `hid` is unique per project and stays the run's own).

### 3. Later: protocol-driven proposals in the panel

The other direction, so planned work is never unformatted: the panel chat
(and the dashboard's Control mode) proposes **from a compiled protocol** —
`for_each_well.wells` accepting a Bitácora plate-map reference was left open
in `opentrons-server/docs/PLAN_REPEAT_DESIGN.md`. The run then files under
its Plan the normal way. Out of scope for this plan; noted so steps 1–2 do
not paint it into a corner (the as-run protocol uses the same vocabulary the
compiler consumes).

## Rules check

- Rule 1 (SDK boundary / human sign-off): the filing never writes to a
  project repo; attach/make-design open PRs; merge is human.
- Rule 3 (validated plans): nothing here executes; artefacts are records.
- "You do not decide chemistry": skeleton fields that are intent are TODOs;
  the agent may *propose* names only in conversation with the human, in
  Bitácora's chat, never by writing them.
- Access: unformatted runs follow BitacoraDB `can_read` (approver, project
  members/PIs, admins) — the same rule the panel and dashboard apply.

## Open questions for the operator

1. **Where do PRs go** — the project repo Bitácora already manages (then
   Attach needs the room's git identity), or a `drafts/` branch per project?
2. **Measurements for every run, or only on attach?** Every run is simpler
   and makes the well view work immediately; it also puts ~96 samples +
   readings per run into BitacoraDB for runs nobody will ever attach.
3. **`platebalance.*` as lab-skills skills** (so the as-run protocol compiles
   without `manual` placeholders)? Small, but it is a lab-skills change.

## Tests (each repo)

- filing: as-run protocol round-trips a pattern plan (prelude/template/
  epilogue, `{well}`), a flat plan, and a halted plan; skeleton has TODOs for
  every intent field and never a guessed factor name; samples/measurements
  are idempotent on retry; back-fill is a no-op on an already-complete record.
- bitacora: `unformatted` list respects `can_read`; Attach opens a PR and
  PATCHes the Experiment without touching `main`; `_well_experiment` matches
  `meta.protocol` and prefers a Plan when one exists; UI renders the section
  apart from authorized runs and says why.
