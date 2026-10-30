import sqlite3
from contextlib import closing

from app.action_storage import REPLY_ATTEMPT_COMMON_SQL, insert_action
from app.current_database_schema import create_current_database_schema
from app.run_entity_storage import create_run
from app.task8_storage import register_task8_functions


def test_equal_terminal_run_provenance_and_summary_are_stored_once():
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        run = create_run(db, values={
            "status": "completed", "session_id": "native-session",
            "transcript_start": 12, "transcript_end": 34,
        })
        result = insert_action(db, "reply_attempts", {
            "conversation_id": "conversation", "conversation_title": "Conversation",
            "trigger_message_id": "message", "trigger_sender": "sender", "trigger_text": "input",
            "action": "agent_run", "sensitivity_kind": "general", "send_status": "completed",
            "agent_run_id": run["id"], "codex_session_id": "native-session",
            "codex_transcript_start_line": 12, "codex_transcript_end_line": 34,
            "codex_reason": "All approved actions verified", "audit_summary": "All approved actions verified",
        })
        raw = db.execute(
            "select json_extract(payload_json,'$[7]'),json_extract(payload_json,'$[22]'),"
            "json_extract(payload_json,'$[52]'),json_extract(payload_json,'$[53]') "
            "from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?",
            (result.lastrowid,),
        ).fetchone()
        assert tuple(raw) == (None, None, None, None)
        row = db.execute(
            f"select codex_reason,audit_summary,codex_session_id,codex_transcript_start_line,"
            f"codex_transcript_end_line from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (result.lastrowid,),
        ).fetchone()
        assert tuple(row) == ("All approved actions verified", "All approved actions verified", "native-session", 12, 34)
        from app.action_storage import _ATTEMPT_ORDER, _FRESH_ATTEMPT_ORDER, _MEMORY_ORDER
        from app.current_action_schema import current_reply_action_projection
        for sql, expected in ((REPLY_ATTEMPT_COMMON_SQL, _FRESH_ATTEMPT_ORDER),
                              (current_reply_action_projection("reply_attempts"), _ATTEMPT_ORDER),
                              (current_reply_action_projection("memory_write_events"), _MEMORY_ORDER)):
            cursor = db.execute(f"select * from ({sql}) limit 0")
            assert [column[0] for column in cursor.description] == expected



def test_distinct_and_unowned_facts_survive_updates():
    from app.action_storage import update_action
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        first = create_run(db, values={'status': 'completed', 'session_id': 'first', 'transcript_start': 1, 'transcript_end': 2})
        second = create_run(db, values={'status': 'completed', 'session_id': 'second', 'transcript_start': 3, 'transcript_end': 4})
        running = create_run(db, values={'status': 'running', 'session_id': 'running', 'transcript_start': 5, 'transcript_end': 6})
        def insert(run_id, session, start, end, reason, summary):
            return insert_action(db, 'reply_attempts', {
                'conversation_id': 'conversation', 'conversation_title': 'title',
                'trigger_message_id': 'message', 'trigger_sender': 'sender', 'trigger_text': 'input',
                'action': 'agent_run', 'sensitivity_kind': 'general', 'send_status': 'completed',
                'agent_run_id': run_id, 'codex_session_id': session,
                'codex_transcript_start_line': start, 'codex_transcript_end_line': end,
                'codex_reason': reason, 'audit_summary': summary,
            }).lastrowid
        def values(id):
            return tuple(db.execute(f'select codex_reason,audit_summary,codex_session_id,codex_transcript_start_line,codex_transcript_end_line,agent_run_id from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?', (id,)).fetchone())
        def raw(id):
            return tuple(db.execute("select json_extract(payload_json,'$[7]'),json_extract(payload_json,'$[22]'),json_extract(payload_json,'$[52]'),json_extract(payload_json,'$[53]') from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?", (id,)).fetchone())
        owned = insert(first['id'], 'first', 1, 2, 'same', 'same')
        assert raw(owned) == (None, None, None, None)
        db.execute('create temp table update_count (n integer)')
        db.execute('create temp trigger count_attempt_update after update on main.actions begin insert into update_count values (1); end')
        update_action(db, 'reply_attempts', owned, {'audit_summary': 'new'})
        assert db.execute('select count(*) from update_count').fetchone()[0] == 1
        assert values(owned) == ('same', 'new', 'first', 1, 2, first['id'])
        update_action(db, 'reply_attempts', owned, {'codex_reason': ''})
        assert values(owned)[0:2] == ('', 'new')
        update_action(db, 'reply_attempts', owned, {'agent_run_id': second['id']})
        assert values(owned) == ('', 'new', 'first', 1, 2, second['id'])
        assert raw(owned) == ('', 'first', 1, 2)
        before = db.total_changes
        assert update_action(db, 'reply_attempts', owned, {'audit_summary': 'ignored'}, where_sql='audit_summary=?', parameters=('wrong',)).rowcount == 0
        assert db.total_changes == before
        orphan = insert(None, 'orphan', 7, 8, 'different', 'summary')
        assert raw(orphan) == ('different', 'orphan', 7, 8)
        mismatch = insert(first['id'], 'different', 1, 2, '', 'summary')
        assert raw(mismatch) == ('', 'different', 1, 2)
        live = insert(running['id'], 'running', 5, 6, 'x', 'y')
        assert raw(live) == ('x', 'running', 5, 6)


def test_raw_noncanonical_cells_and_invalid_owner():
    import pytest
    from app.attempt_run_ownership import materialize_payload, normalize_payload
    from app.task_lifecycle_jsonb import _array, _framed
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        run = create_run(db, values={'status': 'failed', 'session_id': 's', 'transcript_start': 1, 'transcript_end': 2})
        result = insert_action(db, 'reply_attempts', {
            'conversation_id': 'c', 'conversation_title': 't', 'trigger_message_id': 'm',
            'trigger_sender': 's', 'trigger_text': 't', 'action': 'agent_run',
            'sensitivity_kind': 'general', 'send_status': 'failed', 'agent_run_id': run['id'],
            'codex_session_id': 's', 'codex_transcript_start_line': 1, 'codex_transcript_end_line': 2,
            'codex_reason': 'r', 'audit_summary': 'r',
        })
        blob = db.execute('select payload_json from actions where record_type=? and cast(legacy_key as integer)=?', ('reply_attempts', result.lastrowid)).fetchone()[0]
        original = materialize_payload(db, blob)
        width, cells = _array(original)
        cells[52] = b'\xc3\x01' + b'1'  # same integer with a wider JSONB header
        padded = _framed(cells, width)
        assert _array(normalize_payload(db, padded))[1][52] == cells[52]
        cells[52] = db.execute("select jsonb('1')").fetchone()[0]  # canonical integer
        cells[53] = db.execute("select jsonb('\"2\"')").fetchone()[0]  # text, not integer
        assert _array(normalize_payload(db, _framed(cells, width)))[1][53] == cells[53]
        width, cells = _array(blob)
        cells[55] = b'\x00'
        with pytest.raises(ValueError, match='invalid Attempt Run owner'):
            materialize_payload(db, _framed(cells, width))
        cells[22] = db.execute("select jsonb('\"s\"')").fetchone()[0]
        with pytest.raises(ValueError, match='mixed null'):
            materialize_payload(db, _framed(cells, width))


def test_public_null_inputs_keep_nonnull_contract_and_ignore_semantics():
    import pytest
    from app.action_storage import update_action
    from app.attempt_run_ownership import normalize_payload, materialize_payload
    from app.task_lifecycle_jsonb import _array, _framed
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        base = {'conversation_id': 'c', 'conversation_title': 't', 'trigger_message_id': 'm',
                'trigger_sender': 's', 'trigger_text': 't', 'action': 'agent_run',
                'sensitivity_kind': 'general', 'send_status': 'completed'}
        existing = insert_action(db, 'reply_attempts', base).lastrowid
        for field in ('codex_reason', 'codex_session_id', 'codex_transcript_start_line', 'codex_transcript_end_line'):
            with pytest.raises(sqlite3.IntegrityError):
                insert_action(db, 'reply_attempts', base | {field: None})
            with pytest.raises(sqlite3.IntegrityError):
                update_action(db, 'reply_attempts', existing, {field: None})
            assert update_action(db, 'reply_attempts', existing, {field: None}, where_sql='id=?', parameters=(-1,)).rowcount == 0
            assert insert_action(db, 'reply_attempts', base | {'id': existing, field: None}, ignore=True).rowcount == 0
            assert insert_action(db, 'reply_attempts', base | {'id': 1000, field: None}, ignore=True).rowcount == 0
        assert insert_action(db, 'reply_attempts', base).lastrowid == 1001
        with pytest.raises(sqlite3.IntegrityError):
            insert_action(db, 'reply_attempts', base | {'agent_run_id': 999999, 'codex_session_id': None,
                          'codex_transcript_start_line': None, 'codex_transcript_end_line': None})
        run = create_run(db, values={'status': 'completed', 'session_id': 's', 'transcript_start': 1, 'transcript_end': 2})
        id = insert_action(db, 'reply_attempts', base | {'agent_run_id': run['id'], 'codex_session_id': 's',
                           'codex_transcript_start_line': 1, 'codex_transcript_end_line': 2}).lastrowid
        blob = db.execute('select payload_json from actions where record_type=? and cast(legacy_key as integer)=?', ('reply_attempts', id)).fetchone()[0]
        assert normalize_payload(db, materialize_payload(db, blob)) == blob
        assert normalize_payload(db, blob) == blob
        width, cells = _array(blob)
        cells[55] = db.execute("select jsonb('999999')").fetchone()[0]
        with pytest.raises(ValueError, match='invalid Attempt Run owner'):
            normalize_payload(db, _framed(cells, width))


def test_projection_preserves_affinity_for_conditional_updates_and_raw_types():
    from app.action_storage import update_action
    from app.current_action_schema import current_reply_action_projection
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        base = {'conversation_id': 'c', 'conversation_title': 't', 'trigger_message_id': 'm',
                'trigger_sender': 's', 'trigger_text': 't', 'action': 'agent_run',
                'sensitivity_kind': 'general', 'send_status': 'completed'}
        id = insert_action(db, 'reply_attempts', base | {'codex_session_id': '123',
            'codex_transcript_start_line': 12, 'codex_transcript_end_line': 34}).lastrowid
        for sql in (REPLY_ATTEMPT_COMMON_SQL, current_reply_action_projection('reply_attempts')):
            assert db.execute(f'select count(*) from ({sql}) where id=? and codex_session_id=? and codex_transcript_start_line=? and codex_transcript_end_line=?',
                              (id, 123, '12', '34')).fetchone()[0] == 1
        run = create_run(db, values={'status': 'completed', 'session_id': '123', 'transcript_start': 12, 'transcript_end': 34})
        owned = insert_action(db, 'reply_attempts', base | {'agent_run_id': run['id'], 'codex_session_id': '123',
            'codex_transcript_start_line': 12, 'codex_transcript_end_line': 34}).lastrowid
        assert db.execute(f'select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where id=? and codex_session_id=? and codex_transcript_start_line=? and codex_transcript_end_line=?',
                          (owned, 123, '12', '34')).fetchone()[0] == 1
        result = update_action(db, 'reply_attempts', id, {'audit_summary': 'matched'},
            where_sql='codex_session_id=? and codex_transcript_start_line=? and codex_transcript_end_line=?',
            parameters=(123, '12', '34'))
        assert result.rowcount == 1
        update_action(db, 'reply_attempts', id, {'codex_session_id': b'\x00\xff', 'codex_transcript_start_line': 'not-a-number'})
        row = db.execute(f'select codex_session_id,typeof(codex_session_id),codex_transcript_start_line,typeof(codex_transcript_start_line) from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?', (id,)).fetchone()
        assert tuple(row) == (b'\x00\xff', 'blob', 'not-a-number', 'text')


def test_noncanonical_run_values_leave_local_provenance_intact():
    import pytest
    from app.attempt_run_ownership import materialize_payload, normalize_payload
    from app.task_lifecycle_jsonb import _array, _framed
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_database_schema(db)
        base = {'conversation_id': 'c', 'conversation_title': 't', 'trigger_message_id': 'm',
                'trigger_sender': 's', 'trigger_text': 't', 'action': 'agent_run',
                'sensitivity_kind': 'general', 'send_status': 'completed'}
        for session, start, end in ((b'native-session', 1, 2), ('native', b'\x01', 2),
                                    ('native', 'nonnumeric', 2), ('native', 1.5, 2)):
            run = create_run(db, values={'status': 'failed', 'session_id': session,
                                         'transcript_start': start, 'transcript_end': end})
            attempt = insert_action(db, 'reply_attempts', base | {'agent_run_id': run['id'],
                'codex_session_id': 'independent-session', 'codex_transcript_start_line': 3,
                'codex_transcript_end_line': 4})
            blob = db.execute('select payload_json from actions where record_type=? and cast(legacy_key as integer)=?', ('reply_attempts', attempt.lastrowid)).fetchone()[0]
            width, cells = _array(blob)
            assert tuple(db.execute(f'select codex_session_id,codex_transcript_start_line,codex_transcript_end_line from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?', (attempt.lastrowid,)).fetchone()) == ('independent-session', 3, 4)
            for i in (22, 52, 53):
                cells[i] = b'\x00'
            invalid = _framed(cells, width)
            with pytest.raises(ValueError, match='invalid Attempt Run owner'):
                normalize_payload(db, invalid)
            with pytest.raises(ValueError, match='invalid Attempt Run owner'):
                materialize_payload(db, invalid)
