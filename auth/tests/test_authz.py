"""Tests for the central account → device-role resolver seam (authz.py)."""

from __future__ import annotations

from ac_auth.authz import effective_device_role
from ac_auth.db import User


def _user(role="user", is_service_account=False) -> User:
    return User("x@lab.local", role, "active", is_service_account)


def test_human_user_maps_to_hplcms_user():
    assert effective_device_role(_user(role="user"), "agilent_uplc_ms") == "hplcms_user"


def test_human_admin_maps_to_hplcms_admin():
    assert effective_device_role(_user(role="admin"), "agilent_uplc_ms") == "hplcms_admin"


def test_service_account_maps_to_hte():
    # Machine principal → workflow role, regardless of its human role column.
    assert effective_device_role(_user(role="user", is_service_account=True), "x") == "hte"
    assert effective_device_role(_user(role="admin", is_service_account=True), "x") == "hte"


def test_flat_today_equipment_key_does_not_change_result():
    u = _user(role="admin")
    assert effective_device_role(u, "agilent_uplc_ms") == effective_device_role(u, "some_other_device")
