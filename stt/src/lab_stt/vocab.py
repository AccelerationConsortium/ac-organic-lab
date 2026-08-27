"""Vocabulary biasing for the lab.

Qwen3-ASR was trained with context biasing: a free-text prompt nudges its
token probabilities toward the terms it names. The terms that matter here are
the lab's device names — "PlateLoc", "Cytation", "xArm" — which are exactly
what a generic ASR model mangles and exactly what the assistant must resolve.

The vocabulary is generated from equipment.yaml rather than maintained by
hand, so onboarding a device extends the recognizer the same way it already
extends the dashboard. Parsed with plain PyYAML: this venv deliberately does
not depend on lab-skills (different Python, GPU stack), and the two fields
read here are stable registry schema.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Terms the registry does not carry but operators say constantly.
STANDING_TERMS = [
    "SDL2", "tailnet", "journald", "aggregator", "claim", "e-stop",
    "seal cycle", "plate stage", "well plate", "tip rack", "fume hood sash",
]


def _registry_names(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text())
    names: list[str] = []
    for entry in doc.get("equipment", []):
        name = entry.get("name")
        eid = entry.get("id")
        if name:
            names.append(str(name))
        # ids are spoken too ("ot2_hte"); spaces make them pronounceable and
        # give the model the token boundary it will actually hear.
        if eid:
            names.append(str(eid).replace("_", " "))
    return names


def build_context(equipment_yaml: str | None = None, extra_file: str | None = None) -> str:
    """The biasing prompt handed to the model with every request."""
    terms: list[str] = []

    path = Path(equipment_yaml or os.environ.get("STT_EQUIPMENT_YAML", ""))
    if path.is_file():
        try:
            terms += _registry_names(path)
        except Exception:  # a broken registry must not take speech down
            logger.exception("could not parse %s for vocabulary", path)
    else:
        logger.warning("no equipment.yaml at %r — vocabulary is generic", str(path))

    extra = Path(extra_file or os.environ.get("STT_VOCAB_FILE", ""))
    if extra.is_file():
        terms += [ln.strip() for ln in extra.read_text().splitlines() if ln.strip()]

    terms += STANDING_TERMS
    seen: dict[str, None] = {}
    for t in terms:
        seen.setdefault(t, None)
    return "Lab operations vocabulary: " + "; ".join(seen) + "."
