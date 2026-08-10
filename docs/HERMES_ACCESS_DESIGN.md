# Hermes access design — a platform agent, not a science agent

**Status:** design note, 2026-08-09. Phase 2 (the edge-path policy) is
**implemented** in `auth/`; Phases 0/1/3/4 are proposed and not yet done.

**The requirement, in the operator's words:** Hermes should *learn from platform
operation* and *help work on other devices over SSH so only the server needs
hands-on work* — but must not reach **project details, background, design, or
analysis**. Raw data is fine.

This note records what that boundary actually is, where it can be enforced, in
what order, and what was rejected. Companion to [`AUTH_DESIGN.md`](AUTH_DESIGN.md),
which owns identity and roles; this note owns only the agent-access question.

---

## 1. The principle

**Hermes gets the instrument and platform layer. It does not get the scientific
record.** It is an operations and coding agent; the science belongs to the ELN
and to specialist agents (LaAgenteAnalitica). This is
[`ARCHITECTURE.md`](ARCHITECTURE.md)'s division of labour expressed as access
control rather than as convention.

## 2. Where the line falls

Verified against the live services on 2026-08-09.

| System | Contents | Hermes |
|---|---|---|
| `lab.db` (dashboard `api/`) | uptime, `equipment_events`, sensors, dosing runs, `agent_observation` | **allow** — platform telemetry, no science. This is the corpus "learn from operation" needs |
| Device PCs over SSH | instrument output, service logs, NSSM/uv state | **allow** — pre-ELN raw output, and the ops surface itself |
| AnaliticaDB `:8010` — `/measurements`, `/files`, `/samples`, `/uploads/experiments` | raw data | **allow** |
| AnaliticaDB `:8010` — `/projects`, `/experiments`, `/plans`, `/analyses`, `/analysis-files`, `/notes`, `/agent-run-graphs` | project details, background, design, analysis, agent reasoning traces | **deny** |
| bitácora — `/projects/{id}/rooms/{id}/{designs,protocols,decisions,messages,pr,trace}` | the design-and-collaboration ELN, end to end | **deny** |

Two findings that corrected the first draft of this note, both from reading the
services rather than the docs:

- **bitácora has no data route.** Its entire API is nested under
  `/projects/{id}/rooms/{id}/…`. It *is* the design layer; nothing in it sits on
  the allowed side. So the boundary is "all of bitácora", not a cut through it —
  and it needs no rule, because §4.2's policy defaults to deny.
- **The cut through AnaliticaDB is real and clean.** Its 24 routes separate raw
  data from record along precisely the wording of the requirement.

### 2.1 Two leaks this design does not close

Recorded so they are accepted knowingly rather than discovered later.

- **Names encode design.** Sample ids and filenames routinely carry the
  experiment (`plate3_pd_ligand_screen_48h`), so reading `/measurements` and
  `/files` leaks *some* intent regardless of how the routes split. The
  mitigation is project-scoping the raw reads as well — which needs
  `can_read(project, caller)`, and that does not exist yet in either service
  (checked). Until it lands, this leak is open.
- **Working on the ELN is not reading the ELN.** `bitacora/.hermes/plans/`
  already exists — Hermes develops that repo today and should keep doing so.
  But developing means running it, seeding fixtures, reading logs, all of which
  drift toward production records. **Rule: Hermes may hold the ELN's source,
  never its production credentials.** Dev work runs against a dev database.

## 3. The finding that sets the order

**An edge policy cannot constrain Hermes while Hermes runs as `sdl2`.**

Both record services are gated at the single Caddy edge (`/etc/caddy/Caddyfile`,
live): `/analytica/*` is `forward_auth`'d to ac_auth and proxied to `:8010` with
a shared `X-Edge-Secret`, while bitácora is loopback-bound and trusts
`X-Auth-User` from that same edge. The edge is the only *intended* path in.

But **six `.env` files under `~/caoyang/` are readable by `sdl2`** and carry that
edge secret — one of them at mode `644`. Anything running as `sdl2` with a shell
can therefore talk to `:8010` directly, present the secret, and bypass the edge
and every policy attached to it.

So the path policy is necessary but **not sufficient**, and its precondition is
that Hermes cannot read the secret. That is why the separate OS user is Phase 0
rather than later hardening: doing the policy first would produce a boundary
that *looks* enforced and is not — the same failure mode as a `/status`
endpoint that reports `ready` without checking anything.

This is also the moment `pypoe/CLAUDE.local.md` D2 reserved: *"revisit before
any write-capable skill hits prod."*

## 4. Phases

### Phase 0 — a `hermes` OS user (prerequisite; needs root)

**Two installs, split by attendance — not one replacing the other.** The line
that matters is *attended vs unattended*, not which binary is on disk:

| Invocation | Runs as | Rationale |
|---|---|---|
| A human driving Hermes on the repos | `sdl2` | It executes with privileges that human already holds, with them present to approve. Boxing it buys nothing and costs the tool they actually use. |
| Timers, webhooks, alert triggers; anything ingesting device or ELN data unsupervised | `hermes` | No human in the loop, and the input may be attacker-influenced. |

Steps:

- Create `hermes` with its own home; **not** in any group that can read
  `~sdl2/caoyang/**/.env`, the Caddy config, or the lab service units.
- Tighten every `~/caoyang/*/.env` to `600` — load-bearing, see below.
- Grant traverse-only access so the boxed account can reach the repos without
  reading `/home/sdl2` itself: `setfacl -m u:hermes:--x /home/sdl2`.
  **Order matters**: this grants the traversal that makes a `644` `.env`
  readable, so the `chmod` must come first. (Before this ACL, `/home/sdl2`
  being `700` was the only thing protecting those files — a single bit with
  nothing behind it.)
- Copy `~/.hermes` to `/home/hermes/.hermes` and repoint the venv's hardcoded
  paths. `PATH` separates the two without further work: `sdl2` resolves
  `~/.local/bin/hermes`, `hermes` resolves `/usr/local/bin/hermes`.
- **Verify by failing:** as `hermes`, the edge secret is unreadable and a direct
  `:8010` request is refused, while the repos remain readable. A phase that
  cannot be verified by a failing probe is not done.

**The discipline this replaces deletion with:** "interactive" is a property of
*how Hermes is invoked*, not of the install — `hermes cron`, `serve`, `gateway`,
`--yolo`, and delegation/spawn-trees all turn an interactive install into an
unattended one. So: **automated triggers are configured under `hermes`, never
under `sdl2`.** Re-checkable via `~/.hermes/cron`, `processes.json`, and
`systemctl list-unit-files | grep hermes` (all clear as of 2026-08-09).

**Consequence, accepted knowingly:** the §2 data boundary binds the `hermes`
principal only. A human driving Hermes interactively reaches project design and
analysis with their own credentials — correct, because that is a person with
access, not an agent acting alone. This design bounds *unsupervised* access.

### Phase 1 — a machine principal (configuration only)

ac_auth already has machine principals (`X-Api-Key`) and per-equipment grants,
so no new mechanism is needed:

- Add `hermes@lab.local` under `automation:` in `roster.yaml`, `approved: true`.
- Grant `analytica_db`. **Do not grant `bitacora_eln`** — that single omission
  denies the whole design ELN, with no code.

### Phase 2 — edge-path policy — **implemented**

Grants are **service-level**: a grant on `analytica_db` opens all 24 of its
routes, so Phase 1 alone would hand Hermes `/plans` and `/analyses` alongside
`/measurements`. Closing that is the only new code in this design.

- `roster.PathPolicy` — an optional `paths: {allow, deny}` block on a roster
  entry, both `RosterUser` and `RosterAutomation`.
- `authz.path_permitted(policy, uri)` — **deny wins, then allow, else refuse.**
- Enforced in `GET /auth/verify` against the `X-Forwarded-Uri` the edge already
  sends, returning 403.

Three decisions worth keeping:

- **Default-deny for unlisted paths.** A route added to AnaliticaDB later starts
  closed for path-scoped principals. Default-allow would silently widen every
  agent each time someone added an endpoint.
- **Fail closed when `X-Forwarded-Uri` is absent.** If we cannot tell what is
  being authorized, refuse — otherwise any caller reaching `/auth/verify`
  without the edge's header is unrestricted, and the policy is a suggestion.
- **Absent policy means unrestricted**, so every human on the roster today is
  unaffected and the change is purely additive.

Paths are normalised before matching — percent-decoded (repeatedly), `..`
collapsed, backslashes folded, query string dropped — because a naive prefix
match lets `/analytica/measurements/../plans` straight through. The test suite
pins each of those evasions.

### Phase 3 — access grants

- **`lab.db`**: read-only, and preferably through the existing read-only MCP
  servers (`pypoe lab-mcp`, dashboard `lab-history`) rather than a file handle —
  same data, already bounded and audited.
- **SSH to device PCs**: an sshd already listens on `sdl2-pc-03-cytation` (it
  answers `Permission denied (publickey,…)`, not connection refused), so this is
  key trust, not new capability. Prefer **Tailscale SSH** — authorization in the
  tailnet ACL, centrally inspectable, per-connection audit — over
  `authorized_keys` scattered across Windows boxes. Document in
  [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md), which mentions no SSH today.

### Phase 4 — learning policy

Per decision #11 and `AGENTS.md` §5, which already name Hermes:

- Repo-specific lessons → that repo's `AGENTS.md`, **as a reviewed diff**.
- Cross-repo/machine facts → `~/.hermes/memories/MEMORY.md`, *proposed for
  approval*, never silently written.
- The signal to learn *from* is `lab.db` via MCP (Phase 3) — no new plumbing.
- **Watch item:** Hermes also keeps a private `state.db` (56 MB) and a memory
  graph. Knowledge accumulating only there defeats decision #11's *no hidden
  state that silently steers agent behavior*; durable platform knowledge belongs
  in the committed files.

## 5. Decided against — do not relitigate

- **Enforce in Caddy matchers.** Scatters policy across a root-owned file that
  no test touches, and duplicates per service. ac_auth is already on the request
  path of every gated route and is roster-driven, so policy stays in the file
  humans already review.
- **Enforce inside AnaliticaDB.** Would have to be repeated in bitácora and in
  every future service, and leaves the direct-to-`:8010` bypass untouched
  anyway.
- **Per-grant path lists** (`grants[].paths`). Needs an equipment-id → edge-path
  mapping that ac_auth does not have and should not invent. A principal-level
  policy needs no such mapping and is easier to audit: one block, one answer.
- **Rebuilding PyPoe with Hermes' abilities.** Hermes is ~3,200 Python files to
  PyPoe's 51. The subset PyPoe actually wants (bounded tool-calling over its own
  read-only lab tools) is a few hundred lines and is already scoped in
  `pypoe/CLAUDE.local.md` §4.1/§4.5.
- **Making `hermes` the *only* way to run Hermes** (deleting the `sdl2`
  install). Rejected 2026-08-09: a human's interactive agent executes with
  privileges that human already has, under their supervision, so boxing it
  protects nothing and removes a working dev tool. The boundary is about
  unattended execution — see Phase 0.
- **Giving the alert-path agent a terminal.** Webhook-supplied device text is
  interpolated into the investigation prompt, the endpoint is unauthenticated,
  and the run is unattended. Terminal operations belong on an operator-initiated
  trigger behind the approval gate, never on that path.

## 6. Verification checklist

Each phase is done only when its **negative** test passes.

- [ ] P0 — as `hermes`: no `.env` under `~sdl2/caoyang` is readable; a direct
      `:8010` request without the edge is refused; the repos *are* readable; and
      no unattended trigger (cron / process / systemd unit) is configured under
      `sdl2`.
- [ ] P1 — as `hermes`: bitácora through the edge returns 403.
- [x] P2 — `/analytica/measurements` permitted; `/analytica/{plans,analyses,experiments,projects}`
      refused; an unlisted route defaults refused; traversal and encoding
      evasions refused; a missing `X-Forwarded-Uri` refused.
      *(33 tests in `auth/tests/test_path_policy.py`.)*
- [ ] P3 — `lab.db` readable; SSH to one device PC succeeds and is audited.
- [ ] P4 — a learned fact appears as a reviewable diff, not only in `state.db`.

## See also

- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — identity, roles, the `can_read` design
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — layering; decisions #10, #11
- [`DEVICE_PC_SETUP.md`](DEVICE_PC_SETUP.md) — device PC recipe (Phase 3)
- `AGENTS.md` §5 — the memory policy Phase 4 implements
