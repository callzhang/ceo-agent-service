# Follow-Up Reply Task Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route follow-up replies into the task agent so task state is updated by task decisions, not keyword matching in `follow_up.py`.

**Architecture:** The ordinary reply path will enqueue task Work Items for messages that arrive near recently sent follow-ups, including `no_reply` messages. The task agent prompt will include recent follow-up candidates and will output normal task JSON plus a small `follow_up_changes` patch list for existing follow-up rows. `follow_up.py` will keep deterministic send/rate-limit/DingTalk checks and stop scanning reply text for completion or suppression keywords.

**Tech Stack:** Python 3.12, Pydantic models, SQLite store helpers, existing Codex task-agent runner, pytest.

---

## File Structure

- Modify `app/task_models.py`
  - Add `WorkItemTaskSignals` for lightweight routing hints.
  - Add `FollowUpDraftChange` for updating existing follow-up rows by id.
  - Add `task_signals` to `WorkItem`.
  - Add `follow_up_changes` to `TaskAgentDecision`.
- Modify `app/schemas/task_agent_decision.schema.json`
  - Add `follow_up_changes` to the strict output schema.
- Modify `app/store.py`
  - Add a recent follow-up candidate query for task context.
- Modify `app/worker.py`
  - Enqueue Work Items after ordinary reply processing when recent sent follow-ups make the message task-relevant, including no-reply outcomes.
- Modify `app/task_agent.py`
  - Render follow-up candidates into the task-agent prompt.
  - Apply `follow_up_changes` to existing `follow_up_drafts`.
  - Include follow-up changes in work update audit JSON.
- Modify `app/follow_up.py`
  - Remove reply-text keyword reaction scanning and task-state writes.
  - Keep deterministic send flow, stale checks, daily caps, known completion evidence checks, and DingTalk Todo pull-before-send.
- Modify tests:
  - `tests/test_task_agent.py`
  - `tests/test_worker.py`
  - `tests/test_follow_up.py`
  - `tests/test_task_store.py`

---

### Task 1: Extend Task Models and Schema

**Files:**
- Modify: `app/task_models.py`
- Modify: `app/schemas/task_agent_decision.schema.json`
- Test: `tests/test_task_agent.py`

- [ ] **Step 1: Write failing model and schema tests**

Append these tests near the existing schema tests in `tests/test_task_agent.py`:

```python
def test_work_item_accepts_task_routing_signals():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1992",
                "title": "Lily",
                "conversation_id": "cid-lily",
                "conversation_title": "Lily",
                "created_at": "2026-06-28 09:44:05",
            },
            "summary": "Lily反馈海外数据合规P0追错owner。",
            "project_name": "",
            "context": {
                "sender": "Lily",
                "participants": ["Lily"],
                "source_conversation_kind": "direct",
                "source_conversation_title": "Lily",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "progress_claim": False,
                "owner_correction": True,
                "complaint_about_followup": True,
                "signal_reason": "同一会话里有近期已发送follow-up，且用户反馈追错owner。",
            },
        }
    )

    assert item.task_signals.possible_task_update is True
    assert item.task_signals.owner_correction is True
    assert item.task_signals.complaint_about_followup is True
    assert "追错owner" in item.task_signals.signal_reason
```

Add this schema/model test:

```python
def test_task_agent_decision_supports_follow_up_changes():
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "discard_reason": "",
            "project": {
                "id": 372,
                "title": "海外数据合规与中美开发隔离闭环",
                "category": "strategy",
                "tags": [],
                "status": "active",
                "priority": "P0",
                "risk_level": "high",
                "needs_derek_attention": False,
                "owner_user_id": "02412744671048909",
                "owner_name": "Ming Hu(胡明)/运维",
                "related_people": [],
                "goal": "",
                "background": "Lily反馈该P0事项应由胡明和运维负责。",
                "memory_context": _memory_context(),
                "facts": [],
                "current_state": "",
                "blocker": "",
                "next_step": "",
                "next_follow_up_at": "",
                "follow_up_mode": "none",
                "source_conversations": [],
            },
            "todo_changes": [],
            "follow_up_drafts": [],
            "follow_up_changes": [
                {
                    "follow_up_id": 1566,
                    "status": "skipped",
                    "suppressed_reason": "owner_corrected_by_reply",
                    "reaction_status": "",
                    "reaction_summary": "",
                    "evidence_check": {
                        "source": "reply_attempt:1992",
                        "summary": "Lily说明该事项由胡明和运维负责。",
                    },
                    "scheduled_at": "",
                }
            ],
            "update_summary": "停止追Lily并修正owner口径。",
            "merge_reason": "follow-up reply corrected owner",
            "memory_recall_used": True,
            "confidence": 0.86,
            "failure_risk": "继续追错owner会降低执行效率并造成被追问人的焦虑。",
            "failure_risk_score": 0.8,
        }
    )

    assert decision.follow_up_changes[0].follow_up_id == 1566
    assert decision.follow_up_changes[0].status == "skipped"
    assert decision.follow_up_changes[0].suppressed_reason == "owner_corrected_by_reply"
```

Add this schema test:

```python
def test_task_agent_schema_includes_follow_up_changes():
    from app.task_agent import TASK_AGENT_DECISION_SCHEMA_PATH

    schema = json.loads(TASK_AGENT_DECISION_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert "follow_up_changes" in schema["required"]
    assert schema["properties"]["follow_up_changes"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/follow_up_change"},
    }
    change_schema = schema["$defs"]["follow_up_change"]
    assert set(change_schema["required"]) == set(change_schema["properties"])
    assert change_schema["properties"]["follow_up_id"]["type"] == "integer"
    assert change_schema["properties"]["status"]["enum"] == [
        "draft",
        "approved",
        "sent",
        "skipped",
        "failed",
        "cancelled",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_work_item_accepts_task_routing_signals tests/test_task_agent.py::test_task_agent_decision_supports_follow_up_changes tests/test_task_agent.py::test_task_agent_schema_includes_follow_up_changes -q
```

Expected: FAIL because `WorkItem.task_signals`, `TaskAgentDecision.follow_up_changes`, and schema `$defs.follow_up_change` do not exist.

- [ ] **Step 3: Add Pydantic models**

In `app/task_models.py`, add after `WorkItemContext`:

```python
class WorkItemTaskSignals(BaseModel):
    possible_task_update: bool = False
    mentions_follow_up: bool = False
    progress_claim: bool = False
    owner_correction: bool = False
    complaint_about_followup: bool = False
    signal_reason: str = ""
```

Change `WorkItem` to:

```python
class WorkItem(BaseModel):
    source: WorkItemSource
    summary: str
    project_name: str = ""
    context: WorkItemContext
    task_signals: WorkItemTaskSignals = Field(default_factory=WorkItemTaskSignals)
```

Add after `FollowUpDraftDecision`:

```python
class FollowUpDraftChange(BaseModel):
    follow_up_id: int
    status: FollowUpDraftStatus = FollowUpDraftStatus.SKIPPED
    suppressed_reason: str = ""
    reaction_status: str = ""
    reaction_summary: str = ""
    evidence_check: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: str = ""
```

Change `TaskAgentDecision` to include:

```python
    follow_up_changes: list[FollowUpDraftChange] = Field(default_factory=list)
```

- [ ] **Step 4: Update strict JSON schema**

In `app/schemas/task_agent_decision.schema.json`:

Add `"follow_up_changes"` to the root `"required"` array immediately after `"follow_up_drafts"`.

Add this root property immediately after `"follow_up_drafts"`:

```json
"follow_up_changes": {
  "type": "array",
  "items": {
    "$ref": "#/$defs/follow_up_change"
  }
},
```

Add this `$defs` entry after `follow_up_draft`:

```json
"follow_up_change": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "follow_up_id",
    "status",
    "suppressed_reason",
    "reaction_status",
    "reaction_summary",
    "evidence_check",
    "scheduled_at"
  ],
  "properties": {
    "follow_up_id": {
      "type": "integer"
    },
    "status": {
      "type": "string",
      "enum": ["draft", "approved", "sent", "skipped", "failed", "cancelled"]
    },
    "suppressed_reason": {
      "type": "string"
    },
    "reaction_status": {
      "type": "string"
    },
    "reaction_summary": {
      "type": "string"
    },
    "evidence_check": {
      "type": "object",
      "additionalProperties": true
    },
    "scheduled_at": {
      "type": "string"
    }
  }
}
```

If the previous `$defs` entry is not the last entry, keep valid JSON commas.

- [ ] **Step 5: Update existing test payloads**

In `tests/test_task_agent.py`, every `TaskAgentDecision` dict should include:

```python
"follow_up_changes": [],
```

This can be a mechanical update for all task-agent fake decisions. Do not change test meaning.

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/task_models.py app/schemas/task_agent_decision.schema.json tests/test_task_agent.py
git commit -m "feat: extend task decisions for follow-up changes"
```

---

### Task 2: Add Store Query for Recent Follow-Up Candidates

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_task_store.py`

- [ ] **Step 1: Write failing store test**

Add this test to `tests/test_task_store.py`:

```python
def test_list_recent_follow_ups_for_task_context_matches_conversation_or_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="张丽丽恢复海外数据合规项目当前状态与未完成清单",
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        status="open",
        priority="P0",
    )
    matched_by_conversation = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        target_conversation_id="cid-lily",
        target_kind="direct",
        question_text="两份投资人开放文档是否已经按会议口径改完？",
        status="sent",
        sent_at="2026-06-27 02:45:30",
        scheduled_at="2026-06-26T10:00:00+08:00",
    )
    matched_by_owner = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        target_conversation_id="cid-other",
        target_kind="direct",
        question_text="DataPack开放包是否收口？",
        status="draft",
        scheduled_at="2026-06-29 09:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-other",
        owner_name="Other",
        target_conversation_id="cid-other",
        target_kind="direct",
        question_text="无关问题",
        status="sent",
        sent_at="2026-06-27 02:45:30",
    )

    candidates = store.list_recent_follow_ups_for_task_context(
        conversation_id="cid-lily",
        owner_user_id="144339455824043200",
        since="2026-06-26 00:00:00",
        limit=10,
    )

    assert [item.id for item in candidates] == [
        matched_by_owner,
        matched_by_conversation,
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_task_store.py::test_list_recent_follow_ups_for_task_context_matches_conversation_or_owner -q
```

Expected: FAIL because `AutoReplyStore.list_recent_follow_ups_for_task_context` is missing.

- [ ] **Step 3: Add store method**

Add this method near existing follow-up query methods in `app/store.py`:

```python
    def list_recent_follow_ups_for_task_context(
        self,
        *,
        conversation_id: str = "",
        owner_user_id: str = "",
        since: str = "",
        limit: int = 20,
    ) -> list[FollowUpDraft]:
        if limit <= 0:
            return []
        clauses = ["status in ('sent', 'draft', 'approved')"]
        args: list[object] = []
        match_clauses: list[str] = []
        if conversation_id.strip():
            match_clauses.append("target_conversation_id=?")
            args.append(conversation_id.strip())
        if owner_user_id.strip():
            match_clauses.append("owner_user_id=?")
            args.append(owner_user_id.strip())
        if not match_clauses:
            return []
        clauses.append(f"({' or '.join(match_clauses)})")
        if since.strip():
            clauses.append(
                """
                datetime(coalesce(nullif(sent_at, ''), nullif(scheduled_at, ''), created_at))
                    >= datetime(?)
                """
            )
            args.append(since.strip())
        args.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select *
                from follow_up_drafts
                where {' and '.join(clauses)}
                order by datetime(
                    coalesce(nullif(sent_at, ''), nullif(scheduled_at, ''), created_at)
                ) desc,
                id desc
                limit ?
                """,
                args,
            ).fetchall()
            return [FollowUpDraft.model_validate(dict(row)) for row in rows]
```

- [ ] **Step 4: Run focused store tests**

Run:

```bash
.venv/bin/pytest tests/test_task_store.py::test_list_recent_follow_ups_for_task_context_matches_conversation_or_owner -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_task_store.py
git commit -m "feat: query follow-ups for task context"
```

---

### Task 3: Enqueue Follow-Up Reply Work Items From the Reply Path

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing worker tests**

Add this test near `test_process_batch_enqueues_task_work_item_from_reply` in `tests/test_worker.py`:

```python
def test_process_batch_enqueues_task_work_item_for_no_reply_near_sent_follow_up(
    tmp_path: Path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="张丽丽恢复海外数据合规项目当前状态与未完成清单",
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        status="open",
        priority="P0",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        target_conversation_id="cid-1",
        target_kind="direct",
        question_text="海外数据合规 P0 当前状态是什么？",
        status="sent",
        sent_at="2026-06-28 09:00:00",
    )
    trigger = message("这个是胡明和运维在负责。")
    trigger.sender_name = "Lily"
    trigger.sender_user_id = "144339455824043200"
    trigger.create_time = "2026-06-28 09:44:05"
    dws = FakeDws([conversation(single_chat=True, title="Lily")], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="只是说明owner口径，不需要对话回复。",
            audit_summary="Lily说明海外数据合规P0由胡明和运维负责。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store = store

    worker._process_batch(
        conversation(single_chat=True, title="Lily"),
        [trigger],
        [],
        ignore_existing_attempt=True,
    )

    claimed = store.claim_work_summary_inputs(limit=1)
    assert len(claimed) == 1
    payload = json.loads(claimed[0].payload_json)
    assert payload["task_signals"]["possible_task_update"] is True
    assert payload["task_signals"]["mentions_follow_up"] is True
    assert payload["context"]["sender"] == "Lily"
    assert "胡明和运维" in payload["summary"]
```

Add a negative test:

```python
def test_process_batch_does_not_enqueue_no_reply_without_task_context(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("收到")
    trigger.sender_name = "Lily"
    dws = FakeDws([conversation(single_chat=True, title="Lily")], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="礼貌确认不需要回复。",
            audit_summary="无任务状态变化。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker._process_batch(
        conversation(single_chat=True, title="Lily"),
        [trigger],
        [],
        ignore_existing_attempt=True,
    )

    assert worker.store.claim_work_summary_inputs(limit=1) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_worker.py::test_process_batch_enqueues_task_work_item_for_no_reply_near_sent_follow_up tests/test_worker.py::test_process_batch_does_not_enqueue_no_reply_without_task_context -q
```

Expected: first test FAILS because no-reply follow-up replies are not enqueued.

- [ ] **Step 3: Add routing helper in worker**

In `app/worker.py`, update `_enqueue_task_work_item` so it can enqueue for:

- `send_reply`
- `ask_clarifying_question`
- `no_reply` only when recent follow-up context exists.

Add helper methods near `_enqueue_task_work_item`:

```python
    def _task_work_item_follow_up_signals(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
    ) -> tuple[dict[str, object], list[object]]:
        since = trigger.create_time or ""
        candidates = self.store.list_recent_follow_ups_for_task_context(
            conversation_id=conversation.open_conversation_id,
            owner_user_id=getattr(trigger, "sender_user_id", "") or "",
            since="",
            limit=10,
        )
        if not candidates:
            return {}, []
        return (
            {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "progress_claim": False,
                "owner_correction": False,
                "complaint_about_followup": False,
                "signal_reason": (
                    "recent sent or draft follow-up exists for this conversation "
                    "or sender; task agent must decide whether the message updates it"
                ),
            },
            candidates,
        )
```

Then change the action guard in `_enqueue_task_work_item` to:

```python
        task_signals, follow_up_candidates = self._task_work_item_follow_up_signals(
            conversation,
            trigger,
        )
        allowed_by_reply_action = action in {
            CodexAction.SEND_REPLY.value,
            CodexAction.ASK_CLARIFYING_QUESTION.value,
        }
        allowed_by_follow_up_context = (
            action == CodexAction.NO_REPLY.value and bool(follow_up_candidates)
        )
        if not allowed_by_reply_action and not allowed_by_follow_up_context:
            return
```

When building the Work Item payload, include:

```python
                "task_signals": task_signals,
```

The helper deliberately does not classify completion, owner correctness, or task id.

- [ ] **Step 4: Include sender user id in context if available**

Add sender user id to `context` without changing existing required fields:

```python
                    "sender_user_id": getattr(trigger, "sender_user_id", "") or "",
```

Because `WorkItemContext` currently ignores unknown extras, Task 1 or this task should add a typed field:

```python
class WorkItemContext(BaseModel):
    sender: str = ""
    sender_user_id: str = ""
    participants: list[str] = Field(default_factory=list)
    source_conversation_kind: WorkItemSourceKind
    source_conversation_title: str = ""
```

If this field was not added in Task 1, add it here and update any expected payload assertions.

- [ ] **Step 5: Run worker tests**

Run:

```bash
.venv/bin/pytest tests/test_worker.py::test_process_batch_enqueues_task_work_item_from_reply tests/test_worker.py::test_process_batch_enqueues_task_work_item_for_no_reply_near_sent_follow_up tests/test_worker.py::test_process_batch_does_not_enqueue_no_reply_without_task_context -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/worker.py app/task_models.py tests/test_worker.py
git commit -m "feat: enqueue follow-up replies for task agent"
```

---

### Task 4: Render Follow-Up Candidates in Task Agent Prompt

**Files:**
- Modify: `app/task_agent.py`
- Test: `tests/test_task_agent.py`

- [ ] **Step 1: Write failing prompt test**

Add this test to `tests/test_task_agent.py`:

```python
def test_process_work_item_includes_recent_follow_up_candidates_in_prompt(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="张丽丽恢复海外数据合规项目当前状态与未完成清单",
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        status="open",
        priority="P0",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        target_conversation_id="cid-lily",
        target_kind="direct",
        question_text="海外数据合规 P0 当前状态是什么？",
        status="sent",
        sent_at="2026-06-28 09:00:00",
    )
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1992",
                "title": "Lily",
                "conversation_id": "cid-lily",
                "conversation_title": "Lily",
                "created_at": "2026-06-28 09:44:05",
            },
            "summary": "Lily反馈海外数据合规P0追错owner，这个是胡明和运维负责。",
            "project_name": "",
            "context": {
                "sender": "Lily",
                "sender_user_id": "144339455824043200",
                "participants": ["Lily"],
                "source_conversation_kind": "direct",
                "source_conversation_title": "Lily",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "signal_reason": "recent follow-up candidate exists",
            },
        }
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "discard",
            "discard_reason": "prompt inspection only",
            "project": None,
            "todo_changes": [],
            "follow_up_drafts": [],
            "follow_up_changes": [],
            "update_summary": "不更新。",
            "merge_reason": "",
            "memory_recall_used": False,
            "confidence": 0.5,
            "failure_risk": "测试prompt。",
            "failure_risk_score": 0.1,
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    prompt = codex.prompts[0]
    assert "近期 follow-up 候选" in prompt
    assert f'"id": {follow_up_id}' in prompt
    assert "海外数据合规 P0 当前状态是什么？" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_process_work_item_includes_recent_follow_up_candidates_in_prompt -q
```

Expected: FAIL because the prompt only contains project candidates.

- [ ] **Step 3: Add follow-up context rendering**

In `app/task_agent.py`, add:

```python
def render_follow_up_candidate_prompt(candidates: list[object]) -> str:
    payload = []
    for draft in candidates:
        payload.append(
            {
                "id": draft.id,
                "project_id": draft.project_id,
                "todo_id": draft.todo_id,
                "owner_user_id": draft.owner_user_id,
                "owner_name": draft.owner_name,
                "target_conversation_id": draft.target_conversation_id,
                "target_kind": draft.target_kind,
                "question_text": draft.question_text,
                "status": getattr(draft.status, "value", draft.status),
                "scheduled_at": draft.scheduled_at,
                "sent_at": draft.sent_at,
                "reaction_status": draft.reaction_status,
                "reaction_summary": draft.reaction_summary,
                "suppressed_reason": draft.suppressed_reason,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

Add:

```python
def build_candidate_context_prompt(
    *,
    project_candidates: str,
    follow_up_candidates: str,
) -> str:
    return (
        "候选项目:\n"
        f"{project_candidates}\n\n"
        "近期 follow-up 候选:\n"
        f"{follow_up_candidates}"
    )
```

In `process_work_item`, after `candidates = retrieve_project_candidates(...)`, add:

```python
        follow_up_candidates = store.list_recent_follow_ups_for_task_context(
            conversation_id=work_item.source.conversation_id,
            owner_user_id=work_item.context.sender_user_id,
            since="",
            limit=10,
        )
        candidate_prompt = build_candidate_context_prompt(
            project_candidates=render_candidate_prompt(candidates),
            follow_up_candidates=render_follow_up_candidate_prompt(
                follow_up_candidates
            ),
        )
```

Pass `candidate_prompt` to `runner.decide` instead of `render_candidate_prompt(candidates)`.

- [ ] **Step 4: Update task agent instructions**

In `build_task_agent_prompt`, add this bullet near the existing BM25 candidate rules:

```text
- 近期 follow-up 候选只是上下文线索。你必须自己判断当前 Work Item 是否真的回应了某条 follow-up；不能因为候选存在就关闭 TODO 或 suppress follow-up。
- 如果 Work Item 明确说明追错 owner、重复追问或不应继续跟进，可以通过 follow_up_changes 更新已有 follow_up_draft；不要生成新的 follow_up_draft 来继续追同一个错误 owner。
- 只有当前消息和候选上下文共同明确证明 TODO 完成时，才把 todo_changes 写成 close 并提供 completion_evidence。
```

Also update output requirements:

```text
- follow_up_changes 用于更新已有 follow_up_drafts；必须引用 follow_up_id，且只能在当前 Work Item 明确支持时使用。
```

- [ ] **Step 5: Run task-agent prompt test**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_process_work_item_includes_recent_follow_up_candidates_in_prompt -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "feat: show follow-up candidates to task agent"
```

---

### Task 5: Apply Follow-Up Changes From Task Decisions

**Files:**
- Modify: `app/task_agent.py`
- Test: `tests/test_task_agent.py`

- [ ] **Step 1: Write failing apply-decision test**

Add this test to `tests/test_task_agent.py`:

```python
def test_apply_decision_suppresses_existing_follow_up_without_closing_todo(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        priority="P0",
        risk_level="high",
        owner_name="张丽丽(Lily)",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="张丽丽恢复海外数据合规项目当前状态与未完成清单",
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        status="open",
        priority="P0",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="144339455824043200",
        owner_name="张丽丽(Lily)",
        target_conversation_id="cid-lily",
        target_kind="direct",
        question_text="海外数据合规 P0 当前状态是什么？",
        status="sent",
        sent_at="2026-06-27 02:45:30",
    )
    item = _work_item(project_name="")
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "discard_reason": "",
            "project": {
                "id": project_id,
                "title": "海外数据合规与中美开发隔离闭环",
                "category": "strategy",
                "tags": [],
                "status": "active",
                "priority": "P0",
                "risk_level": "high",
                "needs_derek_attention": False,
                "owner_user_id": "02412744671048909",
                "owner_name": "Ming Hu(胡明)/运维",
                "related_people": [],
                "goal": "",
                "background": "Lily反馈该P0事项由胡明和运维负责，不能继续追Lily。",
                "memory_context": _memory_context(),
                "facts": [
                    {
                        "description": "Lily反馈海外数据合规P0 owner应为胡明和运维。",
                        "source": "reply_attempt:1992",
                        "created": "2026-06-28 09:44:05",
                        "updated": "2026-06-28 09:44:05",
                    }
                ],
                "current_state": "",
                "blocker": "",
                "next_step": "后续如需确认进展，应问胡明或运维。",
                "next_follow_up_at": "",
                "follow_up_mode": "none",
                "source_conversations": [],
            },
            "todo_changes": [
                {
                    "action": "update",
                    "todo_id": todo_id,
                    "todo_ref": "",
                    "title": "确认海外数据合规 P0 当前状态与真实 owner 分工",
                    "owner_user_id": "02412744671048909",
                    "owner_name": "Ming Hu(胡明)",
                    "status": "open",
                    "priority": "P0",
                    "deadline_at": "2026-06-28T23:00:00+08:00",
                    "next_follow_up_at": "",
                    "follow_up_question": "",
                    "completion_evidence": None,
                    "blocker": "",
                }
            ],
            "follow_up_drafts": [],
            "follow_up_changes": [
                {
                    "follow_up_id": follow_up_id,
                    "status": "skipped",
                    "suppressed_reason": "owner_corrected_by_reply",
                    "reaction_status": "",
                    "reaction_summary": "",
                    "evidence_check": {
                        "source": "reply_attempt:1992",
                        "summary": "Lily说明该事项由胡明和运维负责。",
                    },
                    "scheduled_at": "",
                }
            ],
            "update_summary": "停止追Lily并修正海外数据合规owner。",
            "merge_reason": "follow-up reply corrected owner",
            "memory_recall_used": True,
            "confidence": 0.86,
            "failure_risk": "继续追错owner会影响执行效率和用户体验。",
            "failure_risk_score": 0.8,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        memory_recall_attempted=True,
    )

    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "open"
    assert todo.owner_name == "Ming Hu(胡明)"
    assert todo.completion_evidence_json == "{}"
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert skipped.id == follow_up_id
    assert skipped.suppressed_reason == "owner_corrected_by_reply"
    assert "reply_attempt:1992" in skipped.evidence_check_json
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_apply_decision_suppresses_existing_follow_up_without_closing_todo -q
```

Expected: FAIL because `apply_task_agent_decision` ignores `follow_up_changes`.

- [ ] **Step 3: Apply follow-up changes**

In `app/task_agent.py`, add:

```python
def _apply_follow_up_change(store: AutoReplyStore, change) -> None:
    values: dict[str, object] = {
        "status": _enum_value(change.status),
        "suppressed_reason": change.suppressed_reason,
        "reaction_status": change.reaction_status,
        "reaction_summary": change.reaction_summary,
        "evidence_check_json": _json_dumps(change.evidence_check),
    }
    if change.scheduled_at.strip():
        values["scheduled_at"] = change.scheduled_at
    store.update_follow_up_draft(change.follow_up_id, **values)
```

In `apply_task_agent_decision`, after creating follow-up drafts, add:

```python
    for change in decision.follow_up_changes:
        _apply_follow_up_change(store, change)
```

In the `changes_json` payload, add:

```python
                "follow_up_changes": [
                    change.model_dump(mode="json")
                    for change in decision.follow_up_changes
                ],
```

- [ ] **Step 4: Validate follow-up ids**

In `_validate_task_agent_decision`, add:

```python
    for change in decision.follow_up_changes:
        if change.follow_up_id <= 0:
            raise ValueError("follow_up_change.follow_up_id is required")
```

- [ ] **Step 5: Run focused test**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_apply_decision_suppresses_existing_follow_up_without_closing_todo -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "feat: apply task-agent follow-up changes"
```

---

### Task 6: Remove Keyword-Based Follow-Up Reply Processing

**Files:**
- Modify: `app/follow_up.py`
- Test: `tests/test_follow_up.py`

- [ ] **Step 1: Replace old completion-reaction tests**

In `tests/test_follow_up.py`, remove or rewrite tests that assert keyword-based reply handling:

- `test_due_follow_up_skips_when_recent_reply_says_completed`
- `test_completion_reaction_pushes_dingtalk_todo_done`
- `test_due_follow_up_skips_when_recent_reply_asks_for_source`

Add this regression test:

```python
def test_due_follow_up_does_not_close_todo_from_reply_keywords(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 09:00:00",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="客户交付群",
        trigger_message_id="msg-complete",
        trigger_sender="Alex",
        trigger_text="完成了，这块已经结束了。",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-27 09:30:00',
                updated_at='2026-06-27 09:30:00'
            where id=?
            """,
            (attempt_id,),
        )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-27 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "open"
    assert todo.completion_evidence_json == "{}"
```

Keep existing tests that verify:

- skips when `todo.status` is already done
- skips when `completion_evidence_json` exists
- skips when linked DingTalk Todo is done
- sends when linked DingTalk Todo is not done
- daily caps
- stale follow-ups
- DWS auth retry handling

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_follow_up.py::test_due_follow_up_does_not_close_todo_from_reply_keywords -q
```

Expected: FAIL because current keyword code closes the TODO and skips sending.

- [ ] **Step 3: Remove reply keyword scanning from follow_up.py**

In `app/follow_up.py`, remove these constants and helpers:

```python
COMPLETION_REACTION_PHRASES
REDIRECT_REACTION_PHRASES
SOURCE_REQUEST_REACTION_PHRASES
NEGATIVE_REACTION_PHRASES
_reaction_status_for_text
_reaction_evidence_for_draft
_refresh_recent_sent_reactions
_recent_reaction_should_suppress
```

In `_completion_supported_by_current_evidence`, remove the block that calls `_reaction_evidence_for_draft` and closes the TODO from `status == "completed"`.

In `process_due_follow_ups`, remove:

```python
    _refresh_recent_sent_reactions(store, dws, now=now)
```

Remove the blocks that call `_reaction_evidence_for_draft` and `_recent_reaction_should_suppress`.

When writing `evidence_check_json` after a send, keep deterministic fields:

```python
            evidence_check_json=json.dumps(
                {
                    "checked_at": now,
                    "completion_supported": False,
                    "sensitive": sensitive,
                },
                ensure_ascii=False,
            ),
```

- [ ] **Step 4: Run follow-up tests**

Run:

```bash
.venv/bin/pytest tests/test_follow_up.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/follow_up.py tests/test_follow_up.py
git commit -m "refactor: remove keyword follow-up completion handling"
```

---

### Task 7: Add End-to-End Task-Agent Reply Cases

**Files:**
- Modify: `tests/test_task_agent.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Add Lily acceptance test**

Add a task-agent integration test that creates:

- project `海外数据合规与中美开发隔离闭环`
- open Lily-owned TODO
- sent Lily follow-up
- Work Item from `reply_attempt:1992`
- FakeCodex decision that updates owner to Hu Ming, suppresses the old follow-up, and does not close the TODO

Use the expected decision payload from Task 5. Assert:

```python
assert store.get_work_project(project_id).owner_name == "Ming Hu(胡明)/运维"
assert store.get_work_todo(todo_id).status == "open"
assert store.get_work_todo(todo_id).owner_name == "Ming Hu(胡明)"
assert store.list_follow_up_drafts(statuses=("skipped",))[0].suppressed_reason == "owner_corrected_by_reply"
```

- [ ] **Step 2: Add clear completion test**

Add a task-agent test where FakeCodex outputs:

```python
"todo_changes": [
    {
        "action": "close",
        "todo_id": todo_id,
        "todo_ref": "",
        "title": "",
        "owner_user_id": "",
        "owner_name": "",
        "status": "done",
        "priority": "none",
        "deadline_at": "",
        "next_follow_up_at": "",
        "follow_up_question": "",
        "completion_evidence": {
            "description": "Owner replied that the specific ETA document was delivered.",
            "source": "reply_attempt:2001",
            "completed_at": "2026-06-29 10:00:00",
        },
        "blocker": "",
    }
]
```

Assert:

```python
todo = store.get_work_todo(todo_id)
assert todo.status == "done"
assert "reply_attempt:2001" in todo.completion_evidence_json
```

- [ ] **Step 3: Add ambiguous no-change test**

Add a task-agent test where FakeCodex returns:

```python
{
    "action": "discard",
    "discard_reason": "回复说已处理，但无法确定对应项目或TODO。",
    "project": None,
    "todo_changes": [],
    "follow_up_drafts": [],
    "follow_up_changes": [],
    "update_summary": "不更新task。",
    "merge_reason": "",
    "memory_recall_used": False,
    "confidence": 0.4,
    "failure_risk": "错误关闭TODO会掩盖真实风险。",
    "failure_risk_score": 0.5,
}
```

Assert no TODO or follow-up status changed.

- [ ] **Step 4: Run task-agent and worker targeted tests**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py tests/test_worker.py::test_process_batch_enqueues_task_work_item_for_no_reply_near_sent_follow_up -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_task_agent.py tests/test_worker.py
git commit -m "test: cover task-agent follow-up reply handling"
```

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Test: focused pytest and compileall

- [ ] **Step 1: Update README task summary section**

In `README.md`, under "启用 task 总结", add:

```markdown
Follow-up 回复不会由 `follow_up.py` 按关键词直接关闭 TODO。普通回复链路会把疑似 task/follow-up 相关消息写成 Work Item，task agent 再结合最近 sent follow-up、BM25 项目/TODO候选、DWS 上下文和 memory_recall 判断是否更新 task JSON。只有 task agent 写出 `todo.status=done` 且包含 completion evidence 时，才会关闭内部 TODO 并同步钉钉 Todo done。
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_task_store.py tests/test_task_agent.py tests/test_worker.py tests/test_follow_up.py tests/test_cli.py tests/test_todo_sync.py -q
```

Expected: PASS.

- [ ] **Step 3: Run compileall**

Run:

```bash
python -m compileall app/task_models.py app/store.py app/worker.py app/task_agent.py app/follow_up.py app/todo_sync.py app/cli.py
```

Expected: all files compile without errors.

- [ ] **Step 4: Check git diff**

Run:

```bash
git diff --check
git status --short
```

Expected:

- `git diff --check` prints nothing.
- `git status --short` shows only intended modified files before commit.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document task-agent follow-up replies"
```

- [ ] **Step 6: Restart service after runtime changes**

Because this repo runs by launchd and Python changes are not hot-reloaded, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main
```

Expected:

- `launchctl print` shows `state = running`.
- The `pid` value is present and changed from the pre-restart process when available.

- [ ] **Step 7: Runtime health and backlog checks**

Run:

```bash
curl -sS http://127.0.0.1:8765/
sqlite3 data/auto-reply.sqlite3 "select 'reply_tasks' as table_name, status, count(*) from reply_tasks where status in ('failed','processing') group by status union all select 'work_summary_inputs', status, count(*) from work_summary_inputs where status in ('failed','processing') group by status union all select 'follow_up_drafts', status, count(*) from follow_up_drafts where status in ('failed','processing') group by status;"
```

Expected:

- Curl returns the audit web HTML.
- The SQLite backlog query prints no rows. If rows exist, inspect whether they are pre-existing or caused by this change before reporting completion.

---

## Self-Review

Spec coverage:

- Reply agent only creates Work Items: Task 3.
- Task agent owns association and task writes: Tasks 4, 5, and 7.
- No keyword completion logic in `follow_up.py`: Task 6.
- Lily acceptance case: Tasks 5 and 7.
- DingTalk Todo only syncs after task decision closes TODO with evidence: Task 5 preserves existing close path; Task 6 removes keyword close path; Task 7 verifies close evidence path.
- Documentation and runtime verification: Task 8.

Placeholder scan:

- No placeholder red flags remain in this plan.
- Every task has exact files, tests, commands, and expected results.

Type consistency:

- `WorkItem.task_signals` is introduced before worker code writes it.
- `TaskAgentDecision.follow_up_changes` is introduced before task-agent code reads it.
- `FollowUpDraftChange.follow_up_id` matches `follow_up_drafts.id`.
- `evidence_check` maps to existing `follow_up_drafts.evidence_check_json`.
