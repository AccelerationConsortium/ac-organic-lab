# AC Organic Self-Driving Lab — Roadmap

This document is the durable, git-tracked record of where the SDK + dashboard
stack is, what is deferred, and what must be true before the next SDK
milestone (v0.4) starts. The original layered plan lives in
`.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md`; this doc is
its committed companion so the project state is recoverable without the
Cursor plan UI.

## Current state (May 2026)

| Milestone | Scope | Status |
|-----------|-------|--------|
| **v0.1** | Monorepo + skills SDK foundation + aggregator move | ✅ shipped on `main` |
| **v0.2** | `EquipmentClient.command`, sync wrapper, control exceptions, skill catalog, `LabSession.skills()`, typed per-kind clients | ✅ shipped on `main` |
| **v0.3** | STATUS_SPEC v1.1 spec doc, `ClaimManager`, `Plan` / `validate_plan` / `PlanReport`, `Violation` + `register_interlock`, two built-in interlocks, graceful degradation for v1.0 devices | ✅ shipped on `main` |
| **v0.4** | MCP server companion (catalog → tools, `/status` → resources, CLI `lab-skills mcp serve`) | ⏸ paused (see below) |
| **v0.5** | Standalone `lab-skills serve` CLI exposing the aggregator as a long-lived HTTP service | not started |

`uv run pytest -q` from the repo root: **120 passed** (98 v0.1/v0.2 + 8 claims
+ 14 plan/interlocks). Dashboard `/api/equipment` is verified end-to-end.

## Why v0.4 is paused

v0.3 shipped the *SDK side* of the v1.1 contract. The agent-facing payoff —
"an MCP-aware agent calls a tool, hardware moves with claim semantics
intact" — needs at least one device that actually implements
`/control/{claim,heartbeat,release}` and populates `allowed_actions`.
Today only `agilent_plateloc` is even v1.0 conformant, and it is currently
flagged `adapter: mock` in `equipment.yaml` because the on-prem instance
is not wired up. Standing up MCP against an all-mock fleet would lock in
abstractions before they have been stress-tested against real hardware
quirks.

**Strategy:** before v0.4, migrate two or three real devices to STATUS_SPEC
v1.0 (and ideally `agilent_plateloc` to v1.1) so interlocks + plan
validation can be exercised end-to-end. Then resume the SDK roadmap.

The deferred SDK items below are the v0.4 / v0.4.5 carry-over list — they
do not block equipment migration; they wait until at least one v1.1 device
exists.

### Deferred from v0.3 → folded into v0.4

- `execute_plan(plan)` — sequential executor that wraps each step in
  `ClaimManager`, re-runs interlocks before each step, emits
  `PlanRunReport`. Cannot be acceptance-tested without claim semantics on
  at least one real device.
- **Async interlocks** — current `fn(plan, step, session) -> list[Violation]
  | None` is sync. Live-status interlocks (`docs/INTERLOCKS.md`'s
  long-form async signature) come with `execute_plan`.
- **Sync façades** for `ClaimManager` and `validate_plan` in
  `lab_skills.sync` so notebooks / sync CLIs / the MCP stdio loop can
  use them without async plumbing.

## Equipment migration plan (the in-flight effort)

Each per-device repo is migrated independently. The dashboard's
`equipment.yaml` flips per-entry as repos ship: `adapter: legacy_http`
→ `http` once `/status` is `EquipmentStatus`-shaped, and `protocol:
"1.0"` → `"1.1"` once the device implements claims.

### Priority order

1. **`agilent_plateloc`** — already v1.0 conformant. Add v1.1 (claim
   endpoints + `allowed_actions` + `details.claimed_by`). Smallest delta,
   biggest payoff: gives v0.3 / v0.4 a real reference device.
2. **`filter_every_well` (press)** — FastAPI + `/status` already exist,
   shape is the closest to spec. Rename `/init`, `/press/up`, ...
   to `/control/*`; switch envelope to `EquipmentStatus`. Stay on v1.0;
   v1.1 later.
3. **`dose_every_well` (solid_doser)** — same shape as press: rename
   endpoints to `/control/*`, switch envelope.
4. **`fume_hood_actuator`** — Flask → FastAPI port plus spec envelope.
   Bigger lift; do after the FastAPI repos are clean.
5. **`xarm_translocation`** — defer. Lives on a private subnet; needs the
   gateway-PC shim discussion resolved first. Stays
   `do_not_call_connect: true` in `equipment.yaml`.

### Per-device migration checklist (v1.0)

A repo is considered v1.0 conformant when:

- [ ] `models.py` carries the STATUS_SPEC v1.0 Pydantic types verbatim
  (or imports them from a shared package once one is published).
- [ ] `GET /` returns `ProbeResponse(equipment_id, equipment_name,
  protocol_version="1.0")`.
- [ ] `GET /health` returns `HealthResponse(status="healthy")`.
- [ ] `GET /status` returns `EquipmentStatus` with snake_case field
  names. **Side-effect-free.** Always 200 unless the process is broken.
- [ ] `GET /openapi.json` is served (FastAPI gives this for free).
- [ ] All control endpoints under `/control/*`, gated by Pydantic body
  schemas with `Field(ge=, le=)` ranges.
- [ ] CORS allows the dashboard origin.
- [ ] No `_get_wlan_ip()` / `_get_tailscale_ip()` self-discovery code.
- [ ] Snapshot fixtures saved under `tests/fixtures/status_*.json`
  covering at least: `ready`, `requires_init`, `error`, `dry_run` (if
  applicable).
- [ ] `README.md` says "This repo conforms to lab status spec v1.0".
- [ ] `equipment.yaml` flipped from `adapter: legacy_http` (or
  `mock`) to `adapter: http`.

(Reference: `docs/STATUS_SPEC.md`. The full conformance checklist lives
in that doc and is the source of truth.)

### Per-device migration checklist (v1.1, additive over v1.0)

A repo is considered v1.1 conformant when, on top of v1.0:

- [ ] `protocol_version` reported on `/` and `/status` is `"1.1"`.
- [ ] `POST /control/claim`, `POST /control/heartbeat`,
  `POST /control/release` implemented per
  `docs/STATUS_SPEC_v1_1.md`.
- [ ] `EquipmentStatus.allowed_actions` is populated with the skill
  names (matching `Skill.name` from the catalog) the device will
  currently honor.
- [ ] `details.claimed_by` populated while a claim is held; cleared on
  release / expiry.
- [ ] `X-Claim-Token` enforced on `/control/*` (recommended; HTTP 423
  on miss) **or** the README documents that claims are advisory.
- [ ] Snapshot fixtures cover `ready` with no claim, `ready` with a
  claim held, `requires_init`.
- [ ] `README.md` says "This repo conforms to lab status spec v1.1".
- [ ] `equipment.yaml` entry has `protocol: "1.1"`.

### Per-device sub-tasks (current state)

#### `agilent_plateloc`

- v1.0 conformance: ✅ already shipped (FastAPI; `/`, `/health`,
  `/status`, `/control/*`).
- Open work for v1.1:
  - [ ] Replace `_lock` (currently in `service.py`) with a real
    claim/heartbeat/release implementation.
  - [ ] Add `claim_token` storage + TTL expiry.
  - [ ] Populate `allowed_actions` based on the device's current state
    (`requires_init` → `["startup"]`; `ready` → `["seal.start",
    "seal.set_temperature", ...]`; etc.).
  - [ ] Populate `details.claimed_by` while a claim is held.
  - [ ] Bump `PROTOCOL_VERSION` to `"1.1"`.
  - [ ] Snapshot fixtures for the three claim states.
  - [ ] Stand up the on-prem instance and flip `equipment.yaml`:
    `adapter: mock` → `http`, add `base_url`, `protocol: "1.1"`.

#### `filter_every_well`

- Current: FastAPI on `100.64.254.104:8000`, `/status` returns
  `StatusResponse` (legacy), control endpoints are `/init`,
  `/press/up`, `/press/down`, `/plate/in`, `/plate/out`, `/stop`.
- v1.0 work:
  - [ ] Add `models.py` with STATUS_SPEC types.
  - [ ] Replace `StatusResponse` on `/status` with `EquipmentStatus`.
  - [ ] Add `GET /` (`ProbeResponse`) and `GET /health`
    (`HealthResponse`).
  - [ ] Rename control endpoints under `/control/*`. Keep the legacy
    paths around for one transition window (return 200 + log a
    deprecation warning).
  - [ ] Update `equipment.yaml`: `adapter: legacy_http` → `http`,
    `status_path: /status` (already correct).
  - [ ] Drop the `legacy_http` adapter for this entry once the
    dashboard verifies green for 24h.

#### `dose_every_well`

- Current: FastAPI, `/status` returns `SystemStatus` (legacy), control
  endpoints are `/startup`, `/shutdown`, `/dose/well`, `/dose/multiple`,
  `/dose/row`, `/dose/column`, `/calibrate/flow-rate`, `/control/home`,
  `/control/tare`, `/control/read-balance`, plus plate management.
- v1.0 work:
  - [ ] Add `models.py` with STATUS_SPEC types.
  - [ ] Replace `SystemStatus` with `EquipmentStatus` on `/status`.
  - [ ] Add `/` and `/health`.
  - [ ] Move `/startup`, `/shutdown`, `/dose/*`, `/plate/*` under
    `/control/*` (some are already there). Keep legacy paths for one
    transition window.
  - [ ] Update `equipment.yaml`.

#### `fume_hood_actuator`

- Current: Flask on `100.64.254.100:5000`, `status_path:
  /equipment/status`.
- v1.0 work:
  - [ ] Port from Flask to FastAPI. Re-use the actuator/sensor classes
    untouched.
  - [ ] Add `models.py` with STATUS_SPEC types.
  - [ ] Standardise `/status` (drop the `/equipment/` prefix).
  - [ ] Add `/`, `/health`, `/control/*`.
  - [ ] Update `equipment.yaml`: `status_path: /equipment/status` →
    `/status`; `adapter: legacy_http` → `http`.

#### `xarm_translocation`

- Out of scope for this round. Keep `do_not_call_connect: true`. The
  gateway-PC shim architecture (xArm sits on a private subnet behind a
  PC running a thin REST proxy) needs to be decided before any
  spec-conformance work makes sense.

#### Mock-only entries

The remaining yaml entries (`ot2`, `agilent_uplc_ms`, `agilent_biostack`,
`cytation_5`, env sensors) stay `adapter: mock` until their respective
repos exist. They are intentionally not on this round's critical path.

## Resumption criteria for v0.4

The MCP milestone resumes when **all** of the following are true:

1. At least three devices in `equipment.yaml` have `adapter: http`
   (currently zero).
2. `agilent_plateloc` is at `protocol: "1.1"` (currently v1.0,
   `mock`).
3. `lab.skills()` returns a non-empty catalog with `available=True`
   entries against at least one v1.1 device.
4. A workflow can run a five-step `Plan` against `agilent_plateloc` (in
   dry-run mode is fine) using `validate_plan` + an executor (which
   means the v0.3 carry-overs above also need to land before v0.4 is
   considered "started").

Once those are green, v0.4 PR-1 (`execute_plan` + async interlocks +
sync façades) is the unblocking change, then PR-2 (`lab_skills.mcp` +
CLI) is the headline feature, then PR-3 (live agent acceptance against
PlateLoc) is the gate.

## Out of scope for this whole roadmap

- **Workflow runner** (Prefect / Temporal) — lives in project repos or
  a future `ac-organic-lab-runner`.
- **LLM / agent code** — future `ac-organic-lab-agents` repo.
- **Run records / manifests** — project repos own these.
- **`lab-status-contract` shared package** — wait until 3+ device repos
  ship on v1.1 cleanly (per `docs/STATUS_SPEC.md`).
- **Maintenance-tile UI rendering** — tracked separately; not blocking.

## See also

- `docs/STATUS_SPEC.md` — v1.0 device contract (authoritative).
- `docs/STATUS_SPEC_v1_1.md` — v1.1 additions (claims, `allowed_actions`).
- `docs/SKILLS_CATALOG.md` — what `Skill.name` / `allowed_actions` refer to.
- `docs/INTERLOCKS.md` — four-layer safety model; layer 4 ships in v0.3.
- `docs/ARCHITECTURE.md` — system layering + repo boundaries.
- `.cursor/plans/build_ac-organic-lab-skills_5bb34ed0.plan.md` — original
  layered plan; this file is its committed counterpart.
