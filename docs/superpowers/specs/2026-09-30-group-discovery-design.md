# Reusable DingTalk Group Discovery Design

**Status:** design approved for review; runtime implementation has not started.

**Scope:** provide a reusable capability for finding DingTalk groups with
verifiable evidence. The capability does not choose the final outbound target,
compose a message, or send it.

## Context

Meeting summaries currently combine DingTalk search, group-member checks,
title matching, recent-message reads, evidence ranking, and business fallback
logic in the meeting-alignment path. The low-level DWS reads are reusable, but
the discovery flow is not. The immediate incident exposed two requirements:

1. Search order must not hide a valid group behind unrelated results.
2. Recent-message reads are the expensive semantic step and should run only
   after cheaper hard filters reduce the candidate set.

The reusable boundary is therefore group discovery, not general target
selection. A meeting, sales notice, or another caller remains responsible for
deciding whether to send, where to fall back, and what content is allowed.

## Goals

- Return candidate groups with explicit, inspectable evidence.
- Reuse one staged discovery flow across meeting, sales, and future group-based
  notifications.
- Apply hard filters before recent-message reads when the source has a complete
  participant or audience roster.
- Preserve semantic verification for the remaining candidates; member overlap
  and group-name similarity alone do not prove business ownership.
- Distinguish no verified group from a retryable DingTalk/provider failure.
- Keep caller-specific fallback and authorization rules outside the discovery
  service.
- Make provider reads request-scoped and deduplicated.

## Non-goals

- Selecting a person or deciding between group and direct delivery.
- Sending, withdrawing, or retrying a DingTalk message.
- Generating message content or invoking an Agent.
- Building a universal discovery framework for documents, projects, people, and
  groups in one abstraction.
- Adding a cross-task long-lived cache before request-scoped deduplication is
  measured.

## Architecture

```text
Business caller
  └─ GroupDiscoveryRequest + GroupDiscoveryPolicy
       └─ GroupDiscoveryService
            ├─ query generation
            ├─ group search and deduplication
            ├─ hard filters
            │    ├─ participant/audience coverage
            │    ├─ title/topic match
            │    └─ sendability
            ├─ recent-message evidence for survivors
            └─ ranked GroupDiscoveryResult
```

### Provider boundary

The service depends on a small provider protocol rather than the concrete DWS
client:

```python
class GroupDiscoveryProvider(Protocol):
    def search_groups(self, query: str) -> tuple[GroupRef, ...]: ...
    def list_group_members(self, conversation_id: str) -> frozenset[str]: ...
    def read_recent_group_messages(
        self, conversation_id: str, *, limit: int
    ) -> tuple[GroupMessage, ...]: ...
    def get_group_sendability(self, conversation_id: str) -> Sendability: ...
```

The first implementation adapts the existing DWS methods. The provider owns
transport and response parsing; the discovery service owns ordering, filtering,
deduplication, and evidence assembly.

### Policy boundary

Business-specific rules are injected through a policy object:

```python
class GroupDiscoveryPolicy(Protocol):
    def build_queries(self, request: GroupDiscoveryRequest) -> tuple[str, ...]: ...
    def participant_eligible(
        self,
        request: GroupDiscoveryRequest,
        group_members: frozenset[str],
    ) -> bool: ...
    def title_score(self, request: GroupDiscoveryRequest, group_title: str) -> float: ...
    def discussion_score(
        self,
        request: GroupDiscoveryRequest,
        messages: tuple[GroupMessage, ...],
    ) -> DiscussionEvidence: ...
    def minimum_evidence(self, request: GroupDiscoveryRequest) -> EvidenceRequirement: ...
```

The policy defines thresholds and evidence semantics. The service does not
know what “sales”, “recruiting”, or “meeting” means.

## Discovery flow

### Complete audience roster

When the caller supplies a complete, stable roster:

```text
search groups
→ deduplicate by conversation_id
→ read member lists and apply audience coverage
→ score group title against subject/body
→ check sendability
→ read recent messages for survivors
→ rank candidates by policy evidence
```

The participant filter comes first because it is the strongest cheap audience
constraint. A group-name match is only a discovery signal. Recent messages are
still required for the surviving unique candidate because audience overlap does
not prove that the group currently owns the business topic.

### Incomplete audience roster

When the roster is missing or cannot be verified:

```text
search groups
→ deduplicate by conversation_id
→ score group title against subject/body
→ check sendability
→ read recent messages for survivors
→ return participant_coverage=unavailable
```

The service must not invent coverage from member counts or display names. The
caller may require human confirmation or decline to send when the evidence is
insufficient.

### Candidate outcomes

```python
class GroupDiscoveryOutcome(str, Enum):
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    NO_VERIFIED_GROUP = "no_verified_group"
    RETRYABLE_FAILURE = "retryable_failure"
```

- `VERIFIED`: one candidate clearly meets the policy evidence requirement.
- `AMBIGUOUS`: multiple candidates remain materially plausible.
- `NO_VERIFIED_GROUP`: reads completed, but no candidate met the requirement.
- `RETRYABLE_FAILURE`: search, member, message, or sendability reads failed in
  a retryable way.

`NO_VERIFIED_GROUP` never means “send to the organizer”. That decision remains
with the business caller.

## Evidence model

Each candidate contains:

```python
class GroupEvidence:
    title_match: TitleMatchEvidence | None
    participant_coverage: ParticipantCoverageEvidence | None
    discussion: DiscussionEvidence | None
    recurring_delivery: RecurringDeliveryEvidence | None
    sendability: SendabilityEvidence | None
```

Evidence must preserve both positive and negative facts where they affect the
decision. Examples include `3/4` participant coverage, the matched subject
terms, recent message excerpts with timestamps, prior successful delivery, and
the reason a candidate was filtered.

The service returns a `GroupCandidate` with its rank and evidence, plus a
request-level summary containing search queries, counts, and provider reads.
Raw message bodies remain internal; callers receive bounded evidence excerpts.

## Caller integration

### Meeting summaries

The meeting path will convert `MeetingSource` into a
`GroupDiscoveryRequest` and use a `MeetingGroupDiscoveryPolicy`. The existing
Meeting Alignment Agent continues to decide:

- `audience_scope`;
- public versus sensitive content;
- how to interpret multiple business candidates;
- whether a caller-specific fallback is allowed.

The Agent may only select a group from the returned candidates. It cannot invent
a conversation ID. Existing target validation and delivery idempotency remain
in the meeting path.

### Sales notifications

A future sales or recruiting notification will provide a sales policy with its
own audience roster, title terms, message evidence rules, and minimum coverage.
It may choose a sales group, request human confirmation for ambiguity, or use a
separately defined direct fallback. None of those choices are embedded in the
discovery service.

## Error handling and operational boundaries

- Provider search/member/message failures retain their provider error and return
  `RETRYABLE_FAILURE` when retryable.
- A partial provider response is not treated as an empty search.
- A missing stable participant identity lowers evidence; it does not become a
  guessed identity.
- The service does not downgrade a provider failure into
  `NO_VERIFIED_GROUP`.
- Request-scoped memoization prevents duplicate search, member, message, and
  sendability reads for the same identifiers.
- Long-lived caching is deferred until read volume and freshness requirements
  are measured.

## Migration plan

### Phase 1: extract the meeting behavior

- Add the shared request, result, evidence, provider, and policy models.
- Adapt DWS to the provider protocol.
- Implement the staged service while preserving current meeting thresholds.
- Convert meeting alignment to the service and retain existing target and
  delivery validation.

### Phase 2: establish reusable verification

Add focused tests for:

- search deduplication;
- audience coverage filtering;
- title filtering after audience filtering;
- one-candidate versus multi-candidate message reads;
- incomplete-roster behavior;
- no verified group versus retryable provider failure;
- bounded evidence and request-scoped read deduplication;
- no implicit direct fallback.

### Phase 3: add one second caller

Integrate one real sales notification flow only after the meeting migration is
stable. Compare provider read counts, ambiguous outcomes, false group choices,
and delivery outcomes against the existing meeting path before broadening the
abstraction.

## Acceptance criteria

The design is ready for implementation when:

1. Meeting discovery produces the same or better target evidence for existing
   cases, including the KA sales roundtable case.
2. A valid group after the first raw search page remains discoverable.
3. A unique roster- and title-qualified candidate causes one recent-message
   read, not a read of every raw search result.
4. Multiple candidates receive comparable discussion evidence before ranking.
5. Provider failures remain retryable and never silently trigger direct sending.
6. The meeting caller retains control over final target and fallback policy.
7. A second caller can supply a different policy without changing the service.

