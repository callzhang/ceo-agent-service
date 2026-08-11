import inspect
from pathlib import Path

import pytest

from app.task_agent import build_task_agent_prompt
from app.task_models import TaskAgentDecision, TodoChange
from tests.e2e.test_task7_work_tracking_semantics_live import (
    _assert_assigned_owners_are_supported,
    _verify_bound_follow_up,
    _verify_speaker_not_owner,
)


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-work-tracking" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_work_tracking_skill_owns_judgment_and_delegates_only_mechanics():
    text = " ".join(_skill_text().split())

    for required in (
        "Decide whether the input deserves durable tracking",
        "Choose a one-time action, TODO, or project",
        "Require stable owner identity and owner evidence",
        "Every follow-up must bind to a TODO",
        "Read current project, TODO, and external status before following up",
        "Apply replies to the existing work item",
        "exact-message idempotency",
        "A corrected or materially changed message is a new revision",
    ):
        assert required in text

    for forbidden in ("re.compile", "sender_name ==", "WEAK_TITLES"):
        assert forbidden not in text


def test_task_agent_prompt_builder_contains_transport_not_business_policy():
    source = inspect.getsource(build_task_agent_prompt)

    assert "load_skill_text" in source
    assert "TaskAgentDecision.model_json_schema" in source
    for duplicated_policy in (
        "流程性内容默认忽略",
        "owner_user_id 不能靠猜",
        "参与人、发言人、转述人",
        "P0 今天跟进",
        "小青显示",
    ):
        assert duplicated_policy not in source


def test_task_agent_contract_has_no_checked_duplicate_schema():
    assert not (ROOT / "app" / "schemas" / "task_agent_decision.schema.json").exists()


def _follow_up_decision(
    *,
    todo_id=None,
    todo_ref="",
    owner_user_id="",
    owner_name="",
):
    return TaskAgentDecision.model_validate(
        {
            "action": "create_project",
            "todo_changes": [],
            "follow_up_drafts": [
                {
                    "todo_id": todo_id,
                    "todo_ref": todo_ref,
                    "title": "Progress check",
                    "description": "Current delivery state",
                    "owner_user_id": owner_user_id,
                    "owner_name": owner_name,
                    "target_kind": "direct",
                    "question_text": "What is the current blocker and ETA?",
                }
            ],
        }
    )


def test_live_binding_assertion_rejects_invented_existing_todo_id():
    decision = _follow_up_decision(todo_id=999)

    with pytest.raises(AssertionError):
        _verify_bound_follow_up(decision, {"current_todos": []})


def test_live_binding_assertion_accepts_explicit_same_decision_todo_ref():
    decision = _follow_up_decision(todo_ref="launch-checklist")
    decision.todo_changes.append(
        TodoChange.model_validate(
            {
                "action": "create",
                "todo_ref": "launch-checklist",
                "title": "Complete launch checklist",
            }
        )
    )

    _verify_bound_follow_up(decision, {"current_todos": []})


def test_live_owner_assertion_rejects_identity_without_owner_evidence():
    decision = _follow_up_decision(todo_ref="launch-checklist", owner_name="Sam")

    with pytest.raises(AssertionError):
        _verify_speaker_not_owner(
            decision,
            {"owner_evidence": [], "verified_owner_resolution": []},
        )


def test_live_owner_assertion_accepts_identity_in_supported_evidence_set():
    decision = TaskAgentDecision.model_validate(
        {
            "action": "create_project",
            "todo_changes": [
                {
                    "action": "create",
                    "title": "Complete launch checklist",
                    "owner_user_id": "uid-1",
                }
            ],
        }
    )

    _assert_assigned_owners_are_supported(
        decision,
        {
            "owner_evidence": [
                {"user_id": "uid-1", "source": "explicit commitment"}
            ]
        },
    )


def _owned_todo_decision(*, owner_user_id: str, owner_name: str):
    return TaskAgentDecision.model_validate(
        {
            "action": "create_project",
            "todo_changes": [
                {
                    "action": "create",
                    "title": "Complete launch checklist",
                    "owner_user_id": owner_user_id,
                    "owner_name": owner_name,
                }
            ],
        }
    )


def test_live_owner_assertion_rejects_cross_record_identity_match():
    decision = _owned_todo_decision(
        owner_user_id="uid-1",
        owner_name="Display One",
    )

    with pytest.raises(AssertionError):
        _assert_assigned_owners_are_supported(
            decision,
            {
                "owner_evidence": [
                    {"user_id": "uid-1", "name": "Display Two"},
                    {"user_id": "uid-2", "name": "Display One"},
                ]
            },
        )


def test_live_owner_assertion_accepts_coherent_identity_pair():
    decision = _owned_todo_decision(
        owner_user_id="uid-1",
        owner_name="Display One",
    )

    _assert_assigned_owners_are_supported(
        decision,
        {
            "verified_owner_resolution": [
                {
                    "user_id": "uid-1",
                    "display_name": "Display One",
                    "source": "verified directory resolution",
                }
            ]
        },
    )
