"""Tests for the central account → device-role resolver seam (authz.py)."""

from __future__ import annotations

from ac_auth.authz import (
    data_scope,
    effective_central_role,
    effective_device_role,
)
from ac_auth.db import User
from ac_auth.roster import Grant


def _user(role="user", is_automation=False, grants=()) -> User:
    return User("x@lab.local", role, "active", is_automation, grants=list(grants))


# --- data-access scope (Phase 3) ------------------------------------------


def test_data_scope_passes_through_projects():
    s = data_scope(_user(role="operator"), member_projects=["p1", "p2"], pi_projects=["p3"])
    assert s.member_projects == frozenset({"p1", "p2"})
    assert s.pi_projects == frozenset({"p3"})
    assert s.is_admin is False


def test_data_scope_is_admin_from_flat_role():
    s = data_scope(_user(role="admin"), member_projects=(), pi_projects=())
    assert s.is_admin is True


def test_data_scope_is_admin_from_global_grant():
    u = _user(role="operator", grants=[Grant(scope="global", role="admin")])
    assert data_scope(u, member_projects=(), pi_projects=()).is_admin is True


def test_data_scope_plain_operator_is_not_admin():
    s = data_scope(_user(role="operator"), member_projects=(), pi_projects=())
    assert s.is_admin is False


def test_human_user_maps_to_user():
    assert effective_device_role(_user(role="user"), "agilent_uplc_ms") == "user"


def test_human_admin_maps_to_service():
    assert effective_device_role(_user(role="admin"), "agilent_uplc_ms") == "service"


def test_automation_account_maps_to_automation():
    # Machine principal → workflow role, regardless of its human role column.
    assert effective_device_role(_user(role="user", is_automation=True), "x") == "automation"
    assert effective_device_role(_user(role="admin", is_automation=True), "x") == "automation"


# --- automation scope -----------------------------------------------------
# An automation account reaches only the equipment its roster entry declares
# (main._automation_grants turns `platform:` / `grants:` into these). Undeclared
# stays lab-wide so an entry predating the field is never silently revoked.

_HTE = {"cam_hte_tapo_c245": {"hte"}, "plateloc": {"hte"}, "analytica_db": set()}


def _robot(grants=()) -> User:
    return _user(role="user", is_automation=True, grants=grants)


def test_undeclared_automation_stays_lab_wide():
    robot = _robot()
    assert effective_device_role(robot, "plateloc", _HTE) == "automation"
    assert effective_device_role(robot, "analytica_db", _HTE) == "automation"


def test_platform_scoped_automation_is_bounded_to_that_platform():
    robot = _robot([Grant(scope="platform", id="hte", role="operator")])
    assert effective_device_role(robot, "plateloc", _HTE) == "automation"
    # Off-platform equipment is now out of reach, not silently allowed.
    assert effective_device_role(robot, "analytica_db", _HTE) is None


def test_equipment_scoped_automation_reaches_only_that_equipment():
    """The xarm-camera case: camera-follow must not hold control of the sealer."""
    cam = _robot([Grant(scope="equipment", id="cam_hte_tapo_c245", role="operator")])
    assert effective_device_role(cam, "cam_hte_tapo_c245", _HTE) == "automation"
    assert effective_device_role(cam, "plateloc", _HTE) is None


def test_platform_scope_needs_membership_to_resolve():
    """Without the membership map a platform grant matches nothing — it must not
    fall open to lab-wide."""
    robot = _robot([Grant(scope="platform", id="hte", role="operator")])
    assert effective_device_role(robot, "plateloc") is None


def test_global_scoped_automation_is_lab_wide():
    robot = _robot([Grant(scope="global", role="operator")])
    assert effective_device_role(robot, "anything", _HTE) == "automation"


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
