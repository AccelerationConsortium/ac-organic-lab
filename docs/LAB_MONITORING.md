# Lab Monitoring — Logging, Events, History, and Alerting

Platform-specific guidelines, storage schema, alerting stack, and dashboard
integration notes for the AC Organic Self-driving Lab. Read alongside
[`STATUS_SPEC.md`](STATUS_SPEC.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Platform hardware reference

| Host | Examples in lab | RAM | Storage | Key constraint |
|---|---|---|---|---|
| **Pi Zero 2 W** | Sash automation, simple sensors | 512 MB | µSD card (slow, limited write cycles) | No high-frequency writes to disk. Journald in RAM only. |
| **Raspberry Pi 5** | `dose_every_well`, filter-every-well, future sensor hubs | 4–8 GB | µSD or NVMe HAT | Safe for rotating log files. Enable journald persistence. |
| **Linux PC** | xArm (`sdl2-pc-03`), UPLC-MS (`sdl2-pc-06`), dashboard (`100.64.254.6`) | 8–32 GB | SSD | Standard Linux defaults work. SQLite database lives here. |

---

## 2. Journald guidelines per platform

### Pi Zero — keep everything in RAM

```ini
# /etc/systemd/journald.conf on Pi Zero hosts
[Journal]
Storage=volatile          # RAM only — never writes to SD card
RuntimeMaxUse=32M         # cap at 32 MB of RAM
```

Rationale: the µSD write endurance is limited and a Pi Zero has no real-time data
worth persisting on-device. Journald is for live debugging (`journalctl -fu <service>`)
not long-term storage. The relevant science records are pushed to the central SQLite
database on the dashboard host (§4).

### Pi 5 — enable persistence, cap size

```ini
# /etc/systemd/journald.conf on Pi 5 hosts
[Journal]
Storage=persistent        # writes to /var/log/journal/
SystemMaxUse=256M         # max 256 MB across all rotated journals
SystemKeepFree=512M       # never use more than this below free-disk threshold
MaxRetentionSec=30day     # drop entries older than 30 days
```

Enable with:

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```

### Linux PC (dashboard host) — standard retention

```ini
# /etc/systemd/journald.conf on the dashboard host
[Journal]
Storage=persistent
SystemMaxUse=2G
MaxRetentionSec=90day
```

The dashboard host also holds the SQLite database (§4), so journald here is backup
context, not primary storage.

---

## 3. Application logging — what goes where

Three tiers, chosen by frequency and audience:

```
Tier 1 — Journald (text, per host, for humans)
  Who reads it: engineers debugging a specific failure right now.
  What belongs here: every log.INFO+ line from the uvicorn process.
  Rate: any rate is fine — journald handles it.

Tier 2 — events.jsonl (JSONL, per device, for device-level audit)
  Who reads it: post-run analysis, device maintainers.
  What belongs here: startup, shutdown, state transitions, dispense results,
                     claim acquired/released, calibration, errors.
  Rate: < 100 events/day per device. Never write balance readings here.
  Location: ~/.dose_every_well/logs/events.jsonl  (dev)
            /var/log/<service>/events.jsonl        (production on Pi 5 / PC)

Tier 3 — Central SQLite on dashboard host (structured, for cross-device analysis)
  Who reads it: dashboard plots, workflow scripts, scientists.
  What belongs here: run records, well results, sensor summaries, uptime events.
  Rate: aggregator writes one row per completed action — never per-poll.
  Location: /opt/ac-organic-lab/data/lab.db
```

### Per-platform write rules

| Write | Pi Zero | Pi 5 | PC |
|---|---|---|---|
| Journald text log | RAM-only, any rate | Persistent, any rate | Persistent, any rate |
| events.jsonl on device | Avoid (SD wear) | OK — /var/log/ | OK |
| SQLite rows | Never locally | Never locally | Lives here, written by aggregator |
| Balance readings raw | RAM buffer only | RAM buffer only | RAM buffer only |
| Camera snapshots/recordings | N/A | N/A | /var/lib/kasa-tapo-media/ |

---

## 4. Central SQLite schema (`lab.db`)

Lives at `/opt/ac-organic-lab/data/lab.db` on the deployed dashboard host;
the code default is repo-root `data/lab.db`, overridden via `LAB_DB_PATH`
(see `api/app/db.py`).
Written exclusively by the FastAPI aggregator process — never by device services directly.

```sql
-- ─────────────────────────────────────────────────────────────────────────
-- Equipment state events  (state transitions, errors, startup, shutdown)
-- One row per event — low frequency (~10/day per device)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS equipment_events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,           -- ISO-8601 UTC
    device_id   TEXT    NOT NULL,           -- equipment.yaml key, e.g. "dose_every_well"
    event_type  TEXT    NOT NULL,           -- "state_transition" | "activity_transition" |
                                            -- "error" | "startup" | "shutdown" |
                                            -- "calibration" | "claim_acquired" |
                                            -- "control_action" (dashboard operator write —
                                            --   payload: {action, method, status_code,
                                            --   outcome, owner, duration_s};
                                            --   written by api/app/control.py)
                                            -- App-written types are pinned in
                                            -- api/app/events.py (§4 registry below).
    from_state  TEXT,                       -- for {state,activity}_transition
    to_state    TEXT,                       -- for {state,activity}_transition
    message     TEXT,                       -- human-readable description
    payload     TEXT                        -- JSON blob for extra fields
);
CREATE INDEX IF NOT EXISTS idx_ee_device_ts ON equipment_events(device_id, ts);

-- ─────────────────────────────────────────────────────────────────────────
-- Service uptime  (service start / stop / crash per host)
-- Used for the uptime percentage tile on the dashboard
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS service_uptime (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,
    device_id   TEXT    NOT NULL,
    event       TEXT    NOT NULL,   -- "up" | "down" | "unreachable" | "recovered"
    consecutive_failures INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_su_device_ts ON service_uptime(device_id, ts);

-- ─────────────────────────────────────────────────────────────────────────
-- Environmental sensor readings  (downsampled — one row per minute maximum)
-- For temperature, humidity, CO2, pressure etc. across the 4 sensor zones
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sensor_readings (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,
    sensor_id   TEXT    NOT NULL,   -- equipment.yaml zone id, e.g. "env_lab499_west"
    metric      TEXT    NOT NULL,   -- "temperature_c" | "humidity_pct" | "co2_ppm"
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL    -- "°C" | "%" | "ppm" | "hPa"
);
CREATE INDEX IF NOT EXISTS idx_sr_sensor_ts ON sensor_readings(sensor_id, ts);

-- ─────────────────────────────────────────────────────────────────────────
-- Dosing runs  (one row per plate dosing run)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT    PRIMARY KEY,    -- UUID from workflow script
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    device_id       TEXT    NOT NULL,
    config_name     TEXT,
    plate_id        TEXT,                   -- plate barcode or user-assigned name
    compound_id     TEXT,                   -- compound being dosed (optional)
    target_mg       REAL,                   -- nominal target per well
    n_wells         INTEGER DEFAULT 0,
    n_converged     INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'in_progress'
                            CHECK(status IN ('in_progress','complete','failed','aborted'))
);

-- ─────────────────────────────────────────────────────────────────────────
-- Per-well dispense results
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS well_results (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    REFERENCES runs(id) ON DELETE CASCADE,
    ts          TEXT    NOT NULL,
    well        TEXT    NOT NULL,           -- "A1" … "H12"
    target_mg   REAL    NOT NULL,
    actual_mg   REAL,
    converged   INTEGER NOT NULL DEFAULT 0, -- 0/1
    iterations  INTEGER,
    duration_s  REAL
);
CREATE INDEX IF NOT EXISTS idx_wr_run ON well_results(run_id);
```

### The `event_type` registry

`event_type` is TEXT with no CHECK constraint — any string is accepted on
the wire. This registry records what is actually emitted (and by whom) so
readers don't have to reverse-engineer it from the table:

| `event_type` | Emitted by | Notes |
|---|---|---|
| `state_transition` | **Aggregator** 60 s poll (`api/app/main.py`) on an observed `equipment_status` change; **device-pushed** by exporters (xArm) for fine-grained transitions | The poll path misses any transition that begins and ends inside one 60 s window; device-pushed rows close that gap. Both use `from_state` / `to_state`. |
| `activity_transition` | **Aggregator** 60 s poll, in parallel with `state_transition` (STATUS_SPEC v1.2 §2.3); **device-pushed** rows welcome (see below) | The activity series (`idle`/`running`/`unknown` in `from_state`/`to_state`) that survives a chronic health fault — utilization for a device stuck on `degraded`. Poll rows carry `payload: {source: device\|status\|components\|none}` recording how the value was derived (`events.py::derive_activity`). Poll-sampled: a cycle shorter than 60 s is **missed**, not undercounted (§2.3.1) — hence `metrics["cycles_total"]` and device-pushed rows. **Device-pushed convention:** POST to `/api/ingest/events` with `event: "activity_transition"`, exact `timestamp`, `from_state`/`to_state` ∈ {idle, running}, `extra: {source: "device_event"}`. Duplicate/overlapping rows from the two emitters are harmless — the time-pct window charges by `to_state`, and both emitters describe the same hardware truth. |
| `control_action` | Dashboard control passthrough (`api/app/control.py`) | One audit row per operator write: `payload: {action, method, status_code, outcome, owner, duration_s}`. `duration_s` = wall-clock of the device interaction (claim → action → release); null on rows before 2026-07-24 and on refusals that never reached the device. |
| `ssh_session` | Browser SSH console (`api/app/ssh_console.py`), admin-only | Two rows per session: `outcome: "ticket_issued"` when an admin asks for a shell, and one when it ends (`outcome: exited\|disconnected\|idle_timeout\|error`) carrying `payload: {user, target, duration_s, exit_code, detail}`. `device_id` is the **SSH host id** (`gaia` / `cytation-pc` / `uplc-pc`), not an `equipment.yaml` id — a host machine is not equipment, but "who opened a shell where, when" belongs in the same audit table as "who moved the sash". Human admins only: a machine principal (`X-Api-Key`) cannot mint a ticket, so no agent surface appears in this series — see [`UI_DESIGN.md`](UI_DESIGN.md) §6. |
| `agent_observation` | PyPoe read-only journaling; dashboard assistant (`record_observation`, actor-stamped) | Free-form agent notes via `/api/ingest/events`. Read back with `GET /api/history/events/{device_id}?event_type=agent_observation` (PyPoe's `recent_observations` tool, and the assistant's `query_equipment_events(event_type=…)`) so an investigation recognises a recurrence and builds on the prior root cause instead of starting cold. |
| `alert_emitted` | Device-alert notifier (`api/app/alert_notifier.py`) | One audit row per device alert pushed to PyPoe's `/alerts/device` webhook: `payload: {event, devices, outcome, target}`. Enabled by `PYPOE_ALERT_URL`. |
| `hostops_action` | **Host-pushed** by `sdl-lab-hostops` instances ([AccelerationConsortium/sdl-lab-hostops](https://github.com/AccelerationConsortium/sdl-lab-hostops), registered in `mcp/servers.yaml`; first instance: `hostops_cytation_pc`, 2026-08-11) | One audit row per **mutating** host-ops tool call — today only `restart_service` (reads are deliberately not audited): `message: "restart_service <svc>: ok\|FAILED"`, `payload.extra: {action, service, ok, source: "lab-hostops"}`. `device_id` is the hostops instance's `equipment_id` — the *host*; the target service rides in `extra.service`. Best-effort: enabled by `HOSTOPS_INGEST_URL` (base URL — the path is appended), and a failed post never blocks the restart. See [`AGENTIC_LAB_DESIGN.md`](AGENTIC_LAB_DESIGN.md). |
| `error` | **Device-pushed** (xArm exporter, from the SDK error/warn callbacks) | `payload.extra: {severity: "error"\|"warning", error_code, warn_code, xarm_state, graph_node}`. |
| `startup` / `shutdown` | **Device-pushed** (xArm exporter, on connect / disconnect) | Marks controller lifecycle, not process lifecycle (that's `service_uptime`). |
| `calibration`, `claim_acquired` | *reserved — not yet emitted* | Kept in the vocabulary for future device exporters. |
| `plate_moved`, `plate_custody_mismatch`, `plate_custody_unknown` | **Run executor** (`api/app/workflow.py::custody_after_step`, since 2026-08-23) for every compiled step carrying `custody: {plate, hid, to}`; **human front door** `POST /api/custody/move` (`api/app/custody.py`) for bench-top moves ([`PLATE_TRACKING.md`](PLATE_TRACKING.md) D5–D8) | **Ops audit only.** `device_id` is the equipment anchoring the destination place (or `custody` for a bench move). `plate_moved`: one row per recorded move — `payload: {step_id, plate, hid, to, recorded, result, observed, verdict, run_id, authorization_id, performed_by, source: executor\|bench}`. `plate_custody_mismatch`: the observed side contradicted the commanded move (a deviation Note is filed with the run too; nothing auto-corrects). `plate_custody_unknown`: the step was sent and never answered, so **no** move was recorded and the last known place stands. Custody itself — where a plate *is* — is read from AnaliticaDB (`Container.location_id` + the `ContainerAction` ledger), never from these rows; there is deliberately no `/api/history/plates` — `GET /api/custody/plates` is a read-through. |

#### Device-pushed events (reference implementation: xArm)

The `xarm-translocation` repo ships the first device-side exporter
(`src/core/events_exporter.py`): a stdlib-only, bounded-queue,
daemon-thread forwarder that POSTs batches to `POST /api/ingest/events`
straight from the xArm SDK's `state_changed` / `error_warn_changed`
callbacks. Conventions any future exporter should copy:

- **Best-effort by contract.** `emit()` never blocks or raises; a full
  queue or unreachable dashboard drops the row. The aggregator's 60 s
  poll remains the coarse backstop, so a dropped row costs timing
  fidelity, never truth.
- **Disabled unless configured** — the xArm reads `XARM_INGEST_URL`
  (full ingest endpoint URL) and `XARM_INGEST_DEVICE_ID` (defaults to
  the `equipment.yaml` id); unset means no-op, so dev machines and CI
  emit nothing.
- **Standard context keys** ride in `extra` on every record:
  `xarm_state` (raw SDK state int), `error_code`, `warn_code`,
  `graph_node` (current motion-graph node or null). The ingest handler
  folds `extra` into the persisted JSON `payload` verbatim, so new keys
  need no `api/` change.

**Next exporter candidate: `plateloc` (open).** Since device v1.4.0 the
sealer reports `activity` natively, but its primary operation — a seal cycle
— lasts 0.5–12 s against a 60 s sweep, so the poll path will almost never
sample a `running` row. It is the fleet's clearest case of "sampling cannot
see this at all" (§2.3.1). Two things follow: today, utilization for the
sealer must be read from the `metrics["cycles_total"]` delta (the device
mirrors the instrument's lifetime odometer, so the counter is exact even
across restarts of the *service*); and an `activity_transition` exporter on
the device — emitting the exact start/stop instants around its blocking
`StartCycle` call — would give the history DB true cycle timings. The
device already knows both timestamps (`activity_since`,
`details.cycle_started_at`); nothing but the POST is missing.

### Why not InfluxDB or TimescaleDB?

Use SQLite until you have > 1 sensor per zone reading at > 1 Hz, or until you need
to query across more data than SQLite handles (it comfortably handles tens of millions
of rows). PostgreSQL/InfluxDB add real operational overhead — backups, vacuuming,
version upgrades — that is not justified at the current scale.
Revisit when the real `sense-every-zone` nodes are live and you want
sub-minute trend plots.

---

## 5. Data flow to the dashboard

Two poll cadences: the aggregator's **live poll** refreshes current device
state every ~2.5 s (`AGGREGATOR_POLL_INTERVAL_S`, default 2.5); a separate
**60 s sweep** writes uptime transitions to SQLite and feeds the alert
notifier (§6b). The web dashboard is Next.js — `:3000` deployed, `:8000` in dev.

```
Device service                Dashboard aggregator (FastAPI :8001)        Dashboard (Next.js)
─────────────────             ────────────────────────────────────        ─────────────────────────
GET /status every ~2.5s ────► Live poll refreshes current state;          GET /api/history/uptime/{id}
                              the 60 s sweep writes service_uptime        ──► uptime % over last 7 days
                              rows on state changes (up→down, down→up)

POST /control/dose.start ───► On 200 response, write well_results row    GET /api/history/runs
                                                                           GET /api/history/runs/{id}/wells
                                                                           ──► plate heatmap (colour = actual_mg)

GET /status from env zone ──► Poll loop writes to sensor_readings once   GET /api/history/sensors/{id}
                              per minute (ignores intermediate values)    ──► temperature/humidity line chart

equipment_events.jsonl ─────► Exporter script POSTs to                   GET /api/history/events/{device_id}
(on Pi 5 device)              POST /api/ingest/events                     ──► event timeline
                              Aggregator writes to equipment_events table
```

### API endpoints

Implemented in `api/app/history.py` (`/api/history/*` reads +
`/api/ingest/*` writes). See §9 for the full endpoint reference —
earlier revisions of this section listed the endpoints as future work;
they shipped.

---

## 6. Uptime tracking — how the aggregator writes it

The aggregator already polls every device via `GET /status`.  The write logic is simple:

```python
# In the poll loop, per device:
prev_state = last_known[device_id]      # "up" or "down" or "unreachable"
if response.ok and prev_state != "up":
    db.execute("INSERT INTO service_uptime(ts, device_id, event) VALUES (?, ?, 'up')", ...)
elif not response.ok and prev_state == "up":
    db.execute("INSERT INTO service_uptime(ts, device_id, event) VALUES (?, ?, 'down')", ...)
```

Uptime percentage for a time window is then a simple query:

```sql
-- Uptime % for dose_every_well over last 7 days
WITH events AS (
    SELECT ts, event,
           LEAD(ts) OVER (ORDER BY ts) AS next_ts
    FROM service_uptime
    WHERE device_id = 'dose_every_well'
      AND ts >= datetime('now', '-7 days')
)
SELECT
    ROUND(
        100.0 * SUM(CASE WHEN event = 'up' THEN
            (julianday(COALESCE(next_ts, datetime('now'))) - julianday(ts)) * 86400
        ELSE 0 END)
        / (7 * 86400), 1
    ) AS uptime_pct
FROM events;
```

### 6b. Alerting — Kuma, the aggregator notifier, and PyPoe

**Status:** live since 2026-07-18. Recording transitions (§6) is passive;
this section is the operator-facing overview + runbook for alerting on
them. PyPoe-side detail (webhook payloads, investigation prompts, Kuma
deployment) lives in PyPoe's `docs/LAB_INTEGRATION.md`.

#### The big picture

Three watchers, one front door, no silent deaths:

```
                       WATCHERS                          FRONT DOOR (PyPoe web, :8006)
┌─────────────────────────────────────────┐
│ Uptime Kuma (docker, host net, :8005)   │──── POST /alerts/kuma ──────┐
│   watches the 7 PLATFORM SERVICES:      │                             │
│   aggregator · dashboard · pypoe web    │                             ▼
│   kasa-tapo · go2rtc · auth · Analitica │                    ┌──────────────────┐
└─────────────────────────────────────────┘                    │ Slack #lab-alerts│
┌─────────────────────────────────────────┐                    │ 🚨 line, then a  │
│ Aggregator alert notifier (api/, 60 s)  │──── POST /alerts/device ──▶ │ threaded reply   │
│   watches all DEVICES via the poll loop │                    │ from `claude -p` │
│   (debounced, cooldown, storm collapse) │                    └──────────────────┘
└─────────────────────────────────────────┘                             ▲
┌─────────────────────────────────────────┐                             │
│ Dashboard (mutual watchdog)             │            read-only investigation via the
│   `uptime_kuma` tile ← PyPoe gateway    │            `pypoe-lab` MCP server (status,
│   `/kuma/status`; PyPoe /status carries │            events, uptime, consult_poe,
│   a required `uptime_kuma` component    │            append_observation — no /control/*)
└─────────────────────────────────────────┘
```

Division of labor (do not blur it):

- **Kuma watches services, not devices.** Uptime Kuma (dashboard host,
  `:8005`) watches the *platform services* (aggregator, dashboard,
  PyPoe, gateways, auth, AnaliticaDB) and alerts through PyPoe's
  `/alerts/kuma`. Device reachability is already the aggregator's job,
  and the aggregator is the single authority on device state — adding
  device monitors to Kuma would create a second, disagreeing source of
  truth.
- **The aggregator watches devices, not services.** Its notifier rides
  the existing 60 s uptime sweep; it cannot see its own death — that is
  exactly what Kuma is for.
- **The dashboard watches Kuma.** Kuma serves no STATUS_SPEC envelope,
  so PyPoe gateway-fronts it (`GET /kuma/status`, same pattern as
  kasa-tapo-services): per-monitor `ComponentStatus` rows from Kuma's
  public status page (slug `lab`), `degraded` when any monitor is down,
  `unknown` per STATUS_SPEC §2.1 when Kuma itself is unreachable.
  Registered as `uptime_kuma` under **Services**.

#### The device-alert notifier

The device path is `api/app/alert_notifier.py`, fed by the same 60 s
loop as §6 (one `observe()` per device per sweep, one `flush()` per
sweep). It POSTs device alerts to PyPoe's `POST /alerts/device` webhook,
which posts to Slack and spawns a read-only Claude investigation.

Rules (all in `alert_notifier.py`, unit-tested in
`api/tests/test_alert_notifier.py`):

- `unreachable` only after **2 consecutive failed sweeps** — a single
  missed poll never alerts. Gateway-fronted kinds (`camera`,
  `smart_plug`, `power_strip`) reporting `unknown` count as unreachable
  per STATUS_SPEC §2.1.
- `error` / `e_stop` alert **immediately** on the state edge, carrying
  the device's `last_error`.
- `recovered` is sent only for devices that previously alerted.
- Devices with `enabled: false`, a `maintenance:` block, or
  `adapter: mock` are suppressed.
- **30-min per-device cooldown**; ≥3 devices tripping in one sweep
  collapse into a single storm alert (shared-cause heuristic).
- Delivery is best-effort (never blocks the poll loop); every emitted
  alert writes an `alert_emitted` audit row (§4 registry), so "did we
  alert, and did delivery succeed" is answerable from the history DB.

Enabled by `PYPOE_ALERT_URL` in the repo-root `.env`
(e.g. `http://100.64.254.6:8006/alerts/device`); unset = off.

#### What an alert looks like

**Service down (Kuma path):** `🚨 *<monitor>* DOWN — <msg> 🔍
Investigating…` in `#lab-alerts`, then a threaded reply from a headless
`claude -p` run that reads the aggregator + history DB through the
read-only `pypoe-lab` MCP server, optionally consults Poe models for a
second opinion, journals `agent_observation` rows, and ends with a
diagnosis + plain-English recovery recommendation. Recovery posts a
single `✅ recovered` line — no investigation.

**Device problem (aggregator path):** same shape, but the event is one of
`unreachable | error | e_stop | recovered` and the investigation prompt
is device-focused (`get_equipment_status("<id>")`, `recent_events`,
`device_uptime`, with the device's `last_error` inline).

#### Where everything lives

| Piece | Where | Config |
|---|---|---|
| Uptime Kuma | docker `uptime-kuma`, **host networking**, UI on `:8005` | admin creds: `~/.pypoe/uptime-kuma-admin.credentials` on the dashboard host |
| Kuma status page | `http://<host>:8005/status/lab` (public) | feeds the `/kuma/status` gateway |
| Service webhook | PyPoe web `POST /alerts/kuma` | Kuma notification `pypoe-alert-bot` (default, applied to all monitors) |
| Device webhook | PyPoe web `POST /alerts/device` | `PYPOE_ALERT_URL` in this repo's `.env` |
| Alert notifier | `api/app/alert_notifier.py` | rules/env above |
| Investigation | `claude -p` spawned by PyPoe | needs `pypoe-lab` MCP registered + `--allowedTools` (see PyPoe `docs/LAB_INTEGRATION.md`) |
| Slack channel | `#lab-alerts` | PyPoe `slack.yaml` → `slack.alert_channel` |
| Kuma dashboard tile | `uptime_kuma` in `equipment.yaml` → PyPoe `/kuma/status` | `PYPOE_KUMA_URL` / `PYPOE_KUMA_STATUS_SLUG` in PyPoe's `.env` |

#### Runbook

**Add a service monitor.** Kuma UI on `:8005` (creds above) → Add
Monitor → HTTP, 60 s interval, retries 2 → attach the `pypoe-alert-bot`
notification → add the monitor to the `lab` status page so the dashboard
tile sees it. Gotchas: Kuma runs with host networking *on purpose* — the
aggregator binds loopback and PyPoe binds the Tailscale IP, so a
bridge-network container reaches neither. And Kuma's HTTP checks send
browser-style `Accept` headers: an auth-gated endpoint that 302s to a
login page will flap (302 → `/` → 404) — probe a plain endpoint instead
(e.g. the auth sidecar is monitored on `/status`, not `/auth/verify`).

**Take a device out of alerting** (planned maintenance): set the
`maintenance:` block (or `enabled: false`) on its `equipment.yaml` entry
and restart the api service — the notifier suppresses it, same as every
other workflow surface ([`EQUIP_GUIDE.md`](EQUIP_GUIDE.md) §3).

**Tune the notifier**: thresholds are constructor defaults in
`api/app/alert_notifier.py` (`sustained_sweeps=2`, `cooldown_s=1800`,
`storm_threshold=3`). Turn the whole thing off by removing
`PYPOE_ALERT_URL` from `.env`.

**Test the pipeline** without touching hardware: add a temporary Kuma
monitor pointed at a dead port (name it `TEST … (ignore)`), watch the
Slack thread appear, then delete the monitor. For the device path, POST
a synthetic event to `/alerts/device` with `event: "recovered"` — it
posts one ✅ line and runs no investigation.

#### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No Slack post at all | Kuma notification detached, or PyPoe web down (check the `pypoe_web` tile) | Kuma UI → notification `pypoe-alert-bot` set as default; `systemctl status pypoe-web` |
| Alert line appears, thread reply says "tools unavailable" | `pypoe-lab` MCP not registered for the `claude` CLI, or `--allowedTools` missing | PyPoe `docs/LAB_INTEGRATION.md` → *Headless permissions* |
| Thread reply is generic / no lab data | `LAB_API_URL` unset or pointing at a dead address | must be the aggregator, `http://127.0.0.1:8001` on the dashboard host |
| `uptime_kuma` tile "unreachable" | Kuma container down | `docker start uptime-kuma`; the PyPoe tile also shows a failed `uptime_kuma` component |
| A Kuma monitor flaps down with `404` while `curl` works | login-redirect endpoint + browser `Accept` headers | monitor a plain `/status`-style endpoint |
| Device alert never fired for a real outage | shorter than 2 sweeps, inside the 30-min cooldown, or device in maintenance | check `equipment_events` for `alert_emitted` rows |

#### See also

- PyPoe `docs/LAB_INTEGRATION.md` — webhook payloads, investigation
  prompts, MCP registration, Kuma deployment notes.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — where the pieces sit in the
  platform layering.
- [`STATUS_SPEC.md`](STATUS_SPEC.md) §2.1 — the `unknown` /
  "unreachable" semantics the gateway tile follows.

---

## 7. Environmental monitoring write rate

The sensor zones exist in `equipment.yaml` (`env_lab499_west`,
`env_lab499_east`, … — currently `adapter: mock`, with synthetic readings
generated by the aggregator in `api/app/main.py`). When the real
`sense-every-zone` services replace the mocks, the write rules stay the same:

- **Do not** write every 2-second poll reading to SQLite. That is 43,200 rows/day/metric.
- **Write one row per minute** (or on threshold change > 0.5 °C / 2% RH).
- Keep the raw readings in a fixed-size in-memory ring buffer in the aggregator for the
  live dashboard tile; the database holds the downsampled history for plots.

```python
# Aggregator pseudo-code for sensor write rate limiting
if (now - last_sensor_write[sensor_id]) > timedelta(minutes=1):
    db.execute("INSERT INTO sensor_readings ...", ...)
    last_sensor_write[sensor_id] = now
```

---

## 8. Quick reference — "where does X go?"

| Information | Where | Written by |
|---|---|---|
| Service crash + restart | Journald | systemd |
| Python WARNING / ERROR during a run | Journald | uvicorn |
| Dispense result for one well | `events.jsonl` on Pi 5, and `well_results` in SQLite | Device exporter → aggregator |
| Balance reading during a dispense | RAM only (not persisted) | ClosedLoopDoser |
| Plate run summary | `runs` table in SQLite | Aggregator |
| Sensor reading (current, live tile) | Aggregator in-memory ring buffer | Poll loop |
| Sensor reading (history plot) | `sensor_readings` SQLite (1/min) | Aggregator poll loop |
| Device uptime | `service_uptime` SQLite | Aggregator poll loop (on state change) |
| State transition / error | `equipment_events` SQLite | Aggregator (via ingest endpoint) |
| Operator control action (who clicked what) | `equipment_events` SQLite (`event_type: control_action`) | Dashboard control passthrough (`api/app/control.py`) |
| Camera snapshots | `/var/lib/kasa-tapo-media/` on dashboard host | kasa-tapo-services |

---

## 9. History API endpoint reference

All endpoints are on the dashboard host at `:8001`. The Next.js UI calls them
via Next's built-in proxy (`/api/...` → `http://127.0.0.1:8001/api/...`).

### Read endpoints (dashboard → browser)

| Method + Path | Query params | Returns | Used by |
|---|---|---|---|
| `GET /api/history/uptime` | `days=7` | `{devices: {id: {uptime_pct, last_event, state_pcts, activity_pcts, activity_tracking_since, days}}}` | History / Uptime section (health bar + v1.2 utilization bar) |
| `GET /api/history/uptime/{device_id}` | `days=7` | `{uptime_pct, events: [{ts, event, ...}]}` | Per-device drill-down |
| `GET /api/history/events/{device_id}` | `limit=50`, `event_type=` | `{events: [{ts, event_type, from_state, to_state, message}]}` | Event timeline; `event_type` narrows to one kind (e.g. `agent_observation`) |
| `GET /api/history/sensors/latest` | — | `{readings: [{sensor_id, metric, value, unit, ts}]}` | Live sensor tile |
| `GET /api/history/sensors/{sensor_id}/{metric}` | `since_hours=1`, `limit=500` | `{readings: [{ts, value, unit}]}` | Sensor line chart |
| `GET /api/history/runs` | `limit=20`, `device_id=` | `{runs: [RunRecord]}` | Run history table |
| `GET /api/history/runs/{run_id}/wells` | — | `{wells: [WellResult]}` | 96-well heatmap |

### Write endpoints (device services → aggregator)

| Method + Path | Body | Description |
|---|---|---|
| `POST /api/ingest/events` | `{device_id, records: [{timestamp, event, ...}]}` | Batch-upload `events.jsonl` records from a Pi service |
| `POST /api/ingest/runs` | `RunRecord` | Create / update a dosing run |
| `POST /api/ingest/wells` | `[WellResultRecord]` | Append per-well results to a run |

---

## 10. The lab assistant as a read consumer (MCP)

The dashboard ships a lab assistant (the chat bubble) whose main job is to
*consume* the observability data described above and turn it into operator
answers ("has the plateloc errored today?", "when did the gateway last go
down?"). It cannot actuate hardware by construction and is the main
non-human reader of `lab.db` and the dashboard's journald units. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10 for the design rationale
(including the two selectable chat engines); this section documents what it
reads and the limits it reads under.

The tools live in `api/app/mcp_server.py` (the `lab-history` MCP server, stdio
transport, `lab-history-mcp` entry point) and are surfaced to the assistant —
and to any developer who runs `claude mcp add lab-history` — as eight
read-only tools plus one append-only journal write:

| MCP tool | Reads from | Maps to |
|---|---|---|
| `list_equipment_now` | live aggregator (`GET /api/equipment`) | current device state, `fetch_error`, `latency_ms` |
| `get_equipment_status` | live aggregator (one device) | the full envelope: components, details, metrics, `allowed_actions`, activity |
| `query_equipment_events` | `equipment_events` table | §4 state transitions / errors / startup / shutdown |
| `query_service_uptime` | `service_uptime` table | §6 uptime % + reachability transitions |
| `query_sensor_readings` | `sensor_readings` table | §7 downsampled env history |
| `query_runs` | `runs` table | dosing-run records |
| `query_well_results` | `well_results` table | per-well dispense results |
| `tail_journald` | `journalctl -u <unit>` | §2 host journald (whitelisted units only) |
| `record_observation` (write) | → `POST /api/ingest/events` | one actor-stamped `agent_observation` row — the shared, audited agent journal; fails closed without a verified operator |

Guardrails (all enforced in `mcp_server.py`, mirroring the API bubble):

- **Read-only.** No tool writes the DB or actuates hardware. The history DB's
  single-writer invariant (§4, decision #9) is unaffected — the assistant is
  purely a reader.
- **Journald unit whitelist.** `tail_journald` accepts only
  `ac-organic-lab-api.service`, `ac-organic-lab-web.service`,
  `kasa-tapo-services.service`, and `ac-go2rtc.service`, so the tool cannot
  become a side channel into the host's full journal. Capped at
  `MAX_JOURNAL_LINES = 200` lines and an 8 s `journalctl` timeout.
- **Query caps.** `MAX_LIMIT = 200` rows, `MAX_SINCE_HOURS = 24 × 7` lookback,
  clamped server-side regardless of what the model requests.

What the assistant *does* leave behind, and where: an Ask-mode chat turn
produces no `lab.db` row, but every turn (both modes) writes two journald
lines in the `ac-organic-lab-api` unit — `assistant chat:` (the verified
actor, mode, backend) and `assistant turn done:` (elapsed, tool rounds,
tokens, rate-limit status, backend, model) — so latency and account burn are
greppable per turn. Three assistant paths do write `lab.db`, all through the
normal audited routes: a Control-mode proposal is recorded as an
`assistant_proposal` event the moment the model emits it; the operator's
*Authorize* click lands as the usual `control_action` row stamped
`origin: assistant`; and a `record_observation` call appends an
`agent_observation` journal row. The single-writer invariant (§4, decision
#9) holds for all three — they arrive via the API's own ingest/audit paths,
never a second DB connection.

---

## 11. Direct database access

The database lives at `/opt/ac-organic-lab/data/lab.db` on the deployed
dashboard host. Without `LAB_DB_PATH` the code defaults to repo-root
`data/lab.db` (`api/app/db.py`); override the path with `LAB_DB_PATH` in `.env`.

### SQLite CLI inspection

```bash
# Open the database (read-only is safer while the server is running)
sqlite3 /opt/ac-organic-lab/data/lab.db

-- Check table sizes
SELECT name, COUNT(*) FROM sqlite_master
JOIN (
    SELECT 'service_uptime' tbl, COUNT(*) n FROM service_uptime
    UNION ALL SELECT 'equipment_events', COUNT(*) FROM equipment_events
    UNION ALL SELECT 'sensor_readings',  COUNT(*) FROM sensor_readings
    UNION ALL SELECT 'runs',             COUNT(*) FROM runs
    UNION ALL SELECT 'well_results',     COUNT(*) FROM well_results
) ON name = tbl;

-- Uptime last 24 h
SELECT device_id, event, ts FROM service_uptime
WHERE ts >= datetime('now', '-1 day') ORDER BY ts DESC;

-- All failed dispenses across all runs
SELECT r.plate_id, w.well, w.target_mg, w.actual_mg, w.ts
FROM well_results w
JOIN runs r ON w.run_id = r.id
WHERE w.converged = 0
ORDER BY w.ts DESC LIMIT 50;

-- Latest sensor reading per zone
SELECT sensor_id, metric, value, unit, ts FROM sensor_readings
WHERE id IN (SELECT MAX(id) FROM sensor_readings GROUP BY sensor_id, metric)
ORDER BY sensor_id, metric;
```

### Python one-liner (from the dashboard host)

```python
import sqlite3, json
db = sqlite3.connect("/opt/ac-organic-lab/data/lab.db")
db.row_factory = sqlite3.Row
runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 10")]
print(json.dumps(runs, indent=2))
```

---

## 12. Backup and retention

```bash
# Safe hot backup while server is running (WAL mode makes this safe)
sqlite3 /opt/ac-organic-lab/data/lab.db \
    ".backup /opt/ac-organic-lab/data/lab.db.bak"

# Cron example: daily backup to a dated file, keep 30 days
# Add to /etc/cron.d/lab-db-backup:
# 0 3 * * * ac sqlite3 /opt/ac-organic-lab/data/lab.db \
#     ".backup /opt/ac-organic-lab/data/backups/lab_$(date +\%F).db" && \
#     find /opt/ac-organic-lab/data/backups/ -name "lab_*.db" -mtime +30 -delete
```

Retention guidelines:

| Table | Rows/day (typical) | 1-year size estimate | Trim when |
|---|---|---|---|
| `service_uptime` | ~5 per device × 10 devices = 50 | ~18 K rows | Never (tiny) |
| `equipment_events` | ~20 total | ~7 K rows | Never (tiny) |
| `sensor_readings` | 1/min × 4 zones × 3 metrics = 17 280 | ~6 M rows | After 2 years (still fast) |
| `well_results` | ~96 per run × ~2 runs/day = 192 | ~70 K rows | Never for a single-lab run |
| `runs` | ~2 | ~730 rows | Never |

SQLite handles tens of millions of rows without tuning. At the current
lab throughput `lab.db` will stay under 500 MB for several years.
Revisit when `sensor_readings` exceeds 50 M rows (~8 years at current rate).

