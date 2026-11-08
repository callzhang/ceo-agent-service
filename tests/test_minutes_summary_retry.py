from __future__ import annotations

import json

import pytest

from app.dws_client import DwsClient, DwsError
from app.minutes_sync import (
    MINUTES_SYNC_SCANNER,
    archive_transcription_recovery,
    sync_minutes_once,
)
from app.store import AutoReplyStore


FAILED = {
    "textGenerationInfo": {
        "state": "FAILED",
        "reason": "DEADLINE_EXCEEDED",
        "terminal": True,
    }
}


class Source:
    def __init__(self, *outcomes, paragraphs=None):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.paragraphs = paragraphs or []

    def list_minutes(self):
        return [{"taskUuid": "test-uuid"}]

    def parse_minutes_list(self, payload):
        return payload

    def get_minutes_info(self, task_uuid):
        return {"result": {"title": "Test", "startTime": 1700000000000}}

    def get_minutes_summary(self, task_uuid, *, timeout_seconds=None):
        self.calls.append(timeout_seconds)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return {"result": outcome}

    def get_all_minutes_transcription(self, task_uuid):
        return {"paragraphs": self.paragraphs}


def run_case(tmp_path, source):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    result = sync_minutes_once(store, source, archive_dir=tmp_path / "archive")
    return store, result


def test_retry_terminal_failure_then_archive_success(tmp_path):
    source = Source(
        FAILED, {"fullSummary": "recovered"}, paragraphs=[{"paragraph": "official"}]
    )
    store, result = run_case(tmp_path, source)
    assert source.calls == [None, 300]
    assert result.synced == 1 and result.failed == 0
    assert result.summary_recovery_ids == ()
    assert "recovered" in next((tmp_path / "archive").rglob("*.md")).read_text()


def test_terminal_failure_twice_creates_empty_official_transcript_archive_for_recovery(
    tmp_path,
):
    source = Source(FAILED, FAILED)
    store, result = run_case(tmp_path, source)
    assert source.calls == [None, 300]
    assert result.failed == 1 and result.summary_recovery_ids == ("test-uuid",)
    assert "DEADLINE_EXCEEDED" in result.failures[0][1]
    [path] = (tmp_path / "archive").rglob("*.md")
    original = path.read_text()
    assert "DEADLINE_EXCEEDED" in original and "# Transcript" in original
    cursor = json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])
    assert "test-uuid" not in cursor["archived_ids"]
    archive_transcription_recovery(
        store,
        tmp_path / "archive",
        task_uuid="test-uuid",
        supplement="completed ASR",
        apply=True,
    )
    assert original.rstrip() in path.read_text() and "completed ASR" in path.read_text()


def test_transient_summary_error_retries_once_then_asr_eligible(tmp_path):
    first = DwsError("first timeout", retryable_external_dependency=True)
    second = DwsError("second timeout", retryable_external_dependency=True)
    source = Source(first, second)
    _, result = run_case(tmp_path, source)
    assert source.calls == [None, 300]
    assert result.failed == 1 and result.summary_recovery_ids == ("test-uuid",)
    assert (
        "first timeout" in result.failures[0][1]
        and "second timeout" in result.failures[0][1]
    )
    assert list((tmp_path / "archive").rglob("*.md"))


@pytest.mark.parametrize(
    "second", [DwsError("not_authenticated", code="2"), {"unknown": "shape"}]
)
def test_second_auth_or_unknown_shape_never_enters_asr_queue(tmp_path, second):
    source = Source(FAILED, second)
    _, result = run_case(tmp_path, source)
    assert source.calls == [None, 300]
    assert result.summary_recovery_ids == ()


@pytest.mark.parametrize(
    "first", [DwsError("not_authenticated", code="2"), {"unknown": "shape"}]
)
def test_first_auth_or_unknown_shape_never_retries(tmp_path, first):
    source = Source(first)
    _, result = run_case(tmp_path, source)
    assert source.calls == [None]
    assert result.summary_recovery_ids == ()


def test_summary_timeout_override_is_forwarded(monkeypatch):
    client = DwsClient()
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return {"result": {}}

    monkeypatch.setattr(client, "run_json", run)
    client.get_minutes_summary("u")
    client.get_minutes_summary("u", timeout_seconds=300)
    assert calls == [{}, {"timeout_seconds": 300}]


def test_unknown_retry_preserves_first_failure_diagnostics(tmp_path):
    source = Source(FAILED, {"unexpected": "shape"})
    _, result = run_case(tmp_path, source)
    assert result.failed == 1 and result.summary_recovery_ids == ()
    assert "DEADLINE_EXCEEDED" in result.failures[0][1]
    assert "unexpected" in result.failures[0][1]


def test_successful_scan_preserves_extension_and_clears_old_pagination_error(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at="old",
        cursor_json=json.dumps(
            {
                "archived_ids": [],
                "permission_pending_ids": [],
                "summary_recoveries": {"u": {"status": "running"}},
                "pagination_deferred": True,
                "pagination_error": "old failure",
            }
        ),
        last_error="old failure",
    )
    source = Source({"fullSummary": "ok"})
    sync_minutes_once(store, source, archive_dir=tmp_path / "archive")
    cursor = json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])
    assert cursor["summary_recoveries"] == {"u": {"status": "running"}}
    assert "pagination_deferred" not in cursor and "pagination_error" not in cursor


@pytest.mark.parametrize(
    "error",
    [
        DwsError("not_authenticated", code="2"),
        DwsError("forbidden", code="PAT_HIGH_RISK_NO_PERMISSION"),
    ],
)
def test_transcript_credential_failure_does_not_become_asr_recovery(tmp_path, error):
    class AuthSource(Source):
        def get_all_minutes_transcription(self, task_uuid):
            raise error

    source = AuthSource(FAILED, FAILED)
    _, result = run_case(tmp_path, source)
    assert result.failed == 1 and result.summary_recovery_ids == ()
    assert "DEADLINE_EXCEEDED" in result.failures[0][1]
    assert (
        "not_authenticated" in result.failures[0][1]
        or "forbidden" in result.failures[0][1]
    )
    assert not list((tmp_path / "archive").rglob("*.md"))


def test_transcript_resource_permission_wait_preserves_summary_failure(tmp_path):
    class RestrictedSource(Source):
        def get_minutes_info(self, task_uuid):
            return {
                "result": {
                    "title": "Test",
                    "startTime": 1700000000000,
                    "duration": 600000,
                }
            }

        def get_all_minutes_transcription(self, task_uuid):
            raise DwsError(
                "restricted", server_key="minutes", business_message="no permission"
            )

    source = RestrictedSource(FAILED, FAILED)
    _, result = run_case(tmp_path, source)
    assert result.permission_pending == 1 and result.summary_recovery_ids == ()
    assert "DEADLINE_EXCEEDED" in result.failures[0][1]
    assert not list((tmp_path / "archive").rglob("*.md"))
