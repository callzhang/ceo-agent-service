from __future__ import annotations

from dataclasses import replace
import json

import pytest

from app.agent_cron.context import ScheduledAgentContextBuilder
from app.agent_cron.context import ScheduledAgentContext
from app.audit_web import refresh_scheduled_rerun_skill_snapshot
from app.audit_web import handle_rerun_attempt_post
from app.store import AutoReplyStore, ManualRerunConflict
from tests.support.native_task_fields import set_native_task_fields
from tests.test_scheduled_agent_consumer import fixture as scheduled_fixture


def test_scheduled_manual_rerun_refreshes_only_the_selected_skill_snapshot(tmp_path):
    store, run, options = scheduled_fixture(tmp_path)
    builder = ScheduledAgentContextBuilder(options)
    original = builder.build(run, reply_task_id=417543)
    original_json = original.to_execution_json()
    current = store.get_scheduled_task(run.scheduled_task_id)
    assert current is not None
    old_ref = run.snapshot.skill_refs[0]
    revision = store.create_managed_skill_revision(
        old_ref.managed_skill_id,
        "---\nname: managed-check\ndescription: updated\nmetadata:\n  managed_by: ceo-agent-service\n---\n\nUPDATED MANAGED BODY\n",
        source="settings",
    )
    current_refs = (
        replace(old_ref, managed_revision_id=revision.id),
        *current.skill_refs[1:],
    )
    current = store.update_scheduled_task(
        current.id, expected_version=current.version, skill_refs=current_refs, now=run.scheduled_for
    )
    options.managed = revision

    refreshed_json = refresh_scheduled_rerun_skill_snapshot(
        original_json,
        store=store,
        skill_options=options,
        reply_task_id=417543,
    )
    refreshed = ScheduledAgentContext.from_execution_json(
        refreshed_json,
        reply_task_id=417543,
    )

    assert refreshed.context.trigger_raw_payload["scheduled_task_run_id"] == run.id
    assert refreshed.context.trigger_raw_payload["scheduled_for"] == run.scheduled_for.isoformat()
    assert refreshed.context.trigger_raw_payload["scheduled_task_version"] == run.snapshot.task_version
    assert refreshed.context.trigger_raw_payload["skills"][0]["revision_id"] == revision.id
    assert "UPDATED MANAGED BODY" in refreshed.skill_materials[0].content
    assert refreshed.route == original.route
    assert refreshed.context.trigger_text == original.context.trigger_text
    assert original.to_execution_json() == original_json


def test_manual_scheduled_rerun_budget_is_per_exact_input_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    source = {
        "channel": "scheduled",
        "conversation_id": "scheduled-task-run:408515",
        "conversation_title": "CEO 每日总结",
        "single_chat": False,
        "trigger_message_id": "run-408515",
        "trigger_create_time": "2026-10-26T13:00:00+00:00",
        "trigger_sender": "Agent Cron",
        "trigger_text": "生成 CEO 每日总结",
    }
    attempt_id = store.record_reply_attempt(
        channel="scheduled",
        conversation_id=source["conversation_id"],
        conversation_title=source["conversation_title"],
        trigger_message_id=source["trigger_message_id"],
        trigger_sender=source["trigger_sender"],
        trigger_text=source["trigger_text"],
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )

    def rerun(skill_revision: int):
        return store.enqueue_manual_rerun_reply_task(
            **source,
            trigger_message_json=json.dumps(
                {"schema": "scheduled_agent_execution.v1", "skill_revision": skill_revision}
            ),
            oa_url="",
            attempt_id=attempt_id,
        )

    for _ in range(3):
        task = rerun(267)
        with store._connect() as db:
            set_native_task_fields(db, "reply_tasks", task.id, {"status": "failed"})

    with pytest.raises(ManualRerunConflict, match="three manual retries"):
        rerun(267)

    revised = rerun(277)
    assert revised.status == "pending"
    assert revised.manual_rerun_attempt_id == attempt_id


def test_history_rerun_queues_the_refreshed_input_for_the_original_occurrence(tmp_path):
    store, run, options = scheduled_fixture(tmp_path)
    original = ScheduledAgentContextBuilder(options).build(run, reply_task_id=0)
    old_json = original.to_execution_json()
    conversation_id = original.context.conversation_id
    store.enqueue_reply_task(
        channel="scheduled",
        conversation_id=conversation_id,
        conversation_title=original.context.conversation_title,
        single_chat=False,
        trigger_message_id=original.context.trigger_message_id,
        trigger_create_time=original.context.trigger_create_time,
        trigger_sender=original.context.trigger_sender,
        trigger_text=original.context.trigger_text,
        trigger_message_json=old_json,
    )
    task = store.get_reply_task_for_message(
        conversation_id,
        original.context.trigger_message_id,
        channel="scheduled",
    )
    assert task is not None
    with store._connect() as db:
        set_native_task_fields(db, "reply_tasks", task.id, {"status": "failed"})
    attempt_id = store.record_reply_attempt(
        channel="scheduled",
        conversation_id=conversation_id,
        conversation_title=original.context.conversation_title,
        trigger_message_id=original.context.trigger_message_id,
        trigger_sender=original.context.trigger_sender,
        trigger_text=original.context.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    selected = store.get_scheduled_task(run.scheduled_task_id)
    assert selected is not None
    old_ref = run.snapshot.skill_refs[0]
    revision = store.create_managed_skill_revision(
        old_ref.managed_skill_id,
        "---\nname: managed-check\ndescription: updated\nmetadata:\n  managed_by: ceo-agent-service\n---\n\nUPDATED MANAGED BODY\n",
        source="settings",
    )
    selected = store.update_scheduled_task(
        selected.id,
        expected_version=selected.version,
        skill_refs=(replace(old_ref, managed_revision_id=revision.id), *selected.skill_refs[1:]),
        now=run.scheduled_for,
    )
    options.managed = revision

    status, headers, body = handle_rerun_attempt_post(
        store,
        attempt_id,
        skill_options_factory=lambda: options,
    )

    assert (status, headers["Location"], body) == (
        303,
        f"/attempts/{attempt_id}",
        "",
    )
    rerun = store.get_reply_task(task.id)
    assert rerun is not None and rerun.status == "pending"
    assert rerun.trigger_message_json != old_json
    refreshed = ScheduledAgentContext.from_execution_json(
        rerun.trigger_message_json,
        reply_task_id=rerun.id,
    )
    assert refreshed.context.trigger_raw_payload["scheduled_task_run_id"] == run.id
    assert refreshed.context.trigger_raw_payload["scheduled_for"] == run.scheduled_for.isoformat()
    assert refreshed.context.trigger_raw_payload["skills"][0]["revision_id"] == revision.id
    assert "UPDATED MANAGED BODY" in refreshed.skill_materials[0].content
