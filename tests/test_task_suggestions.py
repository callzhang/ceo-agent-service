from dataclasses import replace
import json
import sqlite3
import subprocess
import sys

import pytest

from app.project_context_service import ProjectContextService
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
    duty_id = store.create_business_task_signal(
        source_type="meeting",
        source_ref="meeting:p",
        evidence_text="P 项目王五负责商务和回款。",
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
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=context, signal_ids=(duty_id,), db=db
        )
    signal = SourceSignal(
        source_type="message",
        source_ref="chat:p:1",
        evidence_text="P 项目本期款项尚未到账。",
        dedupe_key="chat:p:1:risk",
        author_kind=BusinessActorKind.HUMAN,
    )
    risk_id = store.create_business_task_signal(**signal.__dict__)
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
    return TaskSemanticService(store).record_suggestion(command)


def counts(store):
    with store._connect() as db:
        return {
            table: db.execute(f"select count(*) from {table}").fetchone()[0]
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
    assert service.record_suggestion(command).task_id == first.task_id
    after = counts(store)
    assert after["business_tasks"] == before["business_tasks"]
    assert after["business_task_events"] == before["business_task_events"] + 1
    service.record_suggestion(command)
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
            TaskSemanticService(store).record_suggestion(command, _db=db)
        assert counts_in_transaction(db) == before
    assert counts(store) == before


def counts_in_transaction(db):
    return {
        table: db.execute(f"select count(*) from {table}").fetchone()[0]
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
    task_id = store.create_business_task(title="旧候选", stage="candidate")
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
        signal_id = store.create_business_task_signal(
            source_type=provenance_type,
            source_ref="provenance:duty",
            evidence_text="王五负责商务和回款。款项尚未到账。",
            dedupe_key=f"{provenance_type}:duty",
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
            TaskSemanticService(store).record_suggestion(command, _db=db)
        assert counts_in_transaction(db) == before


def test_legacy_task_columns_upgrade_preserves_truth_and_raw_history(project):
    store, _, signal, _ = project
    result = TaskSemanticService(store).record_candidate(
        RecordCandidate(title="原始待明确事项", signal=signal)
    )
    original = store.get_business_task(result.task_id).model_dump(mode="json")
    original.pop("origin")
    original.pop("suggestion_json")
    formal_id = store.create_business_task(
        title="已有真实承诺",
        stage="formal",
        formal_basis="explicit_assignment",
        commitment_status="accepted",
        owner_name="王五",
        owner_user_id="wangwu",
        owner_evidence_json='{"source_ref":"historic:human"}',
        deadline_at="2026-10-08",
    )
    original_formal = store.get_business_task(formal_id).model_dump(mode="json")
    original_formal.pop("origin")
    original_formal.pop("suggestion_json")
    # Produce a real pre-Task-3 representation in this test-owned database.
    with sqlite3.connect(store.path) as db:
        history = db.execute(
            "select * from business_task_events order by id"
        ).fetchall()
        db.execute("alter table business_tasks drop column origin")
        db.execute("alter table business_tasks drop column suggestion_json")
        db.execute(
            "update service_state set value='2026-10-04.2' where key='store_schema_version'"
        )
    # A real service upgrade restarts its process; do not let this process's
    # initialized-path cache bypass the migration being tested.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from app.store import AutoReplyStore; AutoReplyStore(Path(sys.argv[1]))",
            str(store.path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    upgraded = AutoReplyStore(store.path)
    current = upgraded.get_business_task(result.task_id).model_dump(mode="json")
    assert current.pop("origin") == "source"
    assert current.pop("suggestion_json") == "{}"
    assert current == original
    current_formal = upgraded.get_business_task(formal_id).model_dump(mode="json")
    assert current_formal.pop("origin") == "source"
    assert current_formal.pop("suggestion_json") == "{}"
    assert current_formal == original_formal
    with upgraded._connect() as db:
        assert {"origin", "suggestion_json"}.issubset(
            {row["name"] for row in db.execute("pragma table_info(business_tasks)")}
        )
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == "2026-10-04.3"
        )
        assert [
            tuple(row)
            for row in db.execute("select * from business_task_events order by id")
        ] == history
        assert db.execute("pragma foreign_key_check").fetchall() == []
    AutoReplyStore(store.path)
    assert upgraded.get_business_task(result.task_id).stage.value == "candidate"
