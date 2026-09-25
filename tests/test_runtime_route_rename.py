"""Renaming an added runtime route carries every reference to it."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent_cron.models import ScheduledTaskSnapshot
from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.config import read_env_file
from app.store import AgentRole, AutoReplyStore
from tests.test_console_web_api import _client


NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def _task(store: AutoReplyStore, name: str, runtime_id: str):
    return store.create_scheduled_task(
        name=name,
        prompt="summarize",
        cron_expression="0 9 * * *",
        timezone_name="Asia/Shanghai",
        runtime_id=runtime_id,
        now=NOW,
    )


def _run(store: AutoReplyStore, task_id: int, minutes: int, status: str):
    run = store.create_scheduled_task_run(
        task_id,
        trigger_kind="manual",
        scheduled_for=NOW + timedelta(minutes=minutes),
        now=NOW,
    )
    with store._connect() as db:
        db.execute(
            "update scheduled_task_runs set dispatch_status=? where id=?",
            (status, run.id),
        )
    return run.id


def _snapshot_runtime(store: AutoReplyStore, run_id: int) -> str:
    with store._connect() as db:
        row = db.execute(
            "select snapshot_json from scheduled_task_runs where id=?", (run_id,)
        ).fetchone()
    return ScheduledTaskSnapshot.from_json(str(row["snapshot_json"])).runtime_id


def _attempt_route(store: AutoReplyStore) -> int:
    assert store.enqueue_reply_task(
        conversation_id="cid-history",
        conversation_title="History",
        single_chat=False,
        trigger_message_id="msg-history",
        trigger_create_time="2026-09-24 09:00:00",
        trigger_sender="Derek",
        trigger_text="route this",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = store.claim_agent_run(
        task.id,
        "initial",
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{task.id}:initial",
        owner="rename-test",
    ).run
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "MiniMax-M3"
    )
    return attempt.id


def _seed(store: AutoReplyStore) -> dict[str, int]:
    renamed = _task(store, "daily", "codex_api")
    other = _task(store, "other", "codex_oauth")
    ids = {
        "renamed_task": renamed.id,
        "other_task": other.id,
        "pending": _run(store, renamed.id, 1, "pending"),
        "dispatched": _run(store, renamed.id, 2, "dispatched"),
        "skipped": _run(store, renamed.id, 3, "skipped"),
        "other_pending": _run(store, other.id, 4, "pending"),
        "attempt": _attempt_route(store),
    }
    store.upsert_conversation_runtime_session("cid-1", "codex_api", "session-1", "c1")
    store.upsert_conversation_runtime_session("cid-1", "codex_oauth", "oauth-1", "c1")
    # A leftover row under the new name belongs to a route that no longer exists.
    store.upsert_conversation_runtime_session("cid-2", "kksj", "stale-2", "c1")
    store.upsert_conversation_runtime_session("cid-2", "codex_api", "session-2", "c1")
    store.open_runtime_route_pause(
        "codex_api", "provider_capacity", NOW + timedelta(days=3650)
    )
    store.record_runtime_capability_snapshot(
        RuntimeCapabilitySnapshot(
            route_name="codex_api",
            capabilities=frozenset({"structured_output"}),
            healthy=True,
            checked_at="2026-09-24T09:59:00+00:00",
            expires_at="2026-09-24T10:05:00+00:00",
        ),
        pid=4242,
    )
    return ids


def test_rename_carries_every_reference_and_keeps_attempt_history(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "rename.sqlite3")
    ids = _seed(store)
    version = store.get_scheduled_task(ids["renamed_task"]).version

    moved = store.rename_runtime_route("codex_api", "kksj")

    assert moved == {
        "scheduled_tasks": 1,
        "scheduled_task_runs": 2,
        "conversation_sessions": 2,
        "route_pauses": 1,
        "capability_snapshots": 1,
    }
    task = store.get_scheduled_task(ids["renamed_task"])
    assert task.runtime_id == "kksj"
    assert task.version == version + 1
    assert store.get_scheduled_task(ids["other_task"]).runtime_id == "codex_oauth"
    # Runs that can still be dispatched or rebuilt follow; a final one does not.
    assert _snapshot_runtime(store, ids["pending"]) == "kksj"
    assert _snapshot_runtime(store, ids["dispatched"]) == "kksj"
    assert _snapshot_runtime(store, ids["skipped"]) == "codex_api"
    assert _snapshot_runtime(store, ids["other_pending"]) == "codex_oauth"
    assert store.get_conversation_runtime_session("cid-1", "kksj") == "session-1"
    assert store.get_conversation_runtime_session("cid-1", "codex_api") is None
    assert store.get_conversation_runtime_session("cid-1", "codex_oauth") == "oauth-1"
    assert store.get_conversation_runtime_session("cid-2", "kksj") == "session-2"
    assert store.active_runtime_route_pause("kksj") == "provider_capacity"
    assert store.active_runtime_route_pause("codex_api") is None
    assert store.runtime_capability_snapshots_for_pid(
        ("kksj", "codex_api"), pid=4242
    ) == {
        "kksj": RuntimeCapabilitySnapshot(
            route_name="kksj",
            capabilities=frozenset({"structured_output"}),
            healthy=True,
            checked_at="2026-09-24T09:59:00+00:00",
            expires_at="2026-09-24T10:05:00+00:00",
        )
    }
    # History records the name that was in use at the time.
    assert store.get_agent_runtime_attempt(ids["attempt"]).route_name == "codex_api"


def test_a_committed_rename_moves_nothing_when_retried(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "rename.sqlite3")
    _seed(store)
    store.rename_runtime_route("codex_api", "kksj")

    assert store.rename_runtime_route("codex_api", "kksj") == {
        "scheduled_tasks": 0,
        "scheduled_task_runs": 0,
        "conversation_sessions": 0,
        "route_pauses": 0,
        "capability_snapshots": 0,
    }
    assert store.get_conversation_runtime_session("cid-1", "kksj") == "session-1"


def test_a_rename_needs_a_different_name(tmp_path: Path):
    with pytest.raises(ValueError):
        AutoReplyStore(tmp_path / "rename.sqlite3").rename_runtime_route("kksj", "kksj")


ENV = (
    "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,claude_oauth\n"
    "CEO_RUNTIME_CODEX_API_KIND=codex_api\n"
    "CEO_RUNTIME_CODEX_API_BASE_URL=https://gateway.example/v1\n"
    "CEO_RUNTIME_CODEX_API_MODEL=MiniMax-M3\n"
    "CEO_RUNTIME_CODEX_API_API_KEY=codex-secret\n"
)


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(path))
    return path


def _rename(tmp_path: Path, name: str, new_name: str):
    with _client(tmp_path) as client:
        return client.post(
            f"/api/console/settings/agent-runtime/routes/{name}/rename",
            json={"new_name": new_name},
        )


def test_the_console_rename_moves_the_settings_and_the_database_references(
    tmp_path: Path, env_path: Path
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store, "daily", "codex_api")

    response = _rename(tmp_path, "codex_api", "kksj")

    assert response.status_code == 200, response.json()
    assert response.json()["item"]["moved"]["scheduled_tasks"] == 1
    values = read_env_file(env_path)
    assert values["CEO_AGENT_RUNTIME_ROUTES"] == "codex_oauth,kksj,claude_oauth"
    assert values["CEO_RUNTIME_KKSJ_KIND"] == "codex_api"
    assert values["CEO_RUNTIME_KKSJ_BASE_URL"] == "https://gateway.example/v1"
    assert values["CEO_RUNTIME_KKSJ_MODEL"] == "MiniMax-M3"
    assert values["CEO_RUNTIME_KKSJ_API_KEY"] == "codex-secret"
    assert not [key for key in values if key.startswith("CEO_RUNTIME_CODEX_API_")]
    assert store.get_scheduled_task(task.id).runtime_id == "kksj"
    assert "codex-secret" not in json.dumps(response.json())


@pytest.mark.parametrize(
    ("name", "new_name", "message"),
    [
        ("codex_oauth", "kksj", "内置线路 codex_oauth 不能改名"),
        ("friday_runtime", "kksj", "内置线路 friday_runtime 不能改名"),
        ("codex_api", "claude_oauth", "claude_oauth 是内置线路的名字"),
        ("codex_api", "Kksj", "小写字母"),
        ("codex_api", "", "小写字母"),
        ("unknown", "kksj", "不在已配置的线路里"),
    ],
)
def test_the_console_rename_refuses_built_in_invalid_and_unknown_names(
    tmp_path: Path, env_path: Path, name: str, new_name: str, message: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _task(store, "daily", "codex_api")

    response = _rename(tmp_path, name, new_name)

    assert response.status_code == 400
    assert message in response.json()["message"]
    assert env_path.read_text(encoding="utf-8") == ENV
    assert store.get_scheduled_task(task.id).runtime_id == "codex_api"


def test_the_console_rename_refuses_a_name_already_configured(
    tmp_path: Path, env_path: Path
):
    env_path.write_text(
        ENV.replace("codex_oauth,codex_api,claude_oauth", "codex_oauth,codex_api,kksj")
        + "CEO_RUNTIME_KKSJ_KIND=codex_oauth\nCEO_RUNTIME_KKSJ_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )
    before = env_path.read_text(encoding="utf-8")

    response = _rename(tmp_path, "codex_api", "kksj")

    assert response.status_code == 400
    assert "已经存在" in response.json()["message"]
    assert env_path.read_text(encoding="utf-8") == before
