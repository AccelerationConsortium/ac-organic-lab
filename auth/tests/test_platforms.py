"""Tests for the platform↔equipment membership loader (platforms.py)."""

from __future__ import annotations

from ac_auth.platforms import load_membership

_YAML = """
sections:
  - id: lab_environment
    kind: environmental_map
    equipment: [env_a, env_b]
  - id: hte
    kind: platform
    equipment: [ot2, cytation_5, xarm_translocation]
  - id: web_services
    kind: platform
    equipment: [pypoe_web, ot2]      # shared device → belongs to both platforms
"""


def _write(tmp_path, text):
    p = tmp_path / "platforms.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_membership_maps_platform_sections_only(tmp_path):
    m = load_membership(_write(tmp_path, _YAML))
    assert m["cytation_5"] == {"hte"}
    assert m["pypoe_web"] == {"web_services"}
    # shared equipment belongs to every containing platform
    assert m["ot2"] == {"hte", "web_services"}
    # environmental_map is not a control platform → its equipment isn't a member
    assert "env_a" not in m


def test_missing_file_is_fail_soft(tmp_path):
    assert load_membership(tmp_path / "nope.yaml") == {}


def test_malformed_is_fail_soft(tmp_path):
    assert load_membership(_write(tmp_path, "not: a: valid: mapping: [")) == {}
