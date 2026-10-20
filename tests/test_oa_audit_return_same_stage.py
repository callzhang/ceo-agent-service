import pytest

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditFeedback


@pytest.mark.parametrize(
    ("stage_index", "predecessor_review_id"),
    ((0, None), (2, 417)),
)
def test_audit_feedback_requires_replacement_at_current_stage(
    stage_index, predecessor_review_id
):
    context = AgentTaskContext(
        task_id=7,
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="Test",
        single_chat=False,
        trigger_message_id="mid",
        trigger_sender="ET",
        trigger_create_time="2026-10-20 12:00:00",
        trigger_text="Review",
        messages=(),
        materials=(),
        prior_receipts=(),
        stage_index=stage_index,
        predecessor_review_id=predecessor_review_id,
    )
    feedback = AuditFeedback(
        rule="Complete current candidate",
        observation="Candidate is incomplete",
        requested_revision="Replace the complete candidate",
    )

    rendered = context.render(proposal_revision=1, feedback=feedback)

    assert "An Audit return does not advance the stage" in rendered
    assert "keep the exact service-bound stage_index and predecessor_review_id" in rendered
    assert '"stage_index":' + str(stage_index) in rendered
    expected_predecessor = (
        "null" if predecessor_review_id is None else str(predecessor_review_id)
    )
    assert '"predecessor_review_id":' + expected_predecessor in rendered
