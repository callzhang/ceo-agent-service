# Meeting Alignment Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically analyze Derek-attended DingTalk meetings ten minutes after they end and, only when alignment work is needed, send one auditable message to the best group or the other one-to-one participant.

**Architecture:** Add independent `meeting-producer` and `meeting-consumer` loops inside the existing `com.ceo-agent-service.main` process. They use a dedicated SQLite queue and Meeting Alignment Agent, while reusing DWS, Codex session auditing, delivery primitives, and the reply-agent History presentation contract.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, Codex CLI structured output, DingTalk `dws`, FastAPI audit UI, pytest.

---

## File Map

Create focused units rather than adding meeting semantics to `app/worker.py` or
`app/task_agent.py`:

- `app/meeting_alignment_models.py`: source, decision, queue, run, target, and
  mention contracts.
- `app/schemas/meeting_alignment_decision.schema.json`: Codex structured-output
  contract.
- `app/meeting_alignment_source.py`: Minutes pagination and normalization.
- `app/meeting_alignment_agent.py`: prompt, Codex runner, and decision parsing.
- `app/meeting_alignment_delivery.py`: target validation, identity resolution,
  real mentions, sending, and ambiguous-result verification.
- `app/meeting_alignment.py`: producer and consumer orchestration only.
- `app/history.py`: shared reply/meeting History feed representation and merge.

Modify existing integration surfaces:

- `app/store.py`: meeting queue/run persistence and unified History queries.
- `app/dws_client.py`: complete transcript pagination and target verification
  helpers.
- `app/cli.py`: settings, commands, recovery, loops, and service components.
- `app/config.py`, `.env.example`, and
  `launchd/com.ceo-agent-service.main.plist`: interval configuration.
- `app/audit_web.py`: render reply and meeting records through the same History
  components.
- `README.md`: operating and verification documentation.

Do not modify or revert unrelated work already present in `app/task_agent.py`,
`tests/test_task_agent.py`, or `.tmp/`.

### Task 1: Define the meeting contracts and structured-output schema

**Files:**
- Create: `app/meeting_alignment_models.py`
- Create: `app/schemas/meeting_alignment_decision.schema.json`
- Create: `tests/test_meeting_alignment_models.py`

- [ ] **Step 1: Write failing model validation tests**

```python
from pydantic import ValidationError
import pytest

from app.meeting_alignment_models import MeetingAlignmentDecision


def valid_send_decision():
    return {
        "action": "send",
        "trigger_reasons": ["unresolved_disagreement"],
        "topics": [{
            "title": "上线范围",
            "state": "unresolved",
            "views": [
                {"speaker": "A", "view": "全量上线", "reason": "验证收入"},
                {"speaker": "B", "view": "小流量", "reason": "控制风险"},
            ],
            "conclusion": "",
            "alignment_reason": "",
        }],
        "derek_viewpoint": None,
        "key_questions": [{
            "question": "如果本周必须验证收入，最多接受多大故障面？",
            "answer_owner_names": ["A", "B"],
        }],
        "mention_names": ["A", "B"],
        "target": {
            "kind": "group",
            "conversation_id": "cid-1",
            "direct_user_id": "",
            "title": "项目群",
            "candidates": [{
                "conversation_id": "cid-1",
                "title": "项目群",
                "evidence": ["会前后讨论同一上线范围"],
            }],
        },
        "final_message": "会后对齐｜上线评审\n\n目前尚未对齐…",
        "audit_summary": "发现一个未对齐的上线范围取舍。",
        "confidence": 0.86,
    }


def test_send_decision_requires_message_and_target():
    payload = valid_send_decision()
    payload["final_message"] = ""
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)


def test_no_action_rejects_delivery_payload():
    payload = valid_send_decision()
    payload.update(action="no_action", final_message="")
    with pytest.raises(ValidationError):
        MeetingAlignmentDecision.model_validate(payload)
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `.venv/bin/pytest tests/test_meeting_alignment_models.py -q`

Expected: FAIL with `ModuleNotFoundError: app.meeting_alignment_models`.

- [ ] **Step 3: Add strict Pydantic contracts**

Implement `MeetingAlignmentDecision` with `extra="forbid"`, literal action and
state values, and an after-validator enforcing these invariants:

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlignmentView(StrictModel):
    speaker: str
    view: str
    reason: str


class AlignmentTopic(StrictModel):
    title: str
    state: Literal["aligned", "unresolved"]
    views: list[AlignmentView]
    conclusion: str
    alignment_reason: str


class DerekViewpoint(StrictModel):
    expressed_view: str
    meeting_evidence: list[str]
    omitted_layer: str
    plain_explanation: str
    analogy: str
    example: str
    historical_sources: list[str]


class KeyQuestion(StrictModel):
    question: str
    answer_owner_names: list[str]


class TargetCandidate(StrictModel):
    conversation_id: str
    title: str
    evidence: list[str]


class DeliveryTarget(StrictModel):
    kind: Literal["group", "direct"]
    conversation_id: str
    direct_user_id: str
    title: str
    candidates: list[TargetCandidate]


class MeetingAlignmentDecision(StrictModel):
    action: Literal["no_action", "send"]
    trigger_reasons: list[
        Literal["aligned_disagreement", "unresolved_disagreement", "derek_viewpoint"]
    ]
    topics: list[AlignmentTopic]
    derek_viewpoint: DerekViewpoint | None
    key_questions: list[KeyQuestion]
    mention_names: list[str]
    target: DeliveryTarget | None
    final_message: str
    audit_summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action == "no_action":
            if self.target is not None or self.final_message.strip():
                raise ValueError("no_action cannot contain delivery output")
            return self
        if self.target is None or not self.final_message.strip():
            raise ValueError("send requires target and final_message")
        if not self.trigger_reasons:
            raise ValueError("send requires trigger_reasons")
        if self.target.kind == "group":
            if not self.target.conversation_id or not self.target.candidates:
                raise ValueError("group target requires candidates and conversation_id")
            if self.target.candidates[0].conversation_id != self.target.conversation_id:
                raise ValueError("group target must select the first ranked candidate")
        return self
```

Also define `MeetingSource`, `MeetingParticipant`, `AlignmentView`,
`AlignmentTopic`, `DerekViewpoint`, `KeyQuestion`, `TargetCandidate`,
`DeliveryTarget`, `MeetingAlignmentJob`, and `MeetingAlignmentRun`. Queue states
must be exactly `waiting`, `pending`, `processing`, `no_action`,
`ready_to_send`, `sent`, `retry`, and `failed`.

Use these source and persistence shapes so later tasks do not invent new field
names:

```python
class MeetingParticipant(StrictModel):
    name: str
    user_id: str
    open_dingtalk_id: str = ""


class TranscriptLine(StrictModel):
    speaker_name: str
    speaker_user_id: str = ""
    timestamp: str = ""
    text: str


class MeetingSource(StrictModel):
    meeting_id: str
    title: str
    status: Literal["ended"]
    started_at: str
    ended_at: str
    participants: list[MeetingParticipant]
    current_user_id: str
    summary: str
    transcript: list[TranscriptLine]
    source_url: str = ""


class MeetingAlignmentJob(StrictModel):
    id: int
    meeting_id: str
    title: str
    source_json: str
    participants_json: str
    ended_at: str
    eligible_at: str
    status: str
    attempts: int
    locked_at: str | None = None
    available_at: str
    error: str
    decision_json: str
    target_kind: str
    target_id: str
    target_title: str
    mentions_json: str
    final_message: str
    send_result_json: str
    created_at: str
    updated_at: str


class MeetingAlignmentRun(StrictModel):
    id: int
    job_id: int
    codex_session_id: str
    codex_transcript_start_line: int
    codex_transcript_end_line: int
    decision_json: str
    audit_tool_events_json: str
    audit_summary: str
    status: str
    error: str
    created_at: str
```

- [ ] **Step 4: Generate and inspect the JSON schema**

Run:

```bash
.venv/bin/python -c 'import json; from app.meeting_alignment_models import MeetingAlignmentDecision; print(json.dumps(MeetingAlignmentDecision.model_json_schema(), ensure_ascii=False, indent=2))'
```

Use `apply_patch` to add the generated JSON to
`app/schemas/meeting_alignment_decision.schema.json`, add the draft 2020-12
`$schema` field, and confirm every object has `additionalProperties: false`.

- [ ] **Step 5: Run model tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the contracts**

```bash
git add app/meeting_alignment_models.py app/schemas/meeting_alignment_decision.schema.json tests/test_meeting_alignment_models.py
git commit -m "feat: define meeting alignment contracts"
```

### Task 2: Add the dedicated queue and immutable run persistence

**Files:**
- Modify: `app/store.py`
- Create: `tests/test_meeting_alignment_store.py`

- [ ] **Step 1: Write failing queue lifecycle tests**

```python
def test_meeting_job_claim_is_exclusive_and_terminal_states_do_not_requeue(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="上线评审",
        source_json='{"meeting_id":"minutes-1"}',
        participants_json='[{"name":"Derek","user_id":"u-derek"}]',
        ended_at="2026-07-14 02:00:00",
        eligible_at="2026-07-14 02:10:00",
        status="pending",
    )

    claimed = store.claim_meeting_alignment_jobs(limit=1, now="2026-07-14 02:11:00")
    assert [job.id for job in claimed] == [job_id]
    assert store.claim_meeting_alignment_jobs(limit=1, now="2026-07-14 02:11:00") == []

    store.update_meeting_alignment_job(job_id, status="no_action", error="")
    assert store.claim_meeting_alignment_jobs(limit=1, now="2026-07-14 02:12:00") == []


def test_meeting_agent_runs_are_immutable(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="上线评审",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-14 02:00:00",
        eligible_at="2026-07-14 02:10:00",
        status="pending",
    )
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-1",
        decision_json="{}",
        audit_summary="首次发送结果不明确",
        status="retry",
        error="send status pending",
    )
    store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-2",
        decision_json='{"action":"send"}',
        audit_summary="发送已确认",
        status="sent",
        error="",
    )
    assert [run.status for run in store.list_meeting_alignment_runs(job_id)] == [
        "sent", "retry"
    ]
```

- [ ] **Step 2: Run the persistence tests and verify missing methods**

Run: `.venv/bin/pytest tests/test_meeting_alignment_store.py -q`

Expected: FAIL with missing `upsert_meeting_alignment_job`.

- [ ] **Step 3: Add the two tables and indexes**

Add to `AutoReplyStore._initialize()`:

```sql
create table if not exists meeting_alignment_jobs (
    id integer primary key autoincrement,
    meeting_id text not null unique,
    title text not null default '',
    source_json text not null default '{}',
    participants_json text not null default '[]',
    ended_at text not null default '',
    eligible_at text not null default '',
    status text not null default 'waiting',
    attempts integer not null default 0,
    locked_at text,
    available_at text not null default '',
    error text not null default '',
    decision_json text not null default '{}',
    target_kind text not null default '',
    target_id text not null default '',
    target_title text not null default '',
    mentions_json text not null default '[]',
    final_message text not null default '',
    send_result_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
create index if not exists idx_meeting_alignment_jobs_claim
    on meeting_alignment_jobs(status, available_at, eligible_at, id);

create table if not exists meeting_alignment_runs (
    id integer primary key autoincrement,
    job_id integer not null,
    codex_session_id text not null default '',
    codex_transcript_start_line integer not null default 0,
    codex_transcript_end_line integer not null default 0,
    decision_json text not null default '{}',
    audit_tool_events_json text not null default '[]',
    audit_summary text not null default '',
    status text not null,
    error text not null default '',
    created_at text not null default current_timestamp,
    foreign key(job_id) references meeting_alignment_jobs(id)
);
create index if not exists idx_meeting_alignment_runs_job
    on meeting_alignment_runs(job_id, id);
```

- [ ] **Step 4: Implement atomic queue methods**

Add typed methods for upsert, get, claim, update, schedule retry, reset processing
on service startup, record run, and list runs. Claim with one SQLite transaction:

```python
def claim_meeting_alignment_jobs(self, limit: int, now: str) -> list[MeetingAlignmentJob]:
    with self._connect() as db:
        rows = db.execute(
            """
            with candidates as (
                select id from meeting_alignment_jobs
                where status in ('pending', 'retry')
                  and datetime(eligible_at) <= datetime(?)
                  and (available_at='' or datetime(available_at) <= datetime(?))
                order by eligible_at, id
                limit ?
            )
            update meeting_alignment_jobs
            set status='processing', attempts=attempts+1,
                locked_at=current_timestamp, updated_at=current_timestamp
            where id in (select id from candidates)
              and status in ('pending', 'retry')
            returning *
            """,
            (now, now, limit),
        ).fetchall()
        return [MeetingAlignmentJob.model_validate(dict(row)) for row in rows]
```

Ensure `update_meeting_alignment_job` has an explicit column allowlist and never
updates `meeting_id`.

- [ ] **Step 5: Run queue and store regression tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment_store.py tests/test_store.py tests/test_task_store.py -q`

Expected: PASS.

- [ ] **Step 6: Commit queue persistence**

```bash
git add app/store.py tests/test_meeting_alignment_store.py
git commit -m "feat: add meeting alignment queue"
```

### Task 3: Normalize Minutes metadata and read the complete transcript

**Files:**
- Modify: `app/dws_client.py`
- Create: `app/meeting_alignment_source.py`
- Modify: `tests/test_dws_client.py`
- Create: `tests/test_meeting_alignment_source.py`

- [ ] **Step 1: Write failing pagination and normalization tests**

```python
def test_read_complete_transcript_walks_until_next_token_disappears():
    dws = FakeDws(transcript_pages={
        "": {"paragraphs": [{"speakerName": "A", "text": "先全量"}], "nextToken": "p2"},
        "p2": {"paragraphs": [{"speakerName": "B", "text": "先灰度"}]},
    })
    source = read_meeting_source(dws, "minutes-1")
    assert [line.text for line in source.transcript] == ["先全量", "先灰度"]
    assert dws.tokens == ["", "p2"]


def test_normalization_requires_explicit_end_time_and_current_user_participant():
    with pytest.raises(MeetingSourceIncomplete, match="end time"):
        normalize_meeting_source(info_without_end_time, [], current_user_id="u-derek")
```

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/pytest tests/test_meeting_alignment_source.py tests/test_dws_client.py -q`

Expected: FAIL because complete transcript and normalization helpers are absent.

- [ ] **Step 3: Add a complete transcript method to `DwsClient`**

```python
def get_all_minutes_transcription(self, task_uuid: str) -> dict[str, Any]:
    paragraphs: list[dict[str, Any]] = []
    next_token = ""
    seen_tokens: set[str] = set()
    for _ in range(100):
        page = self.get_minutes_transcription(task_uuid, next_token=next_token)
        paragraphs.extend(
            item for item in self.parse_minutes_transcription_paragraphs(page)
            if isinstance(item, dict)
        )
        next_token = self.parse_minutes_next_token(page)
        if not next_token:
            return {"paragraphs": paragraphs}
        if next_token in seen_tokens:
            raise DwsError("minutes transcription pagination repeated next token")
        seen_tokens.add(next_token)
    raise DwsError("minutes transcription pagination exceeded 100 pages")
```

Add a parser test using actual supported DWS response nesting. Do not silently
return a partial transcript on repeated tokens or page-limit exhaustion.

- [ ] **Step 4: Implement source normalization**

`read_meeting_source()` must combine `get_minutes_info`,
`get_minutes_summary`, and `get_all_minutes_transcription`, resolve the
authenticated current user through `get_current_user_id()`, and return a strict
`MeetingSource`. Normalize external field aliases at this boundary only. Raise
typed `MeetingSourceIncomplete` errors for missing participant data, explicit
ended state, end time, or incomplete pagination.

- [ ] **Step 5: Run source tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment_source.py tests/test_dws_client.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Minutes hydration**

```bash
git add app/dws_client.py app/meeting_alignment_source.py tests/test_dws_client.py tests/test_meeting_alignment_source.py
git commit -m "feat: hydrate complete meeting minutes"
```

### Task 4: Implement the independent meeting producer

**Files:**
- Create: `app/meeting_alignment.py`
- Create: `tests/test_meeting_alignment.py`

- [ ] **Step 1: Write failing eligibility tests**

Cover: Derek absent, participant data unavailable, ended less than ten minutes
ago, ended exactly ten minutes ago, stable-ID deduplication, and a previously
terminal job.

```python
def test_producer_queues_at_exactly_ten_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws([ended_meeting(end="2026-07-14T10:00:00+08:00", participants=["u-derek"])])
    queued = produce_meeting_alignment_jobs(
        store, dws, now=datetime.fromisoformat("2026-07-14T10:10:00+08:00"),
        settle_seconds=600,
    )
    assert queued == 1
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1").status == "pending"
```

- [ ] **Step 2: Run the producer tests and verify failure**

Run: `.venv/bin/pytest tests/test_meeting_alignment.py -q`

Expected: FAIL with missing `produce_meeting_alignment_jobs`.

- [ ] **Step 3: Implement deterministic discovery only**

The producer must paginate `list_minutes_page`, use only list/info metadata for
eligibility, compare authenticated current-user ID plus configured principal
aliases, upsert `waiting` after participation is confirmed, and promote to
`pending` at `ended_at + settle_seconds`. It must not read the transcript,
invoke Codex, search groups, or send messages.

- [ ] **Step 4: Run producer and task-scanner tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment.py tests/test_task_scanners.py -q`

Expected: PASS with the existing task scanner unchanged.

- [ ] **Step 5: Commit the producer**

```bash
git add app/meeting_alignment.py tests/test_meeting_alignment.py
git commit -m "feat: produce eligible meeting jobs"
```

### Task 5: Build the Meeting Alignment Agent and semantic eval fixtures

**Files:**
- Create: `app/meeting_alignment_agent.py`
- Create: `tests/test_meeting_alignment_agent.py`
- Create: `tests/test_meeting_alignment_eval.py`
- Create: `tests/fixtures/meeting_alignment_cases.json`

- [ ] **Step 1: Write failing prompt and parser tests**

Assert the prompt contains the full transcript and these behavioral contracts:
explicit agreement only, multiple minimal trade-off questions allowed, the
user-facing title `Derek 的观点输出解读`, history may explain but not invent,
multi-person target must be the first ranked group, and one message per meeting.

```python
def test_prompt_requires_explicit_alignment_and_minimal_question_set(source):
    prompt = build_meeting_alignment_prompt(source, work_profile="重视端到端结果")
    assert "沉默不算对齐" in prompt
    assert "可以提出多个问题" in prompt
    assert "完成对齐所需的最小集合" in prompt
    assert "Derek 的观点输出解读" in prompt
```

- [ ] **Step 2: Run tests and verify missing agent module**

Run: `.venv/bin/pytest tests/test_meeting_alignment_agent.py -q`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement the Codex runner using the existing runner contract**

Mirror `TaskAgentCodexRunner` for process timeout, session extraction, transcript
line bounds, and audit tool events, but use
`meeting_alignment_decision.schema.json` and always start a fresh session:

```python
class MeetingAlignmentCodexRunner:
    def decide(self, prompt: str) -> MeetingAlignmentDecision:
        self.last_transcript_start_line = 0
        raw = self._execute(prompt=prompt, session_id=None)
        self.last_session_id = self._extract_codex_session_id(raw) or ""
        self.last_transcript_end_line = self._session_line_count(self.last_session_id)
        self.last_audit_tool_events = self._extract_events(self.last_session_id, 0, self.last_transcript_end_line)
        return parse_meeting_alignment_decision(raw)
```

Read `data/work-profile/work_profile.md` through the existing work-profile
helper. Instruct the agent to use `memory_recall` only for historical explanation
or project background, never to replace transcript evidence.

The prompt must also require DWS group discovery during a `send` decision:
search explicit meeting links first, then meeting title/core-topic messages,
participant activity, organizer/speaker overlap, temporal proximity, and recent
accessible groups. Require every candidate to contain evidence and require the
selected group to be candidate index zero even when its association is weak.
For one-to-one meetings, require a direct target for the other participant and
forbid group search.

If `DerekViewpoint.historical_sources` is non-empty, validate that the Codex
audit events contain `memory_recall` or that each cited source is the configured
work-profile file. Reject uncited historical examples.

- [ ] **Step 4: Add table-driven semantic fixtures**

The fixture file must contain at least these expected outcomes:

```json
[
  {"id":"wording-only","expected_action":"no_action"},
  {"id":"host-announces-no-confirmation","expected_state":"unresolved"},
  {"id":"all-sides-restate","expected_state":"aligned"},
  {"id":"two-independent-tradeoffs","expected_question_count":2},
  {"id":"derek-view-lost","expected_trigger":"derek_viewpoint"},
  {"id":"history-would-invent-position","forbidden_trigger":"derek_viewpoint"},
  {"id":"aligned-plus-derek-explanation","expected_action":"send"}
]
```

Use a deterministic fake executor in unit tests. Mark any real-model eval as
`@pytest.mark.live` so the default suite stays offline.

Add `tests/test_meeting_alignment_eval.py` as a live table-driven runner over
the same fixture file. It should invoke `MeetingAlignmentCodexRunner`, assert
the expected action/state/question count/trigger for every case, and include the
fixture ID in assertion messages.

- [ ] **Step 5: Run agent tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment_agent.py -q`

Expected: PASS.

Run the real semantic evaluation before live deployment:

`CEO_NOT_SEND_MESSAGE=1 .venv/bin/pytest tests/test_meeting_alignment_eval.py -m live -q`

Expected: PASS for every fixture without sending DingTalk messages.

- [ ] **Step 6: Commit the agent**

```bash
git add app/meeting_alignment_agent.py tests/test_meeting_alignment_agent.py tests/test_meeting_alignment_eval.py tests/fixtures/meeting_alignment_cases.json
git commit -m "feat: add meeting alignment agent"
```

### Task 6: Validate targets, resolve real mentions, and deliver once

**Files:**
- Create: `app/meeting_alignment_delivery.py`
- Create: `tests/test_meeting_alignment_delivery.py`
- Modify: `app/dws_client.py`
- Modify: `tests/test_dws_client.py`

- [ ] **Step 1: Write failing delivery tests**

Cover the top-ranked group rule even at low confidence, no direct fallback for
multi-person meetings, one-to-one direct delivery, unique identity resolution,
ambiguous identity omission, real open-DingTalk mention IDs, and ambiguous send
status verification.

```python
def test_group_delivery_uses_first_candidate_and_real_mentions():
    result = deliver_meeting_alignment(decision, source, dws)
    assert dws.sent[0]["conversation_id"] == "cid-first"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-a", "open-b"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["A", "B"]


def test_multi_person_no_group_never_falls_back_to_direct():
    with pytest.raises(MeetingDeliveryRetry, match="sendable group"):
        deliver_meeting_alignment(no_group_decision, multi_person_source, dws)
    assert dws.direct_sends == []
```

- [ ] **Step 2: Run delivery tests and verify failure**

Run: `.venv/bin/pytest tests/test_meeting_alignment_delivery.py -q`

Expected: FAIL with missing delivery module.

- [ ] **Step 3: Implement deterministic validation and identity resolution**

Require `target.candidates[0]` for groups and verify it through
`conversation-info`. Resolve each mention name with `search_user_profiles`, then
disambiguate using participant user IDs, open DingTalk IDs, department/title,
and recent group senders. Never choose among multiple remaining matches.

Return both resolved and unresolved mention records. Pass resolved identities to
`DwsClient.send_message(... at_open_dingtalk_ids=..., at_open_dingtalk_names=...)`.

- [ ] **Step 4: Verify ambiguous send results before retry**

Add a small DWS helper that extracts `openTaskId` from a send result and calls
the existing send-status command. A confirmed success returns `sent`; a
confirmed failure raises a retryable error; an outcome with no verifiable ID is
recorded as ambiguous and must not immediately send a duplicate.

- [ ] **Step 5: Run delivery and DWS tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment_delivery.py tests/test_dws_client.py -q`

Expected: PASS.

- [ ] **Step 6: Commit delivery**

```bash
git add app/meeting_alignment_delivery.py app/dws_client.py tests/test_meeting_alignment_delivery.py tests/test_dws_client.py
git commit -m "feat: deliver meeting alignment messages"
```

### Task 7: Orchestrate the consumer, retries, idempotency, and startup recovery

**Files:**
- Modify: `app/meeting_alignment.py`
- Modify: `tests/test_meeting_alignment.py`

- [ ] **Step 1: Write failing consumer lifecycle tests**

```python
def test_consumer_records_no_action_run_and_terminal_job(store, runner, dws):
    job = seed_pending_job(store)
    runner.decision = no_action_decision()
    assert consume_meeting_alignment_jobs(store, dws, runner, limit=1) == 1
    assert store.get_meeting_alignment_job(job.id).status == "no_action"
    assert store.list_meeting_alignment_runs(job.id)[0].status == "no_action"


def test_consumer_send_retry_does_not_duplicate_confirmed_delivery(store, runner, dws):
    # First attempt returns an ambiguous task ID; second checks it as success.
    consume_meeting_alignment_jobs(store, dws, runner, limit=1)
    consume_meeting_alignment_jobs(store, dws, runner, limit=1)
    assert len(dws.send_calls) == 1
    assert store.get_meeting_alignment_job_by_meeting_id("minutes-1").status == "sent"
```

- [ ] **Step 2: Verify lifecycle tests fail**

Run: `.venv/bin/pytest tests/test_meeting_alignment.py -q`

Expected: FAIL with missing consumer orchestration.

- [ ] **Step 3: Implement the state machine**

For each claimed job: hydrate full source, invoke the agent, persist an immutable
run, write `no_action` directly or `ready_to_send` before delivery, resolve and
send, then mark `sent`. On a typed retryable error set `retry` and `available_at`;
on schema or invariant failure set `failed`. Always record `errors.kind` as a
stage-specific value such as `meeting_source`, `meeting_agent`,
`meeting_target`, or `meeting_send`.

- [ ] **Step 4: Add startup recovery behavior**

Reset only stale `processing` meeting jobs to `retry`, clear their lock, preserve
attempt count and evidence, and record `meeting_alignment_service_startup_requeue`.

- [ ] **Step 5: Run meeting orchestration tests**

Run: `.venv/bin/pytest tests/test_meeting_alignment.py tests/test_meeting_alignment_store.py -q`

Expected: PASS.

- [ ] **Step 6: Commit orchestration**

```bash
git add app/meeting_alignment.py tests/test_meeting_alignment.py
git commit -m "feat: consume meeting alignment jobs"
```

### Task 8: Start independent meeting loops inside the existing service

**Files:**
- Modify: `app/config.py`
- Modify: `app/cli.py`
- Modify: `.env.example`
- Modify: `launchd/com.ceo-agent-service.main.plist`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_hourly_dry_run_launchd.py`

- [ ] **Step 1: Extend the service topology test first**

Update `test_run_service_starts_web_producer_and_consumer` to expect these two
additional independent threads and calls:

```python
assert ("start", "ceo-agent-service-meeting-producer", True) in calls
assert ("meeting-producer", 60, 600) in calls
assert ("start", "ceo-agent-service-meeting-consumer", True) in calls
assert ("meeting-consumer", 10, 4) in calls
```

Also assert that monkeypatched `run_producer_loop` receives no meeting calls.

- [ ] **Step 2: Run CLI tests and verify the missing components**

Run: `.venv/bin/pytest tests/test_cli.py::test_run_service_starts_web_producer_and_consumer -q`

Expected: FAIL because only four components start.

- [ ] **Step 3: Add settings and loop functions**

Add defaults:

```python
meeting_producer_interval_seconds: PositiveInt = 60
meeting_consumer_poll_interval_seconds: PositiveInt = 10
meeting_settle_seconds: PositiveInt = 600
```

Add `run_meeting_producer_loop()` and `run_meeting_consumer_loop()` that call the
Task 4 and Task 7 functions and sleep only on their own intervals. Add the two
components to `run_service()` and call meeting startup recovery before threads
start. Do not call meeting production from `DingTalkAutoReplyWorker.produce_once`.

- [ ] **Step 4: Wire environment and launchd arguments**

Document and pass:

```text
CEO_MEETING_PRODUCER_INTERVAL_SECONDS=60
CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS=10
CEO_MEETING_SETTLE_SECONDS=600
```

Keep one `com.ceo-agent-service.main` plist. Do not add another plist or crontab.

- [ ] **Step 5: Run CLI and launchd tests**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_hourly_dry_run_launchd.py -q`

Expected: PASS.

- [ ] **Step 6: Commit service integration**

```bash
git add app/config.py app/cli.py .env.example launchd/com.ceo-agent-service.main.plist tests/test_cli.py tests/test_hourly_dry_run_launchd.py
git commit -m "feat: run independent meeting workers"
```

### Task 9: Show meeting runs through the reply-agent History experience

**Files:**
- Create: `app/history.py`
- Modify: `app/store.py`
- Modify: `app/audit_web.py`
- Create: `tests/test_history.py`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing unified History tests**

```python
def test_history_merges_reply_and_meeting_runs_by_time(store):
    seed_reply_attempt(store, created_at="2026-07-14 10:00:00")
    meeting_run_id = seed_meeting_run(store, created_at="2026-07-14 10:01:00")
    items = store.list_history_items(limit=20)
    assert [(item.kind, item.source_id) for item in items[:2]] == [
        ("meeting", meeting_run_id), ("reply", 1)
    ]


def test_meeting_history_uses_reply_card_contract(store):
    run_id = seed_meeting_run(store, status="sent")
    html = render_attempt_list(store)
    assert f'/meeting-attempts/{run_id}' in html
    assert 'class="attempt-item"' in html
    assert "会后对齐" in html
    assert "项目群" in html
```

- [ ] **Step 2: Run History tests and verify missing unified query**

Run: `.venv/bin/pytest tests/test_history.py tests/test_audit_web.py -q`

Expected: FAIL because History only queries `reply_attempts`.

- [ ] **Step 3: Add a shared History feed model and SQL union**

Define a `HistoryItem` with fields consumed by the existing card renderer:

```python
class HistoryItem(BaseModel):
    kind: Literal["reply", "meeting"]
    source_id: int
    source_title: str
    source_actor: str
    input_label: str
    input_text: str
    output_label: str
    output_text: str
    action: str
    status: str
    target_title: str = ""
    codex_session_id: str = ""
    created_at: str
```

Implement `count_history_items()` and `list_history_items()` with a SQLite
`union all` over reply attempts and meeting runs joined to their jobs. Apply
search, status filters, ordering, limit, and offset after the union so pagination
is globally chronological.

- [ ] **Step 4: Reuse the existing card and detail components**

Adapt replies and meetings into `HistoryItem`; render both through the same
`attempt-item`, status-pill, time, search, filter, pagination, and auto-refresh
functions. Add `/meeting-attempts/{run_id}` but build its body from the same
detail sections used by reply attempts. Include source, decision, target ranking,
mention resolution, send result, and the existing `/codex/{session_id}` link.

Do not insert meeting rows into `reply_attempts`, and do not build a separate
meeting History page.

- [ ] **Step 5: Include meeting events in the 24-hour chart and Codex related history**

Map `no_action` to skipped, `sent` to sent, and `retry`/`failed` to failed for
the existing event chart. When a Codex session file is missing, keep the meeting
detail and related-history link visible exactly as reply history does.

- [ ] **Step 6: Run History regression tests**

Run: `.venv/bin/pytest tests/test_history.py tests/test_audit_web.py tests/test_codex_history.py -q`

Expected: PASS.

- [ ] **Step 7: Commit History integration**

```bash
git add app/history.py app/store.py app/audit_web.py tests/test_history.py tests/test_audit_web.py
git commit -m "feat: show meeting agents in history"
```

### Task 10: Document, regression-test, and verify the live service

**Files:**
- Modify: `README.md`
- Modify: `docs/product-logic.md`
- Modify: `tests/e2e/test_local_pipeline.py`

- [ ] **Step 1: Add an end-to-end dry-run test**

Create a fake DWS meeting that ended eleven minutes ago, includes Derek and two
other participants, contains one unresolved disagreement, returns two candidate
groups, and resolves two real mention identities. Assert exactly one send call,
the first candidate group, real mention IDs, a `sent` job, an immutable run, and
a meeting card in History. Run the same producer/consumer cycle again and assert
the send count stays one.

- [ ] **Step 2: Run the focused end-to-end test**

Run: `.venv/bin/pytest tests/e2e/test_local_pipeline.py -k meeting_alignment -q`

Expected: PASS.

- [ ] **Step 3: Update operating documentation**

Document the five internal components, ten-minute settling rule, group/direct
boundary, History visibility, environment variables, queue states, retry
behavior, dry-run command, and live verification queries. State explicitly that
there is one launchd service and no meeting crontab.

- [ ] **Step 4: Run the complete non-live suite**

Run: `.venv/bin/pytest -q`

Expected: PASS with no failures.

- [ ] **Step 5: Commit docs and end-to-end coverage**

```bash
git add README.md docs/product-logic.md tests/e2e/test_local_pipeline.py
git commit -m "docs: document meeting alignment workflow"
```

- [ ] **Step 6: Restart the main service after the runtime commits**

Request user approval for the exact service-control action, then run:

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Expected: command exits successfully.

- [ ] **Step 7: Verify a new process and all queue backlogs**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: state is running and the PID differs from the pre-restart PID.

Run a read-only SQLite query covering `reply_tasks`, `work_summary_inputs`, and
`meeting_alignment_jobs`.

Expected: zero unresolved stale `processing` rows and no unreviewed `failed`
meeting jobs. Open `http://127.0.0.1:8765/` and confirm a Meeting Alignment Agent
run uses the same History presentation and Codex link behavior as a reply-agent
run.
