import json

import pytest

from app.minutes_owner_backfill import backfill_minutes_owners, ownerless_candidate_meetings
from app.store import AutoReplyStore
from app.task_semantic_service import RecordCandidate, SourceSignal, TaskSemanticService, UpdateBusinessTask
from app.task_semantic_models import BusinessTaskStatus

MEETING = {"taskUuid": "minutes-1", "title": "每周内容同步", "createdAt": "2026-09-24T11:29:20+08:00"}
TODOS = {"result": {"actions": ['{"value":"整理访谈问题清单"}'], "dingtalkTodoList": [
    {"title": "整理访谈问题清单", "createdTime": 738480, "executorList": []},
]}}
PARAGRAPHS = [
    {"nickName": "磊哥", "paragraph": "框架啊。", "startTime": "736000", "endTime": "738000"},
    {"nickName": "Zoey", "paragraph": "我们可以先列一个list给你看。", "startTime": "738500", "endTime": "748400"},
]


class FakeDws:
    def __init__(self, *, todos=TODOS, paragraphs=PARAGRAPHS, summary=""):
        self.todos, self.paragraphs, self.summary, self.reads = todos, paragraphs, summary, []

    def get_minutes_todos(self, minutes_id):
        self.reads.append(minutes_id)
        return self.todos

    def get_all_minutes_transcription(self, minutes_id):
        return {"paragraphs": self.paragraphs}

    def get_minutes_summary(self, minutes_id):
        return {"result": {"fullSummary": self.summary}}


def _candidate(store, *, minutes_id="minutes-1", title="整理访谈问题清单", meeting=MEETING, owner=""):
    evidence = json.dumps({"meeting": meeting, "todos": TODOS}, ensure_ascii=False)
    ref = f"{minutes_id}#todos-sha256=old"
    result = TaskSemanticService(store).record_candidate(RecordCandidate(
        title=title, owner_name=owner,
        signal=SourceSignal(source_type="ai_minutes", source_ref=ref, evidence_text=evidence, dedupe_key=f"ai_minutes:{ref}:{title}"),
    ))
    return result.task_id


def _queued(store):
    with store._connect() as db:
        return [row[0] for row in db.execute("select source_ref from work_summary_inputs")]


def test_a_meeting_with_an_ownerless_candidate_is_queued_once_with_the_conversation_attached(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    task_id = _candidate(store)

    dry = backfill_minutes_owners(store, FakeDws())
    assert (dry.inspected, dry.queued, dry.decisions[0]["outcome"]) == (1, 0, "would queue")
    assert _queued(store) == []

    applied = backfill_minutes_owners(store, FakeDws(), dry_run=False)
    assert applied.queued == 1 and applied.decisions[0]["task_ids"] == [task_id]
    [ref] = _queued(store)
    assert ref.startswith("minutes-1#todos-sha256=") and ref != "minutes-1#todos-sha256=old"
    [claimed] = store.claim_work_summary_inputs(limit=5)
    summary = json.loads(json.loads(claimed.payload_json)["summary"])
    assert summary["meeting"] == MEETING
    assert "Zoey：我们可以先列一个list给你看。" in summary["transcript_excerpts"][0]["lines"]

    again = backfill_minutes_owners(store, FakeDws(), dry_run=False)
    assert (again.queued, again.decisions[0]["outcome"]) == (0, "already queued")
    assert len(_queued(store)) == 1


def test_only_open_ownerless_candidates_from_minutes_count(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _candidate(store, minutes_id="owned", title="已有负责人", owner="Zoey")
    ignored = _candidate(store, minutes_id="ignored", title="已忽略")
    TaskSemanticService(store).update_task(UpdateBusinessTask(
        task_id=ignored, status=BusinessTaskStatus.CANCELLED,
        signal=SourceSignal(source_type="console", source_ref="console:1", evidence_text="忽略", dedupe_key="console:1"),
    ))
    open_id = _candidate(store, minutes_id="open", title="待处理")

    assert [(minutes_id, ids) for minutes_id, _record, ids in ownerless_candidate_meetings(store)] == [("open", [open_id])]


@pytest.mark.parametrize(
    ("dws", "outcome"),
    [
        (FakeDws(todos={"result": {"actions": []}}), "skipped: the meeting has no action items now"),
        (FakeDws(paragraphs=[]), "skipped: no action item can be located in the transcript, and no meeting summary either"),
    ],
)
def test_a_meeting_that_cannot_be_read_further_is_reported_and_left_alone(tmp_path, dws, outcome):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _candidate(store)

    result = backfill_minutes_owners(store, dws, dry_run=False)

    assert (result.queued, result.decisions[0]["outcome"]) == (0, outcome)
    assert _queued(store) == []


def test_a_meeting_with_no_locatable_excerpt_but_a_summary_is_still_queued(tmp_path):
    """Derek 2026-09-28: a transcript-window miss is not proof the source has no owner; the summary may still have it."""
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _candidate(store)
    full_summary = "行动项：**磊哥**与**周俊杰**负责代码 Review。"

    result = backfill_minutes_owners(store, FakeDws(paragraphs=[], summary=full_summary), dry_run=False)

    assert (result.queued, result.decisions[0]["outcome"]) == (1, "queued")
    assert result.decisions[0]["has_summary"] is True
    [claimed] = store.claim_work_summary_inputs(limit=5)
    summary = json.loads(json.loads(claimed.payload_json)["summary"])
    assert summary["meeting_summary"] == full_summary
    assert "transcript_excerpts" not in summary


def test_limit_takes_the_newest_meetings_first(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    _candidate(store, minutes_id="old", title="旧", meeting={**MEETING, "taskUuid": "old"})
    _candidate(store, minutes_id="new", title="新", meeting={**MEETING, "taskUuid": "new"})

    result = backfill_minutes_owners(store, FakeDws(), limit=1)

    assert [decision["minutes_id"] for decision in result.decisions] == ["new"]


def test_two_ownerless_tasks_read_the_same_original_meeting_body(tmp_path):
    store = AutoReplyStore(tmp_path / "w.sqlite3")
    first = _candidate(store, title="整理访谈问题清单")
    second = _candidate(store, title="确定访谈时间")
    assert ownerless_candidate_meetings(store) == [("minutes-1", MEETING, [first, second])]
    signals = store.list_business_task_signals()
    assert len(signals) == 2
    assert signals[0].source_document_id == signals[1].source_document_id
