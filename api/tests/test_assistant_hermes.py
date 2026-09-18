import json
from pathlib import Path

import pytest

from app import assistant_hermes as hermes
from app.assistant import ChatMessage


def test_control_config_is_actor_bound_and_waits_for_cold_mcp(monkeypatch):
    monkeypatch.setenv('LAB_DB_PATH', '/test/lab.db')
    monkeypatch.setenv('LAB_REGISTRY_PATH', '/test/equipment.yaml')
    config = hermes.turn_config('test@example.org', control=True)
    assert set(config['mcp_servers']) == {'lab-history', 'lab-inventory', 'lab-control'}
    assert config['mcp_servers']['lab-control']['env']['LAB_ACTOR'] == 'test@example.org'
    assert config['mcp_servers']['lab-history']['env']['LAB_DB_PATH'] == '/test/lab.db'
    assert config['mcp_discovery_timeout'] == 30
    assert config['tools']['tool_search']['enabled'] == 'off'
    assert 'propose_action' in config['agent']['system_prompt']
    assert 'lab-control' not in hermes.turn_config(None, control=True)['mcp_servers']
    assert 'lab-control' not in hermes.turn_config('test@example.org')['mcp_servers']


@pytest.mark.parametrize('kind', ['proposal', 'plan', 'declined'])
def test_control_results_translate_only_from_control_server(kind):
    payload = {kind: {'id': 'synthetic'}}
    event = {'type': 'tool_result', 'name': 'mcp__lab_control__propose_action',
             'output': json.dumps({'result': json.dumps(payload)})}
    assert hermes.control_events(event) == [{'type': kind, **payload}]
    assert hermes.control_events({**event, 'name': 'mcp__lab_inventory__search_inventory'}) == []
    assert hermes.control_events({**event, 'is_error': True}) == []
    assert hermes.control_events({**event, 'output': event['output'][:20] + '...'}) == []


@pytest.mark.asyncio
async def test_control_turn_registers_proposal_before_emitting_card(tmp_path, monkeypatch):
    binary = tmp_path / 'hermes'
    binary.write_text('''#!/usr/bin/python3
import os,json,pathlib,sys
c=json.loads((pathlib.Path(os.environ['HERMES_HOME'])/'config.yaml').read_text())
assert c['mcp_servers']['lab-control']['env']['LAB_ACTOR']=='test@example.org'
assert sys.argv[sys.argv.index('--toolsets')+1]=='lab-history,lab-inventory,lab-control'
print(json.dumps({'type':'tool_result','name':'mcp__lab_control__propose_action',
 'output':json.dumps({'result':json.dumps({'proposal':{'id':'synthetic'}})})}))
print(json.dumps({'type':'result','exit_code':0,'text':'Review the proposal.'}))
''')
    binary.chmod(0o700)
    monkeypatch.setattr(hermes, 'BINARY', str(binary))
    monkeypatch.setattr(hermes, '_runtime_dir', lambda: tmp_path)
    monkeypatch.setenv('ASSISTANT_HERMES_API_KEY', 'test-only')
    registered = []
    async def record(proposal):
        registered.append(proposal)
    frames = []
    async for frame in hermes.run_hermes_turn(
        [ChatMessage(role='user', content='synthetic test')], control=True,
        actor='test@example.org', on_proposal=record,
    ):
        if b'"type": "proposal"' in frame:
            assert registered == [{'id': 'synthetic'}]
        frames.append(frame)
    assert b'"done"' in b''.join(frames)


@pytest.mark.asyncio
async def test_hermes_isolates_turn_and_streams_without_tool_payloads(tmp_path, monkeypatch):
    binary = tmp_path / 'hermes'
    binary.write_text('''#!/usr/bin/python3
import os, json, pathlib, sys
root = pathlib.Path(os.environ['HERMES_HOME'])
c = json.loads((root/'config.yaml').read_text())
assert os.environ['HOME'] == str(root)
assert 'UNRELATED_SECRET' not in os.environ
assert set(c['mcp_servers']) == {'lab-history', 'lab-inventory'}
assert c['mcp_servers']['lab-history']['env']['LAB_ACTOR'] == 'test@example.org'
assert c['platforms'] == {}
assert sys.argv[sys.argv.index('--toolsets')+1] == 'lab-history,lab-inventory'
assert 'hello' in pathlib.Path(sys.argv[sys.argv.index('--query-file')+1]).read_text()
print(json.dumps({'type':'tool_result','name':'status','output':'PRIVATE'}))
print(json.dumps({'type':'text','text':'response'}))
print(json.dumps({'type':'result','exit_code':0,'text':'response'}))
''')
    binary.chmod(0o700)
    monkeypatch.setattr(hermes, 'BINARY', str(binary))
    monkeypatch.setattr(hermes, '_runtime_dir', lambda: tmp_path)
    monkeypatch.setenv('ASSISTANT_HERMES_API_KEY', 'test-only')
    monkeypatch.setenv('UNRELATED_SECRET', 'not-for-child')
    frames = [f async for f in hermes.run_hermes_turn([ChatMessage(role='user', content='hello')], actor='test@example.org')]
    result = b''.join(frames).decode()
    assert 'response' in result and '"done"' in result
    assert 'PRIVATE' not in result
    assert not list(tmp_path.glob('hermes-turn-*'))


@pytest.mark.asyncio
async def test_control_and_failed_child_do_not_report_success(tmp_path, monkeypatch):
    frames = [f async for f in hermes.run_hermes_turn([], control=True)]
    assert b'verified signed-in user' in b''.join(frames)
    binary = tmp_path / 'hermes'
    binary.write_text('#!/usr/bin/python3\nprint(\'{"type":"result","exit_code":1,"error":"PRIVATE"}\')\n')
    binary.chmod(0o700)
    monkeypatch.setattr(hermes, 'BINARY', str(binary))
    monkeypatch.setattr(hermes, '_runtime_dir', lambda: tmp_path)
    monkeypatch.setenv('ASSISTANT_HERMES_API_KEY', 'test-only')
    result = b''.join([f async for f in hermes.run_hermes_turn([ChatMessage(role='user', content='hello')])])
    assert b'"error"' in result and b'"done"' not in result and b'PRIVATE' not in result

@pytest.mark.asyncio
async def test_result_finishes_turn_before_child_shutdown(tmp_path, monkeypatch):
    binary = tmp_path / 'hermes'
    binary.write_text('''#!/usr/bin/python3
import json,time
print(json.dumps({'type':'text','text':'finished'}),flush=True)
print(json.dumps({'type':'result','exit_code':0,'text':'finished'}),flush=True)
time.sleep(5)
''')
    binary.chmod(0o700)
    monkeypatch.setattr(hermes, 'BINARY', str(binary))
    monkeypatch.setattr(hermes, '_runtime_dir', lambda: tmp_path)
    monkeypatch.setattr(hermes, 'DEFAULT_TIMEOUT_S', 0.5)
    monkeypatch.setenv('ASSISTANT_HERMES_API_KEY', 'test-only')
    result=b''.join([f async for f in hermes.run_hermes_turn([ChatMessage(role='user',content='hello')])])
    assert b'"done"' in result and b'"error"' not in result


@pytest.mark.asyncio
async def test_control_without_complete_outcome_does_not_report_success(tmp_path, monkeypatch):
    binary = tmp_path / 'hermes'
    binary.write_text('''#!/usr/bin/python3
import json
print(json.dumps({'type':'tool_result','name':'mcp__lab_control__propose_plan',
 'output':'{"result":"truncated...'}))
print(json.dumps({'type':'result','exit_code':0,'text':'I proposed a plan.'}))
''')
    binary.chmod(0o700)
    monkeypatch.setattr(hermes, 'BINARY', str(binary))
    monkeypatch.setattr(hermes, '_runtime_dir', lambda: tmp_path)
    monkeypatch.setenv('ASSISTANT_HERMES_API_KEY', 'test-only')
    result = b''.join([f async for f in hermes.run_hermes_turn(
        [ChatMessage(role='user', content='synthetic test')],
        control=True, actor='test@example.org',
    )])
    assert b'"error"' in result and b'"done"' not in result
    assert b'"type": "plan"' not in result
