"""Anonymous calendar transport must match the real role reader's envelope."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from app.agent_cli import build_role_server
from app.dws_client import DwsCalendarEvent
from app.store import AutoReplyStore

BASE = Path(__file__).resolve().parents[1] / 'evals/trajectory_optimization'


def test_calendar_fixture_matches_actual_native_reader(tmp_path):
    spec = importlib.util.spec_from_file_location('fixture', BASE / 'native_contract_compaction.business_fixture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((BASE / 'native_contract_compaction.business_cases.v7.json').read_text())
    case = next(c for c in manifest['cases'] if c['id'] == 'calendar-cross-midnight')
    fact = case['phases']['cold']['calendar_events'][0]
    model = DwsCalendarEvent(
        event_id=fact['event_id'], title=fact['title'], start_time=fact['start_time'],
        end_time=fact['end_time'], description=fact['description'],
        organizer=fact['organizer']['user_id'], self_response_status=fact['principal_response_status'],
    )
    actual = build_role_server('consumer')._tool_manager.get_tool('read_dingtalk_calendar_event').fn
    with patch('app.dws_client.DwsClient') as client:
        client.return_value.get_calendar_event.return_value = model
        expected = actual(event_id=fact['event_id'])
    store = AutoReplyStore(tmp_path / 'fixture.sqlite3')
    trigger = case['trigger']
    store.enqueue_reply_task(
        conversation_id=trigger['conversation_id'], conversation_title=trigger['conversation_title'],
        single_chat=False, trigger_message_id=trigger['message_id'],
        trigger_create_time=trigger['create_time'], trigger_sender=trigger['sender'],
        trigger_text=trigger['text'], execution_generation='probe', trigger_message_json='{}',
    )
    assert store.claim_reply_tasks(limit=1)[0].id == 1
    server = module.build_fixture(tmp_path, case, 'cold')
    result = server._tool_manager.get_tool('read_dingtalk_calendar_event').fn(event_id=fact['event_id'])
    assert result == expected
    assert result['event']['self_response_status'] == 'needsAction'
    listed = server._tool_manager.get_tool('list_dingtalk_calendar_events').fn(
        start='2026-10-16T20:00:00-07:00', end='2026-10-17T01:00:00-07:00')
    assert listed['events'][0] == expected['event']
