# Opentrons HTTP control integration

This integration is pinned to `opentrons-server` commit
`e5936de28b20d9296d94c21c2050f2e0e66fa571` on
`feat/http-actions-assistant-safety`. The running gateway remains the authority:
`GET /status` supplies identity, activity, errors and current
`allowed_actions`; `GET /docs/agent` supplies equipment guidance;
`GET /plans/actions` supplies the model-specific proposal catalog and request
schemas; and `GET /openapi.json` proves that the corresponding HTTP routes are
installed. A source checkout or SDK version does not prove a device deployment.

`EquipmentClient.discover()` reads the three optional documentation endpoints.
A 404 or 405 is reported in `EquipmentDiscovery.unavailable`, which identifies
a reachable older deployment. Transport errors, server errors and malformed
documents still fail. On an older gateway the dashboard assistant shows only
actions present in the live status and does not infer the new surface.

The SDK catalog contains the typed union of the OT-2 and Flex plan actions:
liquid handling (`blow_out`, `touch_tip`, `mix`, `air_gap`, optional dispense
`push_out`), pipette settings and homing, heater-shaker, temperature, magnetic
module and thermocycler operations, plus `comment` and `delay`. The Flex union
adds gripper motion/jaw/home actions and explicit movable-trash registration.
Schemas match the gateway's units and bounds. Runtime discovery selects the
actual robot profile, so Flex actions are never advertised for an OT-2 and
magnetic-module actions are never advertised for Flex.

The dashboard assistant has read and proposal tools only. For a liquid handler
it reads the deployed documentation before proposing and verifies registry,
status and documentation identity. A proposal must be in the deployed plan
catalog and its first step must also be in live `allowed_actions`. The existing
browser review card, operator authorization, claim forwarding, device
preconditions, per-step recheck, tip tracking and stop-on-refusal behavior are
unchanged. Startup, shutdown, pause, resume, stop and reconcile remain in the
embedded operator panel and cannot be proposed by the assistant.

The assistant resolves loaded pipette and labware references from
`details.snapshot.pipettes` and `details.snapshot.labwares`. It does not treat
`details.session_recipe` as observed state. Deck and tip-rack state are shown to
the operator before authorization. `deck.declare` is still a full-layout
replacement, and explicit zero gripper offsets are retained.

`move_to` preserves `force_direct`, `speed` and `minimum_z_height` exactly.
`force_direct=true` means a straight move without a Z-retract waypoint. Holding
height during XY motion therefore requires the destination Z to equal the
current Z; neither the SDK nor assistant inserts a home, retract or safe-Z
waypoint. Flex absolute gripper motion defaults to direct axes, and relative
`dz=0` retains height.

Flex decks use A1-D4 locations. This release supports full 1- and 8-channel
50/1000 µL heads, explicit trash locations in deck columns 1 or 3, and the
gateway's 218 mm Z limit. It does not enable 96-channel or partial layouts,
assume fixed trash, or import a 250 mm sample-prep safe height. The two existing
OT-2 registrations and operator-panel routes remain unchanged; a Flex requires
a separate gateway instance and registry entry.

`POST /control/stop` is a claim-gated operator software stop for the owned HTTP
run. The embedded panel's **STOP RUN** remains its dashboard surface so it stays
separate from assistant proposals and can use its own connection while another
request is pending. Success requires stopped readback. A failed stop has unknown
outcome, is not a physical emergency-stop guarantee, and does not guarantee all
heater-shaker activity has ended. Aborting a dashboard plan prevents later
steps; it cannot interrupt a command already in progress. The persistent stop
latch must not be deleted to force recovery.

## Release and deployment

Merge and deploy the central dashboard change first or together with the
gateway release. On the dashboard host, pull the reviewed commit, sync the uv
workspace, build the Next.js standalone bundle, copy its static assets, and
restart the API and web units as described in [deploy/README.md](../deploy/README.md).

Gateway deployment is a separate Windows device-PC release. After the server
branch is reviewed and merged, update one isolated deploy checkout at a time.
If dependencies change, stop only that gateway service, run
`uv sync --extra labware` as the service account, then start it; otherwise use
the repository's `git pull` → sync → `nssm restart <service>` procedure. Never
bulk-restart both gateways. Confirm the deployed identity and surface over
`/status`, `/docs/agent`, `/plans/actions` and `/openapi.json`. The HTE robot is
not an acceptance-test target; any later physical acceptance is an explicit
operator action on the Complexation robot under the server repository policy.

The gateway assistant has separate configuration from this dashboard
assistant. Enabling it requires a valid caller `X-Claim-Token`; when using
`OPENAI_API_KEY`, configure `OT2_ASSISTANT_BASE_URL` explicitly and choose a
compatible model. These settings do not expand the central assistant's access.
