from __future__ import annotations

import json
from types import SimpleNamespace

from app.meeting_memory_export import export_sent_meeting_memory


def _job(*, status: str = "sent") -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        status=status,
        meeting_id="minutes-7",
        title="经营复盘",
        ended_at="2026-09-14T09:30:00+08:00",
        final_message="【经营复盘结论】\n本周先完成客户验证。",
        source_json=json.dumps(
            {
                "calendar_evidence": {
                    "participants": [{"name": "Derek"}, {"name": "Claire"}]
                }
            },
            ensure_ascii=False,
        ),
        decision_json=json.dumps(
            {
                "target": {"kind": "group", "title": "经营群"},
                "derek_viewpoint": {"expressed_view": "先验证，再扩大投入。"},
                "key_questions": ["谁负责下周客户回访？"],
            },
            ensure_ascii=False,
        ),
    )


def test_export_merges_sent_conclusion_and_archived_minutes_once(tmp_path):
    archive = tmp_path / "AI听记"
    archive.mkdir()
    (archive / "meeting.md").write_text(
        "<!-- row_key: minutes-7 -->\n# AI Summary\n客户反馈显示需要先验证。\n# Transcript\n...\n",
        encoding="utf-8",
    )
    output = tmp_path / "memory" / "meetings.jsonl"

    result = export_sent_meeting_memory([_job()], archive_dir=archive, output_path=output)

    assert result.records == 1
    assert result.summaries_attached == 1
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [record["key"] for record in records] == ["meeting-alignment:minutes-7"]
    text = records[0]["arguments"]["data"]
    assert "本周先完成客户验证" in text
    assert "客户反馈显示需要先验证" in text
    assert "先验证，再扩大投入" in text
    assert "Transcript" not in text


def test_export_excludes_non_sent_jobs(tmp_path):
    output = tmp_path / "meeting-alignment.jsonl"

    result = export_sent_meeting_memory([_job(status="failed")], archive_dir=tmp_path, output_path=output)

    assert result.records == 0
    assert output.read_text(encoding="utf-8") == ""
