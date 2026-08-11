import inspect
from pathlib import Path

from app.task_agent import build_task_agent_prompt


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
