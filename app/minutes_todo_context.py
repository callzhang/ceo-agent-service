"""Where in the meeting each AI-minutes action item came from.

DingTalk leaves `executorList` empty on the action items it extracts from a
meeting, so the owner is not in the action item. It is in the conversation: each
action item carries `createdTime`, the millisecond offset in the recording at
which it was extracted, and the transcript gives every paragraph a speaker and a
start and end offset. This cuts the few paragraphs around that moment out of the
transcript, one line per sentence as "speaker：text", so an Agent can quote a
line that contains both the person and what was said (Derek 2026-09-25: the owner
information is in the original material; only the summary was being passed on).

A paragraph's speaker is whatever DingTalk labelled it; an unrecognised speaker
arrives as a generic "发言人 N" label. The excerpt keeps labels as they are and
leaves it to the reading Agent to decide who is being asked to do the work.
"""
from __future__ import annotations

from typing import Any

PARAGRAPHS_BEFORE = 6
PARAGRAPHS_AFTER = 4


def _millis(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _todo_entries(payload: Any) -> list[dict[str, Any]]:
    """The extracted action items with their titles and offsets."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("dingtalkTodoList")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    for key in ("result", "data"):
        found = _todo_entries(payload.get(key))
        if found:
            return found
    return []


def todo_transcript_excerpts(todos_payload: Any, paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One excerpt per action item whose position in the recording is known.

    An action item without a positive `createdTime`, or one past the end of the
    transcript, has no excerpt: its position is not known, and guessing one would
    put unrelated speech next to it.
    """
    timed = [
        (start, end, paragraph)
        for paragraph in paragraphs
        if (start := _millis(paragraph.get("startTime"))) is not None
        and (end := _millis(paragraph.get("endTime"))) is not None
    ]
    timed.sort(key=lambda row: row[0])
    excerpts: list[dict[str, Any]] = []
    for entry in _todo_entries(todos_payload):
        created = _millis(entry.get("createdTime"))
        title = str(entry.get("title") or "").strip()
        if not title or created is None or created <= 0:
            continue
        anchor = next((index for index, (_start, end, _p) in enumerate(timed) if end >= created), None)
        if anchor is None:
            continue
        window = timed[max(0, anchor - PARAGRAPHS_BEFORE): anchor + PARAGRAPHS_AFTER + 1]
        excerpts.append({
            "todo": title,
            "created_ms": created,
            "lines": [line for _start, _end, paragraph in window for line in _lines(paragraph)],
        })
    return excerpts


def _lines(paragraph: dict[str, Any]) -> list[str]:
    """One line per sentence, each with the speaker label.

    A paragraph is often several sentences long. A line per sentence lets an Agent
    quote the one sentence that matters with its label as an exact substring; a
    line per paragraph invites "label + a sentence from the middle", which is not
    text the source contains.
    """
    speaker = str(paragraph.get("nickName") or "").strip()
    sentences = [
        str(sentence.get("sentence") or "").strip()
        for sentence in paragraph.get("sentenceList") or []
        if isinstance(sentence, dict)
    ]
    sentences = [sentence for sentence in sentences if sentence]
    return [f"{speaker}：{text}" for text in sentences or [str(paragraph.get("paragraph") or "").strip()]]
