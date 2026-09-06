# Meeting Business Audience Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route business meeting follow-ups to the evidence-backed business group even when the calendar roster is missing or lists two attendees, and reserve direct messages for verified personal one-to-one meetings.

**Architecture:** Preserve the source of the attendee roster in `MeetingSource`, add an explicit `audience_scope` to the agent's structured decision, and validate the combination before any delivery. The agent remains responsible for finding and ranking business groups; the deterministic layer blocks an invalid direct route and prevents group-send failures from falling back to a person.

**Tech Stack:** Python 3, Pydantic v2, JSON Schema, SQLite job persistence, pytest, DingTalk DWS adapter.

---

## File structure

| File | Responsibility |
| --- | --- |
| `app/meeting_alignment_models.py` | Carries roster provenance and the declared business/personal audience scope in the typed decision. |
| `app/schemas/meeting_alignment_decision.schema.json` | Requires the same scope in the routed Codex structured output. |
| `app/meeting_alignment_source.py` | Preserves calendar-versus-transcript roster provenance when building a `MeetingSource`. |
| `app/meeting_alignment_agent.py` | Prompts for content-first audience selection and rejects direct targets unless the source proves a personal 1:1. |
| `app/meeting_alignment_delivery.py` | Removes all creator-direct fallbacks and treats an unsendable selected group as retryable. |
| `tests/test_meeting_alignment_source.py` | Verifies transcript rosters stay incomplete and calendar rosters stay complete. |
| `tests/test_meeting_alignment_agent.py` | Verifies prompt and target validation for business/personal and complete/incomplete rosters. |
| `tests/test_meeting_alignment_delivery.py` | Verifies no group-delivery failure can create a direct message. |

### Task 1: Preserve attendee-roster provenance

**Files:**
- Modify: `app/meeting_alignment_models.py`
- Modify: `app/meeting_alignment_source.py`
- Test: `tests/test_meeting_alignment_source.py`

- [ ] **Step 1: Write the failing source tests**

Add these tests beside the existing transcript-roster tests:

```python
def test_read_meeting_source_marks_calendar_roster_complete():
    source = read_meeting_source(
        FakeDws(), "minutes-1", calendar_evidence=calendar_evidence()
    )
    assert source.attendee_evidence == "calendar"
    assert source.attendee_roster_complete is True


def test_read_meeting_source_marks_transcript_roster_incomplete():
    source = read_meeting_source(
        FakeDws(),
        "minutes-1",
        calendar_evidence=calendar_evidence(
            event_id="transcript:minutes-1", source="transcript"
        ),
    )
    assert source.attendee_evidence == "transcript"
    assert source.attendee_roster_complete is False
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
pytest tests/test_meeting_alignment_source.py -q
```

Expected: the new tests fail because `MeetingSource` has no attendee-evidence fields.

- [ ] **Step 3: Add the typed provenance fields**

Extend `MeetingSource` in `app/meeting_alignment_models.py`:

```python
class MeetingSource(StrictModel):
    meeting_id: str
    title: str
    status: Literal["ended"]
    started_at: str
    ended_at: str
    participants: list[MeetingParticipant]
    attendee_evidence: Literal["calendar", "transcript"]
    attendee_roster_complete: bool
    creator: MeetingParticipant | None = None
    current_user_id: str
    summary: str
    transcript: list[TranscriptLine]
    source_url: str = ""
```

In `normalize_meeting_source`, accept the two values as keyword-only arguments and place them in the returned model:

```python
def normalize_meeting_source(
    info: dict[str, Any],
    transcription: dict[str, Any] | list[dict[str, Any]],
    *,
    current_user_id: str,
    attendee_evidence: Literal["calendar", "transcript"] = "calendar",
    attendee_roster_complete: bool = True,
    # existing arguments remain unchanged
) -> MeetingSource:
    # existing validation remains unchanged
    return MeetingSource(
        # existing fields remain unchanged
        attendee_evidence=attendee_evidence,
        attendee_roster_complete=attendee_roster_complete,
    )
```

Pass the evidence through `read_meeting_source`:

```python
return normalize_meeting_source(
    _merge_calendar_evidence(info, calendar_evidence, current_user_id, transcription),
    transcription,
    current_user_id=current_user_id,
    attendee_evidence=calendar_evidence.source,
    attendee_roster_complete=calendar_evidence.source == "calendar",
    summary=summary,
    meeting_id=meeting_id,
    creator=creator or calendar_evidence.creator,
)
```

Update every direct test fixture that constructs `MeetingSource` to supply
`attendee_evidence="calendar"` and `attendee_roster_complete=True` unless the
test explicitly exercises a transcript roster.

- [ ] **Step 4: Run the focused source suite**

Run:

```bash
pytest tests/test_meeting_alignment_source.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the provenance boundary**

```bash
git add app/meeting_alignment_models.py app/meeting_alignment_source.py tests/test_meeting_alignment_source.py
git commit -m "feat(meeting): preserve attendee roster provenance"
```

### Task 2: Make content scope explicit in the agent decision

**Files:**
- Modify: `app/meeting_alignment_models.py`
- Modify: `app/schemas/meeting_alignment_decision.schema.json`
- Modify: `app/meeting_alignment_agent.py`
- Test: `tests/test_meeting_alignment_agent.py`

- [ ] **Step 1: Write failing scope-validation tests**

Add tests with existing `source()` and `send_payload_with_target()` helpers:

```python
def test_agent_rejects_business_direct_target_for_calendar_one_to_one():
    payload = send_payload_with_target(direct_target_for("alex", "Alex"))
    payload["audience_scope"] = "business"
    with pytest.raises(MeetingAlignmentTargetError, match="business send requires a group"):
        MeetingAlignmentAgent(FakeMeetingCodex(payload)).decide(source(participant_count=2))


def test_agent_rejects_personal_direct_target_for_transcript_roster():
    payload = send_payload_with_target(direct_target_for("alex", "Alex"))
    payload["audience_scope"] = "personal"
    with pytest.raises(MeetingAlignmentTargetError, match="complete calendar roster"):
        MeetingAlignmentAgent(FakeMeetingCodex(payload)).decide(
            source(participant_count=2, attendee_evidence="transcript", attendee_roster_complete=False)
        )


def test_agent_accepts_business_group_for_transcript_roster():
    payload = send_payload_with_target(group_target("cid-project", "项目交付群"))
    payload["audience_scope"] = "business"
    decision = MeetingAlignmentAgent(FakeMeetingCodex(payload)).decide(
        source(participant_count=2, attendee_evidence="transcript", attendee_roster_complete=False)
    )
    assert decision.target is not None
    assert decision.target.kind == "group"
```

Make `no_action_payload()` include `"audience_scope": "business"` so it
continues to satisfy the strict output schema.

- [ ] **Step 2: Run the focused agent suite and verify failure**

Run:

```bash
pytest tests/test_meeting_alignment_agent.py -q
```

Expected: FAIL because `audience_scope` is not declared in the model/schema
and existing target validation still permits direct business delivery.

- [ ] **Step 3: Add `audience_scope` to the contract**

Add this field to `MeetingAlignmentDecision` before `target`:

```python
audience_scope: Literal["business", "personal"]
```

Add the JSON Schema property and required entry:

```json
"audience_scope": {
  "enum": ["business", "personal"],
  "title": "Audience Scope",
  "type": "string"
}
```

Add `"audience_scope"` to the top-level `required` array.

Replace the target-selection branch in `build_meeting_alignment_prompt` with
this single content-first contract; it intentionally applies to two-person
and larger meetings alike:

```text
先把 audience_scope 判为 business 或 personal。客户、项目、产品、需求、
交付、排期、测试、部署、客户沟通或跨团队行动均是 business；不能因为日历
只列两个人或逐字稿只识别到一位他人说话者而判为 personal。

- audience_scope=business：必须用 DWS 搜索并排序业务承接群，target 必须是
  group，候选必须按项目承接证据排序并选择第一项。多个合理群时自行选择证据
  最强者；没有有证据的可发送群时返回 action=no_action，绝不返回 direct。
- audience_scope=personal：只有 attendee_evidence=calendar 且
  attendee_roster_complete=true、participants 恰好为 Derek 与一位他人时，才可
  target=direct 到该他人。否则返回 action=no_action。
```

Replace `_validate_source_aware_target` with the following complete decision
boundary (keep `_canonical_person_name` and existing one-to-one identity checks):

```python
def _validate_source_aware_target(source: MeetingSource, decision: MeetingAlignmentDecision) -> None:
    if decision.action == "no_action":
        return
    target = decision.target
    if target is None:
        raise MeetingAlignmentTargetError("send requires an explicit target")
    if decision.audience_scope == "business":
        if target.kind != "group":
            raise MeetingAlignmentTargetError("business send requires a group target")
        return
    if (
        source.attendee_evidence != "calendar"
        or not source.attendee_roster_complete
        or len(source.participants) != 2
    ):
        raise MeetingAlignmentTargetError(
            "personal direct send requires a complete calendar roster with exactly two participants"
        )
    if target.kind != "direct":
        raise MeetingAlignmentTargetError("personal one-to-one send requires a direct target")
    _validate_one_to_one_direct_target(source, target)
```

Extract the existing two-person counterpart checks into
`_validate_one_to_one_direct_target(source, target)` without changing their
identity semantics. Delete `_validate_multi_party_direct_target_creator` and
its call sites.

- [ ] **Step 4: Run agent and schema-adjacent tests**

Run:

```bash
pytest tests/test_meeting_alignment_agent.py tests/test_routed_result_privacy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the structured decision change**

```bash
git add app/meeting_alignment_models.py app/schemas/meeting_alignment_decision.schema.json app/meeting_alignment_agent.py tests/test_meeting_alignment_agent.py
git commit -m "feat(meeting): route business follow-ups to groups"
```

### Task 3: Remove creator-direct delivery fallbacks

**Files:**
- Modify: `app/meeting_alignment_delivery.py`
- Test: `tests/test_meeting_alignment_delivery.py`

- [ ] **Step 1: Replace fallback tests with failure tests**

Delete the tests that assert a multi-person creator direct send succeeds and
add the following tests:

```python
def test_unsendable_group_retries_without_direct_message():
    dws = FakeDws()
    dws.conversation_info["singleChat"] = True
    with pytest.raises(MeetingDeliveryRetry, match="selected target is not a sendable group"):
        deliver_meeting_alignment(send_decision(), meeting_source(), dws, message_sender=sender(), delivery_key="meeting:1")
    assert dws.sent == []


def test_direct_delivery_rejects_business_scope():
    decision = send_decision(target="direct", audience_scope="business")
    with pytest.raises(MeetingDeliveryError, match="business meeting delivery requires a group"):
        deliver_meeting_alignment(decision, meeting_source(one_to_one=True), FakeDws(), message_sender=sender(), delivery_key="meeting:1")
```

Adapt `send_decision()` so its default remains a business group decision and
accepts an explicit `audience_scope` argument for direct-message cases.

- [ ] **Step 2: Run the delivery suite and verify failure**

Run:

```bash
pytest tests/test_meeting_alignment_delivery.py -q
```

Expected: FAIL because an unsendable group currently invokes
`_creator_direct_identity` and sends a direct message.

- [ ] **Step 3: Make delivery enforce the same boundary**

At the start of `deliver_meeting_alignment`, after checking `action`, add:

```python
if decision.audience_scope == "business" and decision.target and decision.target.kind != "group":
    raise MeetingDeliveryError("business meeting delivery requires a group target")
if decision.audience_scope == "personal" and (
    source.attendee_evidence != "calendar"
    or not source.attendee_roster_complete
    or len(source.participants) != 2
):
    raise MeetingDeliveryError(
        "personal direct delivery requires a complete calendar two-person roster"
    )
```

Replace the `group_state == "unsendable"` branch with:

```python
if group_state == "unsendable":
    raise MeetingDeliveryRetry("selected target is not a sendable group")
```

Delete `_creator_direct_identity`. Simplify `_direct_target_participant` to
only find the unique non-current participant in a two-person source; remove
the multi-party creator branch entirely.

- [ ] **Step 4: Run the delivery suite**

Run:

```bash
pytest tests/test_meeting_alignment_delivery.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the no-fallback sender boundary**

```bash
git add app/meeting_alignment_delivery.py tests/test_meeting_alignment_delivery.py
git commit -m "fix(meeting): prevent group delivery direct fallback"
```

### Task 4: Verify the integrated queue contract and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Test: `tests/test_meeting_alignment.py`
- Test: `tests/test_documentation_contract.py`

- [ ] **Step 1: Add an integration regression test for transcript evidence**

In `tests/test_meeting_alignment.py`, add a job whose roster evidence source
is `transcript`, whose structured agent decision is `business` plus a group
target, and whose fake DWS marks that group sendable:

```python
assert job.status == "sent"
assert job.target_kind == "group"
assert dws.sent[0]["conversation_id"] == "cid-project"
```

Add the companion negative assertion for a transcript-sourced `personal`
direct decision:

```python
assert job.status == "failed"
assert "complete calendar roster" in job.error
assert dws.sent == []
```

- [ ] **Step 2: Run the integration test and verify failure before wiring fixtures**

Run:

```bash
pytest tests/test_meeting_alignment.py -q
```

Expected: the fixture or old decision payload fails until it supplies
`audience_scope` and roster provenance.

- [ ] **Step 3: Update the operational contract**

Replace the meeting-delivery paragraph in `README.md` and the matching
architecture/runtime text with these requirements:

```text
业务会议按客户、项目、模块、决策、Owner 与交付行动寻找并发送到最能承接工作的
团队群；即使日历只列两人也不因此私信。日历用于补充参会人证据，逐字稿发言人
名单不等于完整参会名单。只有日历可靠确认严格 1:1 且会议内容为非业务个人沟通
时才可私信另一位参会人。多个业务群候选由 Agent 按承接证据自行排序选择；业务
内容没有可信群时不发送，群不可发送时重试或选择下一候选，绝不降级私信。
```

Update `tests/test_documentation_contract.py` to assert the new phrases
`"逐字稿发言人名单不等于完整参会名单"` and `"绝不降级私信"` appear in the
public contract documentation.

- [ ] **Step 4: Run focused integration and documentation tests**

Run:

```bash
pytest tests/test_meeting_alignment.py tests/test_documentation_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the full verification suite**

Run:

```bash
pytest tests/test_meeting_alignment_source.py tests/test_meeting_alignment_agent.py tests/test_meeting_alignment_delivery.py tests/test_meeting_alignment.py tests/test_routed_result_privacy.py tests/test_documentation_contract.py -q
```

Expected: PASS with no new failures.

- [ ] **Step 6: Commit integration coverage and documentation**

```bash
git add README.md docs/architecture.md docs/runtime-mechanism.md tests/test_meeting_alignment.py tests/test_documentation_contract.py
git commit -m "docs(meeting): document content-first audience routing"
```

- [ ] **Step 7: Activate and verify the runtime after all code commits**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
python -m app.cli quality-check --db "$CEO_WORKER_DB"
```

Expected: `com.ceo-agent-service.main` reports a new running process, and the
quality check reports no unresolved `failed` or `processing` meeting backlog.

## Plan self-review

- Spec coverage: Tasks 1-2 prevent transcript-only 1:1 classification;
  Task 2 makes business-versus-personal scope explicit and lets the agent rank
  multiple business groups; Task 3 removes direct fallbacks; Task 4 covers
  queue integration, docs, and live runtime activation.
- Placeholder scan: no TBD/TODO markers, unspecified test names, or generic
  error-handling steps remain.
- Type consistency: `attendee_evidence`, `attendee_roster_complete`, and
  `audience_scope` use the same names in model, source, agent, delivery, and
  tests.
