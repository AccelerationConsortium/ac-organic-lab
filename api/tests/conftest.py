"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    """Load a fixture JSON file (returns its `_http_status` and `_body`)."""

    def _load(name: str) -> tuple[int, Any]:
        with (FIXTURE_DIR / f"{name}.json").open() as f:
            data = json.load(f)
        return data["_http_status"], data["_body"]

    return _load
