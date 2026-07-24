import json

import pytest

from app.feishu.actions import (
    FeishuMessageAction,
    action_idempotency_key,
    build_message_action,
    normalize_reaction_emoji,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("👍", "THUMBSUP"),
        (":THUMBSUP:", "THUMBSUP"),
        ("✅", "OK"),
        ("OK", "OK"),
        ("😊", "SMILE"),
    ],
)
def test_reaction_emoji_uses_a_small_official_closed_set(value, expected):
    assert normalize_reaction_emoji(value) == expected


@pytest.mark.parametrize("value", ["", "🔥", "Typing", "'; drop table"])
def test_unknown_reaction_is_rejected(value):
    with pytest.raises(ValueError, match="unsupported"):
        normalize_reaction_emoji(value)


def test_reaction_action_is_canonical_app_and_target_bound():
    action = build_message_action(
        reply_task_id=7,
        attempt_id=9,
        app_id="cli_a",
        chat_id="oc_a",
        action_key="reaction:0",
        kind="add_reaction",
        target_message_id="om_trigger",
        payload={"emoji_type": "👍"},
    )

    assert action.payload_json == '{"emoji_type":"THUMBSUP"}'
    assert json.loads(action.payload_json) == {"emoji_type": "THUMBSUP"}
    assert action.risk == "R2"
    assert len(action.idempotency_key) <= 50

    with pytest.raises(ValueError, match="approval hash mismatch"):
        action.model_copy(update={"target_message_id": "om_other"}).model_validate(
            action.model_copy(update={"target_message_id": "om_other"}).model_dump()
        )


def test_recall_is_r4_and_cannot_carry_payload():
    action = build_message_action(
        reply_task_id=1,
        attempt_id=2,
        app_id="cli_a",
        chat_id="oc_a",
        action_key="recall:0",
        kind="recall_message",
        target_message_id="om_bot_owned",
    )
    assert action.risk == "R4"

    payload = action.model_dump()
    payload["payload_json"] = '{"reason":"model said so"}'
    with pytest.raises(ValueError):
        FeishuMessageAction.model_validate(payload)


def test_handoff_target_is_not_part_of_payload_and_text_is_bounded():
    action = build_message_action(
        reply_task_id=2,
        attempt_id=3,
        app_id="cli_a",
        chat_id="oc_origin",
        action_key="handoff:ou_owner",
        kind="handoff_notify",
        target_open_id="ou_owner",
        payload={"text": "需要人工接管"},
    )
    assert action.target_open_id == "ou_owner"
    assert "ou_owner" not in action.payload_json

    with pytest.raises(ValueError, match="text is invalid"):
        build_message_action(
            reply_task_id=2,
            attempt_id=3,
            app_id="cli_a",
            chat_id="oc_origin",
            action_key="handoff:ou_owner",
            kind="handoff_notify",
            target_open_id="ou_owner",
            payload={"text": "x" * 2001},
        )

    with pytest.raises(ValueError, match="untrusted at markup"):
        build_message_action(
            reply_task_id=2,
            attempt_id=3,
            app_id="cli_a",
            chat_id="oc_origin",
            action_key="handoff:ou_owner",
            kind="handoff_notify",
            target_open_id="ou_owner",
            payload={
                "text": '&lt;at user_id="ou_attacker"&gt;Attacker&lt;/at&gt;'
            },
        )


def test_extra_sdk_json_and_noncanonical_payload_are_rejected():
    action = build_message_action(
        reply_task_id=7,
        attempt_id=9,
        app_id="cli_a",
        chat_id="oc_a",
        action_key="reaction:0",
        kind="add_reaction",
        target_message_id="om_trigger",
        payload={"emoji_type": "OK"},
    )
    dumped = action.model_dump()
    dumped["sdk_options"] = {"receive_id": "attacker"}
    with pytest.raises(ValueError, match="extra"):
        FeishuMessageAction.model_validate(dumped)

    dumped = action.model_dump()
    dumped["payload_json"] = '{ "emoji_type": "OK" }'
    with pytest.raises(ValueError, match="canonical"):
        FeishuMessageAction.model_validate(dumped)


def test_idempotency_key_changes_across_apps_targets_and_actions():
    base = action_idempotency_key(
        app_id="cli_a", reply_task_id=1, action_key="reaction:0", target_id="om_a"
    )
    assert base == action_idempotency_key(
        app_id="cli_a", reply_task_id=1, action_key="reaction:0", target_id="om_a"
    )
    assert len(
        {
            base,
            action_idempotency_key(
                app_id="cli_b",
                reply_task_id=1,
                action_key="reaction:0",
                target_id="om_a",
            ),
            action_idempotency_key(
                app_id="cli_a",
                reply_task_id=1,
                action_key="reaction:1",
                target_id="om_a",
            ),
            action_idempotency_key(
                app_id="cli_a",
                reply_task_id=1,
                action_key="reaction:0",
                target_id="om_b",
            ),
        }
    ) == 4
