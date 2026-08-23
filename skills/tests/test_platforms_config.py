"""platforms.yaml loader: the committed file parses, and the `default`
flag (which platform the Platforms tab opens on) is validated."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from lab_skills.platforms import PlatformsConfig, load_platforms


def test_committed_platforms_yaml_has_at_most_one_default():
    cfg = load_platforms()
    defaults = [s.id for s in cfg.sections if s.default]
    assert len(defaults) <= 1
    # Every section flagged default must be a real bench platform (has a page).
    for s in cfg.sections:
        if s.default:
            assert s.kind == "platform" and s.href


def test_default_flag_is_parsed_and_optional(tmp_path):
    yaml_text = textwrap.dedent(
        """
        sections:
          - id: a
            title: A
            kind: platform
            href: /platforms/a
            equipment: [x]
          - id: b
            title: B
            kind: platform
            href: /platforms/b
            default: true
            equipment: [y]
        """
    )
    path = tmp_path / "platforms.yaml"
    path.write_text(yaml_text)
    cfg = load_platforms(path)
    assert [s.default for s in cfg.sections] == [False, True]


def test_more_than_one_default_is_rejected():
    with pytest.raises(ValidationError, match="only one section may set default"):
        PlatformsConfig.model_validate(
            {
                "sections": [
                    {"id": "a", "title": "A", "kind": "platform", "equipment": [], "default": True},
                    {"id": "b", "title": "B", "kind": "platform", "equipment": [], "default": True},
                ]
            }
        )
