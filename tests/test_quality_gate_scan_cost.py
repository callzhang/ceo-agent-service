import sqlite3
from collections import Counter

import pytest

from app import leak_check, quality_gate
from app.run_storage import create_run_schema, insert_run


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
