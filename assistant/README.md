# lab-assistant

The lab's chat-assistant engine, extracted from `api/app/` (step 1 of
[`docs/ASSISTANT_CONSOLIDATION_PLAN.md`](../docs/ASSISTANT_CONSOLIDATION_PLAN.md)):

| module | was |
|---|---|
| `lab_assistant.engine` | `app.assistant` — backends dispatch, claude-cli loop, SSE, Ask/Control routes |
| `lab_assistant.openai_backend` | `app.assistant_openai` |
| `lab_assistant.hermes_backend` | `app.assistant_hermes` |
| `lab_assistant.sessions` | `app.assistant_sessions` — saved Plan-mode sessions |
| `lab_assistant.plan_contract` | plan constants + `plan_step_hash`, from `app.assistant_control` |

No behaviour change. The old `app.*` names remain as module aliases for one
release. The package never imports the dashboard: the dashboard registers
what the engine needs (the `lab.db` location) and mounts its routers. Tools
stay in their MCP servers (`lab-history-mcp`, `lab-inventory-mcp`,
`lab-control-mcp`), which remain in `api/`.

Tests for these modules still live in `api/tests/` and run with the
workspace suite (`uv run pytest api/tests`).
