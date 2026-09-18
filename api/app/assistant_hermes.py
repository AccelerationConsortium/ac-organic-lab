"""Isolated Hermes chat backend with only the dashboard's lab MCP tools."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile

from .assistant import (
    CONTROL_PROMPT_ADDENDUM, DEFAULT_TIMEOUT_S, SYSTEM_PROMPT,
    _format_prompt, _runtime_dir, _sse, _translate_event,
)

MODEL = os.environ.get('ASSISTANT_HERMES_MODEL', 'deepseek/deepseek-v4-flash-vision-exp')
BINARY = os.environ.get('ASSISTANT_HERMES_BIN', str(Path.home() / '.hermes/hermes-agent/venv/bin/hermes'))


def configured() -> bool:
    return os.access(BINARY, os.X_OK) and bool(os.environ.get('ASSISTANT_HERMES_API_KEY'))


def turn_config(actor, extra_system_prompt=None, *, control=False):
    from .assistant_openai import _server_specs
    include_control = control and bool(actor)
    servers = _server_specs(include_control, actor)
    # Hermes sanitizes the environment of each MCP child. Bind these explicitly
    # so history reads the same database/registry as the dashboard process.
    for key in ('LAB_DB_PATH', 'LAB_REGISTRY_PATH'):
        if key in os.environ:
            servers['lab-history']['env'][key] = os.environ[key]
    return {
        'model': {'default': MODEL, 'provider': 'openrouter'},
        'agent': {'max_turns': 12, 'system_prompt': SYSTEM_PROMPT
                  + (CONTROL_PROMPT_ADDENDUM if include_control else '')
                  + (extra_system_prompt or '')},
        # Every turn starts fresh MCP processes. The interactive 1.5-second
        # discovery window can snapshot an empty registry on a cold start.
        'mcp_discovery_timeout': 30,
        'mcp_single_query_discovery_timeout': 30,
        # This small, fixed allowlist must be visible directly. Hermes otherwise
        # replaces MCP schemas with tool_search/tool_describe/tool_call, which
        # also hides the originating tool name from proposal result events.
        'tools': {'tool_search': {'enabled': 'off'}},
        'mcp_servers': servers,
        'platforms': {},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False},
    }


def public_event(event):
    kind = event.get('type')
    if kind == 'text':
        return {'type': 'text', 'delta': event.get('text', '')}
    if kind in ('tool_use', 'tool_result'):
        # Tool payloads can contain private data; expose only progress here.
        return {'type': kind, 'name': event.get('name', 'lab tool')}
    return None


def control_events(event):
    """Translate only trusted lab-control results into existing approval cards.

    Hermes wraps MCP text in {result: ...}, as supported by the shared parser.
    A CLI-truncated result must fail closed, never become a partial proposal.
    """
    if event.get('type') != 'tool_result' or event.get('is_error'):
        return []
    name = event.get('name', '')
    if not name.startswith(('mcp__lab_control__', 'mcp__lab-control__')):
        return []
    frames = _translate_event({'type': 'user', 'message': {'content': [
        {'type': 'tool_result', 'content': event.get('output', '')},
    ]}})
    return [f for f in frames if f['type'] in ('proposal', 'plan', 'proposal_refused', 'declined')]


async def run_hermes_turn(messages, *, control=False, actor=None, on_proposal=None,
                          on_plan=None, extra_system_prompt=None):
    if control and not actor:
        yield _sse({'type': 'error', 'message': 'Control requires a verified signed-in user.'})
        return
    if not configured():
        yield _sse({'type': 'error', 'message': 'Hermes assistant is not configured on the dashboard host.'})
        return
    yield _sse({'type': 'status', 'phase': 'thinking', 'label': 'Hermes is thinking…'})
    # Every user/turn gets separate state; no personal profiles, hooks, memory,
    # session history or tools are inherited from the full-access admin agent.
    with tempfile.TemporaryDirectory(prefix='hermes-turn-', dir=_runtime_dir()) as home:
        root = Path(home)
        config = turn_config(actor, extra_system_prompt, control=control)
        (root / 'config.yaml').write_text(json.dumps(config))
        # JSON is valid YAML. Secrets are passed only in the child environment.
        (root / 'query.txt').write_text(_format_prompt(messages))
        keep = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'LAB_DB_PATH',
                'LAB_REGISTRY_PATH', 'BITACORA_URL')
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env.update(HOME=home, HERMES_HOME=home, OPENROUTER_API_KEY=os.environ['ASSISTANT_HERMES_API_KEY'])
        args = [BINARY, 'chat', '--query-file', str(root / 'query.txt'), '--format', 'stream-json',
                '--model', MODEL, '--provider', 'openrouter', '--toolsets',
                ','.join(config['mcp_servers']), '--ignore-rules', '--max-turns', '12']
        proc = None
        try:
            async with asyncio.timeout(DEFAULT_TIMEOUT_S):
                proc = await asyncio.create_subprocess_exec(*args, cwd=home, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True, limit=1024 * 1024)
                text_seen = False
                control_outcome_seen = False
                pending = None
                try:
                    while True:
                        pending = asyncio.create_task(proc.stdout.readline())
                        while not pending.done():
                            ready, _ = await asyncio.wait({pending}, timeout=5)
                            if not ready:
                                yield b": keep-alive\n\n"
                        line = pending.result()
                        pending = None
                        if not line:
                            yield _sse({'type': 'error', 'message': 'Hermes exited without completing the response.'})
                            return
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get('type') == 'result':
                            if event.get('exit_code') or event.get('error'):
                                yield _sse({'type': 'error', 'message': 'Hermes could not complete this turn. Check the assistant configuration and provider availability.'})
                            else:
                                if not text_seen and event.get('text'):
                                    yield _sse({'type': 'text', 'delta': event['text']})
                                if control and not control_outcome_seen:
                                    yield _sse({'type': 'error', 'message': 'Hermes did not return a complete control proposal or decline. No action was authorized.'})
                                    return
                                yield _sse({'type': 'done'})
                            return
                        frame = public_event(event)
                        if frame:
                            text_seen |= frame['type'] == 'text'
                            yield _sse(frame)
                        if control:
                            for outcome in control_events(event):
                                callback = {'proposal': on_proposal, 'plan': on_plan}.get(outcome['type'])
                                if callback is not None:
                                    await callback(outcome[outcome['type']])
                                control_outcome_seen = True
                                yield _sse(outcome)
                finally:
                    if pending is not None:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
        except TimeoutError:
            yield _sse({'type': 'error', 'message': 'Hermes response timed out.'})
        finally:
            if proc is not None:
                # Also reap MCP descendants when the browser cancels a turn.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
