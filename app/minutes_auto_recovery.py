"""Automatic Minutes ASR recovery through existing native Workbench turns."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from app.minutes_archive_recovery import recover_archive
from app.minutes_recovery_agent import build_recovery_prompt, read_completed_asr
from app.minutes_sync import MINUTES_SYNC_SCANNER, MinutesSyncResult, _minutes_sync_lock
from app.workbench.store import WorkbenchStore


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _cursor(store) -> tuple[dict, dict]:
    state = store.get_daily_scan_state(MINUTES_SYNC_SCANNER) or {}
    return state, json.loads(state.get("cursor_json") or "{}")


def _save(store, state: dict, cursor: dict) -> None:
    store.set_daily_scan_state(
        MINUTES_SYNC_SCANNER,
        last_success_at=state.get("last_success_at") or "",
        cursor_json=json.dumps(cursor, ensure_ascii=False),
        last_error=state.get("last_error") or "",
    )


def prepare_audio(directory: Path, task_uuid: str, dws) -> None:
    """Download one original recording, retaining the actual provider receipt."""
    audio = directory / "original-media"
    receipt_path = directory / "download-receipt.json"
    if receipt_path.exists():
        receipt = _read(receipt_path)
    else:
        # A download is a read. An interrupted unreceipted local partial can
        # be replaced; a receipted download is reused after a probe restart.
        audio.unlink(missing_ok=True)
        receipt = dws.run_json(
            [
                dws.dws_bin,
                "minutes",
                "+download",
                "--id",
                task_uuid,
                "--output",
                "original-media",
                "--format",
                "json",
            ],
            cwd=directory,
            timeout_seconds=180,
        )
        _write(receipt_path, receipt)
    duration = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(audio),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    with audio.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _write(directory / "download-receipt.json", receipt)
    _write(
        directory / "metadata.json",
        {
            "task_uuid": task_uuid,
            "audio_sha256": digest,
            "audio_duration_seconds": float(
                json.loads(duration.stdout)["format"]["duration"]
            ),
            "python_executable": sys.executable,
        },
    )


def upload_audio(work_dir: Path, upload_url: str) -> dict:
    """Upload original bytes; checkpoint the returned job before exposing its ID."""
    metadata = _read(work_dir / "metadata.json")
    audio = work_dir / "original-media"
    with audio.open("rb") as stream:
        if (
            hashlib.file_digest(stream, "sha256").hexdigest()
            != metadata["audio_sha256"]
        ):
            raise ValueError("original audio digest changed")
        stream.seek(0)
        with httpx.Client(
            timeout=180, headers={"User-Agent": "ceo-agent-service"}
        ) as client:
            response = client.post(
                upload_url, files={"file": ("original-media.mp3", stream, "audio/mpeg")}
            )
            response.raise_for_status()
            result = response.json()
    if not isinstance(result.get("job_id"), str) or not result["job_id"]:
        raise ValueError("ASR upload did not return a job identity")
    _write(work_dir / "upload-result.json", result)
    return result


def _ensure_turn(
    store, wb, dws, task_uuid: str, service_root: Path, temp_root: Path | None
) -> dict:
    # Serialize creation with ordinary sync, but never hold the mutex while
    # waiting for the background Agent or while publishing its archive.
    with _minutes_sync_lock(store):
        state, cursor = _cursor(store)
        recoveries = cursor.setdefault("summary_recoveries", {})
        recovery = recoveries.get(task_uuid)
        if recovery is None:
            directory = Path(tempfile.mkdtemp(prefix="ceo-minutes-asr-", dir=temp_root))
            recovery = {"work_dir": str(directory), "attempt": 1}
            recoveries[task_uuid] = recovery
            _save(store, state, cursor)
        directory = Path(recovery["work_dir"])
        if recovery.get("turn_id"):
            turn = wb.get_turn(recovery["turn_id"])
            if turn is None:
                raise ValueError("recovery turn is missing")
            if turn.status == "stopped":
                raise RuntimeError("Minutes recovery was explicitly stopped")
            if turn.status != "failed" and not recovery.get("retry_needed"):
                return dict(recovery)
            if recovery["attempt"] >= MAX_AUTOMATIC_ATTEMPTS:
                raise RuntimeError(
                    f"Minutes recovery attempts exhausted: {turn.error_code}"
                )
            recovery["attempt"] += 1
            recovery.pop("retry_needed", None)
            recovery.pop("turn_id")
            _save(store, state, cursor)
        if not (directory / "metadata.json").exists():
            prepare_audio(directory, task_uuid, dws)
        if not recovery.get("task_id"):
            recovery["task_id"] = wb.create_task(
                title=f"Minutes supplementary archive: {task_uuid}",
                runtime_kind="codex",
            ).id
            _save(store, state, cursor)
        wb.save_attachment(
            recovery["task_id"],
            client_request_id=str(
                uuid5(NAMESPACE_URL, f"minutes-recovery:{task_uuid}:metadata")
            ),
            filename="minutes-recovery.json",
            media_type="application/json",
            content=(directory / "metadata.json").read_bytes(),
        )
        turn = wb.create_turn(
            recovery["task_id"],
            user_text=build_recovery_prompt(directory, service_root),
            client_request_id=f"minutes-asr:{task_uuid}:{recovery['attempt']}",
        )
        recovery["turn_id"] = turn.id
        recovery.setdefault("turn_ids", []).append(turn.id)
        _save(store, state, cursor)
        return dict(recovery)


def _cleanup_archived(store) -> None:
    with _minutes_sync_lock(store):
        state, cursor = _cursor(store)
        for task_uuid, recovery in cursor.get("summary_recoveries", {}).items():
            if task_uuid in cursor.get("archived_ids", []) and not recovery.get(
                "cleaned"
            ):
                directory = Path(recovery["work_dir"])
                if directory.exists():
                    # These directories were created by this coordinator and
                    # are kept only until the verified archive is published.
                    shutil.rmtree(directory)
                recovery["cleaned"] = True
        _save(store, state, cursor)


def _finish(store, wb, archive_dir: Path, recovery: dict) -> None:
    directory = Path(recovery["work_dir"])
    metadata = _read(directory / "metadata.json")
    upload_path = directory / "upload-result.json"
    if not upload_path.exists():
        raise ValueError(
            f"native Agent completed without an ASR upload receipt; turn={recovery['turn_id']}"
        )
    upload = _read(upload_path)
    completed_path = directory / "asr-complete.json"
    # A file written by an Agent is not an ASR receipt. Reconstruct the actual
    # native call from the recorded turns, including a previous failed turn
    # whose ASR completed before its summary step failed.
    asr = None
    for turn_id in recovery["turn_ids"]:
        asr = read_completed_asr(wb, turn_id, upload["job_id"])
        if asr is not None:
            break
    if asr is None:
        raise ValueError("completed native ASR result not found")
    _write(completed_path, asr)
    turn = wb.get_turn(recovery["turn_id"])
    if turn is None or turn.status != "completed" or not turn.final_text.strip():
        raise ValueError("supplementary summary has not completed")
    packet = {
        **{
            key: metadata[key]
            for key in ("task_uuid", "audio_sha256", "audio_duration_seconds")
        },
        "asr_job_id": upload["job_id"],
        "asr_result": _read(completed_path),
        "derived_summary": turn.final_text,
    }
    recover_archive(
        store,
        archive_dir,
        packet,
        directory / "original-media",
        _read(directory / "download-receipt.json"),
        apply=True,
    )
    _cleanup_archived(store)


def _recover_summaries(
    store,
    dws,
    *,
    archive_dir: Path,
    service_root: Path,
    result: MinutesSyncResult,
    workbench=None,
    wait_seconds: float = 900,
    temp_root: Path | None = None,
) -> MinutesSyncResult:
    """Resume native work and count only a verified, published archive as success."""
    _cleanup_archived(store)
    if not result.summary_recovery_ids:
        return result
    wb = workbench if workbench is not None else WorkbenchStore(store.path)
    _, cursor = _cursor(store)
    recovered = set(result.summary_recovery_ids) & set(cursor.get("archived_ids", []))
    errors = {}
    deadline = time.monotonic() + wait_seconds
    for task_uuid in result.summary_recovery_ids:
        if task_uuid in recovered:
            continue
        try:
            recovery = _ensure_turn(store, wb, dws, task_uuid, service_root, temp_root)
            while True:
                turn = wb.get_turn(recovery["turn_id"])
                if turn is None:
                    raise ValueError("recovery turn is missing")
                if turn.status == "completed":
                    _finish(store, wb, archive_dir, recovery)
                    recovered.add(task_uuid)
                    break
                if turn.status in {"failed", "stopped"}:
                    directory = Path(recovery["work_dir"])
                    if (directory / "upload-result.json").exists():
                        job = _read(directory / "upload-result.json")["job_id"]
                        asr = read_completed_asr(wb, turn.id, job)
                        if asr is not None:
                            _write(directory / "asr-complete.json", asr)
                    raise RuntimeError(
                        f"Minutes recovery {turn.status}: {turn.error_code}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Minutes recovery still {turn.status}; turn={turn.id}"
                    )
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            httpx.HTTPError,
        ) as exc:
            # Preserve the real source failure alongside the recovery failure;
            # the same native turn/checkpoint is resumed on the next sync.
            errors[task_uuid] = str(exc)
            with _minutes_sync_lock(store):
                state, cursor = _cursor(store)
                entry = cursor.get("summary_recoveries", {}).get(task_uuid)
                if entry is not None:
                    entry["last_error"] = str(exc)
                    turn = (
                        wb.get_turn(entry["turn_id"]) if entry.get("turn_id") else None
                    )
                    if (
                        turn is not None
                        and turn.status == "completed"
                        and isinstance(exc, ValueError)
                    ):
                        entry["retry_needed"] = True
                    if turn is not None and (
                        turn.status == "stopped"
                        or (
                            entry["attempt"] >= MAX_AUTOMATIC_ATTEMPTS
                            and (turn.status == "failed" or entry.get("retry_needed"))
                        )
                    ):
                        directory = Path(entry["work_dir"])
                        if directory.exists():
                            shutil.rmtree(directory)
                        entry["cleaned"] = True
                    _save(store, state, cursor)
    return replace(
        result,
        failed=result.failed - len(recovered),
        synced=result.synced + len(recovered),
        failures=tuple(
            (key, f"{reason}; ASR recovery: {errors[key]}" if key in errors else reason)
            for key, reason in result.failures
            if key not in recovered
        ),
        summary_recovery_ids=tuple(
            key for key in result.summary_recovery_ids if key not in recovered
        ),
    )


def recover_summaries(
    store,
    dws,
    *,
    archive_dir: Path,
    service_root: Path,
    result: MinutesSyncResult,
    workbench=None,
    wait_seconds: float = 900,
    temp_root: Path | None = None,
) -> MinutesSyncResult:
    # Recovery callers share checkpoints and cleanup. Serialize them while
    # leaving the ordinary Minutes mutex free for the independent Agent.
    lock_path = store.path.resolve().with_name(
        f".{store.path.name}.minutes-asr-recovery.lock"
    )
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _recover_summaries(
            store,
            dws,
            archive_dir=archive_dir,
            service_root=service_root,
            result=result,
            workbench=workbench,
            wait_seconds=wait_seconds,
            temp_root=temp_root,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["upload"])
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--upload-url", required=True)
    args = parser.parse_args()
    print(json.dumps(upload_audio(args.work_dir, args.upload_url)))


if __name__ == "__main__":
    main()
