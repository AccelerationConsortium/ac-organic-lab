# Auth Design — identity, authorization, and data access

**Status:** v1 + **Phase 0 shipped** (`auth/ac_auth`: email-code login, sessions,
Gmail sender, send-rate limiting, and the **`roster.yaml` allow-list** — config in
a gitignored YAML with fail-closed validation, runtime-only SQLite). The hierarchy
/ per-equipment authorization (Phase 1+), data isolation, and automation-approval
layers below are designed but not yet built — see *Phasing*. Drafted 2026-06-23;
revised 2026-06-27 (role model simplified; equipment↔platform many-to-many;
data-isolation + automation-approval added; **allow-list moved to `roster.yaml`
— Phase 0, now shipped + deployed**).
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
   press, cytation, ot2; finish the rest.)
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

Two requirements follow:

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
      - { scope: equipment, id: ot2, role: operator }             # ONLY this device
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
ot2, role: operator}` = "may operate only the OT-2." A `none` account can still
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

### Current implementation (being migrated to `roster.yaml` — Phase 0)

Today the allow-list still lives in the SQLite `users` table (`ac_auth/db.py`):
email PK; `role` (`user`/`admin`, where `user` == target `operator`); `status`;
`is_automation`; and the account-management columns `name` / `lab_account` /
`notes` / `expires_at` (login-enforced) / `last_login_at` / `email_verified` /
`disabled_at` / `disabled_reason` / `created_at` (additive via `_migrate()`).
**Phase 0 moves these fields into `roster.yaml` and shrinks SQLite to the runtime
tables above;** `last_login_at` / `email_verified` become derived and the `users`
table is retired.

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

**Send-rate limiting (implemented).** `/auth/request-code` throttles code emails
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

**Optional future (not planned):** "Sign in with Microsoft" (Entra OIDC + Duo) —
a front-door swap that touches none of the model below; needs a UofT app
registration we chose to avoid.

## Auth flows

- **Human login:** `POST /auth/request-code {email}` → (if allow-listed &
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
(these write the SQLite `api_keys` table — secrets are never in the YAML). When
the hierarchy lands, grants + platform/equipment membership are added to
`roster.yaml`, not new CLI verbs.

> Pre-Phase-0 (today) the mutating CLI still exists — `add-user` / `set-user` /
> `disable-user` / `list-users` write the SQLite `users` table directly. They are
> replaced by the file workflow above once Phase 0 lands.

## Service / API surface

Human endpoints at the **public edge** (Caddy); device-plane endpoints
(`/authz/check`, `/equipment/{key}/roster`) are Tailnet-only.

- `GET  /auth/verify` — forward-auth (session cookie or `X-Api-Key`).
- `POST /auth/request-code`, `POST /auth/verify-code`, `POST /auth/logout`,
  `GET /auth/me`, `GET /auth/users`.
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
(request-code / verify-code / verify / me / logout / users), allow-list CLI,
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
| **3** | Finish hard claim enforcement everywhere; device authorizes the claim against its roster (`operator`+) | control needs grant **and** claim |
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
   Gmail. OIDC remains an optional, unplanned future front door.
2. **Force-release on revoke:** when a grant/account is revoked, do we also
   force-release that principal's *active* claims, or only block new ones? (Lean:
   block new + let the claim lapse at its TTL; force-release only for admin
   disable.)
3. **Per-campaign automation accounts** in addition to the standing platform
   principal — build now or defer until an agent campaign needs it?

## See also

- `docs/STATUS_SPEC.md §4.11` & §5 — Tailnet-as-boundary posture; the claim protocol.
- `docs/ROADMAP.md` → *Control-surface exposure* — the direct-device side-door this design closes.
- `docs/EQUIPMENT_INTEGRATION.md §6b` — the `CONTROL_PASSWORD` gate this replaces.
- `docs/OBSERVABILITY.md` — history DB schema that gains the `owner` column (data isolation).
- `api/app/control.py` — claim/heartbeat/release passthrough that stamps the real `owner`.
- Device side (`agilent-hplcms-server`): the roster + role gates (`user` / `automation` / `service`) this feeds.
