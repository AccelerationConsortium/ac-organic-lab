# Deployment (Linux + systemd)

The dashboard runs on a single Linux server attached to the lab's Tailnet.
Two systemd services, no app-level auth - access is gated by Tailscale ACLs.

> The macOS-specific LaunchDaemon (`deploy/limit.maxfiles.plist`) and the
> `WATCHPACK_POLLING=true` dev hack in `web/package.json` are **only** for
> local development on macOS. Linux production uses the Next.js
> **standalone build** (no file watcher, no FD limit issue) and systemd's
> `LimitNOFILE=65536` in each unit. You can ignore them for server deploy.

## Layout on the server

```
/opt/ac-organic-lab/
├── api/                              # checked-out source + venv
│   └── .venv/                        # python deps
├── web/
│   └── .next/
│       └── standalone/                # production server + static assets
│           ├── server.js
│           ├── .next/static/          # copied from web/.next/static
│           └── public/                # copied from web/public (if any)
├── equipment.yaml                    # registry (committed)
├── docs/STATUS_SPEC.md
└── .env                              # gitignored; optional for v1
```

## One-time server setup

```bash
# 1. Create a non-privileged service user
sudo useradd --system --create-home --home /opt/ac-organic-lab \
    --shell /usr/sbin/nologin ac

# 2. Clone the repo
sudo -u ac git clone <repo-url> /opt/ac-organic-lab

# 3. Install the API
cd /opt/ac-organic-lab/api
sudo -u ac python3 -m venv .venv
sudo -u ac .venv/bin/pip install -e .

# 4. Build the web app
cd /opt/ac-organic-lab/web
sudo -u ac npm ci
sudo -u ac npm run build

# 5. Copy static assets into the standalone bundle.
#    Next.js standalone expects static/ and public/ next to server.js.
sudo -u ac cp -r .next/static .next/standalone/.next/static
sudo -u ac cp -r public        .next/standalone/public 2>/dev/null || true

# 6. Install systemd units
sudo cp deploy/ac-organic-lab-api.service /etc/systemd/system/
sudo cp deploy/ac-organic-lab-web.service /etc/systemd/system/
sudo systemctl daemon-reload

# 7. Enable at boot and start now
sudo systemctl enable --now ac-organic-lab-api ac-organic-lab-web

# 8. Verify
sudo systemctl status ac-organic-lab-api ac-organic-lab-web
curl -s http://127.0.0.1:8001/api/health
curl -s http://sdl2-server-gaia.tail6a1dd7.ts.net:8000/
```

## Day-to-day operations

```bash
# Live logs
journalctl -fu ac-organic-lab-api
journalctl -fu ac-organic-lab-web

# Filter by service name (uses SyslogIdentifier)
journalctl -t ac-organic-lab-api -n 200
journalctl -t ac-organic-lab-web -n 200

# Restart after editing equipment.yaml
sudo -u ac git -C /opt/ac-organic-lab pull
sudo systemctl restart ac-organic-lab-api
# web does NOT need a restart; it only proxies to the API.

# Full redeploy (code change in web/)
cd /opt/ac-organic-lab
sudo -u ac git pull
cd web
sudo -u ac npm ci
sudo -u ac npm run build
sudo -u ac cp -r .next/static .next/standalone/.next/static
sudo -u ac cp -r public        .next/standalone/public
sudo systemctl restart ac-organic-lab-web

# Full redeploy (code change in api/)
cd /opt/ac-organic-lab
sudo -u ac git pull
cd api
sudo -u ac .venv/bin/pip install -e .
sudo systemctl restart ac-organic-lab-api
```

## Exposing the dashboard on the Tailnet

Two options:

### Option A: Caddy in front (recommended)

Caddy terminates TLS via Tailscale's `tailscale cert` and keeps the
dashboard processes loopback-only.

```bash
sudo apt install caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the hostname first
sudo systemctl reload caddy
```

The default web unit binds to `100.64.254.6:8000` - leave it that way when
using Caddy.

### Option B: Bind web directly to the Tailnet

Edit `ac-organic-lab-web.service` and change:

```
Environment=HOSTNAME=127.0.0.1
```

to one of:

```
Environment=HOSTNAME=0.0.0.0             # all interfaces, including tailscale0
Environment=HOSTNAME=100.64.254.xxx      # only the server's tailnet IP
```

Then `sudo systemctl daemon-reload && sudo systemctl restart ac-organic-lab-web`.
No TLS in this case - fine for lab use over the encrypted Tailscale network,
but browsers may show "Not Secure".

## Sandboxing the services

The units include systemd hardening directives:
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
`PrivateDevices`, and friends. These are safe for both services and buy
free defense-in-depth. `ReadWritePaths` is limited to each service's own
directory inside `/opt/ac-organic-lab/`.

## Service dependencies

- **API** waits for `network-online.target` and `tailscaled.service` so
  the aggregator's first poll doesn't fail while the tailnet is still
  coming up.
- **Web** requires the API (`Requires=ac-organic-lab-api.service`). If the
  API stops, the web service stops too. Both are set to
  `Restart=on-failure` with a 3s backoff.

## Optional: cameras + smart plugs (`kasa-tapo-services`)

Cameras (Tapo C200/C210/C220/C245D) and Kasa smart plugs (HS103,
HS300) live on the lab LAN and don't speak HTTP natively, so a
companion gateway translates them. Deploy the
[`kasa-tapo-services`](https://github.com/cyrilcaoyang/kasa_tapo_services)
package onto the **same** dashboard host:

* `kasa-tapo-services.service` (gateway, port `8002`) - exposes a
  STATUS_SPEC v1.0 surface plus `/control/{ptz,preset/*,privacy,
  streaming,snapshot,recording/*}` for cameras.
* `ac-go2rtc.service` (port `1984`) - converts each camera's RTSP feed
  to MSE / WebSockets so the browser can play it without a plugin.

Once both services are running, register each device in this repo's
`equipment.yaml` with `adapter: http`, `base_url: http://127.0.0.1:8002`
and `status_path: /cameras/<id>/status` (or `/plugs/<id>/status`). The
camera tile, PTZ pad, preset selector, and snapshot/record buttons
appear automatically inside whichever platform's panel the camera is
listed under (and on `/platforms/<platform>`).

The dashboard's `api/app/control.py` proxies the browser's calls
through to the gateway:

```
POST /api/equipment/<id>/control/snapshot           ->  POST :8002/cameras/<id>/control/snapshot
POST /api/equipment/<id>/control/recording/start    ->  POST :8002/cameras/<id>/control/recording/start
GET  /api/equipment/<id>/media                      ->  GET  :8002/cameras/<id>/media
GET  /api/equipment/<id>/media/{rest}               ->  streamed GET  :8002/cameras/<id>/media/{rest}
```

Caddy already covers this via the existing `reverse_proxy 100.64.254.6:8000`
block (everything `/api/*` flows through the API). The optional
`/streams/*` block in [`Caddyfile`](Caddyfile) is what gives the browser
WebSocket access to go2rtc.

For storage paths (snapshots and recordings on disk), `ffmpeg` install,
and the systemd unit details, see
[`kasa_tapo_services/deploy/README.md`](https://github.com/cyrilcaoyang/kasa_tapo_services/blob/main/deploy/README.md).

## Healthchecks / monitoring

The aggregator exposes:

```
GET http://127.0.0.1:8001/api/health   -> {"status":"healthy",...}
```

Wire this into your monitoring (Uptime Kuma, healthchecks.io, Datadog,
etc.). The payload includes `equipment_count`, so an unexpected change
in that number is a useful alert.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `journalctl -u ac-organic-lab-web` shows `ENOENT server.js` | standalone static assets not copied | Re-run step 5 of the setup |
| API returns `unknown` state for every device | Tailscale down, or `base_url` in `equipment.yaml` wrong | `tailscale status`, verify URLs |
| Aggregator can't reach a device that a manual `curl` can reach | the `ac` user isn't in the tailnet ACL | check tailnet ACL / `tailscale up --accept-routes` |
| Web shows blank page | static assets missing from `.next/standalone` | Re-run step 5 and `systemctl restart ac-organic-lab-web` |
