# Robot Motion dashboard workspace

The Ligand Development UR5e keeps a compact status tile, with the same orange
**Open control panel ↗** link used by xArm. It opens `/utils/robot_motion` in a
new tab, inside the dashboard's existing SDL2 login layout. The Process Chemistry
UR5-CB3 and Gibbie UR3e remain observation-only tiles without this link.

The UR5e route is a standalone presentation: the shared SDL2 auth banner stays
at the top, with the Control Interface filling the remaining viewport. Dashboard
heading/navigation, utility tabs, footer and assistant overlays are omitted on
this route only. The embedded interface retains its own read-only safety notice.
This is layout selection, not an authentication exception.

## Authentication and scope

The page loads `/api/robot-motion/ligand_ur5e/web/index.html` only after sign-in
and an equipment-access check. `api/app/robot_motion.py` independently verifies
the session cookie with the SDL2 auth sidecar, then checks the verified account's
UR5e grant on **every** panel, asset, status, and graph request. API keys,
caller-supplied identity headers, and `DASHBOARD_CONTROL_OPEN` do not bypass this
gate. Logout/revocation stops subsequent requests; it cannot erase a local graph
already downloaded into a browser.

The dashboard proxy permits only the bundled HTML/CSS/JS assets, GET status,
driver inventory and configured graph, and POST topology validation / route
preview. Those POSTs do not save or execute a graph. Documentation links use
the existing read-only documentation renderer, with Swagger execution disabled.
No control, claim, WebSocket, arbitrary file, URL, or redirect passthrough exists.
Credentials and identity headers are never forwarded to the device service.
Responses are bounded and non-cacheable. API requests have a 15-second total
deadline; graph bodies are limited to 256 KiB.

This works through the normal Next API rewrite, including direct dashboard
access: no new Caddy exception or device route is needed. Next overwrites
`X-Forwarded-Host`; the loopback-only API uses it for the graph POST Origin check.
Keep that deployment trust boundary when adding another reverse proxy.

Direct dashboard URLs also serve the existing shared SDL2 auth banner. Next
rewrites only `/auth/banner.js` and the five public login routes (`me`, `users`,
`login`, `verify-code`, `logout`); the JSON routes reuse `/api/auth/*` handlers
and their existing cookie policy. Sidecar admin/authz routes are not exposed.

## Physical-control boundary

This is the Robot Motion **prototype workspace**, not commissioned UR control.
The service observes controller status and supports offline graph editing.
Physical controls remain unavailable, and the tile has no claim or lifecycle
hooks. Topology validation does not establish collision safety, reachability,
calibration, or authorization to execute. Future physical control needs a
separate reviewed SDK/claims integration; do not widen this proxy allowlist.

The underlying read-only service remains reachable by its existing Tailnet
clients and does not itself implement SDL2 login. This change authenticates the
dashboard workspace, not the raw device port. Restricting that port to the
aggregator or adding device-side auth is a separate deployment change.

## Deployment

The workspace requires an enabled `ligand_ur5e` HTTP registry entry with a
configured service URL. Missing or disabled registration returns 503 without
contacting the equipment; installing the dashboard code alone does not register
or connect an arm.

Stage and deploy the dashboard API/web with `tools/deploy-dashboard.py`; follow
the human-only cutover instructions in [deploy/README.md](../deploy/README.md).
The Robot Motion UI package is deployed independently to its isolated device-PC
service. Do not restart chromatography, xArm, or unrelated device services.

The UPLC prototype runs an installed wheel in its isolated environment, not an
editable import from the retained source checkout. The wheel includes the
xArm-style workspace and its allowlisted shared assets. Updating checkout files
alone will not update the running UI: install the reviewed wheel without changing
dependencies, validate it offline, and restart only `robot-motion-prototype`.
Keep an environment rollback copy under the project's ignored `.state` directory.
The read-only config and NSSM service arguments remain separate from the package.

Offline regression coverage: `api/tests/test_robot_motion.py`,
`web/src/components/UrMonitorTile.test.tsx`, and
`web/src/app/utils/robot_motion/page.test.tsx`.
