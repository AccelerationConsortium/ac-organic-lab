# Hermes access design — a platform agent, not a science agent

**Status:** design note, 2026-08-09. Phase 2 (the edge-path policy) is
**implemented** in `auth/`; **Phases 0 and 1 are implemented 2026-08-12** —
the `hermes` OS user (record at the end of its section) and the
`hermes@lab.local` roster principal (hot-reloaded and probe-verified:
`bitacora_db` allowed under the §2 path policy, `bitacora_eln` and all
hardware refused). Phase 3 is proposed and not yet done; **Phase 4 (the
learning policy) is drafted 2026-08-12** — rules 4.1–4.6 in its section.

**The requirement, in the operator's words:** Hermes should *learn from platform
operation* and *help work on other devices over SSH so only the server needs
hands-on work* — but must not reach **project details, background, design, or
analysis**. Raw data is fine.

This note records what that boundary actually is, where it can be enforced, in
what order, and what was rejected. Companion to [`AUTH_DESIGN.md`](AUTH_DESIGN.md),
which owns identity and roles; this note owns only the agent-access question.

---

> **2026-09-03 — record layer paths.** The lab's record layer is **BitacoraDB**
> (`bitacora_db`, edge path `/bitacoradb/*`, loopback `127.0.0.1:8013`). Where
> this document still shows `/analytica/*` paths they are the path-policy
> *examples* the tests exercise; any Hermes path policy written today should
> name `/bitacoradb/...`. AnaliticaDB / `analytica_db` is LaAgenteAnalitica's
> separate store and is not on the lab agent's read path.

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
| BitacoraDB `:8010` — `/measurements`, `/files`, `/samples`, `/uploads/experiments` | raw data | **allow** |
| BitacoraDB `:8010` — `/projects`, `/experiments`, `/plans`, `/analyses`, `/analysis-files`, `/notes`, `/agent-run-graphs` | project details, background, design, analysis, agent reasoning traces | **deny** |
| bitácora — `/projects/{id}/rooms/{id}/{designs,protocols,decisions,messages,pr,trace}` | the design-and-collaboration ELN, end to end | **deny** |

Two findings that corrected the first draft of this note, both from reading the
services rather than the docs:

- **bitácora has no data route.** Its entire API is nested under
  `/projects/{id}/rooms/{id}/…`. It *is* the design layer; nothing in it sits on
  the allowed side. So the boundary is "all of bitácora", not a cut through it —
  and it needs no rule, because §4.2's policy defaults to deny.
- **The cut through BitacoraDB is real and clean.** Its 24 routes separate raw
  data from record along precisely the wording of the requirement.

### 2.1 Two leaks this design does not close (the first has since closed)

Recorded so they are accepted knowingly rather than discovered later.

- **Names encode design.** Sample ids and filenames routinely carry the
  experiment (`plate3_pd_ligand_screen_48h`), so reading `/measurements` and
  `/files` leaks *some* intent regardless of how the routes split. The
  mitigation is project-scoping the raw reads as well.

  **Closed since this was written (verified 2026-08-14).** `can_read` is
  implemented in BitacoraDB (`src/bitacoradb/authz.py`) as
  `admin OR PI of the project OR active member of the (active) project`, and
  `api/deps.py::filter_readable` applies it row-by-row on the list routes —
  `/measurements`, `/files`, `/samples`, `/analyses`. ac_auth supplies the
  caller's scope (`authz.data_scope`, emitted as `X-Auth-Projects` /
  `X-Auth-Pi-Projects` from the roster's project memberships). So a caller
  outside a project no longer sees its rows, names included; within a project
  the names are visible to people already entitled to the design.

  The residual risk is not the rule but its **inputs**: scope arrives only as
  those two headers, so any surface that forwards `X-Auth-User` and
  `X-Auth-Role` without them silently reduces every non-admin to an empty
  scope. That is not hypothetical — bitácora's ELN and its edge route both did
  exactly that until 2026-08-14, and the symptom (only admins could see
  anything) reads as a role tier rather than as a dropped header. When adding a
  surface that reads the record layer, forward all four.
- **Working on the ELN is not reading the ELN.** `bitacora/.hermes/plans/`
  already exists — Hermes develops that repo today and should keep doing so.
  But developing means running it, seeding fixtures, reading logs, all of which
  drift toward production records. **Rule: Hermes may hold the ELN's source,
  never its production credentials.** Dev work runs against a dev database.

## 3. The finding that sets the order

**An edge policy cannot constrain Hermes while Hermes runs as `sdl2`.**

Both record services are gated at the single Caddy edge (`/etc/caddy/Caddyfile`,
live): `/bitacoradb/*` is `forward_auth`'d to ac_auth and proxied to `127.0.0.1:8013` with
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

#### Phase 0 — implemented 2026-08-12 (record)

Executed as specified, verified by the failing probes (secret reads denied,
repos readable, `hermes --version` runs, `id hermes` shows no extra groups).
Four things the design did not anticipate, recorded so the next reader trusts
the boundary for the right reasons:

- **File permissions were not the only leak.** `systemctl show <unit> -p
  Environment` exposes unit environments to *any* local user over D-Bus,
  regardless of the drop-in file's `600` — `caddy.service` was leaking
  `XARM_EDGE_SHARED_SECRET` / `OT2_EDGE_SECRET` / `GRAPHCHAT_EDGE_SECRET` and
  `ac-organic-lab-web.service` leaked `CONTROL_PASSWORD` this way. Closed by
  moving the values into root-`600` `EnvironmentFile`s
  (`/etc/caddy/edge-secrets.env`, `/etc/ac-organic-lab/web-secrets.env`) —
  `show` prints `EnvironmentFile` *paths*, never contents. This closes the
  leak AUTH_DESIGN recorded. **Follow-up, deliberately not done in-phase:**
  those four values were world-readable-at-rest until this date and should be
  rotated at a calm moment (the xArm one touches the device host, the Caddy
  env file, and the dashboard `.env` — mis-rotation reproduces the 2026-08-11
  401 incident).
- **The `.env` sweep was too narrow.** `~/.claude`, `~/.codex`, and
  `~/.config/gh` were `755`/`751`; agent session transcripts can quote
  secrets, so they were tightened to `700` before the ACL — same
  chmod-before-setfacl reasoning, wider net. Git remotes were scanned for
  embedded `https://user:token@` credentials (none found).
- **The `acl` package was not installed** on the host; `setfacl` needed
  `apt-get install acl` first. The ACL itself was then applied by `sdl2` (the
  file owner) with no root.
- **The venv cannot be copied** (editable install, absolute shebangs). The
  install is a local `git clone` of `~sdl2/.hermes/hermes-agent` (same commit,
  `e57918ac8`) plus a fresh `python3 -m venv` + `pip install -e .` as
  `hermes`, with the wrapper at `/usr/local/bin/hermes` (the PATH split as
  designed). `/home/hermes` is `700`. Sessions, memories, profiles, and
  `auth.json` were **not** copied — the boxed principal starts fresh, and its
  model key should be its own.

### Phase 1 — a machine principal (configuration only)

ac_auth already has machine principals (`X-Api-Key`) and per-equipment grants,
so no new mechanism is needed:

- Add `hermes@lab.local` under `automation:` in `roster.yaml`, `approved: true`.
- Grant `bitacora_db`. **Do not grant `bitacora_eln`** — that single omission
  denies the whole design ELN, with no code.

**Implemented 2026-08-12**, with the Phase-2 `paths:` block attached in the
same entry (mirroring `test_path_policy.py::HERMES` verbatim). One divergence
from §2, accepted knowingly: `/uploads/experiments` sits on the allow side of
the §2 table but is **not** in the pinned allow list, so default-deny closes
it — widen deliberately if raw-upload reads are ever needed. The pattern is
documented in `roster.yaml.example` (the live roster is gitignored). Key
issuance (`ac-auth issue-key`) deferred to run-trigger profile wiring.

### Phase 2 — edge-path policy — **implemented**

Grants are **service-level**: a grant on `bitacora_db` opens all of its
routes, so Phase 1 alone would hand Hermes `/plans` and `/analyses` alongside
`/measurements`. Closing that is the only new code in this design.

- `roster.PathPolicy` — an optional `paths: {allow, deny}` block on a roster
  entry, both `RosterUser` and `RosterAutomation`.
- `authz.path_permitted(policy, uri)` — **deny wins, then allow, else refuse.**
- Enforced in `GET /auth/verify` against the `X-Forwarded-Uri` the edge already
  sends, returning 403.

Three decisions worth keeping:

- **Default-deny for unlisted paths.** A route added to BitacoraDB later starts
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

### Phase 4 — learning policy [DRAFTED 2026-08-12; first enforced slice 2026-08-13]

The first mechanism built *to* this policy is the dashboard assistant's
`record_observation` tool (`api/app/mcp_server.py`): the permitted learning
loop as one audited journal write — device-anchored `agent_observation` rows
via `/api/ingest/events`, stamped with the verified operator (fails closed
without one), size-capped, lab-public, and read back by any lab-history
client next session. Deliberately a shared journal and not a per-agent
memory: 4.1 (platform, never science) is prompt-enforced; 4.2/4.3/4.5 are
enforced by construction.

Per decision #11 and `AGENTS.md` §5, which already name Hermes:

- Repo-specific lessons → that repo's `AGENTS.md`, **as a reviewed diff**.
- Cross-repo/machine facts → `~/.hermes/memories/MEMORY.md`, *proposed for
  approval*, never silently written.
- The signal to learn *from* is `lab.db` via MCP (Phase 3) — no new plumbing.
- **Watch item:** Hermes also keeps a private `state.db` (56 MB) and a memory
  graph. Knowledge accumulating only there defeats decision #11's *no hidden
  state that silently steers agent behavior*; durable platform knowledge belongs
  in the committed files.

The rules below were settled 2026-08-12, when the boxed `lab-runner` profile
went live and plan-drafting-through-conversation became the agreed rung-3
shape. They exist because a **learning agent's memory is an access-control
bypass waiting to happen**: people will *tell* the agent project details in
conversation, its memory has no `can_read(project, caller)`, and anything it
remembers can potentially be extracted by anyone allowed to talk to it.

**4.1 Memory holds the platform, never the science.** The agent's durable
memory (files, MEMORY.md, state) may hold operational knowledge only — device
quirks, timing, failure patterns, workflow lessons: the facility manager's
knowledge. Project details heard in conversation (goals, compounds, designs,
results) are used for the task at hand and are **never promoted to durable
memory**. This is §1's principle ("the instrument and platform layer, not the
scientific record") extended from *access* into *retention*.

**4.2 The audience defines the confidentiality domain.** Whoever can message
an agent instance must be trusted with everything that instance has ever been
told. One shared lab agent = one shared confidentiality domain. When that
stops being true (mutually confidential projects), the answer is per-project
agent instances with separate memories — mirroring `can_read` — never one
omniscient agent with a promise to be discreet.

**4.3 Memory stays reviewable.** Agent memory lives in human-readable files
that can be read, diffed, edited, and deleted (decision #11). Periodic review
is a real control; the `state.db` watch item above is the standing threat to
it.

**4.4 Conversations transit third parties — know which.** Every turn with a
lab agent leaves the building: the dashboard assistant goes to Anthropic (the
operator's Claude Code account); Hermes profiles go to the configured model
provider (today OpenRouter → Z.AI for GLM). Local persistence differs — the
dashboard bubble keeps no server-side transcript (browser sessionStorage
only), while Hermes profiles keep session logs under their own profile dir,
boxed with the OS user. **Choice of model provider is therefore a
confidentiality decision**, made per agent, with the provider's retention
settings checked — not a convenience default.

**4.5 Audit rows are lab-public; keep them operational.** Fragments of
conversation become permanent, lab-wide-readable records by design: a
Control-mode proposal's `reason` line and every `plan_run` / `control_action`
row land in `lab.db`, which every lab-history client (dashboard assistant,
`lab-ops`, `lab-runner`) can read. That is the audit trail working. The rule
it implies: the *stated reason* for an action is operational text, never
project-confidential text.

**4.6 The record stores stay behind people.** Standing decision, same date:
**no API key is issued for `hermes@lab.local`** — the BitacoraDB grant and
path policy (Phases 1/2) remain in the roster as defense-in-depth should a
key ever be issued deliberately, but today the agent cannot reach the record
store at all, and plan drafts enter bitácora through the human who approves
them, never by agent write. Issuing a key is a human decision that reopens
this section.

## 5. Decided against — do not relitigate

- **Enforce in Caddy matchers.** Scatters policy across a root-owned file that
  no test touches, and duplicates per service. ac_auth is already on the request
  path of every gated route and is roster-driven, so policy stays in the file
  humans already review.
- **Enforce inside BitacoraDB.** Would have to be repeated in bitácora and in
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
