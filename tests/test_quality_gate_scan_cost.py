import sqlite3

from app import leak_check, quality_gate


def runtime_history(*, count=30, model="model"):
    db = sqlite3.connect(":memory:")
    db.execute("create table agent_runs (id integer primary key, status text)")
    db.execute(
        """create table agent_runtime_attempts (
        agent_run_id integer, workload_kind text, workload_key text, status text,
        route_name text, runtime_kind text, credential_mode text, model text,
        session_id text, failure_code text, transcript_reference text)"""
    )
    db.executemany(
        """insert into agent_runtime_attempts values (
        null, 'task', ?, 'completed', 'route', 'runtime', 'oauth', ?,
        'session', '', 'session:reference')""",
        [(str(index), model) for index in range(count)],
    )
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
