# Camera viewing sessions

Live video is an authenticated, bounded resource, not a public equipment GET.
The broker in `api/app/camera_streams.py` verifies credentials directly with
ac_auth (never trusts browser X-Auth headers), checks the registered camera's
account scope, and issues a one-use 30-second ticket. The ticket is the first
WebSocket frame, never a query parameter. Only registered camera/lens names and
receive-only MSE or WebRTC messages reach go2rtc. Camera control remains separate.
Registered `transport: mjpeg` equipment cameras use the same admission,
authorization, heartbeat and lease model. The broker proxies only the fixed
`stream_path` joined to that equipment's registered `base_url`; the browser
never supplies or receives an upstream URL. The Opentrons Flex is the first
such embedded camera and its capture lifecycle remains owned by
`sdl2-gibbie-server`, not the Kasa/Tapo gateway.

## Viewer behavior

Show stream remains opt-in. Interactive hidden tabs stop after three seconds;
returning to the tab requests a new session. Two tabs on the same feed share the
relay input but consume two viewer reservations. A 20-second authenticated
heartbeat renews the 60-second server lease. Missing heartbeats, revoked grants,
expired tickets and disconnected clients release capacity. Playback stalls for
30 seconds or more stop the player; MSE buffering is bounded to 4 MiB and
transport retries are limited to three with increasing delays. Permission and
capacity refusals require an explicit retry, not an automatic reconnect loop.

The initial admission limits are one distinct camera/lens input, six viewers
globally, three per account, and a conservative 6 Mbps reservation budget:
1.5 Mbps per source plus 1.5 Mbps per viewer, assuming both cross Wi-Fi. Thus
the budget currently admits at most three viewers on the one feed. These are
configurable capacity reservations, **not measured traffic shaping or a claim
that every camera is exactly 1.5 Mbps**. Measure before raising them.

Environment: `CAMERA_MAX_SOURCES`, `CAMERA_MAX_VIEWERS`,
`CAMERA_MAX_VIEWERS_PER_USER`, `CAMERA_FEED_BUDGET_MBPS`,
`CAMERA_NETWORK_BUDGET_MBPS`; optional `GO2RTC_BASE` and `AUTH_SERVICE_BASE`.
To allow two distinct feeds without increasing the reservation budget, set
`CAMERA_MAX_SOURCES=2` in the deployment's private environment file. This allows
two feeds with one viewer each, or one feed shared by up to three viewers.
A second feed is refused while two or more viewers already share the first:
one viewer must release capacity first. Restart the API outside active workflows
to load environment changes; existing viewing leases and approvals end on restart.
Run exactly one dashboard API worker: the broker is process-local. Multiple
workers require a shared admission/ticket store and distributed revocation first.

## Humans, kiosks and agents

`/admin/camera-streams` lists current sessions and allows a human administrator
to disconnect one viewer or create/revoke monitoring approvals. Each approval
names one account, camera feeds, purpose and expiry (maximum 24 hours).
Approvals never bypass capacity or existing camera authorization.

Humans can open the generated `/utils/camera-monitor` link, sign in and click
Start approved monitoring. This explicit mode may remain active in a hidden tab.
An agent supplies its own X-Api-Key and approval ID. Agent live viewing additionally
requires an existing running workflow ID in the dashboard's approved executor;
the approval ends when that run ends or aborts. External workflows fail closed:
they need a reviewed run-state integration, not an invented run ID. Ordinary
agents should use the existing scoped snapshot/status tools, not an unattended
browser. Viewing does not authorize hardware actions or establish physical truth.

Protocol:

1. POST `/api/camera-streams/sessions` with `{stream, grant_id?}`.
2. Open the returned `ws_path`; first message `{type: "session", value: ticket}`.
3. Send one MSE codec handshake or a receive-only WebRTC offer, then ICE candidates
   for WebRTC. Other commands/URLs are refused.
4. POST `/api/camera-streams/sessions/{id}/heartbeat` every 20 seconds with the
   original account's valid credentials. Missing renewals cause server teardown.
5. DELETE `/api/camera-streams/sessions/{id}` on completion; stop on errors and
   report lost visibility. Never interpret lost video as a completed workflow.

Approvals and leases are runtime-only and disappear on API restart. Start/end,
approval and revocation records are logged without tickets or camera credentials.
MSE bytes are counted; WebRTC media bypasses the HTTP proxy and is not included
in that counter. Admission is conservative for both transports. This first
version does not pre-reserve capacity before a workflow starts; admission must
succeed before depending on live monitoring.

## Relay lifecycle and deployment

Stock go2rtc v1.9.14 does not close WebRTC media when its signaling WebSocket
closes. The pinned patch in `deploy/patches/go2rtc-viewing-leases.patch` attaches
media cleanup after stream attachment, including the setup/disconnect race.
WebRTC offers are refused unless the relay reports the `-lab-lease1` build marker;
MSE remains available independently. Keep an original binary for rollback.

Deployment order, outside an active dashboard-executed workflow:

The prepared cutover helper is `tools/deploy-camera-viewing.py --check` (read
only). After confirming no dashboard workflow is active, an administrator runs
it with `--apply --confirm-no-active-workflow` under sudo. It uses the staged
artifacts in `.run/camera-rollout`, keeps rollback copies, patches only the
camera sections of the installed Caddyfile, and uses an ETag to refuse overwriting
concurrent live edge changes. A failed step stops for inspection; this is not an
unattended recovery script. Inspect the reported backups before retrying.

1. Build/test the relay patch and web app in separate directories. Preserve the
   running build and all existing unrelated repository changes.
2. Install the patched relay with a rollback copy and restart it once.
3. Restart the API with the broker; verify unauthenticated requests fail and
   authorized session creation/renewal/teardown work.
4. Swap the staged web bundle and restart web. Old tabs may need one refresh.
5. Change only camera routes in the **current** live Caddy configuration:
   `/api/camera-streams/*` → API; `/streams/*` → 410. Patch the same sections in
   `/etc/caddy/Caddyfile` for persistence. Do not replace the whole live config
   with the repository template: other runtime routes may differ.
6. Verify HTTP, HTTPS and direct :8000 cannot reach raw relay APIs. Remove the
   temporary client-specific block only after the universal gate is verified.

No instrument service restart or credential rotation is bundled into this change.
Review potentially exposed credentials and schedule rotation separately. Agents
with unrestricted local shells can still reach loopback services; application
viewing permissions are not an OS sandbox. Production unattended agents must not
have relay-admin access or broad local shells.

Rollback preserves security: retain the `/streams/*` denial while restoring the
old API/web bundle or relay. Restoring the stock relay disables managed WebRTC
until the patched relay returns; do not reopen anonymous playback as a fallback.
