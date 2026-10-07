import json
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.task_agent import build_task_agent_prompt
from app.task_models import (
    TaskAgentDecision,
    TaskDecision,
    owner_identity_is_supported,
    task_agent_output_schema,
)

from app.business_skills import bundled_business_skills_root

SKILLS_ROOT = bundled_business_skills_root()


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = SKILLS_ROOT / "ceo-work-tracking" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_work_tracking_skill_owns_judgment_and_delegates_only_mechanics():
    text = " ".join(_skill_text().split())

    for required in (
        "One Task Agent reads new evidence, current Project context and existing Tasks.",
        "Return one envelope with all three required lists",
        "Use tools only for read-only source/context discovery",
        "Project can have zero Tasks.",
        "A bare responsibility clause (a person being responsible for a business area) is ProjectContext only",
        "Actual Task: a source-backed independently completable deliverable/action.",
        "Memory is discovery/background, not observed source proof.",
        "Newly observed DingTalk human completion deterministically updates only its explicitly linked Task.",
    ):
        assert required in text

    for forbidden in ("re.compile", "sender_name ==", "WEAK_TITLES"):
        assert forbidden not in text


def test_candidate_skill_repeats_crm_customer_citation_in_project_evidence():
    text = (ROOT / "ci/shared-skills/ceo-work-tracking/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "repeat that identical citation in the Project's `evidence` list" in " ".join(
        text.split()
    )


def test_task_agent_prompt_builder_contains_transport_not_business_policy():
    source = inspect.getsource(build_task_agent_prompt)

    assert "load_skill_text" in source
    assert "task_agent_output_schema" in source
    for duplicated_policy in (
        "流程性内容默认忽略",
        "owner_user_id 不能靠猜",
        "参与人、发言人、转述人",
        "P0 今天跟进",
        "小青显示",
    ):
        assert duplicated_policy not in source


def test_task_agent_output_schema_matches_the_pydantic_contract():
    schema_path = ROOT / "app" / "schemas" / "task_agent_decision.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema == task_agent_output_schema()

    def assert_strict_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                properties = value.get("properties", {})
                assert value.get("additionalProperties") is False
                assert value.get("required", []) == list(properties)
            for nested in value.values():
                assert_strict_objects(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_strict_objects(nested)

    assert_strict_objects(schema)


def test_legacy_completion_fields_are_not_current_skill_operations():
    decision_fields = TaskAgentDecision.model_fields
    task_decision_fields = TaskDecision.model_fields
    assert "todo_changes" in decision_fields
    assert "follow_up_changes" in decision_fields
    assert "todo_changes" not in task_decision_fields
    assert "follow_up_changes" not in task_decision_fields

    text = " ".join(_skill_text().split())
    assert (
        "todo_changes, follow_up_changes, search_trace and old completion-check source enums "
        "are historical, not current operations."
    ) in text


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
    with pytest.raises(ValidationError, match="source excerpt .* and reference"):
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
