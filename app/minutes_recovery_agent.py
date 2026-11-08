"""Instructions and native ASR receipt reading for Minutes recovery."""

import json
import shlex
from pathlib import Path
from typing import Any


def build_recovery_prompt(work_dir: Path, service_root: Path) -> str:
    work = shlex.quote(str(work_dir))
    root = shlex.quote(str(service_root))
    return (
        f"Read {work}/metadata.json and {root}/docs/minutes-asr-recovery.md. "
        "Follow the recovery procedure and return the supplementary summary."
    )


def read_completed_asr(
    workbench_store: Any, turn_id: str, expected_job_id: str
) -> dict | None:
    for job_id, result in _completed_asr_results(
        workbench_store, turn_id, expected_job_id
    ):
        if job_id == expected_job_id:
            return result
    return None


def completed_asr_receipt(
    workbench_store: Any, turn_id: str, expected_job_ids: list[str]
) -> dict | None:
    for job_id in reversed(expected_job_ids):
        result = read_completed_asr(workbench_store, turn_id, job_id)
        if result is not None:
            return {"job_id": job_id, "result": result}
    return None


def _completed_asr_results(
    workbench_store: Any, turn_id: str, expected_job_id: str | None = None
):
    for event in _native_events(workbench_store, turn_id):
        if event.get("type") != "item.completed":
            continue
        payload = event.get("item", {})
        if (
            payload.get("type") != "mcp_tool_call"
            or payload.get("server") != "ai_services"
            or payload.get("tool") != "get_audio_transcription"
            or payload.get("status") not in (None, "completed")
        ):
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("native ASR call arguments unavailable")
        job_id = arguments.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("native ASR job identity unavailable")
        if expected_job_id is not None and job_id != expected_job_id:
            continue
        result = payload.get("result")
        if isinstance(result, str):
            result = {"content": [{"type": "text", "text": result}]}
        if not isinstance(result, dict):
            raise ValueError("native ASR result unavailable")
        if "Ok" in result:
            result = result["Ok"]
        if not isinstance(result, dict) or result.get("isError"):
            raise ValueError("native ASR call failed")
        data = result.get("structuredContent")
        if data is None:
            content = result.get("content")
            if (
                not isinstance(content, list)
                or len(content) != 1
                or not isinstance(content[0], dict)
                or content[0].get("type") != "text"
            ):
                raise ValueError("native ASR result shape is unknown")
            try:
                data = json.loads(content[0]["text"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("native ASR result is invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("native ASR result shape is unknown")
        if data.get("status") == "running":
            continue
        if (
            data.get("status") != "done"
            or not isinstance(data.get("text"), str)
            or not data["text"].strip()
            or not isinstance(data.get("segments"), list)
            or not data["segments"]
        ):
            raise ValueError("native ASR result is incomplete")
        yield job_id, data


def _native_events(workbench_store: Any, turn_id: str) -> list[dict]:
    from app.native_trajectory import read_run_events_from_attempts

    with workbench_store._connect() as db:
        workbench_store._require_turn(db, turn_id)
        attempts = db.execute(
            "select runtime_kind,session_id,transcript_reference,transcript_start,transcript_end "
            "from runtime_attempts where workload_kind='workbench' and workload_key=? "
            "and session_id<>'' order by id",
            (turn_id,),
        ).fetchall()
    if not attempts:
        return []
    return read_run_events_from_attempts({}, attempts)
