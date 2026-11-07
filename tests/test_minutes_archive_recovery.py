from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.minutes_sync import MINUTES_SYNC_SCANNER
from app.store import AutoReplyStore


def fixture(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"synthetic audio")
    archive = tmp_path / "archive"
    archive.mkdir()
    original = "<!-- row_key: test-uuid -->\n\n# AI Summary\n\n> Summary unavailable: FAILED\n\n# Transcript\n\n[00:00] official words\n"
    path = archive / "original.md"
    path.write_text(original)
    packet = {
        "task_uuid": "test-uuid",
        "asr_job_id": "job-1",
        "audio_duration_seconds": 10.0,
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "derived_summary": "Derived synthetic summary",
        "asr_result": {
            "status": "done",
            "model": "qwen3-asr",
            "text": "synthetic transcript",
            "segments": [{"start": 0.0, "end": 4.0, "text": "synthetic transcript"}],
        },
    }
    receipt = {
        "ok": True,
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "results": [
            {
                "taskUuid": "test-uuid",
                "path": audio.name,
                "sizeBytes": audio.stat().st_size,
            }
        ],
    }
    return store, audio, archive, path, original, packet, receipt


def test_preview_and_apply_preserve_official_artifacts_and_cursor(tmp_path):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at="old",
        cursor_json=json.dumps(
            {"archived_ids": ["prior"], "permission_pending_ids": [], "other": "keep"}
        ),
        last_error="prior error",
    )
    recover_archive(store, archive, packet, audio, receipt, apply=False)
    assert path.read_text() == original
    assert json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])[
        "archived_ids"
    ] == ["prior"]
    recover_archive(store, archive, packet, audio, receipt, apply=True)
    text = path.read_text()
    assert original in text
    assert (
        "Derived synthetic summary" in text
        and "synthetic transcript" in text
        and "ai-services" in text
    )
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER)
    cursor = json.loads(state["cursor_json"])
    assert (
        cursor["archived_ids"] == ["prior", "test-uuid"] and cursor["other"] == "keep"
    )
    assert state["last_success_at"] == "old" and state["last_error"] == "prior error"


@pytest.mark.parametrize(
    "mutation",
    ["pending", "empty", "wrong_uuid", "wrong_hash", "out_of_range", "wrong_size"],
)
def test_invalid_evidence_is_rejected_without_write(tmp_path, mutation):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    if mutation == "pending":
        packet["asr_result"]["status"] = "running"
    if mutation == "empty":
        packet["asr_result"]["text"] = ""
    if mutation == "wrong_uuid":
        receipt["results"][0]["taskUuid"] = "different"
    if mutation == "wrong_hash":
        packet["audio_sha256"] = "0" * 64
    if mutation == "out_of_range":
        packet["asr_result"]["segments"][0]["end"] = 11
    if mutation == "wrong_size":
        receipt["results"][0]["sizeBytes"] += 1
    with pytest.raises(ValueError):
        recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert path.read_text() == original
    assert store.get_daily_scan_state(MINUTES_SYNC_SCANNER) is None


def test_write_failure_never_marks_cursor(tmp_path, monkeypatch):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError):
        recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert store.get_daily_scan_state(MINUTES_SYNC_SCANNER) is None


def test_retry_after_cursor_failure_does_not_duplicate_content(tmp_path, monkeypatch):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    real_set = store.set_daily_scan_state

    def fail(*args, **kwargs):
        raise OSError("cursor failed")

    monkeypatch.setattr(store, "set_daily_scan_state", fail)
    with pytest.raises(OSError):
        recover_archive(store, archive, packet, audio, receipt, apply=True)
    monkeypatch.setattr(store, "set_daily_scan_state", real_set)
    recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert path.read_text().count("<!-- ai-services-asr-recovery -->") == 1
    assert json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])[
        "archived_ids"
    ] == ["test-uuid"]


def test_preview_cli_does_not_create_database(tmp_path):
    import subprocess
    import sys

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    packet_file = tmp_path / "packet.json"
    packet_file.write_text(json.dumps(packet))
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt))
    absent_db = tmp_path / "missing" / "db.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.minutes_archive_recovery",
            "--db",
            str(absent_db),
            "--archive-dir",
            str(archive),
            "--input-json",
            str(packet_file),
            "--audio-file",
            str(audio),
            "--download-receipt",
            str(receipt_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not absent_db.parent.exists()
    assert path.read_text() == original


def test_conflicting_completed_asr_job_id_is_rejected(tmp_path):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    packet["asr_result"]["job_id"] = "different-job"
    with pytest.raises(ValueError):
        recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert path.read_text() == original
    assert store.get_daily_scan_state(MINUTES_SYNC_SCANNER) is None


def test_zero_length_segment_is_rejected(tmp_path):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    packet["asr_result"]["segments"][0]["end"] = 0
    with pytest.raises(ValueError):
        recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert path.read_text() == original


@pytest.mark.parametrize("database_alias", [False, True])
def test_recovery_waits_for_running_minutes_scan(tmp_path, database_alias):
    import threading
    from app.minutes_archive_recovery import recover_archive
    from app.minutes_sync import sync_minutes_once
    from tests.test_minutes_sync import FakeDws

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    recovery_store = store
    if database_alias:
        alias = tmp_path / "alias.sqlite3"
        alias.symlink_to(store.path)
        recovery_store = AutoReplyStore(alias)
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    errors = []

    class BlockingDws(FakeDws):
        def list_minutes(self):
            entered.set()
            assert release.wait(5)
            return []

    def scan():
        try:
            sync_minutes_once(store, BlockingDws([]), archive_dir=archive)
        except Exception as exc:
            errors.append(exc)

    def recover():
        try:
            recover_archive(recovery_store, archive, packet, audio, receipt, apply=True)
            completed.set()
        except Exception as exc:
            errors.append(exc)

    scan_thread = threading.Thread(target=scan)
    scan_thread.start()
    assert entered.wait(5)
    recovery_thread = threading.Thread(target=recover)
    recovery_thread.start()
    try:
        assert not completed.wait(0.3)
        assert path.read_text() == original
    finally:
        release.set()
        scan_thread.join(5)
        recovery_thread.join(5)
    assert not errors and completed.is_set()
    assert json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])[
        "archived_ids"
    ] == ["test-uuid"]


def test_recovery_preserves_full_asr_text_alongside_segments(tmp_path):
    from app.minutes_archive_recovery import recover_archive

    store, audio, archive, path, original, packet, receipt = fixture(tmp_path)
    packet["asr_result"]["text"] = "complete synthetic text with punctuation"
    recover_archive(store, archive, packet, audio, receipt, apply=True)
    assert "complete synthetic text with punctuation" in path.read_text()
    assert "synthetic transcript" in path.read_text()
