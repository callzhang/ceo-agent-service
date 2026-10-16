"""A completed child may still have unread events in both OS pipes."""
import json
import selectors
import subprocess
import sys

import pytest

from app import process_runner
from app.codex_decision import extract_codex_session_id
from app.email_unsubscribe_page_judge import parse_page_judgement


@pytest.mark.parametrize('valid', [True, False])
def test_exited_child_drains_stdout_after_stderr_ready(monkeypatch, tmp_path, valid):
    real_popen = subprocess.Popen
    real_selector = selectors.DefaultSelector
    child = []

    def popen(*args, **kwargs):
        value = real_popen(*args, **kwargs)
        child.append(value)
        return value

    class StderrFirst(real_selector):
        def select(self, timeout=None):
            # Deliberately observe stderr first after the child has exited.
            # Its stdout (larger than the normal read chunk) is already queued.
            child[-1].wait(timeout=5)
            return [pair for pair in super().select(timeout)
                    if pair[0].fileobj is child[-1].stderr]

    monkeypatch.setattr(process_runner.subprocess, 'Popen', popen)
    monkeypatch.setattr(process_runner.selectors, 'DefaultSelector', StderrFirst)
    session = '00000000-0000-4000-8000-000000000078'
    answer = json.dumps({'state': 'expired', 'evidence': 'This subscription link expired.'}) if valid else 'invalid judgement'
    events = '\n'.join([
        json.dumps({'type': 'thread.started', 'thread_id': session}),
        json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': answer}}),
    ])
    # >4096 bytes exercises complete draining rather than a single tail read.
    output = (' ' * 5000) + events + '\n'
    script = 'import os; os.write(1, ' + repr(output.encode()) + '); os.write(2,b"diagnostic\\n")'
    lines = []
    result = process_runner.run_process_with_idle_timeout(
        [sys.executable, '-c', script], prompt='', env=None,
        total_timeout_seconds=10, idle_timeout_seconds=10,
        on_stdout_line=lines.append,
    )
    assert result.returncode == 0
    assert result.stderr == 'diagnostic'
    assert result.stdout == output.strip()
    assert extract_codex_session_id(result.stdout) == session
    assert len(lines) == 2
    if valid:
        assert parse_page_judgement(result.stdout).state == 'expired'
    else:
        with pytest.raises(ValueError):
            parse_page_judgement(result.stdout)

    # Exercise durable routed bookkeeping with the same real pipe ordering.
    from app.store import AutoReplyStore
    from app.agent_runtime_router import RoutedCodexExecution, RoutedCodexExecutionError
    from app.email_unsubscribe_page_judge import build_page_judge
    from tests.test_routed_codex_execution import FakeAdapter, config, make_router

    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    from app.task_storage import insert_task
    with store._connect() as db:
        insert_task(db, 'reply_tasks', {
            'conversation_id': 'email:test', 'channel': 'email',
            'conversation_title': 'test', 'single_chat': 1,
            'trigger_create_time': '', 'trigger_sender': 'test',
            'trigger_message_id': 'email-action:' + 'a' * 64,
            'status': 'processing',
        })
    routes = config.__wrapped__()

    def execute_child(_command, **kwargs):
        return process_runner.run_process_with_idle_timeout(
            [sys.executable, '-c', script], prompt=kwargs['prompt'], env=None,
            total_timeout_seconds=10, idle_timeout_seconds=10,
            on_stdout_line=kwargs['on_stdout_line'],
        )

    routed = RoutedCodexExecution(
        store=store, config=routes, router=make_router(store, routes),
        adapter=FakeAdapter(), executor=execute_child,
        session_line_counter=lambda _session: 2,
    )
    judge = build_page_judge(routed)
    if valid:
        assert judge('email-action:' + 'a' * 64, 'example.test', 'expired link', []).state is None
    else:
        with pytest.raises(RoutedCodexExecutionError, match='runtime_result_invalid'):
            judge('email-action:' + 'a' * 64, 'example.test', 'expired link', [])
    from app.run_storage import RUNTIME_ATTEMPT_SQL
    with store._connect() as db:
        row = db.execute('select * from (' + RUNTIME_ATTEMPT_SQL + ')').fetchone()
    assert row['session_id'] == session
    assert row['transcript_reference'] == 'codex_session:' + session
    assert row['transcript_end'] >= 2
    assert row['status'] == ('completed' if valid else 'failed')
    assert row['failure_code'] == ('' if valid else 'runtime_result_invalid')
