# OT-2 Full-Page Interface

**Status:** shipped 2026-07-15 (branch `feature-ot2-interface`).
**Audience:** dashboard operators and developers touching the OT-2 UI.

The dashboard hosts a dedicated, server-side (Linux central server) full-page
interface for each Opentrons OT-2, alongside the existing compact
`LiquidHandlerTile` on the platform pages. The Windows gateways
(`opentrons-server`, ports 8020/8021) are **unchanged** — they remain the
single authority for deck, plate and tip state; this interface is a pure
consumer of the central equipment API.

## Routes

| Route | What it is |
|---|---|
| `/equipment/[equipmentId]/control` | Generic full-page equipment control view. Renders the OT-2 interface for `kind: liquid_handler`; other kinds get their status header and a notice. |
| `/ot2_hte` | Fixed-id alias of `/equipment/ot2_hte/control` (HTE bench OT-2). |
| `/ot2_complexation` | Fixed-id alias of `/equipment/ot2_complexation/control` (complexation bench OT-2). |

Adding a third OT-2 needs no new code: register it in `equipment.yaml` and use
`/equipment/<id>/control` (add an alias page only if a short URL is wanted).

## Data flow (unchanged invariants)

- **Reads** — the page polls `GET /api/equipment/{id}/status` (2.5 s React
  Query interval), the same central aggregator endpoint the tiles use. It
  renders `details.snapshot.deck` (normalized deck), `details.robot`
  (probe + live module telemetry), `details.tip_racks` / `details.mounted_tips`
  (gateway tip tracking), `details.claimed_by`, `components.*` (pipettes,
  SSH, protocol) and `last_error`. Nothing is read from the gateway directly,
  and no deck state is duplicated on the server.
- **Writes** — only `POST/DELETE /api/equipment/{id}/control/deck/declare`,
  through the existing audited control passthrough (`api/app/control.py`):
  middleware session check (`ac_auth`) → per-request claim → device →
  release → `control_action` audit row. The browser never calls raw gateway
  `/control/*` endpoints. Hardware execution (setup/home/aspirate/…) stays
  behind `lab-skills` validated plans and interlocks — the page does not
  expose those verbs.
- **Authorization** — same `useControlLock(equipmentId)` gate as the tiles:
  signed out ⇒ picker disabled with a "sign in" hint; signed in without a
  role on the device ⇒ "no access". The middleware enforces the same answer
  server-side; the client gate is UX only.

## Declaration vs physical setup (important)

The deck editor is labelled **"Declare deck intent"** deliberately:

- **Declaring** records *operator intent* in the gateway's persistent
  declaration store (`POST /control/deck/declare`). It is pure metadata — it
  does **not** load labware into an Opentrons protocol context, move
  hardware, or run `/control/setup`.
- The gateway merges declarations with what it *observes* (run/REPL deck)
  and flags disagreements per slot as `slot_state: "mismatch"` — the page
  renders declared and observed separately and badges mismatches (≠).
- **Physical setup** (actually loading labware/instruments on the robot) is
  `/control/setup` driven by a validated `lab-skills` plan — out of scope for
  this interface by design (constraint: the UI must not pretend declaration
  loads labware).

## Custom labware (builder + central store)

Three tiers of custom-labware support, added 2026-07-16:

1. **Free-text declare** — the control page's picker accepts any exact
   Opentrons `load_name` (must match `^[a-z0-9._]+$` and contain `_`,
   otherwise the gateway would parse it as a legacy kind string). Unknown
   names round-trip verbatim.
2. **Labware builder** (`/utils/labware_builder`) — a parametric form (grid, footprint,
   offsets, spacing, well geometry) that generates a complete Opentrons
   **schema-2** definition JSON with a live to-scale preview. Validation
   ports `opentrons-server`'s `LabwareGenerator` limits (footprint
   127 × 85.5 mm, height 200 mm, wells inside the footprint). Anyone can
   build + **download** the JSON; building never touches a robot.
3. **Central definition store** (`/api/labware`, `api/app/labware.py`) —
   two merged sources:
   - **(a) repo-committed**: `<repo>/labware/*.json`, PR-reviewed (see
     `labware/README.md`); wins on name collisions and is immutable via
     the API.
   - **(b) admin-uploaded**: `<data-dir>/labware/*.json`, written by
     `POST /api/labware` (session verified at the middleware, **admin role
     enforced server-side**; uploads validated with the same rules; every
     write audited as a `control_action` on the `labware_store`
     pseudo-device). `DELETE` removes uploaded definitions only.

   Store definitions appear in the deck picker's **"Custom (lab store)"**
   group, and workflows can fetch the full JSON (`GET /api/labware/{name}`)
   to pass as the labware `config` in a lab-skills `setup` plan
   (`protocol.load_labware_from_definition` on the gateway).

Env overrides: `LABWARE_REPO_DIR`, `LABWARE_UPLOAD_DIR` (defaults:
`<repo>/labware`, `<lab.db dir>/labware`).

The API additionally serves the **official Opentrons library** (the
`opentrons-shared-data` package, ~141 definitions, latest schema-2 version
each) read-only at `GET /api/labware/standard` (+ `/{load_name}`); the
builder lists it (searchable) and can load any entry's exact geometry for
modification. Uploads that would shadow a standard load name are refused
(409) — a custom variant needs its own name.

## The catalog (`web/src/lib/ot2-catalog.ts`)

Because the gateway is unchanged, the *choices* offered in the pickers are
authored centrally in the dashboard, separate from runtime state. Entries
carry a stable key, display label, category, the exact declare string, grid
dimensions and optional compatibility notes. Three declaration flavours:

- **Exact Opentrons load_names** (preferred) — e.g.
  `corning_96_wellplate_360ul_flat`, `agilent_1_reservoir_290ml`,
  `opentrons_96_tiprack_300ul`. The gateway parses any string containing
  `_` as a load_name and derives kind/grid from it.
- **Module keys** — `temperature_module`, `magnetic_module`,
  `heater_shaker_module`, `thermocycler_module` (the gateway's
  `deck.py _MODULE_KINDS`). Declared modules are sticky fixtures.
- **Legacy generic kinds** — `96-well`, `waste`, … kept so pre-catalog
  declarations keep round-tripping and coarse intent stays expressible.

Custom (MatterLab) labware definitions and `/control/setup` execution are
explicitly out of scope for this phase.

### Round-trip rule (the bug this fixes)

`POST /control/deck/declare` is a **full-layout replace**, so every edit
re-sends all currently-declared slots. The shared helper
(`declaredMapFromDeck` in `web/src/lib/ot2-deck.ts`) re-sends each declared
slot as its **exact `load_name`** when the gateway reported one (falling back
to `kind` only for legacy declarations, and to the module key for declared
modules). The previous tile round-tripped by `kind` only, which would have
silently degraded an exact load_name declaration on the next unrelated edit.

## Component layout

- `web/src/lib/ot2-deck.ts` — pure /status parsing + declaration logic
  (unit-tested, no React).
- `web/src/lib/ot2-catalog.ts` — the authored catalog + search/grouping.
- `web/src/components/DeckPanel.tsx` — the reusable 12-slot deck
  (`variant="tile"` in `LiquidHandlerTile`, `variant="page"` on the full
  page); module telemetry readouts incl. the temperature-module overhang
  cell.
- `web/src/components/Ot2ControlPanel.tsx` — the full page: header strip,
  claim banner, mismatch banner, deck + slot detail + searchable
  "Declare deck intent" picker, robot/pipettes/modules/tip-racks/
  mounted-tips/claim sections, footer with message + staleness.
- The compact tile is now a **read-only summary** (deck mirror, light /
  pipette / SSH / protocol pills) with a prominent "Control interface →"
  link. All controls — session lifecycle (connect/disconnect, pause),
  lights, and deck declaration — live on this page only.

Tests: `web/src/lib/ot2-deck.test.ts`, `web/src/lib/ot2-catalog.test.ts`
(pure logic, node env) and `web/src/components/DeckPanel.test.tsx`,
`web/src/components/DeclarePicker.test.tsx` (jsdom component tests — slot
selection, exact load-name declaration, mismatch rendering, auth-disabled
controls).

## See also

- [`EQUIP_STATUS.md`](EQUIP_STATUS.md) §11 — the compact tile's behaviour.
- `opentrons-server` `docs/DECK_STATE_PLAN.md` — the normalized deck shape;
  its `feature/deck-viewer` spec informed this page's rendering conventions.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) decision #1 — why writes go through
  the audited passthrough and the device stays the single authority.
