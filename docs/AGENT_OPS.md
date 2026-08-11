# Agent operations layer — central agent, MCP surfaces, host-ops fleet

**Status:** live since 2026-08-10/11 — Hermes `lab-ops` profile on the central
server, with the deployed instances listed below. This document records the *operations* agent setup: which MCP surfaces exist,
their trust tiers, and how the per-machine host-ops fleet is deployed. The
access **boundary** (what the agent may see) is owned by
[`HERMES_ACCESS_DESIGN.md`](HERMES_ACCESS_DESIGN.md); the binding conduct rules
by [`AGENT_RULES.md`](AGENT_RULES.md). Nothing here weakens either.

## The shape

One agent, many small hard-guarded servers — not one agent per machine:

```
Hermes profile "lab-ops"  (central server, ~/.hermes/profiles/lab-ops/)
 ├── lab-history       stdio   read-only telemetry (api/app/mcp_server.py)
 ├── lab-skills        stdio   read + plan preflight, NO --allow-control
 │                             (skills/src/lab_skills/mcp.py)
 ├── hostops-gaia      stdio   central server host-ops (read-only instance)
 └── hostops-<pc>      http    per-device-PC host-ops (bearer token)
```

A co-located agent on a device PC would gain no hardware reach (every device
is already a Tailnet REST service) while adding an unaudited shell next to
`/control/*` endpoints. So machines get **fixed-surface MCP servers** and the
agent stays central. Rationale and the alternatives considered live in the
session that produced this; the operative rule is below.

## Trust tiers

| Surface | May do | Must never do |
|---|---|---|
| `lab-history` | read telemetry, whitelisted journald tails | grow a control tool |
| `lab-skills` (no `--allow-control`) | list equipment/skills, live status, `validate_plan`, `preflight_plan` | register `execute_plan` for an agent client |
| `sdl-lab-hostops` instances | service status/log-tail, serial enumeration, loopback `/status` probes; `restart_service` for that host's `restartable` subset | touch device `/control/*`, run arbitrary shell |
| *(future)* authorized-run trigger | start/abort/watch a run a human already authorized (`api/app/workflow.py`) | compose or approve plans itself |

Two enforcement layers, deliberately redundant:

1. **Server-side** — the tool simply doesn't exist (`execute_plan` unregistered;
   hostops has no shell; hostops targets outside its config whitelist are
   refused).
2. **Client-side** — every `mcp_servers` entry in the agent profile carries a
   `tools: include:` allowlist **mirroring the entry in
   [`mcp/servers.yaml`](../mcp/servers.yaml)**, the reviewed registry. Widening
   an agent's reach is therefore a reviewed diff in that file, never a side
   effect of a server gaining a tool.

## The host-ops fleet (`sdl-lab-hostops`)

Repo: [AccelerationConsortium/sdl-lab-hostops](https://github.com/AccelerationConsortium/sdl-lab-hostops)
(own repo, *not* part of this monorepo — it deploys to every machine except
mostly this one, and its `mcp` 2.x dependency must not constrain this
workspace's shared venv). Registered in `mcp/servers.yaml`.

Per-instance facts:

- **One instance per machine**, installed like any device service
  (DEVICE_PC_SETUP §3 for Windows/NSSM; systemd unit or daemonless
  stdio-over-SSH for Pis — see the repo README).
- **Config is machine-local** (`config.toml`, gitignored): the services
  whitelist, the stricter `restartable` subset, and the loopback ports
  `probe_local_status` may reach. The registry approves the *tool surface*;
  each host decides its *targets*.
- **Windows instances run as `LocalSystem`** — a documented exception to
  DEVICE_PC_SETUP §5, which exists for vendor `HKCU` profiles and COM-port
  sessions; hostops uses neither, and needs service-control rights.
- **Auth:** `http` transport requires `HOSTOPS_TOKEN` on any non-loopback
  bind (refuses to start without it); Tailscale ACLs gate the network path.
  `GET /status` is the one unauthenticated route — a STATUS_SPEC v1.2
  read-only envelope so the instance can be added to `equipment.yaml` as a
  monitored web-service tile.
- **Audit:** mutating calls (today: `restart_service`) post `hostops_action`
  events to `POST /api/ingest/events` — see the LAB_MONITORING §4 registry.
  Reads are not audited.
- The central-server instance (`hostops-gaia`) is **read-only**
  (`restartable = []`): restarts on the live host stay with the human
  operator.

### Deployed instances

| Instance (`equipment_id`) | Host | Transport | `restartable` | Deployed & verified |
|---|---|---|---|---|
| `hostops_gaia` | central server | stdio, spawned by the profile | — (read-only by policy: live-host restarts stay human) | 2026-08-10 |
| `hostops_cytation_pc` | `sdl2-pc-03-cytation` (NSSM service `sdl-lab-hostops`, :8060) | streamable-http + bearer token | `plateloc`, `torry-pines-shaker` | 2026-08-11 — verified end-to-end from the central server: serial enumeration (COM6 shaker / COM7 / COM8 BioStack / COM3 Intel AMT, matching ROADMAP's port notes), NSSM `service_status`, loopback `/status` probe of the shaker (200), whitelist refusal of `sshd`, and 401 on tokenless `/mcp` |

| `hostops_uplc_pc` | `sdl2-pc-06-uplc` (NSSM service `sdl-lab-hostops`, :8060) | streamable-http + bearer token | **empty — read-only by design**: the `hplc-ms-status` sidecar owns the run queue and production runs from branch `fix_server_vial`; agent restarts could disrupt a campaign | 2026-08-11 — verified end-to-end: both services (`hplc-ms-status`, `hplc-ms-sensors`) RUNNING, loopback probe of `:8010` returned the `agilent_uplc_ms` envelope (~2.8 s, the known OpenLab WMI latency), and `restart_service` correctly **refused** by the empty `restartable` list |

The cytation instance also carries the fleet's SSH key-trust grant for the
central agent — see DEVICE_PC_SETUP §2.4 (the uplc grant is pending).

## Hardware control stance

The agent has **no execution path to hardware**: `execute_plan` is never
registered, hostops has no route to `/control/*`, and AGENT_RULES §1.1/§1.3
bind any future change. The agreed direction for execution is a thin
authorized-run MCP surface over `api/app/workflow.py` — the agent may pull a
trigger a human has already loaded (bitácora run authorization, digest-pinned
package, revocable mid-run), and nothing more. Enabling `--allow-control`
for an agent client without that gate is a spec violation of the
`mcp/servers.yaml` `lab-skills` entry, not a config choice.

## See also

- [`mcp/servers.yaml`](../mcp/servers.yaml) — the reviewed registry: approved
  servers, tool allowlists, provenance pins, data policies.
- [`HERMES_ACCESS_DESIGN.md`](HERMES_ACCESS_DESIGN.md) — the access boundary
  (platform agent, not science agent) and the SSH/key-trust policy for device
  PCs.
- [`LAB_MONITORING.md`](LAB_MONITORING.md) §4 — `hostops_action` event rows.
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — install recipe; §7 carries the
  `sdl-lab-hostops` service row.
- [`AGENT_RULES.md`](AGENT_RULES.md) — binding conduct rules.
