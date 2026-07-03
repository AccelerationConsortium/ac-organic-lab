# LaAgenteAnalitica — Implementation Assessment

> **Status:** uncommitted working note. Not part of the lab contract. Written
> 2026-06-29, **updated 2026-06-30** (see the *Update* section below) from a
> direct read of two repos as they sit on disk: `LaAgenteAnalitica` (the agent,
> deployed on the WSL of a separate PC) and `AnaliticaDB` (the results store,
> deployed on the data server `100.64.254.6`). Lives in `ac-organic-lab/` only
> because that is the central place lab-stack context is kept — LaAgenteAnalitica
> is a *separate project* and does **not** use `lab-skills`, STATUS_SPEC, or the
> dashboard.

## What LaAgenteAnalitica is

An agentic analytical-chemistry assistant: a chat UI where a chemist drops raw
instrument files (LC-MS, GC-MS, NMR, LC-UV) into a per-room workspace and an
LLM agent runs domain analysis workflows, plots results, and persists accepted
results to a durable database. Built on the **Grafico** framework
(pydantic-ai + pydantic-graph), forked/extended in-repo. It is the
application/agent layer that `ac-organic-lab/docs/ARCHITECTURE.md` anticipates
("future agent repos") — but it grew up independently of the lab SDK and
reaches instruments directly over their own REST APIs, not through the
dashboard.

## TL;DR — degree of implementation

| Dimension | Maturity | One-line verdict |
|---|---|---|
| **1. Agentic workflows** | ~85% — production for the shipped domains | LC-MS, NMR, GC-MS, AnaliticaDB CRUD all real and tested; GC-only is a stub; no automated evals yet |
| **2. UI** | ~90% — production-grade | React 19 + Vite + Yjs collaborative chat, file workspace, 3D/heatmap viewers, full Agilent instrument tab |
| **3. Database** | ~95% (results store) / mixed overall | AnaliticaDB itself is complete and deployed; agent-side CRUD complete & enabled; MongoDB backend-only; SPARQL KG built but disabled |
| **Auth** | Complete (confirmed by owner) | Password / OTP / forward_auth-edge / bypass modes, room isolation, short-lived WS tokens |

Legend: percentages are a coarse "how much of the intended surface is real and
working vs. stubbed/planned," not test coverage.

## Update — 2026-06-30 (this session)

Work landed on dedicated branches (AnaliticaDB `develop-sdl2`, LaAgenteAnalitica
`develop-sdl2-auth`); both pushed. The maturity verdicts above hold — this adds a
new in-flight workstream and bumps the DB contract.

- **AnaliticaDB contract → v0.2.0, redeployed.** Added `LC-DAD` and `LC-UV`
  acquisition blocks. Before this, only `GC-MS / LC-MS / LC-DAD-MS / NMR` were
  *registerable* even though the agent already *analyzes* LC-UV — an asymmetry now
  closed. The live `:8010` service was restarted onto `develop-sdl2` and verified
  (LC-UV/LC-DAD present in `/openapi.json`); the agent's ontology pin moved to
  `0.2.0` in lockstep. **No DB migration** (the `technique_t` enum already held the
  values; `acquisition_params` is JSONB).
- **"Catalog-from-DB" direction started.** New target: AnaliticaDB becomes the
  browsable *source catalog* (raw-first, per-user, with metadata); the chemist
  previews 2D/3D heatmaps to pick files, then **"sends to room" to process**.
  Shipped foundations: a developer data-registration utility (`ingest.py` +
  `scripts/register_dev_data.py`) and a per-user read-only **path-confinement
  core** (`preview/paths.py`) for a planned byte/preview service — **Option A**: a
  separate read-only service co-located with storage on the data server, scoped to
  `…/Agilent_UPLC/<username>/`, absolute `storage_uri`, trusting the already
  authenticated GraphChat user id. Design note:
  `AnaliticaDB/docs/data-catalog-and-preview.md`. **Not built yet:** the preview
  HTTP app + deploy unit, and the LaAgenteAnalitica catalog-browser UI.
- **UI shell restructured.** Replaced the three-panel layout with: **email +
  sign-out pinned top-left**; **chat as a collapsible left panel** (room-list →
  conversation → Back, folds to a rail); **Workspace / Tool Usage / Guide /
  Instrument persistent on the right** — so data analysis works with chat
  collapsed / without the agent.

## Deployment topology (as observed)

```
PC (WSL)                              data server 100.64.254.6        instrument PC
─────────────────────────────        ─────────────────────────       ───────────────────────
LaAgenteAnalitica:                    AnaliticaDB (systemd)            agilent-hplcms-server
  chat.py  (pydantic-ai agent)  ─HTTP─►  uvicorn :8010                   :8010  (sdl2-pc-06-uplc)
  graphchat backend (Node/Express)       FastAPI + SQLModel + Postgres   ▲
  graphchat frontend (React/Vite)        4-entity hierarchy + audit      │ read-only status (agent)
  MongoDB (rooms/sessions)                                               │ run submit/abort (operator UI)
                                                                         │
        └──────────────────────── browser Instrument tab ───────────────┘
```

- **AnaliticaDB** binds `--host 100.64.254.6 --port 8010` (`deploy/analytica-db.service`),
  `After=postgresql.service tailscaled.service`. Postgres-backed, reachable
  over the Tailnet. (Note: this is the same `:8010` the lab uses elsewhere; it
  is on a *different host* so there is no collision.)
- The agent points at it via `ANALYTICA_DB_URL` (e.g. `http://100.64.254.6:8010`).
- The Agilent integration targets `sdl2-pc-06-uplc...:8010` directly — this is
  the **same physical instrument** the lab tracks as the `agilent_uplc_ms`
  device, but reached through the instrument's own server, *not* through the
  ac-organic-lab dashboard or `lab-skills`.

---

## 1. Agentic workflows — ~85%

**Framework.** `chat.py` runs a multi-room orchestrator: it discovers GraphChat
rooms over an API, spawns one agent worker per room, and reconnects on failure.
The default agent is a "deep agent" (`pydantic-deep`) with filesystem, todo,
memory, plan, sub-agents, web, checkpoints, and cost-tracking enabled; a "plain"
Grafico agent is selectable via `GRAFICO_MAIN_AGENT_KIND`. Tools are loaded from
`tool_registry.toml` at startup. Default model `anthropic:claude-opus-4-6`,
overridable via `GRAFICO_MODEL`; config enumerates 20+ models across Anthropic /
OpenAI / Google / NVIDIA / OpenRouter / Poe.

**Domains (analysis is structured as pydantic-graph state machines with mixed
deterministic + LLM nodes):**

| Domain | Tool(s) | State | Notes |
|---|---|---|---|
| **LC-MS / LC-UV** | `run_lc_ms_workflow`, `run_lc_uv_workflow`, `plot_tic`, `plot_ms_spectrum`, `plot_uv_chromatogram` | ✅ production | Largest graph (~1.4k lines); auto-detects UV/MS channels; peak detection + adduct/cosine scoring |
| **NMR** | `run_nmr_workflow`, `plot_nmr_spectrum` | ✅ production | Bruker/Varian/JCAMP/NMRPipe; apodization, multiplet analysis, structure elucidation; heavily unit-tested |
| **GC-MS** | `run_gcms_workflow`, `run_ms_gc_workflow` | ✅ production | EI deconvolution + library search; combined and separate variants |
| **GC-only** | `run_gc_workflow` | ❌ **stub** | every graph node raises `NotImplementedError`; `enabled=false` |
| **AnaliticaDB** | 16 CRUD tools | ✅ production | see §3 |
| **Agilent HPLC-MS** | `check_agilent_status` | ✅ production | **read-only by design** — agent helps author run JSON; operator submits from the UI |
| Web / REPL | `duckduckgo_web_search`, `python_repl`, `bash_exec` | ✅ working | general-purpose |
| Knowledge graph | `run_sparql_query`, `get_ontology_snapshot`, `get_instance_from_knowledge_graph`, `get_cif_content` | ⏸️ **built but disabled** | full SPARQL client + query-safety guards exist; `enabled=false` in registry |

Roughly **15 of 20 catalogued tools enabled**; ~73 test files exist
(strong unit coverage in NMR/LC-MS, lighter integration coverage).

**Containerization** exists for an orchestrator/worker split
(`Dockerfile.orchestrator`, `Dockerfile.worker` with Julia/xtb/crest for the GPU
compute domains), plus an A2A server stub (`a2a.py`).

**Gaps:** GC-only domain; KG toolset shipped-but-off; **no automated evaluation
harness** (owner's own roadmap calls this out); A2A scale testing not started;
the design note `docs/grafico-workspace-native-workflow.md` flags a planned (not
yet done) shift from chat-first to workspace-native runs.

## 2. UI — ~90%

A polished React 19 + Vite single-page app (~8.5k lines TS/TSX, ~38 components)
with **real-time collaborative state via Yjs/y-websocket** (multi-user rooms). As
of the 2026-06-30 restructure the shell is: **email + sign-out pinned top-left**,
a **collapsible chat panel** on the left (room-list → conversation → Back, folds
to a rail), and a **persistent workspace** on the right (the tabbed explorer
below) that stays usable with chat collapsed. The surfaces:

- **Chat** (now the collapsible left panel) — Yjs-synced messages, markdown + KaTeX + Shiki code highlighting,
  collapsible "thinking", a **model selector**, and a **deferred-tool
  approval/denial UI** (human-in-the-loop for external/instrument actions).
- **Workspace explorer** — file tree with drag-move, upload (+ zip extract),
  garbage-folder delete; previews for code/images, **3D molecules** (`.xyz`/
  `.cif` via 3Dmol), and an **interactive NPZ viewer** (HPLC/MS heatmaps with
  crosshair, trace/spectrum/TIC slices, prominence-based peak detection,
  "add to chat").
- **Tool-usage** panel (bar chart + call log from `agent_tool_calls.jsonl`) and
  an in-app **Guide** kept in sync with `user_readme.md`.
- **Instrument tab (Agilent HPLC-MS)** — live equipment/module/MS-sensor status
  (10 s auto-refresh, OLSS-derived state), consumables bars, run queue with
  cancel/abort, and **client-validated job submission** with claim-token
  handshake (`X-Claim-Token`). This is a genuinely complete operator console.

**Gaps:** dark mode wired but no toggle; minimal frontend/E2E tests; desktop-only
layout assumptions.

## 3. Database — ~95% (results store), mixed across the three stores

Three persistence layers, with a clean documented ownership rule
(`docs/persistence-boundaries.md`): *"Mongo remembers execution, graph DB
remembers meaning, filesystem remembers artifacts."*

**a) AnaliticaDB — the durable results system-of-record (COMPLETE, deployed).**
FastAPI + SQLModel + Alembic + PostgreSQL. Three-layer architecture
(API → service → repository). Domain is a strict four-level hierarchy:

```
Experiment ──< Sample ──< Measurement ──< MeasurementFile
```

- **5 tables** (the four above + `agent_actions` audit), named PG enums
  (`technique_t` with 16 techniques, `file_type_t`, `software_t`, `operation_t`),
  JSONB for free-form `meta` and a technique-discriminated `acquisition_params`.
  Note: of the 16 technique values, only **6 are *registerable*** (have an
  `acquisition_params` block): `GC-MS, LC-MS, LC-DAD, LC-UV, LC-DAD-MS, NMR`
  (LC-DAD/LC-UV added 2026-06-30).
- **19 endpoints**: health + create/get/list/patch per entity. **No DELETE** by
  design.
- **Agent treated as untrusted:** payloads validated with `extra="forbid"`;
  provenance fields are immutable on PATCH. **Identity rides OpenTelemetry
  baggage, never the request body** (`agent_id` + `session_id`), recorded on an
  `agent_actions` row for every mutation (reads are traced in Logfire but not
  audited to a row).
- Contract is a committed **`ontology.json`** (schema_version `0.2.0`) with
  per-entity create/read/update/list JSON Schemas; CI checks it against the
  Pydantic models so it can't drift.
- Tests: unit + testcontainers integration (HTTP contract + audit contract).
- Deployed via systemd on the data server — **now running `develop-sdl2` at
  v0.2.0**. It is an *editable* install, so a branch switch + service restart is
  the whole deploy (no rebuild).

**b) Agent-side integration to AnaliticaDB (COMPLETE, enabled).**
`domains/analytica_db/` generates **16 tools (4 entities × create/get/list/update)**
straight from the *same* `ontology.json` artifact (exact version match `0.2.0`,
fail-fast on mismatch). The HTTP client injects identity via `logfire.set_baggage`,
is fail-fast (raises on non-2xx) except 404/422 which become `ModelRetry` so the
model can self-correct, and returns compact `{id, title}` summaries to save
tokens. Enabled by default in the registry.

**c) MongoDB (backend infrastructure only).** GraphChat backend stores rooms,
sessions, and runtime metadata. **The agent never touches Mongo directly** —
it's pure UI/orchestration plumbing.

**d) SPARQL knowledge graph (built, default-OFF).** A full SPARQL client + KG
tools with query-safety analysis exist in `grafico/`, but the toolset is
`enabled=false`. So the "graph DB remembers meaning" tier is implemented but not
currently in use.

## Auth — complete

Confirmed by the owner and by code: GraphChat supports password login
(`elagente`), email **OTP** login for `forward_auth` edge mode, a `bypass` mode,
and forward-auth where an HTTP edge proxy supplies identity. Per-user room
isolation; short-lived HMAC **WS tokens** authenticate the Yjs websocket in edge
mode. Recent commits (`acfe32e`, `e8d8e14`) landed the OTP page and ws-token
path.

---

## How this relates to ac-organic-lab

- **Same instrument, different path.** LaAgenteAnalitica drives/reads the
  Agilent UPLC-MS through the instrument's own `agilent-hplcms-server` REST API
  (`sdl2-pc-06-uplc:8010`). The lab stack independently tracks that instrument
  as the `agilent_uplc_ms` STATUS_SPEC device via the dashboard. **There is no
  shared claim/lease between the two paths today** — the agent's
  read-only-status + operator-submits-runs split is its own convention, not the
  lab's `lab-skills` claim protocol. If both surfaces ever issue runs, that
  coupling is unmodeled (worth noting alongside the dashboard's
  control-surface-exposure discussion in `docs/ROADMAP.md`).
- **AnaliticaDB ≠ lab.db.** AnaliticaDB is a separate Postgres results store on
  `100.64.254.6`, with its own four-entity schema and audit table. It is *not*
  the dashboard's SQLite `lab.db` (uptime/events/runs). They serve different
  purposes — durable analytical results vs. operational observability — and do
  not currently talk to each other.
- **Not on the lab SDK.** This project predates/sidesteps `lab-skills`,
  STATUS_SPEC envelopes, and the MCP catalog. If the lab wants agent-driven
  analysis as a first-class platform capability (per ARCHITECTURE long-term goal
  LG6), the integration point would be to either (a) front the Agilent server
  behind the dashboard's audited claim path, or (b) teach the agent to consume
  the lab MCP surface — neither exists yet.

## Bottom line

LaAgenteAnalitica is a **substantially complete, actively-maintained product**:
the UI and the AnaliticaDB results store are production-grade, the analytical
workflows are real and tested for the shipped modalities (LC-MS, NMR, GC-MS),
the agent↔DB contract is clean and version-locked, and auth is done. The honest
gaps are the **GC-only stub**, the **disabled knowledge-graph tier**, the
**absence of automated evals**, and the fact that it lives **outside the lab's
SDK/claim/observability fabric** rather than plugged into it.
