# ELN UI Plan — agent-designed, live-visualized, later-analyzed experiments

**Status:** proposed plan for review. **Read-only survey — no code changed, no services restarted.**
**Date:** 2026-07-16
**Scope:** a cross-repo plan to build the ELN (electronic lab notebook) *user
experience*: (1) an agent converses with a human to **design** an experiment
plan, (2) the plan **executes** with a live visualization, (3) results are
**analyzed** afterward — the DMTA loop, made operable from one UI. This is a
**cross-repo ELN plan** spanning four repos. It lives in `ac-organic-lab/docs/`
— the central place lab-stack context is kept — alongside its companions
[`AGENTIC_ELN_ASSESSMENT.md`](AGENTIC_ELN_ASSESSMENT.md) (the why) and
[`WORKFLOW_UI_DESIGN.md`](WORKFLOW_UI_DESIGN.md) (the execute/monitor surface).

> Ground-truth used: [`ANALITICADB_ELN_LIMS_DESIGN.md`](ANALITICADB_ELN_LIMS_DESIGN.md)
> (the ELN/LIMS record-layer design; Plan/Note/Analysis), this repo's
> `../skills/src/lab_skills/{plan.py,mcp.py}` (`execute_plan`, `PlanRunReport`,
> the MCP tool surface), [`../LAAGENTEANALITICA_ASSESSMENT.md`](../LAAGENTEANALITICA_ASSESSMENT.md)
> (agent-UI maturity + the integration gap), `../../LaAgenteAnalitica/`
> (`architecture-considerations.md` analysis-plan-as-data, the graphchat UI,
> `docs/persistence-boundaries.md`), `../../organic-solubility/docs/*` (recipe →
> Plan mapping), and `../../opentrons-server/docs/DECK_STATE_PLAN.md` (the OT-2
> deck/labware/tip state model the execution view renders). Where a capability
> is shipped vs. planned, it is flagged.

---

## TL;DR

1. **All the hard backend pieces already exist; the ELN is mostly a *UI + wiring* project.** The record layer (`Plan`/`Experiment`/`Note`/`Analysis`, versioned, approval-gated) shipped in AnaliticaDB Phase 1. The execution layer (`execute_plan`, `validate_plan`, `PlanRunReport`, a control-capable **MCP server** `lab-skills mcp serve --allow-control`, and a read-only lab-history MCP) shipped in `ac-organic-lab` v0.4. The UI shell (graphchat: Yjs rooms, workspace explorer, **deferred-tool human-in-the-loop approval**, model picker, NPZ/molecule viewers) is production-grade in LaAgenteAnalitica. **The analysis third of the loop is basically done.**
2. **The one structural gap is that LaAgenteAnalitica lives *outside* the lab's SDK/claim/MCP fabric** (`LAAGENTEANALITICA_ASSESSMENT.md` bottom line). It can analyze data and CRUD AnaliticaDB, but it has **no client for the lab MCP surface**, so it cannot yet build or drive an experiment `Plan`. The highest-leverage single change is a **lab-MCP client toolset** in the agent — consumed exactly like it already consumes AnaliticaDB's versioned `ontology.json`.
3. **The design-conversation UX is already prototyped — for the wrong plan type.** LaAgente's `architecture-considerations.md` §5 defines a *dynamic-but-lockable* `AnalysisPlan`: discuss → `add_step`/`preview_plan` → user approves → `lock_plan` → run only locked plans. **Generalize that exact pattern to an *experiment* Plan** and the design surface falls out. The agent composes only from the lab-skills catalog (`list_skills`), so it "cannot invent a step with no implementation" — the same safety invariant.
4. **Execution visualization is a read-model over things that already emit.** `execute_plan` returns a `PlanRunReport` with per-step `{step_id, status ∈ succeeded|blocked|failed|skipped, violations, response}`; live device truth is on `/api/equipment` (`/status` envelopes) and the OT-2 gateway's `details.snapshot.deck` + `details.loaded_plate` (per-well `sample_id`/`volume_ul`). The execution view is a room panel that renders the step ledger + a deck/plate heatmap from those two sources. No new device work.
5. **Two seams need honest design, not just wiring:** (a) **claim coupling** — the agent's Agilent path and the lab's claim protocol don't share a lease today; agent-issued `execute_plan` must go through the lab claim so dashboard and agent can't both actuate; (b) **approval identity** — an AnaliticaDB `Plan` enters `approved` only via a *human principal*; the UI's approval click must carry the authenticated GraphChat user as `author_kind=human`, and for git-authored protocols the approval is the PR merge (CODEOWNERS), not a UI button.

---

# PART A — What each repo owns (the four seams)

The ELN is one loop across four repos. Each owns exactly one seam; the plan's
job is to connect them, not to move responsibility.

| Repo | Owns | ELN role |
|---|---|---|
| **LaAgenteAnalitica** (`graphchat/` UI + `chat.py` agent runtime, pydantic-ai v2) | the human↔agent conversation, the room workspace, deferred-tool approval, analysis capabilities | **the ELN front end** — all three surfaces render here |
| **ac-organic-lab** (`lab-skills` SDK + `api/` dashboard + MCP servers) | real-time execution: `validate_plan`/`execute_plan`, the skill catalog, claims/interlocks, live `/status`, the control-capable + read-only MCP servers | **the execution engine the agent drives + the live-state source the view reads** |
| **organic-solubility** (+ `organic-hte-template`) | the git-authored, PR/CODEOWNERS-approved, CI-`step_id`-stable **protocol**; renders into a `Plan` at run start | **the reviewed procedure** a Plan is an instance of |
| **AnaliticaDB** | the durable record: `Plan` (versioned, `draft→approved→executing→completed`), `Experiment` (lifecycle), `Note` (append-only, step-anchored), `Measurement`, `Analysis`, reports | **the notebook itself** — what was planned, observed, concluded |

**The loop the ELN realizes** (from `ANALITICADB_ELN_LIMS_DESIGN.md` §4):
`Plan (intent) → Notes (execution actuals) → Measurements (instrument
observations) → Analyses (interpretations) → Report → next Plan`.

## A.1 Design — the record layer already models it

`Plan` is a first-class **versioned** entity (revision = insert new version via
`supersedes`, never update). It holds structured `steps` (JSONB, each with a
stable `step_id`), `created_by` + `author_kind` (`human|agent`), a
`session_id`/trace back-reference to the chat room where it was negotiated, and
optional `source_commit` + `protocol_path`. Its lifecycle is
`draft → approved → executing → completed | abandoned`, and **entering
`approved` requires a human principal** — that is the ELN witness/sign-off, and
the durable target INTERLOCKS layer 4 (`validate_plan`) records against.
Crucially, the design says **store the decision artifact and its version
history, not the chat transcript** (the transcript stays in the room; the DB
holds v1 agent-draft → v2 human-edit → v3 approved). *ELN core (Plan + Note +
Analysis) shipped 2026-07-03, contract 0.6.0.*

## A.2 Design — the conversation UX already exists (for analysis)

LaAgente's `architecture-considerations.md` §5 ("Dynamic-but-lockable
pipelines") is the blueprint, applied today to an `AnalysisPlan`:

- **Plan as data** — `list[StepSpec]`, each `StepSpec` an `op` (Literal over
  *implemented* ops) + validated `params` + optional `on_fail`.
- **Interactive editing is Tier-1, pre-lock** — tools `add_step`,
  `remove_step`, `preview_plan`, `lock_plan`; flow is *discuss → edit → preview
  → user approves → lock*. After `locked=True`, editing tools refuse; the run
  tool executes **only** locked plans.
- **Safety invariant** — the agent composes only from implemented ops; users
  get free rein over *ordering and parameters*, never over *what code can run*.

Generalize `op ∈ implemented analysis ops` to `skill ∈ lab-skills catalog` and
`lock_plan` to the AnaliticaDB `Plan.approved` transition, and this **is** the
experiment-design surface.

## A.3 Execute — the engine is shipped and claim-safe

`lab_skills.execute_plan(plan, session, *, owner, dry_run=False,
wait_timeout_s=…)` (`../skills/src/lab_skills/plan.py:311`) runs a validated Plan
sequentially: offline `validate_plan` once, then **per step** re-checks
layer-4 interlocks + layer-3 live `allowed_actions`, acquires a **per-step
claim**, POSTs the skill endpoint with the claim token, and **fails fast**
(first `failed`/`blocked` aborts; the rest report `skipped`). It returns a
`PlanRunReport { ok, dry_run, validation, steps:[StepRunReport], claims_acquired }`,
where each `StepRunReport` carries `{step_id, step_index, role, skill, status,
equipment_id, claimed, response, error, violations}`. `dry_run=True` is a live
preflight that runs every check but never actuates.

The agent reaches this through the **MCP server** (`../skills/src/lab_skills/mcp.py`,
`lab-skills mcp serve`): `list_equipment`, `list_skills` (live catalog with
per-skill availability + reason), `get_status`, `validate_plan`,
`preflight_plan` are always on; **`execute_plan` is registered only under
`--allow-control`**. Resources `lab://equipment` and `lab://status/{target}`
expose the registry + live envelopes. This is the intended agent entry point
(ARCHITECTURE decision #7, LG6).

## A.4 Execute — the live-state the view renders

Two read sources, both already populated:

- **Step ledger** — the `PlanRunReport` (above), streamed as steps complete.
- **Device truth** — `GET /api/equipment` on the dashboard (every device's
  `/status` envelope: `equipment_status`, `components`, `metrics`,
  `allowed_actions`, `details`). For the OT-2 specifically, the gateway
  publishes `details.snapshot.deck` (normalized, provenance-tagged per slot:
  `slots["1".."12"] → {type, load_name, is_tiprack, …}`) and
  `details.loaded_plate` (`{plate_id, model, wells:[{well, sample_id,
  volume_ul, notes}]}`) — *shipped, deployed 2026-07-10; HTTP-transport deck
  parity validated 2026-07-14* (`../../opentrons-server/docs/DECK_STATE_PLAN.md`).
  For history/observability the read-only **lab-history MCP**
  (`../api/app/mcp_server.py`) already exposes `query_equipment_events`,
  `query_service_uptime`, `query_runs`, `query_well_results`.

So a live plate heatmap + deck view + step timeline are all *read models* over
existing surfaces — the execution view adds a panel, not device code.

## A.5 Analyze — largely done

LaAgente ships production LC-MS/LC-UV (atomic tool surface), NMR, GC-MS
capabilities (NMR/GC currently disabled pending the pydantic_graph v2 port),
the AnaliticaDB **data-catalog browser** (stage measurement files into the
room), the interactive NPZ heatmap viewer, and 16 AnaliticaDB CRUD tools
generated from the versioned `ontology.json`. `Analysis` is a first-class
versioned entity (M2M to measurements; zero-measurement + `method`=model-id =
a *prediction*); reports are experiment-level `role="report"` artifacts. The
remaining ELN work here is **loop-closing wiring**, not new analysis.

## A.6 The gap (from [`../LAAGENTEANALITICA_ASSESSMENT.md`](../LAAGENTEANALITICA_ASSESSMENT.md))

- **Not on the lab SDK.** LaAgente predates/sidesteps `lab-skills`,
  STATUS_SPEC, and the MCP catalog. Integration point (b) in the assessment —
  "teach the agent to consume the lab MCP surface" — **does not exist yet**.
  This is the linchpin: without it there is no Design or Execute, only Analyze.
- **No shared claim** between the agent's Agilent path
  (`agilent-hplcms-server` direct) and the lab claim protocol. If both the
  dashboard and the agent can issue runs, the coupling is unmodeled.
- **Approval identity** must be a human principal (A.1) — the UI already has
  authenticated users (ac_auth); the click must stamp `author_kind=human`.

---

# PART B — The three ELN surfaces (target UX)

One room, three tabs on the existing workspace shell (chat stays the
coordination panel; workspace is the work surface, per
`grafico-workspace-native-workflow.md`).

### B.1 Design surface — "let's plan an experiment"
Conversational plan-builder. The human states intent ("caffeine solubility in
DMSO/EtOH, 5 mg/well, seal, shake, read A600, then HPLC the supernatant"). The
agent drafts a `Plan` by composing lab-skills catalog steps (validated live via
`validate_plan`/`preflight_plan`), renders a **plan preview** (step list +
per-step role/skill/args + validation warnings + estimated duration/devices),
and iterates with the human (`add_step`/`edit_step`/`remove_step`). A
**"Approve" action** (human principal) transitions the AnaliticaDB `Plan` to
`approved` and locks it. For a campaign with a git protocol
(`organic-solubility`), the agent instead renders the **protocol** into a Plan
and approval is the PR merge — the UI shows the CODEOWNERS gate, not a button.

### B.2 Execution surface — "watch it run"
On approve→run, the agent calls `execute_plan` (through `--allow-control` MCP,
behind the **existing deferred-tool approval** gate + the lab claim). The panel
renders: a **step timeline** (from streamed `StepRunReport`s — running / done /
blocked-with-violation / failed-with-error / skipped, keyed by `step_id`), a
**live deck + plate heatmap** (from `/api/equipment` `details.snapshot.deck` /
`loaded_plate`, polled ~2–3 s), and a **device tile row** (claim holder,
`equipment_status`, key metrics). Deviations/observations the human types are
written as append-only `Note`s anchored to `(plan_id, step_id)`.

### B.3 Analysis surface — "what did we get"
Post-run, measurements are already in AnaliticaDB (step-correlated via
`(plan_id, step_id)`). The chemist browses them in the catalog, stages files,
runs the LC-MS/NMR capabilities conversationally, and **commits** accepted
results as `Analysis` rows (M2M to the run's Measurements) via the existing
`commit_analysis_result` gate. A **generate-report** action assembles an
experiment-level `role="report"` artifact. The report's conclusions seed the
**next Plan** — closing the loop in the same room.

---

# PART C — Proposed plan (smallest-change-first, reversible)

Ordered so each step is independently useful and low-risk. Scope tag = which
repo changes. Nothing here requires new *device* code.

### Step 0 — Reconcile terminology + write this plan into the canon — *docs only, zero runtime risk*
Register this doc in the `ac-organic-lab/README.md` docs router (alongside the
other ELN notes it now sits with). Pin the vocabulary once:
**protocol** (git template) ≠ **Plan** (rendered, versioned run) ≠ **workflow**
(execution engine) ≠ **AnalysisPlan** (LaAgente's post-hoc analysis pipeline).
Reuse the LaAgente `architecture-considerations.md` §5 plan-as-data language for
the experiment Plan so the two plan concepts stay visibly parallel, not
conflated. *Unblocks shared language for everything below.*

### Step 1 — Lab-MCP client toolset in the agent (**the linchpin**) — *LaAgenteAnalitica*
Add a `domains/lab/` toolset that is a client of `lab-skills mcp serve`,
mirroring how `domains/analytica_db/` consumes `ontology.json`: version-pinned,
fail-fast, compact summaries. Start **read-only** (`list_equipment`,
`list_skills`, `get_status`, `validate_plan`, `preflight_plan`) — no
`--allow-control`. This alone lets the agent *reason about* the lab and draft
validated plans. Reversible: it's a registry-gated toolset (`enabled=false`
default), exactly like the existing disabled KG/GC toolsets.

### Step 2 — Experiment-plan-as-data + design surface — *LaAgenteAnalitica + AnaliticaDB (wiring only)*
Generalize the §5 `AnalysisPlan` pattern to an `ExperimentPlan` whose steps'
`skill` is drawn from `list_skills` (Step 1). Tools: `add_step`/`edit_step`/
`remove_step`/`preview_plan` (validation via `validate_plan`), and
`register_plan` that writes/updates the AnaliticaDB `Plan` (draft) via the
existing CRUD tools. Frontend: a **PlanPreview panel** (React, alongside
`CatalogPanel`/`AgilentStatusPanel`) rendering the step list + validation
warnings + a diff across Plan versions. Approve = a human-principal click →
`Plan.approved`. *Reversible: draft plans are just DB rows; nothing actuates.*

### Step 3 — Claim-safe `execute_plan` behind the approval gate — *LaAgenteAnalitica + ac-organic-lab (config)*
Enable the `execute_plan` MCP tool by pointing the agent at a
`--allow-control` server instance, and route it through the **existing
deferred-tool approval UI** (human-in-the-loop) so a human confirms actuation.
`owner` = the authenticated GraphChat user (audit + `details.claimed_by`).
Resolve seam (a): the agent's actuation must acquire the lab claim (which
`execute_plan` does per-step) so it and the dashboard mutually exclude — and
decide the Agilent path's relationship to the lab claim (either front it behind
the lab claim or document the split). *Reversible: gated by both `--allow-control`
and the approval click; default-off.*

### Step 4 — Execution visualization panel — *LaAgenteAnalitica*
A room panel that streams `PlanRunReport` step outcomes (timeline keyed by
`step_id`, with `violations`/`error` surfaced) and polls `/api/equipment` for
the live deck/plate heatmap (OT-2 `details.snapshot.deck` + `loaded_plate`) and
device tiles. Reuse the NPZ/heatmap rendering machinery already in the
workspace. Read-only; no new backend. *Reversible: pure read-model.*

### Step 5 — Step-anchored Notes (the notebook narration) — *LaAgenteAnalitica + AnaliticaDB (Note API)*
During/after a run, human- and agent-authored observations/deviations become
append-only `Note`s anchored to `(experiment_id, plan_id, step_id)` via the
Note API (ELN core shipped; the `note_files` template is the hh-branch
`experiment_files` work). This is what makes the run a *notebook entry*, not
just a job log. *Reversible: append-only rows; no mutation.*

### Step 6 — Close the analysis loop — *LaAgenteAnalitica (wiring)*
Wire committed `Analysis` rows M2M to the run's Measurements (step-correlated),
and add a **generate-report** action producing an experiment `role="report"`
artifact from the accepted analyses. Surface a "start next Plan from this
report" affordance in the same room. *Analysis capabilities already exist; this
is linkage + a report template.*

### Step 7 — Protocol-first path for campaigns — *organic-solubility + organic-hte-template*
For repo-backed campaigns, the agent renders the git **protocol** into a Plan
(carrying `source_commit`/`protocol_path`) rather than free-composing, and the
design surface shows the **PR/CODEOWNERS** approval as the sign-off. This is the
`organic-solubility` recipe → Plan mapping already sketched
(`docs/analiticadb_record_layer.md`; first slice landed 2026-07-04 with stable
`step_id`s + Plan registration). *Additive; free-compose path from Steps 2–3
still works for ad-hoc runs.*

### Prioritization rationale
Steps 1–2 deliver the **Design** third with zero actuation risk and are the
long pole (the missing lab-MCP bridge). Steps 3–5 deliver **Execute + notebook**
and are where the safety seams live — hence gated, reversible, human-in-the-loop.
Step 6 is mostly linkage on an already-strong **Analyze** base. Step 7 is the
production-grade path but shouldn't block the ad-hoc loop. Every step is
independently shippable and behind a default-off flag until proven.

---

## Open questions (decide before building, not settled here)

1. **Where does `--allow-control` run, and who may trigger it?** One shared
   control-MCP for all rooms, or per-run/per-user? (Ties to the ROADMAP
   control-surface-exposure + AUTH_DESIGN work — the agent becomes a fifth
   writer competing for claims.)
2. **Agilent claim reconciliation** — front `agilent-hplcms-server` behind the
   lab claim, or keep the agent's read-only-status + operator-submits split and
   document it as an accepted exception?
3. **Live step streaming transport** — `execute_plan` returns a *final*
   `PlanRunReport`; the panel needs intermediate progress. Poll `/api/equipment`
   + lab-history events for coarse progress, or add a per-step event stream
   (an MCP-tool progress channel, or device-pushed `equipment_events`)?
4. **Plan step granularity vs. the OT-2** — a lab-skills Plan step is one skill
   on one role; an OT-2 protocol is many pipetting ops. Does the ELN visualize
   at Plan-step granularity, or drill into the gateway's `loaded_plate`/deck for
   sub-step detail? (Affects how "one step" reads in the timeline.)
5. **Rooms ↔ Experiments** — one room per Experiment, or one room spanning a
   campaign of Experiments? The `session_id` back-reference on `Plan` assumes a
   stable mapping.
