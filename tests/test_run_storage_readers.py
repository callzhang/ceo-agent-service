"""Native locator readers must use unified runs without old physical tables."""
import sqlite3

import pytest

from app.native_standalone import exact_standalone_ref, latest_standalone_ref, latest_task_ref
from app.workbench.native_events import _attempts


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('''create table runtime_attempts (
        id integer primary key, parent_id integer, workload_kind text, workload_key text,
        runtime_kind text, session_id text, transcript_start integer, transcript_end integer,
        status text
    )''')
    yield connection
    connection.close()


def seed_attempt(db, public_id, *, workload_kind, workload_key, session, start, end,
                 status='completed', parent_id=None):
    db.execute('''insert into runtime_attempts (
        id,parent_id,workload_kind,workload_key,runtime_kind,session_id,
        transcript_start,transcript_end,status
    ) values (?,?,?,?,?,?,?,?,?)''',
               (public_id, parent_id, workload_kind, workload_key,
                'codex_cli', session, start, end, status))


def test_task_reader_preserves_repair_namespace_and_original_attempt_order(db):
    seed_attempt(db, 20, workload_kind='task', workload_key='7', session='base', start=1, end=4, parent_id=7)
    seed_attempt(db, 21, workload_kind='task', workload_key='7', session='repair', start=5, end=9, parent_id=7)
    seed_attempt(db, 22, workload_kind='task', workload_key='8', session='other-parent', start=1, end=2, parent_id=8)
    ref = latest_task_ref(db, 7)
    assert (ref.session_id, ref.start, ref.end) == ('repair', 5, 9)


def test_exact_okr_reader_does_not_substitute_later_request_result(db):
    seed_attempt(db, 40, workload_kind='structured', workload_key='8', session='old', start=1, end=3)
    seed_attempt(db, 41, workload_kind='structured', workload_key='8', session='new', start=5, end=7)
    row = {'codex_session_id': 'old', 'codex_transcript_start_line': 1,
           'codex_transcript_end_line': 3}
    ref = exact_standalone_ref(db, workload_kind='structured', workload_key='8', row=row)
    assert (ref.session_id, ref.start, ref.end) == ('old', 1, 3)
    row['codex_transcript_end_line'] = 4
    assert exact_standalone_ref(db, workload_kind='structured', workload_key='8', row=row) is None


def test_failed_meeting_attempt_remains_unavailable_without_completed_source(db):
    seed_attempt(db, 50, workload_kind='meeting', workload_key='9', session='failed', start=1, end=2, status='failed')
    legacy = {'codex_session_id': 'legacy', 'codex_transcript_start_line': 1,
              'codex_transcript_end_line': 2}
    assert latest_standalone_ref(db, workload_kind='meeting', workload_key='9', legacy_row=legacy) is None


def test_workbench_reader_uses_exact_turn_uuid(db):
    first = '00000000-0000-4000-8000-000000000001'
    second = '00000000-0000-4000-8000-000000000002'
    seed_attempt(db, 60, workload_kind='workbench', workload_key=first, session='first', start=1, end=3)
    seed_attempt(db, 61, workload_kind='workbench', workload_key=second, session='second', start=4, end=8)
    rows = _attempts(db, first)
    assert len(rows) == 1
    assert (rows[0]['session_id'], rows[0]['transcript_start'], rows[0]['transcript_end']) == ('first', 1, 3)
