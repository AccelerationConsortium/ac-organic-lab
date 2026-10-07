# Robot Motion (UR5e) panel

The Ligand Development UR5e keeps a compact status tile with the same orange
**Open control panel ↗** link used by xArm. Since 2026-10-06 it opens the
device's own panel at the edge path **`/ur5e/web/`** in a new tab, exactly the
arrangement the xArm uses at `/xarm5/web/` (`web/src/lib/device-panels.ts`).
The Process Chemistry UR5-CB3 and Gibbie UR3e remain observation-only tiles
without this link.

The panel itself is the shared xArm web UI served by the `robot-motion`
service on the Prototyping PC (`sdl2-pc-05`, :8075); the robot-motion repo
substitutes only the tab title ("UR5e Control") and offers the configured
robot as the single "Connect to" profile. Joint and TCP readings come from the
service's RTDE receive stream.

## Edge route and identity

`deploy/Caddyfile.single-edge` has a `/ur5e` block of the same shape as
`/xarm5`: `forward_auth` to ac_auth gates every path except `/ur5e/ws` (the
WebSocket upgrade cannot carry the auth round-trip; it is read-only status
push), `handle_path` strips only the `/ur5e` prefix, and the proxy injects
`X-Edge-Auth` from `ROBOT_MOTION_EDGE_SHARED_SECRET` in
`/etc/dashboard-staging/device-edge-secrets.env`. The panel's JS derives the
`/ur5e` base path from its URL and prefixes its own API and WS calls, so no
rewrite is needed.

The device trusts the injected `X-Auth-User` only when `X-Edge-Auth` matches
the same secret in its own service environment. Without the secret on the
device, the panel still renders read-only; the device simply reports no
identity and refuses every control route (503), so provisioning the secret is
what makes the edge identity usable there, never a bypass.

## Cameras

Since 2026-10-07 the panel's camera tile is live, matching the xArm's
(device commit `627e7e6`):

- **Tapo Camera** — the bench's `cam_ligand_tapo_d246`. Video comes from this
  dashboard's `/api/camera-streams` broker, which is why it plays only when
  the panel is opened through the edge and the viewer is signed in. The PTZ
  pad and preset picker post to the device, which forwards them to the
  camera's control passthrough as the operator (their `ac_auth_session`
  cookie or `X-Api-Key`); a viewer with no role on the camera gets this
  dashboard's 403. The "Follow arm" toggle is hidden: the UR has no motion
  graph to follow.
- **Stereo Camera** — the D435i on the Prototyping PC, served by
  `sdl-camera-server` (127.0.0.1:8070) and proxied by the device at
  `/realsense/d435i/*`: Start/Stop, Color/Depth, click-to-measure distance.

Agents discover both from the device's `GET /cameras` (declared as
documentation on `ligand_ur5e`, so it appears in `/api/catalog`) or its
`/agent-docs` Cameras section. Details and gating: `EQUIP_STATUS.md` §10,
"UR5e (`ligand_ur5e`) cameras".

## Physical-control boundary

The device's `/control/*` routes (claim, connect, joint_step, stop) exist only
when its local config enables them; the deployed `ligand_ur5e` config does
not, and the UR5e is not commissioned. The panel's Take Control, Connect and
motion buttons therefore receive 404 today. Enabling control is a device-side
config change plus a restart in an authorized window, documented in the
robot-motion repo (`JOINT_STEP_CONTROL.md`); nothing on the dashboard side
widens it.

## What was retired

The dashboard-side `/api/robot-motion/*` proxy (`api/app/robot_motion.py`),
its CSP/asset allowlist, and the `/utils/robot_motion` framed page were
removed on 2026-10-06. They served the service's earlier UR-specific UI, which
no longer exists; the shared panel's inline theme script and `?v=` asset
queries would have failed that proxy's `script-src 'self'` and query-free
allowlist in any case. `/utils/robot_motion` is now an ordinary (404) route
with normal dashboard chrome.

## Deployment

1. Edge: add `ROBOT_MOTION_EDGE_SHARED_SECRET=…` to
   `/etc/dashboard-staging/device-edge-secrets.env`, deploy the Caddyfile,
   reload the edge (see `deploy/edge/README.md`).
2. Device: set the same `ROBOT_MOTION_EDGE_SHARED_SECRET` in the
   `robot-motion-prototype` NSSM environment and restart that service only.
3. Verify from a tailnet host:
   `curl -s -o /dev/null -w '%{http_code}\n' http://100.64.254.6/ur5e/web/`
   → 401 (gate), not 502; signed in, the panel shows live UR5e readings.

Offline regression coverage: `web/src/components/UrMonitorTile.test.tsx`
(link target) and the robot-motion repo's `tests_robot_motion` suite (panel
serving, title substitution, profile list, control gating).
