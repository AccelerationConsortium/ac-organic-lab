# Private Bitácora beta (retired 2026-10-09)

**Status: retired.** The `/bitacora-beta` prefix, its `forward_auth` gate
(`/api/admin/bitacora-beta`), the admin-page link and the
`BITACORA_BETA_TESTER_EMAIL` setting were removed on 2026-10-09. The beta
existed to trial Bitácora on local Git projects instead of GitHub. That work
landed on Bitácora's `main` (AccelerationConsortium/bitacora PR #89) and
production cut over to it on 2026-10-01, so the beta had nothing left to
prove: one tester, no writes to its stores after September 2026.

Removed from this repo: the Caddy block in `deploy/Caddyfile.single-edge`,
the Next route and its tests, and the admin page's tester-only query. The
production notebook link on Admin, previously visible only to the tester, now
shows to any admin. `/bitacora/` itself keeps its sign-in gate at the edge
and stays out of the tab row (below).

On the host, retirement is operator work with sudo, in this order: a final
`python -m bitacora.backup create` of `/data/bitacora-beta` (the Bitácora
repo's `docs/BACKUP_RESTORE.md`, with a beta config), `verify` it; remove the
beta block from `/etc/dashboard-staging/Caddyfile`, `caddy validate`, reload
`dashboard-edge`; stop and disable `bitacora-beta`, `bitacora-beta-frontend`,
`bitacoradb-beta`, `bitacoradb-beta-preview` and stop the
`bitacoradb-beta-staging-postgres` container; drop the tester `Environment=`
line from the installed `ac-organic-lab-web.service` and restart it. The data
under `/data/bitacora-beta/`, the checkout `~/caoyang/bitacora-beta` (branch
`feat/standalone-foundation`, pushed), and the env files under
`/etc/dashboard-integrations/bitacora-beta-*.env`,
`/etc/bitacoradb-beta-staging/` and `/data/shared/final-sync/*beta*` stay
until the final backup has been verified and the operator deletes them.

## How it worked (historical, 2026-09-08 to 2026-10-09)

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
Both notebook links on Admin require this same verified tester grant.
The existing `/bitacora` route retains its sign-in gate.

Since 2026-09-11 the production notebook (`/bitacora/`) is also **hidden from
the dashboard's tab row** while it is under test: the Nav's Notebooks tab and
the platform placeholder's "Open the notebook" button are gone, and the one
dashboard entry point is a link on the Admin page (tester-only until
2026-10-09, now shown to any admin). The notebook service tile is also omitted from
`platforms.yaml`; the service remains registered for monitoring. The stale `/notebooks` bookmark redirect follows suit (admins reach
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
