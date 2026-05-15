# ac-organic-lab

Monorepo for the Acceleration Consortium (AC) Organic Self-driving Lab platform stack: the equipment-status contract, the inventory, the Python SDK that workflows and the dashboard share, and the dashboard's web server and Next.js UI.

The dashboard runs on a single Tailscale-attached server and aggregates status from each lab equipment's REST API into one normalized contract. The browser only ever talks to the dashboard server; the dashboard server is the only client that calls the equipment APIs over the lab Tailnet. Workflow code uses the same SDK directly without going through the dashboard.

## Dashboard Preview

![Organic Self-driving Lab dashboard preview](docs/images/dashboard-preview.png)

## Architecture

```
Browser  ->  Next.js (web/, port 3000)  ->  FastAPI (api/, port 8001)  ->  lab-skills (skills/)  ->  Equipment APIs over Tailscale
                                                                                       ^
                                                       Workflow scripts ----------------+
```

- **`skills/`** - `lab-skills` Python SDK. Owns the registry, polling aggregator, per-device adapters, and the workflow-facing session API. Imported by `api/` and by project workflow repos.
- **`api/`** - FastAPI dashboard server. Thin presentation layer over `skills/`.
- **`web/`** - Next.js 14 (App Router) + TypeScript + TanStack Query.
- **`docs/`** - architectural documents, device contract, runbooks, roadmap. See [Documentation](#documentation) below.
- **`equipment.yaml`** - the equipment registry (committed). Tailscale hostnames are not treated as secrets.
- **`.env`** / **`.env.example`** - real secrets only (control tokens, webhooks). Currently unused in the read-only v1.
- **`deploy/`** - example systemd units for the two services.

## Documentation

All design documents live in [`docs/`](docs/). Start with [`STATUS_SPEC.md`](docs/STATUS_SPEC.md) if you are bringing a new piece of equipment online, and [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) if you want to understand how the platform fits together.

| Document | What it covers |
|---|---|
| [`docs/STATUS_SPEC.md`](docs/STATUS_SPEC.md) | **Authoritative device contract.** Combined v1.0 baseline + v1.1 additions (cooperative claims, `allowed_actions`, `details.claimed_by`). Includes the conformance checklists every device repo follows and an appendix comparing this contract to the **SiLA 2** standard. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Long-form description of the monorepo's layering, why we picked it, and the responsibilities of `skills/`, `api/`, `web/`, and the per-device repos. |
| [`docs/SKILLS_CATALOG.md`](docs/SKILLS_CATALOG.md) | How the SDK describes "what the lab can do right now": `SkillDef` (static) vs `Skill` (runtime), how `allowed_actions` is computed, evolution from hard-coded → device-declared. |
| [`docs/INTERLOCKS.md`](docs/INTERLOCKS.md) | Four-layer safety model (hardware limits → device state machine → skill preconditions → project plan interlocks); `validate_plan` / `execute_plan` API. |
| [`docs/EQUIPMENT_INTEGRATION.md`](docs/EQUIPMENT_INTEGRATION.md) | Operational runbook: registering a new device in `equipment.yaml`, preventing placeholder-hostname regressions, maintenance windows, camera + smart-plug onboarding. |
| [`docs/DEVICE_PC_SETUP.md`](docs/DEVICE_PC_SETUP.md) | Canonical install recipe for a Windows device PC (uv + NSSM + Tailscale). Linked from every device repo's README rather than duplicated per-repo. |
| [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) | Logging tiers (journald → events.jsonl → central SQLite), the history DB schema, dashboard history endpoints, retention guidance. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Per-device migration status (`legacy_http` → v1.0 → v1.1), SDK milestones (v0.1 → v0.5), and live operational regressions. |
| [`deploy/README.md`](deploy/README.md) | Linux server deployment, systemd units, Caddy + Tailscale TLS, day-to-day operations. |

## Local development

### Python (uv workspace)

The two Python packages (`skills/` and `api/`) share one `.venv` at the repo root, managed by [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                # creates .venv/ at the root, installs both members editable
uv run uvicorn api.app.main:app --reload \
    --reload-include "*.py" --reload-include "*.yaml" --reload-include "*.yml" \
    --port 8001
```

The aggregator reads `equipment.yaml` from the repo root at startup. Override the path with `LAB_REGISTRY_PATH=/abs/path/to/equipment.yaml`.

The extra `--reload-include` flags are required: `uvicorn --reload` only watches `*.py` by default, so YAML edits would otherwise need a manual restart. With them set, saving `equipment.yaml` triggers an automatic API restart and the browser picks up the change on its next poll (no manual refresh needed).

### Frontend (Next.js)

```bash
cd web
npm install
npm run dev
```

The dev server runs on `http://localhost:3000` and proxies `/api/*` to `http://localhost:8001`.

The `dev` script sets `WATCHPACK_POLLING=true` so Next.js's file watcher uses polling instead of FSEvents. This avoids the `EMFILE: too many open files` errors that happen on macOS because `launchctl limit maxfiles` defaults to 256 (way below what Next.js watches). Polling adds ~1-2% CPU and no dev-experience downside.

If you want native FSEvents back (slightly lower idle CPU), raise the system limit permanently and remove the env vars from `web/package.json`:

```bash
sudo cp deploy/limit.maxfiles.plist /Library/LaunchDaemons/limit.maxfiles.plist
sudo chown root:wheel /Library/LaunchDaemons/limit.maxfiles.plist
sudo chmod 644 /Library/LaunchDaemons/limit.maxfiles.plist
sudo launchctl load -w /Library/LaunchDaemons/limit.maxfiles.plist
# reboot, then verify: launchctl limit maxfiles  -> 65536 65536
```

To regenerate the TypeScript types from the live FastAPI OpenAPI doc (the aggregator must be running):

```bash
cd web
npm run gen:api-types   # writes src/types/api.generated.ts
```

`web/src/types/api.ts` is hand-curated and re-exports the friendly type names from the auto-generated file. Edit `api.ts` if you want to add aliases; never edit `api.generated.ts`.

## Customising the layout

Everything visible on the dashboard - equipment ordering, tile sizes, and sensor positions on the lab map - is driven by `equipment.yaml`. No frontend code changes needed for layout tweaks. Edit YAML, save, and the API auto-reloads (`uvicorn --reload`); the browser hot-reloads on its own.

### Equipment order

The order of entries in `equipment.yaml` is the order tiles appear in the UI - both inside each platform card on the overview page and on the platform detail pages.

### Tile sizes (HTE platform detail page)

The overview page (`/`) always uses a compact two-column equipment list inside each platform card - tile sizing has no effect there.

The platform detail pages (e.g. `/platforms/hte`) lay equipment cards on a 4-column CSS grid. Add a `tile` entry to any equipment to change its size:

```yaml
- id: xarm_translocation
  name: UFactory xArm5
  ...
  tile: { w: 2, h: 2 }   # spans 2 of 4 columns and 2 rows
```

| `w` | spans (lg+) | typical use |
|-----|-------------|-------------|
| `1` | quarter row | very compact, info-light device |
| `2` | half row (default) | most devices |
| `3` | three-quarters row | rare |
| `4` | full row | banner / hero device |

`h` (1..4) is honoured as a row span on the 4-col grid. Heights are content-driven - rich cards just take whatever vertical space they need. Both fields are validated by Pydantic on startup so a typo fails the API immediately.

Responsive behaviour: on mobile (< sm) every card is full-width; from sm to lg the grid is 2 columns and `w` is capped at 2; from lg+ the full 4-column grid applies.

The current HTE Platform layout (lg breakpoint and above), top to bottom:

```
+--------------------------+--------------------------+
|                          |                          |
|     UFactory xArm5       |     Opentrons OT-2       |   2x2 each
|                          |                          |
+--------------------------+--------------------------+
|                          |
|    Fume Hood Actuator    |    (remaining 2x1 cards fill in)
|                          |
+--------------------------+--------------------------+
|     UPLC-MS    | Dose Every Well | Waters Filtration | ...
+--------------------------+--------------------------+
```

### Sensor positions on the lab map

Environmental sensors place markers on the SVG floorplan in `web/src/components/LabMap.tsx`. The map is rotated 90° clockwise from the building plan, so on screen north is up. Set `location.x` and `location.y` as percentages of the map (0-100) inside each sensor entry:

```yaml
- id: env_lab499_west
  kind: environmental_sensor
  ...
  location: { x: 20, y: 75, label: "Lab 499 · West" }
```

The four zones are:

- Stairs (top-left quadrant, greyed out, no markers expected)
- Sample Prep (top-right quadrant)
- Storage (middle horizontal band)
- Lab 499 (bottom half, the main lab)

## Tests

Python tests (both packages):

```bash
uv run pytest skills/tests api/tests -q
```

Frontend type-check and build:

```bash
cd web
npm run typecheck
npm run build
```

## Deployment (Linux server with systemd)

Both processes run as separate systemd services on one Tailscale-attached Linux server. There is no per-equipment authentication in v1 - access is gated by Tailscale ACLs.

See [`deploy/README.md`](deploy/README.md) for:

- The complete one-time server setup (user, venv, build, static-asset copy, systemd install).
- Day-to-day operations (log tailing, redeploy commands, restart flow when `equipment.yaml` changes).
- How to front the service with Caddy over Tailscale's `tailscale cert` for TLS, or bind it directly to the tailnet.
- Sandboxing directives included in each unit.
- A troubleshooting table.

For equipment onboarding and maintenance/offline procedures, see
[`docs/EQUIPMENT_INTEGRATION.md`](docs/EQUIPMENT_INTEGRATION.md). For
the canonical install recipe on a Windows device PC see
[`docs/DEVICE_PC_SETUP.md`](docs/DEVICE_PC_SETUP.md).

The unit files themselves live at [`deploy/ac-dashboard-api.service`](deploy/ac-dashboard-api.service) and [`deploy/ac-dashboard-web.service`](deploy/ac-dashboard-web.service). Both set `Restart=on-failure`, journal logging, `LimitNOFILE=65536`, and standard systemd hardening directives (`ProtectSystem=strict`, `NoNewPrivileges`, etc.).

## Cameras and smart plugs

Tapo cameras and Kasa smart plugs are integrated through a companion
gateway service ([`kasa-tapo-services`](https://github.com/cyrilcaoyang/kasa_tapo_services))
that translates the proprietary device protocols into the same
[STATUS_SPEC](docs/STATUS_SPEC.md) HTTP envelope as every other piece of equipment.

When a camera is registered in `equipment.yaml` with `kind: camera`,
the dashboard renders a richer tile on its platform panel:

- live MSE video feed (active lens), with a Wide/Tele tab strip on
  dual-lens models;
- 8-direction PTZ pad on the left;
- preset selector + "Save current view as…" in the middle;
- snapshot, record (start/stop/cancel), and "Recent captures →" link
  on the right;
- Streaming / Privacy toggles and a staleness indicator at the bottom.

Snapshots and recordings are written by the gateway on the dashboard
host (default: `/var/lib/kasa-tapo-media/{snapshots,recordings}/<camera_id>/<lens>/`)
and exposed back through `GET /api/equipment/<id>/media` (listing) and
`GET /api/equipment/<id>/media/<kind>/<lens>/<file>` (binary download).
The minimal "Recent captures" page at
`/platforms/<platform>/media/<camera_id>` lists everything currently on
disk.

See [`deploy/README.md` § _Optional: cameras + smart plugs_](deploy/README.md#optional-cameras--smart-plugs-kasa-tapo-services)
for the production wiring.

## Status

**v1 (current scope):** read-only monitoring of plate readers, sealers,
sensors, etc., plus full control of cameras (PTZ, presets, snapshot,
recording) and Kasa plugs through `kasa-tapo-services`. Two main pages:
lab overview (`/`) and per-platform detail (e.g. `/platforms/hte`).
Polling every 2-3 seconds.

**Future:** WebSocket-based real-time pages, control endpoints with
explicit confirmations on the slow lab equipment, persistent history.
