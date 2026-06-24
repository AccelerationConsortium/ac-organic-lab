"""Auth sidecar for the AC Organic Self-driving Lab.

A small FastAPI service that authenticates dashboard users by **passwordless
email one-time code** (codes sent via Gmail), issues an opaque session cookie,
and exposes ``GET /auth/verify`` for Caddy ``forward_auth`` to gate + attribute
control writes per user. See :mod:`ac_auth.main` and
``docs/AUTH_SERVICE_DESIGN.md``.

Setup: ``python -m ac_auth.setup_gmail`` (store the Gmail App Password locally),
then ``python -m ac_auth.cli add-user EMAIL --role admin`` (allow-list the first
admin). ``identity.py`` (Tailscale whois) is retained for the device-plane but
is no longer the human-auth path.

Authorization (``authz.py``) resolves each account to a device role and projects
it via ``GET /equipment/{key}/roster``; machine principals (service accounts)
authenticate by API key (``cli.py`` ``add-service-account`` / ``issue-key``).
"""

__version__ = "0.3.0"
