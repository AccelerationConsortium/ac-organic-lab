# Lab Observability — Logging, Events, and History

Platform-specific guidelines, storage schema, and dashboard integration notes for the
AC Organic Self-driving Lab. Read alongside [`STATUS_SPEC.md`](STATUS_SPEC.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

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

Lives at `/opt/ac-organic-lab/data/lab.db` on the dashboard host.
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
    event_type  TEXT    NOT NULL,           -- "state_transition" | "error" | "startup" |
                                            -- "shutdown" | "calibration" | "claim_acquired" |
                                            -- "control_action" (dashboard operator write —
                                            --   payload: {action, method, status_code,
                                            --   outcome, owner}; written by api/app/control.py)
    from_state  TEXT,                       -- for state_transition
    to_state    TEXT,                       -- for state_transition
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
    sensor_id   TEXT    NOT NULL,   -- "zone_fume_hood" | "zone_balance" | ...
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
| `control_action` | Dashboard control passthrough (`api/app/control.py`) | One audit row per operator write: `payload: {action, method, status_code, outcome, owner}`. |
| `agent_observation` | PyPoe read-only journaling | Free-form agent notes via `/api/ingest/events`. Read back with `GET /api/history/events/{device_id}?event_type=agent_observation` (PyPoe's `recent_observations` tool, and the assistant's `query_equipment_events(event_type=…)`) so an investigation recognises a recurrence and builds on the prior root cause instead of starting cold. |
| `alert_emitted` | Device-alert notifier (`api/app/alert_notifier.py`) | One audit row per device alert pushed to PyPoe's `/alerts/device` webhook: `payload: {event, devices, outcome, target}`. Enabled by `PYPOE_ALERT_URL`. |
| `error` | **Device-pushed** (xArm exporter, from the SDK error/warn callbacks) | `payload.extra: {severity: "error"\|"warning", error_code, warn_code, xarm_state, graph_node}`. |
| `startup` / `shutdown` | **Device-pushed** (xArm exporter, on connect / disconnect) | Marks controller lifecycle, not process lifecycle (that's `service_uptime`). |
| `calibration`, `claim_acquired` | *reserved — not yet emitted* | Kept in the vocabulary for future device exporters. |

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

### Why not InfluxDB or TimescaleDB?

Use SQLite until you have > 1 sensor per zone reading at > 1 Hz, or until you need
to query across more data than SQLite handles (it comfortably handles tens of millions
of rows). PostgreSQL/InfluxDB add real operational overhead — backups, vacuuming,
version upgrades — that is not justified at the current scale.
Revisit when `env_sensors` is live and you want sub-minute trend plots.

---

## 5. Data flow to the dashboard

```
Device service                Dashboard aggregator (FastAPI :8001)        Dashboard (Next.js :8000)
─────────────────             ────────────────────────────────────        ─────────────────────────
GET /status every 2s  ──────► Poll loop writes to service_uptime          GET /api/history/uptime/{id}
                              when state changes (up→down, down→up)       ──► uptime % over last 7 days

POST /control/dose.start ───► On 200 response, write well_results row    GET /api/history/runs
                                                                           GET /api/history/runs/{id}/wells
                                                                           ──► plate heatmap (colour = actual_mg)

GET /status from env_sensor ► Poll loop writes to sensor_readings once   GET /api/history/sensors/{id}
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

### 6b. Device alerting — the alert notifier

Recording transitions is passive; **alerting** on them is the job of
`api/app/alert_notifier.py`, fed by the same 60 s loop (one `observe()`
per device per sweep, one `flush()` per sweep). It POSTs device alerts
to PyPoe's `POST /alerts/device` webhook, which posts to Slack and
spawns a read-only Claude investigation (see PyPoe's
`docs/LAB_INTEGRATION.md`).

Division of labor: **Uptime Kuma** (dashboard host, `:8005`) watches
the *platform services* (aggregator, dashboard, PyPoe, gateways, auth,
AnaliticaDB) and alerts through PyPoe's `/alerts/kuma`; the **aggregator
notifier** watches the *devices* and alerts through `/alerts/device`.
The aggregator stays the single authority on device state — do not add
device monitors to Kuma. Kuma itself is visible on the dashboard as the
`uptime_kuma` tile, gateway-fronted by PyPoe (`/kuma/status`).

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
  alert writes an `alert_emitted` audit row (§4 registry).

Enabled by `PYPOE_ALERT_URL` in the repo-root `.env`
(e.g. `http://100.64.254.6:8006/alerts/device`); unset = off.

---

## 7. Environmental monitoring write rate

The `env_sensors` module does not exist yet, but when it does:

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
| `GET /api/history/uptime` | `days=7` | `{devices: {id: {uptime_pct, last_event, days}}}` | History / Uptime section |
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

The dashboard ships a read-only Claude assistant (the chat bubble) whose
entire job is to *consume* the observability data described above and turn it
into operator answers ("has the plateloc errored today?", "when did the
gateway last go down?"). It is read-only by construction and is the main
non-human reader of `lab.db` and the dashboard's journald units. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) decision #10 for the design rationale;
this section documents what it reads and the limits it reads under.

The tools live in `api/app/mcp_server.py` (the `lab-history` MCP server, stdio
transport, `lab-history-mcp` entry point) and are surfaced to the assistant —
and to any developer who runs `claude mcp add lab-history` — as seven
read-only tools:

| MCP tool | Reads from | Maps to |
|---|---|---|
| `list_equipment_now` | live aggregator (`GET /api/equipment`) | current device state, `fetch_error`, `latency_ms` |
| `query_equipment_events` | `equipment_events` table | §4 state transitions / errors / startup / shutdown |
| `query_service_uptime` | `service_uptime` table | §6 uptime % + reachability transitions |
| `query_sensor_readings` | `sensor_readings` table | §7 downsampled env history |
| `query_runs` | `runs` table | dosing-run records |
| `query_well_results` | `well_results` table | per-well dispense results |
| `tail_journald` | `journalctl -u <unit>` | §2 host journald (whitelisted units only) |

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

Nothing the assistant does is itself logged to `lab.db` — unlike operator
*control* writes (§8, `event_type: control_action`), a read-only chat turn
produces no audit row. The subprocess's own stdout/stderr land in the
`ac-organic-lab-api` journald unit (it runs inside that FastAPI process).

---

## 11. Direct database access

The database lives at `/opt/ac-organic-lab/data/lab.db` on the dashboard host.
Override the path with `LAB_DB_PATH` in `.env`.

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

