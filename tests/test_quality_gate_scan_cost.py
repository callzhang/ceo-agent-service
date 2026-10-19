import sqlite3
from collections import Counter
from datetime import datetime, timezone

import pytest

from app import leak_check, quality_gate
from app.run_storage import create_run_schema, insert_run
from app.action_storage import create_action_schema, insert_action, reply_action_projection


def runtime_history(*, count=30, model="model"):
    db = sqlite3.connect(":memory:")
    create_run_schema(db)
    for index in range(count):
        insert_run(db, 'agent_runtime_attempts', {
            'workload_kind': 'task', 'workload_key': str(index), 'attempt_number': 1,
            'status': 'completed', 'route_name': 'route', 'runtime_kind': 'runtime',
            'credential_mode': 'oauth', 'model': model, 'session_id': 'session',
            'transcript_reference': 'session:reference',
        })
    return db


def test_runtime_history_resolves_path_configuration_once_per_scan(monkeypatch):
    calls = []

    def prefixes():
        calls.append(None)
        return ("/configured-private/",)

    monkeypatch.setattr(leak_check, "forbidden_path_prefixes", prefixes)
    monkeypatch.setattr(quality_gate, "forbidden_path_prefixes", prefixes, raising=False)
    with runtime_history() as db:
        issues = []
        quality_gate._check_runtime_attempt_invariants(db, issues)
    assert issues == []
    assert len(calls) == 1


def test_runtime_history_checks_identical_values_once_per_scan(monkeypatch):
    credential_calls = Counter()
    path_calls = Counter()
    credential_check = quality_gate.contains_credential
    path_check = quality_gate.contains_local_runtime_leak

    def credential(value):
        credential_calls[value] += 1
        return credential_check(value)

    def local_path(value, **kwargs):
        path_calls[value] += 1
        return path_check(value, **kwargs)

    monkeypatch.setattr(quality_gate, "contains_credential", credential)
    monkeypatch.setattr(quality_gate, "contains_local_runtime_leak", local_path)
    with runtime_history() as db:
        issues = []
        quality_gate._check_runtime_attempt_invariants(db, issues)

    assert issues == []
    expected = Counter({value: 1 for value in (
        "route", "runtime", "oauth", "model", "session", "", "session:reference",
    )})
    assert credential_calls == expected
    assert path_calls == expected


@pytest.mark.parametrize("column", [
    "route_name", "runtime_kind", "credential_mode", "model", "session_id",
    "failure_code", "transcript_reference",
])
@pytest.mark.parametrize("unsafe_value", [
    "Bearer synthetic-test-credential", "/tmp/runtime-evidence",
])
def test_repeated_unsafe_values_still_count_every_runtime_row(column, unsafe_value):
    with runtime_history(count=4) as db:
        db.execute(f"update runs set {column}=?", (unsafe_value,))
        issues = []
        quality_gate._check_runtime_attempt_invariants(db, issues)

    assert [(issue.code, issue.count) for issue in issues] == [("runtime_secret_leak", 4)]


def test_runtime_history_reads_new_configuration_on_each_scan(monkeypatch):
    monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/first-private/")
    with runtime_history(count=1, model="/first-private/model") as db:
        first = []
        quality_gate._check_runtime_attempt_invariants(db, first)
        monkeypatch.setenv("CEO_FORBIDDEN_PATH_PREFIXES", "/second-private/")
        second = []
        quality_gate._check_runtime_attempt_invariants(db, second)
    assert [(issue.code, issue.count) for issue in first] == [("runtime_secret_leak", 1)]
    assert second == []


def test_explicit_empty_prefixes_keep_fixed_runtime_paths(monkeypatch):
    def unexpected_resolution():
        raise AssertionError("an explicit path configuration must not be reloaded")

    monkeypatch.setattr(leak_check, "forbidden_path_prefixes", unexpected_resolution)
    assert leak_check.contains_local_runtime_leak("/tmp/runtime", path_prefixes=())
    assert leak_check.contains_local_runtime_leak("/var/runtime", path_prefixes=())
    assert leak_check.contains_local_runtime_leak("/private/var/runtime", path_prefixes=())
    assert not leak_check.contains_local_runtime_leak("ordinary model", path_prefixes=())


def _current_latest_cte(monkeypatch):
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(quality_gate, "_count", lambda *args: 0)
        patch.setattr(quality_gate, "_check_structured_needs_human",
                      lambda db, latest, *args: captured.append(latest))
        quality_gate._check_reply_attempts(
            None, datetime(2026, 10, 14, tzinfo=timezone.utc), [], [],
        )
    return captured[0]


def test_latest_reply_candidates_match_original_window_partitions_and_order(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("""create table attempts (
        id integer primary key, channel text, conversation_id text,
        trigger_message_id text, action text, send_status text, updated_at text,
        reviewed_at text, resolved_at text, agent_run_id integer
    )""")
    db.execute("create index trigger_identity on attempts(channel,conversation_id,trigger_message_id,updated_at desc,id desc)")
    rows = []

    def add(key, status, timestamp, *, channel="dingtalk", conversation="chat"):
        rows.append((len(rows) + 1, channel, conversation, key, "send_reply",
                     status, timestamp, None, "", None))

    for candidate in ("failed", "BLOCKED", "dry_run", "needs_human"):
        for later in ("sent", "skipped", "completed", "unknown", None, "failed"):
            key = f"{candidate}:{later}"
            add(key, candidate, "2026-10-14 00:00:00")
            add(key, later, "2026-10-14 01:00:00")
    for channel, conversation, trigger in (
        (None, "chat", "null-channel"),
        ("dingtalk", None, "null-conversation"),
        ("dingtalk", "chat", None),
        (None, None, None),
    ):
        add(trigger, "sent", "2026-10-14 00:00:00", channel=channel, conversation=conversation)
        add(trigger, "failed", "2026-10-14 01:00:00", channel=channel, conversation=conversation)
    add("offset", "failed", "2026-10-14T02:30:00+02:00")
    add("offset", "sent", "2026-10-14T00:45:00Z")
    add("equal-instant", "sent", "2026-10-14T02:30:00+02:00")
    add("equal-instant", "blocked", "2026-10-14T00:30:00Z")
    add("invalid", "failed", "not-a-date")
    add("invalid", "sent", "also-not-a-date")
    add("valid-over-invalid", "failed", "2026-10-14 00:00:00")
    add("valid-over-invalid", "sent", "zz-invalid")
    add("null-time", "sent", None)
    add("null-time", "needs_human", None)
    add("channel-split", "sent", "2026-10-14 01:00:00")
    add("channel-split", "failed", "2026-10-14 00:00:00", channel="wechat")
    db.executemany("insert into attempts values(?,?,?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(quality_gate, "REPLY_ATTEMPT_COMMON_SQL", "select * from attempts")
    monkeypatch.setattr(quality_gate, "REPLY_ATTEMPT_PROBLEM_TRIGGER_KEYS_SQL", """
        select channel,conversation_id,trigger_message_id from attempts
        where lower(send_status) in ('failed','blocked','dry_run','needs_human')
    """)
    candidate = _current_latest_cte(monkeypatch)
    original = """with latest as (
        select *,row_number() over (
            partition by channel,conversation_id,trigger_message_id
            order by datetime(updated_at) desc,id desc
        ) as ordinal from attempts
    )"""
    selection = """select id,channel,conversation_id,trigger_message_id,send_status,updated_at
        from latest where ordinal=1
        and lower(send_status) in ('failed','blocked','dry_run','needs_human') order by id"""
    assert db.execute(candidate + selection).fetchall() == db.execute(original + selection).fetchall()
    db.close()


@pytest.mark.parametrize("history_shape", ["terminal", "repeated_candidates", "many_candidate_keys"])
def test_latest_reply_candidates_bound_terminal_and_repeated_candidate_history(
    monkeypatch, history_shape,
):
    db = sqlite3.connect(":memory:")
    db.execute("create table tasks(id integer primary key,record_type text)")
    create_run_schema(db)
    create_action_schema(db, include_receipts=True, include_postfix=True,
                         include_delivery=True, include_reply=True,
                         reply_variant="fresh_memory_fk", compact_payload=True)
    insert_action(db, "reply_attempts", {
        "id": 1, "conversation_id": "chat", "conversation_title": "Chat",
        "trigger_message_id": "seed", "trigger_sender": "Sender", "trigger_text": "text",
        "action": "send_reply", "sensitivity_kind": "normal", "send_status": "sent",
        "updated_at": "2026-10-14 00:00:00",
    })
    cursor = db.execute("select * from (" + reply_action_projection("reply_attempts", "fresh_memory_fk") + ") where id=1")
    payload = dict(zip([column[0] for column in cursor.description], cursor.fetchone(), strict=True))
    repeated_candidates = history_shape == "repeated_candidates"
    history_count = 200 if repeated_candidates else 2000
    first_failure = history_count - 48 if history_shape == "many_candidate_keys" else history_count + 1
    for identity in range(2, history_count + 2):
        item = {**payload, "id": identity, "trigger_message_id": "seed" if repeated_candidates else f"message-{identity}"}
        if repeated_candidates or identity >= first_failure:
            item["send_status"] = "failed"
        insert_action(db, "reply_attempts", item)
    cte = _current_latest_cte(monkeypatch)
    steps = 0

    def progress():
        nonlocal steps
        steps += 100
        return 0

    db.set_progress_handler(progress, 100)
    rows = db.execute(cte + """select id,channel,conversation_id,trigger_message_id,
        action,send_status,updated_at,reviewed_at,resolved_at,agent_run_id
        from latest where ordinal=1
        and lower(send_status) in ('failed','blocked','dry_run','needs_human') order by id""").fetchall()
    db.set_progress_handler(None, 0)
    expected_ids = list(range(first_failure, history_count + 2)) if history_shape == "many_candidate_keys" else [history_count + 1]
    assert [row[0] for row in rows] == expected_ids
    assert steps < history_count * 120
    db.close()
