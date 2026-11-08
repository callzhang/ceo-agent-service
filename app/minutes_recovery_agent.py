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
    for event in workbench_store.events_after(turn_id):
        if event.event_type != "tool_completed":
            continue
        payload = event.payload
        if (
            payload.get("kind") != "mcp"
            or payload.get("server") != "ai_services"
            or payload.get("tool") != "get_audio_transcription"
            or payload.get("status") != "completed"
        ):
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("native ASR call arguments unavailable")
        if arguments.get("job_id") != expected_job_id:
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
        return data
    return None
