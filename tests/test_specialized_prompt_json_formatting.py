import json

from app.email_classifier_agent import build_agent_classification_prompt
from app.meeting_alignment_agent import build_meeting_alignment_prompt
from app.meeting_alignment_models import MeetingSource, MeetingAlignmentDecision
from app.task_agent import build_task_agent_prompt
from app.task_models import WorkItem


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def test_meeting_full_source_and_schema_are_compact_lossless():
    source = MeetingSource.model_validate({
        'meeting_id': 'synthetic:meeting', 'title': '原文标题', 'status': 'ended',
        'started_at': '2026-10-14T10:00:00+08:00', 'ended_at': '2026-10-14T11:00:00+08:00',
        'participants': [], 'attendee_evidence': 'transcript', 'attendee_roster_complete': False,
        'current_user_id': 'anonymous', 'summary': '  原始摘要\n第二行  ',
        'transcript': [{'speaker_name': '匿名', 'text': '原文 "引用"\\路径\t末尾空白  '}],
    })
    prompt = build_meeting_alignment_prompt(source, work_profile='', work_profile_source='synthetic')
    text = prompt.split('完整会议来源 JSON：\n', 1)[1].strip()
    assert json.loads(text) == source.model_dump(mode='json')
    assert text == compact(source.model_dump(mode='json'))
    section = prompt.split('MeetingAlignmentDecision Pydantic JSON schema:\n', 1)[1]
    schema, end = json.JSONDecoder().raw_decode(section)
    original = MeetingAlignmentDecision.model_json_schema()
    original.pop('description', None)
    assert schema == original
    assert section[:end] == compact(original)


def test_email_classification_context_is_compact_without_changing_sort_or_values():
    context = {'message': {'text': '  正文\n引文\t"内容"  ', 'message_id': '001'},
               'provider_unread': True, 'allowed_category_keys': ['work', 'junk']}
    prompt = build_agent_classification_prompt(context, skill_text='synthetic skill')
    text = prompt.split('Exact invocation context:\n', 1)[1]
    assert json.loads(text) == context
    assert text == json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def test_task_workitem_metadata_is_compact_and_context_unchanged(monkeypatch, tmp_path):
    source = tmp_path / 'task' / 'SKILL.md'
    source.parent.mkdir()
    source.write_text('synthetic task skill')
    monkeypatch.setattr('app.task_agent.WORK_TRACKING_SKILL_PATH', source)
    work = WorkItem.model_validate({'summary': '原始正文\n不能丢失',
                                   'source': {'type': 'local_file', 'ref': 'synthetic:001'},
                                   'context': {'source_conversation_kind': 'file'}})
    facts = 'EXACT_CONTEXT\n原始正文\n不能丢失'
    prompt = build_task_agent_prompt(work, facts, current_time='2026-10-14T09:00:00Z')
    expected = work.model_dump(mode='json')
    expected.pop('summary')
    assert compact(expected) in prompt
    assert prompt.count(facts) == 1
    assert '2026-10-14T09:00:00Z' in prompt
