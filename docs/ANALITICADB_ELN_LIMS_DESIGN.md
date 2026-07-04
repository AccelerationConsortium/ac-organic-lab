# AnaliticaDB → ELN + LIMS generalization — design analysis

> **Status:** design-analysis note (2026-07-03). **Canonical copy:**
> `AnaliticaDB/docs/eln-lims-generalization.md` — edit there; this copy lives
> in `ac-organic-lab/docs/` because this repo is the central place lab-stack
> context is kept (like `LAAGENTEANALITICA_ASSESSMENT.md`). AnaliticaDB is a
> separate project (the durable record system); this platform stack remains
> the real-time layer. Companion note in that repo:
> `docs/dmta-analysis-layering.md` (the analysis/report layering).

## The vision under analysis

AnaliticaDB today is the **Test**-stage store of a DMTA cycle: instrument
observations (`Experiment > Sample > Measurement > MeasurementFile`), exposed
over HTTP to an untrusted agent, with identity via OTel baggage, an
`AgentAction` audit trail, and a versioned ontology contract. The proposal is
to generalize it into the durable record system of an agent-operated lab — a
combined ELN + LIMS in which:

1. **Design** — a human and an agent collaboratively design experiments within
   a project, and that collaboration is stored;
2. **Execute / observe** — observations and metadata taken during the
   experiment are recorded and correlated to procedure steps;
3. **Test** — instrument data lands as it does today;
4. **Analyze / report** — analysis is performed afterward and reports
   generated (per the DMTA layering note);
5. **Materials** — chemicals from commercial sources are managed as *special
   samples* (a project acts as the chemical inventory; experiments model
   procure → enter → consume → reorder);
6. **Labware** — empty containers are objects that will hold future samples.

**Overall verdict: the generalization is sound and this repo is the right
place for it** — one PostgreSQL record system with one agent contract, next to
the real-time layer (`ac-organic-lab` dashboard/skills) rather than inside it.
Items 1–4 map cleanly onto the layering already argued in the DMTA note.
Items 5–6 identify the right *requirements* but the proposed *modeling*
(inventory-as-experiments, chemicals-as-samples) shoehorns ledger-shaped data
into the ELN hierarchy; a small dedicated LIMS module in the same service fits
better. Detailed analysis per item below.

## Framing: ELN and LIMS are different shapes of data

The reason "one hierarchy for everything" breaks is that the two product
categories hold differently-shaped records:

| | ELN (items 1–4) | LIMS (items 5–6) |
|---|---|---|
| Unit of record | narrative/intent: plans, notes, interpretations | assets & logistics: lots, containers, quantities, locations |
| Write pattern | **append-only timeline** (never edit history) | **ledger + current state** (balance is computed) |
| Lifetime | scoped to one experiment/project | spans many experiments and projects |
| Ownership | project-owned (existing `project` authz) | **lab-shared** (a bottle of THF belongs to no project) |

What unifies them is provenance, and the vocabulary is already in this repo's
DNA (W3C PROV, per the DMTA note): a **Plan** (intent), **Activities**
(execution steps, analyses, transactions), **Entities** (samples, lots, files,
results), **Agents** (human and AI principals — already modeled via
`creator` + baggage identity). Every addition below is one of those four
things, linked, append-only where it records history.

## 1. Design collaboration → store the *artifact*, not the conversation

The human–agent negotiation ("try 3 equivalents", "agent proposes a solvent
screen") happens in the agent repo's chat rooms and is already captured there
(Yjs docs, session logs, Logfire traces). The operational DB should **not**
store chat transcripts — it should store the **decision artifact** the
conversation converges on, with a link back to where it was negotiated:

- **`Plan` as a first-class, versioned entity** (not only a JSONB blob on
  `Experiment`): `plan_id`, optional `experiment_id`, `project`, structured
  `steps` (JSONB array where each step has a stable `step_id`, an action, and
  *nominal* parameters), `created_by` + `author_kind` (`human | agent`),
  `session_id` / trace id back-reference, optional `source_commit` +
  `protocol_path` (when the plan was rendered from a git-authored protocol —
  see the project-repo blueprint section below), and a `supersedes` self-FK.
  Revision = **insert a new version**, never update — the same
  insert-not-update rule the DMTA note sets for `Analysis`. The
  design-iteration history *is* the collaboration record: v1 (agent draft) →
  v2 (human edit) → v3 (approved) tells you who contributed what, without
  storing prose.
- **Approval is the human sign-off** (the ELN witness function, lightweight):
  a status lifecycle `draft → approved → executing → completed | abandoned`,
  where entering `approved` requires a human principal. This is also a safety
  hook: `ac-organic-lab/docs/INTERLOCKS.md` layer 4 (project plan interlocks /
  `validate_plan`) gets a durable place to record *which plan version* was
  validated and executed.
- `Experiment` gains `plan_id` (the version being executed) and a lifecycle
  `status` (`designed → running → completed → analyzed`). The experiment
  record then *starts* as a design onto which everything else appends —
  exactly the shape the DMTA note recommends (`prov:Plan`, ESCALATE's
  template→object split, nominal-vs-actual).

## 2. In-experiment observations → append-only `Note`, anchored to steps

A single append-only table covers lab-notebook narration, metadata captured
mid-run, and deviations:

- **`Note`**: `note_id`, required `experiment_id`, optional finer anchors
  (`sample_id`, `measurement_id`, `plan_id` + `step_id` string), `kind`
  (`observation | event | deviation | comment` — named PG enum per house
  style), free-text `body`, structured `data` JSONB, `created_at`,
  `creator` + `author_kind`. **Immutable — no PATCH endpoint at all**;
  corrections are new notes referencing the old (`corrects` self-FK). That is
  the ELN compliance posture (you never edit a lab notebook, you strike
  through and annotate) and it costs nothing to adopt from day one.
- **Step correlation** works because plan steps carry stable `step_id`s: a
  note anchored to (`plan_id`, `step_id`) is "observed during step 3", and
  *actual* execution values (start/stop, measured temperature, "solution
  turned orange") attach to the nominal step without mutating the plan — the
  ESCALATE `_nominal` / `_actual` split realized as plan-row vs note-rows.
- Anchoring uses **explicit nullable FKs, not a polymorphic
  `target_table`/`target_id` pair** (which `AgentAction` uses). Audit can
  afford polymorphism because it is write-only diagnostics; notes are queried
  back ("all observations on this sample") and deserve real FK integrity and
  join ergonomics. Requiring `experiment_id` keeps every note owned and
  authz-resolvable via the existing denormalized `project` pattern.
- Photos/spectra snapshots taken as observations: reuse the file pattern — a
  `note_files` table shaped like `measurement_files` (the `develop-sdl2-hh`
  branch's `experiment_files` work is the template).

## 3. Instrument data — unchanged

The existing four-level hierarchy stays the immutable observation core.
The only touch: `Measurement` may optionally reference (`plan_id`, `step_id`)
so instrument data is step-correlated the same way notes are.

## 4. Analysis and reports — already settled

Per [`dmta-analysis-layering.md`](dmta-analysis-layering.md): `Analysis` as a
first-class entity (M2M to measurements, typed results, `supersedes`,
insert-not-update), reports as experiment-level artifacts (`role="report"`)
generated from analyses. `Analysis` additionally carries an optional
`source_commit` (+ repo reference) pinning the exact analysis code that
produced it — see the project-repo blueprint section below. Nothing in this note changes that; the `Plan` and
`Note` layers complete the same pattern on the front end of the cycle. The
full loop then reads: **Plan (intent) → Notes (execution actuals) →
Measurements (instrument observations) → Analyses (interpretations) → Report
(derived artifact) → next Plan.**

## 5. Chemical inventory — right requirement, wrong shape

The proposal: chemicals from commercial sources are *special samples*; a
project acts as the chemical inventory; procure/enter/consume/reorder are
*experiments*. What this gets right:

- Inventory **belongs in this database** — sample provenance ("made from lot
  X") must join to measurements, so a separate inventory system would split
  provenance across stores.
- An incoming chemical **is sample-like at QC time**: you measure the purity
  of a received lot, and that is a genuine `Experiment` with a genuine
  `Sample`. The instinct that chemicals and samples meet is correct.
- Reusing this service's machinery (validation boundary, `@audited`, ontology
  export, authz) is exactly right.

What breaks if modeled literally:

1. **A `Sample` is a child of one `Experiment`; a chemical lot is consumed
   across many experiments and projects.** The FK hierarchy cannot express
   cross-experiment consumption, and the `project`-based read-authz
   denormalization would assign lab-shared stock to one owning project.
2. **"Consume 5 mL" is a ledger transaction, not an experiment.** It has no
   hypothesis, no measurements, and needs quantity semantics (units, running
   balance, negative-stock guards) that `Experiment` rows don't have.
   Modeling it as one would also pollute the agent's tool surface: the
   ontology would teach the agent that `create_experiment` sometimes means
   "log solvent usage" — the same contract-conflation the DMTA note flags for
   `result_summary`.
3. **Reorder is a query, not a state machine.** "Below reorder point" is a
   view over computed balance; encoding it as experiment lifecycle states
   creates state that can drift from the arithmetic truth.

**Proposed shape — a small ledger module in the same service (3 tables):**

- **`Substance`** — chemical identity, not a physical thing: `name`, CAS,
  InChI/SMILES/InChIKey, and the property fields worth stealing from the
  solubility schema (`mol_weight`, `density`, hazard codes). **One registry,
  not two:** solvent-vs-solute is a *role in a mixture*, not an identity —
  splitting the registry by role forces a chemical that plays both roles into
  two rows. Role lives on the composition/action row, or is simply implied by
  physical state and amount unit.
- **`Lot`** — a physical batch: `substance_id`, vendor, catalog/lot number,
  grade, `initial_amount` + `unit`, expiry, `received_at`, `container_id`
  (see §6), `hid` (the `develop-sdl2-hh` human-readable-id work fits
  barcode/label needs perfectly). Two fields added for provenance breadth:
  - `source: commercial | user_provided | synthesized | donated` (named
    enum) — see the tracking-mechanics section for what each enables;
  - `origin_sample_id` (nullable FK) — a lot *produced by an experiment*
    points at the sample it came from, closing the DMTA **Make → Test** loop:
    material made in experiment A becomes a provenance-linked ingredient of
    experiment B.
- **`ContainerAction`** — one append-only ledger for everything that happens
  to material and vessels, superseding the earlier `MaterialTransaction`
  sketch by folding in the solubility schema's process verbs:
  `action_type` (`receive | dose | transfer | filter | dilute | consume |
  dispose | adjust | move | seal | pierce | shake | read | store` — named
  enum), optional `lot_id`, signed `amount` + `unit` (required for material
  verbs, null for process verbs), `source_container_id` /
  `target_container_id`, `to_location_id`, optional
  `experiment_id`/`sample_id` (what the material went into), optional
  `measurement_id` (for `read`-type actions that produced data),
  `plan_id`+`step_id` (which plan step this executes), `performed_by` device
  or principal, `creator`, `performed_at`, `params` JSONB for
  verb-specific detail (rpm, seal temperature, dilution factor).
  **Material references are FKs, not names in `params`** — a dose action
  points at a `lot_id`, so "which experiments touched lot L-23" is a join,
  not a JSONB string match. **Current stock = SUM over material verbs** (a
  SQL view; materialize only if it measurably hurts). Immutable; mistakes
  are corrected with `adjust` rows.
- **The ELN↔LIMS join point:** a `sample_ingredients` link (sample ↔
  consuming transactions) so any measurement traces back through its sample
  to exact vendor lots — the chain that makes "this impurity peak appears in
  everything made from lot #4173" answerable in SQL.
- Procure/reorder: a stock view (`balance`, `reorder_point`) the agent polls,
  plus (optionally, later) a `ProcurementRequest` record for the
  order-placed → received workflow. `receive` transactions close the loop.

This keeps the user's *lifecycle* (procure → enter → consume → reorder)
fully expressible — as ledger events, which is what those verbs are.

## 6. Labware — containers as first-class physical objects, unified with bottles

Correct instinct, and it unifies with §5 more deeply than proposed: **a vendor
chemical bottle is just a container whose contents arrived pre-filled.** One
table covers labware and inventory vessels (this section was refined
2026-07-03 against Kevin Greenman's `sdl2-solubility-schema` — see the
alignment section below):

- **`Container`**: `container_id`, `hid`/barcode, `container_type` (vial,
  flask, bottle, plate, well, filter_plate… — named enum),
  `parent_container_id` self-FK plus a `position` string with
  `UNIQUE(parent_container_id, position)` — a 96-well plate is one container
  row with 96 child rows at positions `A1…H12`, a vial in a rack is the same
  shape; no separate plate/well tables needed. Optional `model` (labware
  model id, e.g. `agilent_shallow_96`) so device code can resolve geometry.
- **`Location` as a registry table, not a string** (adopted from the
  solubility schema): `location_id`, unique `name` (slash-path convention,
  e.g. `ot2/slot_3`), `location_type` (`storage | instrument | deck | fridge
  | waste` — named enum). `Container.location_id` is the *current* location;
  every change is also a `move` action in the ledger, so location history is
  queryable. An SDL needs "where is plate X" to resolve to something a robot
  understands; a free string can't do that.
- **`status`** (`empty | in_use | dirty | retired`) — mutable-with-audit.
  Empty labware awaiting samples = a container row with `status=empty` and no
  active contents; no special modeling needed.
- **History is derived; current composition is a service-owned cache.** The
  full custody chain of any barcode falls out of the action ledger
  (`from/to_container_id` on every transfer). But the *current mixture* in a
  well after dose → transfer → filter → dilute is **not** derivable by
  summing rows — a transfer moves proportional fractions of every component,
  which requires replay with mixing semantics. So a `ContainerContents` table
  (container ↔ lot, amount, unit) holds the materialized current composition,
  updated **in the same transaction** as the action row by the service —
  never writable by the caller directly. This is the DMTA note's
  "explicit cache" rule applied to LIMS: co-location fine, conflation not;
  the cache is recomputable from the ledger and stamped by it.
- Containers and locations are **lab-scoped, not project-owned** (see
  cross-cutting below).

## Tracking mechanics — how the ledger answers the day-to-day questions

The rule that makes tracking tractable: **every physical object gets exactly
one durable row with an `hid`/barcode (`Container`), every quantity of
chemical gets one identity row (`Lot`), and everything that happens to them
is an append-only `ContainerAction`.** Balances and histories are queries;
only current location/status/composition are materialized, and those are
service-owned caches updated transactionally with the action row.

| Question | Answer |
|---|---|
| Where is bottle #B-0417? | `Container.location_id` (cache) or the last `move` action (history) |
| How much THF is left in lot L-23? | `initial_amount + SUM(signed amounts)` — the stock view |
| What is in vial #V-1102 right now? | `ContainerContents` rows for that container |
| How did it get there? | replay the actions referencing that container |
| Which experiments touched lot L-23? | join `ContainerAction.lot_id = L-23 AND experiment_id IS NOT NULL` |
| What fed well D:A1 across plates? | recursive walk over `transfer`/`filter`/`dilute` actions via `source/target_container_id` |
| What needs reordering? | stock view `WHERE balance < reorder_point AND source = 'commercial'` |

**Receiving a commercial chemical** is one agent verb (`register_lot`, one
service transaction): find-or-create `Substance` (by CAS/InChIKey) → create
`Container` (type `bottle`, the vendor bottle gets a lab barcode) → create
`Lot` (`source=commercial`, vendor, lot number, initial amount, expiry) →
write a `receive` action into that container. A chemical arrives *as* a
container+contents pair — two linked rows, one operation.

**Using it in an experiment** is where "chemicals are special samples"
becomes literally true: draw an aliquot → `transfer` action (bottle → vial,
10 mL) + a `Sample` in the consuming experiment whose `sample_ingredients`
link points at the consuming action(s). The lot stays one shared record; each
use mints a cheap project-owned `Sample` with a two-join path back to the
vendor lot. QC of an incoming chemical is the same flow where the
experiment's purpose is testing the lot itself.

**Sharing across projects** uses the shared-project convention: `Lot`,
`Container`, and their actions carry `project = "lab-inventory"`, and every
principal is auto-enrolled in that project (a default-membership rule, not a
hand-maintained roster). Existing `can_read` then grants everyone stock
visibility and consumption rights, while the consuming experiment and sample
stay in the user's own project — communal stock and private data coexist
with no new authz machinery.

**User-brought chemicals** are why `project` lives on `Lot`/`Container`
rather than being hardcoded: register through the same `register_lot` flow
(their bottle still gets a lab barcode — safety and tracking are
non-negotiable once it is in the building) but with `source=user_provided`
and `project` set to the user's own project. It is now private stock:
visible and consumable only within that project, tracked identically.
Donating it later is a one-field reassignment to `lab-inventory`
(mutable-with-audit); taking it home is a `dispose`/withdraw action. The
reorder view filters `source=commercial`, so personal material never
triggers procurement — behavior that falls out of the enum, not
special-case logic.

**The honest cost:** the ledger is only trustworthy if it is the *only*
path — bench-top aliquoting must actually get recorded, which is an
agent-UX problem (barcode scan + one utterance) more than a schema problem.
Make the `consume`/`transfer` step part of the agent-guided experiment flow
rather than relying on diligence; labs where transactions are optional end
up with fictional balances.

## Alignment with the solubility campaign repos (2026-07-03)

Two sibling repos now bear on this design and were reviewed against it:
**`sdl2-solubility-schema`** (Kevin Greenman's plate-centric SQLModel schema
proposal, which explicitly extends this repo's four-table chain) and
**`organic-solubility`** (the campaign workflow repo, which currently writes
to the dashboard's SQLite `lab.db`).

**Adopted from `sdl2-solubility-schema`** (already folded into §5/§6 above):

- **`Location` as a typed registry table** — replaces this note's original
  "location string to start".
- **Positional addressing** — `UNIQUE(parent, position)` well addressing,
  realized here as generic container children rather than dedicated
  `plates`/`wells` tables, so bottles, vial racks, and filter plates get the
  same treatment.
- **Materialized current composition** (`well_components` →
  `ContainerContents`) — a genuine correction to this note's first draft,
  which claimed occupancy could be purely derived. Composition after
  transfer/filter/dilute needs mixing-semantics replay, so a
  transactionally-maintained cache is the right call. The discipline added
  here: service-owned, written in the same transaction as the action, never
  caller-writable.
- **Process verbs in the action log** (`seal | shake | pierce | read |
  store | move`) — the ledger records physical process events, not only
  material movements; device-generated events belong here, narrative belongs
  in `Note`.
- **`measurement_id` on read-type actions** — the clean join from labware
  history into the measurement chain.
- **The measured-vs-predicted result pattern** (`SolubilityResult.kind` +
  `source`) — not adopted as a table (it is campaign-specific) but as a
  requirement on the `Analysis` entity: an analysis with an *empty*
  measurement set, `method=model-id`, is a prediction; measured and
  predicted values then coexist and are comparable. The DMTA note's
  `Analysis` already has the shape; this confirms it must allow zero
  measurement links.

**Deliberately not adopted:**

- **Split `Solvent`/`Solute` registries** — role is per-mixture (the
  schema's own README says so), so a substance playing both roles would need
  two registry rows. One `Substance` registry; role on the composition row.
- **Substance names inside `params` JSONB** on dose actions — material
  references must be FKs (`lot_id`) or provenance queries degrade to string
  matching. Kevin's `supplier`/`lot` string fields on the registries confirm
  the *need* for lot tracking; the `Lot` table is its enforceable form.
- **App-maintained state without a service boundary** — the schema is a
  bare `create_all` with the application trusted to keep `well_components`
  consistent with `plate_actions`. Under this repo's rules those writes go
  through `@audited` service operations that own the transaction, and the
  contract is the exported ontology, not a shared Python import.

**`organic-solubility` mapping** (recorded in that repo as
`docs/analiticadb_record_layer.md`): the campaign's operational path —
`lab.db` `workflow_runs` / `well_metrics` / `well_artifacts` /
`run_checkpoints` on the dashboard host — stays as-is for run-time state and
dashboard rendering. This database is the *durable record layer* the same
events flow into: protocol (that repo's *recipe*) → `Plan` (steps with
stable `step_id`s), plate
registry entries → `Container` rows (plate + well children), dispenses/
transfers/filtrations → `ContainerAction`s, reads → `Measurement`s, per-well
derived values (saturation concentration) → `Analysis` rows, images/HPLC
`.D` pointers → file rows, the protocol's `parent` plate tree → the
`source/target_container_id` graph. The campaign repo's recursive
"what fed D:A1" query is the container-lineage query above.

## Project-repo blueprint — the git side of the record layer (2026-07-03)

Each project keeps a git repo of **authored artifacts**; this database keeps
the **operational record**; a commit hash on the DB row ties them. Git
answers "how did the procedure evolve and who signed off"; the DB answers
"what ran, what was observed, what was concluded". This is the hybrid that
DataLad/DVC-style practice (code and configs in git, data elsewhere) and
AiiDA-style practice (queryable immutable provenance in a DB, not git)
converge on. A git folder as the system of record was considered and
rejected: no joins, no referential integrity, concurrent machine writers
conflict, and additive-only is convention there rather than enforcement.

**Protocol ≠ Plan.** A *protocol* is the reusable, parameterized procedure —
authored, commented, and PR-reviewed in the project repo (ESCALATE's
template side). A `Plan` row is one rendered run of it: concrete parameter
values, rendered steps, `source_commit` + `protocol_path`, registered by the
orchestrator at run start (ESCALATE's object side). Run-specific values
(plate id, operator, the day's solvent list) belong to the Plan, never to
the protocol file. Terminology (2026-07-03): **"protocol"** chosen over
"recipe" (informal; the campaign repo's current name for the same artifact)
and over "workflow" (already means the *execution* side in this stack — the
engine code, the workflow host, `lab.db workflow_runs`).

**Layout** (published as the `organic-hte-template` starter repo — a GitHub
template repository under `AccelerationConsortium`, science-free):

```
solubility-project/
├── AGENT_RULES.md                # project-specific agent rules; links to lab-wide canon
├── protocols/                    # parameterized procedures (commented YAML)
├── schema/protocol.schema.json   # machine-checkable protocol shape; CI validates every PR
├── analysis/                     # code that computes Analysis rows (commit-stamped)
├── configs/                      # uploader/orchestrator config examples (no secrets/paths)
└── .github/                      # CODEOWNERS (human sign-off) + validate workflow
```

**Protocol rules** (CI-enforced, not just convention):

- **`step_id`s are permanent** once a protocol has executed: steps may be
  added, never renamed or reused — `Note` and `Measurement` rows anchor to
  them. CI diffs ids against the previous version of the file.
- **Comments are for humans, fields are for machines.** Rendering a protocol
  into a Plan drops YAML comments; the Plan's `source_commit` points back to
  the commented source. Anything the executor needs must be a field.
- **No run data in the repo.** Measurements, summaries, images go to this DB
  via the API; data in git would be a second source of truth.
- **No filename versioning** (`v1/` folders, `-v2` suffixes): git history is
  the version history; DB rows pin commits.

**Approval flow:** protocol changes arrive by PR; `CODEOWNERS` requires a
human approver; merge to `main` is the sign-off. The orchestrator executes
only from `main` and records the merge commit on the Plan's `approved`
transition — giving INTERLOCKS layer 4 (`validate_plan`) a concrete identity
for *which procedure version* was validated.

**`AGENT_RULES.md` placement:** lab-wide rules live once, in
`ac-organic-lab/docs/AGENT_RULES.md`; each project repo's root file holds
only project-specific rules and links to the canonical one (single source —
copies drift). Rules files are guidance an agent reads, **not enforcement**:
anything safety-critical must also exist as a check (interlock, CI
validation, approval gate).

**History protection:** branch protection / rulesets on `main` (no
force-push, no branch deletion, PR-only, no bypass actors) makes the shared
history effectively append-only — local clones can rewrite, the server
refuses the push. The DB is the tamper-evidence backstop: a Plan row stores
the rendered steps *and* the commit hash in a no-edit, audited record, so a
rewritten repo is both pointless (the record of what ran survives here) and
detectable (the stored hash no longer resolves). Signed commits are optional
extra attribution, not required at lab scale.

**Applying this to `organic-solubility`'s existing files:** the commented
recipe YAMLs in `examples/` (`recipe_caffeine.yaml`, `recipe_dilution_series.yaml`)
move to `protocols/` and adopt the *protocol* name, with run-specific values
(`plate_id`, `operator`) lifted out into per-run parameters; the
`examples/v1/` copy is retired in favor of git history; the prose recipe
schema in `docs/workflow_design.md` gets a machine-checkable
`schema/protocol.schema.json` counterpart; the workflow engine in `src/` is
unchanged except that it registers the Plan (rendered steps +
`source_commit`) at run start; the `lab.db` real-time path stays as-is per
the mapping above.

## Cross-cutting consequences

- **Ownership scope.** The current authz model assumes every row has an
  owning `project`. Substances, lots, and containers are lab-shared: they
  need a resource scope (`project`-scoped vs `lab`-scoped) in
  `analytica_db.authz`, not a fake owning project. This is the one place the
  existing foundation genuinely has to bend.
- **Audit and identity generalize as-is.** `@audited` + `AgentAction` +
  baggage identity apply to every new write; `author_kind` (human vs agent)
  on plans/notes is new and matters for the collaboration record — worth
  deriving from the authenticated principal, never from the payload.
- **Immutability spectrum, explicit per table:** immutable-insert-only
  (`Note`, `MaterialTransaction`, `Measurement`+files), versioned-via-
  `supersedes` (`Plan`, `Analysis`), mutable-with-audit (`Container.location`
  / `.status`, `Lot` metadata corrections). Write it into each table's
  docstring; it is the ELN/LIMS compliance story in one line each.
- **Contract discipline.** Each module (notebook, analysis, inventory,
  containers) extends `ontology.json` with the same
  `table/collection/create/read/update/list` export. The ontology stays the
  single agent-facing contract; `SCHEMA_VERSION` keeps exact-match semantics.
  The agent's verbs should stay semantic (`register_lot`, `consume`,
  `check_stock`) even where they compile down to ledger inserts.
- **Relation to `ac-organic-lab`.** No overlap in responsibility: the
  dashboard/skills stack is the *real-time* layer (what can the lab do right
  now, STATUS_SPEC, interlocks); AnaliticaDB becomes the *record* layer (what
  was planned, done, observed, consumed, concluded). They meet at three
  seams: `Plan` executions reference validated plans (INTERLOCKS layer 4),
  notes/measurements carry equipment ids from `equipment.yaml`, and trace
  ids/session ids link records to Logfire and chat rooms.

## Entity map

```
                          project (scope string)
                             │
   Plan (versioned) ────< Experiment ────< Note (append-only, step-anchored)
     │  steps[step_id]       │    │
     │                       │    └────< ExperimentFile / report (role="report")
     │   ┌───────────────────┘
     │   Sample ───< Measurement ───< MeasurementFile
     │     │  ▲          │ M2M            ▲
     │     │  └──────── Analysis (versioned, typed results; may have
     │     │ sample_ingredients           zero measurements = prediction)
     ▼     ▼                              │ read-actions link
  (step_id refs)   ContainerAction (ledger: material + process verbs)
                     │ lot_id      │ from/to                │ to_location
                     ▼             ▼                        ▼
      Substance ──< Lot       Container ──< ContainerContents   Location
      (one registry) │ origin_sample_id │ parent + position     (typed
                     │ source enum      │ (well ⊂ plate)         registry)
                     └── held in ───────┘        (lab-scoped: project="lab-inventory")
```

## Suggested phasing

1. **Phase 1 — complete the DMTA loop (ELN core):** `Analysis` (per the DMTA
   note), `Plan` + experiment lifecycle, `Note`. One contract bump. This is
   also the phase that resolves the `result_summary` question blocking the
   `develop-sdl2-hh` merge. **Shipped 2026-07-03 (contract 0.4.0→0.6.0,
   DB + API only; the `develop-sdl2-hh` merge landed the same day —
   `result_summary` materialises as `Analysis` rows at upload)** — deferred: `note_files` (template = the hh branch's
   `experiment_files`), typed result blocks, the experiment `status`/
   `plan_id` columns, and all cross-repo wiring (plan registration from
   protocols, note capture, analysis pipelines). The `source_commit`/`protocol_path` pins from the
   project-repo blueprint land here (nullable columns on `Plan`/`Analysis`);
   the git side (repo restructure, CI, branch protection) needs no DB work.
2. **Phase 2 — LIMS core:** `Container` (+ positional children), `Location`
   registry, `Substance`/`Lot` (with `source` + `origin_sample_id`),
   `ContainerAction`, `ContainerContents`, `sample_ingredients`, stock view,
   the `lab-inventory` shared project with auto-enrollment.
3. **Phase 3 — conveniences:** procurement requests, current-preferred-
   analysis cache view, report generation tooling in the agent repo,
   campaign-repo ingestion (`organic-solubility` events flowing into the
   record layer alongside `lab.db`).

## Recorded follow-ups — schema tightenings (2026-07-04)

Critique accepted after the hh merge; filed here so they don't evaporate:

1. **Provenance stamps on derived artifacts.** `experiment_files` and
   `experiment_tables` carry no link to the analyses that produced them —
   a `role="report"` file cannot say *which* analyses it renders. Add an
   optional `generated_by` (analysis FK or small link table) to both, per
   the DMTA note's explicit-cache rule ("stamped with the analysis that
   produced it").
2. **`experiment_tables` mutability.** The entity is a display cache, yet
   it is PATCHable — a hand-editable, unstamped summary is exactly the
   `result_summary` failure mode in a new shape. Either drop its PATCH or
   make edits versioned/audited; socially, keep steering claims into
   `Analysis` rows (the upload conversion already does).
3. **`analysis_inputs` (analysis → analysis).** Analyses can only cite
   measurements, but second-order work (a plate-level correlation computed
   from 96 per-well yield analyses) derives from *analyses*. Add an
   `analysis_inputs` M2M so the derivation chain is recorded at the level
   it actually happened.

## Open questions (deliberately not settled here)

- Units handling for the ledger (free string + convention vs a `unit` enum vs
  pint-style canonicalization at the boundary). Recommend: enum of the ~10
  units the lab actually uses (the solubility schema's `amount_unit_t` —
  mg/g/µL/mL/mol/mmol — is a good seed); canonicalize per-substance at write
  time.
- Whether plan `steps` stay JSONB (flexible, ontology-opaque) or become typed
  step blocks in `params.py` style. Start JSONB with a required `step_id`;
  type the step vocabulary once the agent's procedure language stabilizes.
- Where report *generation* runs (agent repo, using DB data) vs where reports
  are *stored* (here, as experiment files) — this note assumes generate-there,
  store-here.
- Whether `Note` needs full-text search from day one (probably not; add a
  GIN index when the corpus exists).
