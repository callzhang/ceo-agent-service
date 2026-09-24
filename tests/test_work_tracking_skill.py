import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.task_agent import build_task_agent_prompt
from app.task_models import TaskAgentDecision, TaskDecision, owner_identity_is_supported

from app.business_skills import bundled_business_skills_root

SKILLS_ROOT = bundled_business_skills_root()


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = SKILLS_ROOT / "ceo-work-tracking" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_work_tracking_skill_owns_judgment_and_delegates_only_mechanics():
    text = " ".join(_skill_text().split())

    for required in (
        "Extract zero or more distinct Tasks from supplied source context",
        "Never originate a Task, deliverable, owner, assignment, or date from Agent judgment alone",
        "An external TODO proves a formal record exists; it does not prove its assignee accepted it",
        "Keep date meanings separate and source-backed",
        "confirmed business anchor and a concrete material trigger",
        "Memory is optional context, not source evidence or completion proof",
        "one Task Agent returns one `TaskAgentDecision` lifecycle contract",
        "The Task Agent cannot create a TODO through completion fields",
        "The current Codex route has no per-turn",
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


def test_completion_operations_are_top_level_unified_decision_fields_and_skill_agrees():
    decision_fields = TaskAgentDecision.model_fields
    task_decision_fields = TaskDecision.model_fields
    assert "todo_changes" in decision_fields
    assert "follow_up_changes" in decision_fields
    assert "todo_changes" not in task_decision_fields
    assert "follow_up_changes" not in task_decision_fields

    text = " ".join(_skill_text().split())
    assert "top-level `todo_changes` and `follow_up_changes` fields" in text
    assert "Do not emit legacy `todo_changes`" not in text
    assert "outside the approved Task 6 scope, not a release blocker" in text
    assert "`max_raw_reads` cap remains unmet and is a release blocker" not in text


def _formal_task(**overrides) -> dict[str, object]:
    return {
        "action": "create_task",
        "transition": "none",
        "source_excerpt": "王明负责提交报价，周五前完成。",
        "source_ref": "message:42",
        "title": "提交报价",
        "formal_basis": "explicit_assignment",
        "owner_user_id": "uid-1",
        "owner_name": "Display One",
        "owner_evidence": {
            "source_ref": "message:42",
            "excerpt": "王明负责提交报价",
        },
        **overrides,
    }


def test_formal_task_cannot_be_created_without_source_binding():
    with pytest.raises(ValidationError, match="exact source excerpt and reference"):
        TaskDecision.model_validate(_formal_task(source_excerpt="", source_ref=""))


def test_formal_owner_identity_requires_source_evidence():
    with pytest.raises(ValidationError, match="owner_evidence"):
        TaskDecision.model_validate(_formal_task(owner_evidence={}))


def test_owner_id_and_name_must_be_supported_by_same_evidence_record():
    assigned = {"owner_user_id": "uid-1", "owner_name": "Display One"}

    assert not owner_identity_is_supported(
        assigned,
        [
            {"user_id": "uid-1", "name": "Display Two"},
            {"user_id": "uid-2", "name": "Display One"},
        ],
    )
    assert owner_identity_is_supported(
        assigned,
        [
            {
                "user_id": "uid-1",
                "display_name": "Display One",
                "source": "verified source identity",
            }
        ],
    )
