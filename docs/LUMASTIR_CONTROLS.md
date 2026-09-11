# Lumastir dashboard controls

The Complexation tile has three columns, corresponding to verified output pairs:

| Column | Motor stable ID | LED stable ID |
|---|---|---|
| Vial 1 | motor_0 | led_17 |
| Vial 2 | motor_4 | led_18 |
| Vial 3 | motor_8 | led_27 |

The tile resolves the current command index from each stable ID in live status.
Motor On uses the column's selected percentage (initially 50); Off writes zero
only to that motor. Set applies a new percentage to a running motor. LED On
uses the configured maximum PWM and Off writes zero only to that LED. Values
are commanded PWM, not measured RPM or optical intensity. Unknown or stale
status disables individual controls. All off remains available to an authorized
operator during a pending command or a device fault.

## Claims and authorization

All requests use the same-origin `/api/equipment/lumastir/control/{action}`
route. Sign-in and the existing equipment authorization check are required.
The first action acquires a 30-second claim; the page retains its token only
in memory and sends heartbeats at most five seconds apart. Changing one
output does not release the claim or affect the other outputs. Leaving the
page, losing authorization, or an action/heartbeat failure releases the claim
best-effort and stops renewal. If release cannot reach the device, lease expiry
stops every output. Browser suspension can also cause lease expiry.

No command is automatically retried. Reconcile reported output state after an
ambiguous failure before issuing another action. All off calls the claim-exempt
device stop operation, then releases this page's claim if it holds one.

The server accepts only claim, heartbeat, release, motor/set, led/set, and stop
for this equipment. It uses `EquipmentClient`, validates bodies, checks live
`allowed_actions` for setpoints, and audits outcomes without recording tokens.
It does not use the generic per-request claim/release sequence, which would
immediately turn off the outputs after every click. Other equipment retains
its existing proxy behavior.

## SDK and API reference

`skills_for("other", "lumastir")` returns `lumastir.motor.set` and
`lumastir.led.set`; `skills_for("other")` and unrelated equipment IDs do not.
Session discovery, plan validation, interlocks, plan execution, and the dashboard
API catalog use the equipment-specific lookup. These are setpoint commands;
keep the SDK claim alive for the required operating period. Releasing it stops
all outputs. Approved-plan and recording requirements remain unchanged.

Timed runs remain documented on the device API but are not exposed as a
fire-and-forget dashboard/plan action: safe timed-run SDK integration must wait
for the job outcome while retaining the claim. Stop-all is an attended safety
control, not a new assistant-proposable skill.

The API Reference lists both typed setpoint schemas and links to Swagger,
OpenAPI JSON, and the agent guide. No device API changes are needed for this tile.

## Validation and deployment

Run the Lumastir proxy, catalog, tile, and claim-lifecycle tests with mocked
transport; no physical activation is needed. Run plan/session regressions and
a production web build. Deploy the approved source/configuration changes,
build the web standalone output using the normal package build command, then
restart the dashboard API and web services. Verify the catalog and live status
read-only. Do not energize hardware as a deployment test.
