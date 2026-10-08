from dataclasses import dataclass

import pytest

import app.group_discovery as discovery
from app.dws_client import DwsError
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
            assert conversation.last_message_create_at is None
            return [type("Message", (), {"content": "销售招聘进展"})()]
    adapter = DingTalkGroupDiscoveryProvider(Dws(), workspace_key="workspace-a")
    group = adapter.search_groups("销售招聘")[0]
    assert group.provider_scope == scope
    assert adapter.list_group_members(group) == (MemberRef(scope, "open-u1"),)
    assert adapter.read_recent_group_messages(group, limit=30)[0].text_excerpt == "销售招聘进展"


@pytest.mark.parametrize("retryable", [False, True])
def test_dingtalk_message_read_preserves_non_retryable_confidential_denial(retryable):
    error = DwsError(
        "confidential group read refused",
        code="1001",
        business_message="该群为保密群，无法获取消息记录",
        retryable_external_dependency=retryable,
        server_key="chat",
    )

    class Dws:
        def read_recent_messages(self, conversation, limit):
            raise error

    adapter = DingTalkGroupDiscoveryProvider(Dws())
    group = GroupRef(adapter.scope, "cid-confidential", "Private group")
    expected = RetryableProviderError if retryable else discovery.GroupHistoryReadDenied

    with pytest.raises(expected) as caught:
        adapter.read_recent_group_messages(group, limit=30)

    if not retryable:
        assert caught.value.group == group
        assert caught.value.code == "1001"
        assert caught.value.reason == error.business_message
        assert caught.value.__cause__ is error


@pytest.mark.parametrize("platform", ["dingtalk", "lark", "slack"])
@pytest.mark.parametrize("all_denied", [False, True])
def test_candidate_history_denial_preserves_roster_and_remaining_reads(platform, all_denied):
    scope = ProviderScope(platform, "workspace-a")
    groups = tuple(GroupRef(scope, key, "Topic") for key in ("denied", "next"))
    audience = (MemberRef(scope, "u1"), MemberRef(scope, "u2"))
    roster = audience + (MemberRef(scope, "other"),)
    provider = FakeProvider(scope, groups, {g.external_group_id: roster for g in groups}, {})

    def read(group, *, limit):
        provider.calls.append(("messages", group.external_group_id))
        if all_denied or group.external_group_id == "denied":
            raise discovery.GroupHistoryReadDenied(group, code="restricted", reason="Access denied")
        return (Message("Topic discussion"),)

    provider.read_recent_group_messages = read
    result = GroupDiscoveryService(provider, FakePolicy()).discover(
        GroupDiscoveryRequest(scope, "Topic", audience=audience, audience_is_complete=True)
    )
    assert result.outcome.value == ("history_read_denied" if all_denied else "ambiguous")
    assert {c.group for c in result.candidates} == set(groups)
    denied = next(c for c in result.candidates if c.group.external_group_id == "denied")
    assert denied.evidence.members == roster
    assert denied.evidence.participant_coverage.total == 2
    assert denied.evidence.history_read_denial.code == "restricted"
    assert denied.evidence.history_read_denial.reason == "Access denied"
    assert denied.evidence.discussion is None
    assert denied.evidence.sendability.allowed is None
    assert [c for c in provider.calls if c[0] == "messages"] == [("messages", "denied"), ("messages", "next")]


@pytest.mark.parametrize("failure", [RetryableProviderError("transport interrupted"), ValueError("unknown provider failure")])
def test_history_denial_does_not_swallow_remaining_provider_failure(failure):
    scope = ProviderScope("slack", "workspace-a")
    groups = tuple(GroupRef(scope, key, "Topic") for key in ("denied", "next"))
    provider = FakeProvider(scope, groups, {}, {})

    def read(group, *, limit):
        if group.external_group_id == "denied":
            raise discovery.GroupHistoryReadDenied(group, code="restricted", reason="Access denied")
        raise failure

    provider.read_recent_group_messages = read
    request = GroupDiscoveryRequest(scope, "Topic")
    if isinstance(failure, RetryableProviderError):
        result = GroupDiscoveryService(provider, FakePolicy()).discover(request)
        assert result.outcome == GroupDiscoveryOutcome.RETRYABLE_FAILURE
        assert result.provider_error == str(failure)
        assert result.candidates == ()
    else:
        with pytest.raises(ValueError, match="unknown provider failure"):
            GroupDiscoveryService(provider, FakePolicy()).discover(request)


def test_history_denial_cannot_be_attached_to_another_scoped_group():
    scope = ProviderScope("slack", "workspace-a")
    group = GroupRef(scope, "cid", "Topic")
    other = GroupRef(ProviderScope("slack", "workspace-b"), "cid", "Topic")
    provider = FakeProvider(scope, (group,), {}, {})

    def read(group, *, limit):
        raise discovery.GroupHistoryReadDenied(other, code="restricted", reason="Access denied")

    provider.read_recent_group_messages = read
    with pytest.raises(ValueError, match="history denial does not match requested group"):
        GroupDiscoveryService(provider, FakePolicy()).discover(GroupDiscoveryRequest(scope, "Topic"))


@pytest.mark.parametrize("code,message", [("1001", "other business failure"), ("500", "该群为保密群，无法获取消息记录")])
def test_dingtalk_unknown_message_read_failure_remains_retryable(code, message):
    class Dws:
        def read_recent_messages(self, conversation, limit):
            raise DwsError("read failed", code=code, business_message=message)

    adapter = DingTalkGroupDiscoveryProvider(Dws())
    with pytest.raises(RetryableProviderError):
        adapter.read_recent_group_messages(GroupRef(adapter.scope, "cid", "Group"), limit=30)
