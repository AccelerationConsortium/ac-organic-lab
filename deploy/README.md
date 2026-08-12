# Deployment (Linux + systemd)

The dashboard runs on a single Linux server attached to the lab's Tailnet.
Two systemd services, no app-level auth - access is gated by Tailscale ACLs.

> The macOS-specific LaunchDaemon (`deploy/limit.maxfiles.plist`) and the
> `WATCHPACK_POLLING=true` dev hack in `web/package.json` are **only** for
> local development on macOS. Linux production uses the Next.js
> **standalone build** (no file watcher, no FD limit issue) and systemd's
> `LimitNOFILE=65536` in each unit. You can ignore them for server deploy.

## Layout on the server

> **The paths below are the recommended layout, not necessarily where a given
> server actually has it.** The units in this directory are templates; the live
> deployment on `gaia` runs from `/home/sdl2/caoyang/ac-organic-lab` (installed
> before this layout was written), and its unit files under
> `/etc/systemd/system/` carry those paths. So never assume `/opt` when
> operating a running server — ask systemd:
>
> ```bash
> systemctl show ac-organic-lab-api \
>   -p FragmentPath -p WorkingDirectory -p EnvironmentFiles -p ExecStart -p User
> ```
>
> `EnvironmentFiles=` is where environment variables belong (e.g.
> `DEVICE_EDGE_SHARED_SECRET`); `FragmentPath=` is the unit actually in force.
> Note that `systemctl show -p Environment` does **not** expand
> `EnvironmentFile=`, so an empty result there proves nothing — read the live
> process instead:
>
> ```bash
> sudo tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value ac-organic-lab-api)/environ
> ```
>
> That file holds secrets once `DEVICE_EDGE_SHARED_SECRET` or auth config is in
> it, so it should not be world-readable — match its ownership to the unit's
> `User=` and use `600` (or `640` with the service's group).

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

## Secrets in service environments (`Environment=` vs `EnvironmentFile=`)

**Rule: a secret never goes in `Environment=`.** Put it in an
`EnvironmentFile=` with mode `0640 root:<service-user>`, and keep it out of the
process's own startup logging.

The reason is not file permissions — it is that systemd re-publishes
`Environment=` values through a world-readable property:

```bash
systemctl show caddy.service -p Environment      # prints values, no root needed
```

Locking the file down does **not** help. As of 2026-08-12 the edge secrets live
in `/etc/systemd/system/caddy.service.d/edge-secret.conf`, mode `600 root:root`
— unreadable as `sdl2` — and `systemctl show` prints every one of them anyway,
because systemd parsed them at load time. Contrast
`ac-organic-lab-api.service`, which uses `EnvironmentFile=` and whose
`Environment` property is empty; file contents are never exposed that way.

**Why it matters here.** Those values are the per-device edge secrets
(`XARM_EDGE_SHARED_SECRET`, `OT2_EDGE_SECRET`, `GRAPHCHAT_EDGE_SECRET`) that
let a device trust an injected `X-Auth-User` (see
[`AUTH_DESIGN.md`](../docs/AUTH_DESIGN.md) → *How a device learns who the
operator is*). Anyone with a shell on this host can read one, then POST
straight to a device on the Tailnet with `X-Auth-User: <anyone>` and act as
that person — bypassing the edge, the dashboard passthrough, and the
`control_action` audit row. Bounded, in that a shell here already implies lab
control, but it converts "has shell" into "can impersonate a named operator in
the audit trail", which is a different and worse thing.

**Second leak on the same service:** `ExecStart` runs `caddy run --environ`,
which prints the entire environment to stdout at every start, i.e. into the
journal for anyone in `adm` / `systemd-journal`. Moving to an
`EnvironmentFile` does not fix that — `--environ` has to go too.

### Onboarding an edge-fronted device: the secret goes in **two** places

A device that trusts injected identity needs its secret in three environments,
and it is easy to stop at two:

1. **the device's own service env** (`XARM_EDGE_SHARED_SECRET` in its NSSM/systemd config)
2. **Caddy's env**, so the edge can inject it on that device's panel routes
3. **the dashboard API's env** (`/home/sdl2/caoyang/ac-organic-lab/.env`), so the
   *control passthrough* — tile buttons, the workflow executor, the assistant's
   Authorize — can present it too

Step 3 is the one that gets missed, because the panel works without it: the
panel goes through Caddy, the passthrough does not. Symptom is a device that
operates fine from its own framed UI while every dashboard control returns 401.
`equipment.yaml` names the variable (`edge_secret_env:`) but cannot supply its
value.

The dashboard logs this rather than failing quietly — grep the journal after
adding a device:

```bash
journalctl -u ac-organic-lab-api -S -10min | grep edge_secret_env
# edge_secret_env=XARM_EDGE_SHARED_SECRET is set on xarm_translocation
# but that variable is empty; falling back to DEVICE_EDGE_SHARED_SECRET
```

That line means step 3 is missing. Add the variable to `.env` and restart
`ac-organic-lab-api` (an `EnvironmentFile` is read at unit start, so a reload is
not enough).

### Migration (do it when nobody is mid-run — restarting Caddy drops the edge)

Every dashboard URL goes through Caddy, so this is a brief full outage of the
web UI, the device panels and the auth flow. Nothing device-side changes: each
device keeps its own copy of its secret.

```bash
# 1. Copy the current values into a locked file, without ever printing them.
sudo install -m 0640 -o root -g caddy /dev/null /etc/caddy/caddy.env
systemctl show caddy.service -p Environment --value | tr ' ' '\n' \
  | grep -E '^(XARM_EDGE_SHARED_SECRET|OT2_EDGE_SECRET|GRAPHCHAT_EDGE_SECRET)=' \
  | sudo tee /etc/caddy/caddy.env >/dev/null
sudo wc -l /etc/caddy/caddy.env          # expect 3

# 2. Replace the drop-in: EnvironmentFile instead of Environment, and drop
#    --environ so the values stop being printed at startup.
sudo tee /etc/systemd/system/caddy.service.d/edge-secret.conf >/dev/null <<'CONF'
[Service]
EnvironmentFile=/etc/caddy/caddy.env
# Clear the inherited ExecStart before setting our own (systemd requires the
# reset), and run without --environ: it dumps the environment to the journal.
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
CONF

sudo systemctl daemon-reload
sudo systemctl restart caddy.service
```

Verify — the first command should now print nothing, and the panels should
still answer their auth gate rather than 502:

```bash
systemctl show caddy.service -p Environment          # expect: Environment=
curl -s -o /dev/null -w '%{http_code}\n' http://100.64.254.6/xarm5/web/    # 401 (gate), not 502
curl -s -o /dev/null -w '%{http_code}\n' http://100.64.254.6/              # 200
```

A 502 on the panel means Caddy started without the secret — check that
`/etc/caddy/caddy.env` is readable by the `caddy` user and that the variable
names match the `{env.*}` placeholders in the Caddyfile.

**Rollback:** restore the previous drop-in content (`Environment=` lines) and
`daemon-reload` + `restart`. Keep a copy before step 2.

**While you are there:** the same reasoning applies to any future service that
needs a secret — and to `equipment.yaml`, which must keep naming *variables*
rather than carrying values, since it is committed.

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
