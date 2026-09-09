# Dashboard API Reference and equipment documentation

The dashboard's **Utils → API Reference** page combines two read-only sources:

- `GET /api/openapi.json` describes the dashboard API that is actually
  running.
- `GET /api/catalog` describes registered device actions and the equipment
  documentation links declared in `equipment.yaml`.

Equipment documentation uses the same dashboard origin so a remote browser
does not try to open a device-only hostname or the dashboard host's
`127.0.0.1`. The allowlisted route is:

```text
GET /api/equipment/{equipment_id}/documentation/{document_path}
```

Only `documentation:` paths on that equipment's registry entry are accepted.
The route cannot proxy arbitrary device paths, accepts no write method, does
not claim equipment, and never calls `/control/*`. Swagger pages are rendered
by the dashboard against the registered, proxied OpenAPI JSON.

The two `opentrons-server` entries publish links for `/docs`,
`/openapi.json`, `/docs/agent`, and `/plans/actions`. The Bambu Lab gateway
publishes `/docs` and `/openapi.json`; its OpenAPI document includes the
gateway, per-printer telemetry, queues, and submission lifecycle. Per-printer
registry entries do not duplicate the gateway-level links.

For Bambu, the live OpenAPI `LoadedTray` model describes inventory fields used
by the printer tiles: `ams_id`, `tray_id`, `tray_color`, `tray_color_name`,
`tray_color_source`, and nullable `remaining_percent`. The source enum is
`bambu_color_match`, `operator_declared`, `generic`, or `unknown`. An
operator-declared Transparent label is not inferred from an all-zero color.
Profiles withhold stale observations; the status envelope keeps inventory
under `details.ams_trays` and empty units under `details.ams_unit_ids`.
The typed frontend interface `BambuAmsTray` mirrors these display fields.

The Bambu reference links to `/bambu/docs` for the authenticated gateway API;
all schema URLs there retain the edge prefix. The dashboard's generic Swagger
viewer disables “Try it out”: its allowlisted proxy serves documents only and
must not suggest that it forwards submission or hardware operations. The
gateway's submission/approval records still do not dispatch prints.

Full gateway field and authentication semantics:
[`bambu-server/docs/API_REFERENCE.md`](../../bambu-server/docs/API_REFERENCE.md).

These declarations describe where documentation is expected, not proof that
every deployment has it. A running older server's 404 response is preserved
unchanged. This makes version skew visible in the API Reference rather than
substituting a newer local schema.
