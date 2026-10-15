"""The task snapshot, live conversation and service bindings have distinct owners."""

from dataclasses import replace
import json

from app.agent_context import AgentContextMessage, AgentTaskContext, AuditTurnContext, MaterialReference
from app.reviewed_sources import capture_candidate_sources
from tests.test_reviewed_orchestration import candidate


def _prompt_json_section(prompt: str, title: str):
    section = prompt.split(f"\n{title}\n", 1)[1]
    return json.JSONDecoder().raw_decode(section)[0]


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

    authority = _prompt_json_section(rendered, "Task trigger authority")
    provenance = _prompt_json_section(rendered, "Task input provenance")
    messages = _prompt_json_section(rendered, "Recent conversation context")
    materials = _prompt_json_section(rendered, "Raw material references and exact read commands")
    assert authority["reply_target_message_id"] == "original-trigger"
    assert "this original request has already been resolved" in rendered
    assert provenance["snapshot_source"] == "queued_task_input"
    assert provenance["context_source"] == "recent_conversation_read"
    assert provenance["same_id_context_messages"] == [
        {"message_id": "original-trigger", "text_matches_frozen_trigger": False}
    ]
    assert any(message["message_id"] == "later-request" for message in messages)
    assert any(material["reference"] == "task_id=104336481142" for material in materials)
    assert context.trigger_text == "Approval card: task 104336481142"
    assert context.trigger_raw_payload == {"card": "task 104336481142"}


def test_message_order_is_computed_from_instants_and_preserves_reply_target():
    rendered = _context().render_business_context(current_time="2026-10-14T07:50:00Z")

    times = _prompt_json_section(rendered, "Canonical time facts")["message_create_times"]
    by_message = {item["message_id"]: item for item in times}
    assert by_message["service-reply"]["seconds_from_trigger"] == 319
    assert by_message["later-request"]["seconds_from_trigger"] == 401
    assert _prompt_json_section(rendered, "Task trigger authority")["reply_target_message_id"] == "original-trigger"


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

    times = _prompt_json_section(rendered, "Canonical time facts")["message_create_times"]
    by_message = {item["message_id"]: item for item in times}
    assert by_message["reply"]["seconds_from_trigger"] == 319
    assert by_message["later"]["seconds_from_trigger"] == 401
    assert by_message["unknown"]["create_time"]["status"] == "unparseable"
    assert by_message["unknown"]["seconds_from_trigger"] is None


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

    revision = _prompt_json_section(rendered, "Candidate revision")
    assert any(binding["provider"] == "task_context" for binding in revision["candidate"]["source_bindings"])
    provenance = _prompt_json_section(rendered, "Service-captured source binding provenance")
    assert provenance["managed_by"] == "service"
    assert "Consumer cannot edit source_bindings" in rendered
    assert "current card" in rendered
