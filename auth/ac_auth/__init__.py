"""Auth sidecar for the AC Organic Self-driving Lab.

A tiny FastAPI service that resolves *who* is making a control request by
asking the local ``tailscaled`` daemon (Tailscale identity), so the dashboard
control plane can attribute and (later) gate writes per user.

Phase 1 (audit mode): ``/auth/verify`` resolves and **logs** the identity but
never blocks — see :mod:`ac_auth.main`. Enforcement + edge wiring land in
later phases (see ``docs/AUTH.md``).
"""

__version__ = "0.1.0"
