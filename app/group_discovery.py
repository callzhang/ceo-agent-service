"""Provider-neutral, staged discovery of collaboration groups.

The service only returns evidence-backed candidates. It never chooses a final
business target and never sends a message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from app.dws_client import DwsError


def is_non_retryable_dingtalk_group_read_denial(error: Exception) -> bool:
    return (
        isinstance(error, DwsError)
        and error.code == "1001"
        and error.business_message.strip() == "该群为保密群，无法获取消息记录"
        and not error.retryable_external_dependency
    )


@dataclass(frozen=True)
class ProviderScope:
    platform: str
    workspace_key: str


@dataclass(frozen=True)
class GroupRef:
    provider_scope: ProviderScope
    external_group_id: str
    display_name: str
    group_type: str | None = None
    is_external: bool | None = None

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        return (
            self.provider_scope.platform,
            self.provider_scope.workspace_key,
            self.external_group_id,
        )


@dataclass(frozen=True)
class MemberRef:
    provider_scope: ProviderScope
    external_user_id: str

    @property
    def scoped_key(self) -> tuple[str, str, str]:
        return (
            self.provider_scope.platform,
            self.provider_scope.workspace_key,
            self.external_user_id,
        )


@dataclass(frozen=True)
class GroupMessage:
    provider_scope: ProviderScope
    external_message_id: str
    group: GroupRef
    author: MemberRef | None
    sent_at: datetime | None
    text_excerpt: str
    thread_key: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    member_lists: bool = True
    message_history: bool = True
    sendability: bool = True
    pagination: bool = True
    threads: bool = False


@dataclass(frozen=True)
class Sendability:
    allowed: bool | None
    reason: str = ""


@dataclass(frozen=True)
class GroupDiscoveryRequest:
    provider_scope: ProviderScope
    subject: str
    body: str = ""
    audience: tuple[MemberRef, ...] = ()
    audience_is_complete: bool = False
    source_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParticipantCoverageEvidence:
    matched: int | None
    total: int | None
    status: str

    @property
    def ratio(self) -> float | None:
        if self.matched is None or self.total in (None, 0):
            return None
        return self.matched / self.total


@dataclass(frozen=True)
class DiscussionEvidence:
    score: float
    excerpts: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryReadDenial:
    code: str
    reason: str


class GroupHistoryReadDenied(RuntimeError):
    """A provider conclusively denied history access for one scoped group."""

    def __init__(self, group: GroupRef, *, code: str, reason: str) -> None:
        super().__init__(reason)
        self.group = group
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class GroupEvidence:
    title_score: float | None = None
    participant_coverage: ParticipantCoverageEvidence | None = None
    discussion: DiscussionEvidence | None = None
    sendability: Sendability | None = None
    prior_delivery_count: int = 0
    members: tuple[MemberRef, ...] | None = None
    history_read_denial: HistoryReadDenial | None = None


@dataclass(frozen=True)
class GroupCandidate:
    group: GroupRef
    evidence: GroupEvidence
    rank: int = 0


class GroupDiscoveryOutcome(StrEnum):
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    NO_VERIFIED_GROUP = "no_verified_group"
    RETRYABLE_FAILURE = "retryable_failure"
    HISTORY_READ_DENIED = "history_read_denied"


@dataclass(frozen=True)
class GroupDiscoveryResult:
    outcome: GroupDiscoveryOutcome
    candidates: tuple[GroupCandidate, ...] = ()
    queries: tuple[str, ...] = ()
    provider_error: str | None = None
    reads: int = 0


class GroupDiscoveryProvider(Protocol):
    scope: ProviderScope
    capabilities: ProviderCapabilities

    def search_groups(self, query: str) -> Sequence[GroupRef]: ...

    def list_group_members(self, group: GroupRef) -> Sequence[MemberRef]: ...

    def read_recent_group_messages(
        self, group: GroupRef, *, limit: int
    ) -> Sequence[GroupMessage]: ...

    def get_group_sendability(self, group: GroupRef) -> Sendability: ...


class GroupDiscoveryPolicy(Protocol):
    def build_queries(self, request: GroupDiscoveryRequest) -> Sequence[str]: ...

    def participant_coverage(
        self,
        request: GroupDiscoveryRequest,
        members: Sequence[MemberRef],
    ) -> ParticipantCoverageEvidence: ...

    def title_score(self, request: GroupDiscoveryRequest, title: str) -> float: ...

    def discussion_score(
        self,
        request: GroupDiscoveryRequest,
        messages: Sequence[GroupMessage],
    ) -> DiscussionEvidence: ...

    def minimum_title_score(self, request: GroupDiscoveryRequest) -> float: ...

    def accepts(self, request: GroupDiscoveryRequest, candidate: GroupCandidate) -> bool: ...


class RetryableProviderError(RuntimeError):
    """A provider read failed before discovery could reach a conclusion."""


class GroupDiscoveryService:
    def __init__(self, provider: GroupDiscoveryProvider, policy: GroupDiscoveryPolicy):
        if provider.scope.platform != provider.scope.platform.strip():
            raise ValueError("provider platform must not have surrounding whitespace")
        self.provider = provider
        self.policy = policy

    def discover(self, request: GroupDiscoveryRequest) -> GroupDiscoveryResult:
        if request.provider_scope != self.provider.scope:
            raise ValueError("request provider scope does not match provider")
        queries = tuple(dict.fromkeys(str(q).strip() for q in self.policy.build_queries(request) if str(q).strip()))
        if not queries:
            return GroupDiscoveryResult(
                outcome=GroupDiscoveryOutcome.NO_VERIFIED_GROUP,
                queries=queries,
            )
        reads = 0
        groups: dict[tuple[str, str, str], GroupRef] = {}
        try:
            for query in queries:
                for group in self.provider.search_groups(query):
                    if group.provider_scope != request.provider_scope:
                        continue
                    groups.setdefault(group.dedup_key, group)
                    reads += 1

            candidate_groups = tuple(groups.values())
            prechecked_sendability: dict[tuple[str, str, str], Sendability] = {}
            # With no authoritative attendee filter, title selection is
            # independent of member reads. Do not fetch discarded rosters.
            if not request.audience_is_complete:
                for group in candidate_groups:
                    sendability = (
                        self.provider.get_group_sendability(group)
                        if self.provider.capabilities.sendability
                        else Sendability(None, "unavailable")
                    )
                    if self.provider.capabilities.sendability:
                        reads += 1
                    if sendability.allowed is not False:
                        prechecked_sendability[group.dedup_key] = sendability
                candidate_groups = tuple(
                    group for group in candidate_groups
                    if group.dedup_key in prechecked_sendability
                )
                threshold = self.policy.minimum_title_score(request)
                title_selected = tuple(
                    group for group in candidate_groups
                    if self.policy.title_score(request, group.display_name) >= threshold
                )
                if title_selected:
                    candidate_groups = title_selected

            staged: list[tuple[GroupRef, GroupEvidence]] = []
            audience_keys = {member.scoped_key for member in request.audience}
            for group in candidate_groups:
                coverage: ParticipantCoverageEvidence | None = None
                members: tuple[MemberRef, ...] | None = None
                if self.provider.capabilities.member_lists:
                    unique_members: dict[tuple[str, str, str], MemberRef] = {}
                    for member in self.provider.list_group_members(group):
                        if member.provider_scope != request.provider_scope:
                            raise ValueError("group member scope does not match request")
                        unique_members.setdefault(member.scoped_key, member)
                    members = tuple(unique_members.values())
                    reads += 1
                if request.audience_is_complete:
                    if not self.provider.capabilities.member_lists:
                        coverage = ParticipantCoverageEvidence(None, len(request.audience), "unavailable")
                    else:
                        member_keys = {member.scoped_key for member in members}
                        matched = len(audience_keys & member_keys)
                        coverage = self.policy.participant_coverage(request, members)
                        if coverage.matched is None:
                            coverage = ParticipantCoverageEvidence(matched, len(request.audience), coverage.status)
                        if coverage.status == "ineligible":
                            continue

                title_score = self.policy.title_score(request, group.display_name)
                sendability: Sendability | None = None
                if not request.audience_is_complete:
                    sendability = prechecked_sendability[group.dedup_key]
                elif self.provider.capabilities.sendability:
                    sendability = self.provider.get_group_sendability(group)
                    reads += 1
                    if sendability.allowed is False:
                        continue
                elif self.provider.capabilities.sendability is False:
                    sendability = Sendability(None, "unavailable")
                staged.append((group, GroupEvidence(title_score=title_score, participant_coverage=coverage, sendability=sendability, members=members)))

            minimum_title_score = self.policy.minimum_title_score(request)
            if any((evidence.title_score or 0) >= minimum_title_score for _, evidence in staged):
                staged = [
                    (group, evidence)
                    for group, evidence in staged
                    if (evidence.title_score or 0) >= minimum_title_score
                ]

            if not staged:
                return GroupDiscoveryResult(
                    outcome=GroupDiscoveryOutcome.NO_VERIFIED_GROUP,
                    queries=queries,
                    reads=reads,
                )

            candidates: list[GroupCandidate] = []
            for group, evidence in staged:
                discussion: DiscussionEvidence | None = None
                denial: HistoryReadDenial | None = None
                if not self.provider.capabilities.message_history:
                    discussion = DiscussionEvidence(0.0, ("unavailable",))
                else:
                    reads += 1
                    try:
                        messages = tuple(self.provider.read_recent_group_messages(group, limit=30))
                    except GroupHistoryReadDenied as exc:
                        if exc.group != group:
                            raise ValueError("history denial does not match requested group") from exc
                        denial = HistoryReadDenial(exc.code, exc.reason)
                    else:
                        discussion = self.policy.discussion_score(request, messages)
                candidate = GroupCandidate(
                    group=group,
                    evidence=GroupEvidence(
                        title_score=evidence.title_score,
                        participant_coverage=evidence.participant_coverage,
                        discussion=discussion,
                        sendability=Sendability(None, denial.reason) if denial else evidence.sendability,
                        members=evidence.members,
                        history_read_denial=denial,
                    ),
                )
                if denial is not None or self.policy.accepts(request, candidate):
                    candidates.append(candidate)

            candidates.sort(key=self._sort_key, reverse=True)
            ranked = tuple(
                GroupCandidate(group=item.group, evidence=item.evidence, rank=index + 1)
                for index, item in enumerate(candidates)
            )
            if not ranked:
                outcome = GroupDiscoveryOutcome.NO_VERIFIED_GROUP
            elif all(item.evidence.history_read_denial is not None for item in ranked):
                outcome = GroupDiscoveryOutcome.HISTORY_READ_DENIED
            elif any(item.evidence.history_read_denial is not None for item in ranked):
                outcome = GroupDiscoveryOutcome.AMBIGUOUS
            elif len(ranked) == 1:
                outcome = GroupDiscoveryOutcome.VERIFIED
            else:
                outcome = GroupDiscoveryOutcome.AMBIGUOUS
            return GroupDiscoveryResult(outcome=outcome, candidates=ranked, queries=queries, reads=reads)
        except RetryableProviderError as exc:
            return GroupDiscoveryResult(
                outcome=GroupDiscoveryOutcome.RETRYABLE_FAILURE,
                queries=queries,
                provider_error=str(exc),
                reads=reads,
            )

    @staticmethod
    def _sort_key(candidate: GroupCandidate) -> tuple[float, float, float]:
        evidence = candidate.evidence
        coverage = evidence.participant_coverage.ratio if evidence.participant_coverage else 0.0
        discussion = evidence.discussion.score if evidence.discussion else 0.0
        return (discussion, evidence.title_score or 0.0, coverage or 0.0)


class DingTalkGroupDiscoveryProvider:
    """Translate the existing DWS group reads to the neutral provider contract."""

    def __init__(self, dws: Any, *, workspace_key: str = "default") -> None:
        self.dws = dws
        self.scope = ProviderScope("dingtalk", workspace_key)
        self.capabilities = ProviderCapabilities()

    def search_groups(self, query: str) -> tuple[GroupRef, ...]:
        try:
            return tuple(
                GroupRef(self.scope, item.open_conversation_id, item.title)
                for item in self.dws.search_conversations(query)
            )
        except Exception as exc:  # provider adapters classify transport failures
            raise RetryableProviderError(str(exc)) from exc

    def list_group_members(self, group: GroupRef) -> tuple[MemberRef, ...]:
        try:
            ids = self.dws.list_group_member_open_dingtalk_ids(group.external_group_id)
            return tuple(MemberRef(self.scope, value) for value in ids)
        except Exception as exc:
            raise RetryableProviderError(str(exc)) from exc

    def read_recent_group_messages(self, group: GroupRef, *, limit: int) -> tuple[GroupMessage, ...]:
        try:
            conversation = DingTalkConversationShim(group.external_group_id, group.display_name)
            return tuple(
                GroupMessage(self.scope, str(index), group, None, None, str(message.content)[:1200])
                for index, message in enumerate(self.dws.read_recent_messages(conversation, limit=limit))
            )
        except Exception as exc:
            if is_non_retryable_dingtalk_group_read_denial(exc):
                raise GroupHistoryReadDenied(group, code=exc.code, reason=exc.business_message) from exc
            raise RetryableProviderError(str(exc)) from exc

    def get_group_sendability(self, group: GroupRef) -> Sendability:
        return Sendability(True)


@dataclass(frozen=True)
class DingTalkConversationShim:
    open_conversation_id: str
    title: str
    single_chat: bool = False
    unread_point: int = 0
    # DWS message-list reads use the same conversation contract as the worker.
    # Meeting discovery has no cursor, so an unset timestamp means "recent".
    last_message_create_at: int | None = None
