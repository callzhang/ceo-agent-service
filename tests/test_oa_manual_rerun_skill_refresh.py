import json
from types import SimpleNamespace

import pytest

from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.agent_cron.models import ScheduledTaskSkillRef
from app.audit_web import (
    handle_reviewed_message_reply,
    handle_rerun_attempt_post,
    refresh_oa_rerun_skill_snapshot,
)
from app.dws_client import DingTalkMessage
from app.store import AutoReplyStore


class Options:
    def list_managed_skill_options(self):
        return (
            SimpleNamespace(
                skill_id=7,
                name="stardust-oa-people-review",
                revisions=(
                    SimpleNamespace(revision_id=11, revision_number=1, available=True),
                    SimpleNamespace(revision_id=12, revision_number=2, available=True),
                ),
            ),
        )

    def resolve_managed_skill_revision(self, *, skill_id, revision_id, skill_name):
        return SimpleNamespace(
            id=revision_id,
            skill_id=skill_id,
            sha256=f"sha-{revision_id}",
            content=f"People rules revision {revision_id}",
        )

    def resolve_operation_skill(self, name):
        return SimpleNamespace(
            path=f"/skills/{name}/SKILL.md",
            sha256="current-operation-sha",
            content="current operation Skill body",
        )


def test_manual_oa_rerun_binds_latest_selected_skill_without_mutating_prior_input():
    prior_context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review this OA event.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="stardust-oa-people-review", content="old people rules revision 11"
            ),
        ),
        skill_protocol_source="generated_discovery",
    )
    original = {
        "raw_payload": {
            "source_field": "retain",
            "scheduled_consumer": prior_context.to_payload(),
        },
        "message_id": "trigger-1",
    }

    refreshed_json = refresh_oa_rerun_skill_snapshot(
        json.dumps(original),
        store=SimpleNamespace(
            get_scheduled_task=lambda task_id: SimpleNamespace(
                id=task_id,
                command="scan-oa-approvals",
                prompt="Review this OA event.",
                skill_refs=(
                    ScheduledTaskSkillRef(
                        skill_source="managed",
                        skill_name="stardust-oa-people-review",
                        managed_skill_id=7,
                        managed_revision_id=11,
                        position=0,
                    ),
                ),
            )
        ),
        skill_options=Options(),
    )
    refreshed = json.loads(refreshed_json)
    new_context = ServiceCommandConsumerContext.from_payload(
        refreshed["raw_payload"]["scheduled_consumer"]
    )

    assert new_context is not None
    assert new_context.skill_materials[0].content.endswith("People rules revision 12")
    assert refreshed["raw_payload"]["source_field"] == "retain"
    assert refreshed["message_id"] == "trigger-1"
    assert prior_context.skill_materials[0].content == "old people rules revision 11"


def test_manual_oa_rerun_refuses_to_copy_a_snapshot_when_refresh_is_unavailable():
    original = json.dumps({"raw_payload": {"source_field": "retain"}})

    with pytest.raises(ValueError, match="OA rerun Skill snapshot"):
        refresh_oa_rerun_skill_snapshot(
            original,
            store=SimpleNamespace(get_scheduled_task=lambda _: None),
            skill_options=None,
        )


def test_reviewed_oa_supplement_refreshes_selected_skill_snapshot_before_queueing():
    ref = ScheduledTaskSkillRef(
        skill_source="managed",
        skill_name="stardust-oa-people-review",
        managed_skill_id=7,
        managed_revision_id=11,
        position=0,
    )
    context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review this OA event.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(ServiceCommandSkillMaterial(
            name="stardust-oa-people-review",
            content="old people rules revision 11",
        ),),
        skill_protocol_source="generated_discovery",
    )
    message = DingTalkMessage(
        open_conversation_id="oa_pending_scan:proc-1",
        open_message_id="oa-pending:proc-1:revision-1:2026-10-19",
        conversation_title="审批待办",
        single_chat=True,
        sender_name="Derek OA",
        create_time="2026-10-19T10:00:00Z",
        content="Review this OA process.",
        raw_payload={"source": "oa_pending_scan", "scheduled_consumer": context.to_payload()},
    )
    task = SimpleNamespace(
        conversation_id=message.open_conversation_id,
        conversation_title=message.conversation_title,
        single_chat=message.single_chat,
        trigger_message_id=message.open_message_id,
        trigger_create_time=message.create_time,
        trigger_sender=message.sender_name,
        trigger_text=message.content,
        trigger_message_json=message.model_dump_json(),
        input_compacted=False,
    )
    attempt = SimpleNamespace(
        id=15739,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        trigger_create_time=task.trigger_create_time,
        channel="dingtalk",
        action="send_reply",
        oa_process_instance_id="proc-1",
        reviewer_feedback="Apply the current OA Skill.",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    scheduled_task = SimpleNamespace(
        id=3,
        command="scan-oa-approvals",
        prompt=context.prompt,
        skill_refs=(ref,),
    )
    captured = {}
    store = SimpleNamespace(
        get_reply_attempt=lambda attempt_id: attempt if attempt_id == attempt.id else None,
        get_reply_task_for_message=lambda *_args, **_kwargs: task,
        get_scheduled_task=lambda task_id: scheduled_task if task_id == 3 else None,
        record_actionable_attempt_decision=lambda _attempt_id, **kwargs: (
            captured.update(kwargs) or (attempt.id, task)
        ),
    )

    handle_reviewed_message_reply(
        store,
        attempt_id=attempt.id,
        reply_text="",
        reviewer_feedback="Apply the current OA Skill.",
        actionable_source_attempt_id=attempt.id,
        review_candidate_id=1077,
        supplement_instruction="Apply the current OA Skill.",
        skill_options_factory=Options,
    )

    refreshed = json.loads(captured["trigger_message_json"])
    new_context = ServiceCommandConsumerContext.from_payload(
        refreshed["raw_payload"]["scheduled_consumer"]
    )
    assert new_context is not None
    assert new_context.skill_materials[0].content.endswith("People rules revision 12")


def test_compacted_oa_scan_rerun_restores_saved_run_without_reading_synthetic_message(
    monkeypatch,
):
    ref = ScheduledTaskSkillRef(
        skill_source="managed", skill_name="stardust-oa-people-review",
        managed_skill_id=7, managed_revision_id=11, position=0,
    )
    prior_context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review current OA approvals.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(ServiceCommandSkillMaterial(
            name="stardust-oa-people-review", content="old people rules revision 11"
        ),),
        skill_protocol_source="generated_discovery",
    )
    original = DingTalkMessage(
        open_conversation_id="oa_pending_scan:proc-1",
        open_message_id="oa-pending:proc-1:revision-1:2026-10-19",
        conversation_title="审批待办", single_chat=True, sender_name="Derek OA",
        create_time="2026-10-19T10:00:00Z", content="Review this OA process.",
        raw_payload={
            "source": "oa_pending_scan", "processInstanceId": "proc-1",
            "taskId": "task-1", "scheduled_consumer": prior_context.to_payload(),
        },
    )
    compacted = AutoReplyStore._compact_reply_input_json(
        AutoReplyStore._reply_input_provenance("Review this OA process.", original.model_dump_json())
    )
    task = SimpleNamespace(
        id=405350, channel="dingtalk", conversation_id="oa_pending_scan:proc-1",
        conversation_title="审批待办", single_chat=True,
        trigger_message_id=original.open_message_id,
        trigger_create_time=original.create_time, trigger_sender="Derek OA",
        trigger_text="", trigger_message_json=compacted, input_compacted=True,
        business_object_key="oa:proc-1:task-1", oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        execution_generation="generation-1",
    )
    attempt = SimpleNamespace(
        id=15716, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender="Derek OA",
        trigger_text="", channel="dingtalk", action="agent_run", agent_run_id=None,
        resolved_at="", send_status="done", send_error="", codex_reason="",
        oa_process_instance_id="proc-1", oa_url=task.oa_url,
    )
    refs = (ref,)
    scheduled_run = SimpleNamespace(
        id=20,
        snapshot=SimpleNamespace(
            task_id=3, prompt="Review current OA approvals.", skill_refs=refs,
        ),
    )
    scheduled_task = SimpleNamespace(
        id=3, command="scan-oa-approvals", prompt="Review current OA approvals.",
        skill_refs=refs,
    )
    captured = {}
    store = SimpleNamespace(
        get_reply_attempt=lambda attempt_id: attempt if attempt_id == attempt.id else None,
        get_reply_task_for_message=lambda *_args, **_kwargs: task,
        get_conversation=lambda *_args: None,
        get_agent_run=lambda *_args, **_kwargs: None,
        get_scheduled_task_run=lambda run_id: scheduled_run if run_id == 20 else None,
        get_scheduled_task=lambda task_id: scheduled_task if task_id == 3 else None,
        enqueue_manual_rerun_reply_task=lambda **kwargs: captured.update(kwargs),
    )

    def forbidden_provider_read(*_args, **_kwargs):
        raise AssertionError("scanner-generated OA IDs are not provider message IDs")

    monkeypatch.setattr("app.audit_web.DwsClient.read_message_by_id", forbidden_provider_read)
    status, _, _ = handle_rerun_attempt_post(
        store, attempt.id, skill_options_factory=Options,
    )

    rerun = json.loads(captured["trigger_message_json"])
    consumer = ServiceCommandConsumerContext.from_payload(
        rerun["raw_payload"]["scheduled_consumer"]
    )
    assert status == 303
    assert captured["trigger_message_id"] == task.trigger_message_id
    assert captured["expected_execution_generation"] == "generation-1"
    assert rerun["raw_payload"]["source"] == "oa_pending_scan"
    assert rerun["raw_payload"]["processInstanceId"] == "proc-1"
    assert rerun["raw_payload"]["taskId"] == "task-1"
    assert consumer is not None
    assert consumer.skill_materials[0].content.endswith("People rules revision 12")


def test_reviewer_feedback_can_reopen_skipped_oa_review_only_while_same_task_is_pending(
    monkeypatch,
):
    ref = ScheduledTaskSkillRef(
        skill_source="managed", skill_name="stardust-oa-people-review",
        managed_skill_id=7, managed_revision_id=11, position=0,
    )
    prior_context = ServiceCommandConsumerContext(
        scheduled_task_id=3, scheduled_task_run_id=20,
        prompt="Review current OA approvals.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(ServiceCommandSkillMaterial(
            name="stardust-oa-people-review", content="old people rules revision 11"
        ),), skill_protocol_source="generated_discovery",
    )
    original = DingTalkMessage(
        open_conversation_id="oa_pending_scan:proc-1",
        open_message_id="oa-pending:proc-1:revision-1:2026-10-19",
        conversation_title="审批待办", single_chat=True, sender_name="Derek OA",
        create_time="2026-10-19T10:00:00Z", content="Review this OA process.",
        raw_payload={
            "source": "oa_pending_scan", "processInstanceId": "proc-1",
            "taskId": "task-1", "scheduled_consumer": prior_context.to_payload(),
        },
    )
    compacted = AutoReplyStore._compact_reply_input_json(
        AutoReplyStore._reply_input_provenance("Review this OA process.", original.model_dump_json())
    )
    task = SimpleNamespace(
        id=405350, channel="dingtalk", conversation_id="oa_pending_scan:proc-1",
        conversation_title="审批待办", single_chat=True,
        trigger_message_id=original.open_message_id,
        trigger_create_time=original.create_time, trigger_sender="Derek OA",
        trigger_text="", trigger_message_json=compacted, input_compacted=True,
        business_object_key="oa:proc-1:task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        execution_generation="generation-1", status="done",
    )
    attempt = SimpleNamespace(
        id=15716, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender="Derek OA",
        trigger_text="", channel="dingtalk", action="agent_run", agent_run_id=None,
        resolved_at="", resolution="no_action",
        send_status="skipped",
        send_error="", codex_reason="", reviewer_feedback="Re-review using the supplied business evidence.",
        oa_process_instance_id="proc-1", oa_task_id="task-1", oa_action="review",
        oa_action_result_json="", oa_url=task.oa_url,
    )
    refs = (ref,)
    scheduled_run = SimpleNamespace(
        id=20, snapshot=SimpleNamespace(
            task_id=3, prompt="Review current OA approvals.", skill_refs=refs,
        ),
    )
    scheduled_task = SimpleNamespace(
        id=3, command="scan-oa-approvals", prompt="Review current OA approvals.",
        skill_refs=refs,
    )
    captured = {}
    store = SimpleNamespace(
        get_reply_attempt=lambda attempt_id: attempt if attempt_id == attempt.id else None,
        get_reply_task_for_message=lambda *_args, **_kwargs: task,
        get_conversation=lambda *_args: None,
        get_agent_run=lambda *_args, **_kwargs: None,
        get_scheduled_task_run=lambda run_id: scheduled_run if run_id == 20 else None,
        get_scheduled_task=lambda task_id: scheduled_task if task_id == 3 else None,
        enqueue_manual_rerun_reply_task=lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        "app.audit_web.DwsClient.read_message_by_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OA review reruns must restore saved context")
        ),
    )

    from app.history_actions import can_reopen_skipped_oa_review

    assert can_reopen_skipped_oa_review(attempt, task)

    status, _, _ = handle_rerun_attempt_post(
        store, attempt.id, skill_options_factory=Options,
    )

    assert status == 303
    assert captured["attempt_id"] == attempt.id
    assert captured["expected_execution_generation"] == "generation-1"


def test_resolved_terminal_oa_review_cannot_be_reopened_from_history():
    from app.history_actions import can_reopen_skipped_oa_review

    attempt = SimpleNamespace(
        send_status="skipped",
        reviewer_feedback="Review this again.",
        action="agent_run",
        oa_action="review",
        oa_action_result_json="",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        resolved_at="2026-10-24 18:00:00",
        resolution=(
            "Live DingTalk OA records verified process proc-1 terminal operation AGREE; "
            "no further service action is permitted."
        ),
    )
    task = SimpleNamespace(
        channel="dingtalk",
        conversation_id="oa_pending_scan:proc-1",
        business_object_key="oa:proc-1:task-1",
        status="done",
    )

    assert not can_reopen_skipped_oa_review(attempt, task)


def test_skipped_oa_review_without_feedback_cannot_be_reopened():
    attempt = SimpleNamespace(
        id=15716, conversation_id="oa_pending_scan:proc-1",
        trigger_message_id="oa-pending:proc-1:revision-1:2026-10-19",
        channel="dingtalk", action="agent_run", agent_run_id=None,
        resolved_at="", send_status="skipped",
        reviewer_feedback="", oa_process_instance_id="proc-1",
        oa_task_id="task-1", oa_action="review", oa_action_result_json="",
    )
    task = SimpleNamespace(
        channel="dingtalk", conversation_id="oa_pending_scan:proc-1",
        business_object_key="oa:proc-1:task-1", status="skipped",
    )

    from app.history_actions import can_reopen_skipped_oa_review

    assert not can_reopen_skipped_oa_review(attempt, task)


def test_manual_oa_rerun_rotates_pending_generation_when_refreshed_skill_changes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid", title="OA", single_chat=False)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid", conversation_title="OA", trigger_message_id="trigger",
        trigger_sender="Submitter", trigger_text="Review this OA", action="agent_run",
        sensitivity_kind="normal", send_status="failed",
    )
    store.update_reply_attempt(
        attempt_id, oa_process_instance_id="instance", send_error="codex_result_invalid"
    )
    original_context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review this OA event.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(ServiceCommandSkillMaterial(
            name="stardust-oa-people-review", content="old people rules revision 11"
        ),),
        skill_protocol_source="generated_discovery",
    )
    trigger = DingTalkMessage(
        open_conversation_id="cid", open_message_id="trigger", conversation_title="OA",
        single_chat=False, sender_name="Submitter", create_time="2026-10-16 10:00:00",
        content="Review this OA",
    ).model_dump()
    trigger["raw_payload"] = {"scheduled_consumer": original_context.to_payload()}
    trigger_json = json.dumps(trigger)
    task_id = store.ensure_reply_task(
        conversation_id="cid", conversation_title="OA", single_chat=False,
        trigger_message_id="trigger", trigger_create_time="2026-10-16 10:00:00",
        trigger_sender="Submitter", trigger_text="Review this OA",
        trigger_message_json=trigger_json,
    )
    store.enqueue_manual_rerun_reply_task(
        conversation_id="cid", conversation_title="OA", single_chat=False,
        trigger_message_id="trigger", trigger_create_time="2026-10-16 10:00:00",
        trigger_sender="Submitter", trigger_text="Review this OA",
        trigger_message_json=trigger_json, attempt_id=attempt_id,
    )
    current = store.get_reply_task(task_id.id)
    claimed = store.claim_reply_task(task_id.id)
    assert claimed.execution_generation == current.execution_generation
    store.get_scheduled_task = lambda _: SimpleNamespace(
        id=3,
        command="scan-oa-approvals",
        prompt="Review this OA event.",
        skill_refs=(ScheduledTaskSkillRef(
            skill_source="managed", skill_name="stardust-oa-people-review",
            managed_skill_id=7, managed_revision_id=11, position=0,
        ),),
    )

    status, _, _ = handle_rerun_attempt_post(
        store, attempt_id, skill_options_factory=Options,
    )

    rerun = store.get_reply_task(task_id.id)
    assert status == 303
    assert rerun.execution_generation != current.execution_generation
    assert rerun.status == "pending"
    refreshed = ServiceCommandConsumerContext.from_payload(
        json.loads(rerun.trigger_message_json)["raw_payload"]["scheduled_consumer"]
    )
    assert refreshed is not None
    assert refreshed.skill_materials[0].content.endswith("People rules revision 12")
