import json
from types import SimpleNamespace

import pytest

from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.agent_cron.models import ScheduledTaskSkillRef
from app.audit_web import handle_rerun_attempt_post, refresh_oa_rerun_skill_snapshot
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


def test_manual_oa_rerun_rotates_pending_generation_when_refreshed_skill_changes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid", title="OA", single_chat=False, codex_session_id="")
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
