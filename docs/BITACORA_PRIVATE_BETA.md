# Private Bitácora beta

The admin page offers `/bitacora-beta` only when the server authorizes the
current browser session for the private beta. The server setting
`BITACORA_BETA_TESTER_EMAIL` selects one account; it must also have the verified
admin role. An unset setting disables access. Keep the tester identity in local
service configuration, not client bundles or a hard-coded roster exception.

The literal Next route `/api/admin/bitacora-beta` verifies the session cookie
with ac_auth. It does not forward API keys or trust incoming identity headers.
It returns a link and verified identity/project headers only for the selected
tester, and fails closed on an expired session or unavailable auth service.
`DASHBOARD_CONTROL_OPEN` does not bypass this route's check.

Caddy uses this same endpoint as `forward_auth` for every beta page, asset, and
API request, then proxies to the loopback beta frontend on port 13001. Merely
hiding a link would not protect the route. Other admins are excluded too.
The admin page's other features and existing `/bitacora` route are unchanged.

Since 2026-09-11 the production notebook (`/bitacora/`) is also **hidden from
the dashboard's tab row** while it is under test: the Nav's Notebooks tab and
the platform placeholder's "Open the notebook" button are gone, and the one
dashboard entry point is an admin-only link on the same admin page, above the
beta link. The stale `/notebooks` bookmark redirect follows suit (admins reach
`/bitacora/`, everyone else the Overview). This is **visibility only** — unlike
the beta prefix, `/bitacora/` stays gated at the edge by sign-in, not by role,
so any signed-in account that knows the URL can still open it. Restoring the
tab is a one-line revert in `web/src/components/Nav.tsx`.

Deployment order: prepare isolated Bitácora/BitacoraDB services and storage,
test them, install the dashboard build and tester setting, then apply the beta
Caddy block to the current installed configuration without overwriting unrelated
edge changes. Validate configuration before reload and check denial for anonymous
requests and forged headers. An actual browser login is needed to verify the
final permitted-user path; do not manufacture production sessions for testing.

Data lives under `/data/bitacora-standalone/`; the existing `/data/bitacora/`
deployment remains separate. No GitHub credentials or live hardware connector
are configured for beta. OpenRouter can be configured separately. The code and
schema of BitacoraDB are unchanged; beta has its own database, credentials and
file storage. See the Bitácora repo's `docs/STANDALONE_BETA.md` for product limits
and backup requirements.
