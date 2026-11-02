"""Native reaction participants are display names, not organization user IDs."""

import pytest

from app.agent_contracts import ProposedAction
from app.dws_client import DwsUserProfile
from app.system_action_handlers import MessageEmotionHandler


def action():
    return ProposedAction(
        description="Acknowledge", action_identity="ack", capability="dingtalk-chat",
        operation="add_message_emoji", target={"conversation_id": "conversation", "message_id": "message"},
        payload={"emoji": "👍"},
    )


class NativeReactions:
    def __init__(self, *, names=None, profiles=None, message_id="message", emoji="👍"):
        self.names = ["Current nickname"] if names is None else names
        self.profiles = profiles if profiles is not None else [
            DwsUserProfile(user_id="current-id", name="Current legal name", nick="Current nickname"),
            DwsUserProfile(user_id="assistant-id", name="Current nickname assistant"),
        ]
        self.message_id, self.emoji = message_id, emoji
        self.sent = 0
        self.queries = []

    def get_current_user_id(self):
        return "current-id"

    def list_message_emotion_replies(self, message_id):
        assert message_id == "message"
        return {"success": True, "result": {"messages": [{
            "openMessageId": self.message_id,
            "emotionReplyList": [{"emoji": self.emoji, "replyUsers": self.names}],
        }]}}

    def search_user_profiles(self, query):
        self.queries.append(query)
        return self.profiles

    def add_message_emoji(self, *args):
        self.sent += 1
        return {"success": True}


@pytest.mark.parametrize("entrypoint", ["dispatch", "reconcile"])
def test_native_nickname_is_resolved_to_current_principal(entrypoint):
    dws = NativeReactions()
    result = getattr(MessageEmotionHandler(dws), entrypoint)(action(), action_key="key", candidate={})
    assert result.status == "verified"
    assert result.provider_result["user_id"] == "current-id"
    assert result.provider_result["readback"]["replyUsers"] == ["Current nickname"]
    assert dws.queries == ["Current nickname"]
    assert dws.sent == (entrypoint == "dispatch")


@pytest.mark.parametrize("profiles", [
    [],
    [DwsUserProfile(user_id="other", nick="Current nickname")],
    [DwsUserProfile(user_id="current-id", nick="Current nickname assistant")],
    [DwsUserProfile(user_id="current-id", nick="Current nickname"),
     DwsUserProfile(user_id="other", nick="Current nickname")],
])
def test_missing_other_fuzzy_or_ambiguous_participant_is_unconfirmed(profiles):
    dws = NativeReactions(profiles=profiles)
    result = MessageEmotionHandler(dws).reconcile(action(), action_key="key", candidate={})
    assert result.status == "uncertain"
    assert dws.sent == 0


@pytest.mark.parametrize("kwargs", [
    {"message_id": "other-message"}, {"emoji": "🎉"}, {"names": []},
])
def test_readback_still_requires_exact_message_and_emoji(kwargs):
    dws = NativeReactions(**kwargs)
    result = MessageEmotionHandler(dws).reconcile(action(), action_key="key", candidate={})
    assert result.status == "uncertain"
    assert dws.queries == []
    assert dws.sent == 0
