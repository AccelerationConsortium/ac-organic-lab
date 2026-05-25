# AC Organic Self-driving Lab — Architecture

**Status:** living document. Last revised after the platforms.yaml refactor (schema v2).

This document describes the long-term architecture of the AC Organic Self-driving Lab software stack — what each piece is for, why it exists, and how the pieces fit together. For step-by-step implementation milestones see the working plan in `.cursor/plans/`.

## What this repo is

`ac-organic-lab` is a monorepo housing the platform-level pieces of the lab:

- The unified equipment status contract (`docs/STATUS_SPEC.md`)
- The equipment inventory (`equipment.yaml`)
- The platform layout config (`platforms.yaml`)
- The Python SDK that workflows, agents, and the dashboard use to drive the lab (`skills/`)
- The dashboard's web server (`api/`) and Next.js UI (`web/`)
- Deployment and operations docs (`deploy/`, `docs/`)

It does **not** house:

- Per-device drivers / REST APIs (one repo per instrument: `agilent-plateloc-server`, `filter_every_well`, `xarm_translocation`, `dose_every_well`, `fume_hood_actuator`, ...)
- Project-specific workflow code (e.g. `solubility-screening`, `hte-screening`)
- Agent code (LLM planners, prompts, evals — future `ac-organic-lab-agents`)

## Layered system

```mermaid
graph TB
    subgraph applayer [Application]
      proj["Project workflow repos<br/>solubility-screening, hte-screening, ..."]
      agents["Agent repos<br/>LLM planners, evals, MCP clients"]
    end

    subgraph platform [Platform monorepo]
      skills["skills SDK<br/>registry, aggregator, adapters,<br/>session, claims, plan validation, MCP"]
      api["api dashboard server<br/>presentation + history DB<br/>(db.py, history.py)"]
      db[("data/lab.db<br/>SQLite<br/>uptime · events<br/>sensors · runs")]
      web["web Next.js UI<br/>(History tab, Equipment grid)"]
      yaml["equipment.yaml<br/>inventory"]
      platforms["platforms.yaml<br/>Overview layout"]
      spec["docs/STATUS_SPEC.md<br/>contract"]
    end

    subgraph devicelayer [Devices]
      dev["Per-device REST services<br/>implement STATUS_SPEC v1.x"]
    end

    proj --> skills
    agents --> skills
    api --> skills
    web --> api
    skills --> yaml
    skills --> platforms
    skills --> dev
    api --> dev
    api --> db
    spec -.governs.-> dev
    spec -.governs.-> skills
```

Three responsibilities, three layers:

1. **Device layer** — each instrument runs its own REST service implementing `STATUS_SPEC`. Authoritative for that device's state.
2. **Platform layer** (this repo) — the SDK aggregates device state, provides typed control, manages claims/leases, and exposes the runtime skill catalog. The dashboard's web server is a thin client of the SDK. The Next.js UI calls the dashboard server.
3. **Application layer** — project workflows and agents consume the SDK to run experiments. Each project lives in its own repo with its own data model, recipes, and interlocks.

## Why a monorepo

The pieces inside `ac-organic-lab/` change together. Putting them in one repo means:

- One PR for cross-package changes (e.g. registry schema bumps that touch `skills/`, `api/`, and `web/` simultaneously)
- One canonical `equipment.yaml` at the root, no path/URL/sync games
- One CI pipeline with package-scoped jobs
- The Python packages still publish independently — workflow project repos depend on `lab-skills` via package, not on the whole monorepo

The pieces *outside* are deliberately separate repos because they have different lifecycles, audiences, and dependency profiles:

- Per-device repos are touched per-instrument, often by people who only care about one device
- Project workflow repos contain chemistry, not platform code
- Agent repos pull in heavy LLM/prompt dependencies that should not infect the platform

## Repo layout

```
ac-organic-lab/
├── pyproject.toml                  # uv workspace declaration
├── equipment.yaml                  # inventory (root)
├── platforms.yaml                  # Overview layout / section config (root)
├── data/
│   └── lab.db                      # SQLite history database (gitignored)
├── docs/
│   ├── STATUS_SPEC.md              # combined v1.0 baseline + v1.1 (claims, allowed_actions)
│   ├── ARCHITECTURE.md             # this document
│   ├── OBSERVABILITY.md            # logging, events, history DB schema
│   └── ROADMAP.md                  # milestone tracking
├── deploy/
│   ├── ac-organic-lab-api.service    # systemd unit (FastAPI)
│   └── ac-organic-lab-web.service    # systemd unit (Next.js)
├── skills/                         # PYTHON: lab-skills SDK
│   ├── pyproject.toml
│   └── src/lab_skills/
│       ├── registry.py             # EquipmentEntry, Tile, PillConfig, Registry
│       ├── platforms.py            # PlatformSection, PlatformsConfig, load_platforms
│       └── aggregator.py           # EquipmentAggregator
├── api/                            # PYTHON: dashboard web server
│   ├── pyproject.toml              # depends on ../skills
│   └── app/
│       ├── main.py                 # FastAPI app + lifespan + uptime poll task
│       ├── db.py                   # LabDatabase (SQLite, stdlib only)
│       ├── history.py              # /api/history/* + /api/ingest/* routes
│       ├── control.py              # control passthrough (cameras, plugs)
│       └── presentation.py        # dashboard snapshot types + location
└── web/                            # Next.js UI
    └── src/
        ├── app/
        │   ├── page.tsx            # Overview (platforms.yaml-driven sections)
        │   ├── history/page.tsx    # History tab (uptime, sensors, runs)
        │   └── platforms/hte/      # HTE platform detail
        ├── components/
        │   └── Nav.tsx             # auto-injects platform tabs from /api/platforms
        └── lib/
            ├── use-equipment.ts    # React Query hook — equipment list
            ├── use-platforms.ts    # React Query hook — platforms config
            ├── api.ts              # typed fetch fns
            ├── history-api.ts      # typed fetch fns for history endpoints
            └── use-history.ts      # React Query hooks (30 s refetch)
```

## Component responsibilities

### `skills/` — `lab-skills`

The Python SDK and aggregator. **The single authoritative layer for control and runtime state.**

Owns:

- `equipment.yaml` parsing → `Registry` / `EquipmentEntry` model
- `platforms.yaml` parsing → `PlatformsConfig` / `PlatformSection` model
- One async polling loop per process (`EquipmentAggregator`) over all configured devices
- Per-device adapters for STATUS_SPEC v1.0, legacy pre-spec devices, and mocks
- The `Lab.connect()` / `LabSession` / `EquipmentClient` API used by workflow code
- `wait_until_state` and other state-machine helpers
- The `Skill` catalog — runtime view of "what's invokable right now" per role
- Claim/lease management (STATUS_SPEC v1.1+)
- Cross-device `validate_plan(plan)` for preflight checks
- The MCP server companion (exposes the skill catalog as MCP tools)
- The `serve` CLI (runs the aggregator as a standalone HTTP service)

Does **not** own:

- Per-device drivers (those live in their own repos)
- Project chemistry or interlocks (those live in project repos)
- LLM/agent code

### `api/` — dashboard web server

A FastAPI app that serves the dashboard. **Thin presentation + observability.**

Owns:

- HTTP routes consumed by the Next.js UI (`/api/equipment`, `/api/platforms`, `/api/health`)
- Presentation-only types (`EquipmentSnapshot`, tile layout, location coords, pill config)
- The `_snapshot()` wrapper that decorates an SDK snapshot with dashboard-specific fields: `platform` and `tile` are resolved from `PlatformsConfig` at compose time; `pill` is forwarded from `EquipmentEntry.pills`
- CORS / auth at the dashboard edge
- **Lab history database** (`db.py`, `history.py`): SQLite at `data/lab.db`
  - `equipment_events` — state transitions, errors, startup/shutdown
  - `service_uptime` — reachability transitions (up / down / recovered)
  - `sensor_readings` — environmental metrics, 1/min downsampled
  - `runs` + `well_results` — dosing run records and per-well outcomes
- **Background uptime poll task** (`main.py`): runs every 60 s, writes to
  `service_uptime` only on reachability *transitions* (not every poll)
- **History API** (`history.py`): `GET /api/history/*` read endpoints for
  the dashboard; `POST /api/ingest/*` write endpoints for device services

Does **not** own:

- Polling, adapters, or the registry model — all imported from `skills`
- Any control logic — control calls are forwarded to device gateways verbatim

### `web/` — Next.js UI

The user-facing dashboard. Reads from `api/`. No Python.

The Overview page (`page.tsx`) is entirely driven by `/api/platforms`: it iterates `sections` in order and dispatches on `kind` — `environmental_map` renders the `LabMap`; `platform` renders a `PlatformCard` with snapshots looked up by the section's equipment id list. Adding or reordering a section requires only a `platforms.yaml` edit; no frontend code changes.

The Nav (`Nav.tsx`) auto-injects one tab per section that has an `href` field, between the static `Overview` and `History` tabs.

### `equipment.yaml` (root)

The static inventory of "what equipment exists in this lab". Edited by humans when hardware physically changes or goes into maintenance.

**Schema v2** — each entry includes:

- `id`, `name`, `kind`
- `adapter` (`http` for spec-conformant, `legacy_http` for pre-spec, `mock` for not-yet-deployed)
- `base_url`, `status_path`, `poll_timeout_seconds`
- `enabled: bool`, `maintenance: { reason, until, contact }` for soft maintenance toggling without commenting out
- `tiles: dict[section_id, {w, h}]` — per-section tile sizing for the equipment grid. A missing key defaults to `{w:2, h:1}`. The section id matches `platforms.yaml`
- `pills: {open: bool}` — shared Overview pill config. `open: true` renders an "Open ↗" link to `base_url` in the platform card pill row
- `location: {x, y, label}` — position on the lab floorplan map (environmental sensors only; parsed by `api/`, not `skills`)
- `camera:` / `plug:` — kind-specific blocks (lenses, outlets); parsed by `api/` and forwarded to the frontend via `EquipmentSnapshot`

> **`platform:` removed in schema v2.** Equipment entries no longer carry a `platform:` field. Section membership is declared exclusively in `platforms.yaml`; `EquipmentSnapshot.platform` is resolved by the API at compose time.

> **Stream visibility** is not a YAML field. When a platform has a camera the platform card shows a "Show stream / Hide stream" toggle — the live feed is collapsed by default. There is no `hide_stream` flag; the toggle is purely a runtime UI control.

### `platforms.yaml` (root)

Defines which sections appear on the Overview page, in what order, and which equipment ids belong to each. This is the single source of truth for the Overview layout and Nav tab list.

```yaml
sections:
  - id: lab_environment
    title: Lab Environment
    kind: environmental_map      # renders LabMap instead of PlatformCard
    equipment: [env_sample_prep, ...]

  - id: hte
    title: HTE Platform
    href: /platforms/hte         # presence of href → tab appears in Nav
    kind: platform
    equipment: [cam_hte_tapo_c245, ot2, ...]

  - id: web_services
    title: Web Services
    kind: platform               # no href → no Nav tab
    equipment: [pypoe_web]
```

Key behaviours:

- **Section order** determines render order on the Overview page and tab order in the Nav.
- **Equipment order** within a section determines tile order on the platform's detail page and pill order in the Overview card.
- **Shared equipment** — an equipment id may appear in more than one section. The resolved `EquipmentSnapshot.platform` for that id is the **first** section listing it (sections are in display order, so this is deterministic).
- **Missing file** raises immediately on startup; there is no fallback to defaults.
- Loaded once at API server startup via `load_platforms()` in `lab_skills.platforms`; exposed as `GET /api/platforms`.

### `docs/STATUS_SPEC.md`

The contract every per-device REST service implements. Combines the v1.0 baseline with the v1.1 additions (claims, `allowed_actions`, `details.claimed_by`); v1.0 devices remain conformant without changes.

## Key design decisions

### 1. Skills SDK is the control authority

The dashboard polls. Workflows command. Both go through the same SDK code. The SDK owns the registry, the polling loop, the claim/lease state, and the skill catalog.

The dashboard does **not** write to devices. Workflows do. This split keeps the dashboard simple and ensures only one piece of code can change device state.

### 2. Per-device REST services are authoritative for their own state

The dashboard's aggregator and the SDK both *cache* device status. Neither is ever the source of truth — the device itself is. A workflow about to issue a control command always re-reads `/status` directly from the device, never from a cache, because cache staleness measured in seconds is forever in robotics.

### 3. Project repos depend only on `lab-skills`

A workflow author writing `solubility-screening` adds **one** dependency: `lab-skills`. They never `pip install ac-organic-lab` (too heavy) or import from `api/` (presentation, not control). They certainly never add per-device repos as dependencies — every device is reached through the SDK.

### 4. Roles, not equipment IDs, in workflow code

Workflow code says `lab.role("sealer").seal_start(...)`, never `lab.get("plateloc").seal_start(...)`. A `binding: dict[str, str]` config in the project repo maps role → equipment_id. This makes workflows portable across labs and survivable through device replacements.

### 5. Two YAML files, cleanly separated concerns

`equipment.yaml` answers "what hardware exists and how to reach it". `platforms.yaml` answers "how should the UI present it". Keeping them separate means:

- Hardware changes (new device, maintenance, URL change) never touch the UI layout file
- UI layout changes (reorder sections, add a new platform page) never touch hardware config
- The SDK (`lab-skills`) parses both — `load_registry()` for equipment, `load_platforms()` for sections — but `EquipmentEntry` carries no UI concerns beyond tile sizing and pill config
- `platform` assignment is computed at API compose time, not stored on the equipment entry

### 6. Soft maintenance over commented-out lines

Taking a device offline for maintenance is a one-line `enabled: false` flip with optional `maintenance: { reason, until, contact }`. The dashboard renders the tile in maintenance state; the SDK raises a typed `EquipmentInMaintenance` exception. Comment-out is reserved for actual hardware removal.

### 7. Agents talk to the SDK via MCP

Future agent repos do not import the SDK directly. They speak [Model Context Protocol](https://modelcontextprotocol.io) against an MCP server the SDK exposes. This keeps prompt engineering, LLM clients, and eval frameworks out of the platform layer, and lets any MCP-aware agent (Claude Desktop, Cursor, custom) plug in without per-agent glue.

### 8. STATUS_SPEC ships before code

Every contract change is a doc PR first (`docs/STATUS_SPEC_v*.md`), then a reference implementation in one device repo, then SDK support, then rollout to remaining devices. Spec is the negotiated artifact; code follows.

### 9. History database is append-only and owned by the aggregator

`data/lab.db` (SQLite) is written exclusively by the `api/` dashboard server — never by device services directly. The aggregator observes reachability from its existing poll loop and records transitions. Device services push domain events via `POST /api/ingest/events` rather than opening a DB connection. This keeps the database on one host with one writer, eliminates connection pooling concerns, and lets the file be backed up with a single `cp` while the server is running (WAL mode).

## Long-term goals

These are non-binding directional commitments — not a roadmap.

### LG1. Multi-lab portability

The platform should run unchanged in a second lab. Only `equipment.yaml`, `platforms.yaml`, deployment config, and the binding YAMLs in project repos differ between labs. No per-lab forks of `skills/`, `api/`, or `web/`.

### LG2. Spec-first device migrations

Every device currently using `adapter: legacy_http` migrates to `adapter: http` (full STATUS_SPEC compliance). The legacy adapter is a transition tool, not a permanent feature. Sunset target: when zero entries in `equipment.yaml` use `legacy_http`.

### LG3. Claim/lease as a first-class operational primitive

Multi-operator and multi-workflow safety becomes a guarantee: any device under workflow control is leased, with automatic release on workflow crash via heartbeat expiry. The dashboard surfaces who holds what.

### LG4. Crash-safe workflow runs

Project repos persist run manifests + append-only event logs. Resuming or replaying a run is a first-class operation. No experiment is lost because an orchestrator crashed mid-flight.

### LG5. Shared status contract package

Once 3+ device repos have shipped on STATUS_SPEC v1.1 cleanly for ~1 month, extract `lab-status-contract` as a tiny shared Python package. Per-device repos and the SDK then `from lab_status_contract import EquipmentStatus, ...` instead of vendoring a copy.

### LG6. Agent-native operations

The lab is operable by humans, by scripted workflows, and by agents — using the same SDK and the same MCP surface. Agent ops are subject to the same claim/lease, plan validation, and audit trail as human ops.

### LG7. Deterministic dry-run for the entire lab

The SDK should run end-to-end in dry-run mode without any device powered on. Per-device dry-run modes (already implemented in `agilent-plateloc-server`, `filter_every_well`) compose into a full simulated lab usable for CI, dev, and agent training.

## Glossary

- **Device** — one piece of physical equipment with its own REST service
- **Adapter** — `skills/` code that translates a device's responses into `EquipmentStatus`
- **Aggregator** — the polling loop in `skills/` that fans out to all configured devices
- **Section** — one card on the Overview page, defined in `platforms.yaml`; either a `platform` (equipment tiles) or an `environmental_map`
- **Skill** — a named capability (e.g. `seal.start`) invokable on a role; computed at runtime from `/status` + the equipment kind
- **Role** — a logical capability slot in a workflow (e.g. `sealer`, `filtration`, `plate_mover`); bound to an `equipment_id` per project
- **Claim / lease** — a short-lived, heartbeated lock on a device that prevents concurrent control by other clients
- **Plan validation** — preflight check that runs project interlocks and device state checks before a workflow does anything destructive
- **STATUS_SPEC** — the contract defining what `/status`, `/health`, `/`, and `/control/*` look like for every device

## See also

- `docs/STATUS_SPEC.md` — combined device contract (v1.0 baseline + v1.1 additions + SiLA comparison appendix)
- `docs/SKILLS_CATALOG.md` — skill catalog design (`SkillDef` / `Skill`, runtime availability, evolution from hard-coded → device-declared)
- `docs/INTERLOCKS.md` — four-layer safety model and the project interlock API (`add_interlock`, `validate_plan`, `PlanReport`)
- `docs/OBSERVABILITY.md` — logging, events, and the central history DB
- `docs/EQUIPMENT_INTEGRATION.md` — onboarding and maintenance runbook
- `docs/DEVICE_PC_SETUP.md` — canonical install recipe for a Windows device PC
- `docs/ROADMAP.md` — per-device migration status
- `equipment.yaml` — the lab's equipment inventory (schema v2)
- `platforms.yaml` — the Overview page layout and Nav tab config
- `skills/README.md` — SDK usage (created when v0.1 ships)
- `api/README.md` — dashboard server (created when api/ is reorganized in v0.1)
- `.cursor/plans/build_lab-skills_*.plan.md` — current working milestone plan
