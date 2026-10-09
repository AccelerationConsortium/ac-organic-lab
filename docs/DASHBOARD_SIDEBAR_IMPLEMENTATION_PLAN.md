# Dashboard sidebar, Home, and private entry: implementation and server handoff

Date: 2026-10-09. Status: implementation plan for review and continuation.
This commit contains documentation only. It does not implement, deploy, change
access, or operate equipment. The requested next development step is a protected
preview of the existing Overview inside a shared left sidebar, before production
navigation or entry routes change.

## 1. Agreed direction

Keep the existing tile-based dashboard. Add vertical page navigation on the left;
render the selected page on the right. This is one application with a shared
shell, not a second dashboard and not all pages stacked into one long page.

Final entry flow: signed-out landing at `/` -> existing email-code sign-in ->
Home at `/home`. The current Overview moves to `/overview`. Home is a quiet
starting point with Book equipment, Platforms, and ELN cards and personal upcoming
reservations. Do not turn Home into another fleet-status wall.

The sidebar contains Home, Overview, Bookings, Platforms, Inventory, History,
Tools, Contact admin, and Admin where permitted. Preserve existing History and
Utils destinations; “Tools” may be the label for `/utils`. Keep ELN visibly
available through Home and/or a sidebar link with a separate-window cue.
Assistant and My account are persistent signed-in actions. On small screens the
sidebar becomes an accessible menu. The public landing has no private sidebar,
assistant, equipment queries, inventory, roster, or event subscriptions.

The in-chat previews establish flow and placement only. Their green styling,
fictional stock, sample permissions, mock assistant, demo sign-in, role switch,
and provisional timezone are not production requirements. Match the existing
application design and real data sources. Never ship the demo role switch or
accept arbitrary verification codes.

## 2. Authorities and implementation baseline

Read `AGENTS.md`, the binding Part I of `AGENTIC_LAB_DESIGN.md`, `STATUS_SPEC.md`,
and `BITACORA_INTEGRATION_DIRECTION.md` before implementation. Booking semantics
remain in [BOOKING_AND_HOME_DESIGN.md](BOOKING_AND_HOME_DESIGN.md); this document
adds the sidebar-first delivery sequence and account/support scope. It does not
relax the booking or device contracts.

Inspected baseline: commit `212447f` on 2026-10-09. Reinspect on the server before
editing; other work may have landed.

| Existing location | Current behavior and intended reuse |
| --- | --- |
| `web/src/app/page.tsx` | Client Overview with equipment/platform queries, tiles, lab map, section visibility; extract for reuse without changing behavior |
| `web/src/app/layout.tsx` | Shared auth banner, providers, header, horizontal Nav, content, footer, AssistantBubble and StateReferencePanel |
| `web/src/components/Nav.tsx` | Existing destinations, platform query, role-based Admin visibility, Bitácora popup/new-tab interaction |
| `web/src/components/DashboardShell.tsx` | Chrome/content wrappers and a framed control-workspace exception; preserve these behaviors |
| `web/src/lib/user-auth.tsx`, `auth-service.ts` | Existing identity/session integration; reuse it |
| `web/src/lib/query-client.tsx` | Query provider; ensure identity changes cannot reuse private cache |
| `web/src/app/platforms/` | Existing platform pages and deep links; retain |
| `web/src/app/inventory/page.tsx` | Real chemical inventory iframe from `/bitacora/inventory/embed`; reuse the authoritative inventory, not sample consumables |
| `web/src/app/admin/page.tsx` | Existing accounts, sessions, API keys, authorization observations; currently read-only, roster edits remain in `roster.yaml` |
| `web/src/app/api/admin/` | Existing privileged proxy routes; preserve verified identity and existing restrictions |
| `web/src/middleware.ts` | Many writes protected, many reads deliberately public; not yet the proposed private application boundary |

The private Bitácora beta is retired per `BITACORA_PRIVATE_BETA.md`. Do not
reintroduce it. Production ELN is `/bitacora/` with its own permissions. Some
older visibility prose conflicts with the restored Nav link; verify source and
installed edge behavior instead of copying old comments.

## 3. Stage A: isolated sidebar preview — first implementation PR

Deliver a testable real application layout while keeping `/` and existing
production navigation behavior unchanged.

1. Extract Overview rendering into a reusable component (suggested
   `components/overview/OverviewContent.tsx`). Keep the root page as a thin
   wrapper. Preserve data hooks, loading/error states, card order, permissions,
   section visibility storage, detail links, and opt-in camera behavior.
2. Introduce a reusable `WorkspaceShell` and sidebar navigation definition.
   Keep one scroll owner, sufficient content width, `min-h-0`/`min-w-0`, existing
   sticky filter behavior, theme support, print layout, and framed-workspace
   exceptions. Do not wrap a full existing page shell inside another shell.
3. Add a server-configured preview switch, default off, and a proposed
   `/preview/dashboard` route for Home, with `/preview/dashboard/overview` for
   the actual Overview component. Disabled preview routes return 404. When
   enabled, require a verified browser session before rendering or mounting
   private components; start with existing admins as reviewers. No public
   feature flag or client role toggle may grant access.
4. Make the root layout select preview chrome without also rendering the old
   horizontal Nav, second assistant, second footer, or second auth banner.
   Keep QueryProvider and UserAuthProvider single-mounted. Route selection is
   presentation only; server verification is still required.
5. Reuse current page bodies for preview Platforms/Inventory where practical.
   If a destination exits the preview shell to an existing route, label that
   behavior explicitly during this stage. Do not duplicate API implementations,
   proxy device controls, or copy whole pages that would drift.
6. Use clearly marked in-memory fixtures for Bookings and Contact admin in the
   preview. Mock interactions must not invoke production mutation endpoints.
   Do not persist mock reservations or messages in the real backend.
7. Integrate the existing assistant once. A header trigger should open the
   same assistant experience and preserve sessions, not create a new engine.
   Keep any preview-only assistant simulation visibly distinct from live chat.
8. Keep the existing sign-in mechanism in Stage A. The new sign-in dropdown
   and public landing belong to Stage C; do not add two competing auth forms.

Acceptance: the same Overview tiles behave correctly in both old and preview
layouts; deep links, back/forward, filters, theme, fullscreen/framed panels and
print remain usable. At 360, 768, 1024 and 1440px there is no page overflow or
obstructed control. Mobile navigation supports Escape, focus return, keyboard
navigation and a clearly indicated active page. Direct unauthorized preview
requests fail before private render/prefetch. Opening the preview does not
operate devices or automatically start camera streams.

Do not invoke live equipment controls to validate the new shell. Use mocks for
control interactions; visual inspection and authorized read-only telemetry do
not establish physical readiness.

## 4. Stage B: shared navigation and existing destinations

After reviewing Stage A, promote the shell for authenticated dashboard pages.
Keep the old default entry behavior until the private boundary and landing are
ready; sidebar promotion and public-entry cutover are separate changes.

- Centralize destination labels, hrefs, active-route matching, external-window
  behavior, and role visibility. Match route boundaries, not accidental prefix
  matches. Do not fetch the whole equipment fleet just to draw navigation.
- Keep page-specific platform/category filters within their page, not the
  sidebar. Preserve equipment URLs, existing bookmarks and tool routes.
- Introduce `/overview` using the same extracted component. During transition,
  `/` can still render the old Overview until Stage C changes entry behavior.
- Add `/home` with the three agreed cards. Until bookings have a real backend,
  show an honest unavailable/coming-soon state outside the explicit preview;
  never render sample bookings as a user's real reservations.
- Keep the Inventory embed. Replace the existing hard-coded viewport offset
  with sizing that fits the new content container; test iframe focus/scroll.
- Preserve the assistant's current operational scope and persistence. Page
  context must be minimal and authorized; do not inject unrelated inventory,
  roster or scientific records automatically. Scientific capture belongs in
  Bitácora, not a second dashboard planning lifecycle.

## 5. Stage C: private boundary, landing, and sign-in

Implement the private boundary before claiming the landing protects the lab.
This includes API/edge behavior, not only the visible interface.

1. Inventory all dashboard pages, API reads/writes, inventory embeds, downloads,
   assistant endpoints, streams, service paths, and direct backend listeners.
   Record which require browser auth, scoped machine auth, or an explicit public
   exception. Keep integrations working under their existing authentication.
2. Split public and authenticated layouts. Only the landing, required assets,
   existing auth endpoints, minimal session bootstrap and reviewed data-free
   liveness endpoints are public. Anonymous prefetch must not mount private
   hooks or return private server-rendered payloads.
3. Protect Next routes, API handlers and trusted proxy boundaries consistently.
   Strip untrusted identity headers before verified injection, prevent bypass
   through backend listeners, use private/nonshared caching, and apply
   Origin/CSRF protection to cookie-authenticated mutations.
4. Preserve WebSocket matcher exclusions and ticket authentication. Independently
   validate admission/revocation for SSE, WebSockets and camera/media sessions.
   A blanket edge gate must not break machine/service auth or safety paths.
5. Check the inventory embed and underlying inventory API directly: protecting
   the iframe's parent alone is insufficient. Coordinate any required change
   with the owning application; do not silently migrate inventory storage.
6. Build one accessible top-right email-code sign-in surface using the existing
   roster service. No roster download, self-registration, demo login or duplicate
   banner. Support feedback, rate-limit errors, narrow layouts and focus return.
7. Cut over `/`: signed out -> landing; signed in -> `/home`. Default login goes
   to Home. Preserve only validated same-origin application return paths for
   protected deep links. Logout and session expiry hide private content, stop
   polling/subscriptions and clear identity-scoped private caches.
8. Keep ELN opening separately with safe opener handling and normal new-tab
   fallback. The link does not grant ELN/project permissions.

Acceptance: anonymous direct requests and forged identity headers cannot access
private pages or data; APIs return 401 JSON, and forbidden authenticated requests
return 403. Test expiry, logout, account switching, stale caches, deep links and
open redirects. Preserve authorized service clients and stream handshakes.
Apply deployment edge changes as patches to the installed configuration, never
by overwriting it with a repository snapshot.

## 6. Account surfaces — reuse before extending

My account (`/account`, proposed) shows the verified user's identity, role and
actual equipment grants, with sign-out and a request-access link to Contact
admin. Do not derive control permission from a booking or from sidebar visibility.
Never expose the full roster through a self-service endpoint.

“Account manager” is an organization of existing Admin capabilities, not a new
identity service. Initially reuse the read-only account/session/key views and
make the current roster-based edit process explicit. No password system or new
editable account database. Web-based roster/grant editing is a separate future
work item requiring one authority, validation, audit, revision checks, revocation
behavior, and explicit review; it is not part of the sidebar pilot.

Notification preferences require real durable storage before displaying a saved
state. Optional reminders may be configurable; essential booking changes and
safety-related notices cannot be disabled by a convenience preference. If that
service is not implemented, omit the settings or clearly mark them unavailable.

## 7. Contact admin and inbox — separate functional increment

Proposed routes: `/contact-admin` for members and `/admin/messages` for authorized
admins. This is an in-app operations inbox, not email/Slack delivery and not an
emergency response channel. The assistant never sends a note merely because a
user mentions an admin; a user must intentionally submit the note.

- Form: topic, optional authorized instrument/booking reference, message, submit.
  Use plain text with bounded lengths; no attachments in the first version.
- Member view: only their submitted notes and current Sent / Read / Resolved
  state. Admin view: notes in administered scope, unread count, mark-read and
  resolve actions. Read means an authorized admin opened/marked the message,
  not that a background fetch occurred. Resolution records actor and time.
- Persist messages and state transitions in a server-owned operational store
  outside Git. Fields: ID, verified sender principal, topic, scoped reference,
  body, status, created/updated timestamps, revision, and transition actor/time.
  Audit state changes; define retention and backup/restore before live use.
- Proposed API: `POST /api/admin-messages`, `GET /api/admin-messages/mine`,
  admin-scoped `GET /api/admin/messages`, and revision-checked admin state
  mutations. Final naming follows existing API conventions.
- Derive sender from verified auth, authorize every reference and individual
  message read, protect against cross-user ID enumeration, enforce CSRF and
  rate/size limits, and render text safely. Use actor-scoped idempotency on
  submission. Show Sent only after durable success; preserve drafts on failure.
- Admin status changes update the member's view. No hidden delegation to an
  external messaging service; no model-generated claim that a person read it.
- Tests: member-to-member denial, non-admin denial, admin-role revocation,
  duplicate submission, stale revision, malicious text, durable restart,
  failed-submit draft retention and accurate unread counts.

## 8. Booking delivery remains a distinct project

Use the complete booking design for scheduling, policies, maintenance, privacy,
transactions, admission and safe overrun handling. The sidebar PR must not build
an abbreviated booking backend or imply technical enforcement from UI labels.

Sequence: durable scheduling and personal reservations -> admin policies/blocks
and approvals -> one verified enforced pilot -> instrument-by-instrument expansion.
Schedule-only equipment stays explicitly labeled. Booking never grants control,
execution approval, force takeover, or automatic experiment starts. Required
booking policies cannot activate until every relevant claim path is covered.

Before activation confirm actual lab timezone, opening hours, instrument limits,
approval rules and safe handoff behavior. Preview values are not approved policy.

## 9. Validation and suggested commit boundaries

| Increment | Required evidence |
| --- | --- |
| A: Overview extraction + sidebar preview | Component/navigation tests, unchanged legacy route, authenticated preview gate, responsive review |
| B: Shared shell + Home | Route/deep-link regressions, single assistant/provider instances, inventory sizing, existing admin visibility |
| C: Private boundary + landing | Anonymous/API/proxy/cache/stream denial tests, auth lifecycle, authorized service compatibility, installed edge verification |
| D: My account + admin organization | Own-user data scope, read-only authority preserved, no accidental privilege changes |
| E: Contact admin + inbox | Persistence, authorization, idempotency/revisions, audit and notification states |
| F: Scheduling and enforcement | Full acceptance matrix in the booking design; independent device/ACL review |

Run relevant component/middleware tests as each implementation changes. In `web/`,
run `pnpm typecheck`, `pnpm lint`, targeted `pnpm test -- <test paths>`, and the
production build before rollout. Follow existing test conventions (no jest-dom).
For backend changes use `uv run pytest <affected paths> -m 'not integration'`;
add the appropriate non-integration regression suite before deployment. Never
run hardware integration tests as a side effect of UI work. Regenerate API types
when API schemas change. Do not claim a green build unless it was run successfully.
Documentation-only commits need diff/links/content validation, not application tests.

## 10. Server continuation and rollout checklist

1. Fetch the handoff branch and inspect its diff. Preserve local modifications;
   use an isolated worktree or clean feature branch based on the latest main.
   Do not reset a live server checkout to get a clean tree.
2. Read this plan and the booking design, then recheck the listed source files,
   current auth behavior and deployment instructions. Keep host addresses, paths,
   credentials and preview-switch values in existing local configuration.
3. Implement Stage A only first. Reuse the real Overview and current styling.
   Keep production `/` unchanged and the preview default-off. Develop against
   mocks/staging; record the preview URL in the handoff without committing
   deployment-specific addresses.
4. Run Stage A checks and show Home, the real Overview in the sidebar, Inventory,
   mobile navigation and assistant access for review. Record feedback and any
   remaining responsive defects before broad rollout.
5. Continue in the separate increments above. A successful UI review does not
   certify the private boundary or booking enforcement. Gate each on its own
   evidence and the deployment authorization applicable at that time.
6. Before deployment inspect installed service/configuration state, current edge
   routes and dependencies. Follow `deploy/README.md` and `deploy/edge/README.md`.
   Validate edge config before reload and smoke-test the browser and service
   paths after deployment. Do not restart unrelated services or devices.
7. Rollback Stage A by disabling/removing the preview entry; the old root remains.
   For shell promotion retain a tested prior build. After private-entry cutover,
   rollback must retain auth gates: never restore anonymous private reads as a
   convenience rollback. Operational stores require backups and forward-compatible
   recovery; never drop messages or reservation/occupancy state to revert UI.

First server task: **build the protected sidebar preview around the existing
Overview, preserving the current dashboard and all device behavior.**
