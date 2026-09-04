# ac-organic-lab dashboard API

The FastAPI dashboard server: thin presentation + observability over the
[`lab-skills`](../skills/README.md) SDK. It polls the fleet, serves the
Next.js UI (`web/`), owns the lab's operational history DB, and hosts the
operator-facing write paths (control passthrough, authorized-run executor,
assistant proposals). The full design rationale lives in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); this README is the
package-level map.

This package is a workspace member of the `ac-organic-lab` monorepo and
shares the repo-root `.venv` with `skills/` and `auth/`.

## Module map (`app/`)

| Module | What it owns |
|---|---|
| `main.py` | FastAPI app + lifespan; `/api/equipment`, `/api/platforms`, `/api/health`; the 60 s background uptime/activity poll; root logging config (app INFO → journald). |
| `presentation.py` | `EquipmentSnapshot` and the `_snapshot()` compose step (tile, platform, pills, location decorations). |
| `db.py` / `history.py` | `LabDatabase` (SQLite, `data/lab.db`, WAL, single writer) and the `/api/history/*` read + `/api/ingest/*` write routes. Schema in [`docs/LAB_MONITORING.md`](../docs/LAB_MONITORING.md). |
| `events.py` | Event-row helpers + the reader-side v2 field projection (`derive_v2_fields`, STATUS_SPEC Appendix B.2). |
| `control.py` | Operator control passthrough: per-request claim → action → release, per-equipment authz via the `ac_auth` sidecar, one `control_action` audit row per call (with `origin` from `X-Control-Origin`). |
| `workflow.py` | Authorized-run executor (Phase F): pulls a bitácora run authorization, re-verifies everything, drives the pinned package through `execute_plan` with an SSE step stream and cooperative abort; `plan_run` audit rows; D-23 filing to BitacoraDB via `record.py`. |
| `run_trigger.py` | The `lab-runs` MCP server: start/watch/abort a human-authorized run and nothing more (trust story in [`docs/AGENTIC_LAB_DESIGN.md`](../docs/AGENTIC_LAB_DESIGN.md) Part II). |
| `assistant.py` | `POST /api/assistant/chat` (SSE): backend dispatch + the `claude` CLI engine; per-turn journald observability lines. |
| `assistant_openai.py` | The second chat engine: OpenAI-compatible endpoint (default OpenRouter) with its own tool loop over the same MCP servers. |
| `assistant_control.py` | The `lab-control` MCP server: propose-only — validates one action against live `allowed_actions` + per-equipment authz and returns a proposal card; never POSTs to a device. |
| `mcp_server.py` | The `lab-history` MCP server: eight read-only tools over `lab.db` / the live aggregator / whitelisted journald, plus the append-only `record_observation` journal write. |
| `alert_notifier.py` | Debounced device alerts (unreachable / error / e_stop / recovered) → PyPoe's `/alerts/device` webhook. |
| `record.py` | D-23 record layer: files finished runs in BitacoraDB (Experiment ensured, run as `Plan`, non-success steps as `Note`s). |
| `deck.py` / `labware.py` | Shared OT-2 deck-layout store; the reviewed + uploaded custom-labware store (see [`labware/README.md`](../labware/README.md)). |

## Run it

```bash
uv sync                      # from the repo root
uv run uvicorn app.main:app --reload \
    --reload-include "*.py" --reload-include "*.yaml" --port 8001
```

Reads `equipment.yaml` / `platforms.yaml` from the repo root (`LAB_REGISTRY_PATH`
/ `LAB_PLATFORMS_PATH` override). Configuration is env-driven — the deployed
unit loads the repo-root `.env` via `EnvironmentFile=`; the assistant's
`ASSISTANT_*` families are documented in the `assistant.py` /
`assistant_openai.py` module docstrings, and
[`deploy/README.md`](../deploy/README.md) covers where secrets belong.

## Tests

```bash
uv run pytest api/tests -q
```

## See also

- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — layering, the two-writers
  decision (#1), the assistant decisions (#10), history-DB ownership (#9).
- [`docs/LAB_MONITORING.md`](../docs/LAB_MONITORING.md) — DB schema, event
  registry, alerting pipeline.
- [`deploy/README.md`](../deploy/README.md) — systemd units, sandboxing,
  secrets, operations.
