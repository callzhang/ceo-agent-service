from __future__ import annotations

import errno

import pytest

from app.store import AutoReplyStore
from app.wechat import scheduled_command
from app.wechat.reader_ipc import ReaderIpcError
from app.wechat.scheduled_command import (
    WECHAT_DATA_PERMISSION_REQUIRED,
    WECHAT_READER_UNAVAILABLE,
    WechatProduceOnceCommand,
)
from tests.test_store_task_entity_creator import _target_store


@pytest.fixture(autouse=True)
def _reader_enabled(monkeypatch):
    monkeypatch.setattr(scheduled_command, "wechat_reader_enabled", lambda: True)


def _store(tmp_path, *, ready: bool = True) -> AutoReplyStore:
    store = _target_store(tmp_path / "wechat-command.sqlite3")
    if ready:
        store.upsert_wechat_read_state(
            account_id="a1", account_dir="/a1", db_dir="/a1/db_storage",
            app_version="4.1.10", self_user_id="self-1", capability_status="ready",
        )
    return store


def _error_kinds(store) -> list[str]:
    return [row.kind for row in reversed(store.list_errors())]


def _reader_health(store) -> dict:
    return next(
        (row for row in store.list_service_health_components()
         if row["component"] == "wechat.reader"),
        {},
    )


def _command(store, *, produce, restarts=None, readers=None):
    def factory():
        readers.append(object())
        return readers[-1]
    return WechatProduceOnceCommand(
        store,
        restart_reader=lambda: restarts.append("restart"),
        reader_factory=factory,
    )


def test_disabled_reader_is_a_summary_and_never_touches_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled_command, "wechat_reader_enabled", lambda: False)
    monkeypatch.setattr(scheduled_command, "ready_account_state", lambda _store: pytest.fail("must not read"))

    result = _command(object(), produce=None, restarts=[], readers=[])()

    assert result.summary == "wechat produce-once skipped: reader disabled"
    assert result.producer_counts is None and not result.complete


def test_missing_ready_account_is_a_summary_and_never_builds_a_reader(tmp_path, monkeypatch):
    store = _store(tmp_path, ready=False)
    monkeypatch.setattr(scheduled_command, "run_produce_once", lambda *a, **k: pytest.fail("must not read"))
    restarts, readers = [], []

    result = _command(store, produce=None, restarts=restarts, readers=readers)()

    assert result.summary == "wechat produce-once skipped: no ready WeChat account"
    assert result.producer_counts is None and not result.complete
    assert readers == [] and restarts == [] and _error_kinds(store) == []


def test_successful_pass_reports_queued_count_and_marks_reader_healthy(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = []

    def produce(store_arg, reader, account, *, self_user_id):
        calls.append((store_arg, reader, account.account_id, self_user_id))
        return 2

    monkeypatch.setattr(scheduled_command, "run_produce_once", produce)
    restarts, readers = [], []
    command = _command(store, produce=None, restarts=restarts, readers=readers)

    first = command()
    second = command()
    assert first.summary == second.summary == "wechat produce-once queued=2"
    assert first.producer_counts == second.producer_counts == (("queued", 2),)
    assert first.complete and second.complete

    assert len(readers) == 1
    assert calls == [(store, readers[0], "a1", "self-1")] * 2
    assert _reader_health(store)["state"] == "healthy"
    assert _error_kinds(store) == [] and restarts == []


def test_reader_ipc_failures_request_one_restart_after_three_passes(tmp_path, monkeypatch):
    store = _store(tmp_path)
    outcomes = [ReaderIpcError("helper timeout", code="reader_timeout")] * 4 + [1] + [
        ReaderIpcError("helper timeout", code="reader_timeout")
    ] * 3

    def produce(*_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(scheduled_command, "run_produce_once", produce)
    restarts, readers = [], []
    command = _command(store, produce=None, restarts=restarts, readers=readers)

    first, second, third, fourth = (command() for _ in range(4))

    assert {result.summary for result in (first, second, third, fourth)} == {
        "wechat produce-once reader unavailable: helper timeout"
    }
    assert restarts == ["restart"]
    assert _error_kinds(store) == [WECHAT_READER_UNAVAILABLE]
    assert _reader_health(store)["state"] == "degraded"

    successful = command()
    assert successful.summary == "wechat produce-once queued=1"
    assert successful.producer_counts == (("queued", 1),)
    assert successful.complete
    assert _reader_health(store)["state"] == "healthy"

    for _ in range(3):
        command()
    assert restarts == ["restart", "restart"]
    assert _error_kinds(store) == [WECHAT_READER_UNAVAILABLE] * 2


@pytest.mark.parametrize(
    "failure",
    (
        ReaderIpcError("app data denied", code="permission_required"),
        OSError(errno.EACCES, "denied"),
    ),
)
def test_permission_problems_are_reported_once_and_do_not_fail_the_trigger(
    tmp_path, monkeypatch, failure
):
    store = _store(tmp_path)

    def produce(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(scheduled_command, "run_produce_once", produce)
    restarts, readers = [], []
    command = _command(store, produce=None, restarts=restarts, readers=readers)

    first = command()
    second = command()
    assert first.summary == second.summary == "wechat produce-once paused: data permission required"
    assert first.producer_counts is second.producer_counts is None
    assert not first.complete and not second.complete

    assert _error_kinds(store) == [WECHAT_DATA_PERMISSION_REQUIRED]
    assert restarts == []


def test_unexpected_errors_are_real_command_failures(tmp_path, monkeypatch):
    store = _store(tmp_path)

    def produce(*_args, **_kwargs):
        raise RuntimeError("producer bug")

    monkeypatch.setattr(scheduled_command, "run_produce_once", produce)

    with pytest.raises(RuntimeError, match="producer bug"):
        _command(store, produce=None, restarts=[], readers=[])()
    assert _error_kinds(store) == []
