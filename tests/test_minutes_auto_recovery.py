from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.minutes_sync import MINUTES_SYNC_SCANNER, MinutesSyncResult
from app.store import AutoReplyStore


def setup(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    archive = tmp_path / "archive"
    archive.mkdir()
    path = archive / "original.md"
    path.write_text(
        "<!-- row_key: source -->\n\n# AI Summary\n\n> Summary unavailable\n\n# Transcript\n\nOfficial words\n"
    )
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        cursor_json=json.dumps({"archived_ids": [], "other": "keep"}),
        last_success_at="old",
        last_error="",
    )
    result = MinutesSyncResult(
        discovered=1,
        failed=1,
        failures=(("source", "generation failed"),),
        summary_recovery_ids=("source",),
    )
    return store, archive, path, result


class Workbench:
    def __init__(self):
        self.tasks = []
        self.turns = []
        self.attachments = []
        self.status = "completed"

    def create_task(self, **kw):
        self.tasks.append(kw)
        return SimpleNamespace(id="10")

    def save_attachment(self, *args, **kw):
        self.attachments.append(kw)

    def create_turn(self, task_id, **kw):
        turn = SimpleNamespace(
            id="11",
            task_id=task_id,
            status=self.status,
            final_text="Supplementary summary",
            error_code="test failure",
        )
        self.turns.append(turn)
        return turn

    def get_turn(self, turn_id):
        return self.turns[-1]


def prepare(directory, task_uuid, dws):
    import hashlib

    audio = directory / "original-media"
    audio.write_bytes(b"actual test audio")
    metadata = {
        "task_uuid": task_uuid,
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "audio_duration_seconds": 10.0,
    }
    receipt = {
        "ok": True,
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "results": [
            {
                "taskUuid": task_uuid,
                "path": audio.name,
                "sizeBytes": audio.stat().st_size,
            }
        ],
    }
    (directory / "metadata.json").write_text(json.dumps(metadata))
    (directory / "download-receipt.json").write_text(json.dumps(receipt))
    (directory / "upload-result.json").write_text(json.dumps({"job_id": "native-job"}))


def asr(*args):
    return {
        "status": "done",
        "model": "test-asr",
        "text": "actual transcript",
        "segments": [{"start": 0, "end": 5, "text": "actual transcript"}],
    }


def test_automatic_pipeline_archives_and_cleans_only_after_native_completion(
    tmp_path, monkeypatch
):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", asr)
    recovered = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert recovered.failed == 0 and recovered.synced == 1
    assert (
        "Official words" in path.read_text()
        and "Supplementary summary" in path.read_text()
    )
    assert len(wb.tasks) == len(wb.turns) == len(wb.attachments) == 1
    cursor = json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])
    assert cursor["archived_ids"] == ["source"] and cursor["other"] == "keep"
    state = cursor["summary_recoveries"]["source"]
    assert state["turn_id"] == "11" and not Path(state["work_dir"]).exists()
    again = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=MinutesSyncResult(),
        workbench=wb,
        temp_root=tmp_path,
    )
    assert again.failed == 0 and len(wb.turns) == 1


def test_pending_is_not_success_and_next_pass_reuses_same_turn(tmp_path, monkeypatch):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    wb.status = "running"
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", asr)
    first = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        wait_seconds=0,
        temp_root=tmp_path,
    )
    assert first.failed == 1 and "Supplementary summary" not in path.read_text()
    wb.turns[-1].status = "completed"
    second = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert second.failed == 0 and len(wb.tasks) == len(wb.turns) == 1


@pytest.mark.parametrize("status,receipt", [("failed", asr()), ("completed", None)])
def test_failed_or_self_reported_completion_does_not_archive(
    tmp_path, monkeypatch, status, receipt
):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    wb.status = status
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", lambda *args: receipt)
    answer = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert answer.failed == 1 and "Supplementary summary" not in path.read_text()
    assert (
        json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])[
            "archived_ids"
        ]
        == []
    )


def test_noneligible_failure_never_starts_agent(tmp_path):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    result = replace(result, summary_recovery_ids=())
    assert (
        auto.recover_summaries(
            store,
            object(),
            archive_dir=archive,
            service_root=tmp_path,
            result=result,
            workbench=wb,
        )
        == result
    )
    assert wb.tasks == []


def test_service_command_runs_recovery_before_deciding_success(tmp_path, monkeypatch):
    from app import cli, minutes_auto_recovery, minutes_sync
    from app.cli import WorkerSettings

    _, _, _, failed = setup(tmp_path)
    monkeypatch.setattr(minutes_sync, "sync_minutes_once", lambda *args, **kw: failed)
    calls = []

    def recover(store, dws, **kw):
        calls.append(kw)
        return replace(failed, failed=0, synced=1, failures=(), summary_recovery_ids=())

    monkeypatch.setattr(minutes_auto_recovery, "recover_summaries", recover)
    actual = cli.sync_minutes_once_command(
        WorkerSettings(db_path=tmp_path / "db.sqlite3", workspace=tmp_path),
        return_result=True,
    )
    assert actual.failed == 0 and len(calls) == 1 and calls[0]["result"] is failed


def test_audio_prepare_reuses_download_receipt_after_interrupted_probe(
    tmp_path, monkeypatch
):
    from app import minutes_auto_recovery as auto

    audio = tmp_path / "original-media"
    audio.write_bytes(b"previous download")
    receipt = {
        "ok": True,
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "results": [
            {
                "taskUuid": "source",
                "path": audio.name,
                "sizeBytes": audio.stat().st_size,
            }
        ],
    }
    (tmp_path / "download-receipt.json").write_text(json.dumps(receipt))

    class Dws:
        dws_bin = "dws"

        def run_json(self, *args, **kw):
            pytest.fail("completed original download must not be repeated")

    monkeypatch.setattr(
        auto.subprocess,
        "run",
        lambda *args, **kw: SimpleNamespace(stdout='{"format":{"duration":"10"}}'),
    )
    auto.prepare_audio(tmp_path, "source", Dws())
    assert json.loads((tmp_path / "metadata.json").read_text())["task_uuid"] == "source"


def test_concurrent_recovery_resumes_one_native_turn_and_both_report_actual_archive(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", asr)

    def run():
        return auto.recover_summaries(
            store,
            object(),
            archive_dir=archive,
            service_root=tmp_path,
            result=result,
            workbench=wb,
            temp_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert all(answer.failed == 0 for answer in results)
    assert len(wb.tasks) == len(wb.turns) == 1
    assert path.read_text().count("<!-- ai-services-asr-recovery -->") == 1


def test_agent_completed_without_asr_can_resume_in_new_native_turn(
    tmp_path, monkeypatch
):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", lambda *args: None)
    first = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert first.failed == 1
    monkeypatch.setattr(auto, "read_completed_asr", asr)
    second = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert second.failed == 0 and len(wb.turns) == 2


def test_recovery_budget_is_persisted_and_expired_audio_is_cleaned(
    tmp_path, monkeypatch
):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    wb.status = "failed"
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", asr)
    for _ in range(auto.MAX_AUTOMATIC_ATTEMPTS + 1):
        actual = auto.recover_summaries(
            store,
            object(),
            archive_dir=archive,
            service_root=tmp_path,
            result=result,
            workbench=wb,
            temp_root=tmp_path,
        )
        assert actual.failed == 1
    assert len(wb.turns) == auto.MAX_AUTOMATIC_ATTEMPTS
    cursor = json.loads(store.get_daily_scan_state(MINUTES_SYNC_SCANNER)["cursor_json"])
    assert not Path(cursor["summary_recoveries"]["source"]["work_dir"]).exists()
    assert "Supplementary summary" not in path.read_text()


def test_archive_publication_error_retries_same_completed_turn(tmp_path, monkeypatch):
    from app import minutes_auto_recovery as auto

    store, archive, path, result = setup(tmp_path)
    wb = Workbench()
    monkeypatch.setattr(auto, "prepare_audio", prepare)
    monkeypatch.setattr(auto, "read_completed_asr", asr)
    publisher = auto.recover_archive

    def interrupted(*args, **kwargs):
        raise OSError("publication interrupted")

    monkeypatch.setattr(auto, "recover_archive", interrupted)
    first = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert first.failed == 1
    monkeypatch.setattr(auto, "recover_archive", publisher)
    second = auto.recover_summaries(
        store,
        object(),
        archive_dir=archive,
        service_root=tmp_path,
        result=result,
        workbench=wb,
        temp_root=tmp_path,
    )
    assert second.failed == 0 and len(wb.turns) == 1
