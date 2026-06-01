# Plan: Authentication sidecar for control access

**Status:** design / not started. Drafted 2026-05-31.
**Scope:** `ac-organic-lab` dashboard control access first; reusable for
future control panels (per-device `/web/` UIs, standalone control apps).

## Goal & guiding principle

Replace the current single shared-password gate (`CONTROL_PASSWORD` +
Next.js middleware, see `docs/EQUIPMENT_INTEGRATION.md §6b`) with a
**standalone auth sidecar** that:

- gives **per-user identity** (not just "the password holder"),
- enforces auth at the **reverse-proxy edge** (Caddy `forward_auth`), so
  it's reusable by any future control panel with one config line,
- stamps the real user into device **claims**
  (`details.claimed_by.owner`) and the history DB for audit,
- preserves the existing **destructive-vs-convenience** policy
  (lights / camera / env sensors stay open — see `web/src/lib/tile-policy.ts`).

This follows the posture already blessed in the docs: *"If a device ever
leaves the Tailnet, put it behind oauth2-proxy at the edge — never roll
auth into individual equipment repos."* (`docs/STATUS_SPEC.md §4.11`).
The sidecar is the sanctioned central place to put auth.

## Architecture (forward-auth pattern)

```
Tailnet client
   │
   ▼
Caddy  (TLS via `tailscale cert`)
   ├─ forward_auth  →  auth-sidecar  GET /auth/verify
   │     200 + X-Auth-User/X-Auth-Role → allow, inject identity downstream
   │     401/403                       → deny / redirect to login
   │     (scoped to mutating control routes only; reads stay open)
   ├─ reverse_proxy /api/*     → FastAPI :8001
   ├─ reverse_proxy /*         → Next.js :3000
   └─ reverse_proxy /streams/* → go2rtc
```

The sidecar is a tiny FastAPI service (e.g. `:8009`, systemd unit
`ac-organic-lab-auth.service`) exposing:

- `GET  /auth/verify` — the forward-auth endpoint (cheap, cookie/token check)
- `POST /auth/login`, `POST /auth/logout`
- `GET  /auth/me` — identity for the frontend lock chip

## The one decision (Phase 0)

| Backend | Identity source | Pros | Cons |
|---|---|---|---|
| **Tailscale identity** *(recommended)* | sidecar calls tailscaled LocalAPI `whois` on the source IP | zero new credentials, real per-user SSO for free, matches current Tailnet-only posture | only works for Tailnet clients (fine today) |
| Custom password/token | shared or per-user secret → signed cookie | works off-Tailnet, full control | rolling your own auth |
| oauth2-proxy + IdP | GitHub/Google OAuth | battle-tested, the documented escape hatch | needs an OAuth app, heavier |

**Recommendation:** Tailscale identity as primary (gives `user@…`-level
identity with no new secrets), with a signed-token fallback for the
off-Tailnet case, and oauth2-proxy noted as the heavy alternative if a
public edge is ever needed.

## Phased rollout

**Phase 1 — Stand up the sidecar (audit mode, no enforcement).** New
`auth/` package + systemd unit + Caddyfile block. Implement
`/auth/verify` returning 200 always but logging the resolved identity, so
Tailscale `whois` extraction is confirmed before anything is gated.

**Phase 2 — Enforce at the edge + stamp claims.**
- Caddy `forward_auth` enforces on
  `POST|PUT|PATCH|DELETE /api/equipment/*/control/*`, with URL exceptions
  mirroring today's `actionBypassesControlGate` (`/control/lights*` stays open).
- `api/app/control.py` reads the injected `X-Auth-User` and uses it as the
  claim `owner` (replacing the generic `ac-organic-lab-dashboard`), and
  keeps the **kind-based bypass** (`kindBypassesControlGate` for camera/env)
  since only the FastAPI layer knows the equipment `kind` from the id.
  → instant "who did what" in `details.claimed_by` and `equipment_events`.

**Phase 3 — Frontend.** Swap the `ControlAuthProvider` password modal for a
`/auth/me`-driven flow (with Tailscale, often *no* modal — already
authenticated). Keep the lock-chip UX and 10s auto-relock;
`tile-policy.ts`'s bypass classification is unchanged.

**Phase 4 — Roles + audit (optional).** Git-tracked `auth.yaml` mapping
identity → `viewer|operator|admin`; control requires `operator`. Add a
"control history by user" view later.

**Phase 5 — Generalize to other control panels.** Document the
`forward_auth` snippet so any new panel behind the same Caddy opts in with
one block. Device-side panels (xArm `/web/`, etc.) get fronted at the edge
if/when needed — never auth in the device repo.

**Phase 6 — TLS + cookie hardening; retire `CONTROL_PASSWORD`.** Enable
`tailscale cert` TLS in Caddy, flip session cookies to `Secure`, then
remove the old middleware once the sidecar covers everything.

## Files in play

- **New:** `auth/` service, `deploy/ac-organic-lab-auth.service`,
  `auth.yaml`, `docs/AUTH.md`; edits to `deploy/Caddyfile`.
- **Edit:** `api/app/control.py` (identity→claim owner, keep kind bypass),
  `web/src/middleware.ts` (replace), `web/src/components/ControlAuth*` +
  `web/src/lib/use-control-lock.ts`, `docs/EQUIPMENT_INTEGRATION.md §6b`,
  `deploy/README.md`, `docs/ARCHITECTURE.md`.

## Key risks / things to confirm later

1. **Reads stay open?** Current posture gates only mutations — confirm
   read auth isn't also wanted.
2. **Kind-based bypass** can't live purely in Caddy (URL lacks `kind`) —
   it must stay in `control.py`. Caddy handles the broad surface +
   action-name exceptions.
3. **Tailscale `whois`** needs the sidecar to reach the local tailscaled
   socket (fine on the dashboard host) and won't identify non-Tailnet
   clients — acceptable today; the fallback covers it.

## See also

- `docs/EQUIPMENT_INTEGRATION.md §6b` — current `CONTROL_PASSWORD` gate and
  the two-layer bypass model this plan subsumes.
- `web/src/lib/tile-policy.ts` — destructive-vs-convenience classification
  (preserved by this plan).
- `api/app/control.py` — control passthrough + claim/heartbeat/release
  dance that gains the real `owner`.
- `deploy/README.md` — Caddy front-end config this plan extends.
