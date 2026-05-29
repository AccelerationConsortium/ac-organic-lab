# Deployment (Lab Server)

This directory contains the Linux systemd deployment for the lab API process.
The current operator dashboard is the Vite React app in `app/frontend/`; AWS
dashboard deployment is configured from the repo root via
`.github/workflows/deploy.yml` and `platform/`.

## Layout on the server

```text
/opt/ac-organic-lab/
├── api/                              # checked-out source + venv
│   └── .venv/                        # Python deps
├── equipment.yaml                    # registry
├── platforms.yaml                    # dashboard layout
├── docs/STATUS_SPEC.md
└── .env                              # gitignored; optional local overrides
```

## One-time API setup

```bash
sudo useradd --system --create-home --home /opt/ac-organic-lab \
    --shell /usr/sbin/nologin ac

sudo -u ac git clone <repo-url> /opt/ac-organic-lab

cd /opt/ac-organic-lab
sudo -u ac uv sync

sudo cp deploy/ac-organic-lab-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ac-organic-lab-api

sudo systemctl status ac-organic-lab-api
curl -fsS http://127.0.0.1:8001/api/health
```

The API reads `equipment.yaml` and `platforms.yaml` from the repo root. Override
paths with `LAB_REGISTRY_PATH` and `LAB_PLATFORMS_PATH` if the service layout is
different.

## Local dashboard with the lab API

For bench-top/local work, run the React dashboard next to the API:

```bash
cd /opt/ac-organic-lab/app/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5174
```

The dev server proxies `/api/*` and `/camera/*` to `http://localhost:8001`.

## Day-to-day API operations

```bash
journalctl -fu ac-organic-lab-api
journalctl -t ac-organic-lab-api -n 200

sudo -u ac git -C /opt/ac-organic-lab pull
sudo systemctl restart ac-organic-lab-api
```

## Optional Caddy front door

[`Caddyfile`](Caddyfile) is a local/Tailnet convenience config. For a local
dashboard dev server it can proxy browser traffic to `127.0.0.1:5174` while
leaving the lab API on `127.0.0.1:8001`.

## Cameras and smart plugs

Cameras and Kasa plugs are bridged through the companion
`kasa-tapo-services` gateway on the dashboard host:

- gateway API: `127.0.0.1:8002`
- go2rtc stream service: `127.0.0.1:1984`

Register each bridged device in `equipment.yaml` with `adapter: http`,
`base_url: http://127.0.0.1:8002`, and the appropriate status path. The lab API
continues to expose equipment status and media through `/api/equipment/*`.

## Healthcheck

```text
GET http://127.0.0.1:8001/api/health
```

The payload includes `equipment_count`, which is useful for monitoring
unexpected registry or startup changes.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| API returns `unknown` state for every device | Tailscale down, or `base_url` in `equipment.yaml` wrong | `tailscale status`, verify URLs |
| Aggregator cannot reach a device that manual `curl` can reach | the `ac` user is not in the tailnet ACL | check tailnet ACL / `tailscale up --accept-routes` |
| Dashboard cannot load API data locally | frontend dev proxy cannot reach port 8001 | verify `curl http://127.0.0.1:8001/api/health` |
