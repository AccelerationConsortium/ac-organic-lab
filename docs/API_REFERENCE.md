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

These declarations describe where documentation is expected, not proof that
every deployment has it. A running older server's 404 response is preserved
unchanged. This makes version skew visible in the API Reference rather than
substituting a newer local schema.
