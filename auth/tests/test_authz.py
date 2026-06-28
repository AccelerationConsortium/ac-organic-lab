"""Tests for the central account → device-role resolver seam (authz.py)."""

from __future__ import annotations

from ac_auth.authz import effective_central_role, effective_device_role
from ac_auth.db import User
from ac_auth.roster import Grant


def _user(role="user", is_automation=False, grants=()) -> User:
    return User("x@lab.local", role, "active", is_automation, grants=list(grants))


def test_human_user_maps_to_user():
    assert effective_device_role(_user(role="user"), "agilent_uplc_ms") == "user"


def test_human_admin_maps_to_service():
    assert effective_device_role(_user(role="admin"), "agilent_uplc_ms") == "service"


def test_automation_account_maps_to_automation():
    # Machine principal → workflow role, regardless of its human role column.
    assert effective_device_role(_user(role="user", is_automation=True), "x") == "automation"
    assert effective_device_role(_user(role="admin", is_automation=True), "x") == "automation"


def test_no_grants_equipment_key_does_not_change_result():
    u = _user(role="admin")
    assert effective_device_role(u, "agilent_uplc_ms") == effective_device_role(u, "some_other_device")


# ---- Phase 1: per-scope grants (elevation) -------------------------------


def test_global_admin_grant_elevates_everywhere():
    u = _user(role="user", grants=[Grant(scope="global", role="admin")])
    assert effective_device_role(u, "ot2") == "service"
    assert effective_device_role(u, "anything_else") == "service"


def test_platform_admin_grant_resolves_via_membership():
    u = _user(role="user", grants=[Grant(scope="platform", id="hte", role="admin")])
    membership = {"ot2": {"hte"}, "cytation_5": {"hte"}, "pypoe_web": {"web_services"}}
    # admin on devices in the hte platform...
    assert effective_device_role(u, "ot2", membership) == "service"
    # ...but only operator (flat) on a device outside it
    assert effective_device_role(u, "pypoe_web", membership) == "user"
    # and with no membership map, the platform grant simply doesn't resolve
    assert effective_device_role(u, "ot2") == "user"


def test_equipment_operator_grant_does_not_demote_flat_admin():
    # grants only elevate; a flat admin stays service even with a narrower grant
    u = _user(role="admin", grants=[Grant(scope="equipment", id="ot2", role="operator")])
    assert effective_device_role(u, "ot2") == "service"
    assert effective_device_role(u, "other") == "service"


def test_effective_central_role():
    assert effective_central_role(_user(role="user"), "x") == "operator"
    assert effective_central_role(_user(role="admin"), "x") == "admin"
    u = _user(role="user", grants=[Grant(scope="platform", id="hte", role="admin")])
    assert effective_central_role(u, "ot2", {"ot2": {"hte"}}) == "admin"
    assert effective_central_role(u, "ot2") == "operator"  # membership absent


# ---- Phase 1b: role:none restriction (no access except where granted) -----


def test_role_none_has_no_access_without_grants():
    u = _user(role="none")
    assert effective_central_role(u, "ot2") is None
    assert effective_device_role(u, "ot2") is None


def test_role_none_with_equipment_grant_only_reaches_that_device():
    u = _user(role="none", grants=[Grant(scope="equipment", id="ot2", role="operator")])
    assert effective_device_role(u, "ot2") == "user"
    assert effective_device_role(u, "cytation_5") is None  # not granted → no access


def test_role_none_with_platform_grant_scoped_by_membership():
    u = _user(role="none", grants=[Grant(scope="platform", id="hte", role="operator")])
    membership = {"ot2": {"hte"}}
    assert effective_device_role(u, "ot2", membership) == "user"
    assert effective_device_role(u, "pypoe_web", membership) is None
    assert effective_device_role(u, "ot2") is None  # membership absent → grant doesn't resolve


def test_role_none_with_global_admin_grant_reaches_everything():
    u = _user(role="none", grants=[Grant(scope="global", role="admin")])
    assert effective_device_role(u, "ot2") == "service"
    assert effective_device_role(u, "anything") == "service"
