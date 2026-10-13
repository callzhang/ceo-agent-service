"""The event reader must preserve organizer identity from the original provider."""
from unittest.mock import patch

import pytest

from app.agent_cli import build_role_server
from app.dws_client import DwsClient


def event_record(organizer):
    return {
        'id': 'event-source-1', 'title': 'Anonymous decision meeting',
        'start': {'dateTime': '2026-10-17T13:30:00+08:00'},
        'end': {'dateTime': '2026-10-17T14:30:00+08:00'},
        'organizer': organizer,
    }


@pytest.mark.parametrize('organizer', [
    {'displayName': 'Anonymous organizer', 'userId': '000123', 'openDingTalkId': 'opaque-open-id', 'email': 'organizer@example.test'},
    {'openDingTalkId': 'open-only', 'providerExtension': {'value': [None, False, '000123']}},
    {'displayName': 'Name only'},
])
def test_calendar_projection_preserves_exact_organizer_object(organizer):
    event = DwsClient._parse_calendar_event(event_record(organizer), require_event_id=True)
    assert event is not None
    assert event.model_dump(mode='json')['organizer_details'] == organizer
    if 'displayName' in organizer:
        assert event.organizer == organizer['displayName']


def test_calendar_organizer_string_stays_display_only():
    event = DwsClient._parse_calendar_event(event_record('Anonymous display name'), require_event_id=True)
    assert event.organizer == 'Anonymous display name'
    assert event.model_dump(mode='json')['organizer_details'] == {}


@pytest.mark.parametrize('tool,arguments', [
    ('read_dingtalk_calendar_event', {'event_id': 'event-source-1'}),
    ('list_dingtalk_calendar_events', {'start': '2026-10-16T00:00:00-07:00', 'end': '2026-10-18T00:00:00-07:00'}),
])
def test_native_calendar_readers_return_original_organizer_identity(tool, arguments):
    organizer = {'displayName': 'Anonymous organizer', 'userId': '000123', 'openDingTalkId': 'opaque-open-id'}
    event = DwsClient._parse_calendar_event(event_record(organizer), require_event_id=True)
    server = build_role_server('consumer')
    with patch('app.dws_client.DwsClient') as client:
        client.return_value.get_calendar_event.return_value = event
        client.return_value.list_calendar_events.return_value = [event]
        result = server._tool_manager.get_tool(tool).fn(**arguments)
    actual = result['event'] if tool == 'read_dingtalk_calendar_event' else result['events'][0]
    assert actual['organizer'] == 'Anonymous organizer'
    assert actual['organizer_details'] == organizer
