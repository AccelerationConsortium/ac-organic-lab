# Single-Edge SSO Plan — one login for every lab UI

**Status:** plan (drafted 2026-07-06). Interim Phase 1 config committed at
`deploy/Caddyfile.single-edge`; not yet deployed/tested on the dashboard host.
**Owner concern:** central server (`ac-organic-lab`) — the edge runs on the
dashboard host.
**Relationship to `AUTH_DESIGN.md`:** this is the concrete build-out of that
doc's *"Why sessions can't be shared per-host"* conclusion and its **Phase 4**
("close the direct-device side-door; one origin ⇒ one cookie ⇒ SSO"). It does
**not** change the auth model — `ac_auth` + `roster.yaml` stay the authority.

---

## The principle

**SSO = one origin = one cookie = one login.** Users log in per-UI today only
because each device-native UI is a *separate origin*, and a session cookie
cannot cross origins (and cannot be shared across tailnet hosts — `ts.net` is on
the Public Suffix List, so a `Domain=…ts.net` cookie is silently dropped;
confirmed live 2026-07-06 on the xArm). The fix is not to share cookies across
hosts — it is to **collapse every UI behind one origin**, so a single host-only
cookie already covers every path.

**Auth split is unchanged.** Tailscale is the *network* boundary (nothing is
reachable off-tailnet). `ac_auth` email-code (single-factor, per decision to
keep it simple) proves *who you are*; `roster.yaml` says *what you may do*. This
plan only changes **how many origins** you sign into — from N to 1.

## Current state

- **Dashboard (Next.js)** at `100.64.254.6:8000`, already behind Caddy
  (`deploy/Caddyfile`). One origin; `ac_auth_session` cookie; control/assistant
  routes gated by `web/src/middleware.ts` → `100.64.254.6:8009/auth/verify`.
  Every tile-based device is controlled *through* this one UI, so it is already
  a single login for all of them.
- **Device-native panels** on other hosts are the separate logins — chiefly the
  **xArm `/web/`** panel at `sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000/web/`,
  which runs its own auth banner (its own origin ⇒ its own login).
- **`pypoe_web`** (`100.64.254.6:8006`) and AnaliticaDB `/docs`
  (`100.64.254.6:8010`) are the other browsable surfaces.

So "make login single-source" ≈ "pull the device-native panels onto the
dashboard's origin behind the same `ac_auth`."

---

## Phase 1 — interim single edge at `http://100.64.254.6` (do now)

Make `http://100.64.254.6/` the one entrypoint; path-route every UI under it.
Config: **`deploy/Caddyfile.single-edge`** (extends the existing `deploy/Caddyfile`).

Tasks:

1. **Extend the edge.** Add path routes for each device-native panel
   (`/xarm5` → xArm `/web/`; add others as they gain UIs). Dashboard stays at
   `/`. Camera streams / gateway blocks unchanged. *(config written)*
2. **Gate UI paths with `forward_auth`** → `ac_auth /auth/verify`, injecting
   `X-Auth-User` / `X-Auth-Role` downstream. *(config written)*
3. **Device panel trusts the edge identity.** The xArm panel must accept the
   edge-injected `X-Auth-User` (or drop its own self-login when reached via the
   edge), else the user is prompted twice. **Device-side change in the
   `xarm-translocation` repo.** ← the real work of "single source of login."
4. **Subpath vs base-path.** The xArm `/web/` panel uses root-absolute asset
   links. Either the `rewrite * /web{uri}` in the edge config suffices, or set a
   base path in the xarm repo. Verify on-host; prefer base-path if assets 404.
5. **Browser 401 → login redirect.** `forward_auth` returns the sidecar's 401
   verbatim; for a *page* navigation that's an ugly raw 401. Decide: either
   `ac_auth` grows a redirect mode for browser `Accept: text/html` requests, or
   the edge catches 401 and 302s to the dashboard login with a return URL.
6. **One canonical entrypoint.** Publish only `http://100.64.254.6/`. The ts.net
   hostname and device `host:port`s are different origins → second logins.
   301-redirect the old dashboard hostname to the IP (or vice-versa) so nobody
   splits the cookie.

Outcome: sign in once at `http://100.64.254.6/`; the cookie covers the dashboard
and every panel routed under it. Plain HTTP + non-Secure cookie over the tailnet
(same as today).

## Phase 2 — close the direct-device side-door

Path-routing adds a front door; it does not remove the direct one — a tailnet
member can still `curl sdl2-pc-03:8000/web/` un-gated. Make the edge the *only*
path in:

- **Tailscale ACL** so only the edge host (`100.64.254.6`) may reach device UI
  ports, **or**
- bind device panels to loopback and reverse-proxy locally (the pattern
  `kasa-tapo-services` already uses at `127.0.0.1:8002`).

This is `AUTH_DESIGN.md` Phase 4. Lower urgency among trusted tailnet members
(Tailscale is still the network gate), but required for the login to be
*authoritative* for control rather than advisory. Ship Phase 1 first, tighten
here after.

## Phase 3 — friendly name + real HTTPS (and multi-lab shape)

Replace the hard-to-memorize IP/MagicDNS name with a chosen name that also gets
a valid cert, keeping everything tailnet-only:

- **`sdl2.accelerationconsortium.ai`** → the edge's Tailnet IP `100.64.254.6`
  (public A record is harmless — `100.64.x` is unreachable off-tailnet — or a
  Tailscale custom DNS record if the name should resolve only on-tailnet).
- **DNS-01 wildcard cert** `*.accelerationconsortium.ai` via Caddy's DNS-provider
  plugin (needs only DNS control, not public reachability) → HTTPS + `Secure`
  cookie. Routing from Phase 1 is unchanged; only the site address at the top of
  the Caddyfile changes.
- **Multi-lab:** each lab is its own subdomain / own edge / own tailnet / own
  roster — `sdl2.accelerationconsortium.ai`, `<lab>.accelerationconsortium.ai`.
  Separate origins ⇒ per-lab logins (the wanted isolation). Equipment stays a
  *path* under each lab's edge (`…/xarm5`). Do **not** use
  `accelerationconsortium.ai/<lab>/…` (one origin) unless you actually want one
  AC-wide login backed by one central auth service bridging every lab's tailnet.

---

## Non-goals / decisions carried in

- **No Google/OIDC, no UofT Entra, no public exposure.** Considered and set
  aside: everything stays behind Tailscale; app login is per-user email-code.
- **No Tailscale-identity-as-login.** Considered and rejected by the owner
  (don't want to provision a Tailscale identity per user); users are on the
  tailnet but authenticate to the app with their own `ac_auth` IDs.
- **Single-factor is accepted** for now (simpler); MFA revisited only if control
  ever leaves the tailnet (`AUTH_DESIGN.md` "Going public…").

## Open questions

1. **Phase 1 task 5** — where the browser-facing 401→login redirect lives
   (sidecar redirect mode vs edge catch). Affects `ac_auth` or the Caddyfile.
2. **xArm panel** — cheapest way to make it trust the edge identity: honor
   `X-Auth-User` when present, or a "behind-edge" mode that disables its banner.
3. **Shared/kiosk machines** — Tailscale identity would be ambiguous there, but
   since we're using per-user `ac_auth` login anyway, this is moot; noted only so
   it isn't re-raised.

## See also

- `deploy/Caddyfile.single-edge` — the Phase 1 config.
- `deploy/Caddyfile` — the current (pre-single-edge) dashboard-only edge.
- `docs/AUTH_DESIGN.md` — auth model; "Why sessions can't be shared per-host";
  Phase 4 (side-door). This plan is that phase's build-out.
- `docs/ROADMAP.md` → *Control-surface exposure* — the direct-device hole Phase 2 closes.
