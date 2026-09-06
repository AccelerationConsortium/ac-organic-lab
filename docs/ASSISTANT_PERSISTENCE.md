# Assistant modes — temporary Control and saved Plan work

**Status: revised proposal (2026-09-06).** The user chose convenient temporary
Control sessions plus a separate mode for saved planning. The routine-action
scope and binding-contract amendment in §2 still need a human decision; this
document grants no execution exception. Original proposal: checkpoint dfc59ca.

## 0. User experience

| Mode | Purpose | Conversation | Hardware |
|---|---|---|---|
| **Ask** | Explain status and investigate problems | Temporary; downloadable | Read-only |
| **Control** | Perform permitted routine tasks while the operator is present | Temporary; downloadable; no automatic ELN filing | Reviewed actions through the authorized SDK path |
| **Plan** | Develop reusable protocols | Named, owner-private saved sessions; protocols edited in bitácora | No hardware actions from chat; project review and authorization apply |

Control must work without creating an experiment, provisioning a sandbox, or
visiting bitácora. A download is a conversation export, not a registered Plan,
proof of execution, or permission to replay commands.

The immediate notice, accurate for the current implementation, is:

> Temporary conversation. Download before closing this tab. Proposals and
> control actions remain in the audit trail.

Once the routine-control exception is approved and enforced, Control may add:

> Routine controls are not filed as experiments. Use Plan for recorded work.

Never promise "no record is kept": device logs, proposal/control audit events,
explicit journal observations, and short-lived camera captures still exist.
Deleting a chat does not delete these records. Provider retention is separate.

Changing from temporary work to Plan previews the content to save; it never
silently persists prior Control messages. Leaving Plan preserves that saved
session and opens a new temporary conversation. No switch carries approvals,
claims, or execution continuation. A running action must conclude or reach
its existing stop boundary before changing sessions.

## 1. Decisions

### D-1 — Saved planning sessions belong to the dashboard service

Use assistant.db, owned by api/, with SQLite WAL, serialized writes, migrations,
and bounded retention. Keep it separate from public lab.db telemetry and
BitacoraDB scientific records. Ask and Control do not automatically write
conversations to this store and remain usable if it is unavailable.

Every list/read/rename/delete/export/turn request checks the verified current
principal. Owner and global admins may read; an admin never acts as the owner.
Ownership comes from auth, never a JSON field. Account renames or ownership
transfers need an explicit migration.

Temporary browser caches must be scoped to the verified principal and cleared
on logout/account change. An old cache with no owner is not adopted by the next
person using a shared computer.

### D-2 — Store structured history and restore inertly

Persist messages, projected tool events, historical proposal contents/outcomes,
and attachment references. Do not serialize React state or restore the approval
cache. Exclude hidden reasoning, system prompts, credentials, claim tokens, and
arbitrary raw tool payloads.

Saved sessions reopen in Ask for read-only review; continuing planning uses
Plan's non-actuating toolset. All historical cards are display-only.

Before saved sessions ship, define:

- Server-issued session/message/turn ids and ordered events. Retrying a client
  request id returns the same turn, without rerunning tools.
- A session revision and one active turn per session; concurrent edits conflict
  rather than overwrite.
- Running/completed/interrupted/failed states. Restart or disconnection never
  changes an unfinished answer into success.
- Server reconstruction of model context from session id plus the new message.
  Retained history and context limits are independent; clients cannot replace
  stored history or assert authorship.
- Attachment lifetime, quotas, deletion, and backup retention. Current camera
  URLs expire after about 24 hours; a saved URL is not a saved image.

### D-3 — Conversation snapshots stay in conversation storage

Named snapshots are versioned views of interaction history with explicit
retention/deletion rules. Transcripts enter neither git nor BitacoraDB.
This follows AGENTIC_ELN_DESIGN §13 and DATABASE_DESIGN §1.

This reverses original D-3. BitacoraDB lacks generic project attachments:
Notes require an Experiment, measurement files a Measurement, and analysis
files an Analysis. Do not invent those parents to archive chat. Actual
observations and scientific artifacts use their real project's record entities.

A protocol retains its reviewed decision artifact and source revision even if
a disposable chat expires. A private chat link does not replace reviewable
protocol content.

### D-4 — Access controls precede sandbox provisioning

Admin-provision two or three sandboxes initially. Join auth project, bitácora
registry/repository, and BitacoraDB project through a unique principal-to-sandbox
mapping and resumable provisioning states. Remain unavailable until required
components agree. No self-service roster writes.

An ordinary bitácora project is not owner-private today: chat is member-gated,
but protocol reads and several edits are lab-wide. BitacoraDB also defers
membership-based write authorization. Gate existing protocol/raw reads, edits,
diffs, validation, compile configuration, authorizations, and record mutations
before personal drafts land. Restricting only the new filing tool is insufficient.

### D-5 — Bitácora owns draft import

The dashboard's file_draft_protocol is a narrow HTTP client of a bitácora-owned
operation. Bitácora owns validation, worktree serialization, and commits. The
dashboard/MCP process gains no GitHub credential or direct workspace writes.

Derive the human from verified server identity and attribute the drafting agent
separately. Resolve the destination from the sandbox mapping; accept no arbitrary
repo/path. Permit draft protocol paths on draft branches only, with traversal
and symlink confinement. No action-map, binding, CI, rule, or default-branch edits.

Include source artifact/version, idempotency key, and expected destination
revision. Retries return the same import result; partial failures remain visible.

Start with platform-only content. Project-bearing content stays disabled until
source scopes are carried and checked before filing, export, and model reuse.
Messages, attachments, and summaries inherit those scopes. Personal ownership
and a model's assertion that copying is safe cannot replace source permissions.

### D-6 — Convert one supported shape and verify equivalence

Device skills and scientific protocol actions differ. Start with one reviewed
mapping; the standard HTE template's empty action map is not a usable converter.

Preserve step order and stable identifiers. Pin converter, schema, and template
revisions. Recompile and compare normalized commands, arguments, and resolved
equipment bindings with the source proposal. Missing information and unmapped
actions produce findings; never infer chemistry or silently drop steps.

Incomplete proposals remain downloadable as non-executable drafts. Draft input
has a separate bounded contract: the live confirm-card cap must not prevent
larger drafts from reaching Plan. Promotion recompiles against destination
mappings and presents resulting differences for review.

### D-7 — Sandboxes receive validation, never run authorizations

Initially issue no sandbox run authorizations, including "dry-run authorizations".
Reuse bitácora's draft validation endpoint: it compiles/preflights without claims,
control POSTs, or authorization.

Keep execution policy in one admin-controlled auth/project configuration source,
outside project repositories. Issuer and executor independently resolve it;
missing policy or authoring_only refuses live work. Recheck current policy
between steps like revocation. Existing projects need an explicit migration.

Today executable tests only expiry/revocation and the runner accepts dry_run=false
from its caller. Neither enforces sandbox policy. Refusal must also cover stale
or erroneously issued authorizations and direct runner requests.

### D-8 — Promotion imports a pinned revision through human review

A human imports into a destination project branch. Resolve destination schemas,
action mappings, equipment/material bindings, and rules afresh. Normal PR →
human CODEOWNER merge → compilation/validation → Plan registration →
authorization applies. Provenance travels; approvals do not. The dashboard
assistant receives no shared-project write capability.

### D-9 — Storage grants no execution authority

Never replay saved cards, imported exports, or saved proposal ids. Plan is an
authoring mode. Authorized runs use the existing run surface and record layer.
Calling work "routine" or switching modes cannot exempt an experiment from its
project or scientific-record rules.

### D-10 — Separate review limits from completion reporting

The checkpoint preserves MAX_PLAN_STEPS=256, but the finish API caps results
at 64. Fix reporting to accept every producible step, including skipped steps;
that fix does not approve larger unrecorded workflows.

The proposed routine-Control cap is 40 per reviewed single-device sequence,
subject to §2. Never split work across cards to evade it. Duration, fan-out
inside one action, and attendance also matter; step count alone is insufficient.

## 2. Binding-contract decision

Part I §1.3/§3.1 require merged, registered, validated plans for hardware.
UI_DESIGN §5's claimed exception does not amend them. The proposed human-owned
exception for routine manual operations would require all of:

1. A reviewed routine-action allowlist with argument bounds, enforced on both
   proposal and execution paths; no model-selected "routine" exemption.
2. A signed-in human with the target equipment grant reviewing the exact
   action/sequence and remaining present.
3. SDK-only hardware access, claims, live device checks, applicable skill and
   interlock checks, deck confirmation, and existing stop/escalation rules.
   The model still has no actuating tool.
4. Routine operations may omit a project protocol/Plan and automatic ELN
   filing; proposal, human approval, and observed control outcomes remain
   operational audit records. Downloads grant no execution authority.
5. Experiments, reusable workflows, unattended work, and non-allowlisted
   operations retain the existing project and scientific-record requirements.

**Routine scope awaiting the user's answer:** setup/positioning only, or also
short liquid transfers and dosing. The latter needs explicit limits and
material/lot recording semantics; omitting an experiment record cannot remove
an applicable inventory or custody obligation. This document authorizes neither
an expanded scope nor a contract amendment. Apply the accepted exception
explicitly to Part I §1.3/§3.1 and reference it from §2.1; preserve §1.1/§1.2.

Independently, the existing authorized runner proceeds when opening its
BitacoraDB Plan fails. A live run must not send its first hardware command
without its required registered Plan. Record-write failures after physical
action need truthful outcomes and durable recovery, never automatic replay.

## 3. Build order

**Implemented locally on `design/assistant-modes` (not deployed):** the temporary
chat notice and Markdown/JSON downloads, inert proposal/outcome history, and
owner-scoped tab caches. Only the latest 20 messages survive a reload; downloads
include all messages still available in the current tab. Model context is
bounded separately to 40 messages. Raw device responses and image binaries are
not included in transcript downloads; camera references expire.

Logout/account changes clear the visible conversation and live cards. Shared
cookie changes notify other tabs, identity refreshes on focus, and the chat
API refuses a history whose stated owner differs from the authenticated actor.
An in-flight sequence stops submitting new steps if its component unmounts.
Completion reports accept the same step count as proposals; a report failure
remains visible without changing the reported physical outcome.

Saved Plan sessions, the routine-action exception, sandbox provisioning, and
protocol handoff remain proposed. Part I is unchanged.

1. **Temporary convenience:** truthful notice, Markdown/JSON downloads,
   identity-scoped browser caches, and completion-report sizing. No expansion
   of hardware permissions.
2. **Saved Plan sessions:** D-1/D-2, with ownership isolation, idempotency,
   concurrent-tab, interrupted-stream, retention, and inert-restore checks.
   Saving a conversation must never report that a protocol was filed.
3. **Routine-control policy:** settle §2, explicitly amend the binding
   contract through human review, implement the allowlist and test refusals.
4. **Sandbox handoff:** access controls and D-7 first, then the pilot and one
   validated conversion/import. Test cross-user access, unsafe paths, stale
   revisions, duplicate saves, and direct live execution attempts.
5. **Promotion tooling and named snapshots:** after the handoff proves out.

Saved sessions do not depend on sandbox provisioning. Temporary Control does
not depend on the saved-session service. External-repo edits follow AGENTS §6;
no release validation starts laboratory hardware.

## 4. Consult and sources

- 2026-09-05: original Claude proposal and Codex consult; D-1…D-10 preserved
  in checkpoint dfc59ca.
- 2026-09-06: revised after cross-repo source review and the user's temporary
  Control / saved Plan decision. Corrected D-3; made sandbox access, source
  scope, and execution policy prerequisites; separated the releases.
- [AGENTIC_LAB_DESIGN.md Part I](AGENTIC_LAB_DESIGN.md) — binding rules.
- [AUTH_DESIGN.md](AUTH_DESIGN.md) — identity and runtime storage.
- [AGENTIC_ELN_DESIGN.md §13](AGENTIC_ELN_DESIGN.md) and
  [DATABASE_DESIGN.md §1](DATABASE_DESIGN.md) — conversation/record boundaries.
- [UI_DESIGN.md §5](UI_DESIGN.md) and [ARCHITECTURE.md](ARCHITECTURE.md) —
  assistant tools and execution seams.
