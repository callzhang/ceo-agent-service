"""Fast native repair contracts retained in the fixed deployment gate."""

from types import SimpleNamespace

import pytest

from app.action_storage import insert_action
from app.automatic_repair import AutomaticRepairCoordinator, repair_presentation
from app.automatic_repair_store import enqueue_repair, get_repair, list_repairs
from app.workbench.store import WorkbenchStore

@pytest.fixture
def store(tmp_path, monkeypatch):
    wb = WorkbenchStore(tmp_path / 'service.sqlite3')
    wb.test_native_text = {}
    original = wb.get_turn
    def native_turn(turn_id):
        turn = original(turn_id)
        if turn is not None:
            turn.final_text = wb.test_native_text.get(turn_id, '')
        return turn
    monkeypatch.setattr(wb, 'get_turn', native_turn)
    return wb


def test_recorded_error_transactionally_enqueues_once(store):
    store.record_error(None, None, 'test_failure', 'actual diagnostic failure')
    error = store.list_errors(limit=1)[0]
    entry = get_repair(store, 'errors', error.id)
    assert entry['status'] == 'queued'
    enqueue_repair(store, 'errors', error.id)
    assert len(list_repairs(store)) == 1


def coordinator(store, tmp_path, prs=None):
    return AutomaticRepairCoordinator(
        store, repository=tmp_path / 'dev',
        pr_reader=lambda branch: prs or [], interval_seconds=0,
    )


def finish(store, turn_id, text='I will implement next', status='completed'):
    store.test_native_text[turn_id] = text
    turn = store.claim_next_turn(owner='test', lease_seconds=300)
    assert turn.id == turn_id
    store.complete_turn(turn.id, owner='test', status=status, final_text=text)


def test_restart_resumes_same_conversation_and_uses_idempotent_turn(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    first = coordinator(store, tmp_path)
    first.run_once()
    entry = get_repair(store, 'errors', error.id)
    assert entry['task_id'] and len(entry['turn_ids']) == 1
    coordinator(store, tmp_path).run_once()
    assert get_repair(store, 'errors', error.id) == entry
    finish(store, entry['turn_ids'][0])
    coordinator(store, tmp_path).run_once()
    continued = get_repair(store, 'errors', error.id)
    assert continued['task_id'] == entry['task_id']
    assert len(continued['turn_ids']) == 2
    assert store.get_turn(continued['turn_ids'][1]).client_request_id.endswith(':2')
    assert '/history/errors/' in store.get_turn(continued['turn_ids'][1]).user_text


def test_repair_handoff_uses_public_attempt_identity_when_physical_id_collides(store, tmp_path):
    with store._connect() as db:
        def insert(public_id, title):
            insert_action(db, "reply_attempts", {
                "id": public_id, "conversation_id": title,
                "conversation_title": title, "trigger_message_id": "message",
                "trigger_sender": "sender", "trigger_text": "request",
                "action": "reply", "sensitivity_kind": "normal", "send_status": "failed",
            })

        insert(9800, "unrelated old attempt")
        public_id = db.execute(
            "select id from actions where record_type='reply_attempts' and legacy_key='9800'"
        ).fetchone()[0]
        assert public_id != 9800
        insert(public_id, "intended attempt")

    assert store.get_reply_attempt(public_id).conversation_title == "intended attempt"
    enqueue_repair(store, "reply_attempts", public_id)
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, "reply_attempts", public_id)
    prompt = store.get_turn(entry["turn_ids"][0]).user_text

    assert f"http://127.0.0.1:8765/api/console/history/{public_id}" in prompt
    assert f"record_type='reply_attempts' AND legacy_key='{public_id}'" in prompt
    assert "public Attempt ID" in prompt
    assert "current source state" in prompt


def test_repair_handoff_uses_current_service_error_detail(store, tmp_path):
    store.record_error(None, None, "test_failure", "failed")
    source = store.list_errors(limit=1)[0]
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, "errors", source.id)
    prompt = store.get_turn(entry["turn_ids"][0]).user_text

    assert f"http://127.0.0.1:8765/api/console/history/errors/{source.id}" in prompt
    assert "public Attempt ID" not in prompt


def test_actual_remote_pr_is_linked_even_if_agent_final_was_interrupted(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    finish(store, entry['turn_ids'][0])
    receipt = {'url': 'https://github.com/example/service/pull/9',
               'headRefOid': 'abc', 'state': 'OPEN'}
    coordinator(store, tmp_path, [receipt]).run_once()
    result = repair_presentation(store, 'errors', error.id)
    assert result['status'] == 'pr_created'
    assert result['pr_url'] == receipt['url']
    assert result['conversation_url'] == f"/?task={entry['task_id']}"
    assert len(get_repair(store, 'errors', error.id)['turn_ids']) == 1
    assert not store.get_error(error.id).resolved_at


def test_checkpoint_interruption_reuses_the_already_created_turn(store, tmp_path, monkeypatch):
    import app.automatic_repair as module
    store.record_error(None, None, 'failure', 'detail')
    error = store.list_errors(limit=1)[0]
    real_save = module.save_repair
    calls = 0
    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('checkpoint interrupted')
        return real_save(*args)
    monkeypatch.setattr(module, 'save_repair', interrupted)
    with pytest.raises(RuntimeError, match='checkpoint interrupted'):
        coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    with store._connect() as db:
        assert db.execute('select count(*) from workbench_turns').fetchone()[0] == 1
    monkeypatch.setattr(module, 'save_repair', real_save)
    coordinator(store, tmp_path).run_once()
    assert get_repair(store, 'errors', error.id)['task_id'] == entry['task_id']
    with store._connect() as db:
        assert db.execute('select count(*) from workbench_turns').fetchone()[0] == 1


def test_slow_repair_readback_does_not_block_ordinary_workbench_dispatch():
    import threading
    from app.workbench.api import WorkbenchScheduler
    entered, release, dispatched = threading.Event(), threading.Event(), threading.Event()
    def maintenance():
        entered.set()
        release.wait(2)
    scheduler = WorkbenchScheduler(SimpleNamespace(run_once=dispatched.set),
                                   interval_seconds=0.01, before_run=maintenance)
    scheduler.start()
    try:
        assert entered.wait(1)
        assert dispatched.wait(0.2)
    finally:
        release.set()
        scheduler.close()



def test_user_feedback_sync_claim_restart_and_result_readback(store, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.automatic_repair_contracts import FEEDBACK_REPAIR_CONTRACT
    from app.feedback_repair import FeedbackRepairQueue
    from app.web_api.registration import register_console_routes

    store.set_feedback_iteration_enabled(True)
    synced = []
    def sync(db, page, **_):
        synced.append(len(page))
        db.upsert_feedback_event(key="manual:700", feedback_token="token", comment="review the result")
        if len(synced) == 1:
            db.upsert_feedback_event(key="manual:701", feedback_token="reserved", comment="reserved feedback")
            db.create_feedback_processing_batch(["manual:701"], batch_id="manual-reservation")

    def restart():
        return AutomaticRepairCoordinator(
            store, repository=tmp_path / "dev", interval_seconds=0,
            pr_reader=lambda branch: [],
            user_feedback=FeedbackRepairQueue(store, sync=sync),
        )

    restart().run_once()
    item = store.get_feedback_processing_item("manual:700")
    assert item.status == "processing"
    entry = get_repair(store, "user_feedback", item.current_round_id)
    turn, = entry["turn_ids"]
    assert item.workbench_turn_id == turn
    assert store.get_turn_result_contract(turn) == FEEDBACK_REPAIR_CONTRACT
    assert "/api/console/feedback/manual%3A700" in store.get_turn(turn).user_text

    restart().run_once()
    finish(store, turn, '{"outcome":"no_code_change","summary":"Verified existing receipt"}')
    restart().run_once()
    assert get_repair(store, "user_feedback", item.current_round_id)["turn_ids"] == [turn]
    assert synced
    app = FastAPI()
    register_console_routes(
        app, store_factory=lambda: store, status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [],
    )
    payload = TestClient(app).get("/api/console/feedback").json()["items"]
    result = next(row for row in payload if row["key"] == "manual:700")
    reserved = store.get_feedback_processing_item("manual:701")
    assert reserved.status == "pending"
    assert reserved.batch_id == "manual-reservation"
    assert result["repair"]["status"] == "no_code_change"
    assert result["repair"]["summary"] == "Verified existing receipt"
    assert result["processing_task_id"] == entry["task_id"]


def test_user_feedback_delivery_check_does_not_reenqueue_the_original(store):
    from app.feedback_repair import FeedbackRepairQueue
    from app.automatic_repair_store import save_repair

    task = store.ensure_reply_task(
        conversation_id="original", conversation_title="Original",
        single_chat=True, trigger_message_id="original-msg",
        trigger_create_time="2026-11-20 00:00:00",
        trigger_sender="Sender", trigger_text="Prepare the requested draft",
    )
    attempt = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="reply", send_status="failed", sensitivity_kind="normal",
    )
    claimed = store.claim_reply_task(task.id)
    store.complete_reply_task(task.id, expected_execution_generation=claimed.execution_generation)
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "Already delivered")
    enqueue_repair(store, "user_feedback", 101)
    entry = get_repair(store, "user_feedback", 101)
    entry["business_retry"] = {"attempt_id": attempt, "diagnosis": "Find the delivery", "outcome": "delivery_check"}
    save_repair(store, entry)
    FeedbackRepairQueue(store).advance_business(entry)

    assert get_repair(store, "user_feedback", 101)["status"] == "blocked"
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation


@pytest.mark.parametrize("prior_delivery,new_delivery", [(False, True), (True, True), (True, False)])
def test_user_feedback_hands_off_one_business_revision(store, tmp_path, monkeypatch, prior_delivery, new_delivery):
    from app.action_storage import update_action
    from app.automatic_repair_store import save_repair
    from app.dingtalk_models import DingTalkMessage
    from app.feedback_repair import FeedbackRepairQueue
    from tests.test_store import _claim_audit_run

    trigger = DingTalkMessage(
        open_conversation_id="feedback-ding", conversation_title="Original", single_chat=True,
        open_message_id="reply-original", create_time="2026-11-20 00:00:00",
        sender_name="Sender", sender_user_id="sender-1", content="Review the document",
    )
    task = store.ensure_reply_task(
        conversation_id=trigger.open_conversation_id, conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat, trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time, trigger_sender=trigger.sender_name,
        trigger_text=trigger.content, trigger_message_json=trigger.model_dump_json(),
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(store, task.id, task.execution_generation, owner="audit").run
    attempt = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="agent_run", sensitivity_kind="normal",
        send_status="completed" if prior_delivery else "failed",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt, {"agent_run_id": run.id})

    def record_delivered(audit_run, identity):
        store.complete_agent_run(audit_run.id, {"outcome": "approve"}, owner="audit")
        return store.record_completed_agent_message_delivery(
            agent_run_id=audit_run.id, external_action_key=identity,
            business_object_key=task.business_object_key, action_identity=identity,
            operation="reply_to_message", target_identifiers={"conversation_id": task.conversation_id},
            conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
            reply_text=identity, provider_result={"message_id": identity},
        )

    if prior_delivery:
        old_receipt = record_delivered(run, "original draft")
        store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
        assert store.get_reply_task(task.id).input_compacted
        monkeypatch.setattr("app.feedback_repair.DwsClient.read_message_by_id", lambda *_: trigger)
    else:
        store.fail_agent_run(run.id, {"code": "test_failure"}, owner="audit")
        store.fail_reply_task(task.id, "audit_revision_exhausted", expected_execution_generation=task.execution_generation)
        ordinary_repair = get_repair(store, "reply_attempts", attempt)
        ordinary_repair["status"] = "no_code_change"
        save_repair(store, ordinary_repair)
    store.set_feedback_iteration_enabled(True)
    store.upsert_feedback_event(key=f"manual:{attempt}", feedback_token="token", comment="Correct the title")

    def no_github(_branch):
        pytest.fail("business diagnosis must not depend on GitHub")
    coord = AutomaticRepairCoordinator(
        store, repository=tmp_path / "dev", interval_seconds=0,
        user_feedback=FeedbackRepairQueue(store, sync=lambda *_args, **_kwargs: 0),
        pr_reader=no_github,
    )
    coord.run_once()
    item = store.get_feedback_processing_item(f"manual:{attempt}")
    entry = get_repair(store, "user_feedback", item.current_round_id)
    first_turn = item.workbench_turn_id
    finish(store, first_turn, '{"outcome":"business_revision","summary":"Use the verified title"}')
    if prior_delivery:
        # A deployment recovers the first version's overly broad receipt readback.
        entry.update(status="business_delivered", business_retry={"attempt_id": attempt, "diagnosis": "Use the verified title"})
        save_repair(store, entry)
        coord.run_once()
        second = get_repair(store, "user_feedback", item.current_round_id)
        assert second["task_id"] == entry["task_id"]
        assert len(second["turn_ids"]) == 2 and second["turn_ids"][0] == first_turn
        assert store.get_reply_task(task.id).execution_generation == task.execution_generation
        finish(store, second["turn_ids"][-1], '{"outcome":"business_revision","summary":"Use the verified title"}')
    coord.run_once()
    coord.run_once()
    revised = store.get_reply_task(task.id)
    assert revised.status == "pending"
    assert revised.execution_generation != task.execution_generation
    assert revised.manual_rerun_attempt_id == attempt
    assert revised.trigger_message_json == trigger.model_dump_json()
    assert "Use the verified title" in store.get_reply_attempt(attempt).reviewer_feedback
    coord.run_once()
    assert store.get_reply_task(task.id).execution_generation == revised.execution_generation
    assert len(store.list_reply_task_inputs(task.id)) == 2
    entry = get_repair(store, "user_feedback", item.current_round_id)
    assert entry["status"] == "awaiting_business"
    if prior_delivery:
        assert store.get_sent_reply(task.conversation_id, task.trigger_message_id).id == old_receipt.id

    revised = store.claim_reply_task(revised.id)
    if new_delivery:
        fresh = _claim_audit_run(store, revised.id, revised.execution_generation, owner="audit").run
        receipt = record_delivered(fresh, "corrected draft")
    store.complete_reply_task(revised.id, expected_execution_generation=revised.execution_generation)
    coord.user_feedback.advance_business(entry)
    result = get_repair(store, "user_feedback", item.current_round_id)
    if new_delivery:
        assert result["status"] == "business_delivered"
        assert str(receipt.id) in result["summary"]
    else:
        assert result["status"] == "blocked"
        assert "尚无本次修订的发送回执" in result["summary"]


def test_user_feedback_code_defect_reuses_the_existing_pr_readback(store, tmp_path):
    from app.feedback_repair import FeedbackRepairQueue

    store.set_feedback_iteration_enabled(True)
    store.upsert_feedback_event(key="manual:700", feedback_token="token", comment="Report defect")
    git_branches = []

    def read_branch(branch):
        git_branches.append(branch)
        return [{"url": "https://github.com/example/service/pull/7", "headRefOid": "sha", "state": "OPEN"}]

    coord = AutomaticRepairCoordinator(
        store, repository=tmp_path / "dev", interval_seconds=0,
        user_feedback=FeedbackRepairQueue(store, sync=lambda *_args, **_kwargs: 0),
        pr_reader=read_branch,
    )
    coord.run_once()
    item = store.get_feedback_processing_item("manual:700")
    finish(store, item.workbench_turn_id, '{"outcome":"pr_created","summary":"Regression verified"}')
    coord.run_once()
    assert git_branches == [f"codex/repair-user_feedback-{item.current_round_id}"]
    repair = repair_presentation(store, "user_feedback", item.current_round_id)
    assert repair["status"] == "pr_created"
    assert repair["pr_url"] == "https://github.com/example/service/pull/7"
