# Assistant persistence — saved sessions, draft filing, and sandbox projects

**Status: PROPOSED (2026-09-05). Design only — nothing in this document is
built.** Drafted after a two-model consult (Claude proposal, Codex read-only
review against the repo docs; deltas the review changed are marked). Decisions
are numbered D-1…D-10 so review can address them individually; genuinely open
items are OPEN-1…OPEN-5. Doc-first per ARCHITECTURE decision #8 — no code
until a human has reviewed this.

## 0. The need

Dashboard users want two things the assistant deliberately does not have
today:

1. **Saved sessions.** The chat bubble holds ~20 turns in `sessionStorage`
   and the backend is stateless by design (AUTH_DESIGN → *Assistant chat*:
   "no conversation content at rest"). Close the tab, lose the conversation.
2. **Saved, iterable "scripts."** Control mode now drafts multi-step plans,
   and people want to keep, refine, and reuse them. Context that forced the
   question: `MAX_PLAN_STEPS` was raised 40 → 256 (2026-09-04, uncommitted at
   time of writing) to fit ~96-well plans onto one confirm card — the
   pressure of "where do big drafted plans live?" leaking into the one place
   it doesn't belong, the human-review surface.

The design constraint everything below serves: **the lab already has exactly
one home for a script that runs** — a bitácora protocol, compiled, authorized,
executed with digest pinning, claims, revocation, and custody recording. A
"save and rerun my chat plan" feature inside the assistant would be the start
of a shadow authoring path around that. So: save freely, iterate in the ELN,
execute only through the existing gate.

## 1. The shape in one paragraph

Chat sessions persist **server-side** in an owner-private store the auth
design already reserves for exactly this. Drafted plans are **filed into a
per-user sandbox project in bitácora** through one narrow, actor-bound,
path-locked tool, and all iteration happens in bitácora's real editor and
compiler — the assistant never grows a second protocol editor, never writes
into shared projects, and never executes anything saved. Sandboxes are
**admin-provisioned** (no self-service roster writes), classified
**authoring-only** (dry-run authorizations at most), and a draft reaches
hardware only by **human-initiated promotion** into a shared project through
the normal PR → CODEOWNER merge → compile → authorize path.

## 2. Decisions proposed

**D-1 — Sessions live server-side, in a new owner-private SQLite store.**
A small `assistant.db` owned by `api/` (WAL, one writer — the lab.db
pattern), keyed by the verified `X-Auth-User`: list / resume / rename /
delete, owner + admin visibility, user-clearable, bounded retention.
This is not a new idea — AUTH_DESIGN's assistant section already reserves it
verbatim: "*owner-private runtime data in SQLite under the same `can_read`
policy (owner + admin; user can clear; bounded retention) — never in
`roster.yaml`*". It goes in neither existing store: `lab.db` is public
telemetry, BitacoraDB rows are immutable project records and a growing chat
maps badly onto them. `localStorage` demotes to an optional cache — lab
computers are shared, so the browser must never be the only copy *or* an
identity bypass. *(Codex review reversed the original localStorage-first
phasing; adopted.)*

**D-2 — A restored session reopens in Ask mode with historical cards inert.**
No confirm card ever comes back live from storage: no restored approvals, no
claims, no execution continuation. Re-arming a proposal requires proposing it
again against live device state. *(From the review; adopted — this is the
session-store analog of "the authorization's stored verdict is never
clearance to run now".)*

**D-3 — Snapshots at any point, filed as immutable BitacoraDB files.**
"Save this conversation" names a snapshot and files it (file/note under the
user's sandbox project, provenance attached); later snapshots reference
earlier ones. Snapshots go to **BitacoraDB, never into the sandbox's git
repo** — transcripts can carry run observations and camera frames, and Part I
§2.5 is categorical that run data never enters git. *(Review sharpened
"finished conversations only" into "any point"; adopted.)*

**D-4 — Sandbox projects are admin-provisioned; no auto roster writes.**
The original proposal auto-created `sandbox-<user>` on first save. The review
is right that this collides with AUTH_DESIGN's core posture: `roster.yaml` is
human-edited, schema-validated, fail-closed, deliberately without
self-registration — a first-save write path into it is a new auth surface, not
a convenience. So: a **small pre-provisioned set** of sandboxes for the users
who want this, created by an admin (roster entry + bitácora project init +
BitacoraDB project row). Self-service provisioning, if ever, is its own
AUTH_DESIGN change with a narrowly-privileged provisioning service. Because
provisioning spans three systems and is not atomic, the eventual mechanism
needs: a stable principal id, a unique user↔sandbox mapping, resumable
provisioning states, and fail-closed behavior until roster, ELN, and record
store all agree.

**D-5 — One filing tool, actor-bound and path-locked.**
`file_draft_protocol` on the `lab-control` server: the actor arrives in the
server's environment (the existing `LAB_ACTOR` pattern — never a tool
argument), the destination is locked to the **caller's own sandbox**, and —
the review's sharpest catch — locked to **draft protocol paths/branches
only**. A tool scoped merely "to the repo" could write `compile/actions.yaml`,
CI config, or rules files, which are executable surfaces; protocols are the
only thing the assistant may file. Both the requesting human and the drafting
agent are attributed on the commit. One boundary rule rides along:
**personal storage must not launder project data** — read access to a shared
project is not permission to copy its results into a personal project
(mechanism: OPEN-3).

**D-6 — The plan→protocol conversion is explicit and honest.**
A `propose_plan` step list is device commands (skill + args), not a
compilable protocol — bitácora protocols are action names resolved through a
project's `compile/actions.yaml`, with plates, wells, and parameters. The
conversion must preserve the exact steps, map only what has a defined
mapping, and **surface missing parameters and unmapped actions as refusals**
rather than inventing chemistry. The first slice supports one conversion
shape end-to-end; everything else renders as "cannot convert yet, exported
verbatim instead."

**D-7 — Sandboxes are authoring-only, enforced twice.**
A project-level classification (admin-controlled, default `authoring_only`
for sandboxes) that **both** bitácora's authorization issuance **and** the
run executor check independently: a sandbox protocol can compile and dry-run,
never authorize a live run. A filename convention, prompt text, or hidden UI
is not enforcement. Dry runs must remain incapable of hardware writes.
Real execution requires promotion (D-8). One honest note from the review:
the binding contract requires a human CODEOWNER merge, a registered Plan,
and validation — it does not literally require the reviewer to differ from
the author, so solo-sandbox authorization is a governance gap rather than a
textual contract violation. Authoring-only is proposed as the *policy*
answer to that gap, not as a contract quotation.

**D-8 — Promotion is a human-initiated import, and approvals never travel.**
Promoting a draft = a human imports a **pinned draft revision** into a
destination project branch, then the normal machinery applies: PR, CODEOWNER
merge, compile against the destination's own action mappings, Plan
registration, authorization. Provenance (which sandbox draft, which chat
snapshot) is carried; approvals are not. Destination bindings, materials,
and project rules are re-resolved from scratch. The assistant plays no part
in promotion and needs no shared-project write capability, ever.

**D-9 — Nothing saved is runnable from the assistant.**
No replay button, no "run saved script," no execution semantics in the
session store. Transcripts are not the iteration substrate; protocols are.
(Unchanged from the original proposal; the review concurred.)

**D-10 — The confirm-card cap comes back down once filing ships.**
The honest answer to a 200-step chat plan becomes "it is a draft protocol
now — iterate and authorize it properly," so `MAX_PLAN_STEPS` returns toward
its review-ability rationale (target value: OPEN-4). Never split an
oversized plan across successive cards to evade the cap — that defeats the
bound's purpose while pretending to honor it.

## 3. A pre-existing contract conflict this design must not inherit

Surfaced by the consult, bigger than the feature, and needing its own
resolution: **UI_DESIGN §5 Step 1i (multi-step confirm-card plans) sits in
tension with AGENTIC_LAB_DESIGN Part I** — §1.3 says only validated,
`main`-merged, human-approved plans execute against hardware, and §3.1 says
only `main` executes; Step 1i executes operator-approved ad-hoc sequences
under a claimed carve-out, without layer-4 validation. Per AGENTS.md §1 the
binding contract wins over any working convention, and the quiet 40 → 256
cap raise widened exactly this gap. Resolving it is a **human decision with
two honest exits**: amend the contract to define the operator-attended
confirm-card exception explicitly (a deliberate, human-owned contract
change), or bound/retire the Step 1i carve-out. **This design takes no
position on that resolution — it only refuses to extend the carve-out:
nothing saved under this design executes except through D-7/D-8.**

## 4. Smallest first slice (when approved)

1. **Named server-stored sessions** (`assistant.db`): save / list / resume /
   rename / delete, Ask-mode reopen with inert cards (D-1, D-2), plus plain
   export buttons (markdown/JSON download) on the bubble.
2. **Two or three admin pre-provisioned sandboxes** for the users asking,
   and the **"Save draft / Open in bitácora"** action with one validated
   conversion shape, refusal states rendered honestly (D-4, D-5, D-6).
3. Verification gates before calling it shipped: ownership isolation
   (user A cannot list/resume user B), duplicate-save handling, inert
   restored cards, and a live-run authorization attempt against a sandbox
   being refused at both enforcement points (D-7).

Deferred by design: auto-provisioning, promotion tooling, snapshot filing
(D-3 is slice two), and the cap change (D-10 — after the handoff proves out).

## 5. Open questions

- **OPEN-1** — Sandbox representation in `roster.yaml`: ordinary project
  entries vs a distinct section with the `authoring_only` classification;
  where the classification lives so both enforcement points (D-7) read one
  source.
- **OPEN-2** — Session retention length, and the token-cost policy on resume
  (history is re-sent per turn on the openai backend: cap, truncate, or
  summarize).
- **OPEN-3** — Mechanism for the data-laundering rule (D-5): how a snapshot
  or draft carrying project-scoped content gets source-project access checks
  on filing and on later reuse as model context.
- **OPEN-4** — `MAX_PLAN_STEPS` target after filing ships, and the fate of
  the current uncommitted 256 (commit with an updated rationale comment, or
  revert) — coupled to the §3 resolution.
- **OPEN-5** — Sandbox lifecycle: quotas, archival, account offboarding,
  ownership succession, and template drift across per-user repos.

## 6. Consult record

2026-09-05: proposal drafted in-session (Claude); independent read-only
review by Codex (`gpt-6-astra`, codex-cli 0.153.4) over ARCHITECTURE,
AUTH_DESIGN, AGENTIC_LAB_DESIGN Part I, UI_DESIGN §5, and the bitácora
plans. Adopted from the review: admin provisioning (D-4), server-side
sessions (D-1), inert restored cards (D-2), anytime snapshots to BitacoraDB
(D-3), the path-locked tool and laundering rule (D-5), conversion validation
(D-6), dual-point authoring-only enforcement (D-7), pinned-revision
promotion (D-8), and the §3 conflict flag. The review transcript lived in
the session scratchpad; this section is the durable record.

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) decisions #8 (spec before code) and
  #10 (the assistant is a proposer, not an actuator) — the invariants this
  design extends and must not weaken.
- [`UI_DESIGN.md`](UI_DESIGN.md) §5 — assistant Control mode; §5 Step 1i is
  the carve-out discussed in §3 above.
- [`AUTH_DESIGN.md`](AUTH_DESIGN.md) — the roster model D-4 defers to; the
  *Assistant chat* section whose "owner-private runtime data" note D-1
  implements; the `can_read` data-isolation policy.
- `docs/AGENTIC_LAB_DESIGN.md` Part I — the binding contract (§1.3, §2.5,
  §3.1 are the load-bearing rules here).
- [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) — BitacoraDB, where snapshots
  (D-3) and sandbox project rows (D-4) land.
- `bitácora` — the ELN whose projects, compiler, and authorization gate are
  the destination for everything iterable.
