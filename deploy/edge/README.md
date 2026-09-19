# The lab edge — `dashboard-edge` on sdl2-server-agents (100.64.254.6)

This directory is the **versioned source of truth** for the Caddy edge that
fronts every lab UI. Until 2026-09-19 the live file existed only on the host
(`/etc/dashboard-staging/Caddyfile`), with `Caddyfile.bak.<ts>` copies as its
whole history; the hermes move, the device-route move, the HTTPS origin and a
directive-order fix all went straight into it. Edits now go through this
directory and a PR, then get installed — never the other way round.

`deploy/Caddyfile.single-edge` is the *historical* Phase-1 edge as it ran on
gaia; keep it for the design commentary, don't deploy it.

## What runs where

| file here | installed at | owner / mode |
|---|---|---|
| `Caddyfile` | `/etc/dashboard-staging/Caddyfile` | root 0644 |
| `dashboard-edge.service` | `/etc/systemd/system/dashboard-edge.service` | root 0644 |
| `dashboard-edge.service.d/10-reload.conf` | same path under `/etc/systemd/system/` | root 0644 |
| `dashboard-edge.service.d/20-device-edge-secrets.conf` | same | root 0644 |
| `caddy-tailscale-cert` | `/usr/local/libexec/caddy-tailscale-cert` | root 0755 |
| `caddy-tailscale-cert.{service,timer}` | `/etc/systemd/system/` | root 0644 |

The binary is `/usr/local/lib/dashboard-staging/caddy` — **stock Caddy 2.11.4**
(since 2026-09-18; `caddy.2.6.2-stock.bak` alongside is a stock 2.6.2, not the
original custom build, which is gone). The unit runs as `sdl2` with
`CAP_NET_BIND_SERVICE`, `admin off`, `auto_https off`.

Secrets are **not** in any file here. The Caddyfile references
`{env.XARM_EDGE_SHARED_SECRET}`, `{env.MG400_EDGE_SHARED_SECRET}`,
`{env.OT2_EDGE_SECRET}`, `{env.BITACORADB_EDGE_SECRET}`,
`{env.BAMBU_EDGE_SHARED_SECRET}`; the unit loads them from root-0600
`EnvironmentFile`s (`/etc/device-gateway-staging/edge.env`,
`/etc/bitacoradb-staging/edge.env`, `/etc/dashboard-staging/device-edge-secrets.env`).
`systemctl show -p Environment` prints paths, never values.
`MG400_EDGE_SHARED_SECRET` has never been provisioned on any host; the Dobot
route sends an empty header, as it always has.

## Shape of the Caddyfile

- Global: `admin off`, `auto_https off` (the ts.net cert is not Caddy-managed).
- `http://:8080` (loopback) — the **router**: one explicit `route { … }` that
  strips inbound `X-Auth-*`, then per-UI blocks. **Written order is execution
  order inside it** — header strips, then `redir`, then `forward_auth`, then
  `handle_path`. A `redir` written after `forward_auth` never fires for a
  logged-out caller (see `AGENTS.md` pitfalls).
- `http://100.64.254.6` and `https://sdl2-server-agents.tail6a1dd7.ts.net`
  both `reverse_proxy 127.0.0.1:8080`. Different origins ⇒ separate
  `ac_auth_session` cookies.
- `@retained_gaia_apps` (`/analytica*`, `/agente*`) → `100.64.254.5:80`. Those
  two apps still run on gaia; everything else terminates here.

## Change procedure

```
# 1. edit deploy/edge/Caddyfile, open a PR, merge
# 2. on sdl2-server-agents:
sudo install -m 644 -o root -g root /home/sdl2/caoyang/ac-organic-lab/deploy/edge/Caddyfile /etc/dashboard-staging/Caddyfile
sudo /usr/local/lib/dashboard-staging/caddy validate --config /etc/dashboard-staging/Caddyfile --adapter caddyfile
sudo systemctl reload dashboard-edge      # SIGUSR1: same PID, no dropped connections
# unit / drop-in changes instead need: install + systemctl daemon-reload + systemctl restart dashboard-edge
```

Validate before reload, always; a bad reload is refused by Caddy and the old
config keeps serving, but a bad *restart* takes the edge down.

## Drift check

```
diff <(ssh 100.64.254.6 cat /etc/dashboard-staging/Caddyfile) deploy/edge/Caddyfile && echo "edge matches repo"
```

If they differ, the host was edited by hand: bring the change *into* the repo
first (that is what happened for everything before 2026-09-19), then install.

## TLS

`caddy-tailscale-cert` issues/renews the Let's Encrypt cert for
`sdl2-server-agents.tail6a1dd7.ts.net` via `tailscale cert` (key `root:sdl2
0640`, daily timer, reloads the edge only when the cert changed). The tailnet
must advertise the domain in `tailscale status --json` → `CertDomains`.

A friendly public name (`sdl2.accelerationconsortium.ai`, AUTH_DESIGN Phase 3)
is **not** set up: the DNS record does not exist, and its cert would need a
DNS-01 path (GoDaddy zone API + an `xcaddy` build or external ACME client).
