# AC Organic Lab in the Bitácora product direction

Date: 2026-09-07. Status: agreed direction; integration design and implementation
remain pending. Canonical product scope:
[Bitácora product direction](../../bitacora/docs/PRODUCT_DIRECTION.md).
Record responsibilities:
[BitacoraDB direction](../../BitacoraDB/docs/BITACORA_RECORD_DIRECTION.md).

## Role

AC Organic Lab owns this laboratory's equipment integration and operation:
capabilities, status, claims, preconditions, readiness, authorized execution,
progress, operational telemetry, and physical reconciliation. Bitácora owns
scientific deliberation and review and is intended to work independently for
manual scientists. This lab becomes its first optional execution integration.

Keep scientific review and physical readiness separate. A connector must not
permit a model or optimizer to bypass lab-skills, interlocks, verified identity,
human sign-off, or the existing per-run authorization. This direction does not
amend [the binding lab rules](AGENTIC_LAB_DESIGN.md#part-i--binding-rules-normative)
or [STATUS_SPEC.md](STATUS_SPEC.md).

## Assistant experience

Keep a convenient assistant in the dashboard. Operational questions and
troubleshooting belong here; scientific drafting and capture should delegate to
Bitácora rather than acquire a second independent scientific lifecycle. Users may
stay in one conversational interface while the destination and attribution of
captured artifacts remain explicit.

The checked-in dashboard supports Claude CLI and an OpenAI-compatible backend.
The boxed Hermes operations/Slack deployment is a separate integration. Keeping
Hermes is compatible with this direction; no engine migration is selected.
Existing Hermes access restrictions still apply. Scientific filing and retrieval
need verified project scope, not expanded privileges inferred from a chat request.

| Information | Destination |
|---|---|
| Casual questions and temporary operational conversation | Assistant session storage according to its existing policy |
| Verified equipment observations and intervention history | Operational event journal |
| Ideas, hypotheses, alternatives, scientific rationale | Bitácora drafts through an explicit capture operation |
| Actual experimental observations, deviations, measurements | BitacoraDB through the authorized record path |
| Proposed reusable scientific lesson | Bitácora evidence review before knowledge promotion |

Do not force every chat into BitacoraDB or reinterpret saved transcripts as
scientific records. The existing saved Plan sessions are not registered protocols;
their default retention is 180 inactive days. Valuable ideas need an explicit
capture path rather than dependence on session retention. The proposed ownership
refines the future direction of [ASSISTANT_PERSISTENCE.md](ASSISTANT_PERSISTENCE.md);
it neither removes existing sessions nor implements import or migration.

## Fragile experiments and recovery

Preservation must be an execution-system responsibility, not a final chat task.
Audit whether the runner durably captures completed steps, measurements, deviations,
and pending record writes throughout a run. Distinguish rejected-before-execution,
confirmed completion, uncertain physical outcome, and a documented pause.

A successful software restart does not prove that a dispense did not happen.
Record retries and physical action retries require separate policies. Unknown
outcomes require reconciliation; material changes during a pause can invalidate
continuation even if the device can resume. Recovery must retain the original
attempt and record the continuation, evidence, and authorizing actor.

The custody work across the sibling feature worktrees addresses attribution,
human-confirmed physical facts, duplicate record requests, and stale location
checks. These changes are not assumed deployed. Their contract versions and
client compatibility need verification before integration.

## Proposed connector boundary and next work

The connector should expose versioned capabilities, readiness, authorized run
submission, progress, cancellation semantics, and evidence references. Equipment
control stays here; Bitácora's UI and scientific agent need no direct device API
or sibling SDK checkout. Shared record clients are candidates for packaging,
not permission for clients to open one another's databases.

Next work: specify the connector contract; map the current run/record lifecycle;
exercise interruptions using mocks or simulation; design scoped capture of ideas
and observations; plan existing-session compatibility. Validate that disconnected
equipment does not prevent manual scientific work in Bitácora.

Inventory views here should consume the authoritative inventory API. Migration
of Bitácora's current inventory store into the target record ledger belongs to
the product and record-layer designs, not a second dashboard inventory database.
