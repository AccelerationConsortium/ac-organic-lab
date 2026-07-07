# Single-Edge SSO Plan — one login for every lab UI

**Status:** **Phase 1 edge deployed 2026-07-07** — Caddy on `http://100.64.254.6`
serves the dashboard at `/`, one host-only `ac_auth_session` cookie, and
`forward_auth`-gated device paths. Verified: `/` 200, login round-trips through
the edge, `/xarm5` gate 401→login-redirect logged-out / proxies logged-in. **Still
open (Phase 1):** the xArm `/web/` panel rendering under `/xarm5` (subpath +
trust-edge — device-repo work, tasks 3–4) and publishing the one canonical URL
(task 6). Phases 2–3 (close the side-door; friendly name + HTTPS) not started.
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

1. ✅ **Extend the edge.** Path routes: `/xarm5` → xArm `/web/`, dashboard at `/`,
   camera streams / gateway blocks. *(deployed; a stray one-line-block syntax bug
   in the committed config was fixed on first deploy — commit b0e9619.)*
2. ✅ **Gate UI paths with `forward_auth`** → `ac_auth /auth/verify`, injecting
   `X-Auth-User` / `X-Auth-Role`. *(deployed; verified 401 logged-out / pass
   logged-in on `/xarm5`.)*
3. **Device panel trusts the edge identity.** The xArm panel must accept the
   edge-injected `X-Auth-User` (or drop its own self-login when reached via the
   edge), else the user is prompted twice. **Device-side change in the
   `xarm-translocation` repo.** ← the real work of "single source of login."
4. **Subpath vs base-path.** The xArm `/web/` panel uses root-absolute asset
   links (the live symptom: `/xarm5` 404s once past the gate). Either the
   `rewrite * /web{uri}` in the edge config suffices, or set a base path in the
   xarm repo. Verify on-host; prefer base-path if assets 404.
5. ✅ **Browser 401 → login redirect.** Chosen: `ac_auth`-side. `/auth/verify`
   returns **302 → `AUTH_LOGIN_URL` (default `/`)** for `Accept: text/html`
   navigations; Caddy copies the 3xx back on the `forward_auth` deny path.
   API/XHR (`*/*`, JSON — incl. the Next middleware) still get 401. *(main.py;
   `AUTH_LOGIN_URL` overrides the target.)*
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

## Spec — xArm `/web/` behind the edge (device-repo work, Phase 1 tasks 3–4)

**Repo:** `xarm-translocation` (not in this monorepo). **Symptom today:** once the
edge gate passes, `http://100.64.254.6/xarm5` renders broken / 404s, and the
panel still shows its own login. Two independent fixes.

### What the edge already guarantees (the device can rely on this)

- **Reached at** `sdl2-pc-03-cytation.tail6a1dd7.ts.net:8000`. The edge does
  `handle_path /xarm5/*` (strips `/xarm5`) → `rewrite * /web{uri}` → reverse-proxy,
  so the device receives requests at its own **`/web/…`** root.
- **Auth is already done** at the edge (`forward_auth` → `/auth/verify`). On the
  pass path Caddy injects **`X-Auth-User`** and **`X-Auth-Role`** onto the
  proxied request. The device does **not** need to authenticate the human.

### Task 4 — render under the `/xarm5` prefix (asset paths)

Root cause: the panel emits **root-absolute** asset URLs (`/static/…`, `/web/…`).
The browser resolves those against the edge origin as `http://100.64.254.6/static/…`
(no `/xarm5` prefix) → the edge routes them to the **dashboard** → 404.

**Fix (preferred): make the panel base-path aware.** Serve it under a configurable
prefix so every generated URL is either **relative** or **`/xarm5`-prefixed**:

- If the panel is FastAPI-served: set **`root_path="/xarm5"`** (via
  `FastAPI(root_path=…)` or `--root-path`, driven by an env like
  `XARM_WEB_ROOT_PATH`), and make templates/static references use
  `{{ request.scope.root_path }}`-prefixed or **relative** URLs (`static/…`, not
  `/static/…`). `url_for` with `root_path` set produces correct prefixed URLs.
- Serve when unset (`root_path=""`) exactly as today, so the standalone
  `:8000/web/` still works for on-host use.

**Rejected: edge-side asset rewrites.** Routing `/static/*` and `/web/*` at the
edge back to the xArm doesn't scale — those namespaces collide with the dashboard
and with a second device panel. Base-path is the only approach that generalizes to
N panels under one origin.

### Task 3 — trust the edge identity, drop the second login

When reached via the edge the request carries a trusted **`X-Auth-User`**; the
panel must **adopt that identity and skip its own login banner**, else the user is
prompted twice.

- **Behind the edge → no banner.** If `X-Auth-User` is present (and trusted, see
  below), treat the session as authenticated as that user; don't render the
  self-login.
- **Trust boundary.** `X-Auth-User` is only sound if it can't be forged by a
  direct caller. Today the device is directly reachable on the Tailnet, so a raw
  `curl …:8000/web/ -H 'X-Auth-User: someone'` would spoof it. Two ways to make it
  safe, in order of preference:
  1. **Phase 2 first** — loopback-bind the panel / Tailscale-ACL `:8000` so **only
     the edge** can reach it; then `X-Auth-User` is unspoofable and can be trusted
     directly. (This is the clean sequencing.)
  2. **Interim shared secret** — the edge injects `X-Edge-Auth: <secret>`
     (`header_up X-Edge-Auth {env.EDGE_SHARED_SECRET}` in the Caddyfile) and the
     device trusts `X-Auth-User` **only** when `X-Edge-Auth` matches. Lets you ship
     task 3 before Phase 2.

### Deeper follow-on (not required for SSO; tracked in ROADMAP *Control-surface exposure*)

Suppressing the login is the SSO fix. Separately, actions taken *in* the panel
still drive the arm outside the dashboard's audited claim path. Making the panel
**claim- and authz-aware** — acquire/hold a claim as `owner=X-Auth-User`, honor
the roster role — is the side-door closure, related but beyond Phase 1.

### Acceptance criteria

- [ ] `http://100.64.254.6/xarm5/` renders the panel fully (all assets 200, no
      `/static` 404s against the dashboard).
- [ ] A user already signed in at the edge reaches `/xarm5` with **no second
      login prompt**; the panel shows them as `X-Auth-User`.
- [ ] Standalone `…:8000/web/` still works on-host (base-path unset).
- [ ] A direct `curl` with a forged `X-Auth-User` is **not** trusted (via Phase 2
      ACL, or the `X-Edge-Auth` secret if shipping task 3 first).

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
