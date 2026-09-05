"""Shared guards for the api test suite.

One rule, learned the hard way: **a test must never write the production
history DB.** On 2026-09-04 three pytest runs in the deploy tree filed dozens
of fake audit rows into the live ``data/lab.db`` — ``plan_run`` events for
``ra_test`` with 1 ms durations, and ``plate_moved`` / ``plate_custody_*``
rows for plate ``PLT-1`` under the real ``torry_pines_shaker`` device id —
polluting exactly the audit series the run gate exists to keep trustworthy.

The leak: fixtures like ``test_workflow.run_rig`` run the real ``app.main``
app under ``TestClient``, whose lifespan builds ``LabDatabase`` at
``resolve_db_path()``'s default — repo-root ``data/lab.db``, resolved from the
module's own location. In an isolated worktree that lands on a scratch copy;
in the deploy tree it is production. The fixture below closes the gap for
every current and future test in one place: ``LAB_DB_PATH`` is forced to a
per-test temporary file before any app can start, so no fixture has to
remember to do it. Tests that construct ``LabDatabase(tmp_path / ...)``
directly are unaffected.

``PYPOE_ALERT_URL`` is cleared for the same reason: the lifespan's alert
notifier reads it at startup, and a shell that happens to export it would
otherwise let a test push device alerts into the real Slack pipeline.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_lab_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DB_PATH", str(tmp_path / "lab-test.db"))
    monkeypatch.delenv("PYPOE_ALERT_URL", raising=False)
