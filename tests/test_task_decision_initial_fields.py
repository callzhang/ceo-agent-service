import pytest
from pydantic import ValidationError

from app.task_models import TaskDecision


@pytest.mark.parametrize("transition", ["none", "update_fields"])
def test_new_task_lifecycle_fields_report_that_the_fields_must_be_omitted(transition):
    source_ref = "minutes:meeting-1#todos-sha256=abc123"
    decision = {
        "action": "create_task",
        "transition": transition,
        "source_excerpt": "行动项：张三完成验收清单。",
        "source_ref": source_ref,
        "title": "完成验收清单",
        "formal_basis": "meeting_action_item",
        "status": "open",
        "business_relevance": "relevant",
    }

    with pytest.raises(ValidationError) as exc_info:
        TaskDecision.model_validate(decision)

    assert "new/skip decisions must omit status and business_relevance" in str(
        exc_info.value
    )
    assert decision["source_ref"] == source_ref
