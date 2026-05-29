# AC Organic Lab Dashboard

Battery-dashboard-style React dashboard for the AC Organic Self-driving Lab.

## Local development

Start the lab API from the repo root:

```bash
uv run uvicorn api.app.main:app --host 127.0.0.1 --port 8001
```

Then start the dashboard:

```bash
cd app/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

Open `http://127.0.0.1:5174`. The Vite dev server proxies `/api` and
`/camera` to `http://localhost:8001`.

## Checks

```bash
npx vitest run
npm run build
```
