import json
from types import SimpleNamespace

from app.feishu.approval_preview import (
    action_approval_preview,
    action_list_preview,
    delivery_approval_preview,
    delivery_list_preview,
    stable_identifier_fingerprint,
)
from app.feishu.audit_web import _action_item, _delivery_item


def _delivery(**updates):
    values = {
        "id": 1,
        "status": "ready_to_send",
        "attempts": 0,
        "error": "",
        "error_code": "",
        "approved_at": "",
        "approved_by": "",
        "approval_hash": "a" * 64,
        "chat_id": "oc-private-chat-a",
        "reply_to_message_id": "om-private-target-a",
        "reply_in_thread": False,
        "mention_open_ids": ("ou-private-mention-a",),
        "reply_format": "text",
        "expected_chunks": 1,
        "reply_text": "same-size-A",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _action(**updates):
    values = {
        "id": 1,
        "kind": "handoff_notify",
        "risk": "R2",
        "status": "ready",
        "approved_at": "",
        "approved_by": "",
        "approval_hash": "b" * 64,
        "target_message_id": "",
        "target_open_id": "ou-private-owner-a",
        "payload_json": json.dumps(
            {"text": "handoff <owner> & \"review\""},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_delivery_preview_exposes_every_effect_without_raw_identifiers():
    delivery = _delivery()
    preview = delivery_approval_preview(delivery)
    serialized = json.dumps(preview, ensure_ascii=False)

    assert preview["target"]["provenance"] == "reply_to_inbound_message"
    assert preview["target"]["message_fingerprint"].startswith("sha256:")
    assert preview["reply_in_thread"] is False
    assert preview["mentions"][0]["alias"] == "mention-1"
    assert preview["reply_format"] == "text"
    assert preview["expected_chunks"] == 1
    assert preview["text"] == "same-size-A"
    for private_id in (
        delivery.chat_id,
        delivery.reply_to_message_id,
        delivery.mention_open_ids[0],
    ):
        assert private_id not in serialized

    variants = (
        _delivery(reply_to_message_id="om-private-target-b"),
        _delivery(mention_open_ids=("ou-private-mention-b",)),
        _delivery(reply_in_thread=True),
        _delivery(reply_text="same-size-B"),
    )
    rendered = _delivery_item(
        delivery, None, approvable=False, rejectable=False
    )
    for variant in variants:
        assert delivery_approval_preview(variant) != preview
        assert _delivery_item(
            variant, None, approvable=False, rejectable=False
        ) != rendered

    escaped = _delivery_item(
        _delivery(reply_text='reply <owner> & "review"'),
        None,
        approvable=False,
        rejectable=False,
    )
    assert "reply &lt;owner&gt; &amp; &quot;review&quot;" in escaped
    assert 'reply <owner> & "review"' not in escaped


def test_default_delivery_list_withholds_text_but_keeps_safe_effect_metadata():
    delivery = _delivery(reply_text="PRIVATE-DRAFT")
    preview = delivery_list_preview(delivery)
    serialized = json.dumps(preview, ensure_ascii=False)

    assert "PRIVATE-DRAFT" not in serialized
    assert preview["text_summary"] == "[redacted:13 chars]"
    assert preview["target"]["message_fingerprint"].startswith("sha256:")
    assert preview["mentions"][0]["fingerprint"].startswith("sha256:")


def test_action_preview_is_target_bound_and_html_escapes_exact_handoff_text():
    action = _action()
    preview = action_approval_preview(action)
    page = _action_item(action)

    assert preview["target"]["provenance"] == (
        "locally_allowlisted_handoff_recipient"
    )
    assert preview["effect"]["text"] == 'handoff <owner> & "review"'
    assert action.target_open_id not in json.dumps(preview, ensure_ascii=False)
    assert action.target_open_id not in page
    assert "handoff &lt;owner&gt; &amp; &quot;review&quot;" in page
    assert 'handoff <owner> & "review"' not in page

    other_target = _action(target_open_id="ou-private-owner-b")
    other_text = _action(
        payload_json=json.dumps(
            {"text": "handoff <owner> & \"reviex\""},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    assert action_approval_preview(other_target) != preview
    assert action_approval_preview(other_text) != preview
    assert _action_item(other_target) != page
    assert _action_item(other_text) != page


def test_reaction_and_recall_previews_show_value_and_owned_provenance():
    reaction = _action(
        kind="add_reaction",
        target_open_id="",
        target_message_id="om-private-reaction-target",
        payload_json='{"emoji_type":"OK"}',
    )
    recall = _action(
        kind="recall_message",
        risk="R4",
        target_open_id="",
        target_message_id="om-private-recall-target",
        payload_json="{}",
    )
    reaction_preview = action_approval_preview(reaction)
    recall_preview = action_approval_preview(recall)

    assert reaction_preview["effect"] == {
        "type": "add_reaction",
        "emoji_type": "OK",
    }
    assert reaction_preview["target"]["provenance"] == (
        "persisted_reply_task_trigger_message"
    )
    assert recall_preview["effect"] == {"type": "recall_bot_owned_message"}
    assert recall_preview["target"]["provenance"] == (
        "active_app_owned_delivery_receipt"
    )
    assert reaction.target_message_id not in json.dumps(reaction_preview)
    assert recall.target_message_id not in json.dumps(recall_preview)
    assert "Reaction：</b>OK" in _action_item(reaction)
    assert "撤回该机器人自有消息" in _action_item(recall)


def test_fingerprints_are_stable_domain_separated_and_list_handoff_is_redacted():
    first = stable_identifier_fingerprint("message_id", "same-private-id")
    assert first == stable_identifier_fingerprint(
        "message_id", "same-private-id"
    )
    assert first != stable_identifier_fingerprint("open_id", "same-private-id")
    assert "same-private-id" not in first

    action = _action()
    listed = action_list_preview(action)
    serialized = json.dumps(listed, ensure_ascii=False)
    assert 'handoff <owner> & "review"' not in serialized
    assert listed["effect"]["text_summary"].startswith("[redacted:")
