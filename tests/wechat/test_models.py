from pydantic import ValidationError
import pytest

from app.wechat.models import WechatMessage, WechatReplyScope


def test_group_message_mentions_exact_current_account():
    message = WechatMessage(
        account_id="acct-1",
        conversation_id="group-1",
        message_id="msg-1",
        sender_id="member-1",
        sender_display_name="Mina",
        conversation_type="group",
        direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00",
        kind="text",
        text="@Derek 看下",
        mentioned_user_ids=["self-1"],
        source_version="4.1.10",
    )

    assert message.mentions_user("self-1") is True
    assert message.mentions_user("self-2") is False


def test_group_scope_cannot_use_direct_trigger():
    with pytest.raises(ValidationError):
        WechatReplyScope(
            account_id="acct-1",
            target_type="group",
            target_id="group-1",
            display_name="CEO group",
            trigger_mode="every_inbound_text",
        )


def test_direct_scope_requires_every_inbound_text():
    scope = WechatReplyScope(
        account_id="acct-1",
        target_type="direct",
        target_id="u-1",
        display_name="Alex",
        trigger_mode="every_inbound_text",
    )
    assert scope.trigger_mode == "every_inbound_text"
    with pytest.raises(ValidationError):
        WechatReplyScope(
            account_id="acct-1",
            target_type="direct",
            target_id="u-1",
            display_name="Alex",
            trigger_mode="mention_current_account",
        )
