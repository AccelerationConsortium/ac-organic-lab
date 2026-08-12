# Auth Design — identity, authorization, and data access

**Status:** v1 + **Phases 0 / 1a / 1b + data-ownership projects shipped & deployed**
(`auth/ac_auth`: email-code login, opaque sessions, Gmail sender, send-rate
limiting, the **`roster.yaml` allow-list** with fail-closed validation, per-scope
**grant resolution** — global / platform / equipment — plus `role: none`
restriction, and **project-based data scope**). What remains: data-isolation
*enforcement* (`can_read`), finishing claim-authorization + closing the
direct-device side-door, and automation approval — see *Phasing*. Drafted
2026-06-23; last revised 2026-08-07 (**standalone installs**: absent banner MUST
mean open — see *Policy*). Prior revision 2026-07-07 (login dropdown shows
**names, not emails**; `/auth/request-code` → `/auth/login`; `/auth/*` namespace
documented).
**Scope:** central auth/authorization **module inside `ac-organic-lab`** serving
every platform and device. Single source of truth for *who may do what on which
equipment, as what role* across a platform↔device graph, plus session
management, per-user data isolation, and an audit trail. (Canonical auth doc;
replaces the earlier `auth.md` and `AUTH_SERVICE_DESIGN.md`.)

---

## The one principle everything hangs off

**The auth service owns *grants*; devices enforce them locally from a roster they
pull; every path to hardware goes through one authenticated chokepoint.**

The corollary is load-bearing: **there must be exactly one path to each device's
`/control/*`.** Devices are reachable directly on the Tailnet today, so per-user
rules and claim-binding are *advisory* — anyone can `curl` past them. Per-user
authorization is theater until that side-door is closed (front each device behind
the auth edge, or bind it to loopback and reverse-proxy through the platform
gateway). This is a prerequisite for requirements 1–3 below, not a finishing
touch. See `docs/ROADMAP.md` → *Control-surface exposure*.

### Policy: every web service carries the central auth (normative)

**Every browsable web service in the lab MUST be served behind the single auth
edge (`http://100.64.254.6`) and carry the shared central-auth surface** — the
`ac_auth` session cookie and the shared top banner (`<script
src="/auth/banner.js">`). There is one place a user signs in, and every UI shows
it. This applies to all of them: the dashboard, the xArm `/web/` panel,
AnaliticaDB, LaAgenteAnalitica, `pypoe_web`, and any future UI. A web service
that is only reachable on its own `host:port` origin (a second sign-in, or no
sign-in at all) is a **non-conformance** to be migrated onto the edge.

Two levels, and they are distinct:

- **Carry the banner (mandatory for every UI).** The service is path-routed onto
  the single edge so `/auth/*` resolves same-origin and the host-only
  `ac_auth_session` cookie rides along; the banner renders and the user can sign
  in once for the whole lab. This is required even for a service that is not yet
  gated.
- **Enforce (per service, as it is ready).** Wrapping the service's edge path in
  Caddy `forward_auth` → `/auth/verify` (and/or the service's own cookie-verify
  middleware) is applied when that service is ready to gate access. Bringing a UI
  onto the edge *without* `forward_auth` (banner present, access still open) is
  the sanctioned interim step — carry first, enforce next — not a resting state.

Reads that are deliberately public (live equipment status / telemetry; see *Reads
vs writes*) stay public **behind** the edge — "carry the central auth" means the
sign-in surface is present and identity is available, not that every byte is
gated.

### Standalone installs — an absent banner MUST mean open (normative)

The policy above binds every UI **in this lab**. It does not bind the device
repos themselves, which are independently installable: another lab can `pip
install` `opentrons-server` or `xarm-translocation` and run the panel with no
edge, no `ac_auth`, and no intention of ever having one. That is a supported
deployment (ARCHITECTURE.md LG1 — multi-lab portability), so:

**A panel MUST remain fully usable when `/auth/banner.js` fails to load.** Absent
banner ⇒ **open mode**, never locked out. A UI that hides or disables its own
controls because nobody is signed in would lock a standalone operator out of
their own robot — the failure is silent, looks like a bug in the device, and has
no local remedy.

Three properties make this hold; all three are load-bearing, so preserve them
when touching a panel's shell:

1. **The include is optional by construction.** Inject the tag at runtime with an
   **absolute** path and `defer` — it resolves at the *edge* origin, not from the
   panel's own bundle, so with no `ac_auth` there it 404s and a deferred script
   that 404s blocks neither parsing nor render. Do not `import` it, bundle it, or
   make first paint wait on it.
2. **Every read of the contract is guarded.** The host-page contract the banner
   populates is:

   ```js
   window.labAuth = { enabled: true, identity: {email, role} | null }
   document 'labauth:change'             // fires with the identity (or null)
   window.labAuth.releaseClaimOnSignOut  // optional panel hook; banner awaits it
   ```

   None of it exists in a standalone install. Read it as `window.labAuth &&
   window.labAuth.…` and treat undefined as "no auth in play".
3. **Gate on `enabled`, never on `identity` alone.** `!identity` is true both when
   auth is on and nobody signed in *and* when there is no auth at all — the two
   cases need opposite behaviour. The banner makes the distinction expressible:

   ```js
   window.labAuth = window.labAuth || { enabled: true, identity: null };
   window.labAuth.enabled = true;   // only runs if the script actually loaded
   ```

   so a panel may pre-declare `window.labAuth = { enabled: false, identity: null }`
   in its own `index.html`, and the banner flips `enabled` to `true` **iff** it
   loaded. No probing, no timeout.

**State as of 2026-08-07.** Both panels already carry the banner and already
degrade correctly.

| Panel | Consumes `window.labAuth`? | Standalone behaviour |
|---|---|---|
| OT-2 (`opentrons-server/ui/`) | No — zero reads in `ui/src/` | Nothing to degrade. Theme is pre-applied inline from `localStorage` before first paint, so even the banner's `class="dark"` coupling has its own path. |
| xArm (`xarm-translocation/src/web/`) | Yes — claim wiring in `main.js` | Every read guarded; the one that gates behaviour (`main.js`, `Boolean(window.labAuth && window.labAuth.enabled && !window.labAuth.identity)`) evaluates **false** when the banner is absent, i.e. not blocked. Copy this shape rather than re-deriving it. |

Both also suppress their own banner when framed (`window.self !== window.top`),
since the dashboard embed already shows one — one banner, not two. The xArm
solved that first; the OT-2 panel mirrors it.

**Accepted cost:** a console 404 for `/auth/banner.js` on every load in a
standalone or dev install. Cosmetic, and cheaper than a build flag or a runtime
probe. Say so in the panel's HTML comment so the next reader doesn't "fix" it.

**Not a security position.** Absent banner means absent auth: the panel *and* the
device's `/control/*` are open to anyone who can reach them. That is a
deployment-posture question for the installing lab (network isolation), not
something the UI can or should compensate for — and it is unchanged from the
Tailnet-only stance this lab runs today. Nothing here weakens the policy above:
inside this lab, every UI carries the banner and the side-door still has to close.

#### The switch between the two postures

A device repo does not choose standalone-vs-lab at build time; it is **one env
switch at deploy time**. The OT-2 gateway is the reference implementation
(`opentrons_server/gateway/api.py`), and its comment names the flag an
"§6.5-style override flag" — the STATUS_SPEC §6.5 pattern of a runtime override
that exists for dev and never for production:

| Env var | Default | Effect |
|---|---|---|
| `OT2_UI` | `true` | `off` unmounts the operator UI entirely (headless gateway). |
| `OT2_TRUST_LOCAL_UI` | **`true`** | `true` = **blind trust**: `/ui` + `/labware` are served to anyone who can reach the port, no identity trusted. `false` = **edge-only**: they answer only requests forwarded by the auth edge (`X-Edge-Key` must match `OT2_EDGE_SECRET`), and the edge-asserted `X-Auth-User` is stamped into claim owners. **Direct hits get 404.** |
| `OT2_EDGE_SECRET` | unset | The shared secret the edge presents as `X-Edge-Key`. Becomes **required** when `OT2_TRUST_LOCAL_UI=false`. |
| `OT2_REQUIRE_LOGIN` | **`false`** | `true` = `/control/claim` requires a verified principal. Enforced at claim acquisition — the single chokepoint every motion endpoint already sits behind — because claims are cooperative, **not** authentication (STATUS_SPEC §5). |
| `OT2_API_KEYS` | unset | Machine principals, for callers with no browser session. |

**404, not 401/403, on a direct hit.** From an unauthenticated caller's view the
UI surface simply does not exist. A 401 would advertise that something worth
attacking is listening on that port.

**Default-open is deliberate, and it is the standalone case.** A bare checkout
runs `trust_local_ui=true` so the panel just works with no edge — that is the
same default the section above depends on. Two things keep it honest:

- **It logs loudly at startup** (`OT2_TRUST_LOCAL_UI=true: operator UI is served
  without the auth edge (dev bypass — set OT2_TRUST_LOCAL_UI=false in
  production)`). The warning is the compensating control for the permissive
  default; do not downgrade it to `info`.
- **Both closed postures fail *fast*, not silently.** `OT2_TRUST_LOCAL_UI=false`
  without `OT2_EDGE_SECRET` raises at startup, as does `OT2_REQUIRE_LOGIN=true`
  with neither `OT2_EDGE_SECRET` nor `OT2_API_KEYS` — the code's reason is worth
  keeping: *"Fail-closed with no way in is a bricked device, not a secure one."*

**Posture is observable on `/status`, so you never have to read a service's env
to know how it is running:**

- `details.ui_mode` — `off` | `open` (trust-local) | `edge`
- `details.control_auth` — `open` | `claim_only` (a claim token is the only gate
  — cooperative, not authentication) | `identity`

**Live posture, verified 2026-08-07.** Both gateways run
`control_auth: "identity"`; `/ui/` on the gateway port returns **404** direct
(`:8020` HTE, `:8021` complexation), the edge path returns **401** without a
session, and a tokenless `POST /control/claim` returns **401 `login_required`**.
So for the OT-2s the direct-device side-door in `docs/ROADMAP.md` →
*Control-surface exposure* is **already closed**, page and control plane both —
ahead of the rest of the fleet. Residual: a shell user on the device PC can still
reach `127.0.0.1:8020`, which stays an operational/physical control.

**Generalizing.** Any device repo that ships a browsable operator UI **SHOULD**
expose an equivalent pair — one switch for the UI surface, one for the control
plane — rather than hard-wiring either posture. Keep the permissive default so a
standalone install works out of the box, and pair it with the loud startup
warning and the fail-fast guards above; this lab's deployment sets both closed.
Name them per-repo (`<DEV>_TRUST_LOCAL_UI` / `<DEV>_REQUIRE_LOGIN`) and publish
`ui_mode` / `control_auth` in `details` so a fleet-wide posture sweep is one
`/status` poll.

## Requirements (what this design must deliver)

1. **A clear definition of what each user can do on each equipment/platform** —
   per-scope role grants, resolved to a capability.
2. **Equipment/platform pull their own auth list** from the auth service and
   enforce it locally (defense-in-depth, survives a central outage).
3. **Always claim before control** — every control action is behind a claim held
   by the authenticated identity; the claim *is* the per-user gate.
4. **User data is private** — experiment data is readable only by its owner, the
   platform-admin of the platform that owns the equipment, and a global admin.
5. **Automation accounts are admin-approved** — a machine principal can run
   automation only after a global admin approves it; it is platform-scoped and
   time-boxed, and platform-authorized humans may invoke it.
   *Scoping enforced since 2026-07-24:* `authz.effective_device_role` bounds an
   automation account to its declared roster scope (`platform:` shorthand or
   explicit `grants:`, which may name single equipment); undeclared stays
   lab-wide for back-compat. Since 2026-07-25 `ac_auth.cli validate` also
   cross-checks every grant id against `equipment.yaml`/`platforms.yaml`, so a
   typo'd scope fails validation instead of becoming a silently dead grant.

---

## Background: account auth, not Tailscale

This module enforces at the edge (Caddy `forward_auth`) but **does not use
Tailscale identity for humans**:

- **The dashboard is going public** (off-Tailnet), so external users have no
  Tailscale identity at all.
- **Lab Tailscale nodes are tagged** (`tag:device`, …), so `whois` returns a
  *tag*, not `user@email` — even on-Tailnet it yields no human identity.

So **account-based authentication is primary**. Tailscale's role is narrower: it
is the **network boundary for the device plane** (platform server ↔ device
sidecars, ACL'd by tag) — machine-to-machine, not a human-identity source.

The authn/authz split:

- **Authentication = who you are.** A human account (email; passwordless
  email-code today, optional OIDC later) proven at the public edge and carried as
  an opaque session cookie. Machine principals use API keys.
- **Authorization = what you may do.** This DB owns it: users, the
  platform↔equipment graph, role grants, sessions, data-access policy, and audit.

## Goals / non-goals

**Goals**
- One central identity → role resolution for every platform and device.
- A **platform ↔ equipment** graph (many-to-many: equipment may be shared by
  several platforms) with grant inheritance.
- Devices stay **credential-free** — the platform is the trusted gateway and
  stamps the authenticated user into the device claim (`details.claimed_by.owner`).
- **Per-user data isolation** for experiment data.
- **Admin-gated automation** principals.
- Full **audit**: who claimed / submitted / serviced / was granted what, when —
  including the human behind an automation run.
- A safe **first-admin bootstrap** anchored to server OS access.
- Support a **public dashboard** (off-Tailnet humans) with real edge auth — TLS,
  secure sessions, rate-limiting/lockout.

**Non-goals**
- Tailscale as the *human* identity source.
- Credential storage on device sidecars (they stay Tailnet-only behind the platform).
- A general policy engine (OPA etc.) — coarse roles + one resolution seam suffice
  at this scale and are far more debuggable.
- Gating *read-only* status/telemetry behind auth — operational dashboards are
  public (see *Reads vs writes*).

## Architecture & planes

Two planes: a **public human-facing plane** (account auth at the edge) and a
**Tailnet device plane** (machine-to-machine, tag-ACL'd).

```
   PUBLIC human users (browsers, off-Tailnet)
            │  HTTPS (public cert)
            ▼
   Caddy edge  ── forward_auth ─▶ /auth/verify (session cookie or X-Api-Key)
            │     200 + X-Auth-User/Role → allow · 401/403 → login
            │     (read-only status/telemetry routes bypass auth)
            ▼
   ┌─────────────────────────────────────────────┐
   │  ac-organic-lab (control plane / server)      │
   │   ├─ auth module (FastAPI, this design)        │
   │   │    SQLite (WAL) · opaque sessions           │
   │   │    grants · session validation · audit       │
   │   ├─ dashboard / api  (owner-stamped control)    │
   │   └─ history DB (lab.db: runs/results, owner-tagged)
   └───────────────┬─────────────────────────────────┘
                   │  Tailnet (device plane, tag-ACL'd)
        ┌──────────┼───────────────────────┐
        ▼          ▼                        ▼
   Platform A   Platform B (future)    Platform N …   platforms own/ share devices
        │   └──────── shared equipment ───────┘
   ┌────┴─────┐                         ┌───┴────┐
   ▼          ▼                         ▼        ▼
 device     device   ← credential-free sidecars, Tailnet-only; pull a scoped
 (uplc-ms)  (xArm…)     roster from central; enforce claim + role locally
```

- **Humans** authenticate at the public Caddy edge; the auth module validates the
  **session cookie** at `/auth/verify`. No Tailscale identity in this path.
- **Devices** stay Tailnet-only and credential-free; the platform server is the
  trusted gateway that claims on them as `owner=<username>`. Each device pulls a
  **scoped roster** (its own auth list) and uses it as a local enforcement +
  degraded-mode fallback.
- The auth module is the authority for **all** platforms; other platforms are
  **clients** (`/auth/verify`, `/authz/check`).
- **Equipment ↔ platform is many-to-many** — a device (the xArm plate-mover,
  cameras, power strips) may belong to several platforms. This matches the
  dashboard's existing shared-equipment support in `platforms.yaml`.

---

## Role model & resolution

### Roles (small, capability-named)

| Central role | Scope it can be granted at | What it can do | Maps to device role |
|---|---|---|---|
| `operator` | global / platform / equipment | submit/run control (**must claim**) | `user` |
| `admin` | global / platform **only** | full control incl. `service.*` / overrides **+**, at global scope, governance | `service` |
| `automation` | platform (machine principals only) | submit incl. reserved trays + `workflow.*` lock | `automation` |

Deliberately **no `viewer`** (reads are public — see *Reads vs writes*) and **no
`maintainer`** (folded into `admin`: "admin within a scope" = full control of
that scope's hardware). There is **no equipment-scoped `admin`** — platform is
the finest admin scope; "operate only this one device" is an equipment-scoped
`operator` grant instead.

> Device-side capability roles (`user` / `automation` / `service`) shipped on the
> sidecars are unchanged; the table's right column is the mapping. The flat v1
> `users.role` column still stores `user` for what this model calls `operator`
> (a cosmetic rename to land with the hierarchy; `user` == `operator`).

### Governance split

| Authority | Can do |
|---|---|
| **global admin** | add/remove accounts · assign **all** grants · approve automation accounts · full control everywhere · **read all data** |
| **platform admin** *(an `admin` grant on a platform)* | full control of that platform's devices · **read that platform's experiment data**. **No** account or grant management. |
| **operator** *(grant, any scope)* | control (with a claim) within scope |
| **automation** | machine principal (platform-scoped, approved) |

**Only a global admin manages accounts and grants.** A platform admin is a
*capability* (control + data visibility for its platform), not a delegated
grant-manager. A global `operator` is made a platform admin by a global admin
assigning them an `admin` grant on that platform. (Delegating grant-management to
platform admins is a one-line future relaxation if the lab grows — start strict.)

### Resolution (graph, not tree)

Effective role on device **X** = the **highest** of:

```
global grant  ∪  grant on ANY platform that contains X  ∪  equipment grant on X
```

(`operator < admin`; `automation` is orthogonal, machine-only.) So a platform
grant gives that role on every device the platform contains, and a shared device
takes the **union/highest** across all containing platforms.

**Caveat — sharing is shared access by construction.** A platform grant reaches
every device that platform contains, including shared ones. If a shared device
must be drivable by only *some* of a platform's members, grant it at the
**equipment** scope instead of relying on platform inheritance.

Resolution lives in **one** function (`authz.py::effective_device_role`), the
only place central accounts map to device roles. It is flat today
(`equipment_key` accepted but unused); when the graph lands, only this function's
body changes — callers and the roster contract don't move.

---

## Reads vs writes (what auth actually gates)

- **Public (no *account* required):** live equipment *status* and lab *telemetry*
  (uptime, sensors, equipment state). "Public" means **no login** — *not*
  "visible to the whole internet." The audience is whatever can **reach** the
  dashboard: **Tailnet-only today**, and genuinely internet-visible only once the
  dashboard is deliberately exposed off-Tailnet (or mirrored/shared to a public
  URL). So the network layer is still doing real access control for reads right
  now.
  - **Carve-out:** camera streams/snapshots are **not** in this tier — they are
    privacy-sensitive and stay Tailnet-gated (or behind auth) even on an
    otherwise-public dashboard.
- **Authenticated — control:** any `/control/*` action → `operator`+ on that
  device **and** an active claim (see *Claim-before-control*).
- **Authenticated — private data:** experiment data (runs / results / files) →
  owner, the platform-admin(s) of the owning equipment's platform, or a global
  admin (see *Data isolation*).

A `viewer` role is unnecessary precisely because public reads need no role and
private reads are gated by **ownership**, not a role tier. (The **assistant chat**
is a read surface but it reads *history*, so it requires auth and is owner-scoped
— see *Assistant chat*.)

### Going public collapses two layers into one — keep control on the Tailnet

Control is protected today by **two independent layers**: (1) Tailnet membership
(you can't even reach the dashboard off-Tailnet) and (2) the account allow-list +
claim. Making the dashboard public removes layer (1). Two consequences:

- **Outsiders still cannot self-serve control.** There is no self-registration;
  accounts are global-admin-only, and control needs `operator`+ **and** a claim.
  "After auth" means *after an admin authorized you* — not "anyone who reaches the
  login page." So the answer to *"can outsiders control after auth?"* is **no,
  unless an admin grants them an account.**
- **But the account becomes the *sole* perimeter for control**, and email-code is
  **single-factor (inbox possession)** — a compromised inbox of an allow-listed
  operator = lab control from anywhere, with no Tailnet backstop.

**Recommendation:** expose only the **read tier** publicly; keep `/control/*`
(and the camera tier) **Tailnet-gated** even when reads go public. If control must
be reachable off-Tailnet, require **MFA for control-capable accounts** (the
OIDC+Duo front door) to replace the network layer you removed. The device-plane
direct side-door (§*The one principle*) stays Tailnet-only regardless — it is
never public.

## Claim-before-control (requirement 3)

An invariant, with the claim doubling as the per-user gate:

1. **Hard claim enforcement on every device** — `X-Claim-Token` required on all
   `/control/*` → **423** without. (Done on plateloc, dose, uplc, fume-hood,
   press, cytation, ot2_hte; finish the rest.)
2. **The claim is authorized, not merely exclusive.** On `POST /control/claim`,
   the device checks its roster: `owner` must have `operator`+ on this device,
   else **403**. Acquiring the claim *is* the authorization check; holding it is
   the concurrency guard.
3. **Claim owner = authenticated identity.** The gateway authenticates the human
   (session) and claims as `owner=<user>`; the device trusts the gateway *because
   the side-door is closed*. No self-declared owners.
4. **Mode = claim holder.** A human holding the claim is "manual"; a workflow (or
   automation principal) holding it is "automated." Do **not** build a separate
   manual/automated mode flag.

### Two unrelated TTLs (don't conflate them)

- **Claim TTL** is kept alive by **heartbeats, not activity.** The SDK's
  `ClaimManager` heartbeats in the background regardless of whether a step is in
  flight, so a long pause *between* operations is fine — the claim only expires
  after ~3 consecutive **missed** heartbeats, i.e. when the holder's process dies
  or disconnects. That auto-expiry is the feature: a crashed workflow releases the
  device instead of locking it forever. ("Claim expired" never means "you didn't
  send a command recently.") Manual dashboard control holds no long-lived claim at
  all — each click is acquire → act → release.
- **Roster-cache TTL** is unrelated to operations: it is only *how stale a
  revocation may be* (how long a device's cached auth list lags central). Suggest
  **30–60 s** — a revoked user can still act for at most that long.

## How a device learns who the operator is (edge-injected identity)

**Status: partly built, and the built part has a structural limit — recorded
2026-08-12 after it broke xArm control for a day.**

A login-gated device needs to know *which human* is acting. There are two ways
it can find out, and the lab uses both:

1. **The device's own login.** The operator signs into the device's panel
   (email one-time code) and the device holds its own session.
2. **Identity injected at the edge.** Caddy authenticates the human against
   ac_auth (`forward_auth` → `/auth/verify`), then forwards
   `X-Auth-User` / `X-Auth-Role` to the device, plus **`X-Edge-Auth`, a shared
   secret**. The device trusts the injected identity *only* when that secret
   matches its own copy — which is what stops anyone on the Tailnet from
   simply asserting `X-Auth-User: someone-else` straight at port 8000. It is
   the reason the framed panels (`/xarm5/web/`, `/ot2/*/ui/`) don't ask for a
   second login.

The secret is **per device**. The deployed edge carries
`XARM_EDGE_SHARED_SECRET`, `OT2_EDGE_SECRET` and `GRAPHCHAT_EDGE_SECRET` as
separate values (the repo's `Caddyfile.single-edge` adds
`ANALYTICA_EDGE_SECRET`), each paired with a copy in that device's own service
environment. That is the right shape: one leaked secret compromises one
device's header trust, not the fleet's.

### The limit: the passthrough holds exactly one secret

`api/app/control.py` reads a single `DEVICE_EDGE_SHARED_SECRET` and sends it to
**every** device. Against a fleet of per-device secrets it can satisfy at most
one of them, and every other login-gated device answers 401 `login_required`.

This is not hypothetical: it is what broke xArm control end to end. The
dashboard was holding a 48-character secret while the arm's was 64 characters,
so the panel (through Caddy, with the right secret) worked while the tile, the
workflow executor and the assistant's Authorize button (through the passthrough,
with the wrong one) all failed — with an error indistinguishable from "you are
not logged in". Diagnosis cost a day, most of it spent looking for a missing
credential rather than a mismatched one.

A stopgap on 2026-08-12 pointed `DEVICE_EDGE_SHARED_SECRET` at the xArm's
value, which unblocked the arm — and, on inspection, the value it replaced was
the **OT-2's**. One value for a fleet of per-device secrets is a game of
whack-a-mole; it was replaced the same day by the design below.

**Which devices actually enforce this, measured 2026-08-12** — the answer is
narrower than the alarm suggested, and worth having written down before the
next person reasons from the config alone:

| Device | `/control/*` gate |
|---|---|
| `xarm_translocation` | **identity** — `/control/claim` refuses without an accepted credential (401 `login_required`), and every action needs the claim |
| `ot2_hte`, `ot2_complexation` | **claim only** — a wrong `X-Edge-Auth` and no credential at all both return the same `423 missing or invalid X-Claim-Token`; identity is never checked |
| everything else | unprobed; determining it means requesting a claim, which has a side effect on live hardware |

So the stopgap did **not** break the OT-2s, and the OT-2 entries keep
`edge_secret_env: OT2_EDGE_SECRET` for a different reason: that *is* their
secret — Caddy injects it on their panel routes — and the annotation costs
nothing if the gateway later gates its API the way the xArm does. Note the
asymmetry it reveals: a device can be identity-gated at the panel and
claim-only at the API, so "is it behind the edge" does not answer "does it know
who is calling".

### The fix (shipped 2026-08-12): resolve the secret per equipment

Give the registry the same authority over *how to authenticate to* a device
that it already has over *where to reach* it (`base_url`), naming the
environment variable rather than the value, so nothing secret enters git:

```yaml
  - id: xarm_translocation
    base_url: http://sdl2-pc-03-cytation…:8000
    edge_secret_env: XARM_EDGE_SHARED_SECRET   # name, never the value
```

`EquipmentEntry.edge_secret_env` (in `lab_skills.registry`) carries the name;
`control._edge_secret_for(entry)` resolves it from this process's environment,
falls back to `DEVICE_EDGE_SHARED_SECRET` when the entry names none (so nothing
regresses), and — when an entry *names* a variable that is unset — falls back
too but logs a warning, because a typo is otherwise indistinguishable from a
device that simply does not gate. When neither resolves there is no edge
candidate at all, at which point the 401 fallback (`152a87c`) tries the
operator's own credential instead.

Annotated as of 2026-08-12: `xarm_translocation` → `XARM_EDGE_SHARED_SECRET`,
`ot2_hte` and `ot2_complexation` → `OT2_EDGE_SECRET`. Everything else uses the
fallback, unchanged. Deploying a *new* edge-fronted device means adding its
secret to the dashboard service environment alongside Caddy's — the same
operational step already required on the device side.

**Not covered: `workflow.py`.** The authorized-run executor builds one header
set for a whole multi-device run (`Lab.connect(headers=…)`), so it cannot vary
the secret per step without threading the target through `lab_skills`. It uses
the global fallback; a run touching a device with its own secret fails closed at
that step rather than actuating with the wrong identity.

The alternative — a naming convention like `DEVICE_EDGE_SECRET_<EQUIPMENT_ID>`
with no registry field — needs no schema change, but it hides the wiring: you
cannot tell from `equipment.yaml` whether a device expects edge identity at
all, which is exactly the question that took a day to answer.

### Operational note: the edge secrets are world-readable

`systemctl show caddy.service -p Environment` prints every `Environment=` value
to **any local user** — no root, no journal access. All three edge secrets are
readable that way today (confirmed as `sdl2`, 2026-08-12). Anyone with a shell
on the dashboard host can therefore mint trusted-edge headers for those devices
and act as any user, un-audited.

Note the drop-in holding them is already `600 root:root` — file permissions do
not help, because systemd re-publishes `Environment=` values through that
property regardless. `caddy run --environ` additionally prints them into the
journal at every start.

That is a smaller hole than it sounds while shell access to this host is
already equivalent to lab control — but it converts "has a shell" into "can
impersonate a named operator in the audit trail", and it is free to close.
Step-by-step migration to an `EnvironmentFile` (plus dropping `--environ`) is in
[`deploy/README.md`](../deploy/README.md) → *Secrets in service environments*.

## Data isolation (requirement 4)

Experiment data becomes **project-scoped**; lab telemetry stays public. Data is
owned by the **project** it was produced under (and thus the project's PIs), not
by the individual creator — a user *generates* data, the project's PIs *own* it.
(Revised 2026-06-30 from an owner=creator + platform-admin model to this
project model, to match the AnaliticaDB catalog; the two share one policy.)

- **Stamp at creation.** Each experiment-data record stamps `creator` (the
  authenticated principal that produced it, from `X-Auth-User`) and `project`
  (the project it belongs to). A project has one or more PIs (owners), declared in
  `roster.yaml`; a user may be a member of **many** projects.
- **Identity-aware reads.** Read endpoints require identity and filter via the
  seam below: a caller reads a record iff they are a **global admin**, a **PI of
  the record's project** (owner), or an **active member of the record's active
  project**. Platform-admin is a hardware/operational role — **not** a data
  reader; data scoping is by project, not platform.
- **No per-datum sharing.** To grant access, add the person to the project (a
  PI/admin edits `roster.yaml`). No `shared_with` list or share-approval workflow.
- **One policy seam.** `can_read(project, caller) -> bool`, centralized and
  unit-tested (same discipline as `effective_device_role`). The caller scope
  (`member_projects` / `pi_projects` / `is_admin`) comes from `ac_auth`'s
  `data_scope` — surfaced by `GET /authz/scope` and the `X-Auth-Projects` /
  `X-Auth-Pi-Projects` headers on `/auth/verify`.
- **Scope is experiment data only.** Uptime, equipment state / errors / events,
  and environmental sensors are lab-wide situational awareness and **stay public**
  — that is the dashboard's purpose. Only scientific results are project-scoped;
  the project-scoped store is **AnaliticaDB**. In `lab.db` that means at most the
  per-well/result *values* if those count as experiment data — otherwise `lab.db`
  needs no gate.
- PIs (owners) retain export/delete rights to their projects' data; deletions
  are audited.

## Assistant chat (read surface — inherits auth + data scope)

The dashboard's chat bubble (`api/app/assistant.py`) shells out to the `claude`
CLI with the read-only `lab-history` MCP tools. It is **stateless**:
`--no-session-persistence`, a fresh agent loop per request, the transcript held
in the **browser** and re-sent each turn (max 40 messages); the server stores no
conversation, and it runs under **one shared** Claude Code OAuth login (not
per-user). It cannot actuate hardware (`--allowedTools mcp__lab-history__*`) but
it **can read all lab history**.

As of 2026-08-11 the bubble also has a **Control** mode (UI_DESIGN §5 Step 1).
It still **cannot actuate hardware** — the model never holds an actuating tool.
Control mode adds a second, **propose-only** MCP server (`lab-control`,
`--allowedTools mcp__lab-history__* mcp__lab-control__*`) whose `propose_action`
returns a *validated proposal object*; the hardware POST happens later, on the
operator's *Authorize* click, over the normal `/control/*` passthrough (which
already enforces identity, per-equipment authorization, the claim dance, and the
audit row). See requirement 3 below.

Three requirements follow:

1. **Gate the chat behind auth.** ✅ *(Phase 2, 2026-07-03)* `/api/assistant/*`
   joins the authenticated routes via the same Next.js middleware as control
   (every method, `/health` liveness exempt; `DASHBOARD_CONTROL_OPEN` dev
   escape hatch applies). The verified `X-Auth-User` is injected and logged per
   chat request (the backend Claude account is shared, so attribution lives at
   the dashboard, not the model).
2. **It must inherit the data-isolation policy, not bypass it.** The assistant
   reads the **same** history DB as the REST endpoints via the MCP tools. Once
   *Data isolation* lands, those tools **must apply `can_read` for the
   authenticated user** — otherwise a user could simply *ask* the assistant for
   another user's runs and get what the REST API would deny. The assistant is a
   read surface subject to the identical auth + ownership scope; it is **not** a
   side-channel around it. (Implementation: pass the requester identity into the
   MCP server and filter results — rides Phase 5.)
3. **Control mode binds authority to the tool, not the prompt.** ✅ *(2026-08-11)*
   Control mode is honoured only for a verified `X-Auth-User` and **never** under
   the `DASHBOARD_CONTROL_OPEN` dev bypass (which has no identity to bind a
   proposal to). `assistant.py` re-resolves the actor server-side and passes it
   to the `lab-control` server in its **environment** (`LAB_ACTOR`) — never as a
   tool argument the model could choose, so it cannot borrow another principal's
   authority. `propose_action` re-checks `operator`+ on the *target* equipment
   via the same `GET /authz/check` sidecar the passthrough uses, failing closed
   on a missing role or an unreachable sidecar. The authorizing click carries
   `X-Control-Origin: assistant`, recorded on the `control_action` audit row, and
   the proposal itself is journaled as an `assistant_proposal` event — so the
   trail shows both the click and what proposed it.

**Memory:** keep it **stateless for now** — no conversation content at rest (chats
can carry sensitive lab detail); the browser-held transcript suffices for a
glance panel. *If* persistent per-user memory is wanted later, store it as
**owner-private runtime data in SQLite** under the same `can_read` policy (owner +
admin; user can clear; bounded retention) — **never** in `roster.yaml`, which is
config, not user data.

## Automation accounts & approval (requirement 5)

Automation accounts are `users` rows with `is_automation=true`, authenticated by
an `api_key`. Two distinct controls:

- **Approval (creation).** A new automation account is created `status=pending`;
  **its API keys are inert until a global admin approves it.** Approval sets it
  `active`, assigns the **platform-scoped** `automation` grant, and sets an
  **expiry** (reuse `expires_at`; a time-boxed campaign principal that needs
  re-approval beats a forever robot key).
- **Usage (invocation).** Who may launch a campaign that runs *as* the automation
  principal: **a global admin, or any human with `operator`+ at that platform's
  scope.** The automation account is "the platform's robot identity," so being
  authorized to operate the platform earns the right to drive its automation.
- **Keep the human in the trail.** The device sees `owner=automation@<platform>`
  (so reserved-tray + workflow-lock work), but the run records
  `launched_by=<the human>`. Never "the robot did it" with no human attached.
- **It is a privilege escalation by design** — a platform-`operator` who launches
  the principal transitively gains automation capability they don't hold directly.
  That's intended; if it ever needs tightening, add a small "may launch
  automation" flag on the grant rather than implying it from `operator`.

A standing **platform** automation principal (auto-seeded, approved once) covers
routine ops; optional ephemeral **per-campaign** accounts give a tighter blast
radius for one-off agent runs.

---

## Storage model — config in YAML, runtime in SQLite

The biggest correction over earlier drafts: the **allow-list is configuration,
not runtime state**, so it lives in a **YAML file** (a gitignored `roster.yaml`,
with a committed redacted `.example`), not a database table. SQLite is kept only
for what the service must write on every request and hash at rest.

| Data | Examples | Home | Who writes it |
|---|---|---|---|
| **Declared config** | the allow-list (email, role, name, lab_account, notes, expiry, status); later: grants + platform↔equipment membership | **`roster.yaml`** (gitignored; `roster.yaml.example` committed) | humans, via an editor |
| **Runtime state / secrets** | `sessions`, `login_codes`, `api_keys`, `audit_log` | **SQLite (WAL)** | the service only — never hand-edited |

This matches the repo's config-as-YAML convention (`equipment.yaml`,
`platforms.yaml`). The **real `roster.yaml` is gitignored** — it carries real
emails — and a redacted **`roster.yaml.example`** is committed as the documented
template (mirroring `credentials.env` and the device repos' `config.example.toml`).
Sessions / one-time codes / API keys **cannot** be YAML —
they are minted and looked up on every request and stored hashed; a file the
service rewrites on every login would race and leak. Runtime "user facts"
(`last_login_at`, "verified") are **derived** from the sessions table (e.g.
`MAX(created_at)` per email), not stored as config — so no per-user runtime table
is needed.

### `roster.yaml` — the source of truth

Default path `auth/roster.yaml` (override with `AUTH_ROSTER_PATH` to keep it
outside the repo entirely). The real file is **gitignored**; start from the
committed template:

```
cp auth/roster.yaml.example auth/roster.yaml     # then edit
# or capture a live DB's allow-list:
python -m ac_auth.cli export -o auth/roster.yaml
```

`roster.yaml.example` carries fake accounts, is kept valid (so it passes
`validate` in CI), and doubles as the field-by-field reference for the format.

```yaml
# roster.yaml — the authoritative allow-list. Edit → validate → commit → reload.
# SQLite never holds the allow-list; API-key secrets are NEVER in this file.
users:
  - email: yangcyril.cao@utoronto.ca
    role: admin                       # admin | operator  (operator == today's "user")
  - email: felix.katzenburg@utoronto.ca
    role: operator
    name: Felix Katzenburg
    # lab_account: "…"   notes: "…"   expires: 2026-12-31   status: active
automation:                           # machine principals (API-key auth)
  - email: hte-orchestrator@lab.local
    name: HTE platform principal
    approved: true                    # global-admin approval gate (requirement 5)
    # platform: hte   expires: 2026-12-31   (when the hierarchy lands)
```

**Platform access (Phase 1) is additive and reuses existing config.**
Platform↔equipment **membership already exists in `platforms.yaml`** (each section
lists its equipment; shared equipment is already supported), so the auth model
**reuses it** — `roster.yaml` adds only *per-user grants* that reference a
platform/equipment key. Granting platform access is then a user entry like:

```yaml
  - email: yimeng@…
    grants:                       # Phase 1; omitted = today's flat global role
      - { scope: platform, id: hte, role: operator }              # all HTE devices
  - email: felix@…
    grants:
      - { scope: equipment, id: ot2_hte, role: operator }             # ONLY this device
```

**Three grant scopes:** `global` (everywhere), `platform` (every device the
platform contains, via `platforms.yaml` membership), and `equipment` (one
device). `operator` may be granted at any scope; `admin` only at `global`/
`platform`. **Per-equipment authorization** is thus a first-class case: grant
someone `operator` on just the devices they should touch.

**Grants are additive (highest wins), not deny rules.** Effective role on a
device = the highest of the flat global role + applicable global/platform/equipment
grants. The flat `role` is itself an implicit *global* grant, so to **restrict**
someone to specific equipment set **`role: none`** (no global access) and grant
only the scopes they should reach — e.g. `role: none` + `{scope: equipment, id:
ot2_hte, role: operator}` = "may operate only the OT-2." A `none` account can still
sign in (it's on the allow-list) but is **excluded from the roster of any device
it has no grant for**, and `/authz/check` returns `allowed: false` there. There
is deliberately no "deny one device out of a platform" primitive (negative grants
would be a future, explicit addition); use narrow grants instead.

**Lockout protection counts global admins only.** At least one active account must
be a global admin — a flat `role: admin` **or** a `{scope: global, role: admin}`
grant. A platform/equipment admin grant does *not* satisfy it (only a global admin
governs the allow-list). `load_roster` refuses a roster that would leave none.

**Adding a new platform or per-equipment grant is fully additive:** add/extend a
section in `platforms.yaml` (the normal dashboard process) and grant people in
`roster.yaml`. **No schema migration, no code change** — `effective_device_role`
already takes `equipment_key` and resolves through membership. So we should *not*
build relational `authorizations` / `platforms` / `equipment` tables; reviewed
YAML + the existing `platforms.yaml` do the job.

### SQLite runtime tables (service-written, never hand-edited)

| table | key columns | notes |
|---|---|---|
| `sessions` | token_hash (PK), email, created_at, expires_at | opaque tokens stored hashed → revocable; `last_login_at` derives from here |
| `login_codes` | id, email, code_hash, expires_at, attempts, used, created_at | email one-time codes: single-use, short TTL, attempt-capped, **send-rate-limited** |
| `api_keys` | id, email, key_hash, label, expires_at, revoked | machine-principal secrets (email must resolve to an `approved` automation entry in `roster.yaml`); **secret never in YAML** |
| `audit_log` | id, ts, actor, action, scope, target, **launched_by**, detail (json), ip | meaningful actions + denials only — see *Audit policy* |

Experiment-data **ownership** (`owner` on `runs` / results / artifacts) lives in
the **history DB** (`data/lab.db`, owned by `api/`), not the auth DB — `roster.yaml`
holds identity + grants; `api/` holds the data and enforces `can_read`.

### Safety — how an accidental edit is contained

A file-based allow-list is *safer* than the DB because these are enforced **in
code** (git-independent), so a bad edit is caught before it can affect the running
service:

1. **Schema validation (pydantic).** Every entry is typed — email shape, `role ∈
   {operator, admin}`, parseable `expires`, unique emails. A malformed file is
   rejected as a whole; never half-applied.
2. **Fail *closed* at startup, *keep-last-good* at reload.** A missing/invalid
   roster on cold start **refuses to boot** (loudly) — it must **never** fall back
   to empty-or-allow-all. On a live **reload**, an invalid new file is **logged
   and ignored** and the service keeps serving the **last-good** roster — a typo
   can't take auth down or lock everyone out.
3. **Invariant guards beyond schema.** Refuse to apply a roster with **zero
   active admins** (lockout protection), or — on reload — one that
   removes/disables **more than N accounts** vs the loaded set (mass-paste guard)
   unless explicitly forced.
4. **`validate` command + hooks.** `python -m ac_auth.cli validate <file>` runs
   schema + invariants (exit non-zero on failure). A systemd **`ExecStartPre`**
   validates the live `roster.yaml` so a broken file can't boot the service, and a
   **git pre-commit hook** validates the committed `roster.yaml.example` so the
   template never rots.
5. **Backup, not git (the real file is gitignored).** Because `roster.yaml`
   carries real emails it is **gitignored**, so git is *not* its history by
   default — keep a backup (a periodic `export` snapshot, or a private VCS) if you
   want diff / blame / revert of the live allow-list. **Accident-protection does
   not depend on this:** guards 1–4 are enforced in code regardless, and grant
   changes land in `audit_log` (Phases 1/6).
6. **Explicit reload** in prod (restart or SIGHUP), not on-save — editing the file
   mid-thought doesn't hit the running service until you choose.

---

## The `/auth/*` namespace — providers vs shared machinery

The auth surface is organized around one split, so that adding a login method
(Google, UofT) never touches session handling or the per-request gate:

- **Provider login flows** — *prove an email, then mint a session.* Each provider
  (email-code now; Google / UofT OIDC later) is a pluggable front-end whose only
  job is to produce a **verified email**.
- **Shared session machinery** — *consume a session.* Provider-agnostic: issue /
  validate / revoke the `ac_auth_session` cookie, and the per-request check the
  edge calls on every protected request.

Every provider converges on **one internal seam**: `mint_session(verified_email)
→ Set-Cookie`, which does the **roster gate** (allow-listed? active? not
expired?) + session row + cookie. The corollary is the load-bearing one for the
"public dashboard" question: **OIDC does not bypass the allow-list.** A valid
Google/UofT account is *authentication* only; if the email is not in
`roster.yaml`, `mint_session` refuses (403). No self-registration, regardless of
provider — same as the *Going public* section's "after auth means after an admin
authorized you."

| Category | Route | Method | Status | Notes |
|---|---|---|---|---|
| **email — start** | `POST /auth/login` | POST | now | `{email}` → email a one-time code (403 if not allow-listed / 429 if rate-limited) |
| **email — complete** | `POST /auth/verify-code` | POST | now | `{email, code}` → `mint_session` → session cookie |
| **google — start** | `GET /auth/google/login` | GET | later | 302 → Google; stash state / nonce / PKCE |
| **google — complete** | `GET /auth/google/callback` | GET | later | exchange code → verified email → `mint_session` |
| **uoft — start** | `GET /auth/uoft/login` | GET | later | UofT Entra / Shibboleth OIDC |
| **uoft — complete** | `GET /auth/uoft/callback` | GET | later | → `mint_session` |
| **shared** | `GET /auth/verify` | GET | now | forward-auth: the edge calls it every protected request → 200 + `X-Auth-*` / 401 |
| **shared** | `POST /auth/logout` | POST | now | revoke session + clear cookie |
| **shared** | `GET /auth/me` | GET | now | session introspection (soft; never 401) |
| **shared** | `GET /auth/users` | GET | now | login-dropdown list as `{id, name, role}` — **no email** (see below) |

The naming reads as **start / complete per provider**:

- **start:** `/auth/login` · `/auth/google/login` · `/auth/uoft/login`
- **complete:** `/auth/verify-code` · `/auth/google/callback` · `/auth/uoft/callback`
- **shared consume:** `/auth/verify` (forward-auth) · `/auth/logout` · `/auth/me` · `/auth/users`

Email is the **unnamespaced default provider**: `POST /auth/login` +
`POST /auth/verify-code`. `verify` (GET, forward-auth) and `verify-code` (POST,
email complete) are a deliberately method-split pair — the string proximity is
harmless because they answer different verbs and always have.

The **authorization plane** — `/authz/check`, `/authz/scope`, `/authz/mine`,
`/authz/matrix`, `/equipment/{key}/roster` — is a *separate* namespace (what you
may do, not who you are) and is unaffected by provider changes.

**Login dropdown — names, not emails (privacy).** On a public login page,
returning raw emails would let anyone reaching it enumerate every lab member's
address (network tab). So `GET /auth/users` returns `{id, name, role}` with **no
email**: `id` is an opaque, stable, non-reversible handle
(`sha256("login:"+email)[:16]`) used only as the dropdown's option value; `name`
is the roster display name (falling back to a masked address like `b…@utoronto.ca`
only if a user has none). The dropdown defaults to a blank "Select your name…"
so no identity is pre-revealed. The client then sends that `id` to `/auth/login`
and `/auth/verify-code`, which resolve `id → email` **server-side** by scanning
the roster — a raw address never reaches the browser. (Both endpoints still accept
`{email}` for CLI/back-compat.)

**Service lives on the Tailnet; the surface lives on the public edge.** The
sidecar runs at `100.64.254.6:8009` (Tailnet, loopback refused). But the `/auth/*`
**URLs** must be served from the single public edge origin, because (a) OIDC
`redirect_uri`s must be public HTTPS — Google/UofT can't redirect to a `100.x` IP
or `http://`, so `redirect_uri = https://<public-domain>/auth/google/callback`,
which Caddy reverse-proxies to `:8009`; and (b) the session cookie must be set on
one real registrable domain (not `ts.net`, not a bare IP) to give SSO. This is the
same "edge must be one origin" requirement in *Why sessions can't be shared
per-host* — the namespace and the single-edge move are the same move.

**Config the OIDC providers will need** (mirrors the email settings in
`config.py`; a provider is "enabled" iff its config is present, and `/auth/me` can
advertise `enabled_providers` so the login UI shows the right buttons):

```
AUTH_PUBLIC_BASE_URL                 # e.g. https://dashboard.<domain> — builds absolute redirect_uris
AUTH_GOOGLE_CLIENT_ID / _SECRET      # + optional hosted-domain (hd) restriction
AUTH_UOFT_ISSUER / _CLIENT_ID / _SECRET   # UofT Entra / Shibboleth OIDC
```

> **Rename shipped 2026-07-07:** `POST /auth/request-code` → `POST /auth/login`
> (sidecar `main.py`, the `web/src/app/api/auth/login` proxy route, and
> `user-auth.tsx`). No back-compat alias — every caller is in-repo and deploys
> together; only `/auth/verify` sits on the per-request path and it was untouched,
> so live sessions ride straight through. The Google/UofT rows are the reserved
> shape, not yet built.

---

## Authentication (account-based; public edge)

**Email one-time code (passwordless) — the chosen mechanism, implemented.**
1. User enters their allow-listed email.
2. The service emails a short-lived single-use code (6 digits, ~10 min,
   attempt-capped) **via Gmail** (`ac_auth.smtp_mailer`, App Password set up by
   `python -m ac_auth.setup_gmail`). No Microsoft/Graph sending path.
3. User enters the code → opaque session cookie. No password stored.

Single-factor (inbox possession), acceptable behind the allow-list; `utoronto.ca`
inboxes sit behind UofT Duo in practice.

**Send-rate limiting (implemented).** `/auth/login` throttles code emails
**per target address** so nobody can flood a real user's inbox: a **cooldown**
between sends (`AUTH_CODE_RESEND_COOLDOWN_S`, default 60 s) and a **rolling-hour
cap** (`AUTH_CODE_MAX_PER_HOUR`, default 5). Over either, **429 + `Retry-After`**
and nothing sent. Computed from `login_codes` history; applies only to
allow-listed emails (unknown → 403, no send). Verify keeps single-use + attempt
cap (`AUTH_CODE_MAX_ATTEMPTS`). **Per-IP** limiting belongs at the **Caddy edge**
(`rate_limit` on `/auth/*`) once public — an off-Tailnet client's real IP only
arrives via the proxy.

**Machine principals:** API keys (inert until approved; see *Automation*).

**Sessions:** opaque random tokens, stored hashed, absolute + idle expiry,
server-side revocation (logout / disable / expiry all revoke). Preferred over JWT
here: trivial revocation, no key management, central is already on the path.

**Future providers (namespace reserved, not yet built):** Google and UofT
(Entra / Shibboleth OIDC + Duo) — front-door swaps that touch none of the model
below; each plugs in behind the `/auth/{google,uoft}/{login,callback}` routes and
ends at the shared `mint_session` seam (see *The `/auth/*` namespace*). Deferred
on the UofT app registration; the shape is in place so adding one is additive.

### Why sessions can't be shared per-host — the edge must be one origin

A session cookie is scoped to the origin that set it and **cannot be shared
across the lab's device hosts as they are addressed today.** Two independent
browser rules kill the "one sign-in, every device UI" shortcut, so a shared
parent-domain cookie is not an option over either addressing scheme:

- **Raw `100.x` Tailnet IPs** — RFC 6265 forbids a `Domain` attribute on an
  IP-literal host, so a cookie set on `100.64.254.100:8000` is *host-only* and
  never travels to another IP or to a hostname.
- **MagicDNS `*.tail….ts.net`** — `ts.net` is on the browser Public Suffix List
  (Tailscale registered it so tailnets are origin-isolated), so Chrome/Safari
  **silently drop** any `Domain=tail….ts.net` cookie. A per-device UI that tries
  to set a tailnet-wide session cookie gets no cookie at all. *(Confirmed live
  2026-07-06 on the xArm's own auth banner: `/auth/verify-code` returned 200 but
  the session never stuck; the fix was to leave the cookie host-only — sign in
  once per device host. `localStorage`/bearer schemes don't help — they are
  origin-partitioned the same way.)*

So a device UI hosted directly on its own `<host>:<port>` forces a **separate
login per origin** (the interim state on the xArm today). **This is the concrete
reason the single public Caddy edge is required for SSO, not merely cleaner:**
collapsing every UI behind one origin (path-routed) yields one cookie, set on a
single registrable public domain (*not* `ts.net`), shared across the whole
dashboard — genuine single sign-on, and the same move that closes the
direct-device side-door (Phase 4). Per-host device UIs and a shared session are
mutually exclusive; the edge is what reconciles them.

## Auth flows

- **Human login:** `POST /auth/login {email}` → (if allow-listed &
  un-expired & rate ok) email a code → `POST /auth/verify-code {email,code}` →
  session cookie. Then Caddy `forward_auth` → `/auth/verify` → inject
  `X-Auth-User` / `X-Auth-Role`.
- **Machine:** `X-Api-Key` → principal (must be approved + un-expired).
- **Acting on a device:** the platform resolves the user's effective role for that
  equipment, then **claims** on the device as `owner=<username>`; the device
  re-checks its roster (role) + enforces the claim (defense-in-depth) and stamps
  `claimed_by.owner`.
- **Launching automation:** a platform-`operator`+ (or admin) starts a campaign
  under the platform's automation principal; the run records `launched_by`.
- **Peer platform / device:** `GET /authz/check?user=…&equipment=…&action=…`, or
  the injected `X-Auth-*` headers from the shared edge.

## First-admin bootstrap & CLI

Trust anchor = **shell access to the server**. Run once:

```
python -m ac_auth.cli add-user you@utoronto.ca --role admin   # the bootstrap
```

**Post-Phase-0, the allow-list is edited as a file, not via mutating commands** —
the workflow is *edit → validate → reload* (the real `roster.yaml` is gitignored,
so there is no commit step for it; back it up separately if you want history):

```
cp auth/roster.yaml.example auth/roster.yaml   # first time only
$EDITOR auth/roster.yaml                        # add/change an entry
python -m ac_auth.cli validate auth/roster.yaml # schema + invariants (≥1 admin, …)
sudo systemctl reload ac-organic-lab-auth       # or restart; keep-last-good on failure
```

`expires: YYYY-MM-DD` lapses an account at 23:59:59 UTC of that day (refused at
login like `status: disabled`). The CLI keeps **`validate`** (systemd
`ExecStartPre` + the `roster.yaml.example` pre-commit hook), **`export`** (dump
the live state to roster format — the way to capture/back-up the gitignored file),
and the machine-principal secret commands **`issue-key`** / **`revoke-key`**
(these write the SQLite `api_keys` table — secrets are never in the YAML). Grants
+ platform/equipment membership are edited directly in `roster.yaml` (Phase 1a/1b),
not via new CLI verbs.

## Service / API surface

Human endpoints at the **public edge** (Caddy); device-plane endpoints
(`/authz/check`, `/equipment/{key}/roster`) are Tailnet-only.

- `GET  /auth/verify` — forward-auth (session cookie or `X-Api-Key`).
- `POST /auth/login`, `POST /auth/verify-code`, `POST /auth/logout`,
  `GET /auth/me`, `GET /auth/users`. (Provider login flows + shared session
  machinery — see *The `/auth/*` namespace*; `/auth/google/*` + `/auth/uoft/*`
  are the reserved OIDC shape.)
- `GET  /authz/check` — peer/device authorization probe (**implemented**, Phase 1a):
  `?user&equipment` → `{allowed, role, central_role}` via the grant resolver.
- `GET  /equipment/{key}/roster` — **scope-filtered** owner→role projection a
  device pulls (+ a `/platform/{key}/roster` for a multi-device gateway).
- Admin: users CRUD, **grants CRUD** (scope+role), API-key issue/revoke,
  **automation approve**, platform/equipment registration, and an **access
  matrix** view (users × equipment → effective role) — the human-readable
  "clear definition" of requirement 1.

## Security considerations

**Public-exposure hardening (the dashboard is internet-facing):**
- **TLS** (Caddy → Let's Encrypt; HSTS).
- **Cookies:** `HttpOnly`, `Secure`, `SameSite=Lax/Strict`; short idle expiry.
  (`AUTH_COOKIE_SECURE` flips on in prod.)
- **CSRF** on state-changing requests; CORS locked to the dashboard origin.
- **Login abuse:** per-email send throttle (done) + edge per-IP rate-limit +
  lockout/backoff once public.
- **Email verification / MFA:** email-code is single-factor; an OIDC front door
  would add real MFA for admins if ever wanted.

**Core:**
- **Roster integrity (fail closed).** `roster.yaml` is schema- + invariant-
  validated; **invalid on cold start → refuse to boot** (never empty/allow-all),
  **invalid on reload → keep last-good**. Guards: ≥1 active admin, unique emails,
  mass-change cap. Enforced in code *and* at the git pre-commit hook + systemd
  `ExecStartPre`. See *Storage model → Safety*.
- **Session revocation** on logout / disable / expiry (hashed token store).
- **SQLite:** `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, **one writer
  process**; keep DB access behind a repository layer so a Postgres swap is cheap.
- **Revocation propagation:** disabling a user / revoking a grant must drop them
  from rosters on the next pull (≤ roster-cache TTL), revoke their sessions, and
  (decision) optionally force-release their active claims.
- **Audit policy:** audit **meaningful state changes and authz denials only** —
  never the high-frequency `/auth/verify`. Audit: login/logout, claim,
  `run.submit`, abort/cancel, service-mode toggle, `workflow.start`/`end`, user
  create, grant/revoke, automation approve, **data-access denials**, and the
  **effective role + `launched_by`** for each control action. Escape hatch if
  volume grows: a separate SQLite file for `audit_log`, or stream to journald.
- **SPOF:** central is on the auth path for all platforms → short session caching
  at platforms + device-side roster fallback for degraded-mode operation.

---

## Phasing / rollout (each phase reversible, smallest-change-first)

**DONE (v1, in `auth/ac_auth`):** SQLite store (users / login_codes / sessions,
WAL), Gmail mailer + App-Password setup, email-code routes
(login / verify-code / verify / me / logout / users), allow-list CLI,
**account-management columns** (name / lab_account / notes / expires_at +
**login expiry enforcement** / last_login_at / email_verified / disabled
metadata), and **per-address send-rate limiting**. Tested.

**DONE (roster-projection authZ layer — interim flat, hierarchy-shaped):**
machine-principal / API-key accounts (`is_automation`, `api_keys`); the resolver
seam `effective_device_role`; `GET /equipment/{key}/roster`; owner-stamping in
`api/app/control.py` (`X-Auth-User` into the device claim + audit row); CLI for
automation accounts + keys.

**DONE (Phase 0 — allow-list → `roster.yaml`; shipped + deployed 2026-06-27):**
`ac_auth/roster.py` (pydantic models; `load_roster` fail-closed; `reload_roster`
keep-last-good + mass-change guard; `dump_roster`); the service read path now
resolves identity from the roster (`main.py` `_lookup_principal` /
`_active_principals`), `db.verify_api_key` returns an email resolved against the
roster, and `last_login_at` derives from the `sessions` table (no `touch_login`);
SIGHUP hot-reload + fail-closed startup load; CLI reduced to `validate` / `export`
/ `list-users` (roster-read) / `issue-key` (approval-gated) / `*-key`; the live
`roster.yaml` is **gitignored** with a committed `roster.yaml.example`; both
systemd units carry `ExecStartPre` validate + `ExecReload` (SIGHUP); committed
`auth/hooks/pre-commit`. 56 auth tests pass; verified live on the dashboard host.
The SQLite `users` table/methods remain in `db.py` but are **vestigial** (used
only by `export`); drop in a later cleanup.

**DONE (Phase 1a — per-scope grant *resolution* + elevation; committed 2026-06-28, deployed 2026-07-03):**
`Grant` model (`scope` global/platform/equipment, `id`, `role`) on roster users;
`ac_auth/platforms.py` loads `platforms.yaml` → `equipment_key → {platform_id}`
membership (fail-soft → `{}`); `authz.effective_central_role` /
`effective_device_role` now resolve the **highest applicable** of the flat global
role + grants (global / platform-via-membership / equipment), so a
`platform`-scoped `admin` grant elevates `operator`→`service` on that platform's
devices only. New `GET /authz/check?user&equipment` probe. Backward-compatible:
with no grants it reduces exactly to the flat role (verified live).

**DONE (Phase 1b — per-equipment restriction; committed 2026-06-28, deployed 2026-07-03):**
`role: none` (no global access) lets grants *restrict* — a `none` account reaches
only granted equipment/platforms, is excluded from the roster of any device it
has no grant for, and `/authz/check` denies it there. `effective_*_role` return
`None` for no-access; the lockout invariant now counts **global** admins (flat
`role: admin` or a `{scope: global, role: admin}` grant), not platform/equipment
admins. 72 auth tests. Opt-in: only accounts that set `role: none` are affected.

**DONE (data-ownership projects + scope projection; committed 2026-06-30, deployed 2026-07-03):**
`RosterProject` (`id`, `name`, `status` active/closed, `pis` ≥1, `members`) on the
roster — the unit of **data** ownership, distinct from hardware grants: the PIs own
a project's data, members may read it while the project is active, and a user may
belong to **many** projects. `authz.data_scope(user) -> {member_projects,
pi_projects, is_admin}`, surfaced by `GET /authz/scope` and the `X-Auth-Projects` /
`X-Auth-Pi-Projects` headers on `/auth/verify`. This is the identity/scope source
the data-isolation `can_read` consumes (this service's `lab.db` reads **and** the
AnaliticaDB catalog — one scope, two enforcement points). 83 auth tests.

| Phase | Delivers | Behavior change |
|---|---|---|
| **0 ✅** | **Allow-list → `roster.yaml`** (source of truth); SQLite is runtime-only; `last_login_at`/verified derived; `roster.py` loader with **schema validation + fail-closed-startup + keep-last-good-reload + invariant guards (≥1 admin, mass-change)**; CLI `validate`/`export`; git pre-commit hook + systemd `ExecStartPre`/`ExecReload` | **shipped + deployed** — allow-list edits are file-based; auth behavior for users unchanged |
| **1a ✅** | Per-scope **grant resolution** (global/platform/equipment) via `platforms.yaml` membership; `admin` grants **elevate** (e.g. platform-admin); `GET /authz/check` | **deployed 2026-07-03** — none until grants are added (compat) |
| **1b ✅** | `role: none` (no global access) + grants-only → per-equipment **restriction**; non-granted accounts excluded from a device's roster + `/authz/check` denies them; lockout invariant counts **global** admins (flat or global-grant) | **deployed 2026-07-03** — only affects accounts that opt into `role: none` |
| **2 ✅** | Scope-filter the roster (landed with 1b); admin **access-matrix** view (`GET /authz/matrix`); **gate `/api/assistant/*` behind login** (Next.js middleware, `/health` exempt; `X-Auth-User` logged per chat) | **committed 2026-07-03** (needs api+web restart) — chat needs auth; reads scoped; control unchanged |
| **3** | Finish hard claim enforcement everywhere; device authorizes the claim against its roster (`operator`+). **Gateway half shipped 2026-07-03:** the dashboard passthrough checks `/authz/check` before the claim dance (403 + audit on denial, fail-closed if the sidecar is down; `CONTROL_AUTHZ_ENFORCE=false` dev hatch), and the UI disables unauthorized controls (`/authz/mine` → `canControl` → per-tile gating). Device-side roster authorization remains. | control needs grant **and** claim |
| **4** | **Close the direct-device side-door** (edge / loopback+proxy); claim owner provably = identity | the linchpin |
| **5** | `creator`+`project` stamping + identity-aware reads + `can_read(project, caller)` (**incl. the assistant's MCP reads**); operational telemetry stays public — the project-scoped store is **AnaliticaDB** | experiment data becomes project-scoped |
| **6** | Automation approval workflow (`pending`→approve, platform-scoped + time-boxed grants; `launched_by` audited) | automation gated |

**Phase 0** (storage refactor) is **shipped + deployed**. **Phases 1a, 1b, and
the data-ownership projects layer** are **deployed 2026-07-03** (service
restarted on the new code; `/authz/check` + `/authz/scope` answering) but
**dormant** — all three change nothing until grants/projects are added to
`roster.yaml` (`systemctl reload` hot-loads them). Next: populate roster
grants + the first project, then the cross-cutting infra (Phases 2–4): Caddy
`forward_auth` → `/auth/verify` (+ X-Auth-* strip/re-inject), edge TLS +
per-IP rate-limit, and the device-side roster pull in each device repo (e.g.
`agilent-hplcms-server`'s `control/roster.py`, today static env lists).

## Risks / arguments against (recorded)

1. **Direct-device control path is the real exposure** — until the side-door
   (Phase 4) closes, per-user authz + claim-binding + data isolation are advisory
   on the Tailnet. Sequence Phase 4 with (not after) 1–3 for any device that
   matters.
2. **Public login surface** — email-code is single-factor + leans on
   deliverability. → single-use short-TTL codes + attempt cap + send-rate limit
   (done) + edge per-IP limit; allow-list the Gmail sender via UofT IT if
   first-send quarantine bites.
3. **SQLite write concurrency** under multi-platform + audit. → WAL +
   `busy_timeout` + single writer + repo layer + Postgres path; separate
   `audit_log` file if needed.
4. **Central SPOF** → session cache + device roster fallback.
5. **Shared-equipment access leakage** — platform grants reach shared devices by
   construction; use equipment-scoped grants when that's too broad.
6. **Automation privilege escalation** — platform-`operator` launching the
   principal gains automation capability transitively; tighten with a per-grant
   "may launch" flag only if needed.
7. **Going public collapses the network layer** — a public dashboard removes
   Tailnet membership as a control-path layer, leaving the single-factor account
   as the sole perimeter. → keep `/control/* `+ cameras Tailnet-gated (expose
   only reads), or add MFA for control-capable accounts. Outsiders still can't
   self-serve (no self-registration), and the device side-door stays Tailnet-only.

## Open decisions

1. **Human authN:** decided & built — email one-time code (passwordless) via
   Gmail. The `/auth/*` namespace **reserves** Google + UofT OIDC as pluggable
   providers (see *The `/auth/*` namespace*); building them is deferred, not
   unplanned.
2. **Force-release on revoke:** when a grant/account is revoked, do we also
   force-release that principal's *active* claims, or only block new ones? (Lean:
   block new + let the claim lapse at its TTL; force-release only for admin
   disable.)
3. **Per-campaign automation accounts** in addition to the standing platform
   principal — build now or defer until an agent campaign needs it?

## See also

- `docs/STATUS_SPEC.md §4.11` & §5 — Tailnet-as-boundary posture; the claim protocol.
- `docs/ROADMAP.md` → *Control-surface exposure* — the direct-device side-door this design closes.
- `docs/EQUIP_GUIDE.md §6b` — the `CONTROL_PASSWORD` gate this replaces.
- `docs/LAB_MONITORING.md` — history DB schema that gains the `owner` column (data isolation).
- `api/app/control.py` — claim/heartbeat/release passthrough that stamps the real `owner`.
- Device side (`agilent-hplcms-server`): the roster + role gates (`user` / `automation` / `service`) this feeds.
