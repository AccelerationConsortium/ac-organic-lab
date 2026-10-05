"""The chat engine moved to the ``assistant/`` workspace package (lab-assistant).

What the move must not break, beyond the existing assistant tests that still
import the old ``app.*`` names: those names must *be* the moved modules (so
monkeypatches and ``mock.patch`` targets keep hitting the code that runs),
the engine must not import the dashboard, and the MCP launch path the engine
derives from its own location must still find ``api/``.
"""

from __future__ import annotations

import subprocess
import sys


def test_old_names_are_the_moved_modules():
    import lab_assistant.engine
    import lab_assistant.hermes_backend
    import lab_assistant.openai_backend
    import lab_assistant.sessions
    from app import assistant, assistant_hermes, assistant_openai, assistant_sessions

    assert assistant is lab_assistant.engine
    assert assistant_openai is lab_assistant.openai_backend
    assert assistant_hermes is lab_assistant.hermes_backend
    assert assistant_sessions is lab_assistant.sessions


def test_the_plan_vocabulary_has_one_definition():
    from lab_assistant import plan_contract

    from app import assistant, assistant_control

    for name in ("MAX_PLAN_STEPS", "PLAN_TTL_S", "REFUSAL_CODES", "plan_step_hash"):
        assert getattr(assistant_control, name) is getattr(plan_contract, name)
        assert getattr(assistant, name) is getattr(plan_contract, name)


def test_the_engine_never_imports_the_dashboard():
    code = (
        "import sys\n"
        "import lab_assistant.engine, lab_assistant.openai_backend, "
        "lab_assistant.hermes_backend, lab_assistant.sessions, lab_assistant.plan_contract\n"
        "leaked = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.'))\n"
        "assert not leaked, leaked\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_mcp_servers_still_launch_from_the_api_project():
    from app import assistant

    root = assistant._repo_root()
    assert (root / "api" / "pyproject.toml").is_file()
    assert (root / "assistant" / "pyproject.toml").is_file()


def test_sessions_store_still_sits_beside_lab_db(tmp_path, monkeypatch):
    from app import assistant_sessions

    monkeypatch.delenv("ASSISTANT_DB_PATH", raising=False)
    monkeypatch.setenv("LAB_DB_PATH", str(tmp_path / "lab.db"))
    assert assistant_sessions.resolve_sessions_db_path() == tmp_path / "assistant.db"
