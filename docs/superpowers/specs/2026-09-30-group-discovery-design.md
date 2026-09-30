# Cross-Platform Group Discovery Design

**Status:** design revised for review; runtime implementation has not started.

**Scope:** provide a reusable capability for finding collaboration-platform groups with inspectable evidence. It does not choose the final outbound target, compose a message, or send it.

## Context

Meeting summaries currently combine provider search, group-member checks, title matching, recent-message reads, evidence ranking, and business fallback logic in the meeting-alignment path. The staged flow is reusable, but its low-level implementation is DingTalk-specific. The incident exposed two requirements:

1. Search order must not hide a valid group behind unrelated results.
2. Recent-message reads are the expensive semantic step and should run only after cheaper hard filters reduce the candidate set.

The reusable boundary is **group discovery**, not general target selection. A meeting, sales notice, or another caller remains responsible for deciding whether to send, where to fall back, and what content is allowed.

The current service has a live DingTalk read surface through DWS. Lark and Slack adapters are reserved by this design but are not live integrations in this repository yet. An interface or fixture for a provider is not evidence of production support.

## Goals

- Return candidate groups with explicit, bounded, inspectable evidence.
- Reuse one staged discovery flow across meeting, sales, and future group-based notifications.
- Keep core models and policy independent of DingTalk, Lark, and Slack field names.
- Apply hard filters before recent-message reads when the source has a complete participant or audience roster.
- Preserve semantic verification; member overlap and group-name similarity alone do not prove business ownership.
- Distinguish no verified group from a retryable provider failure.
- Keep caller-specific fallback, authorization, platform preference, and final target selection outside discovery.
- Make provider reads request-scoped and deduplicated.

## Non-goals

- Selecting a person or deciding between group and direct delivery.
- Sending, withdrawing, or retrying a message.
- Generating message content or invoking an Agent.
- Comparing groups by raw IDs or names across providers.
- Building one universal abstraction for documents, projects, people, and groups.
- Adding a long-lived cross-request cache before read volume and freshness requirements are measured.

## Platform and identity rules

The first version accepts one provider scope per discovery request. A scope is the combination of platform and the provider account, workspace, tenant, or organization used for the read. The provider adapter owns authentication and translates external responses into neutral models.

Neutral models:

- PlatformKind = dingtalk | lark | slack
- ProviderScope = platform + opaque workspace_key
- GroupRef = provider scope + opaque external_group_id + display name + optional group type/external flag
- MemberRef = provider scope + opaque external_user_id
- GroupMessage = provider scope + opaque message ID + group + optional author + timestamp + bounded text excerpt + optional thread key

All external IDs are opaque and valid only inside their provider scope. The domain must not expose open_conversation_id, open_dingtalk_id, Lark chat_id, Slack channel IDs, provider-specific receipt fields, or provider command names. Candidate deduplication is by (platform, workspace_key, external_group_id), never by a display name.

## Architecture

Business caller -> GroupDiscoveryRequest + GroupDiscoveryPolicy -> GroupDiscoveryService -> provider search, scoped deduplication, hard filters, recent-message evidence, and ranked GroupDiscoveryResult -> caller-specific target decision and delivery.

### Provider boundary

The provider protocol exposes:

- search_groups(query) -> GroupRef[]
- list_group_members(group) -> MemberRef[]
- read_recent_group_messages(group, limit) -> GroupMessage[]
- get_group_sendability(group) -> Sendability
- capabilities -> member_lists, message_history, sendability, pagination, threads

The provider owns transport, authentication, pagination, rate limits, and response parsing. The discovery service owns ordering, filtering, deduplication, evidence assembly, and bounded error classification. A capability a provider cannot supply is returned as unavailable; it is never silently converted to false or an empty response.

The first adapter wraps existing DingTalk DWS methods. Lark and Slack adapters can be added independently against the same neutral fixtures. Their presence in the protocol does not enable runtime use until authentication, reads, and external readback are verified.

### Request and policy boundary

GroupDiscoveryRequest contains provider scope, subject, body, audience member references, an audience-complete flag, and source context. GroupDiscoveryPolicy supplies query generation, participant eligibility, title scoring, discussion scoring, and minimum evidence.

Policies are business-specific but provider-agnostic. Meeting and sales/recruiting policies can use different audience thresholds, title terms, discussion evidence, and minimum scores without changing the service.

## Discovery flow

### Complete audience roster

search within one provider scope -> paginate and deduplicate -> read member lists and apply audience coverage -> score title against subject/body -> check sendability -> read recent messages for survivors -> rank by policy evidence.

The participant filter comes first because it is the strongest cheap audience constraint. A group-name match is only a discovery signal. Recent messages are still required for a surviving unique candidate because audience overlap does not prove that the group currently owns the business topic.

### Incomplete audience roster

search within one provider scope -> paginate and deduplicate -> score title -> check sendability -> read recent messages for survivors -> return participant coverage as unavailable.

The service must not invent coverage from member counts or display names. The caller may require confirmation or decline to send when evidence is insufficient.

### Candidate outcomes

- VERIFIED: one candidate clearly meets the policy evidence requirement.
- AMBIGUOUS: multiple candidates remain materially plausible.
- NO_VERIFIED_GROUP: reads completed, but no candidate met the requirement.
- RETRYABLE_FAILURE: a provider read failed in a retryable way, or a partial result cannot support a conclusion.

NO_VERIFIED_GROUP never means “send to the organizer”. That decision remains with the business caller.

## Evidence model

Each candidate contains title-match evidence, participant-coverage evidence, discussion evidence, recurring-delivery evidence, and sendability evidence. Evidence preserves positive and negative facts that affect the decision: coverage such as 3/4, matched subject terms, bounded recent excerpts with timestamps, prior successful delivery, and reasons a candidate was filtered. Raw message bodies remain internal; callers receive bounded excerpts.

## Single-platform first, multi-platform later

Discovery should not fan out to all platforms automatically in its first implementation. Provider search cost, identity semantics, permissions, and message history differ. A caller chooses one provider scope and receives explicit provenance.

When at least two providers have read and evidence parity, a separate MultiPlatformGroupDiscoveryCoordinator may fan out to selected adapters. It preserves provenance, applies a caller-supplied platform preference and tie-breaker, and returns ambiguity when equally supported candidates remain. It must not merge candidates solely because names match or silently prefer the adapter that returned first.

## Caller integration

### Meeting summaries

The meeting path converts MeetingSource into a GroupDiscoveryRequest and uses a MeetingGroupDiscoveryPolicy. The existing Meeting Alignment Agent continues to decide audience scope, public versus sensitive content, interpretation of multiple candidates, and whether a caller-specific fallback is allowed. The Agent may only select a returned candidate and cannot invent an external group ID. Existing target validation and delivery idempotency remain in the meeting path.

### Sales notifications

A future sales or recruiting notification supplies its own policy with audience roster, title terms, message evidence rules, and minimum coverage. It may choose a sales group, request confirmation for ambiguity, or use a separately defined direct fallback. None of those choices are embedded in discovery.

## Error handling and operational boundaries

- Provider search/member/message failures retain provider error metadata and return RETRYABLE_FAILURE when retryable.
- A partial provider response is not treated as an empty search.
- A missing stable participant identity lowers evidence; it does not become a guessed identity.
- The service does not downgrade a provider failure into NO_VERIFIED_GROUP.
- Request-scoped memoization prevents duplicate reads for the same scoped identifiers.
- Delivery remains outside discovery and uses the selected candidate's provider-scoped reference through the caller's delivery path.

## Migration plan

### Phase 1: neutral extraction with DingTalk

Add neutral request, result, evidence, provider, capability, and policy models; adapt DWS without leaking DWS field names; implement the staged service while preserving current meeting thresholds; convert meeting alignment while retaining target and delivery validation.

### Phase 2: reusable verification

Add focused tests for pagination and deduplication, audience coverage, title-filter order, one-candidate versus multi-candidate message reads, incomplete-roster behavior, capability gaps, retryable failures, bounded evidence, scoped read deduplication, and no implicit direct fallback.

### Phase 3: Lark adapter

Add a read-only Lark adapter, provider contract tests, authentication and workspace-scope configuration, and external readback. Do not advertise Lark support until those gates pass.

### Phase 4: Slack adapter

Add a read-only Slack adapter with the same contract and evidence gates. Keep Slack channel and thread IDs inside the adapter boundary.

### Phase 5: second caller and optional coordinator

Integrate one real sales notification flow after meeting migration is stable. Compare read counts, ambiguous outcomes, false group choices, and delivery outcomes. Add multi-platform coordination only after two production adapters have comparable evidence behavior.

## Acceptance criteria

1. Core models contain no provider-specific ID fields.
2. A valid group after the first raw search page remains discoverable within a provider scope.
3. A unique roster- and title-qualified candidate causes one recent-message read, not a read of every raw search result.
4. Multiple candidates receive comparable discussion evidence before ranking.
5. Provider capability gaps and failures remain explicit and never silently trigger direct sending.
6. The meeting caller retains control over final target and fallback policy.
7. A sales caller can supply a different policy without changing the service.
8. Lark and Slack are added through provider adapters and contract tests, with no provider identity leaking into the neutral service.
