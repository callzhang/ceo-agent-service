import json
import sqlite3

from app import native_trajectory
from app.agent_turn_runner import recover_completed_runtime_domain_result
from app.agent_runtime_router import RoutedResultCodec, recover_completed_routed_result
from app.agent_result import parse_agent_text_result
from app.store import AutoReplyStore, _audit_event_metadata_json
from tests.test_agent_turn_store import _claim_consumer, _task


def test_run_events_exist_only_in_memory_or_native_session(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-1", "command": "private command", "aggregated_output": "private output"}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    assert store.get_agent_run(run.id).tool_events == [event]
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from agent_run_events where agent_run_id=?", (run.id,)).fetchone()[0] == 0

    native_path = tmp_path / "session.jsonl"
    native_path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "call-1", "command": "private command", "aggregated_output": "private output"}}}) + "\n")
    with store._connect() as db:
        db.execute("update agent_runs set codex_session_id='native-session', transcript_end_line=1 where id=?", (run.id,))
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native_path)
    native_trajectory._LIVE_EVENTS.clear()
    assert store.get_agent_run(run.id).tool_events[0]["item"]["command"] == "private command"


def test_completed_run_result_is_read_from_native_without_sqlite_copy(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    wire = {
        "outcome": "no_action", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "summary": "Nothing to do.", "proposal": None,
        "decision_options": [], "error_code": "", "error_retryable": False,
        "error_authorization_required": False, "durable_memories": [],
    }
    native_path = tmp_path / "session.jsonl"
    native_path.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(wire)}]}}) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native_path)
    store.set_agent_run_session(run.id, "native-session", owner="consumer", transcript_start_line=0)
    store.complete_agent_run(run.id, {"outcome": "no_action", "summary": "Nothing to do."}, owner="consumer", transcript_end_line=1)
    with sqlite3.connect(store.path) as db:
        assert db.execute("select final_result_json from agent_runs where id=?", (run.id,)).fetchone()[0] == ""
    restored = store.get_agent_run(run.id)
    assert json.loads(restored.final_result_json) == wire
    native_path.unlink()
    assert store.get_agent_run(run.id).final_result_json == ""


def test_completed_runtime_attempt_keeps_only_native_reference(tmp_path):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    attempt = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5")
    store.complete_agent_runtime_attempt(
        attempt.id, "native-session", "codex_session:native-session", 0, 1,
        result_schema_id="test.v1",
        result_envelope_json=json.dumps({"schema_id": "test.v1", "value": "private result"}),
    )
    with sqlite3.connect(store.path) as db:
        row = db.execute("select result_schema_id, result_envelope_json, session_id, transcript_end from agent_runtime_attempts where id=?", (attempt.id,)).fetchone()
    assert row == ("test.v1", "", "native-session", 1)


def test_native_result_stream_uses_only_referenced_turn(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": value}]
    }}) for value in ("old", "selected", "later")) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: path)
    raw = native_trajectory.read_native_result_stream("codex_cli", "native-session", 1, 2)
    assert "selected" in raw
    assert "old" not in raw
    assert "later" not in raw
    assert native_trajectory.read_native_result_stream("codex_cli", "native-session", 1, 4) == ""


def test_native_multiturn_range_is_unavailable_without_guessing_a_turn(tmp_path, monkeypatch):
    path = tmp_path / 'session.jsonl'
    records = []
    for identity in ('first', 'later'):
        records.extend([
            {'type': 'event_msg', 'payload': {'type': 'task_started'}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'CommandExecution', 'id': identity, 'command': identity,
                'exit_code': 0, 'aggregated_output': identity + ' effect'}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'AgentMessage', 'id': identity + '-answer',
                'content': [{'type': 'output_text', 'text': json.dumps({'answer': identity})}]}}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete'}},
        ])
    path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
    monkeypatch.setattr(native_trajectory, 'find_codex_session_path', lambda *a, **k: path)
    for start, end in ((0, 8), (2, 6), (2, 8)):
        assert native_trajectory.read_native_result_stream('codex_cli', 'session', start, end) == ''
        assert native_trajectory.read_codex_events('session', start_line=start, end_line=end) == []
    for start, end, selected, absent in ((0, 4, 'first', 'later'), (4, 8, 'later', 'first')):
        raw = native_trajectory.read_native_result_stream('codex_cli', 'session', start, end)
        assert selected in raw and absent not in raw
        events = native_trajectory.read_codex_events('session', start_line=start, end_line=end)
        assert events[0]['item']['command'] == selected
        assert absent not in json.dumps(events)
    for malformed in ('{', '[]'):
        lines = [json.dumps(record) for record in records]
        lines[3] = malformed  # Missing first-turn completion hides the crossing.
        path.write_text('\n'.join(lines) + '\n')
        assert native_trajectory.read_native_result_stream('codex_cli', 'session', 2, 8) == ''
        assert native_trajectory.read_codex_events('session', start_line=2, end_line=8) == []
        assert 'later' in native_trajectory.read_native_result_stream('codex_cli', 'session', 4, 8)


def test_completed_runtime_result_recovers_typed_value_from_native(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    wire = {
        "outcome": "no_action", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "summary": "Native answer.", "proposal": None,
        "decision_options": [], "error_code": "", "error_retryable": False,
        "error_authorization_required": False, "durable_memories": [],
    }
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(wire)}]}}) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: path)
    attempt = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5")
    attempt = store.complete_agent_runtime_attempt(attempt.id, "native-session", "codex_session:native-session", 0, 1,
        result_schema_id="schema-v1", result_envelope_json=json.dumps({"schema_id": "schema-v1"}))
    recovered = recover_completed_runtime_domain_result(attempt, run, schema_id="schema-v1")
    assert recovered.summary == "Native answer."


def test_generic_completed_attempt_recovers_native_text(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "native result"}]}}) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: path)
    attempt = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5")
    attempt = store.complete_agent_runtime_attempt(attempt.id, "native-session", "codex_session:native-session", 0, 1,
        result_schema_id="text.v1", result_envelope_json=json.dumps({"schema_id": "text.v1"}))
    codec = RoutedResultCodec.text(schema_id="text.v1")
    assert recover_completed_routed_result(attempt, parse_agent_text_result, codec) == "native result"
    path.unlink()
    assert recover_completed_routed_result(attempt, parse_agent_text_result, codec) is None


def test_audit_tool_events_are_never_persisted_as_compact_copies():
    events = json.dumps([{"type": "item.completed", "item": {
        "type": "command_execution", "id": "call-1", "exit_code": 0,
        "aggregated_output": "private receipt",
    }}])
    assert _audit_event_metadata_json(events) == "[]"


def test_task_memory_queue_uses_adopted_memories_once_when_native_is_missing(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    memory = {
        "title": "Preference", "content": "Derek prefers direct evidence.",
        "source_time": "2026-10-09T10:00:00Z", "source_refs": ["msg-1"],
        "subject": {"type": "Person", "name": "Derek"},
    }
    wire = {
        "outcome": "no_action", "risk": "low", "confidence": 1.0,
        "rule_coverage": 1.0, "information_completeness": 1.0,
        "summary": "Memory identified.", "proposal": None,
        "decision_options": [], "error_code": "", "error_retryable": False,
        "error_authorization_required": False, "durable_memories": [memory],
    }
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(wire)}]}}) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: path)
    store.set_agent_run_session(run.id, "native-session", owner="consumer", transcript_start_line=0)
    store.complete_agent_run(run.id, {"outcome": "no_action", "durable_memories": [memory]}, owner="consumer", transcript_end_line=1)
    path.unlink()
    with store._connect() as db:
        store._enqueue_task_memory_write_in_connection(db, task_id=task.id, execution_generation=task.execution_generation)
        store._enqueue_task_memory_write_in_connection(db, task_id=task.id, execution_generation=task.execution_generation)
        row = db.execute("select status, memories_json from task_memory_write_events where reply_task_id=?", (task.id,)).fetchone()
        assert db.execute('select count(*) from task_memory_write_events').fetchone()[0] == 1
    assert row["status"] == "pending"
    assert json.loads(row["memories_json"])[0]["title"] == "Preference"


def test_consumer_completion_adopts_prepared_candidate_atomically(tmp_path, monkeypatch):
    from app.agent_orchestrator import AgentOrchestrator
    from tests.test_reviewed_candidates import _candidate

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    raw = _candidate()
    prepared = raw.model_copy(deep=True)
    prepared.proposal.actions[0].payload['text'] = 'hello — service feedback marker'
    store.complete_agent_run(run.id, prepared.model_dump(mode='json'), owner='consumer')
    with store._connect() as db:
        candidate = db.execute('select * from review_candidates where consumer_run_id=?', (run.id,)).fetchone()
    assert candidate is not None
    assert json.loads(candidate['candidate_json']) == prepared.model_dump(mode='json')
    # There is no native transcript; adoption remains recoverable but raw output
    # must stay explicitly unavailable.
    assert store.get_agent_run(run.id).final_result_json == ''
    orchestrator = AgentOrchestrator(store=store, consumer=None, audit=None)
    state = orchestrator._derive_state(task)
    assert state.candidate_id == candidate['id']
    assert state.candidate.proposal.actions[0].payload['text'] == prepared.proposal.actions[0].payload['text']


def test_consumer_candidate_insert_failure_rolls_back_completion(tmp_path):
    import pytest
    from tests.test_reviewed_candidates import _candidate

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    store.append_agent_run_event(run.id, {'type': 'runtime.prompt', 'prompt': 'private'}, owner='consumer')
    with store._connect() as db:
        db.execute("create trigger fail_candidate before insert on review_candidates begin select raise(abort, 'candidate_insert_failed'); end")
    with pytest.raises(sqlite3.IntegrityError, match='candidate_insert_failed'):
        store.complete_agent_run(run.id, _candidate().model_dump(mode='json'), owner='consumer')
    assert store.get_agent_run(run.id).status == 'running'
    assert native_trajectory.live_events(str(store.path.resolve()), run.id)
    with store._connect() as db:
        assert db.execute('select count(*) from review_candidates').fetchone()[0] == 0


def test_terminal_run_releases_live_events_after_commit(tmp_path):
    for outcome in ('completed', 'failed', 'expired'):
        store = AutoReplyStore(tmp_path / f'{outcome}.sqlite3')
        task = _task(store)
        run = _claim_consumer(store, task).run
        store.append_agent_run_event(run.id, {'type': 'runtime.prompt', 'prompt': 'private'}, owner='consumer')
        if outcome == 'completed':
            store.complete_agent_run(run.id, {'outcome': 'no_action'}, owner='consumer')
        elif outcome == 'failed':
            store.fail_agent_run(run.id, {'code': 'failed'}, owner='consumer')
        else:
            with store._connect() as db:
                db.execute("update agent_runs set lease_expires_at='2000-01-01' where id=?", (run.id,))
            store.fail_expired_agent_run(run.id, {'code': 'expired'}, expected_execution_generation=task.execution_generation)
        assert native_trajectory.live_events(str(store.path.resolve()), run.id) is None


def test_observed_event_prevents_discard_without_runtime_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    store.append_agent_run_event(run.id, {'type': 'item.started', 'item': {'type': 'command_execution'}}, owner='consumer')
    assert not store.discard_unstarted_agent_run(run.id, owner='consumer')


def test_cache_publication_is_serialized_with_terminal_writer(tmp_path, monkeypatch):
    import pytest

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    remember = native_trajectory.remember_live_event

    def remember_under_write_lock(*args):
        with sqlite3.connect(store.path, timeout=0) as competing_writer:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                competing_writer.execute('begin immediate')
        remember(*args)

    monkeypatch.setattr(native_trajectory, 'remember_live_event', remember_under_write_lock)
    store.append_agent_run_event(run.id, {'type': 'runtime.prompt'}, owner='consumer')
    store.fail_agent_run(run.id, {'code': 'failed'}, owner='consumer')
    with pytest.raises(ValueError, match='terminal'):
        store.append_agent_run_event(run.id, {'type': 'runtime.prompt'}, owner='consumer')
    assert native_trajectory.live_events(str(store.path.resolve()), run.id) is None


def test_running_cache_keeps_observed_event_when_append_commit_fails(tmp_path, monkeypatch):
    import pytest

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    event = {'type': 'runtime.prompt', 'prompt': 'already observed'}
    with store._connect() as db:
        db.execute('create table deferred_commit_failure (run_id integer references agent_runs(id) deferrable initially deferred)')
    terminal_ids = native_trajectory.terminal_cached_run_ids

    def fail_commit(db, path):
        db.execute('insert into deferred_commit_failure values (-1)')
        return terminal_ids(db, path)

    monkeypatch.setattr(native_trajectory, 'terminal_cached_run_ids', fail_commit)
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        store.append_agent_run_event(run.id, event, owner='consumer')
    assert native_trajectory.live_events(str(store.path.resolve()), run.id) == [event]
    monkeypatch.setattr(native_trajectory, 'terminal_cached_run_ids', terminal_ids)
    assert store.get_agent_run(run.id).transcript_end_line == 0
    assert not store.discard_unstarted_agent_run(run.id, owner='consumer')
    store.fail_agent_run(run.id, {'code': 'failed'}, owner='consumer')
    assert native_trajectory.live_events(str(store.path.resolve()), run.id) is None


def test_completed_consumer_without_adopted_candidate_does_not_run_again(tmp_path):
    from app.agent_orchestrator import AgentOrchestrator

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    with store._connect() as db:
        db.execute("update agent_runs set status='completed',lease_owner='',lease_expires_at='' where id=?", (run.id,))
    state = AgentOrchestrator(store=store, consumer=None, audit=None)._derive_state(task)
    assert state.status == 'failed_terminal'
    assert state.error.code == 'completed_consumer_candidate_unavailable'


def _native_final(tmp_path, monkeypatch, store, run_id, payload):
    path = tmp_path / f'native-{run_id}.jsonl'
    path.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': json.dumps(payload)}
        ]}}) + '\n')
    monkeypatch.setattr(native_trajectory, 'find_codex_session_path',
                        lambda session_id, **kwargs: tmp_path / f'{session_id}.jsonl')
    with store._connect() as db:
        db.execute('update agent_runs set codex_session_id=?,transcript_end_line=1 where id=?',
                   (f'native-{run_id}', run_id))
    return path


def test_completed_turn_reuses_adopted_plan_without_recapturing_sources(tmp_path, monkeypatch):
    from app.agent_turn_runner import AgentTurnProcess
    from tests.test_reviewed_candidates import _candidate

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    raw = _candidate()
    native = _native_final(tmp_path, monkeypatch, store, run.id, raw.model_dump(mode='json'))
    prepared = raw.model_copy(deep=True)
    prepared.proposal.actions[0].payload['text'] = 'service-prepared frozen body'
    from app.agent_contracts import ReviewedSourceBinding
    prepared.source_bindings = (ReviewedSourceBinding(provider='task_context', object_ref='frozen-input', value={'source_version': 1}),)
    store.complete_agent_run(run.id, prepared.model_dump(mode='json'), owner='consumer', transcript_end_line=1)
    assert json.loads(store.get_agent_run(run.id).final_result_json)['proposal']['actions'][0]['payload']['text'] == 'hello'

    def unexpected(*args, **kwargs):
        raise AssertionError('completed turn must not run a provider or recapture mutable sources')

    native.unlink()
    recovered = AgentTurnProcess(store=store, task=task, workspace=tmp_path,
                                 owner='consumer', executor=unexpected).execute(
        run=run, prompt='same task', session_id=None, developer_instructions='',
        configure_command=unexpected, parse_result=unexpected, prepare_result=unexpected,
        persist_conversation_session=True,
    )
    assert recovered.result == prepared
    assert store.get_agent_run(run.id).final_result_json == ''


def test_audit_adoption_rolls_back_and_recovers_without_native_output(tmp_path, monkeypatch):
    import pytest
    from app.agent_contracts import AuditAgentResult
    from app.agent_orchestrator import AgentOrchestrator
    from app.store import AgentRole
    from tests.test_reviewed_candidates import _candidate

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    consumer = _claim_consumer(store, task).run
    result = _candidate()
    store.complete_agent_run(consumer.id, result.model_dump(mode='json'), owner='consumer')
    adopted = store.adopted_candidate_for_consumer_run(consumer.id)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
                                 proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
                                 operation_id='review', owner='audit').run
    reviewed = AuditAgentResult.model_validate({
        'outcome': 'approve', 'summary': 'Exact frozen plan approved', 'proposal_revision': 0,
        'candidate_digest': adopted['candidate_digest'], 'feedback': None, 'error': {},
        'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0, 'information_completeness': 1.0,
    })
    native = _native_final(tmp_path, monkeypatch, store, audit.id, reviewed.model_dump(mode='json'))
    with store._connect() as db:
        db.execute("create trigger fail_review before insert on candidate_reviews begin select raise(abort, 'review_insert_failed'); end")
    with pytest.raises(sqlite3.IntegrityError, match='review_insert_failed'):
        store.complete_agent_run(audit.id, reviewed.model_dump(mode='json'), owner='audit', transcript_end_line=1)
    assert store.get_agent_run(audit.id).status == 'running'
    assert store.get_candidate_review_for_audit_run(audit.id) is None
    with store._connect() as db:
        db.execute('drop trigger fail_review')
    store.complete_agent_run(audit.id, reviewed.model_dump(mode='json'), owner='audit', transcript_end_line=1)
    assert store.get_candidate_review_for_audit_run(audit.id)['decision'] == 'approve'
    native.unlink()
    state = AgentOrchestrator(store=store, consumer=None, audit=None)._derive_state(task)
    assert state.audit_result == reviewed
    assert state.consumer_result == result
    assert store.get_agent_run(audit.id).final_result_json == ''


def test_system_executor_matches_adopted_action_when_native_raw_differs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.system_executor import SystemExecutor
    from app.worker import _accepted_consumer_result
    from app.dingtalk_send_evidence import DingTalkSendEvidenceDriver
    from tests.test_reviewed_candidates import _candidate

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    raw = _candidate()
    _native_final(tmp_path, monkeypatch, store, run.id, raw.model_dump(mode='json'))
    prepared = raw.model_copy(deep=True)
    prepared.proposal.actions[0].payload['text'] = 'prepared with service feedback'
    store.complete_agent_run(run.id, prepared.model_dump(mode='json'), owner='consumer', transcript_end_line=1)
    receipt = {'first_agent_run_id': run.id}
    executor = SystemExecutor(store)
    assert executor._prior_action_matches(receipt, prepared.proposal.actions[0])
    assert not executor._prior_action_matches(receipt, raw.proposal.actions[0])
    audit = SimpleNamespace(parent_agent_run_id=run.id)
    assert _accepted_consumer_result(store, audit) == prepared
    assert DingTalkSendEvidenceDriver(store)._accepted_actions(audit) == [
        prepared.proposal.actions[0].model_dump(mode='json')
    ]


def test_zero_length_runtime_attempt_does_not_read_a_reused_session(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    attempt = store.claim_agent_runtime_attempt(run.id, 'codex_oauth', 'codex_cli', 'local_oauth', 'test')
    store.set_agent_runtime_attempt_session(attempt.id, 'reused', 'codex_session:reused')
    def unrelated(*args, **kwargs):
        raise AssertionError('unbounded native session read')
    monkeypatch.setattr(native_trajectory, 'read_codex_events', unrelated)
    with store._connect() as db:
        row = db.execute('select * from agent_runs where id=?', (run.id,)).fetchone()
        assert native_trajectory.read_run_events(db, row) == []


def test_missing_session_history_uses_one_inventory_and_refreshes_native_refs(tmp_path, monkeypatch):
    root = tmp_path / 'codex'
    (root / 'sessions').mkdir(parents=True)
    calls = []
    original = type(root).rglob
    def counted(path, pattern):
        calls.append(path)
        return original(path, pattern)
    monkeypatch.setattr(type(root), 'rglob', counted)
    for number in range(200):
        assert native_trajectory.find_codex_session_path(f'missing-{number}', codex_home=root) is None
    assert len(calls) == 2
    path = root / 'sessions' / 'new.jsonl'
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'new'}}) + '\n')
    from app.codex_history import _append_session_path_index
    _append_session_path_index(root, 'new', path)
    assert native_trajectory.find_codex_session_path('new', codex_home=root) == path
    assert len(calls) == 2


def test_historical_native_audit_shape_still_drives_delivery_quality(tmp_path, monkeypatch):
    from app.quality_gate import _check_delivery_records
    from tests.test_agent_turn_store import _claim_audit

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_audit(store, _task(store))
    payload = {'outcome': 'executed', 'external_result': {'live_result_reference': {'openTaskId': 'receipt'}}}
    _native_final(tmp_path, monkeypatch, store, run.id, payload)
    store.complete_agent_run(run.id, payload, owner='audit', transcript_end_line=1)
    assert json.loads(store.get_agent_run(run.id).final_result_json) == payload
    with store._connect() as db:
        issues = []
        _check_delivery_records(db, issues)
    assert issues[0].code == 'executed_without_record'


def test_retirement_reads_original_native_question_without_database_body(tmp_path, monkeypatch):
    from app.rule_question_retirement import retire_rule_question
    from tests.test_rule_question_retirement import seed, AUTHORITY

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    attempt_id, task_id, run_id, payload = seed(store)
    _native_final(tmp_path, monkeypatch, store, run_id, payload)
    with store._connect() as db:
        db.execute("update agent_runs set final_result_json='' where id=?", (run_id,))
    result = retire_rule_question(store, attempt_id, authority=AUTHORITY, apply=True)
    assert result['applied'] is True
    assert retire_rule_question(store, attempt_id, authority=AUTHORITY, apply=True) == result


def test_reopening_legacy_event_column_does_not_repopulate_event_table(tmp_path):
    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    with store._connect() as db:
        db.execute("alter table agent_runs add column tool_events_json text not null default '[]'")
        db.execute("update agent_runs set tool_events_json=? where id=?", (json.dumps([{'type': 'item.completed', 'item': {'text': 'legacy-only'}}]), run.id))
        store._migrate_agent_run_events(db)
        assert db.execute('select count(*) from agent_run_events').fetchone()[0] == 0
        assert db.execute('select tool_events_json from agent_runs where id=?', (run.id,)).fetchone()[0] == '[]'


def test_prepared_failure_does_not_adopt_native_proposed_success(tmp_path, monkeypatch):
    from app.agent_contracts import ConsumerOutcome
    from app.agent_result import AgentError
    from app.agent_runtime_config import load_runtime_config
    from app.agent_runtime_router import RuntimeRouteDecision
    from app.agent_turn_runner import AgentTurnProcess
    from app.agent_wire_contracts import parse_consumer_agent_wire_result
    from app.agent_orchestrator import AgentOrchestrator
    from app.process_runner import ProcessRunResult

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    wire = {'outcome': 'no_action', 'summary': 'Proposed success', 'proposal': None,
            'error_code': '', 'error_retryable': False, 'error_authorization_required': False,
            'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0,
            'information_completeness': 1.0, 'durable_memories': []}
    _native_final(tmp_path, monkeypatch, store, run.id, wire)
    monkeypatch.setattr('app.agent_turn_runner.count_codex_session_lines', lambda *a, **k: 1)
    config = load_runtime_config({'CEO_AGENT_RUNTIME_ROUTES': 'codex_oauth'})
    class Router:
        def first_route_decision(self, **kwargs):
            return RuntimeRouteDecision(config.routes[0], False, 'eligible_route')
    def execute(_command, *, on_stdout_line, **kwargs):
        stream = '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': f'native-{run.id}'}),
                            json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(wire)}})])
        for line in stream.splitlines():
            on_stdout_line(line)
        return ProcessRunResult(0, stream, '')
    def prepare(result):
        return result.model_copy(update={'outcome': ConsumerOutcome.FAILED,
            'summary': 'Preparation failed', 'error': AgentError(code='preparation_failed', retryable=False)})
    result = AgentTurnProcess(store=store, task=task, workspace=tmp_path, owner='consumer',
                              executor=execute, runtime_config=config, runtime_router=Router()).execute(
        run=run, prompt='task', session_id=None, developer_instructions='',
        configure_command=lambda command: None, parse_result=parse_consumer_agent_wire_result,
        prepare_result=prepare, persist_conversation_session=True,
    )
    assert result.result.outcome is ConsumerOutcome.FAILED
    assert store.get_agent_run(run.id).status == 'failed'
    assert store.get_agent_run(run.id).final_result_json == ''
    assert store.adopted_candidate_for_consumer_run(run.id) is None
    assert store.list_agent_runtime_attempts(run.id)[0].status == 'failed'
    state = AgentOrchestrator(store=store, consumer=None, audit=None)._derive_state(task)
    assert state.status == 'failed_terminal'
    assert state.error.code == 'preparation_failed'


def test_prepared_failure_transition_is_atomic_and_recoverable(tmp_path):
    import pytest
    from app.agent_orchestrator import AgentOrchestrator

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    attempt = store.claim_agent_runtime_attempt(run.id, 'codex_oauth', 'codex_cli', 'local_oauth', 'gpt-5.5')
    store.append_agent_run_event(run.id, {'type': 'runtime.prompt'}, owner='consumer')
    error = {'code': 'preparation_failed', 'retryable': False,
             'reported_summary': 'The proposed plan could not be prepared.'}
    with store._connect() as db:
        db.execute("create trigger fail_terminal before update of status on agent_runs when new.status='failed' begin select raise(abort, 'failure_write_failed'); end")
    with pytest.raises(sqlite3.IntegrityError, match='failure_write_failed'):
        store.fail_agent_runtime_attempt(attempt.id, 'result', 'runtime_business_result_failed', False,
            owner='consumer', agent_run_error=error, agent_run_transcript_end=2)
    assert store.get_agent_run(run.id).status == 'running'
    assert store.get_agent_runtime_attempt(attempt.id).status == 'starting'
    assert native_trajectory.live_events(str(store.path.resolve()), run.id)
    with store._connect() as db:
        db.execute('drop trigger fail_terminal')
    store.fail_agent_runtime_attempt(attempt.id, 'result', 'runtime_business_result_failed', False,
        owner='consumer', agent_run_error=error, agent_run_transcript_end=2)
    assert native_trajectory.live_events(str(store.path.resolve()), run.id) is None
    reopened = AutoReplyStore(store.path)
    assert reopened.get_agent_runtime_attempt(attempt.id).status == 'failed'
    assert reopened.adopted_candidate_for_consumer_run(run.id) is None
    state = AgentOrchestrator(store=reopened, consumer=None, audit=None)._derive_state(task)
    assert state.status == 'failed_terminal'
    assert state.error.code == 'preparation_failed'


def test_failed_claude_attempt_keeps_exact_native_bounds(tmp_path, monkeypatch):
    from app.agent_turn_runner import AgentTurnProcess

    store = AutoReplyStore(tmp_path / 'runs.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    path = tmp_path / 'claude.jsonl'
    records = [
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'call-1',
            'name': 'Bash', 'input': {'command': 'echo native'}}]}},
        {'type': 'user', 'message': {'content': [{'type': 'tool_result',
            'tool_use_id': 'call-1', 'content': 'native output'}]}},
    ]
    path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
    monkeypatch.setattr(native_trajectory, 'claude_session_path', lambda *a, **k: path)
    attempt = store.claim_agent_runtime_attempt(run.id, 'claude', 'claude_cli', 'local_oauth', 'test')
    attempt = store.set_agent_runtime_attempt_session(attempt.id, 'session', 'claude_session:session', transcript_start=0)
    AgentTurnProcess(store=store, task=task, workspace=tmp_path, owner='consumer')._fail_runtime_attempt_unclassified(
        attempt, ValueError('invalid final result'))
    saved = store.get_agent_runtime_attempt(attempt.id)
    assert (saved.transcript_start, saved.transcript_end) == (0, 2)
    store.fail_agent_run(run.id, {'code': 'invalid_result'}, owner='consumer')
    with path.open('a') as stream:
        stream.write(json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'unrelated later turn'}]}}) + '\n')
    native_trajectory._LIVE_EVENTS.clear()
    events = AutoReplyStore(store.path).get_agent_run(run.id).tool_events
    assert 'native output' in json.dumps(events)
    assert 'unrelated later turn' not in json.dumps(events)
