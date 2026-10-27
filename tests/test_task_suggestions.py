from dataclasses import replace
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from app.project_context_service import ProjectContextService
from app.business_definitions import BUSINESS_PROJECTIONS
from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import (
    BusinessActorKind,
    ProjectContext,
    SourceCitation,
    TaskSuggestion,
)
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import FormalTaskBasis, BusinessTaskDateType
from app.task_semantic_service import (
    ApplyAcceptance,
    PromoteCandidate,
    RecordCandidate,
    SourceSignal,
    TaskSemanticService,
    TaskDateInput,
)
from app import task_semantic_service as commands
from app.todo_sync import maybe_create_dingtalk_todo
from app.business_source_reader import PreparedSources
from app.action_business_definitions import business_action_projection
from app.action_storage import SYNC_ACTION_PROJECTIONS
from app.provenance_storage import signal_projection
from tests.support.business_task_seed import seed_task, seed_task_signal


class FakeTodoDws:
    def __init__(self):
        self.created = []

    def create_todo_task(self, **payload):
        self.created.append(payload)
        return {"todoTaskId": "dt-human-accepted"}

    def get_todo_task(self, task_id):
        return {"id": task_id, "done": False}


@pytest.fixture
def project(tmp_path):
    store = AutoReplyStore(tmp_path / "suggestions.sqlite3")
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(
        anchor_type="project", anchor_ref="registry:p", title="P"
    )
    project_id = resolver.register_official_project(
        anchor_id=anchor_id, registry_source="meeting:p"
    )
    duty_text = "P 项目王五负责商务和回款。"
    duty_id = seed_task_signal(store,
        source_type="meeting",
        source_ref="meeting:p",
        evidence_text=duty_text,
        dedupe_key="meeting:p:duty",
        author_kind="human",
    )
    duty = SourceCitation(
        signal_id=duty_id, source_ref="meeting:p", source_excerpt="王五负责商务和回款"
    )
    context = ProjectContext(
        goal="交付并回款",
        scope="一期",
        overall_owner=None,
        responsibilities=[
            {"person_name": "王五", "responsibility": "商务和回款", "evidence": [duty]}
        ],
        facts=[],
    )
    risk_text = "P 项目本期款项尚未到账。"
    signal = SourceSignal(
        source_type="message",
        source_ref="chat:p:1",
        evidence_text=risk_text,
        dedupe_key="chat:p:1:risk",
        author_kind=BusinessActorKind.HUMAN,
    )
    risk_id = seed_task_signal(store, **signal.__dict__)
    prepared_sources = PreparedSources(store.business_source_reader())
    prepared_sources.add_current(store.get_business_task_signal(duty_id), duty_text)
    prepared_sources.add_current(store.get_business_task_signal(risk_id), risk_text)
    store._suggestion_prepared_sources = prepared_sources
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=context, signal_ids=(duty_id,), db=db,
            prepared_sources=prepared_sources,
        )
    risk = SourceCitation(
        signal_id=risk_id, source_ref=signal.source_ref, source_excerpt="款项尚未到账"
    )
    suggestion = TaskSuggestion(
        reason="按现有商务职责跟进回款",
        suggested_owner_name="王五",
        responsibility_evidence=[duty],
        basis_evidence=[risk],
    )
    return store, anchor_id, signal, suggestion


def record(project, **changes):
    store, anchor_id, signal, suggestion = project
    command = commands.RecordTaskSuggestion(
        title="核实回款进展",
        description="确认延期原因",
        signal=signal,
        suggestion=suggestion,
        project_anchor_id=anchor_id,
        **changes,
    )
    return TaskSemanticService(store).record_suggestion(
        command, prepared_sources=store._suggestion_prepared_sources
    )


def counts(store):
    projections = {
        "business_task_events": SYNC_ACTION_PROJECTIONS["business_task_events"],
        "business_task_evidence": business_action_projection("business_task_evidence"),
        "business_task_signals": signal_projection(),
        "business_task_follow_ups": SYNC_ACTION_PROJECTIONS["business_task_follow_ups"],
        "business_task_todo_sync_outbox": SYNC_ACTION_PROJECTIONS["business_task_todo_sync_outbox"],
        "business_task_dingtalk_links": SYNC_ACTION_PROJECTIONS["business_task_dingtalk_links"],
    }
    with store._connect() as db:
        return {
            table: db.execute(
                f"select count(*) from ({BUSINESS_PROJECTIONS['task']})"
                if table == "business_tasks"
                else f"select count(*) from ({projections[table]})"
            ).fetchone()[0]
            for table in (
                "business_tasks",
                "business_task_events",
                "business_task_evidence",
                "business_task_signals",
                "business_task_follow_ups",
                "business_task_todo_sync_outbox",
                "business_task_dingtalk_links",
            )
        }


def test_suggestion_is_display_only_and_uses_prior_project_duty(project):
    store, anchor_id, signal, suggestion = project
    assert "王五" not in signal.evidence_text
    result = record(project)
    task = store.get_business_task(result.task_id)
    dws = FakeTodoDws()
    assert (
        maybe_create_dingtalk_todo(
            store, dws, business_task_id=task.id, now="2026-10-04 12:00:00"
        )
        is None
    )
    assert dws.created == []
    assert task.origin == "agent_suggestion"
    assert task.stage.value == "candidate" and task.status.value == "open"
    assert task.commitment_status.value == "none" and task.formal_basis is None
    assert task.owner_name == task.owner_user_id == task.deadline_at == ""
    assert task.owner_evidence_json == "{}"
    assert TaskSuggestion.model_validate_json(task.suggestion_json) == suggestion
    assert (
        store.list_business_task_project_links(task_id=task.id)[0].canonical_anchor_id
        == anchor_id
    )
    assert {
        link.signal_id for link in store.list_business_task_evidence(task_id=task.id)
    } == {
        result.signal_id,
        suggestion.responsibility_evidence[0].signal_id,
        suggestion.basis_evidence[0].signal_id,
    }
    assert (
        counts(store)["business_task_follow_ups"]
        == counts(store)["business_task_todo_sync_outbox"]
        == 0
    )


def test_suggestion_replay_and_changed_details_keep_identity(project):
    store, anchor_id, signal, suggestion = project
    first = record(project)
    before = counts(store)
    assert record(project).task_id == first.task_id
    assert record(project, task_id=first.task_id).task_id == first.task_id
    assert counts(store) == before
    changed = suggestion.model_copy(update={"reason": "先核实，再按商务职责追踪"})
    command = commands.RecordTaskSuggestion(
        title="核实回款进展",
        description="确认延期原因",
        signal=signal,
        suggestion=changed,
        project_anchor_id=anchor_id,
        task_id=first.task_id,
    )
    service = TaskSemanticService(store)
    assert service.record_suggestion(
        command, prepared_sources=store._suggestion_prepared_sources
    ).task_id == first.task_id
    after = counts(store)
    assert after["business_tasks"] == before["business_tasks"]
    assert after["business_task_events"] == before["business_task_events"] + 1
    service.record_suggestion(command, prepared_sources=store._suggestion_prepared_sources)
    assert counts(store) == after


def test_evidence_only_suggestion_update_reuses_explicit_identity(project):
    store, anchor_id, signal, suggestion = project
    first = record(project)
    before = counts(store)
    later_source = replace(
        signal,
        source_ref="chat:p:2",
        evidence_text="P 项目回款仍未到账，待核实进展。",
        dedupe_key="chat:p:2:risk",
    )
    command = commands.RecordTaskSuggestion(
        title="核实回款进展",
        description="确认延期原因",
        signal=later_source,
        suggestion=suggestion,
        project_anchor_id=anchor_id,
        task_id=first.task_id,
    )
    service = TaskSemanticService(store)
    result = service.record_suggestion(
        command, prepared_sources=store._suggestion_prepared_sources
    )
    after = counts(store)
    assert result.task_id == first.task_id
    assert after["business_tasks"] == before["business_tasks"]
    assert after["business_task_events"] == before["business_task_events"]
    assert after["business_task_signals"] == before["business_task_signals"] + 1
    assert after["business_task_evidence"] == before["business_task_evidence"] + 1
    assert service.record_suggestion(
        command, prepared_sources=store._suggestion_prepared_sources
    ).task_id == first.task_id
    assert counts(store) == after
    # No-ID replay belongs to the original event-backed creation source.
    assert record(project).task_id == first.task_id
    assert counts(store) == after


@pytest.mark.parametrize(
    "invalid", ["missing_signal", "wrong_ref", "fabricated_quote", "missing_project"]
)
def test_suggestion_invalid_proof_does_not_partially_write(project, invalid):
    store, anchor_id, signal, suggestion = project
    citation = suggestion.basis_evidence[0]
    if invalid == "missing_signal":
        citation = citation.model_copy(update={"signal_id": 999999})
    elif invalid == "wrong_ref":
        citation = citation.model_copy(update={"source_ref": "unrelated"})
    elif invalid == "fabricated_quote":
        citation = citation.model_copy(update={"source_excerpt": "不存在的原文"})
    else:
        anchor_id = 999999
    suggestion = suggestion.model_copy(update={"basis_evidence": [citation]})
    before = counts(store)
    command = commands.RecordTaskSuggestion(
        title="核实回款",
        signal=signal,
        suggestion=suggestion,
        project_anchor_id=anchor_id,
    )
    with store.business_task_transaction() as db:
        with pytest.raises(ValueError):
            TaskSemanticService(store).record_suggestion(
                command, _db=db,
                prepared_sources=store._suggestion_prepared_sources,
            )
        assert counts_in_transaction(db) == before
    assert counts(store) == before


def counts_in_transaction(db):
    projections = {
        "business_task_events": SYNC_ACTION_PROJECTIONS["business_task_events"],
        "business_task_evidence": business_action_projection("business_task_evidence"),
        "business_task_signals": signal_projection(),
        "business_task_follow_ups": SYNC_ACTION_PROJECTIONS["business_task_follow_ups"],
        "business_task_todo_sync_outbox": SYNC_ACTION_PROJECTIONS["business_task_todo_sync_outbox"],
        "business_task_dingtalk_links": SYNC_ACTION_PROJECTIONS["business_task_dingtalk_links"],
    }
    return {
        table: db.execute(
            f"select count(*) from ({BUSINESS_PROJECTIONS['task']})"
            if table == "business_tasks"
            else f"select count(*) from ({projections[table]})"
        ).fetchone()[0]
        for table in (
            "business_tasks",
            "business_task_events",
            "business_task_evidence",
            "business_task_signals",
            "business_task_follow_ups",
            "business_task_todo_sync_outbox",
            "business_task_dingtalk_links",
        )
    }


def test_source_candidate_not_relabelled_as_agent_suggestion(project):
    store, _, signal, _ = project
    source = replace(signal, dedupe_key="chat:p:source-candidate")
    result = TaskSemanticService(store).record_candidate(
        RecordCandidate(title="回款事项待明确", signal=source)
    )
    assert store.get_business_task(result.task_id).origin == "source"
    before = counts(store)
    with pytest.raises(ValueError):
        record(project, task_id=result.task_id)
    assert counts(store) == before


def test_human_assignment_promotes_same_suggestion_without_losing_origin(project):
    store, _, _, suggestion = project
    result = record(project)
    assignment = SourceSignal(
        source_type="message",
        source_ref="chat:p:assigned",
        evidence_text="王五，请核实回款进展。",
        dedupe_key="chat:p:assigned",
        author_user_id="derek",
        author_name="Derek",
        author_kind=BusinessActorKind.HUMAN,
        context_json='{"owner_identity":{"user_id":"wangwu","name":"王五"}}',
    )
    service = TaskSemanticService(store)
    promoted = service.promote_candidate(
        PromoteCandidate(
            task_id=result.task_id,
            signal=assignment,
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_name="王五",
            owner_user_id="wangwu",
            owner_evidence_json=json.dumps(
                {"source_ref": assignment.source_ref, "excerpt": "王五"},
                ensure_ascii=False,
            ),
        )
    )
    assert promoted.task_id == result.task_id
    task = store.get_business_task(result.task_id)
    assert (
        task.stage.value == "formal"
        and task.commitment_status.value == "assigned_unaccepted"
    )
    assert task.origin == "agent_suggestion" and task.owner_name == "王五"
    assert TaskSuggestion.model_validate_json(task.suggestion_json) == suggestion
    accepted = SourceSignal(
        source_type="message",
        source_ref="chat:p:accepted",
        evidence_text="我接受核实回款进展，承诺 2026-10-08 前完成。",
        dedupe_key="chat:p:accepted",
        author_user_id="wangwu",
        author_name="王五",
        author_kind=BusinessActorKind.HUMAN,
        context_json='{"reply_to_source_ref":"chat:p:assigned"}',
    )
    service.apply_acceptance(
        ApplyAcceptance(
            task_id=task.id,
            signal=accepted,
            acceptance_is_explicit=True,
            acceptance_polarity="accepted",
            acceptance_excerpt="我接受核实回款进展",
            referenced_signal_id=promoted.signal_id,
            date_facts=(
                TaskDateInput(
                    date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT,
                    value_at="2026-10-08",
                    raw_phrase="2026-10-08",
                    actor_kind=BusinessActorKind.HUMAN,
                    actor_user_id="wangwu",
                    actor_name="王五",
                ),
            ),
        )
    )
    assert store.get_business_task(task.id).commitment_status.value == "accepted"
    before = counts(store)
    assert record(project).task_id == task.id
    assert counts(store) == before
    dws = FakeTodoDws()
    link = maybe_create_dingtalk_todo(
        store, dws, business_task_id=task.id, now="2026-10-04 12:00:00"
    )
    assert link["business_task_id"] == task.id
    assert len(dws.created) == 1


def test_old_task_shape_defaults_to_source_without_promotion(project):
    store, _, _, _ = project
    task_id = seed_task(store, title="旧候选", stage="candidate")
    task = store.get_business_task(task_id)
    assert task.origin == "source" and task.suggestion_json == "{}"
    assert task.stage.value == "candidate" and task.commitment_status.value == "none"


@pytest.mark.parametrize("provenance_type", ["memory_provenance", "session_provenance"])
@pytest.mark.parametrize("position", ["discovery", "responsibility", "basis"])
def test_unobserved_provenance_cannot_be_original_suggestion_proof(
    project, provenance_type, position
):
    store, anchor_id, signal, suggestion = project
    if position == "discovery":
        signal = replace(
            signal,
            source_type=provenance_type,
            source_ref="provenance:risk",
            dedupe_key=f"{provenance_type}:risk",
        )
    else:
        signal_id = seed_task_signal(store,
            source_type=provenance_type,
            source_ref="provenance:duty",
            evidence_text="王五负责商务和回款。款项尚未到账。",
            dedupe_key=f"{provenance_type}:duty",
        )
        original_text = "王五负责商务和回款。款项尚未到账。"
        store._suggestion_prepared_sources.add_current(
            store.get_business_task_signal(signal_id), original_text
        )
        field = (
            "responsibility_evidence"
            if position == "responsibility"
            else "basis_evidence"
        )
        suggestion = suggestion.model_copy(
            update={
                field: [
                    SourceCitation(
                        signal_id=signal_id,
                        source_ref="provenance:duty",
                        source_excerpt="王五负责商务和回款",
                    )
                ]
            }
        )
    before = counts(store)
    command = commands.RecordTaskSuggestion(
        title="核实回款",
        signal=signal,
        suggestion=suggestion,
        project_anchor_id=anchor_id,
    )
    with store.business_task_transaction() as db:
        with pytest.raises(ValueError, match="observed"):
            TaskSemanticService(store).record_suggestion(
                command, _db=db,
                prepared_sources=store._suggestion_prepared_sources,
            )
        assert counts_in_transaction(db) == before


def test_legacy_task_columns_upgrade_preserves_truth_and_raw_history(tmp_path):
    source = tmp_path / "store-0bd"
    successor = tmp_path / "store-946"
    for ref, destination in (("0bd75f7c", source), ("9469363a", successor)):
        destination.mkdir()
        archive = subprocess.check_output(["git", "archive", ref, "app"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(destination, filter="data")

    path = tmp_path / "historic.sqlite3"
    old_code = """
from datetime import datetime, timezone
from pathlib import Path
import sys
from app.store import AutoReplyStore
store = AutoReplyStore(Path(sys.argv[1]))
now = datetime(2026, 10, 4, tzinfo=timezone.utc)
signal = store.create_business_task_signal(
    source_type='message', source_ref='historic:candidate',
    evidence_text='原始待明确事项', dedupe_key='historic:candidate', now=now)
candidate = store.create_business_task(title='原始待明确事项', stage='candidate', now=now)
formal = store.create_business_task(
    title='已有真实承诺', stage='formal', formal_basis='explicit_assignment',
    commitment_status='accepted', owner_name='王五', owner_user_id='wangwu',
    owner_evidence_json='{"source_ref":"historic:human"}', deadline_at='2026-10-08', now=now)
with store.business_task_transaction() as db:
    store.append_business_task_event(
        task_id=candidate, event_type='created', signal_id=signal,
        before_json='{}', after_json='{"source":"historic"}', reason='historic source', _db=db)
"""
    def run(code, checkout):
        subprocess.run(
            [sys.executable, "-c", code, str(path)], cwd=checkout,
            env={**os.environ, "PYTHONPATH": str(checkout)},
            check=True, capture_output=True, text=True, timeout=120,
        )

    run(old_code, source)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("select value from service_state where key='store_schema_version'").fetchone()[0] == "2026-10-04.2"
        assert "origin" not in {row["name"] for row in db.execute("pragma table_info(business_tasks)")}
        original = [dict(row) for row in db.execute("select * from business_tasks order by id")]
        history = [dict(row) for row in db.execute("select * from business_task_events order by id")]
        assert [(row["id"], row["title"], row["stage"]) for row in original] == [
            (1, "原始待明确事项", "candidate"), (2, "已有真实承诺", "formal")]
        assert len(history) == 1 and history[0]["task_id"] == 1

    upgrade_code = """
from pathlib import Path
import sys
from app.store import AutoReplyStore
store = AutoReplyStore(Path(sys.argv[1]))
assert store.get_business_task(1).stage.value == 'candidate'
assert store.get_business_task(2).stage.value == 'formal'
"""
    for _ in range(2):
        run(upgrade_code, successor)
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            assert db.execute("select value from service_state where key='store_schema_version'").fetchone()[0] == "2026-10-04.3"
            rows = [dict(row) for row in db.execute("select * from business_tasks order by id")]
            assert [{key: row[key] for key in original[0]} for row in rows] == original
            assert [(row["origin"], row["suggestion_json"]) for row in rows] == [
                ("source", "{}"), ("source", "{}")]
            assert [dict(row) for row in db.execute("select * from business_task_events order by id")] == history
            assert db.execute("pragma foreign_key_check").fetchall() == []
