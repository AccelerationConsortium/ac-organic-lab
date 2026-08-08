# Agentic ELN — Implementation Plan

**Status:** consolidated plan (2026-07-22), updated (2026-07-23). Phases A–C
shipped in `bitacora`; Phase D pending. Sequencing and open decisions for
the design in [`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md); the record
layer it builds against is [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md). This
document merges the former `ELN_UI_PLAN.md` Part C (Steps 0–7) and the
former `AGENT_ASSISTED_HTE_WORKFLOW.md` phases (A–G) into one track, and
collects every open decision in one place (§4).

Ordering principles: smallest-change-first, each step independently useful,
everything reversible or behind a default-off flag, actuation always behind
both a control gate and a human approval.

---

## 1. Ground rules for the build

- **Vocabulary is pinned** (do not conflate): **protocol** (git template) ≠
  **Plan** (rendered, versioned run record in AnaliticaDB) ≠ **workflow**
  (execution engine) ≠ **AnalysisPlan** (LaAgente's post-hoc analysis
  pipeline).
- **Nothing here requires new device code.** Execution visualization is a
  read-model over surfaces that already emit (`PlanRunReport`,
  `/api/equipment`, OT-2 deck snapshot).
- The binding contracts gate everything: only `main`-merged, human-approved,
  validated, authorized plans execute; all hardware through the `lab-skills`
  SDK; no run data in git.

## 2. Track 1 — ELN loop wiring (from the former ELN UI plan)

The linchpin first; safety seams gated and reversible.

### Step 0 — Terminology + canon registration — *docs only* ✅ superseded
Register the consolidated docs in the README docs router; pin the vocabulary
(§1). (This consolidation completes Step 0.)

### Step 1 — Lab-MCP client toolset in the agent (**the linchpin**) — *LaAgenteAnalitica*
A `domains/lab/` toolset that is a client of `lab-skills mcp serve`,
mirroring how `domains/analytica_db/` consumes `ontology.json`:
version-pinned, fail-fast, compact summaries. Start **read-only**
(`list_equipment`, `list_skills`, `get_status`, `validate_plan`,
`preflight_plan`) — no `--allow-control`. This alone lets the agent reason
about the lab and draft validated plans. Reversible: registry-gated toolset,
`enabled=false` default.

### Step 2 — Experiment-plan-as-data + design surface — *LaAgenteAnalitica + AnaliticaDB (wiring only)*
Generalize the dynamic-but-lockable `AnalysisPlan` pattern to an
`ExperimentPlan` whose steps' `skill` comes from `list_skills` (Step 1).
Tools: `add_step`/`edit_step`/`remove_step`/`preview_plan` (validated via
`validate_plan`) + `register_plan` writing the AnaliticaDB `Plan` (draft).
Frontend: a PlanPreview panel (step list + validation warnings + version
diff). Approve = human-principal click → `Plan.approved`. Reversible: draft
plans are just DB rows; nothing actuates.

### Step 3 — Claim-safe `execute_plan` behind the approval gate — *LaAgenteAnalitica + ac-organic-lab (config)*
Enable the `execute_plan` MCP tool via a `--allow-control` server instance,
routed through the existing deferred-tool approval UI. `owner` = the
authenticated user (audit + `details.claimed_by`). Resolve the **claim
coupling seam**: agent actuation must acquire the lab claim (which
`execute_plan` does per-step) so agent and dashboard mutually exclude; decide
the Agilent path's relationship to the lab claim (§4 D-9). Reversible: gated
by both `--allow-control` and the approval click; default-off.

### Step 4 — Execution visualization panel — *LaAgenteAnalitica*
A room panel streaming `PlanRunReport` step outcomes (timeline keyed by
`step_id`, violations/errors surfaced) and polling `/api/equipment` for the
live deck/plate heatmap + device tiles. Pure read-model; reuse the NPZ/heatmap
machinery. The dashboard-side runner endpoint + SSE event contract is
specified in [`UI_DESIGN.md`](UI_DESIGN.md) §3 and is the
first build of its §3.7 list.

### Step 5 — Step-anchored Notes — *LaAgenteAnalitica + AnaliticaDB (Note API)*
Human- and agent-authored observations/deviations become append-only `Note`s
anchored to (`experiment_id`, `plan_id`, `step_id`). This is what makes a run
a notebook entry rather than a job log. Reversible: append-only rows.

### Step 6 — Close the analysis loop — *LaAgenteAnalitica (wiring)*
Committed `Analysis` rows M2M to the run's step-correlated Measurements; a
generate-report action producing an experiment-level `role="report"`
artifact; a "start next Plan from this report" affordance. Linkage, not new
analysis.

### Step 7 — Protocol-first path for campaigns — *organic-solubility + bitacora/templates/hte*
For repo-backed campaigns the agent renders the git **protocol** into a Plan
(`source_commit`/`protocol_path`) instead of free-composing; the design
surface shows the PR/CODEOWNERS gate as the sign-off. Additive — the ad-hoc
path from Steps 2–3 still works.

## 3. Track 2 — Planning page & authorization pipeline (from the former workflow doc)

Extends Track 1; phases note their dependencies.

1. **Phase A — repository lifecycle.** Repo stamping + ruleset + project
   registry + server workspace service (bare clone + per-session worktrees).
   **Done (2026-07-23):** FastAPI lifecycle service shipped in `bitacora`.
   **Stamping shipped (2026-07-25)** as seed-from-local — empty org repo + one
   root commit of the customized template tree, ruleset last (see DESIGN §7,
   revised: `/generate` was unimplementable over a subdirectory template).
   Live use pends the org GitHub App installation (App currently installed on
   a personal account; permissions verified sufficient).
2. **Phase B — canonical edit path + visual read view.** The protocol edit
   service (typed edits, schema re-validation, attributed commits). **Done
   (2026-07-23):** editor + schema shipped. Visual read view (Next.js) deferred —
   comes after API is stable.
3. **Phase C — agent + AG-UI.** Project-scoped planning agent streaming over
   AG-UI, editing through the same edit service; conversation store; decision-record
   drafting. **Done (2026-07-23):** lab-skills client, conversation store, agent
   session, tool registry, AG-UI SSE streaming. Fable-reviewed (6 fixes applied),
   native-async refactored. 73 tests passing. Lab client wiring (needs
   `equipment.yaml`) is a deployment concern.
4. **Phase D — PR + scientific diff.** PR open/update from the page; the
   rendered scientific-diff CI job; the Review tab. **Done (2026-07-27):** room
   branch pushed as the App, PR opened/updated idempotently with a structured
   scientific diff as its body (steps, and since 2026-07-28 the `design` /
   `plate_map` blocks summarized), `GET …/diff` + Review tab. Exercised end to
   end on `AccelerationConsortium/sdl-safety-agent` PR #1, merged by a human
   CODEOWNER. The diff is a *rendered summary in the PR body*, not yet a CI job.
5. **Phase E — run authorizer + compiler (dry-run first).** Authorization pins +
   revalidation + package digest; compiler in dry-run (compile + simulate,
   nothing actuates). **Done (2026-07-30), dry-run only:**
   `POST /projects/{id}/authorizations` pins the commit (verified an **ancestor
   of the default branch** — unreviewed branch work cannot run), protocol path,
   `TEMPLATE_VERSION` + `pins.yaml` read *at that commit*, the compiled package
   digest + compiler version, the live-readiness verdict (SDK dry-run
   preflight), and the approving human; immutable `authorization_id`, TTL per
   D-2, revocable without erasure; Authorize tab in the room UI.
   **The compiler resolves the action↔skill question** the first authored plate
   exposed: a protocol `action` is *chemistry* vocabulary (schema-constrained,
   `^[a-z0-9][a-z0-9_]*$`), a skill id is *platform* vocabulary (dotted, owned
   by the SDK catalog), and translating between them is the compiler's job — not
   a mismatch to fix. The map lives in each project's own repo
   (`compile/actions.yaml`, per D-6). An unmapped action or unresolved parameter
   **fails** compilation; the package digest covers `design` and `plate_map`, so
   a plate relayout with identical steps is a different package.
   Still open on E: the **required-checks gate** is inert until the GitHub App
   is granted `actions: read` (authorization refuses unless a human passes a
   recorded `override_checks_reason` — an unverifiable gate that silently passes
   is worse than none); authorizations are stored **locally**, pending the
   AnaliticaDB contract bump for run-authorization linkage
   ([`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) §"Run-authorization linkage");
   real lots/barcodes await LIMS Phase 2.
6. **Phase F — authorized execution.** Wire run authorizations into the
   [`UI_DESIGN.md`](UI_DESIGN.md) §3 runner (Track 1 Steps
   3–5); orchestrated runs consume packages; records carry the five pins
   (repo URL, commit SHA, protocol path, `authorization_id`, package digest).
7. **Phase G — hardening.** Inventory-backed material validation (LIMS Phase
   2 of [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md)), authorization TTLs,
   conversation-store retention/redaction policy (D-17), template schema
   growth.

## 4. Action items — pending decisions

Every open decision across the consolidated docs, in one list. Each blocks
the phase noted; none blocks phases before it.

| # | Decision | Recommendation | Blocks |
|---|---|---|---|
| **D-1** | ~~Sanitized transcript in git~~ — **RESOLVED (2026-07-22): no.** Interaction history lives in the chat layer's conversation store (single store, project-scoped rows); git carries only `decisions.md` with a deep link (`session_id`). See design §13. | — | — |
| **D-2** | **Run-authorization TTL / staleness policy — RESOLVED (2026-07-22):** short, ~one working day. A run authorization that isn't executed within ~1 day must re-validate (schema, units, labware, device readiness, inventory) before execution. Re-validation is cheap; re-running a stale plan against a lab that drifted is the failure to avoid. Tighten-first; loosen later once the system is trusted. | ~1 working day; re-validate on expiry | Phase E |
| **D-3** | **Which validation layers block merge vs. warn — RESOLVED (2026-07-22):** only the rock-solid checks block to start (schema validity, `step_id` uniqueness/permanence rules); heuristic checks (units, labware feasibility, device capability, scientific coherence, workflow completeness) warn and are visible to the reviewer but don't stall the merge. Promote a heuristic to blocking only after it proves stable (no false negatives in practice). Tighten-first on what you trust; earn the rest. | Block on schema + `step_id` rules; warn on the rest; promote to blocking as heuristics prove stable | Phase D |
| **D-4** | **Session ↔ room mapping — RESOLVED (2026-07-22):** one room = one planning session = one branch = one draft Plan = one PR. The room's **starter is its owner** — the scientific reviewer who consents to the PR; others can join (labeled IDs) but the starter owns approval. Competing plans → separate rooms → separate PRs → CODEOWNER picks. Owner is always a human (agent can draft, not own). Branch minted lazily on first edit; see design §6/§15. **Worktree GC — RESOLVED (2026-07-22):** never-edited rooms (no branch minted) clean up silently and immediately. Edited rooms, after close: idle 0–7 days with no nudge; 7–30 days show a daily red reminder in the UI that the worktree is idle and will be cleaned on day 30; on day 30 the server worktree + git branch are deleted (merged branches already gone via GitHub auto-delete; abandoned branches deleted now). | 7-day silence → 23-day red daily reminder → day-30 cleanup; never-edited rooms GC silently on close | Phase A |
| **D-5** | **GitHub org placement + App permission scope — RESOLVED (2026-07-22):** **Org:** the admin (the user) owns the repos, in their own org (not `AccelerationConsortium`). **App scope:** a GitHub App scoped to the lab org only, with `contents:repo` (read/write repo contents), `pull_requests:write`, `rulesets:write` (branch protection), and the repository-creation permission (to stamp new project repos from the template) — nothing else. Least privilege; private key in the platform's secret store, never in a workspace or the browser. Add permissions later only if a real need appears. | GitHub App scoped to the lab org; `contents:repo` + `pull_requests:write` + `rulesets:write` + repo-creation only | Phase A |
| **D-6** | **Where the run authorizer and compiler live — RESOLVED (2026-07-22):** both start as modules inside `bitacora` beside the workflow runner (same repo, same deploy, one log stream). Split out into a separate service only if they grow. **Compiler:** core engine in `bitacora`; per-project chemistry configs and rules in the template repo (`scripts/` or `compile/` dir) so each campaign customizes its own vocabulary/units/rules without touching the core. **Renamed:** "release service" → "run authorizer"; the artifact it produces is a "run authorization" (was "a release"); the act is "authorizing a run" (was "releasing for execution"). | Run authorizer + compiler core in `bitacora`; per-project configs/rules in the template; split out only if they grow | Phase E |
| **D-7** | **Protocol schema growth ownership — RESOLVED (2026-07-22):** `bitacora` hosts multiple templates (one per experiment type), and the **HTE template** is one of them — it contains the core structural blocks (design matrix, plate map, step list, QC, materials). Other templates (synthesis, characterization, etc.) can be added alongside it later. Per-project chemistry extensions stay in the project repo (chemistry-specific vocabulary, validated alongside the core template schema in CI). **Template repo absorbed (Option B):** the existing `organic-hte-template` repo is folded into `bitacora` as `bitacora/templates/hte/` (skeleton + schema together); new projects are stamped from `bitacora` directly; no separate template repo. `pins.yaml` in each project repo pins the bitacora template version it conforms to (same cross-repo pin pattern as the AnaliticaDB ontology pin). **Landed (2026-07-22):** template absorbed as `bitacora/templates/hte/` at `TEMPLATE_VERSION` 1.2.0 (pin renamed `organic-hte-template` → `bitacora-hte-template`; see template `CHANGELOG.md`); `bitacora` commit `e5374f2`. | Core structural blocks in `bitacora/templates/hte/`; chemistry-specific vocabularies per-project; `organic-hte-template` absorbed into `bitacora` | Phase B |
| **D-8** | **Agent backend runtime — RESOLVED (2026-07-22):** new project-scoped agent in the `bitacora` repo, inspired by LaAgenteAnalitica's patterns but not depending on it. The ELN agent needs capabilities LaAgenteAnalitica doesn't have (Undermind literature search, protocol editing, git workflow, authorization pipeline, lab-skills MCP) whose shapes don't fit its abstractions; building fresh lets the abstractions fit the domain. The AG-UI seam stays clean by construction. See design §1/§6. | — | — |
| **D-9** | **Agilent claim reconciliation — RESOLVED (2026-07-22):** front the Agilent path behind the lab claim, **split by intent**. *Actuating* (`/control/*`, run acquisition): the agent goes through the SDK claim like everything else — the binding contract (all hardware via `lab-skills`, never raw `/control/*`); required before Step 3 ships actuation. The Agilent server already implements the STATUS_SPEC v1.1 claim (single slot, TTL, workflow lock, token-gated `/control/*`); the gap was LaAgenteAnalitica's path not acquiring it, so agent and dashboard could drive the instrument simultaneously. *Read-only status* (`/status`, live metrics): documented as an **accepted exception** — reads don't mutate and must not queue behind a claim holder; the claim prevents concurrent *control*, not concurrent *observation*. Not the stricter "claim even for reads" (passive monitoring would contend with runs). | Actuating via SDK claim (binding); read-only status = accepted exception, documented | Track 1 Step 3 |
| **D-10** | **Where `--allow-control` runs and who may trigger it — RESOLVED (2026-07-22):** **one** control-capable `lab-skills mcp serve` per deployment, on the platform host — not per-room, not per-user, not on the agent's host. One chokepoint = one audit stream = one place to revoke. **Allow-listed users** only (the `ac_auth` roster); the agent never holds the control surface directly (design §14: no `lab-skills` control surface from the planning agent). **Every call audited** with the authenticated principal stamped. Per-room/per-user instances multiply attack surface and audit streams for no isolation benefit — claims/interlocks already provide mutual exclusion *at the device*, so process-level separation buys nothing. Matches UI_DESIGN §"one per-deployment instance, allow-listed users". Ties to AUTH_DESIGN + the control-surface-exposure risk in ROADMAP. | Per-deployment single instance on the platform host; allow-listed users; every call audited | Track 1 Step 3 |
| **D-11** | **Live step streaming transport** — `execute_plan` returns a final report; the panel needs intermediate progress. Poll `/api/equipment` + history events, or add a per-step event stream. | Start with the dashboard runner's SSE (already specified in UI_DESIGN §3); revisit if latency hurts | Track 1 Step 4 |
| **D-12** | **Plan step granularity vs. the OT-2** — visualize at Plan-step granularity or drill into the gateway's deck/plate detail for sub-step progress. | Timeline at Plan-step granularity with an expandable OT-2 drill-down | Track 1 Step 4 |
| **D-13** | **DB: ledger units handling — RESOLVED (2026-07-22):** **named enum + write-time per-substance canonicalization** (not free string, not pint). A named enum of the ~10 units the lab actually uses, seeded from the solubility schema's `amount_unit_t` (mg/g/µL/mL/mol/mmol) plus count/dimensionless. Enum over free string because a ledger must *compute balances* — "5 mL" / "5 ml" / "5 mL " must be identical or the arithmetic drifts. Each `Substance` declares a canonical amount unit (liquid → mL/µL, solid → mg/g); every `ContainerAction` amount is normalized to it on write, so the ledger sums a single column with no read-time conversion. Not pint-style general canonicalization — the lab needs a closed vocabulary + per-substance consistency, not arbitrary unit algebra; pint adds a dependency and runtime conversion for a problem a ~10-member enum solves at the schema level. | Named enum (~10 units) seeded from `amount_unit_t`; canonicalize per-substance at write time; no pint | DB Phase 2 |
| **D-14** | **DB: `Plan.steps` JSONB vs. typed step blocks.** | Stay JSONB (required `step_id`) until the procedure vocabulary stabilizes, then type it | DB Phase 2 |
| **D-15** | **DB: report generation location** — generate in the agent repo, store in AnaliticaDB (assumed), or generate server-side. | Keep generate-there, store-here | Track 1 Step 6 |
| **D-16** | **Conversation-store engine + hosting — RESOLVED (2026-07-22):** greenfield Postgres JSONB, in a separate `conversations` database on the AnaliticaDB instance, as a thin persistence module inside `bitacora`. One engine to operate (same as AnaliticaDB), contract untouched, persistence boundary (§13) preserved. Not Mongo (no GraphChat to reuse — `bitacora` is its own platform), not inside the AnaliticaDB database/contract (would weld a redactable chat log onto an immutable record, force `SCHEMA_VERSION` bumps, double writes via `agent_actions`), not a second AnaliticaDB-style service (one writer = the agent backend persisting its own AG-UI stream; read surface = the chat UI's room history; no ontology export, no audit table, no public API until a concrete reader needs one). See design §13 for the three-tier record (conversation store / `decisions.md` / prose-free AnaliticaDB). | — | — |
| **D-17** | **Conversation-record access + retention policy — RESOLVED (2026-07-22):** the room's participants (all project members) can read it; the room's owner (the starter, design §6) consents to the PR; the PI can always read; redaction requires PI sign-off (tombstone, never silent delete); retain ≥ campaign lifetime. Every message attributed to its author (`author_kind` + principal ID; agent never posts under a human's ID). See design §13. | — | — |
| **D-18** | **Planning page frontend hosting — RESOLVED (2026-07-22):** the frontend lives in `bitacora` (its own Next.js app, adopting the GraphChat layout pattern from design §6). Not inside GraphChat (the platform is its own thing, not an extension of the analytical-chemistry agent), not in `ac-organic-lab/web/` (the dashboard is a projection layer, not the agent's home). `bitacora` is a peer of `ac-organic-lab`, reached over the tailnet. | — | — |
| **D-19** | **`bitacora` deployment host + initial substrate — RESOLVED (2026-07-22):** develop locally on the MacBook; production target is the Linux data/platform host (`100.64.254.6`) as its own systemd service and port, path-routed behind the existing single-edge SSO — same machine as the dashboard/AnaliticaDB, but a separate repo, process, deploy, operational database, and trust boundary. Not on a device PC and not inside the AnaliticaDB service. Start as a uv workspace with a FastAPI app member (room for the project-scoped agent later), a `bitacora`-local project registry, config-driven gitignored secret/workspace paths, and API-only Phase A; the Next.js planning surface begins in Phase B. | Scaffold uv workspace + FastAPI API; deploy later as separate systemd unit on `100.64.254.6` behind SSO | Phase A |
| **D-20** | **Where the Phase F run executor lives — RESOLVED (2026-08-08): the dashboard (`ac-organic-lab/api/`), not `bitacora`.** Bitácora issues the authorization; the dashboard runs it. The operator sees one surface either way — the ELN is already framed at `/workflows` on the dashboard's own origin — so this was never a UX question, only *which process holds the claim and writes the audit row*. The dashboard already owns that path end to end (`middleware.ts` injects the verified `X-Auth-User`; `control.py` does `_authorize_control` → `_acquire_claim` → action → release-in-`finally` → `_record_control_event`), which is the same argument UI_DESIGN §5 makes for assistant control mode adding *no new trust surface*. A runner in `bitacora` would rebuild all of it and reopen the audit gap the OT-2 embed already had to close device-side. Note this revises design §12's aside that the authorizer "MAY start as an `api/` module beside the runner" — the authorizer stayed in `bitacora` (D-6) and only the runner moves. | Runner in `ac-organic-lab/api/`; `bitacora` keeps the authorizer | Phase F |
| **D-21** | **Authorization handover shape — RESOLVED (2026-08-08): pull, by `authorization_id`.** The runner does `GET /authorizations/{id}` against `bitacora` at run time and refuses unless `executable` (not revoked, not expired). Deliberately **not** push: a pushed package is true as of when it was sent, and revocation — *"that a run was once authorized and then withdrawn is itself part of the history"* — only works if the runner asks the authority at the moment it starts. The payload needs no translation: `package.steps` are already `lab-skills` plan steps (role + skill + typed args). The runner **re-computes `package_digest` from the published package** before executing; `bitacora` commit 87e30f8 moved every digest input into the package so a second implementation can do that without reassembling filename stems. Device readiness is *not* re-verified from the authorization's stored verdict (up to a day old) — `execute_plan` re-checks live `allowed_actions` and interlocks immediately before each step, which is the authority. | Pull by id; verify digest; refuse unless `executable` | Phase F |
| **D-22** | **Revocation mid-run — RESOLVED (2026-08-08): re-check `executable` between steps**, and treat a revocation as a cooperative abort (claims released, remaining steps `skipped`, exactly the shipped fail-fast semantics). Checking only at start makes an authorization un-withdrawable the moment a run begins, which matters most for the runs where it matters most — an 18 h incubation, not a 30 s transfer. | Re-check between steps; revocation aborts cooperatively | Phase F |
| **D-23** | **Where the run record goes — RESOLVED (2026-08-08): AnaliticaDB, per the mapping in [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) §"ELN artifacts → record layer".** A run is a `Plan` row under the campaign's `Experiment`; deviations and device faults are `step_id`-anchored `Note`s, so "where did this run go wrong" is a query. Not the dashboard's `lab.db`, which holds lab telemetry, not experiment data. **This is not blocked on an ontology bump** — contrary to the earlier reading, AnaliticaDB is live at `SCHEMA_VERSION 0.11.0` with `/plans`, `/notes`, `/experiments` and every field required (`PlanCreate.source_commit`/`protocol_path`/`steps`, `NoteCreate.step_id`/`kind`/`corrects`, `ExperimentCreate.operator`); what is missing is the cross-repo wiring. The first Phase F slice still writes no records, but its return shape must already be `Plan`-plus-`Note`s so that wiring is serialization rather than reverse-engineering. | Records in AnaliticaDB; first slice returns the shape without writing | Phase F |
| **D-24** | **How the runner authenticates to a device — RESOLVED (2026-08-08): the edge-injected identity the operator passthrough already uses, NOT a machine API key.** The runner presents `X-Auth-User` plus the shared secret (`control.py::_device_auth_headers`), reusing that function rather than reimplementing it. **Established by a failed real run, not by design review:** the first non-dry execution was refused `401 login_required`, and so was the second, after the runner was given an `ac_auth` API key. The key was *valid* — `/auth/verify` resolved it to its principal — but the OT-2 gateway **deliberately contacts no external auth service** ("so this gate is usable by anyone who deploys the gateway, not only by this lab"), so an issued key means nothing to it; it accepts only a static entry from its own `OT2_API_KEYS`, or edge headers. The edge path was already deployed and trusted: `DEVICE_EDGE_SHARED_SECRET` is set on the dashboard host, the gateway aliases `X-Edge-Auth` to its own `X-Edge-Key` *specifically* so the passthrough's spelling works, and a claim/release probe against `ot2_complexation` was accepted and released cleanly. **Rejected — a per-principal key in `OT2_API_KEYS`:** it would add a second credential path beside a working one, put the secret outside the roster's control (revoking in `roster.yaml` would not revoke it at the device), and record the run as `api:<name>` rather than a person. The automation principal created while pursuing that option was withdrawn — key revoked, roster entry removed. **Consequence:** the device stores the *human* in `details.claimed_by.owner` and in its own audit rows. For a long run a person's name sits on a claim after they have gone home — more honest than a robot's, and worth deciding rather than inheriting. **Generalizes to:** a credential is only good against the thing that checks it, and *which service does the checking is a per-device fact* — the xArm verifies against the ac_auth sidecar, this gateway verifies nothing external. Do not assume one lab-wide answer. | Edge identity + shared secret, reusing `_device_auth_headers`; no machine key | Phase F |

### Recorded follow-ups already accepted (do, no decision needed)

From the record-layer critique ([`DATABASE_DESIGN.md`](DATABASE_DESIGN.md)
§"Recorded follow-ups"): `generated_by` provenance stamps on derived
artifacts; remove or version `experiment_tables` PATCH; `analysis_inputs`
M2M (analysis → analysis derivation chains). Plus, from the assessment:
**agent regression evals** before the agent authors trusted records.

## 5. Prioritization rationale

Track 1 Steps 1–2 deliver the Design third with zero actuation risk and are
the long pole (the missing lab-MCP bridge). Steps 3–5 deliver Execute + the
notebook and hold the safety seams — hence gated, reversible,
human-in-the-loop. Step 6 is linkage on a strong Analyze base. Step 7 and
Track 2 are the production-grade path and shouldn't block the ad-hoc loop.
Track 2's Phases A–D carry no actuation risk at all; E introduces the authorization
machinery in dry-run; only F touches hardware, and only through the
already-shipped `execute_plan` path.

## See also

- [`AGENTIC_ELN_DESIGN.md`](AGENTIC_ELN_DESIGN.md) — the design this plan builds.
- [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) — the record layer (ELN core shipped; LIMS phased here as "DB Phase 2/3").
- [`UI_DESIGN.md`](UI_DESIGN.md) §3 — dashboard runner endpoint, SSE contract, run view (its §3.7 build order slots into Track 1 Steps 3–4).
- [`ROADMAP.md`](ROADMAP.md) — SDK milestones and operational regressions this plan depends on.
