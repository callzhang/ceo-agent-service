import json
import hashlib

import pytest

from app.business_source_reader import PreparedSources
from app.store import AutoReplyStore
from app.task_agent import (
    _prepare_decision_sources,
    _report_item_source_body,
    _validate_identity_proposal,
    apply_task_agent_decision,
)
from app.task_models import TaskAgentDecision, TaskIdentityProposal, WorkItem
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_service import RecordFormalTask, SourceSignal, TaskSemanticService
from app.task_semantic_models import BusinessActorKind, BusinessEvidenceRole, FormalTaskBasis
from tests.support.business_task_seed import seed_task_evidence, seed_task_signal


REPORT_ITEM = "subway 数据中台商务立项@Claire Huang(Claire)"
TASK_TITLE = "推进Subway数据中台商务立项"
SUBWAY_REPORT_NODE = "YMyQA2dXW7xZQ1yqs1BOo5p6VzlwrZgb"


def test_report_identity_searches_json_wrapped_markdown_not_metadata():
    payload = json.dumps(
        {
            "report": {"title": REPORT_ITEM, "document_id": "subway-report"},
            "markdown": f"本周进展\n- {REPORT_ITEM}\n",
        },
        ensure_ascii=False,
    )
    metadata_only = json.dumps(
        {"report": {"title": REPORT_ITEM, "document_id": "subway-report"}},
        ensure_ascii=False,
    )

    assert _report_item_source_body(payload) == f"本周进展\n- {REPORT_ITEM}\n"
    assert _report_item_source_body(metadata_only) == ""


def _report_ref(body: str, node: str = SUBWAY_REPORT_NODE, *, versioned=True) -> str:
    base = f"dingtalk-doc:{node}"
    digest = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
    return f"{base}#sha256={digest}" if versioned else base


def _record_formal_task(
    store,
    *,
    source_ref: str,
    body: str,
    excerpt: str = REPORT_ITEM,
    owner: str = "Claire Huang(Claire)",
    owner_id: str = "claire-id",
    owner_source_ref: str | None = None,
    owner_source_body: str | None = None,
    source_type: str = "management_weekly_report",
    originals: dict[int, str] | None = None,
):
    document_node = source_ref.removeprefix("dingtalk-doc:").partition("#")[0]
    persisted_body = json.dumps(
        {
            "report": {"title": "2026年8月24日-管理周报收集", "document_id": document_node},
            "markdown": body.strip(),
        },
        ensure_ascii=False,
    )
    signal = SourceSignal(
        source_type=source_type,
        source_ref=source_ref,
        evidence_text=persisted_body,
        dedupe_key=source_ref,
        source_time="2026-08-24T00:00:00Z",
        conversation_id=source_ref,
        author_kind=BusinessActorKind.HUMAN,
        context_json=json.dumps(
            {"owner_identity": {"name": owner, "user_id": owner_id}}
        ),
    )
    owner_ref = owner_source_ref or source_ref
    owner_body = (owner_source_body or body).strip()
    result = TaskSemanticService(store).record_formal_task(
        RecordFormalTask(
            title=TASK_TITLE,
            signal=signal,
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_user_id=owner_id,
            owner_name=owner,
            owner_evidence_json=json.dumps(
                {
                    "source_ref": source_ref,
                    "excerpt": excerpt,
                    "user_id": owner_id,
                    "name": owner,
                }
            ),
        )
    )
    if originals is not None:
        originals[result.signal_id] = persisted_body
    if owner_ref != source_ref:
        owner_node = owner_ref.removeprefix("dingtalk-doc:").partition("#")[0]
        owner_persisted_body = json.dumps(
            {
                "report": {
                    "title": "2026年8月24日-管理周报收集",
                    "document_id": owner_node,
                },
                "markdown": owner_body,
            },
            ensure_ascii=False,
        )
        owner_signal_id = seed_task_signal(store,
            source_type=source_type,
            source_ref=owner_ref,
            evidence_text=owner_persisted_body,
            dedupe_key=f"owner-proof:{owner_ref}",
            context_json="{}",
        )
        if originals is not None:
            originals[owner_signal_id] = owner_persisted_body
        seed_task_evidence(store,
            task_id=result.task_id,
            signal_id=owner_signal_id,
            evidence_role=BusinessEvidenceRole.CORRECTION,
        )
        with store.business_task_transaction() as db:
            task = store.get_business_task_in_transaction(
                task_id=result.task_id,
                _db=db,
            )
            store.update_business_task_in_transaction(
                task=task.model_copy(
                    update={
                        "owner_evidence_json": json.dumps(
                            {
                                "source_ref": owner_ref,
                                "excerpt": excerpt,
                                "user_id": owner_id,
                                "name": owner,
                            }
                        )
                    }
                ),
                _db=db,
            )
    return result


def _prepared_originals(store, decision, originals):
    """Make the exact original report bodies of this test's Signals readable."""
    prepared = (
        PreparedSources(store.business_source_reader())
        if decision is None else _prepare_decision_sources(store, decision)
    )
    for signal_id, body in originals.items():
        fact = store.get_business_task_signal(signal_id)
        prepared.add(fact)
        prepared.add_current(fact, body)
    return prepared


def test_same_versioned_report_item_and_owner_can_merge_duplicate_tasks(tmp_path):
    store = AutoReplyStore(tmp_path / "report-item-identity.sqlite3")
    originals: dict[int, str] = {}
    first_body = f"本周进展\n\n- {REPORT_ITEM}\n"
    second_body = f"本周进展已更新\n\n- {REPORT_ITEM}\n- 其他独立事项\n"
    first = _record_formal_task(
        store,
        source_ref=_report_ref(first_body),
        body=first_body,
        owner_id="",
        originals=originals,
    )
    older_owner_body = f"上周进展\n- {REPORT_ITEM}\n"
    second = _record_formal_task(
        store,
        source_ref=_report_ref(second_body),
        body=second_body,
        owner_id="",
        owner_source_ref=_report_ref(older_owner_body),
        owner_source_body=older_owner_body,
        originals=originals,
    )
    work_item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "reply:merge-report-duplicates",
                "title": "Task identity review",
                "created_at": "2026-10-10T08:00:00Z",
            },
            "summary": "确认同一报告事项的重复记录。",
            "context": {"source_conversation_kind": "group"},
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "将同一报告事项的重复记录合并到既有 Task。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "merge_identity",
                    "task_id": first.task_id,
                    "target_task_id": second.task_id,
                    "source_ref": work_item.source.ref,
                    "source_excerpt": work_item.summary,
                    "title": TASK_TITLE,
                    "identity_proposal": {
                        "source_task_id": first.task_id,
                        "target_task_id": second.task_id,
                        "reason": "两份版本化报告引用同一条唯一行动和同一负责人。",
                        "identity_evidence": {
                            "basis": "same_source_item_same_owner",
                            "source_signal_id": first.signal_id,
                            "target_signal_id": second.signal_id,
                        },
                    },
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=work_item,
        decision=decision,
        record_run=False,
        prepared_sources=_prepared_originals(store, decision, originals),
    )

    assert result.task_ids == (second.task_id,)
    assert store.get_business_task(first.task_id).merged_into_task_id == second.task_id
    retained = store.get_business_task(second.task_id)
    assert retained.status.value == "open"
    assert retained.title == TASK_TITLE
    assert retained.owner_name == "Claire Huang(Claire)"
    retained_evidence = store.list_business_task_evidence(second.task_id)
    assert {row.signal_id for row in retained_evidence} >= {
        first.signal_id,
        second.signal_id,
    }


@pytest.mark.parametrize(
    "case",
    [
        "different_document",
        "different_line",
        "ambiguous_line",
        "unversioned_ref",
        "version_digest_mismatch",
        "owner_proof_digest_mismatch",
        "owner_id_conflict",
        "source_type_mismatch",
    ],
)
def test_source_item_merge_rejects_insufficient_identity_proof(tmp_path, case):
    store = AutoReplyStore(tmp_path / f"reject-{case}.sqlite3")
    originals: dict[int, str] = {}
    source_line = REPORT_ITEM
    target_line = (
        "准备Subway客户workshop并覆盖至少7个场景@Claire Huang(Claire)"
        if case == "different_line"
        else source_line
    )
    source_body = f"本周进展\n\n- {source_line}\n"
    if case == "ambiguous_line":
        source_body += f"- {source_line}\n"
    target_body = f"本周进展\n\n- {target_line}\n"
    if case in {
        "owner_id_conflict",
        "source_type_mismatch",
        "owner_proof_digest_mismatch",
    }:
        target_body += "- 另一个来源版本中的无关事项\n"
    source_ref = _report_ref(source_body, "report-a")
    target_ref = _report_ref(
        target_body,
        "report-b" if case == "different_document" else "report-a",
    )
    if case == "unversioned_ref":
        source_ref = _report_ref(source_body, "report-a", versioned=False)
        target_ref = _report_ref(target_body, "report-a-v2", versioned=False)
    if case == "version_digest_mismatch":
        source_ref = _report_ref(source_body + " tampered", "report-a")
    source_type = "management_weekly_report"
    target_type = (
        "project_weekly_report"
        if case == "source_type_mismatch"
        else source_type
    )
    first = _record_formal_task(
        store,
        source_ref=source_ref,
        body=source_body,
        excerpt=source_line,
        owner_id="claire-id",
        source_type=source_type,
        originals=originals,
    )
    owner_source_ref = None
    owner_source_body = None
    if case == "owner_proof_digest_mismatch":
        owner_source_ref = _report_ref(target_body + " tampered", "report-a")
        owner_source_body = target_body
    second = _record_formal_task(
        store,
        source_ref=target_ref,
        body=target_body,
        excerpt=target_line,
        owner_id="different-id" if case == "owner_id_conflict" else "claire-id",
        owner_source_ref=owner_source_ref,
        owner_source_body=owner_source_body,
        source_type=target_type,
        originals=originals,
    )
    proposal = TaskIdentityProposal.model_validate(
        {
            "source_task_id": first.task_id,
            "target_task_id": second.task_id,
            "identity_evidence": {
                "basis": "same_source_item_same_owner",
                "source_signal_id": first.signal_id,
                "target_signal_id": second.signal_id,
            },
        }
    )

    prepared = _prepared_originals(store, None, originals)
    with (
        store.business_task_transaction() as db,
        pytest.raises(ValueError, match="same_source_item_same_owner"),
    ):
        _validate_identity_proposal(store, proposal, db=db, prepared_sources=prepared)
