"""The task snapshot, live conversation and service bindings have distinct owners."""

from dataclasses import replace

from app.agent_context import AgentContextMessage, AgentTaskContext, AuditTurnContext, MaterialReference
from app.reviewed_sources import capture_candidate_sources
from tests.test_reviewed_orchestration import candidate


def _context() -> AgentTaskContext:
    return AgentTaskContext(
        task_id=15452,
        channel="dingtalk",
        conversation_id="conversation",
        conversation_title="Review",
        single_chat=True,
        trigger_message_id="original-trigger",
        trigger_sender="Derek",
        trigger_text="Approval card: task 104336481142",
        trigger_create_time="2026-10-14 14:41:07",
        trigger_raw_payload={"card": "task 104336481142"},
        messages=(
            AgentContextMessage(
                message_id="original-trigger",
                sender="Derek",
                text="Approval card: task 104377448561",
                create_time="2026-10-14 14:41:07",
            ),
            AgentContextMessage(
                message_id="service-reply",
                sender="Service",
                text="Reply to original request",
                create_time="2026-10-14 14:46:26",
            ),
            AgentContextMessage(
                message_id="later-request",
                sender="Derek",
                text="Please also check this other request",
                create_time="2026-10-14 14:47:48",
            ),
        ),
        materials=(
            MaterialReference(
                kind="dingtalk_oa",
                reference="task_id=104336481142",
                source_message_id="original-trigger",
                read_commands=(),
            ),
        ),
        prior_receipts=(),
    )


def test_same_message_id_card_update_is_labeled_against_frozen_reply_target():
    context = _context()
    rendered = context.render_business_context(current_time="2026-10-14T07:50:00Z")

    assert '"reply_target_message_id": "original-trigger"' in rendered
    assert "this original request has already been resolved" in rendered
    assert '"snapshot_source": "queued_task_input"' in rendered
    assert '"context_source": "recent_conversation_read"' in rendered
    assert '"text_matches_frozen_trigger": false' in rendered
    assert '"message_id": "later-request"' in rendered
    assert '"reference": "task_id=104336481142"' in rendered
    assert context.trigger_text == "Approval card: task 104336481142"
    assert context.trigger_raw_payload == {"card": "task 104336481142"}


def test_message_order_is_computed_from_instants_and_preserves_reply_target():
    rendered = _context().render_business_context(current_time="2026-10-14T07:50:00Z")

    assert '"message_id": "service-reply"' in rendered
    assert '"seconds_from_trigger": 319' in rendered
    assert '"message_id": "later-request"' in rendered
    assert '"seconds_from_trigger": 401' in rendered
    assert '"reply_target_message_id": "original-trigger"' in rendered


def test_mixed_timestamp_offsets_and_unknown_time_do_not_invent_order():
    context = replace(
        _context(),
        trigger_create_time="2026-10-14T06:41:07Z",
        messages=(
            AgentContextMessage(
                message_id="reply",
                sender="Service",
                text="Done",
                create_time="2026-10-14 14:46:26",
            ),
            AgentContextMessage(
                message_id="later",
                sender="Derek",
                text="Another task",
                create_time="2026-10-14T06:47:48+00:00",
            ),
            AgentContextMessage(
                message_id="unknown",
                sender="Derek",
                text="Unknown time",
                create_time="not-a-timestamp",
            ),
        ),
    )
    rendered = context.render_business_context(current_time="2026-10-14T07:50:00Z")

    assert '"seconds_from_trigger": 319' in rendered
    assert '"seconds_from_trigger": 401' in rendered
    assert '"status": "unparseable"' in rendered
    assert '"seconds_from_trigger": null' in rendered


def test_audit_identifies_source_bindings_as_service_captured_evidence():
    context = _context()
    consumer_result = candidate()
    assert consumer_result.source_bindings == ()
    prepared = capture_candidate_sources(consumer_result, context, None)
    assert prepared.source_bindings[0].value["trigger_text"] == context.trigger_text
    assert prepared.source_bindings[0].value["messages"][0]["text"] == context.messages[0].text
    assert consumer_result.source_bindings == ()
    rendered = AuditTurnContext(
        task=context,
        proposal_revision=1,
        operation_id="operation",
        candidate=prepared,
        candidate_digest="a" * 64,
        audit_rules="Review the current request.",
    ).render(current_time="2026-10-14T07:50:00Z")

    assert '"provider": "task_context"' in rendered
    assert '"managed_by": "service"' in rendered
    assert "Consumer cannot edit source_bindings" in rendered
    assert "current card" in rendered
