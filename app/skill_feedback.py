from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

from app.agent_skill_usage import (
    LoadedSkillReceipt,
    resolve_authorized_skill_path,
)


class SkillFeedbackUpdateError(ValueError):
    """The requested Skill update cannot be applied safely."""


_READ_SKILL_COMPLETED = re.compile(r"^item\.completed$")
_MAX_RULE_LENGTH = 1200
_RULE_MARKER = "## Feedback-derived policy rules"


def skill_paths_from_events(
    events_json: str,
    *,
    allow_existing_attempt_id: int = 0,
) -> tuple[Path, ...]:
    try:
        events = json.loads(events_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(events, list):
        return ()
    paths: set[Path] = set()
    for event in events:
        if not isinstance(event, dict) or not _READ_SKILL_COMPLETED.match(
            str(event.get("type") or "")
        ):
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("tool") != "read_skill":
            continue
        if item.get("status") != "completed":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        raw_path = metadata.get("skill_path")
        raw_digest = metadata.get("skill_sha256")
        raw_name = metadata.get("skill_name")
        if (
            not isinstance(raw_path, str)
            or not raw_path.strip()
            or not isinstance(raw_digest, str)
            or not isinstance(raw_name, str)
        ):
            continue
        try:
            resolved = resolve_authorized_skill_path(raw_path).path
            if resolved.parent.name != raw_name:
                continue
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != raw_digest:
                if (
                    allow_existing_attempt_id <= 0
                    or f"Attempt #{allow_existing_attempt_id}:" not in resolved.read_text(
                        encoding="utf-8"
                    )
                ):
                    continue
            paths.add(resolved)
        except Exception:  # noqa: BLE001 - invalid receipts are ignored
            continue
    return tuple(sorted(paths))


def apply_skill_feedback_update(
    *,
    events_json: str,
    feedback: str,
    source_attempt_id: int,
) -> tuple[LoadedSkillReceipt, ...]:
    """Apply one confirmed policy rule and return fresh content receipts.

    Only paths previously read successfully by the Agent are eligible.  The
    update is deterministic and idempotent: the same attempt/rule is appended
    at most once, and the resulting SHA-256 is the receipt consumed by the next
    generation's Skill reread gate.
    """
    rule = " ".join(feedback.split())[:_MAX_RULE_LENGTH].strip()
    if not rule:
        raise SkillFeedbackUpdateError("skill update requires non-empty feedback")
    paths = skill_paths_from_events(
        events_json,
        allow_existing_attempt_id=source_attempt_id,
    )
    if not paths:
        raise SkillFeedbackUpdateError("skill update could not identify a reviewed Skill")
    receipts: list[LoadedSkillReceipt] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillFeedbackUpdateError(f"skill update read failed: {path}") from exc
        entry = f"\n- Attempt #{int(source_attempt_id)}: {rule}\n"
        if _RULE_MARKER not in content:
            content = content.rstrip() + f"\n\n{_RULE_MARKER}\n" + entry
        elif entry not in content:
            content = content.rstrip() + entry
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise SkillFeedbackUpdateError(f"skill update write failed: {path}") from exc
        receipts.append(
            LoadedSkillReceipt(
                name=path.parent.name,
                path=str(path.resolve()),
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(receipts)
