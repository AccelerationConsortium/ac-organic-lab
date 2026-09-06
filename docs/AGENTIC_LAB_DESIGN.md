# Agentic lab design — binding rules & operations layer

**Status:** merged 2026-08-12 from `AGENT_RULES.md` (canonical lab-wide rules,
draft 2026-07-03) and `AGENT_OPS.md` (agent operations layer, live since
2026-08-10/11); this file supersedes both. It has two parts with different
force:

- **Part I — Binding rules** (normative): the lab contract every agent — and
  every human using an agent — operates under. Section numbering (§1–§5) is
  preserved from `AGENT_RULES.md`, so existing citations (`§1.3`, `§2.4`, …)
  resolve unchanged.
- **Part II — Operations layer** (descriptive): the deployed agent-ops setup —
  MCP surfaces, trust tiers, and the per-machine host-ops fleet. Nothing in
  Part II weakens Part I.

Every project repo's root `AGENT_RULES.md` links back to the canonical
lab-wide rules; that target is now **Part I of this file** (the old
`docs/AGENT_RULES.md` path is retired).

---

## Part I — Binding rules (normative)

These rules apply to **every agent** (and every human using an agent)
operating on lab infrastructure: proposing protocols, driving workflows,
reading or writing records. They are guidance an agent must read and plan
around — **not enforcement**. Everything safety-critical also exists as a
hard check (interlocks, claims, CI validation, human approval gates,
protected branches). The absence of a rule is not permission; when a
situation isn't covered, stop and ask a human.

> **See also (non-normative):** this part is the *binding rules*. Day-to-day
> *working instructions* for agents — commands, repo layout, conventions, the
> memory policy — live in the repo-root [`AGENTS.md`](../AGENTS.md) (Claude
> specifics in [`CLAUDE.md`](../CLAUDE.md)). Those files reference these rules
> and may not weaken them.

### 1. Safety and hardware

1. **Never drive hardware directly.** All equipment use goes through the
   `lab-skills` SDK (skill catalog, claims, preconditions — interlock
   layer 3), never raw device `/control/*` endpoints. The SDK refusing a
   call *is* the safety system working.
2. **Never bypass, weaken, or work around an interlock** at any layer
   (hardware limits, device state machines, skill preconditions, project
   plan interlocks — see `INTERLOCKS.md`). If an interlock blocks an
   action, stop and report the violation; do not retry with adjusted
   parameters to get past the check.
3. **Only validated, human-approved plans execute.** A run requires a
   protocol merged to its project repo's `main` (the human sign-off), a
   registered `Plan` in BitacoraDB, and a passing `validate_plan()`
   (interlock layer 4). No ad-hoc command sequences against live hardware.
4. **Respect claims.** Acquire equipment through the SDK's claim mechanism;
   never operate equipment claimed by another session, and release claims
   when done.
5. **Anything physically unexpected → stop and escalate.** Spills, stuck
   plates, sensor readings that contradict expected state: halt the
   workflow and notify a human. Do not improvise recovery that involves
   hardware motion.

### 2. Records and data integrity

1. **Every run is recorded in BitacoraDB** through its REST API: the
   `Plan` at start, notes and measurements during, analyses after. If it
   isn't recorded, it didn't happen — and unrecorded work may not be used
   to justify decisions.
2. **Never fabricate, backfill, or edit records.** Observations are
   immutable; corrections and re-analyses are *new* records referencing
   the old (`corrects`, `supersedes`). Failed runs are recorded as failed,
   never deleted or retried into silence.
3. **Report truthfully.** Errors, deviations, and partial results are
   reported as such — to the human and to the record layer — not smoothed
   over in summaries.
4. **Identity is not negotiable.** Agents carry their own `agent_id` /
   `session_id` (OTel baggage); never write records or acquire claims
   under another identity, and never put secrets, credentials, or personal
   data in baggage, records, or metadata.
5. **Run data never goes into git repos.** Measurements, summary tables,
   images → BitacoraDB. Git holds authored artifacts only (protocols,
   analysis code, rules).

### 3. Protocols and change control

1. **Only `main` executes.** Protocol changes arrive by pull request; a
   human CODEOWNER's merge is the approval. Never push directly to a
   protected branch, never rewrite published history.
2. **`step_id`s are permanent** once a protocol has merged and executed:
   add steps, never rename or reuse ids — records anchor to them.
3. **Comments are for humans, fields are for machines.** Anything the
   executor needs must be a schema-validated field; YAML comments are
   dropped at render time.
4. **Local configs stay local.** Machine paths, hostnames, and credentials
   live in gitignored `*.local.json` files, never in commits.

### 4. Chemicals and materials

*(Enforced tooling lands with the BitacoraDB LIMS phase; the rules apply
now.)*

1. Use only substances and lots registered in the lab inventory; record
   consumption against the lot actually used, not a name string.
2. User-supplied chemicals are registered before use (owner's project,
   `source=user_provided`) — same rules, same records.
3. Never instruct a human to handle material in ways that conflict with
   its safety data; when a protocol touches a substance outside its
   project's declared scope, escalate rather than proceed.

### 5. Escalation

When rules conflict, when a check fails for unclear reasons, or when an
action is irreversible and not explicitly covered: **stop, preserve state,
ask a human.** A blocked run is recoverable; a wrong physical action or a
corrupted record may not be.

Project-specific rules live in each project repo's root `AGENT_RULES.md`
(see the `organic-hte-template` starter), which links back to this part.

---

## Part II — Operations layer

**Status:** live since 2026-08-10/11 (Hermes `lab-ops` profile), extended
2026-08-12 with the boxed `lab-runner` profile (verified: all three MCP
servers connect) and the `lab-runs` trigger surface. This part records the
*operations* agent setup: which MCP surfaces exist, their trust tiers, and how
the per-machine host-ops fleet is deployed. The access **boundary** (what the
agent may see) is owned by
[`HERMES_ACCESS_DESIGN.md`](HERMES_ACCESS_DESIGN.md); the binding conduct
rules are Part I above. Nothing here weakens either.

### The shape

One agent, many small hard-guarded servers — not one agent per machine:

```
Hermes profile "lab-ops"  (central server, sdl2, ~/.hermes/profiles/lab-ops/)
 ├── lab-history       stdio   read-only telemetry (api/app/mcp_server.py)
 ├── lab-skills        stdio   read + plan preflight, NO --allow-control
 │                             (skills/src/lab_skills/mcp.py)
 ├── hostops-gaia      stdio   central server host-ops (read-only instance)
 └── hostops-<pc>      http    per-device-PC host-ops (bearer token)

Hermes profile "lab-runner"  (BOXED: OS user hermes, /home/hermes/.hermes/)
 ├── lab-runs          stdio   authorized-run trigger (api/app/run_trigger.py)
 │                             LAB_ACTOR=hermes@lab.local bound in env
 ├── lab-skills        stdio   read + plan preflight, NO --allow-control
 │                             (eyes before the trigger: preflight, live state)
 └── lab-history       stdio   telemetry, tail_journald excluded; reads
                               lab.db via the read-only fallback
```

The two profiles split by **attendance** (HERMES_ACCESS_DESIGN Phase 0):
`lab-ops` is a human-driven ops console under `sdl2`; `lab-runner` is the
unattended principal — timers, webhooks, and the Slack reporter belong to it
and never to an `sdl2` profile. It carries no terminal, file, web, or cron
toolset: ingesting attacker-influenceable text must be harmless by toolset,
not by prompt.

These two profiles are not the only agent surfaces in the lab. The full map,
each with its own trust story and owning doc:

| Surface | Runs as / backed by | Trust ceiling | Owned by |
|---|---|---|---|
| Dashboard assistant (Ask/Control bubble) | dashboard host; per-mode engine — `claude` CLI (Control) or the OpenAI-compatible backend over OpenRouter (Ask, since 2026-08-13) | read telemetry + journal one observation; **propose** one action — or, since Step 1i (2026-08-20), one ordered multi-step plan on one device — that a human authorizes (never actuates) | `UI_DESIGN.md` §5, ARCHITECTURE #10 |
| Hermes `lab-ops` | `sdl2`, human-driven | ops console incl. shell; attended only | this file |
| Hermes `lab-runner` | boxed `hermes` user, unattended; **live on Slack since 2026-08-13** as *SDL2 Lab Runner* (`hermes-slack.service`, Socket Mode; per-user pairing + `allow_from`; native `/model` command withheld — model choice stays host-side, Phase 4.4) | trigger/watch/abort human-authorized runs; read + preflight; truthful model disclosure | this file + HERMES_ACCESS_DESIGN + `deploy/hermes-lab-runner/` |
| PyPoe lab integration | `pypoe-slack` / `pypoe-web` services | read-only lab surface: alert fan-out to Slack, Kuma tile, `/lab-*` queries, headless `claude -p` investigations | `pypoe/CLAUDE.local.md` Appendix A, LAB_MONITORING §6b |

Division of labour settled 2026-08-12: **PyPoe keeps the plumbing jobs**
(alert fan-out, the `uptime_kuma` tile, investigations) and **`lab-runner`
takes the conversational/trigger jobs** (run reporting, lab Q&A, preflight,
the Slack leg when it lands). Two bots, distinct jobs — the alarm system and
the operator; consolidation is a later decision, taken only after
`lab-runner`'s Slack leg has a track record.

A co-located agent on a device PC would gain no hardware reach (every device
is already a Tailnet REST service) while adding an unaudited shell next to
`/control/*` endpoints. So machines get **fixed-surface MCP servers** and the
agent stays central. Rationale and the alternatives considered live in the
session that produced this; the operative rule is below.

### Trust tiers

| Surface | May do | Must never do |
|---|---|---|
| `lab-history` | read telemetry, whitelisted journald tails | grow a control tool |
| `lab-skills` (no `--allow-control`) | list equipment/skills, live status, `validate_plan`, `preflight_plan` | register `execute_plan` for an agent client |
| `sdl-lab-hostops` instances | service status/log-tail, serial enumeration, loopback `/status` probes; `restart_service` for that host's `restartable` subset | touch device `/control/*`, run arbitrary shell |
| `lab-runs` (authorized-run trigger, `api/app/run_trigger.py`) | start/watch/abort a run a human already authorized — a thin client over `api/app/workflow.py`, which re-verifies everything | compose, edit, list, or approve plans itself |
| `lab-inventory` (chemical stock, `api/app/inventory_mcp.py`) | read chemical stock over bitácora's `/inventory` API: search, sufficiency checks, per-CAS detail, group totals | grow a write tool (import, tombstone, deduction — those stay identity-gated at bitácora's edge); open the store's SQLite file directly |
| `lab-control` (propose-only, `api/app/assistant_control.py`; spawned only by the dashboard assistant's Control mode, never wired into an agent profile) | validate ONE action (or, Step 1i, one ordered plan of actions on ONE device) against live `allowed_actions`, the skill catalog, and per-equipment authz, then return a proposal object the dashboard renders as a confirm card a human must authorize — a plan is approved by step hash and run step by step from the browser (UI_DESIGN §5) | issue a control call itself; accept the acting identity as a tool argument (bound via `LAB_ACTOR` in the server env); propose safety-floor actions (stop verbs, the xArm's connect / clear_errors) |

Two enforcement layers, deliberately redundant:

1. **Server-side** — the tool simply doesn't exist (`execute_plan` unregistered;
   hostops has no shell; hostops targets outside its config whitelist are
   refused).
2. **Client-side** — every `mcp_servers` entry in the agent profile carries a
   `tools: include:` allowlist **mirroring the entry in
   [`mcp/servers.yaml`](../mcp/servers.yaml)**, the reviewed registry. Widening
   an agent's reach is therefore a reviewed diff in that file, never a side
   effect of a server gaining a tool.

### The host-ops fleet (`sdl-lab-hostops`)

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

#### Deployed instances

| Instance (`equipment_id`) | Host | Transport | `restartable` | Deployed & verified |
|---|---|---|---|---|
| `hostops_gaia` | central server | stdio, spawned by the profile | — (read-only by policy: live-host restarts stay human) | 2026-08-10 |
| `hostops_cytation_pc` | `sdl2-pc-03-cytation` (NSSM service `sdl-lab-hostops`, :8060) | streamable-http + bearer token | `plateloc`, `torry-pines-shaker` | 2026-08-11 — verified end-to-end from the central server: serial enumeration (COM6 shaker / COM7 / COM8 BioStack / COM3 Intel AMT, matching ROADMAP's port notes), NSSM `service_status`, loopback `/status` probe of the shaker (200), whitelist refusal of `sshd`, and 401 on tokenless `/mcp` |
| `hostops_uplc_pc` | `sdl2-pc-06-uplc` (NSSM service `sdl-lab-hostops`, :8060) | streamable-http + bearer token | **empty — read-only by design**: the `hplc-ms-status` sidecar owns the run queue and production runs from branch `fix_server_vial`; agent restarts could disrupt a campaign | 2026-08-11 — verified end-to-end: both services (`hplc-ms-status`, `hplc-ms-sensors`) RUNNING, loopback probe of `:8010` returned the `agilent_uplc_ms` envelope (~2.8 s, the known OpenLab WMI latency), and `restart_service` correctly **refused** by the empty `restartable` list |
| `hostops_pi0_environ_01` | `sdl2-pi0-environ-01` (Pi Zero 2W, `~/sdl-lab-hostops`) | **daemonless stdio-over-SSH** (the lite mode): no daemon, no port, no token — spawned per connection via the `environ-01` alias; zero resident footprint on the 512 MB node | `sense-every-zone` (restart-safe passive gateway; `use_sudo` with the node's existing NOPASSWD) | 2026-08-11 — verified over SSH: service RUNNING, journald tail, probe of `:8030` via the v0.1.1 per-port path override (`/zones/env_hte/status`, 200 — the bare-`/status`-404s-by-design gateway shape). Note this node is ~56 % reachable (campus DHCP lease, see ROADMAP); a connect failure here is usually the node's offline window, not hostops |
| `hostops_pi0_fumehood3` | `sdl2-pi0-fumehood3-actuator` (Pi Zero, `~/sdl-lab-hostops`) | daemonless stdio-over-SSH (`fumehood-pi` alias) | **empty — read-only**: `sdl2` on that Pi has no passwordless sudo yet; arm restarts later with a sudoers drop-in for `systemctl restart actuator.service` | 2026-08-11 — verified over SSH: `actuator.service` RUNNING, journald readable, probe of `:5000` returned the `fume_hood_actuator` envelope (200), restart correctly refused |
| `hostops_pi5_minicnc` | `sdl2-pi5-minicnc` (the live `dose_every_well` host; `~caoyang/sdl-lab-hostops`) | daemonless stdio-over-SSH (`doser-pi` alias, user `caoyang`) | `platedoser-api` (restart armed via the node's existing passwordless sudo; the doser boots to `requires_init`, so a restart is recoverable) | 2026-08-12 — verified over SSH: `platedoser-api.service` RUNNING, journald readable, probe of `:8000` returned the `dose_every_well` envelope (`ready`), whitelist refusal of `tailscaled` |
| `hostops_gibbie_pc` | `sdl2-pc-04` (the Gibbie PC; NSSM service `sdl-lab-hostops`, :8060, LocalSystem, `--link-mode copy` like the monitor) | streamable-http + bearer token | `gibbie-server` (the read-only bench monitor; restarting it touches no hardware — the bench workflow is not a Windows service and stays out of reach) | **2026-09-06** — installed from gaia over the new lab-ops SSH grant and verified end-to-end: tokenless `initialize` → 401, with the token `host_info` / `service_status(gibbie-server)` / `probe_local_status(8070)` answer, a non-whitelisted service is refused; `/status` reads `ready` on the PCs & Servers page |

Pi instances run stdio-over-SSH, so they expose no HTTP `/status` — unlike the
Windows instances they get **no** `equipment.yaml` tile; their reachability is
already tracked via the device service they sit next to.

Pending Pi targets (2026-08-12): `sdl2-pi0-waters-filtration` (the press —
that node runs **Tailscale SSH**, so access is a tailnet-ACL `ssh` rule, not
`authorized_keys`; the one node already on HERMES_ACCESS_DESIGN's preferred
mechanism) and `sdl2-pi0-environ-02` (offline, awaiting hardware; trusts the
key from provisioning). `sdl2-pi5-cnc-doser-sam` holds only a dev clone of
the doser, no running service — deliberately no instance.

Both Windows PCs also carry the SSH key-trust grant for the central agent —
see DEVICE_PC_SETUP §2.4.

### Hardware control stance

The agent has **no execution path to hardware**: `execute_plan` is never
registered, hostops has no route to `/control/*`, and Part I §1.1/§1.3
bind any future change. The agreed direction for execution — a thin
authorized-run MCP surface over `api/app/workflow.py`, where the agent may
pull a trigger a human has already loaded (bitácora run authorization,
digest-pinned package, revocable mid-run) and nothing more — **shipped
2026-08-12 as `lab-runs`** (`api/app/run_trigger.py`, registered in
`mcp/servers.yaml`) **and wired the same day into the boxed `lab-runner`
profile** — its named prerequisites were met first: the `hermes` OS
principal (HERMES_ACCESS_DESIGN Phase 0) with the Phase-1 roster identity
(`hermes@lab.local`) bound as `LAB_ACTOR` in the profile environment. The
trigger must never be wired into a profile running as `sdl2` (attribution
rides `LAB_ACTOR` trusted by network position), and enabling
`--allow-control` for an agent client remains a spec violation of the
`mcp/servers.yaml` `lab-skills` entry, not a config choice.

The intended progression for `lab-runner` is recorded here so it is climbed
deliberately: (1) trigger/watch/report — live, and **proven on hardware
2026-08-14**: the fleet's first real authorized run
(`ra_67f32cb0920b4a41`, 14 steps on `ot2_complexation`) was Slack-triggered
by `lab-runner`, executed under per-step claims with the human
authorization pinned underneath, and filed in BitacoraDB — the full
rung-1 loop, dry-run and live, from a phone-reachable surface; (2)
lab-skills eyes (preflight + live state) — live; (3) plan *drafting*
through conversation,
entering bitácora **through the human who approves it** (never by agent
write — HERMES_ACCESS_DESIGN Phase 4.6); (4) coarser approval granularity
(campaign-level), still through the same authorized-run gate. At every rung
what loosens is the granularity of human approval, never the existence of
the hardware gate. Its learning/confidentiality rules are HERMES_ACCESS_DESIGN
Phase 4 (memory holds the platform, never the science).

## See also

- [`mcp/servers.yaml`](../mcp/servers.yaml) — the reviewed registry: approved
  servers, tool allowlists, provenance pins, data policies.
- [`HERMES_ACCESS_DESIGN.md`](HERMES_ACCESS_DESIGN.md) — the access boundary
  (platform agent, not science agent) and the SSH/key-trust policy for device
  PCs.
- [`LAB_MONITORING.md`](LAB_MONITORING.md) §4 — `hostops_action` event rows.
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — install recipe; §7 carries the
  `sdl-lab-hostops` service row.
- [`AGENTS.md`](../AGENTS.md) / [`CLAUDE.md`](../CLAUDE.md) — day-to-day
  working instructions that reference (and may not weaken) Part I.
- [`INTERLOCKS.md`](INTERLOCKS.md) — the four-layer safety model Part I §1
  points at.
