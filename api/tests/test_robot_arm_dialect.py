"""Robot-arm device-action dialects: xArm root vs MG400 /control/*.

Kept out of test_control.py so this file does not need respx — the resolver
is pure and the tile's INIT/STOP/CLEAR 404s if it is wrong.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.control import (
    _resolve_robot_arm_device_action,
    _robot_arm_control_dialect,
)


def _entry(**overrides: Any) -> Any:
    base = {
        "id": "xarm_translocation",
        "kind": "robot_arm",
        "extras": {},
    }
    base.update(overrides)
    obj = MagicMock()
    for key, value in base.items():
        setattr(obj, key, value)
    return obj


def test_dialect_prefers_extras_declaration() -> None:
    """``extras.control_dialect`` wins even when allowed_actions is empty
    (mid-motion, when the MG400 withholds startup/disable)."""
    entry = _entry(id="dobot_mg400", extras={"control_dialect": "control"})
    assert _robot_arm_control_dialect(entry, set()) == "control"
    assert _resolve_robot_arm_device_action(entry, "connect", set()) == "control/startup"
    assert (
        _resolve_robot_arm_device_action(entry, "disconnect", set()) == "control/disable"
    )
    assert _resolve_robot_arm_device_action(entry, "move/stop", set()) == "control/stop"
    assert (
        _resolve_robot_arm_device_action(entry, "clear/errors", set())
        == "control/clear_error"
    )


def test_dialect_defaults_to_xarm_root() -> None:
    """An untagged robot_arm keeps today's xArm paths so the tile does not
    suddenly 404 against the deployed xArm."""
    entry = _entry(extras={})
    assert _resolve_robot_arm_device_action(entry, "connect", set()) == "connect"
    assert _resolve_robot_arm_device_action(entry, "move/stop", set()) == "move/stop"
    assert (
        _resolve_robot_arm_device_action(entry, "clear/errors", set()) == "clear/errors"
    )


def test_dialect_sniffs_mg400_markers() -> None:
    """Fallback when extras is missing: the verbs only the MG400 advertises."""
    entry = _entry(id="dobot_mg400", extras={})
    assert _robot_arm_control_dialect(entry, {"startup", "stop"}) == "control"
    assert _robot_arm_control_dialect(entry, {"stop"}) == "root"
