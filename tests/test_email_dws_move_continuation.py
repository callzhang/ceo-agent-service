from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_email_action_plan,
)
from app.email_store import EmailActionPlanConflict, EmailStore
from app.web_api.email import register_email_routes


def _store_with_move_and_dependents(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    identity = 12345
    account_id = "dingtalk_primary"
    stable_identity = "dingtalk_primary:message-id:<junhe@example.com>"
    actions = (
        EmailAction.MOVE,
        EmailAction.FLAG_IMPORTANT,
        EmailAction.MARK_READ,
    )
    action_parameters = {
        EmailAction.MOVE: {"target_folder": "法务"},
        EmailAction.FLAG_IMPORTANT: {"depends_on": ["move"]},
        EmailAction.MARK_READ: {"depends_on": ["move", "flag_important"]},
    }
    plan = build_email_action_plan(
        classification_id=identity,
        account_id=account_id,
        category=EmailCategory.WORK,
        classification_source="model",
        confidence=0.9,
        model_id="email/model-test",
        config_version="email-test-v1",
        actions=actions,
        action_parameters=action_parameters,
        created_at=datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc),
    )
    classification = EmailClassification(
        classification_id=identity,
        stable_message_identity=stable_identity,
        provider_locator=EmailProviderLocator(
            account_id=account_id,
            folder="INBOX",
            uidvalidity=42,
            uid=identity,
            rfc_message_id="<junhe@example.com>",
            thread_id="junhe-thread",
        ),
        category=EmailCategory.WORK,
        confidence=0.9,
        margin=0.4,
        probabilities={EmailCategory.WORK: 0.9},
        model_id="email/model-test",
        config_version="email-test-v1",
        status=EmailClassificationStatus.PROCESSED,
        classification_source="model",
        action_plan=plan,
    )
    persisted = store.persist_scan_result(
        classification,
        sender="xup@junhe.com",
        recipients=("claire@example.com",),
        subject="JunHe test mail",
        normalized_text="junhe message",
        preview="junhe message",
        received_at="2026-05-07T08:56:27+00:00",
        model_text="junhe message",
    )
    return store, persisted, plan.action_plan_id


def _complete_move_and_skip_flag(store, persisted):
    move = store.claim_next_direct_action(claimed_at="2026-10-29T11:01:00+00:00")
    assert move is not None and move.action_type is EmailAction.MOVE
    store.complete_direct_action_attempt(
        move,
        status="done",
        provider_operation="reconciled_dws_readback",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="dws-readback:DzzzzzzNJ0u:167:" + "a" * 64,
        error="",
        finished_at="2026-10-29T11:01:01+00:00",
    )
    flag = store.claim_next_direct_action(claimed_at="2026-10-29T11:01:02+00:00")
    assert flag is not None and flag.action_type is EmailAction.FLAG_IMPORTANT
    store.complete_direct_action_attempt(
        flag,
        status="skipped",
        provider_operation="readback_missing",
        provider_target=flag.locator.stable_message_identity,
        provider_result_id="message-unavailable:" + "b" * 32,
        error="",
        finished_at="2026-10-29T11:01:03+00:00",
    )
    return move, flag


def test_verified_dws_move_continues_only_remaining_authorized_actions(tmp_path):
    store, persisted, original_plan = _store_with_move_and_dependents(tmp_path)
    move, flag = _complete_move_and_skip_flag(store, persisted)

    resumed = store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:02:00+00:00",
    )

    assert resumed is not None
    assert resumed["current_action_plan_id"] != original_plan
    assert resumed["action_plan"]["action_plan_version"] == 2
    assert resumed["action_plan"]["actions"] == ["flag_important", "mark_read"]
    old_statuses = store.direct_action_statuses_for_plan(original_plan)
    assert old_statuses[int(move.action_id)] == "done"
    assert old_statuses[int(flag.action_id)] == "skipped"
    assert len(old_statuses) == 3
    assert set(old_statuses.values()) == {"done", "skipped", "pending"}
    next_action = store.claim_next_direct_action(
        claimed_at="2026-10-29T11:02:01+00:00"
    )
    assert next_action is not None
    assert next_action.action_type is EmailAction.FLAG_IMPORTANT


def test_dws_continuation_rejects_move_without_dws_receipt(tmp_path):
    store, persisted, _ = _store_with_move_and_dependents(tmp_path)
    move = store.claim_next_direct_action(claimed_at="2026-10-29T11:03:00+00:00")
    assert move is not None
    store.complete_direct_action_attempt(
        move,
        status="done",
        provider_operation="normal_move",
        provider_target=move.locator.stable_message_identity,
        provider_result_id="imap:verified",
        error="",
        finished_at="2026-10-29T11:03:01+00:00",
    )

    with pytest.raises(EmailActionPlanConflict, match="exact completed DWS move readback"):
        store.continue_actions_after_reconciled_move(
            int(persisted["id"]),
            move_action_id=str(move.action_id),
            resumed_at="2026-10-29T11:04:00+00:00",
        )


def test_console_continuation_requires_exact_move_action_id(tmp_path):
    store, persisted, _ = _store_with_move_and_dependents(tmp_path)
    app = FastAPI()
    register_email_routes(app, lambda: store)
    client = TestClient(app)

    missing = client.post(
        f"/api/console/email/classifications/{persisted['id']}/continue-after-move-readback",
        json={},
    )

    assert missing.status_code == 400
    assert missing.json()["code"] == "invalid_move_action_id"


def test_dws_move_receipt_can_resume_after_one_continuation_was_already_created(tmp_path):
    store, persisted, original_plan = _store_with_move_and_dependents(tmp_path)
    move, _ = _complete_move_and_skip_flag(store, persisted)

    first = store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:02:00+00:00",
    )
    assert first is not None
    first_plan = first["current_action_plan_id"]
    first_actions = store.direct_action_statuses_for_plan(first_plan)
    assert set(first_actions.values()) == {"pending"}

    flag = store.claim_next_direct_action(claimed_at="2026-10-29T11:02:01+00:00")
    assert flag is not None and flag.action_type is EmailAction.FLAG_IMPORTANT
    store.complete_direct_action_attempt(
        flag,
        status="skipped",
        provider_operation="readback_missing",
        provider_target=flag.locator.stable_message_identity,
        provider_result_id="message-unavailable:" + "c" * 32,
        error="",
        finished_at="2026-10-29T11:02:02+00:00",
    )

    second = store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:03:00+00:00",
    )

    assert second is not None
    assert second["action_plan"]["action_plan_version"] == 3
    assert second["action_plan"]["actions"] == ["flag_important", "mark_read"]
    assert store.direct_action_statuses_for_plan(original_plan)[move.action_id] == "done"
    assert set(store.direct_action_statuses_for_plan(first_plan).values()) == {"skipped", "pending"}


def test_claimed_dependent_action_exposes_only_its_verified_dws_move_binding(tmp_path):
    store, persisted, _ = _store_with_move_and_dependents(tmp_path)
    move, _ = _complete_move_and_skip_flag(store, persisted)
    store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:02:00+00:00",
    )

    action = store.claim_next_direct_action(claimed_at="2026-10-29T11:02:01+00:00")
    assert action is not None
    binding = store.verified_dws_move_context_for_action(action)

    assert binding == {
        "move_action_id": str(move.action_id),
        "target_folder": "法务",
        "message_id": "DzzzzzzNJ0u",
        "folder_id": "167",
        "receipt_digest": "a" * 64,
        "account_id": "dingtalk_primary",
        "stable_message_identity": action.locator.stable_message_identity,
        "sender": "xup@junhe.com",
        "subject": "JunHe test mail",
        "received_at": "2026-05-07T08:56:27+00:00",
    }


def test_exact_dws_prewrite_failure_can_continue_as_new_immutable_plan(tmp_path):
    store, persisted, _ = _store_with_move_and_dependents(tmp_path)
    move, _ = _complete_move_and_skip_flag(store, persisted)
    first = store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:02:00+00:00",
    )
    assert first is not None
    old_plan = first["current_action_plan_id"]
    flag = store.claim_next_direct_action(claimed_at="2026-10-29T11:02:01+00:00")
    assert flag is not None
    store.complete_direct_action_attempt(
        flag,
        status="failed",
        provider_operation="dws_mail_action",
        provider_target=flag.locator.stable_message_identity,
        provider_result_id="",
        error="dws_provider_error:ValueError",
        finished_at="2026-10-29T11:02:02+00:00",
        retryable=False,
    )

    resumed = store.continue_actions_after_reconciled_move(
        int(persisted["id"]),
        move_action_id=str(move.action_id),
        resumed_at="2026-10-29T11:03:00+00:00",
    )

    assert resumed is not None
    assert resumed["action_plan"]["action_plan_version"] == 3
    assert resumed["action_plan"]["actions"] == ["flag_important", "mark_read"]
    assert set(store.direct_action_statuses_for_plan(old_plan).values()) == {"failed", "pending"}
