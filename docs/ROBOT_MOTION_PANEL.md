# Robot Motion (UR5e) panel

The Ligand Development UR5e has a control tile of the xArm's shape
(`web/src/components/UrArmTile.tsx`, since 2026-10-11): an INIT / ON toggle
for the device's arm session, STOP, the controller / safety / gripper
readings, and the same orange **Open control panel ↗** link. Since 2026-10-06
that link opens the device's own panel at the edge path **`/ur5e/web/`** in a
new tab, exactly the arrangement the xArm uses at `/xarm5/web/`
(`web/src/lib/device-panels.ts`). The Process Chemistry UR5-CB3 and Gibbie
UR3e remain observation-only tiles (`UrMonitorTile`) without this link.

The panel itself is the shared xArm web UI served by the `robot-motion`
service on the Prototyping PC (`sdl2-pc-05`, :8075); the robot-motion repo
substitutes only the tab title ("UR5e Control") and offers the configured
robot as the single "Connect to" profile. Joint and TCP readings come from the
service's RTDE receive stream.

## Gibbie UR3e (`gibbie_ur3e`)

Since 2026-10-12 a second robot-motion instance runs on the Gibbie PC
(`sdl2-pc-04`, NSSM `robot-motion-ur3e`, Tailnet :8075, branch `debug-ur3e`)
pointed at that bench's UR3e. It started the day as a read-only RTDE
observer and gained claim-gated control the same night, commissioned with
the user at the arm: the arm session, moves inside an open-placeholder
motion block, manual mode, force zeroing and the Robotiq 2F-85 (robot-motion
`src/docs/UR3E_GIBBIE_PLAN.md` has the record). The dashboard therefore
treats it exactly like `ligand_ur5e`: `UrArmTile` (INIT / ON for the arm
session, STOP, the same readings and the orange panel link to the edge's
`/ur3e/web/`), `edge_secret_env: ROBOT_MOTION_UR3E_EDGE_SHARED_SECRET` and
`control_dialect: root` on the entry. The secret is the UR3e's own and
follows the same two-places rule as the UR5e's: the edge's env file for the
`/ur3e` block (`deploy/Caddyfile.single-edge`) and the dashboard API's
edge-secrets env file for the tile's passthrough calls. Until both hold it,
the panel serves read-only and the tile's INIT and STOP answer 401. The
device's own service environment already has it. `gibbie_ur_arm`, the
monitor's Dashboard-Server probe of the same arm, stays next to it until
retired.

One difference from the UR5e: the bench workflow (`sdl2_solid_dosing_XG` on
the same PC) drives this arm over RTDE control during its runs, and a UR
controller runs one control script at a time, so an arm session here and a
Gibbie run exclude each other. Do not INIT while `gibbie_workflow` reads
busy; a device-side interlock is a planned stage.

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

The device's control routes exist only when its local config enables them.
Since 2026-10-08 the deployed `ligand_ur5e` config does: claim-gated arm
motion inside the (still placeholder) commissioned limits, the Robotiq
gripper, manual mode and force zeroing, plus joint trajectories since
2026-10-11 (robot-motion `ARM_MOTION.md`, `GRIPPER_CONTROL.md`,
`UR5E_TRAJECTORY_PLAN.md`). Nothing on the dashboard side widens it.

What the tile offers is the device's claim-free lifecycle and safety floor,
exactly what the xArm tile offers:

- **INIT / ON** — `POST /connect` / `POST /disconnect`, the arm session.
  Observation (RTDE receive) is always on, so the device's
  `equipment_status` reads `ready` whether or not a session is open; the
  toggle therefore follows `details.control_session.open`, not the status.
  The device advertises `connect` / `disconnect` in `allowed_actions` only
  when it would accept them (pendant in Remote Control, nobody else holding
  the claim, arm still), and the toggle follows that too, with the reason in
  its tooltip.
- **STOP** — `POST /move/stop` (the device's alias of `/control/stop`;
  identity-only). It stops the arm, a running trajectory and the gripper.
  Offered while the session is open or the gripper is moving; before Connect
  there is nothing to stop and it is greyed, as in the panel.
- **No CLEAR.** The device's Clear errors (`/control/reset`, alias
  `/clear/errors`) clears *the claim holder's* stop latch and is claim-gated,
  so it lives in the panel. The tile shows a `latched` pill instead.

Moves, the gripper, manual mode and Take Control stay in the panel, which
greys Take Control and STOP until the arm session is open (robot-motion
`main.js?v=20261011-ur-session-gates`).

These three tile actions reach the device through the control passthrough
(`/api/equipment/ligand_ur5e/control/{connect,disconnect,move/stop}`), which
presents the signed-in operator as the device's trusted-edge identity:
`X-Auth-User` plus `X-Edge-Auth` from `ROBOT_MOTION_EDGE_SHARED_SECRET`
(`edge_secret_env` on the registry entry). The device accepts no cookie or
api key of its own, so without that variable in the **dashboard API's**
environment every tile action answers 401 while the panel keeps working
(it goes through Caddy, which has the secret). The operator must also be on
the device's `authorized_operators` list.

## Agent skills (joint trajectories)

Since 2026-10-12 `skills_for("robot_arm", "ligand_ur5e")` carries the seven
`trajectory.*` skills (`skills/src/lab_skills/skill_catalog/ur_arm.py`):
`limits` and `status` (GET), `validate`, `create`, `upload_chunk`, `start`
and `cancel`, the xArm's joint-trajectory contract as the UR5e runs it (six
joints, 125 Hz default, servoJ; client guide: robot-motion
`src/docs/TRAJECTORY_API.md`, "UR5e"). They are separate definitions, not
the xArm's list, so each arm's schema states its own joint count and gates.
`/api/catalog` and the API Reference page list them with their schemas.

Three things make them usable from here rather than only from the edge's
`/ur5e` route:

- The device advertises the same names in `/status.allowed_actions`
  exactly while each call would be honoured (validate and start only while
  a moveJ would run, that is with the arm session open and the arm still;
  cancel only while a session is open; create withheld while one is open),
  so `lab.skills()` availability and `execute_plan`'s pre-step checks need
  no mapping. Every entry also requires the device's `control` component
  to read `enabled`, which the device sends only with control on, so a
  receive-only robot-motion (an empty `allowed_actions`) never offers them
  through the SDK's `requires_states` fallback. Devices before
  robot-motion's 2026-10-12 build send neither the names nor the component,
  which reads as "never available".
- The control passthrough presents the operator as the device's edge
  identity (`edge_secret_env`, above) and forwards a caller's own
  `X-Claim-Token` unchanged. A trajectory session belongs to the claim that
  created it, so a client claims once through
  `/api/equipment/ligand_ur5e/control/claim`, then sends that token on
  create, upload, start, status and cancel (the dashboard adds no authority;
  the device verifies the token). Without a caller token the proxy's
  per-request claim would be released after `create`, and `start` would be
  refused as another claim's.
- Only `start` moves the arm, under the device's own gates: an operator has
  Connected the arm (the tile's INIT or the panel), Remote Control on, the
  pendant slider at 100 %, not latched, manual mode off. STOP (the tile or
  `POST /control/stop`) halts a run at once.

The older `ur.joint_step` entry stays as the joint-step contract; the
deployed service runs its `motion` mode, which has no such endpoint, so the
device never advertises it.

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
4. Dashboard API (for the tile's Connect / Disconnect / STOP): add the same
   `ROBOT_MOTION_EDGE_SHARED_SECRET=…` to the api unit's edge-secrets
   `EnvironmentFile` (`/etc/dashboard-integrations/ac-organic-lab-api.service.edge-secrets.env`)
   and restart `ac-organic-lab-api`; `journalctl -u ac-organic-lab-api | grep
   edge_secret_env` must stop reporting the variable as empty. Then, signed
   in with a role on `ligand_ur5e` and with the pendant in Remote Control,
   INIT opens the session (the panel's connection line turns on) and STOP
   answers `stop_requested`.

Offline regression coverage: `web/src/components/UrArmTile.test.tsx` (session
toggle, STOP gating, link target), `UrMonitorTile.test.tsx` (the two
observation-only arms), `skills/tests/test_registry.py` (edge secret and
dialect on the entry) and the robot-motion repo's `tests_robot_motion` suite
(panel serving, title substitution, profile list, control gating).
