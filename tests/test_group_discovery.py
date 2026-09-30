from dataclasses import dataclass

import pytest

from app.group_discovery import (
    DingTalkGroupDiscoveryProvider,
    DiscussionEvidence,
    GroupDiscoveryOutcome,
    GroupDiscoveryRequest,
    GroupDiscoveryService,
    GroupRef,
    MemberRef,
    ParticipantCoverageEvidence,
    ProviderCapabilities,
    ProviderScope,
    RetryableProviderError,
    Sendability,
)


class FakePolicy:
    def build_queries(self, request):
        return (request.subject,)

    def participant_coverage(self, request, members):
        selected = {member.scoped_key for member in request.audience}
        present = len(selected & {member.scoped_key for member in members})
        status = "eligible" if present >= 2 else "ineligible"
        return ParticipantCoverageEvidence(present, len(selected), status)

    def title_score(self, request, title):
        return 1.0 if request.subject in title else 0.0

    def minimum_title_score(self, request):
        return 0.5

    def discussion_score(self, request, messages):
        return DiscussionEvidence(1.0 if any(request.subject in m.text_excerpt for m in messages) else 0.0)

    def accepts(self, request, candidate):
        return candidate.evidence.discussion is not None and candidate.evidence.discussion.score > 0


class FakeProvider:
    def __init__(self, scope, groups, members, messages):
        self.scope = scope
        self.groups = groups
        self.members = members
        self.messages = messages
        self.capabilities = ProviderCapabilities()
        self.calls = []

    def search_groups(self, query):
        self.calls.append(("search", query))
        return self.groups

    def list_group_members(self, group):
        self.calls.append(("members", group.external_group_id))
        return self.members.get(group.external_group_id, ())

    def read_recent_group_messages(self, group, *, limit):
        self.calls.append(("messages", group.external_group_id))
        return self.messages.get(group.external_group_id, ())

    def get_group_sendability(self, group):
        self.calls.append(("sendability", group.external_group_id))
        return Sendability(True)


@dataclass
class Message:
    text_excerpt: str


@pytest.mark.parametrize("platform", ["dingtalk", "lark", "slack"])
def test_staged_discovery_filters_roster_and_title_before_message_reads(platform):
    scope = ProviderScope(platform, "workspace-a")
    groups = (
        GroupRef(scope, "wrong-roster", "销售招聘"),
        GroupRef(scope, "wrong-title", "研发讨论"),
        GroupRef(scope, "right", "销售招聘"),
        GroupRef(scope, "right", "销售招聘"),
    )
    audience = (MemberRef(scope, "u1"), MemberRef(scope, "u2"))
    provider = FakeProvider(
        scope,
        groups,
        {
            "wrong-roster": (audience[0],),
            "wrong-title": audience,
            "right": audience,
        },
        {"right": (Message("销售招聘进展"),)},
    )
    result = GroupDiscoveryService(provider, FakePolicy()).discover(
        GroupDiscoveryRequest(scope, "销售招聘", audience=audience, audience_is_complete=True)
    )
    assert result.outcome == GroupDiscoveryOutcome.VERIFIED
    assert [item.group.external_group_id for item in result.candidates] == ["right"]
    assert [call for call in provider.calls if call[0] == "messages"] == [("messages", "right")]


def test_provider_failure_does_not_become_no_group():
    scope = ProviderScope("dingtalk", "workspace-a")
    provider = FakeProvider(scope, (), {}, {})
    def fail(_query):
        raise RetryableProviderError("search incomplete")
    provider.search_groups = fail
    result = GroupDiscoveryService(provider, FakePolicy()).discover(GroupDiscoveryRequest(scope, "销售招聘"))
    assert result.outcome == GroupDiscoveryOutcome.RETRYABLE_FAILURE
    assert result.provider_error == "search incomplete"


def test_dingtalk_adapter_scopes_ids_and_messages():
    scope = ProviderScope("dingtalk", "workspace-a")
    class Dws:
        def search_conversations(self, query):
            return [type("Conversation", (), {"open_conversation_id": "cid", "title": query})()]
        def list_group_member_open_dingtalk_ids(self, conversation_id):
            assert conversation_id == "cid"
            return {"open-u1"}
        def read_recent_messages(self, conversation, limit):
            assert conversation.open_conversation_id == "cid"
            return [type("Message", (), {"content": "销售招聘进展"})()]
    adapter = DingTalkGroupDiscoveryProvider(Dws(), workspace_key="workspace-a")
    group = adapter.search_groups("销售招聘")[0]
    assert group.provider_scope == scope
    assert adapter.list_group_members(group) == (MemberRef(scope, "open-u1"),)
    assert adapter.read_recent_group_messages(group, limit=30)[0].text_excerpt == "销售招聘进展"
