from app.store import AutoReplyStore


def _sent(store: AutoReplyStore, *, token: str = "feedback-source-token") -> int:
    store.record_sent_reply(
        "conversation-source-1",
        "trigger-source-1",
        "Action owned message body",
        send_result_json='{"message_id":"provider-message-1"}',
        recall_key="provider-message-1",
        feedback_token=token,
    )
    sent = store.get_sent_reply("conversation-source-1", "trigger-source-1")
    assert sent is not None
    return sent.id


def test_history_detail_reads_action_owner(tmp_path):
    from app.web_api.attempts import build_attempt_detail

    store = AutoReplyStore(tmp_path / "history-detail.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="conversation-source-1",
        conversation_title="Source conversation",
        trigger_message_id="trigger-source-1",
        trigger_sender="Source sender",
        trigger_text="Original request",
        action="agent_run",
        sensitivity_kind="ordinary",
        send_status="completed",
    )
    action_id = _sent(store)

    status, detail = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert {key: detail["delivery"][key] for key in (
        "action_id", "conversation_id", "trigger_message_id", "body",
        "operation", "target", "provider_message_id", "source_available",
        "source_label",
    )} == {
        "action_id": action_id,
        "conversation_id": "conversation-source-1",
        "trigger_message_id": "trigger-source-1",
        "body": "Action owned message body",
        "operation": None,
        "target": None,
        "provider_message_id": "provider-message-1",
        "source_available": False,
        "source_label": "来源事实未提供",
    }
    assert detail["delivery"]["provider_receipt"] == {
        "id": detail["delivery"]["provider_receipt"]["id"],
        "available": True,
        "body": '{"message_id":"provider-message-1"}',
        "result_digest": None,
    }
    with store._connect() as db:
        owner = db.execute(
            "select action_delivery_receipt_id from actions where id=? and record_type='action'",
            (action_id,),
        ).fetchone()
        fact = db.execute(
            "select observed_at from action_receipts where id=? and action_id=?",
            (owner[0], action_id),
        ).fetchone()
    assert detail["delivery"]["receipt_id"] == owner[0]
    assert detail["delivery"]["sent_at"] == fact[0]
    assert detail["generated_reply"]["text"] == "Action owned message body"


def test_multiple_observers_show_one_send(tmp_path):
    store = AutoReplyStore(tmp_path / "observer-history.sqlite3")
    action_id = _sent(store)

    with store._connect() as db:
        db.execute("begin")
        from app.action_entity_storage import add_action_observer

        add_action_observer(
            db, action_id, agent_run_id=701, task_id=81,
            created_at="2026-10-20T10:00:00+00:00",
        )
        add_action_observer(
            db, action_id, agent_run_id=702, task_id=81,
            created_at="2026-10-20T10:01:00+00:00",
        )

    assert store.count_sent_replies() == 1
    sent = store.list_sent_replies_after(0)
    assert [item.id for item in sent] == [action_id]
    with store._connect() as db:
        assert db.execute(
            "select count(*) from action_observers where action_id=?", (action_id,),
        ).fetchone()[0] == 2


def test_recall_updates_existing_action_without_replacing_send_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "recall-action.sqlite3")
    action_id = _sent(store)
    before = store.get_sent_reply("conversation-source-1", "trigger-source-1")
    assert before is not None

    store.update_sent_reply_recall(
        action_id, recall_status="recalled", recall_error="",
    )

    after = store.get_sent_reply("conversation-source-1", "trigger-source-1")
    assert after is not None
    assert after.id == action_id
    assert after.reply_text == before.reply_text
    assert after.send_result_json == before.send_result_json
    assert after.sent_at == before.sent_at
    assert after.recall_status == "recalled"
    assert store.count_sent_replies() == 1


def test_feedback_token_resolves_action_and_original_message(tmp_path):
    store = AutoReplyStore(tmp_path / "feedback-action.sqlite3")
    store.record_reply_attempt(
        conversation_id="conversation-source-1",
        conversation_title="Source conversation",
        trigger_message_id="trigger-source-1",
        trigger_sender="Source sender",
        trigger_text="Original request",
        action="agent_run",
        sensitivity_kind="ordinary",
        send_status="completed",
    )
    action_id = _sent(store, token="exact-feedback-token")
    store.upsert_feedback_event(
        key="feedback-event-1", feedback_token="exact-feedback-token",
        rating="positive", comment="Original message feedback",
        received_at="2026-10-20T10:02:00+00:00",
    )

    sent = store.list_sent_replies_with_feedback_tokens()
    feedback = store.list_user_feedback_items()

    assert [(item.id, item.conversation_id, item.trigger_message_id) for item in sent] == [
        (action_id, "conversation-source-1", "trigger-source-1"),
    ]
    assert feedback[0].feedback_token == "exact-feedback-token"
    assert feedback[0].trigger_text == "Original request"
    assert feedback[0].attempt_id > 0


def test_source_missing_is_visible_in_detail(tmp_path):
    from app.web_api.attempts import build_attempt_detail

    store = AutoReplyStore(tmp_path / "missing-action-provenance.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="conversation-source-1",
        conversation_title="Source conversation",
        trigger_message_id="trigger-source-1",
        trigger_sender="Source sender",
        trigger_text="Original request",
        action="agent_run",
        sensitivity_kind="ordinary",
        send_status="completed",
    )
    _sent(store)

    status, detail = build_attempt_detail(store, attempt_id)

    assert status == 200
    assert detail["delivery"]["source_available"] is False
    assert detail["delivery"]["operation"] is None
    assert detail["delivery"]["target"] is None
    assert detail["delivery"]["source_label"] == "来源事实未提供"
