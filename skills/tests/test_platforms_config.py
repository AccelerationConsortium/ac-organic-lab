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


def _sections():
    return [
        {"id": "a", "title": "A", "kind": "platform", "href": "/platforms/a", "equipment": ["x"]},
    ]


def test_catalog_groups_extend_a_section_or_stand_alone():
    cfg = PlatformsConfig.model_validate(
        {
            "sections": _sections(),
            "catalog_groups": [
                {"id": "a", "equipment": ["y"]},
                {"id": "pcs", "title": "Computers", "equipment": ["z"]},
            ],
        }
    )
    assert cfg.equipment_to_catalog_group_id() == {"x": "a", "y": "a", "z": "pcs"}
    # The Overview's mapping is untouched: catalog groups are catalog-only.
    assert cfg.equipment_to_section_id() == {"x": "a"}


def test_catalog_groups_are_optional():
    cfg = PlatformsConfig.model_validate({"sections": _sections()})
    assert cfg.catalog_groups == []
    assert cfg.equipment_to_catalog_group_id() == {"x": "a"}


@pytest.mark.parametrize(
    "groups,match",
    [
        ([{"id": "pcs", "equipment": ["z"]}], "needs a title"),
        ([{"id": "a", "title": "Other", "equipment": ["z"]}], "drop its title"),
        ([{"id": "pcs", "title": "PCs", "equipment": ["x"]}], "'x' is already placed"),
        (
            [
                {"id": "pcs", "title": "PCs", "equipment": ["z"]},
                {"id": "printers", "title": "Printers", "equipment": ["z"]},
            ],
            "'z' is already placed",
        ),
        (
            [
                {"id": "pcs", "title": "PCs", "equipment": ["y"]},
                {"id": "pcs", "title": "PCs", "equipment": ["z"]},
            ],
            "listed twice",
        ),
    ],
)
def test_ambiguous_catalog_groups_are_rejected(groups, match):
    with pytest.raises(ValidationError, match=match):
        PlatformsConfig.model_validate({"sections": _sections(), "catalog_groups": groups})
