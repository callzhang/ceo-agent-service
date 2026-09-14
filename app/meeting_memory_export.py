"""Build one durable-memory record for each delivered meeting conclusion.

The meeting alignment job is the authoritative lifecycle record: it holds the
delivered conclusion and the structured management viewpoint.  A locally
archived DingTalk minute can add the provider summary, but it never creates a
second record for the same meeting.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MEMORY_EXPORT_DIRECTORY = ".memory"
MEETING_MEMORY_EXPORT_FILENAME = "meeting-alignment.jsonl"
_ROW_KEY = re.compile(r"<!--\s*row_key:\s*(\S+)\s*-->")
_AI_SUMMARY = re.compile(r"^# AI Summary\s*\n(.*?)(?=^# |\Z)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class MeetingMemoryExportResult:
    records: int
    summaries_attached: int
    output_path: Path


def export_sent_meeting_memory(
    jobs: Iterable[object],
    *,
    archive_dir: Path,
    output_path: Path,
) -> MeetingMemoryExportResult:
    """Write the current one-record-per-meeting memory-import projection.

    Rewriting the projection is intentional.  The stable record key makes the
    downstream memory writer idempotent while allowing a repaired conclusion
    to replace an earlier export before it is imported.
    """
    summaries = _archive_summaries_by_meeting_id(archive_dir)
    records: list[dict[str, object]] = []
    summaries_attached = 0
    for job in sorted(jobs, key=lambda item: (str(item.ended_at), int(item.id))):
        if str(job.status) != "sent":
            continue
        summary = summaries.get(str(job.meeting_id), "")
        if summary:
            summaries_attached += 1
        records.append(_record_for_job(job, summary=summary))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, output_path)
    return MeetingMemoryExportResult(
        records=len(records),
        summaries_attached=summaries_attached,
        output_path=output_path,
    )


def meeting_memory_export_path(workspace: Path) -> Path:
    return workspace / "AI听记" / MEMORY_EXPORT_DIRECTORY / MEETING_MEMORY_EXPORT_FILENAME


def _record_for_job(job: object, *, summary: str) -> dict[str, object]:
    source = _json_object(str(job.source_json))
    decision = _json_object(str(job.decision_json))
    participants = _participants(source)
    target = _target_label(decision)
    body_parts = [
        f"[会议: {job.meeting_id}] [结束: {job.ended_at}]"
        f" [参会人: {', '.join(participants) or '未记录'}]"
        f" [结论送达: {target or '未记录'}]",
        f"会议《{str(job.title).strip() or '未命名会议'}》的已送达管理结论：",
        str(job.final_message).strip(),
    ]
    if summary:
        body_parts.extend(("AI 听记摘要：", summary))
    viewpoint = decision.get("derek_viewpoint")
    if isinstance(viewpoint, dict):
        text = str(viewpoint.get("expressed_view") or "").strip()
        if text:
            body_parts.extend(("管理者观点：", text))
    raw_questions = decision.get("key_questions")
    open_questions = [
        str(item).strip()
        for item in (raw_questions if isinstance(raw_questions, list) else [])
        if str(item).strip()
    ]
    if open_questions:
        body_parts.extend(("待继续澄清：", "\n".join(f"- {item}" for item in open_questions)))
    return {
        "key": f"meeting-alignment:{job.meeting_id}",
        "arguments": {
            "data": "\n\n".join(body_parts),
            "type": "text",
            "created_at": str(job.ended_at),
            "source_description": (
                f"delivered meeting alignment {str(job.ended_at)[:16]} "
                f"{str(job.title).strip() or 'untitled'}"
            ),
            "entity_type": "Meeting",
            "entity_attributes": {
                "name": str(job.meeting_id),
                "entity_type": "Meeting",
                "type": "Meeting",
                "meeting_id": str(job.meeting_id),
                "title": str(job.title).strip(),
                "participants": participants,
                "delivery_target": target,
            },
        },
    }


def _archive_summaries_by_meeting_id(archive_dir: Path) -> dict[str, str]:
    if not archive_dir.is_dir():
        return {}
    summaries: dict[str, str] = {}
    for path in sorted(archive_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        row_key = _ROW_KEY.search(text)
        summary = _AI_SUMMARY.search(text)
        if row_key is None or summary is None:
            continue
        value = summary.group(1).strip()
        if value:
            summaries.setdefault(row_key.group(1), value)
    return summaries


def _json_object(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _participants(source: dict[str, object]) -> list[str]:
    evidence = source.get("calendar_evidence")
    if not isinstance(evidence, dict):
        return []
    values = evidence.get("participants")
    if not isinstance(values, list):
        return []
    return [
        str(item.get("name") or "").strip()
        for item in values
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def _target_label(decision: dict[str, object]) -> str:
    target = decision.get("target")
    if not isinstance(target, dict):
        return ""
    title = str(target.get("title") or "").strip()
    kind = str(target.get("kind") or "").strip()
    return f"{kind}:{title}" if kind and title else title or kind
