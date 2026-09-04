# Consuming the lab chemical inventory (bitacora) — API & MCP

The chemical inventory the dashboard shows lives in **bitacora** (`/home/sdl2/caoyang/bitacora`),
stored in its own SQLite file (`data/inventory.sqlite3`). **That file has exactly one
reader/writer — the bitacora service itself.** Every other consumer reaches the
data through the `"/inventory/*"` HTTP API, or through the `lab-inventory` MCP server
(which is a thin read-only front over that same API). Do not open the SQLite file from
a second process.

This guide covers: which host/port/path to use, the API endpoints, the MCP server,
and how to connect from an agent, a script, or a remote machine.

---

## 1. Where does it live? (ports & IPs)

There are two ways in — one internal, one over the lab network. Pick based on *where
the consumer runs*.

### 1a. On gaia itself (the lab server) — direct loopback

| Thing | Address |
|---|---|
| bitacora **API service** (owns the data) | `http://127.0.0.1:8050` |
| bitacora **frontend** (Next.js) | `http://127.0.0.1:3001` (basePath `/bitacora`) |
| MCP server default target | `http://127.0.0.1:8050` (env `BITACORA_URL`) |

The API binds **loopback only** (`127.0.0.1:8050`), so this is reachable **only from gaia**.
Any script or MCP server launched on gaia uses this directly.

### 1b. From anywhere on the lab network — through the Caddy edge

All other machines (and any browser/agent not on gaia) go through the single Caddy edge
on the Tailnet:

| Thing | Address |
|---|---|
| Caddy edge (Tailnet, HTTPS) | `https://100.64.254.6` |
| Inventory **reads** over the edge | `https://100.64.254.6/bitacora/api/inventory/...` |
| Inventory **writes** (admin) over the edge | same base — but requires login + admin |

How the path resolves: the edge routes `/bitacora/*` → Next.js on `:3001`; Next.js
rewrites `/api/:path*` → `127.0.0.1:8050/:path*`. So `/bitacora/api/inventory` on the
edge becomes `/inventory` on the backend. **External callers must use the full
`/bitacora/api/` prefix.** Internal callers (on gaia) hit `/inventory` directly.

> **Reads are NOT behind login at the edge.** The Caddy config exempts
> `/bitacora/api/inventory*` (and the embed page + shared static assets) from
> `forward_auth` — the shelf is public-read. Writes (import, tombstone) are still
> gated: without a verified admin identity they fail closed with 403. So exposing the
> read endpoints externally is intentional and opens no write path.

---

## 2. HTTP API reference

All endpoints are on the API service. Substitute the base:

- On gaia: `http://127.0.0.1:8050`
- Over the edge: `https://100.64.254.6/bitacora/api`

Reads need **no auth header** (both internally and over the edge). Writes need identity
plus admin.

### Read endpoints

| Method & path | Purpose |
|---|---|
| `GET /inventory?q=<name or CAS>&limit=50` | **Search** by name / CAS / synonym. `q` empty = browse first `N`. Each result includes all its bottles (availability, location, amount). |
| `GET /inventory/<cas>` | **One chemical** by CAS with every bottle (vendor, lot, expiry, location). 404 if absent. |
| `GET /inventory/check?cas=<cas>&needed=50&unit=mL` | **Sufficiency check**: "is 50 mL of THF on the shelf?" Sums across all bottles in the requested unit and reports `sufficient`. A unit mismatch reports what *is* in stock rather than guessing a conversion. |
| `GET /inventory/stats` | Totals: chemicals, bottles, enriched records. |
| `GET /inventory/groups` | The lab shelves (groups) with bottle counts. |
| `GET /inventory/match?labels=dmso,tempo` | Resolve protocol stock labels → `{cas, name}` (null = lab doesn't stock it). |
| `GET /inventory/snapshots?group=<name>` | Import history (what each sheet covered, when, by whom). |
| `GET /inventory/candidates?group=<name>` | Staleness candidates (a signal only — the system never deletes). |
| `GET /inventory/<cas>/structure.png` | Stored 2D structure image (ETag revalidation, `Cache-Control: no-cache`). 404 = not rendered yet. |

### Write endpoints (admin only)

| Method & path | Purpose |
|---|---|
| `POST /inventory/import` | Upload a Vertere-format `.xlsx` LIMS export. Multipart fields: `file` (.xlsx), `group` (**required** — the lab name), `enrich` (bool, default off — enrichment is slow). |
| `POST /inventory/bottles/{group}/{barcode}/tombstone` | Mark a bottle removed (tombstone, not hard delete — old records keep resolving). Form field `reason` (**required**). |

Writes return 403 without a verified admin identity. Attribution comes from the
`X-Auth-User` header, which only the edge/Caddy injects — never from a request body.

### Example curl (on gaia, read — no auth)

```bash
# Search by name
curl 'http://127.0.0.1:8050/inventory?q=dmf'

# Sufficiency check
curl 'http://127.0.0.1:8050/inventory/check?cas=68-12-2&needed=50&unit=mL'

# One chemical, full record
curl 'http://127.0.0.1:8050/inventory/68-12-2'
```

### Same over the edge (from any lab machine)

```bash
curl 'https://100.64.254.6/bitacora/api/inventory?q=dmf'
curl 'https://100.64.254.6/bitacora/api/inventory/check?cas=68-12-2&needed=50&unit=mL'
```

---

## 3. MCP server (`lab-inventory`) — for AI agents

For agents (Claude Code, Codex, Hermes, any MCP client), the ready-made path is the
**`lab-inventory`** MCP server at
`/home/sdl2/caoyang/ac-organic-lab/api/app/inventory_mcp.py`. It is a thin, read-only
front over the HTTP API and adds no auth itself — it calls `BITACORA_URL`
(default `http://127.0.0.1:8050`) over loopback, so it must run where the API is
reachable (on gaia, or with `BITACORA_URL` pointed at the edge URL).

### 3a. Connect a client

The server is a stdio process, console script `lab-inventory-mcp` from the
`ac-organic-lab` api package. Register it in your MCP client pointing at the deploy venv:

```json
{
  "mcpServers": {
    "lab-inventory": {
      "command": "/home/sdl2/caoyang/ac-organic-lab/.venv/bin/lab-inventory-mcp"
    }
  }
}
```

- Or `uv run lab-inventory-mcp` from the `ac-organic-lab` repo (self-syncs the venv).
- To reach the inventory over the edge instead of loopback:
  `BITACORA_URL=http://127.0.0.1:8050` (default) or set it to the edge inventory base.

### 3b. Tools it exposes (the stable agent contract)

| Tool | Answers |
|---|---|
| `search_inventory(query, limit=20)` | Find by **name / CAS / synonym**; compact per-bottle view (group, location, amount, unit). Empty query = browse. |
| `check_stock(cas, needed, unit="mL")` | "Enough on the shelf for this CAS?" — sufficiency vs summed stock. |
| `get_chemical(cas)` | Full record: hazards (GHS/H/P), storage class, SDS link, every bottle (vendor/lot/expiry/location). |
| `inventory_stats()` | Totals + per-group bottle counts. |

These tool names are a **frozen contract** — the backend may move to BitacoraDB's
Substance/Lot/Container ledger, but the client-facing tools must not change. Never rename
them for a storage change.

### 3c. Safety rules for this server

- **Strictly read-only.** No import/tombstone/write tools exist here, and none may be
  added — inventory writes are admin actions gated at bitacora and must not appear as
  MCP tools.
- It **never opens the SQLite file** — HTTP to the bitacora service is the only channel.
- **Hazards are convenience copies enriched from PubChem** — the SDS is the
  authoritative safety source, not `get_chemical`'s fields.

The full reviewed registration (provenance, allowlist, data policy, known clients) lives
in `/home/sdl2/caoyang/ac-organic-lab/mcp/servers.yaml` under `lab-inventory`.

---

## 4. Which path to use — decision guide

| Situation | Use |
|---|---|
| AI agent with MCP support, running on gaia or wired to the edge | **`lab-inventory` MCP server** (§3) |
| Plain script / integration on gaia | **HTTP API direct** at `http://127.0.0.1:8050/inventory/...` (§2) |
| Script or agent on another lab machine | **HTTP API over the edge** at `https://100.64.254.6/bitacora/api/inventory/...` (§2), or MCP with `BITACORA_URL` set to the edge base |
| Browser / dashboard embed | `/bitacora/inventory/embed` (public-read, no login) |
| Upload a LIMS export / remove a bottle | **HTTP write endpoints as an admin** (§2) — never MCP |

---

## 5. Caveats & gotchas

- **Don't open `data/inventory.sqlite3` directly** from a second process — it breaks the
  WAL single-writer assumption and couples callers to a storage shape that is scheduled
  to move into BitacoraDB. HTTP (or MCP) is the contract.
- **Internal vs external base.** On gaia use `/inventory` (loopback); anywhere else use
  the full `/bitacora/api/inventory` prefix through the edge.
- **Reads are public over the edge; writes are not.** Don't add auth requirements to the
  read endpoints, and don't assume a caller that can read can also write — the admin
  gate is separate and stays.
- **`check_stock` does not guess unit conversions.** Ask in the same unit the bottle
  amounts are stored in (case-insensitive match); a mismatch reports what *is* in stock
  instead of silently comparing numbers of different units.
- **Structure images 404 until rendered.** `GET /inventory/<cas>/structure.png` is
  populated by `python -m bitacora.fetch_structures` (RDKit ACS render, stored once).
  A 404 means "not rendered yet", not "no such chemical".