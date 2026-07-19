# Lab Alerting — how Kuma, the aggregator, and PyPoe work together

**Status:** live since 2026-07-18. This is the operator-facing overview +
runbook for the lab's alerting stack. Component-level detail lives in
[`OBSERVABILITY.md`](OBSERVABILITY.md) §6b (the aggregator's alert
notifier) and PyPoe's `docs/LAB_INTEGRATION.md` (webhooks, investigation,
Kuma deployment).

## The big picture

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

- **Kuma watches services, not devices.** Device reachability is already
  the aggregator's job, and the aggregator is the single authority on
  device state — adding device monitors to Kuma would create a second,
  disagreeing source of truth.
- **The aggregator watches devices, not services.** Its notifier rides
  the existing 60 s uptime sweep; it cannot see its own death — that is
  exactly what Kuma is for.
- **The dashboard watches Kuma.** Kuma serves no STATUS_SPEC envelope,
  so PyPoe gateway-fronts it (`GET /kuma/status`, same pattern as
  kasa-tapo-services): per-monitor `ComponentStatus` rows from Kuma's
  public status page (slug `lab`), `degraded` when any monitor is down,
  `unknown` per STATUS_SPEC §2.1 when Kuma itself is unreachable.
  Registered as `uptime_kuma` under **Services**.

## What an alert looks like

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
`device_uptime`, with the device's `last_error` inline). Debounce rules
(OBSERVABILITY §6b): unreachable needs 2 consecutive failed sweeps;
error/e_stop fire immediately; 30-min per-device cooldown; ≥3 devices in
one sweep collapse into a single "probable shared cause" alert.
Maintenance/disabled/mock devices never alert.

Every emitted device alert also writes an `alert_emitted` audit row to
`equipment_events`, so "did we alert, and did delivery succeed" is
answerable from the history DB.

## Where everything lives

| Piece | Where | Config |
|---|---|---|
| Uptime Kuma | docker `uptime-kuma`, **host networking**, UI on `:8005` | admin creds: `~/.pypoe/uptime-kuma-admin.credentials` on the dashboard host |
| Kuma status page | `http://<host>:8005/status/lab` (public) | feeds the `/kuma/status` gateway |
| Service webhook | PyPoe web `POST /alerts/kuma` | Kuma notification `pypoe-alert-bot` (default, applied to all monitors) |
| Device webhook | PyPoe web `POST /alerts/device` | `PYPOE_ALERT_URL` in this repo's `.env` |
| Alert notifier | `api/app/alert_notifier.py` | rules/env in OBSERVABILITY §6b |
| Investigation | `claude -p` spawned by PyPoe | needs `pypoe-lab` MCP registered + `--allowedTools` (see PyPoe `docs/LAB_INTEGRATION.md`) |
| Slack channel | `#lab-alerts` | PyPoe `slack.yaml` → `slack.alert_channel` |
| Kuma dashboard tile | `uptime_kuma` in `equipment.yaml` → PyPoe `/kuma/status` | `PYPOE_KUMA_URL` / `PYPOE_KUMA_STATUS_SLUG` in PyPoe's `.env` |

## Runbook

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

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No Slack post at all | Kuma notification detached, or PyPoe web down (check the `pypoe_web` tile) | Kuma UI → notification `pypoe-alert-bot` set as default; `systemctl status pypoe-web` |
| Alert line appears, thread reply says "tools unavailable" | `pypoe-lab` MCP not registered for the `claude` CLI, or `--allowedTools` missing | PyPoe `docs/LAB_INTEGRATION.md` → *Headless permissions* |
| Thread reply is generic / no lab data | `LAB_API_URL` unset or pointing at a dead address | must be the aggregator, `http://127.0.0.1:8001` on the dashboard host |
| `uptime_kuma` tile "unreachable" | Kuma container down | `docker start uptime-kuma`; the PyPoe tile also shows a failed `uptime_kuma` component |
| A Kuma monitor flaps down with `404` while `curl` works | login-redirect endpoint + browser `Accept` headers | monitor a plain `/status`-style endpoint |
| Device alert never fired for a real outage | shorter than 2 sweeps, inside the 30-min cooldown, or device in maintenance | check `equipment_events` for `alert_emitted` rows |

## See also

- [`OBSERVABILITY.md`](OBSERVABILITY.md) — §6b notifier rules, §4
  `alert_emitted` / `agent_observation` event registry.
- PyPoe `docs/LAB_INTEGRATION.md` — webhook payloads, investigation
  prompts, MCP registration, Kuma deployment notes.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — where the pieces sit in the
  platform layering.
- [`STATUS_SPEC.md`](STATUS_SPEC.md) §2.1 — the `unknown` /
  "unreachable" semantics the gateway tile follows.
