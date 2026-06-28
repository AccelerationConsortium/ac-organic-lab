# Design: Authorization service (identity → role, platform→device hierarchy)

**Status:** v1 implemented (`auth/ac_auth`: email-code login, sessions, Gmail
sender, allow-list CLI); see *Phasing* for what's done vs remaining. Drafted
2026-06-23.
**Scope:** central auth/authorization **module inside `ac-organic-lab`** serving
every platform and device. Single source of truth for *who may do what, as what
role* across a platform→device hierarchy, plus session management and an audit
trail. (This is the canonical auth doc; it replaces the earlier `auth.md` plan.)

## Background: account auth, not Tailscale

This module enforces at the edge (Caddy `forward_auth`, with the
destructive-vs-convenience bypass) but **does not use Tailscale identity for
humans**, which doesn't hold here:

- **The dashboard is going public** (off-Tailnet), so external users have no
  Tailscale identity at all.
- **Lab Tailscale nodes are tagged** (`tag:device`, …), so `whois` returns a
  *tag*, not `user@email` — even on-Tailnet it yields no human identity.

So **account-based authentication is primary**, not a fallback. Tailscale's role
is narrower and unchanged: it is the **network boundary for the device plane**
(platform server ↔ device sidecars, ACL'd by tag) — machine-to-machine, not a
human-identity source.

The authn/authz split:

- **Authentication = who you are.** A human account — username/email + password
  (argon2id), **or** an external IdP (OIDC/OAuth) — proven at the public edge and
  carried as an opaque session cookie. Machine principals use API keys. (Local
  password vs IdP is the one open authN decision — see *Open decisions*.)
- **Authorization = what you may do.** This DB owns it: users, the
  platform→device hierarchy, role grants, sessions, and audit — regardless of
  which authN front-end is chosen.

## Goals / non-goals

**Goals**
- One central identity → role resolution for all platforms and devices.
- A **platform → device** hierarchy with role inheritance; multiple platforms.
- Devices stay **credential-free** — the platform is the trusted gateway and
  stamps the authenticated user into the device claim (`details.claimed_by.owner`).
- Full **audit**: who claimed / submitted / serviced / was granted what, when.
- A safe **first-admin bootstrap** anchored to server OS access.

**Goals (cont.)**
- Support a **public dashboard** (off-Tailnet human users) with real account
  authentication at the edge — TLS, secure sessions, rate-limiting/lockout.

**Non-goals**
- Using Tailscale as the *human* identity source (tagged nodes give no user; the
  public dashboard has no Tailnet at all). Tailscale stays the **device-plane
  network boundary** only.
- Any credential storage on device sidecars (they remain Tailnet-only behind the
  platform).

## Architecture & hierarchy

Two planes: a **public human-facing plane** (account auth at the edge) and a
**Tailnet device plane** (machine-to-machine, tag-ACL'd).

```
   PUBLIC human users (browsers, off-Tailnet)
            │  HTTPS (public cert)
            ▼
   Caddy edge  ── forward_auth ─▶ /auth/verify (session cookie)
            │     200 + X-Auth-User/Role → allow · 401/403 → login
            ▼
   ┌─────────────────────────────────────────────┐
   │  ac-organic-lab (control plane / server)     │
   │   ├─ auth module (FastAPI, this design)       │
   │   │    SQLite (WAL) · opaque sessions          │
   │   │    authn: account (password or IdP) + API keys
   │   │    authz: grants · session validation · audit
   │   └─ dashboard / api                            │
   └───────────────┬─────────────────────────────────┘
                   │  Tailnet (device plane, tag-ACL'd)
        ┌──────────┼───────────────────────┐
        ▼          ▼                        ▼
   Platform A   Platform B (future)    Platform N …   each owns devices
        │                                   │
   ┌────┴─────┐                         ┌───┴────┐
   ▼          ▼                         ▼        ▼
 device     device   ← credential-free sidecars, Tailnet-only; trust the
 (uplc-ms)  (xArm…)     platform as gateway; apply local roster as defense-in-depth
```

- **Humans** authenticate at the public Caddy edge with an account; the auth
  module validates the **session cookie** at `/auth/verify`. There is no
  Tailscale identity in this path.
- **Devices** stay Tailnet-only and credential-free; the platform server (on the
  Tailnet) is the trusted gateway that claims on them as `owner=<username>`. The
  device's env roster is the **local projection** of central grants and a
  degraded-mode fallback if central is unreachable.
- The auth module is the authority for **all** platforms. Other platforms are
  **clients** that call it (`/auth/verify`, `/authz/check`) — one identity store.
- Each **device belongs to exactly one platform**; grants resolve down the
  hierarchy (see Role resolution).

## Data model (SQLite, WAL mode)

| table | key columns | notes |
|---|---|---|
| `platforms` | id, key (`hte`), name, created_at | a control platform that owns devices |
| `equipment` | id, key (`agilent_uplc_ms`), name, kind, **platform_id→platforms** | each device under one platform |
| `users` | id, email (unique), display_name, **idp_subject NULLABLE** (target/OIDC), password_hash NULLABLE (reserved/unused), status (`active`/`disabled`/`pending`), email_verified, is_service_account, last_login_at, created_at | v1 identifies users by **email**; idp_subject filled in target/OIDC mode. |
| `authorizations` | id, user_id→users, **scope_type** (`global`/`platform`/`equipment`), **scope_id** (null for global), **role** (`user`/`admin`), granted_by, granted_at, revoked_at | the normalized grant = the allow-list. One user, many grants. |
| `api_keys` | id, user_id, key_hash, label, expires_at, revoked_at | machine principals (automation accounts, robot/platform) — keys, not passwords |
| `sessions` | id, user_id, **token_hash**, issued_at, expires_at, idle_expires_at, revoked_at | opaque tokens stored hashed → revocable |
| `login_codes` | id, user_id (or email), **code_hash**, expires_at, attempts, used_at, requested_ip | v1 email one-time codes: single-use, short TTL, attempt-capped, rate-limited |
| `audit_log` | id, ts, actor_user_id, action, scope_type, scope_id, target, detail (json), ip | meaningful actions + denials only (login, claim, submit, service_start/stop, grant, revoke, reset, denied authz) — **not** routine `/auth/verify`; see Audit policy |

Automation accounts are `users` rows with `is_automation=true`, authenticated by
an `api_key`. They are **auto-seeded on registration**: one per platform (the
robot/platform principal) and one per equipment (the equipment automation account),
so "every platform always has an automation account" holds by construction.

## Role model & resolution (hierarchy-aware)

Stored roles are coarse: `user` and `admin`, at a `global` / `platform` /
`equipment` scope. The **effective** capability on a device is the *highest
applicable* grant, resolved top-down:

```
global.admin  >  platform.admin (of device's platform)  >  equipment.admin
              >  platform.user                           >  equipment.user
```

This maps onto the device roles already shipped on the sidecars:

| central grant (effective on a device) | → device role | device capabilities |
|---|---|---|
| `*.user` | `user` | submit to non-reserved trays |
| platform **automation principal** | `automation` | submit (incl. reserved trays) + `workflow.*` |
| `*.admin` (equipment or inherited platform/global) | `service` | submit + `service.*` |

- A **platform admin** is admin on all that platform's devices (inheritance);
  a **global admin** is admin everywhere.
- The **automation principal** (robot/platform) is what grants the device
  `automation` role — i.e. the right to take the equipment-blocking workflow lock on its own
  platform's devices. This also replaces the self-declared `submitter="robot"`
  field on the device: *manual vs robot is derived from the authenticated
  principal's group*, and reserved-tray access is the robot/platform group only.
  (Per the tray rule: a `user` may submit to **any non-reserved tray**.)

## Authentication (account-based; public edge)

Humans authenticate by **email**; authorization (allow-list + roles) is identical
regardless of method. Roadmap: start simple now, swap the front door later
without touching the DB.

**Email one-time code (passwordless) — the chosen mechanism.**
1. User enters their email (must be on the allow-list).
2. The service emails a short-lived **single-use** code (6 digits, ~10 min,
   attempt-capped) **via Gmail** — the sole email backend (`ac_auth.smtp_mailer`,
   Gmail SMTP + an App Password set up by `python -m ac_auth.setup_gmail`). There
   is **no Microsoft/Outlook/Graph sending path** — that plan was dropped.
3. User enters the code → opaque session cookie issued. No password is ever
   stored.

This is **single-factor** — possession of the inbox — *not* true 2FA (true 2FA =
password **+** code). It is acceptable behind the allow-list; for `utoronto.ca`
users the inbox itself sits behind UofT's Duo, which raises the bar in practice.
Must-haves: per-email/IP **rate limiting**, single-use codes with an **attempt
cap**, and deliverable mail. Deliverability note: gmail→utoronto usually inboxes
(Google reputation) but a new recipient can hit first-send quarantine — if it
bites at rollout, ask UofT IT to allow-list the Gmail sender.

**Machine principals** (automation accounts, robot/platform): API keys, unchanged.

The login resolves to: verified email → effective role → session cookie.
**Tailscale is not in this path** (public users; tagged nodes give no user).
`password_hash` is used by neither path — the column stays reserved for a
possible future local-password fallback.

**Optional future (not planned):** "Sign in with Microsoft" (Entra OIDC, Duo
inherited) could replace the email-code login for `utoronto.ca` users, fronted by
Caddy + `oauth2-proxy`. Deferred because it needs a UofT app registration (ITS) —
the dependency we chose to avoid. The allow-list/roles/sessions below are
identical, so it'd be a front-door swap if ever wanted.

**Sessions:** opaque random tokens, **stored hashed**, with absolute + idle
expiry and server-side revocation (logout, admin disable, password reset all
revoke). Preferred over JWT here: trivial revocation, no key management, and the
central service is already on the request path.

## Auth flows

- **Human login (v1 email code):** `POST /auth/request-code {email}` → (if
  allow-listed) email a single-use code → `POST /auth/verify-code {email,code}`
  → set opaque session cookie. Subsequent requests: Caddy `forward_auth` →
  `/auth/verify` validates the cookie → injects `X-Auth-User`/`X-Auth-Role`.
- **Human login (target IdP):** OIDC redirect → callback → map verified email to
  a `users` row (must be allow-listed) → set session cookie → same path.
- **Machine (robot/service):** API key → principal → short-lived session.
- **Acting on a device:** the platform checks the user's grant for that
  equipment, then claims on the device (over the Tailnet) as `owner=<username>`;
  the device applies its own claim/role gate (defense-in-depth) and stamps
  `claimed_by.owner`.
- **Peer platform:** calls central `GET /authz/check?user=…&equipment=…&action=…`
  (or relies on the injected `X-Auth-*` headers from the shared edge).

## First-admin bootstrap (CLI)

Trust anchor = **shell access to the `ac-organic-lab` server** (the operator at
deploy time). A management command, run once:

```
python -m ac_auth.cli add-user you@utoronto.ca --role admin
```

- The first `add-user --role admin` **is** the bootstrap — it allow-lists that
  admin email; there is no separate bootstrap step or setup secret. The admin
  then signs in the normal way by requesting an email code. **No password.**
- Manage the allow-list with the same CLI (`list-users`, `disable-user`,
  `enable-user`, `delete-user`).

After bootstrap, the admin grants the remaining users/admins via the CLI (and,
once the platform/equipment hierarchy below is built, registers those + their
auto-seeded automation accounts).

## Service / API surface

The human endpoints are reachable at the **public edge** (via Caddy); the
device-plane endpoints (`/authz/check`, `/equipment/{key}/roster`) are
Tailnet-only.

- `GET  /auth/verify` — forward-auth endpoint (cheap session-cookie check).
- `POST /auth/request-code`, `POST /auth/verify-code`, `POST /auth/logout`,
  `GET /auth/me`. (target) `GET /auth/oidc/login`, `GET /auth/oidc/callback`.
- `GET  /authz/check` — peer-platform / device authorization probe.
- `GET  /equipment/{key}/roster` — owner→role projection a device can pull.
- Admin: users CRUD, grants CRUD, API-key issue/revoke, password reset
  (force-change), platform/equipment registration.

## Security considerations

**Public-exposure hardening (the dashboard is internet-facing):**
- **TLS** with a real public cert — Caddy auto-provisions Let's Encrypt; HSTS on.
- **Cookies:** `HttpOnly`, `Secure`, `SameSite=Lax/Strict`; short idle expiry.
- **CSRF** protection on state-changing requests (token or `SameSite` + origin
  checks); CORS locked to the dashboard origin.
- **Login abuse:** rate-limit + lockout (`failed_login_count` / `locked_until`),
  exponential backoff, and bot/credential-stuffing protection — public login is
  the most attacked surface. (An IdP offloads most of this.)
- **MFA** for `admin` accounts at minimum (native if local-password; via the IdP
  if OIDC).
- **Email verification** before an account is `active`.

**Core:**
- **argon2id** hashing; passwords never returned; admin reset is **force-change**,
  not a default value.
- **Session revocation** on logout / disable / reset (hashed token store).
- **SQLite:** SQLite serializes writers **database-wide** (one writer at a time,
  whole-file lock); readers are unaffected. Configure:
  - `PRAGMA journal_mode=WAL` — readers and the single writer no longer block
    each other.
  - `PRAGMA busy_timeout=5000` — brief write contention waits-and-retries
    instead of raising `database is locked`.
  - **Single writer process.** The classic `database is locked` trap is running
    several uvicorn/gunicorn workers that each open the same file and write
    concurrently. Keep the auth module to one writer (one worker on the write
    path, or funnel writes through a single connection). Keep DB access behind a
    repository layer so a Postgres swap is cheap if write volume ever grows.
- **Audit policy (keep writes — and the log — useful):** audit **meaningful
  state changes and authz denials only**, never the high-frequency
  `forward_auth` `/auth/verify` calls. Verify fires on *every* request through
  the edge (dashboard polls, page loads, API/tile/metric fetches, device status
  loops) — auditing each one is both a write-flood that contends for the single
  SQLite write lock (blocking logins / password updates) and pure noise. Audit
  instead: login / logout, claim, `run.submit`, abort / cancel, service-mode
  toggle, `workflow.start` / `end`, user create, grant / revoke, password reset,
  and any **denied** authz decision (401 / 403). Log the *effective* role used
  for each control action.
  - **Escape hatch if audit volume ever grows:** put `audit_log` in a **separate
    SQLite file** (its own independent write lock → audit writes can never block
    auth writes regardless of volume), or stream audit to an append-only
    log / journald instead of SQLite.
- **SPOF:** the central service is on the auth path for all platforms; mitigate
  with short session caching at platforms and the device-side roster fallback for
  degraded-mode cooperative operation.

## Phasing

**DONE (v1, in `auth/ac_auth`):** SQLite store (`db.py`: users/login_codes/
sessions, WAL), Gmail mailer (`smtp_mailer.py`) + App-Password setup
(`setup_gmail.py`), email-code auth routes (`main.py`: request-code/verify-code/
verify/me/logout + sessions), allow-list CLI (`cli.py`). Tested.

**DONE (roster-projection authZ layer — interim flat, hierarchy-shaped):**
- **Automation-account / API-key principals** — the machine-principal account type
  (robot/platform → device role `automation`). `db.py`: `users.is_automation`
  (+ additive migration for old DBs; renames the legacy `is_service_account`
  column in place) and an `api_keys` table (hashed key, optional expiry,
  revocable). `/auth/verify` now authenticates a session cookie **or** `X-Api-Key`,
  so humans and machines share one forward-auth edge.
- **Resolver seam** — `authz.py::effective_device_role(user, equipment_key)`:
  automation→`automation`, admin→`service`, user→`user`. The **only** place
  central accounts map to device roles. Flat today (`equipment_key` accepted but
  unused); when the hierarchy lands, only this function's body changes.
- **Roster projection** — `GET /equipment/{key}/roster` returns the active
  accounts as `{owner, role}` entries through the resolver (device-plane,
  Tailnet-only). The endpoint is already keyed by equipment so adding
  per-equipment grants later needs no contract change.
- **Edge ② owner-stamping** — `api/app/control.py::_claim_owner` stamps the
  authenticated `X-Auth-User` (else the dashboard fallback) into the device claim
  **and** the audit row, replacing the hardcoded constant.
- **CLI** — `add-service-account`, `issue-key` (prints once), `list-keys`,
  `revoke-key`. Tested (`test_authz.py`, plus DB/route/control additions).

Remaining:
1. Public-edge hardening for the routes: TLS (Caddy), `HttpOnly`/`Secure`/
   `SameSite` cookies (already set; flip `AUTH_COOKIE_SECURE` on in prod), CSRF,
   per-email/IP rate-limit (only the attempt cap exists today).
2. Edge integration: Caddy `forward_auth` → `/auth/verify` (the owner-stamping
   consumer in `control.py` is done; the Caddy config + X-Auth-* strip/re-inject
   is the remaining infra). Kind-based bypass stays in `api/app/control.py`.
3. **Device-side roster pull (other repo, `agilent-hplcms-server`):** the device
   fetches `GET /equipment/{key}/roster` on a refresh interval and feeds it into
   `control/roster.py` (which today reads static env lists), with a cached
   last-good fallback when central is unreachable. This is what makes per-user
   device roles live end-to-end.
4. Authorization hierarchy (deferred — not needed for one platform/one device):
   `platforms` / `equipment` / `authorizations` tables + top-down grant
   resolution. Additive: treat today's `users.role` as a `global` grant and
   flesh out `effective_device_role`; callers and the roster contract don't move.
5. (Later) Postgres if write volume grows.
6. **Optional, not planned:** "Sign in with Microsoft" (Entra OIDC) + Duo as an
   alternative front door — needs the UofT app registration we chose to avoid.

## Risks / arguments against (recorded)

1. **Public login surface.** Email-code is **single-factor** (inbox control) and
   leans on email **deliverability**; it can be brute-forced or spammed if not
   bounded. → single-use short-TTL codes + attempt cap (done) + per-email/IP
   rate-limit (TODO). Gmail→utoronto can hit first-send quarantine; allow-list
   the sender via UofT IT if it bites. (The earlier "use Tailscale to avoid a
   password store" idea is dead: public users + tagged nodes give no Tailscale
   human identity. Microsoft SSO would add real MFA but needs the UofT app
   registration we're avoiding — kept optional.)
2. **SQLite write concurrency** under multi-platform + audit + workers. → WAL +
   `busy_timeout` + single writer + repo layer + Postgres path.
3. **Central SPOF.** → session cache + device roster fallback.
4. **Module vs standalone service.** → clean internal boundary for later
   extraction.
5. **Hierarchy/inheritance complexity.** → keep the top-down resolution rule
   simple; log the effective role in audit.

## Open decisions

1. **Human authN:** decided & built — **email one-time code (passwordless),
   sent via Gmail** (App Password). Microsoft/Outlook/Graph sending was dropped;
   "Sign in with Microsoft" remains an optional, unplanned future front door.
2. **Inheritance:** platform admin auto-admins all that platform's devices
   (recommended yes) and global admin everywhere — confirm.
3. **One central auth in `ac-organic-lab` for all platforms** (assumed) vs
   per-platform auth instances.

## See also

- `docs/STATUS_SPEC.md §4.11` — Tailnet-as-boundary posture; oauth2-proxy escape
  hatch.
- `docs/EQUIPMENT_INTEGRATION.md §6b` — the `CONTROL_PASSWORD` gate this replaces.
- `api/app/control.py` — claim/heartbeat/release passthrough that gains the real
  `owner`.
- Device side (`agilent-hplcms-server`): the roster + role gates
  (`user` / `automation` / `service`) this service feeds; service-mode and
  workflow-lock are the device capabilities the `service` / `automation` roles unlock.
```
