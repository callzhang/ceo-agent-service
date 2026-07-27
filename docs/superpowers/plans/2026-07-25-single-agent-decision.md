# Single Agent Decision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `UniversalPlanner` and `UniversalPlan` with one Agent-owned final decision, while retaining only trusted-target binding, external writes, idempotency, and receipts in the service.

**Architecture:** The Agent receives the complete `UniversalTaskContext` and returns exactly one `AgentDecision`; there is no action array, planner dependency declaration, confidence score, plan validator, or service-side action composition. The worker calls the Agent directly, persists one decision per task generation, and dispatches that decision once. The service may resolve trusted material bodies and must bind immutable external targets, but it must not replace, append, reorder, or reinterpret the Agent's decision.

**Tech Stack:** Python 3, Pydantic 2, dataclasses, SQLite, pytest, Codex CLI, DWS client, launchd.

---

## Scope Check

This is one subsystem: the main queued DingTalk/OA/mail/calendar/OKR decision path currently routed through `UniversalPlanner` and `UniversalConsumerOrchestrator`.

The change applies to every current universal action, not only OA. It intentionally does not rewrite legacy standalone CLI commands that still use `AgentEnvelope`; those paths are outside the main queued-worker call path and can be removed in a separate plan after their callers are inventoried.

The permanent boundary is:

- Agent-owned: evidence gathering, business judgment, wording, and selection of one final action.
- Service-owned: DWS login preflight, immutable target IDs, permission checks, exactly-once execution, receipts, retries after definite failures, and surfacing unknown outcomes.
- Removed: `UniversalPlanner`, `UniversalPlan`, action arrays, `dependencies`, `confidence`, `memory_write` as a planned side effect, `blocked` as an Agent result, target normalization, action-conflict rules, and plan-level replay.

Low-level steps required to complete one user-visible action remain inside that executor. For example, creating a document, granting access, and sending its link is one `document_reply` decision; those receipt-bearing steps are not exposed as multiple Agent actions.

## File Structure

- Create: `app/agent_decision.py`
  - Own the single-decision schema, canonical JSON, deterministic execution identity, and execution state.
- Create: `app/agent_decision_runner.py`
  - Build the Agent prompt, invoke Codex, parse one decision, repair malformed JSON once, and expose audit tool events separately from the decision.
- Modify: `app/universal_context.py`
  - Keep the trusted task/evidence context, but remove `required_dependencies` and its rendered/canonical representation.
- Modify: `app/store.py`
  - Replace the two-level plan/action persistence API with one `agent_decision_executions` row per reply-task generation.
  - Add `agent_decision_execution_id` to reply attempts.
  - Stop creating or reading the old universal plan/action tables on fresh databases.
- Modify: `app/worker.py`
  - Call the decision runner directly.
  - Preflight DWS before Agent execution.
  - Dispatch one decision without an orchestrator or generic validator.
  - Derive every external target from trusted context, never from Agent-supplied IDs.
- Modify: `app/org_cache.py`
  - Forward the read-only document/sheet methods needed to hydrate trusted material bodies.
- Modify: `app/dws_client.py`
  - Expose the existing sheet read command through a public read-only method.
- Modify: `app/history.py`, `app/audit_web.py`
  - Remove plan-level dependency/action-array observability; rely on the existing attempt action, status, OA/calendar/mail metadata, and receipt fields.
- Modify: `docs/reply-worker-reliability.md`
  - Document the Agent/service boundary and the single-decision retry contract.
- Delete after replacements pass:
  - `app/universal_plan.py`
  - `app/universal_planner.py`
  - `app/universal_consumer.py`
  - `app/universal_validator.py`
  - `app/universal_executor.py`
- Replace planner/orchestrator tests with focused decision tests:
  - Create `tests/test_agent_decision.py`
  - Create `tests/test_agent_decision_runner.py`
  - Create `tests/test_agent_decision_worker.py`
  - Modify `tests/test_store.py`, `tests/test_universal_context.py`, `tests/test_universal_context_enrichment.py`, `tests/test_universal_worker.py`, `tests/test_universal_worker_wiring.py`, `tests/test_universal_okr.py`, `tests/test_universal_parity.py`, and `tests/e2e/test_local_pipeline.py`.

## Task 1: Define One Agent Decision

**Files:**
- Create: `app/agent_decision.py`
- Create: `tests/test_agent_decision.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_agent_decision.py` with these contract tests:

```python
import pytest
from pydantic import ValidationError

from app.agent_decision import AgentAction, AgentDecision


def test_agent_decision_is_one_action_without_planner_metadata() -> None:
    decision = AgentDecision(
        action="oa_approval",
        reason="材料不足，需要申请人补充。",
        content="请补充完整报价依据和风险对策后再提交。",
        parameters={"operation": "comment"},
    )

    assert decision.action is AgentAction.OA_APPROVAL
    assert set(decision.model_dump()) == {
        "action",
        "reason",
        "content",
        "sensitivity_kind",
        "personnel_subject_user_id",
        "candidate_context_known",
        "candidate_department_ids",
        "parameters",
    }
    assert "actions" not in decision.model_dump()
    assert "dependencies" not in decision.model_dump()
    assert "confidence" not in decision.model_dump()
    assert "target" not in decision.model_dump()


@pytest.mark.parametrize("action", ["blocked", "stop_with_error", "memory_write"])
def test_agent_decision_rejects_service_control_actions(action: str) -> None:
    with pytest.raises(ValidationError):
        AgentDecision(action=action, reason="not a final user action")


def test_oa_comment_requires_content() -> None:
    with pytest.raises(ValidationError, match="content"):
        AgentDecision(
            action="oa_approval",
            reason="request more evidence",
            parameters={"operation": "comment"},
        )


def test_agent_decision_rejects_agent_supplied_external_target() -> None:
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            {
                "action": "calendar_response",
                "reason": "accept",
                "content": "",
                "parameters": {"response_status": "accepted"},
                "target": {"event_id": "agent-chosen-event"},
            }
        )
```

- [ ] **Step 2: Run the tests and verify the module is absent**

Run:

```bash
pytest -q tests/test_agent_decision.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.agent_decision'`.

- [ ] **Step 3: Implement the single-decision model**

Create `app/agent_decision.py` with this public contract:

```python
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dingtalk_models import SensitivityKind
from app.universal_context import UniversalTaskContext, universal_context_sha256


class AgentAction(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    OA_APPROVAL = "oa_approval"
    MAIL_REPLY = "mail_reply"
    CALENDAR_RESPONSE = "calendar_response"
    DOCUMENT_REPLY = "document_reply"
    MESSAGE_REACTION = "message_reaction"
    QUEUE_OKR_REVIEW = "queue_okr_review"
    NO_REPLY = "no_reply"
    HANDOFF_TO_HUMAN = "handoff_to_human"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AgentAction
    reason: str
    content: str = ""
    sensitivity_kind: SensitivityKind = SensitivityKind.GENERAL
    personnel_subject_user_id: str | None = None
    candidate_context_known: bool = False
    candidate_department_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def reason_must_be_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_action_content(self) -> "AgentDecision":
        if self.action in {
            AgentAction.SEND_REPLY,
            AgentAction.ASK_CLARIFYING_QUESTION,
            AgentAction.MAIL_REPLY,
            AgentAction.DOCUMENT_REPLY,
        } and not self.content.strip():
            raise ValueError(f"{self.action.value} requires content")
        if self.action is AgentAction.OA_APPROVAL:
            operation = self.parameters.get("operation")
            if operation not in {"同意", "拒绝", "退回", "comment"}:
                raise ValueError("oa_approval requires a supported operation")
            if not self.content.strip():
                raise ValueError("oa_approval requires content")
            if operation == "退回":
                if not str(self.parameters.get("target_activity_id") or "").strip():
                    raise ValueError("oa_approval return requires target_activity_id")
                if self.parameters.get("revert_action") not in {
                    "REVERT_FOR_APPROVAL",
                    "REVERT_FOR_RESUBMIT",
                }:
                    raise ValueError("oa_approval return requires revert_action")
        if self.action is AgentAction.CALENDAR_RESPONSE and self.parameters.get(
            "response_status"
        ) not in {"accepted", "tentative", "declined"}:
            raise ValueError("calendar_response requires response_status")
        if self.action is AgentAction.MESSAGE_REACTION:
            reaction_type = self.parameters.get("reaction_type", "emoji")
            field = "emoji" if reaction_type == "emoji" else "text"
            if reaction_type not in {"emoji", "text_emotion"} or not str(
                self.parameters.get(field) or ""
            ).strip():
                raise ValueError("message_reaction requires reaction content")
        if self.action is AgentAction.QUEUE_OKR_REVIEW and self.parameters:
            raise ValueError("queue_okr_review does not accept parameters")
        return self


class AgentDecisionExecutionState(StrEnum):
    NOT_STARTED = "not_started"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentDecisionExecution:
    execution_id: str
    context: UniversalTaskContext
    decision: AgentDecision
    tool_events: tuple[dict[str, Any], ...] = ()


def canonical_agent_decision_json(decision: AgentDecision) -> str:
    return json.dumps(
        decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_agent_decision_execution(
    context: UniversalTaskContext,
    decision: AgentDecision,
    tool_events: tuple[dict[str, Any], ...] = (),
) -> AgentDecisionExecution:
    identity = json.dumps(
        [context.task_id, context.execution_generation],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return AgentDecisionExecution(
        execution_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        context=context,
        decision=decision.model_copy(deep=True),
        tool_events=tuple(dict(event) for event in tool_events),
    )
```

- [ ] **Step 4: Run the schema tests**

Run `pytest -q tests/test_agent_decision.py`.

Expected: all tests pass.

- [ ] **Step 5: Commit the isolated contract**

```bash
git add app/agent_decision.py tests/test_agent_decision.py
git commit -m "refactor: define a single agent decision"
```

## Task 2: Replace UniversalPlanner With a Decision Runner

**Files:**
- Create: `app/agent_decision_runner.py`
- Create: `tests/test_agent_decision_runner.py`

- [ ] **Step 1: Write failing runner contract tests**

Create tests that assert a direct decision object is parsed and the removed plan controls never appear:

```python
import json
from pathlib import Path

from app.agent_decision import AgentAction
from app.agent_decision_runner import AgentDecisionRunner, parse_agent_decision_json
from app.universal_context import UniversalTaskContext


def _context(trigger_text: str) -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=42,
        conversation_id="cid-1",
        conversation_title="审批",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_sender="申请人",
        trigger_text=trigger_text,
        context_messages=(),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
    )


def test_runner_prompt_requests_one_final_decision() -> None:
    prompt = AgentDecisionRunner(workspace=Path("/tmp/decision-runner")).build_prompt(
        _context("请看完项目材料后决定是否同意审批。")
    )

    assert "exactly one final decision" in prompt
    assert "The service will bind trusted target IDs" in prompt
    assert "UniversalPlan" not in prompt
    assert '"actions"' not in prompt
    assert '"dependencies"' not in prompt
    assert '"confidence"' not in prompt
    assert '"blocked"' not in prompt


def test_parse_agent_decision_accepts_latest_codex_message() -> None:
    payload = {
        "action": "send_reply",
        "reason": "answer the question",
        "content": "可以，今天完成。",
        "parameters": {},
    }
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps({"message": json.dumps(payload, ensure_ascii=False)}),
        ]
    )

    assert parse_agent_decision_json(raw).action is AgentAction.SEND_REPLY


def test_parse_agent_decision_rejects_universal_plan() -> None:
    raw = json.dumps(
        {"task_kind": "oa", "actions": [{"kind": "no_reply"}]},
        ensure_ascii=False,
    )

    try:
        parse_agent_decision_json(raw)
    except ValueError as exc:
        assert "AgentDecision" in str(exc)
    else:
        raise AssertionError("UniversalPlan payload was accepted")
```

- [ ] **Step 2: Run the runner tests and verify they fail**

Run `pytest -q tests/test_agent_decision_runner.py`.

Expected: collection fails because `app.agent_decision_runner` does not exist.

- [ ] **Step 3: Implement `AgentDecisionRunner` by moving only process mechanics**

Create `app/agent_decision_runner.py`. Reuse `CodexRunner`, session extraction, timeout handling, JSONL extraction, image options, and one repair attempt from `universal_planner.py`, but replace its contract with:

```python
AGENT_DECISION_SCHEMA_HINT = (
    'AgentDecision JSON: {"action":"send_reply|ask_clarifying_question|'
    'oa_approval|mail_reply|calendar_response|document_reply|message_reaction|'
    'queue_okr_review|no_reply|handoff_to_human","reason":"non-empty string",'
    '"content":"string","sensitivity_kind":"general|internal_personnel|'
    'external_candidate","personnel_subject_user_id":null,'
    '"candidate_context_known":false,"candidate_department_ids":[],'
    '"parameters":{}}. Return exactly this one object. Do not return a plan, '
    'action array, dependency list, confidence score, target IDs, blocked, '
    'stop_with_error, or memory_write action.'
)


def decision_runner_developer_instructions() -> str:
    shared = codex_developer_instructions().split("\n输出协议：", 1)[0].rstrip()
    return (
        shared
        + "\n\nYou are the decision-making Agent for this task. Gather the evidence you "
        "need, make the business judgment, and return exactly one final decision. "
        "You may call memory_write directly before returning when the shared rules "
        "require durable memory. Do not execute externally visible writes. The "
        "service will bind trusted target IDs and execute the one final decision."
    )
```

The public API is `AgentDecisionRunner(...).build_prompt(context)`,
`AgentDecisionRunner(...).decide(context, session_id=None)`, and
`parse_agent_decision_json(raw)`. Implement `decide()` with this exact control flow:

```python
def decide(
    self,
    context: UniversalTaskContext,
    session_id: str | None = None,
) -> AgentDecision:
    prompt = self.build_prompt(context)
    self.last_audit_tool_events = []
    supplied_session_id = _usable_session_id(session_id)
    self.last_session_id = supplied_session_id
    raw = self._execute(
        self._build_command(supplied_session_id, context.image_paths),
        prompt,
    )
    current_session_id = self.last_session_id
    try:
        return parse_agent_decision_json(raw)
    except (ValueError, json.JSONDecodeError):
        if not current_session_id:
            raise
    repaired = self._execute(
        self._build_command(current_session_id, context.image_paths),
        _repair_prompt(raw),
    )
    return parse_agent_decision_json(repaired)
```

`build_prompt()` must include the shared business rules, material-reference
instructions, `AGENT_DECISION_SCHEMA_HINT`, and `context.render_for_agent()`.
`_execute()`, `_build_command()`, session extraction, bounded raw output, JSONL
candidate extraction, and timeout errors retain the current tested process mechanics
from `universal_planner.py`; move those functions into the new module in the same
commit so the deleted module is never imported. `last_audit_tool_events` remains
runner metadata and must not be inserted into the decision JSON.

- [ ] **Step 4: Run runner tests**

Run `pytest -q tests/test_agent_decision_runner.py`.

Expected: all tests pass, including new/resumed command shapes and malformed-output repair.

- [ ] **Step 5: Commit the runner**

```bash
git add app/agent_decision_runner.py tests/test_agent_decision_runner.py
git commit -m "refactor: let the agent return one final decision"
```

## Task 3: Collapse Plan and Action Persistence Into One Execution Row

**Files:**
- Modify: `app/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write failing fresh-schema and identity tests**

Add tests proving one row owns the decision and old planning tables are absent from a fresh database:

```python
def test_agent_decision_execution_is_one_row_per_task_generation(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "reply.sqlite3")
    context = _universal_context(_enqueue_universal_reply_task(store))
    decision = AgentDecision(action="no_reply", reason="informational message")

    first = store.create_agent_decision_execution(context, decision, ())
    second = store.create_agent_decision_execution(context, decision, ())

    assert first.execution_id == second.execution_id
    with sqlite3.connect(store.path) as db:
        count = db.execute("select count(*) from agent_decision_executions").fetchone()[0]
        tables = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
    assert count == 1
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables


def test_agent_decision_execution_rejects_changed_decision(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "reply.sqlite3")
    context = _universal_context(_enqueue_universal_reply_task(store))
    store.create_agent_decision_execution(
        context,
        AgentDecision(action="no_reply", reason="broadcast"),
        (),
    )

    with pytest.raises(ValueError, match="decision identity mismatch"):
        store.create_agent_decision_execution(
            context,
            AgentDecision(
                action="send_reply",
                reason="changed after persistence",
                content="收到",
            ),
            (),
        )
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
pytest -q tests/test_store.py -k 'agent_decision_execution'
```

Expected: failures report missing `create_agent_decision_execution` and the old tables are still created.

- [ ] **Step 3: Add the single execution table and API**

Replace fresh-schema creation of `universal_plan_executions` and `universal_action_executions` with:

```sql
create table if not exists agent_decision_executions (
    execution_id text primary key,
    reply_task_id integer not null,
    execution_generation text not null,
    context_hash text not null,
    context_json text not null,
    decision_hash text not null,
    decision_json text not null,
    tool_events_json text not null default '[]',
    status text not null default 'ready',
    attempt_id integer not null default 0,
    result_json text not null default '',
    error text not null default '',
    started_at text not null default '',
    completed_at text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(reply_task_id, execution_generation),
    foreign key(reply_task_id) references reply_tasks(id)
);
```

Add `agent_decision_execution_id text not null default ''` and its partial unique index to `reply_attempts`.

Implement `load_agent_decision_execution`, `create_agent_decision_execution`,
`get_agent_decision_execution_state`, `claim_agent_decision_execution`,
`complete_agent_decision_execution`, `mark_agent_decision_execution_unknown`, and
`mark_agent_decision_execution_failed`. Use this transition table exactly:

| Operation | Allowed persisted state | New state / return |
|---|---|---|
| load | any row matching task + generation | return immutable execution |
| create | no row | insert `ready`; return it |
| create | matching row | return existing row |
| claim | `ready`, `failed` | update to `started`; return `NOT_STARTED` |
| claim | `succeeded` | return `SUCCEEDED` |
| claim | `started`, `unknown` | return `UNKNOWN` |
| complete | `started` | update to `succeeded` with attempt/result |
| mark unknown | `started` | update to `unknown` with safe error |
| mark failed | `started` | update to `failed` with safe error |

Every operation must verify task ID, execution generation, canonical context hash,
deterministic execution ID, canonical decision JSON, and decision SHA-256 before
changing state. A mismatched existing row raises `ValueError("decision identity
mismatch")`; a transition whose `update ... where status=?` changes zero rows raises
`ValueError("decision execution transition mismatch")`.

Do not migrate or read the old plan/action tables. Existing production tables remain physically present until a separately authorized database-retention cleanup, but no current code may create, read, or write them.

- [ ] **Step 4: Replace attempt ownership**

Rename `record_universal_reply_attempt()` to `record_agent_decision_attempt()`. It must use `agent_decision_execution_id`, reuse an existing attempt for the same execution ID, and verify `execution.decision.action.value == action`. Remove `universal_execution_scope_id` because a single decision has no plan scope.

- [ ] **Step 5: Run store tests**

Run:

```bash
pytest -q tests/test_store.py -k 'agent_decision or reply_attempt'
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit persistence simplification**

```bash
git add app/store.py tests/test_store.py
git commit -m "refactor: persist one decision execution per task"
```

## Task 4: Call the Agent Directly From the Worker

**Files:**
- Modify: `app/universal_context.py`
- Modify: `app/worker.py`
- Create: `tests/test_agent_decision_worker.py`
- Modify: `tests/test_universal_context.py`

- [ ] **Step 1: Write a failing single-flow test**

Create a fake runner with `decide()` and assert the worker performs one preflight, one decision, and one execution without planner callbacks:

```python
class RecordingDecisionRunner:
    def __init__(self, decision: AgentDecision) -> None:
        self.decision = decision
        self.calls: list[str] = []
        self.last_session_id = "decision-session-1"
        self.last_audit_tool_events: list[dict[str, str]] = []

    def decide(self, context, session_id=None):
        self.calls.append(context.trigger_message_id)
        return self.decision.model_copy(deep=True)


def test_queued_task_preflights_decides_and_executes_once(tmp_path, monkeypatch) -> None:
    runner = RecordingDecisionRunner(
        AgentDecision(action="send_reply", reason="answer", content="收到")
    )
    worker, trigger = make_worker(
        tmp_path,
        monkeypatch,
        agent_decision_runner=runner,
    )
    enqueue(worker, trigger)

    assert worker.consume_once(max_tasks=1) == 1

    assert runner.calls == ["trigger-1"]
    assert worker.dws.auth_status_calls == 1
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        trigger.open_conversation_id,
        trigger.open_message_id,
    )
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
```

Add another test where the stored execution state is `succeeded`; the runner must not be called and the trigger must be marked seen.

- [ ] **Step 2: Run the tests and verify the old constructor rejects the new injection**

Run `pytest -q tests/test_agent_decision_worker.py`.

Expected: failure mentions the unexpected `agent_decision_runner` argument.

- [ ] **Step 3: Remove planner dependencies from task context**

Delete `required_dependencies` from `UniversalTaskContext`, `render_for_agent()`, `canonical_universal_context_json()`, and `build_universal_context()`. Update context tests so the rendered prompt contains no `Required dependencies:` line and the canonical hash changes only for real task/evidence changes.

- [ ] **Step 4: Replace worker construction and routing**

In `DingTalkAutoReplyWorker.__init__`, replace `universal_planner` and `universal_dependency_status_provider` with `agent_decision_runner`. Replace `_universal_planner()` with `_agent_decision_runner()` returning `AgentDecisionRunner`. Keep the session lock and stale-session recovery mechanics, but rename them to `_agent_decision_session()` and `_stale_resume_recovering_decision_runner()`.

Replace `self._universal_consumer().process(context)` with this direct lifecycle:

```python
def _process_agent_task(self, context: UniversalTaskContext, trigger: DingTalkMessage) -> bool:
    if self._universal_existing_terminal_attempt(context) or self._universal_existing_sent_reply(context):
        self._mark_seen([trigger])
        return True

    self._ensure_dws_ready_for_codex()
    execution = self.store.load_agent_decision_execution(context)
    if execution is None:
        runner = self._stale_resume_recovering_decision_runner(
            self._agent_decision_runner()
        )
        with self._agent_decision_session(context, runner):
            decision = runner.decide(
                context,
                session_id=self._universal_session_id(context),
            )
            execution = self.store.create_agent_decision_execution(
                context,
                decision,
                tuple(runner.last_audit_tool_events),
            )

    if context.dry_run:
        return False
    state = self.store.get_agent_decision_execution_state(execution)
    if state is AgentDecisionExecutionState.SUCCEEDED:
        self._mark_seen([trigger])
        return True
    if state is AgentDecisionExecutionState.UNKNOWN:
        raise UniversalActionUnknownError(
            f"agent_decision_outcome_unknown:{execution.execution_id}"
        )
    self.execute_agent_decision(execution)
    self._mark_seen([trigger])
    return True
```

Keep the second duplicate check inside `create_agent_decision_execution()`'s transaction boundary so concurrent workers cannot persist two decisions for one task generation.

- [ ] **Step 5: Add one explicit dispatcher**

Add `execute_agent_decision()` with one handler per action and no list loop:

```python
def execute_agent_decision(self, execution: AgentDecisionExecution) -> bool:
    handlers = {
        AgentAction.SEND_REPLY: self.execute_agent_send_reply,
        AgentAction.ASK_CLARIFYING_QUESTION: self.execute_agent_send_reply,
        AgentAction.OA_APPROVAL: self.execute_agent_oa_approval,
        AgentAction.MAIL_REPLY: self.execute_agent_mail_reply,
        AgentAction.CALENDAR_RESPONSE: self.execute_agent_calendar_response,
        AgentAction.DOCUMENT_REPLY: self.execute_agent_document_reply,
        AgentAction.MESSAGE_REACTION: self.execute_agent_message_reaction,
        AgentAction.QUEUE_OKR_REVIEW: self.execute_agent_okr_review,
        AgentAction.NO_REPLY: self.execute_agent_terminal,
        AgentAction.HANDOFF_TO_HUMAN: self.execute_agent_terminal,
    }
    return handlers[execution.decision.action](execution)
```

The dispatcher must not alter the decision, synthesize `no_reply`, or append a memory action.

- [ ] **Step 6: Run worker and context tests**

Run:

```bash
pytest -q tests/test_agent_decision_worker.py tests/test_universal_context.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit direct worker routing**

```bash
git add app/universal_context.py app/worker.py tests/test_agent_decision_worker.py tests/test_universal_context.py
git commit -m "refactor: execute one agent decision directly"
```

## Task 5: Make Executors Consume the Decision Without Agent-Supplied Targets

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_universal_worker.py`
- Modify: `tests/test_universal_worker_wiring.py`
- Modify: `tests/test_universal_okr.py`
- Modify: `tests/test_universal_parity.py`

- [ ] **Step 1: Write target-binding regressions**

Add focused tests for each external target:

```python
def test_oa_executor_uses_trusted_context_ids_not_decision_parameters(worker, context):
    context = replace(
        context,
        trusted_oa_process_instance_id="trusted-process",
        trusted_oa_task_id="trusted-task",
    )
    execution = build_agent_decision_execution(
        context,
        AgentDecision(
            action="oa_approval",
            reason="approve after review",
            content="材料完整，同意。",
            parameters={"operation": "同意"},
        ),
    )

    assert worker.execute_agent_oa_approval(execution) is True
    assert worker.dws.oa_calls == [
        ("trusted-process", "trusted-task", "同意", "材料完整，同意。")
    ]


def test_calendar_executor_uses_trusted_event_id(worker, context):
    context = replace(context, trusted_calendar_event_id="trusted-event")
    execution = build_agent_decision_execution(
        context,
        AgentDecision(
            action="calendar_response",
            reason="time is available",
            parameters={"response_status": "accepted"},
        ),
    )

    assert worker.execute_agent_calendar_response(execution) is True
    assert worker.dws.calendar_calls == [("trusted-event", "accepted")]
```

Add the same assertion pattern for mail (`trusted_mail_*`), document/reaction/reply (`conversation_id` and `trigger_message_id`), and OKR (current trigger/sender).

- [ ] **Step 2: Run the target-binding tests and verify signature failures**

Run:

```bash
pytest -q tests/test_universal_worker.py tests/test_universal_worker_wiring.py tests/test_universal_okr.py -k 'trusted or agent_decision'
```

Expected: failures show the executors still expect `UniversalActionExecution` and `execution.action.target`.

- [ ] **Step 3: Port each executor to `AgentDecisionExecution`**

Apply these mechanical rules to every executor:

```python
decision = execution.decision
content = decision.content.strip()
parameters = decision.parameters
```

- Reply: destination is `execution.context.conversation_id` and the immutable trigger.
- OA: process/task IDs are `execution.context.trusted_oa_process_instance_id` and `trusted_oa_task_id`; only `operation`, return activity metadata, and remark content come from the decision.
- Mail: mailbox/message/subject are the three `trusted_mail_*` fields; only reply content comes from the decision.
- Calendar: event ID is `trusted_calendar_event_id`; only `response_status` comes from the decision.
- Document, reaction, and OKR: conversation/trigger/sender come from context.

Rename claim/complete/fail/unknown and attempt calls to the new store API. Preserve definite-failure versus unknown-outcome behavior and existing receipt verification.

- [ ] **Step 4: Remove service-generated companion actions**

Make OA approval/comment, mail reply, calendar response, reaction, and OKR queue terminal on their own. Delete code and prompt text that pairs them with `no_reply` or automatically creates a second chat action. A low-level executor may return its existing receipt, but it must not call another top-level decision handler.

- [ ] **Step 5: Remove planned Memory writes**

Delete `execute_universal_memory_write()`, its lease-specific store API, and `MEMORY_WRITE` tests. Keep `memory_write` in the Agent's available tools and shared instructions; its failure remains non-blocking and cannot alter the final decision.

- [ ] **Step 6: Run executor regression suites**

Run:

```bash
pytest -q \
  tests/test_universal_worker.py \
  tests/test_universal_worker_wiring.py \
  tests/test_universal_okr.py \
  tests/test_universal_parity.py
```

Expected: all tests pass with one execution per task generation and no multi-action fixtures.

- [ ] **Step 7: Commit executor migration**

```bash
git add app/worker.py tests/test_universal_worker.py tests/test_universal_worker_wiring.py tests/test_universal_okr.py tests/test_universal_parity.py
git commit -m "refactor: bind trusted targets for single decisions"
```

## Task 6: Preserve Full OA Material Reading Without Business Rules in Code

**Files:**
- Modify: `app/org_cache.py`
- Modify: `app/dws_client.py`
- Modify: `app/worker.py`
- Modify: `tests/test_org_cache.py`
- Modify: `tests/test_dws_client.py`
- Modify: `tests/test_universal_context_enrichment.py`

- [ ] **Step 1: Keep the existing failing adapter regressions**

Retain the current uncommitted assertions that `CachedDwsClient` forwards `list_doc_nodes()` and `read_sheet()` and that `DwsClient.build_read_sheet_command()` produces the read-only sheet command. Run:

```bash
pytest -q \
  tests/test_org_cache.py::test_cached_dws_client_delegates_linked_material_reads \
  tests/test_dws_client.py::test_read_sheet_command_shape
```

Expected before implementation: `CachedDwsClient` lacks `list_doc_nodes` and `read_sheet`.

- [ ] **Step 2: Add direct read-only forwarding**

Add these methods to `CachedDwsClient`; they must delegate without caching because document contents can change:

```python
def list_doc_nodes(
    self,
    workspace_id: str | None = None,
    folder_id: str | None = None,
    page_token: str = "",
):
    return self.client.list_doc_nodes(
        workspace_id=workspace_id,
        folder_id=folder_id,
        page_token=page_token,
    )


def read_sheet(self, node: str):
    return self.client.read_sheet(node)
```

Add `DwsClient.read_sheet(node)` as `run_json(build_read_sheet_command(node))`; the builder must use the existing DWS sheet read command and `--format json`.

- [ ] **Step 3: Add the #3339 material regression**

Add a worker-context test where an OA operation remark contains a DingTalk folder with four children. The fake DWS client must return folder listing plus document, sheet, and XLSX bodies. Assert the Agent context contains all four child titles and body excerpts before `decide()` runs, and assert no code selects approval/comment based on those strings.

- [ ] **Step 4: Keep material hydration bounded and neutral**

`_resolve_universal_material_references()` may identify file type, traverse the referenced folder, and return bounded body text or a concrete read error. It must not contain approval keywords, thresholds, risk scoring, or branches that choose an `AgentAction`. Rename it `_hydrate_agent_material_references()` to reflect that it supplies evidence rather than planning.

- [ ] **Step 5: Run material tests**

Run:

```bash
pytest -q tests/test_org_cache.py tests/test_dws_client.py tests/test_universal_context_enrichment.py
```

Expected: all tests pass, including the folder/child-body regression.

- [ ] **Step 6: Commit material delivery**

```bash
git add app/org_cache.py app/dws_client.py app/worker.py tests/test_org_cache.py tests/test_dws_client.py tests/test_universal_context_enrichment.py
git commit -m "fix: deliver complete OA materials to the agent"
```

## Task 7: Delete Planning Layers and Plan-Level UI

**Files:**
- Delete: `app/universal_plan.py`
- Delete: `app/universal_planner.py`
- Delete: `app/universal_consumer.py`
- Delete: `app/universal_validator.py`
- Delete: `app/universal_executor.py`
- Delete: `tests/test_universal_plan.py`
- Delete: `tests/test_universal_planner.py`
- Delete: `tests/test_universal_consumer.py`
- Delete: `tests/test_universal_validator.py`
- Delete: `tests/test_universal_executor.py`
- Delete: `tests/test_universal_memory.py`
- Modify: `app/history.py`
- Modify: `app/audit_web.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_audit_web.py`
- Modify: `tests/e2e/test_local_pipeline.py`

- [ ] **Step 1: Move surviving behavioral coverage before deleting tests**

Move only still-valid behavior into the new decision suites: duplicate suppression, dry run, resumed execution, unknown outcome, stale Codex session recovery, trusted target binding, OA comment fallback, calendar missing-event no-op, document recovery checkpoint, reaction recovery checkpoint, and OKR queueing. Do not copy tests for dependency ordering, action index, action conflicts, plan confidence, or multi-action execution.

- [ ] **Step 2: Remove plan observability**

Delete `UniversalActionObservation`, `UniversalExecutionObservation`, `get_universal_execution_observability()`, `list_universal_execution_observability()`, `_universal_history_observability()`, and `_universal_execution_card()`. The attempt page already exposes the final action, status, reason, generated content, OA/calendar/mail metadata, tool events, and receipts; no replacement plan card is needed.

- [ ] **Step 3: Delete obsolete modules and tests**

Delete the files listed above only after Tasks 1–6 pass. Remove their imports from `worker.py`, `store.py`, history/UI code, and tests.

- [ ] **Step 4: Prove the planning API is gone**

Run:

```bash
rg -n "UniversalPlanner|UniversalPlan|PlannedAction|UniversalConsumerOrchestrator|UniversalValidator|UniversalActionExecutor|execution_dependencies|action_index" app tests
```

Expected: no matches.

Run:

```bash
rg -n '"actions"|"dependencies"|"confidence"|blocked|stop_with_error|memory_write' app/agent_decision.py app/agent_decision_runner.py tests/test_agent_decision.py tests/test_agent_decision_runner.py
```

Expected: matches only in negative assertions/instructions that explicitly prohibit the removed fields/actions; no schema or runtime branch accepts them.

- [ ] **Step 5: Run the focused architecture suite**

Run:

```bash
pytest -q \
  tests/test_agent_decision.py \
  tests/test_agent_decision_runner.py \
  tests/test_agent_decision_worker.py \
  tests/test_store.py \
  tests/test_history.py \
  tests/test_audit_web.py \
  tests/e2e/test_local_pipeline.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit deletion**

```bash
git add \
  app/history.py \
  app/audit_web.py \
  tests/test_history.py \
  tests/test_audit_web.py \
  tests/e2e/test_local_pipeline.py
git add -u -- \
  app/universal_plan.py \
  app/universal_planner.py \
  app/universal_consumer.py \
  app/universal_validator.py \
  app/universal_executor.py \
  tests/test_universal_plan.py \
  tests/test_universal_planner.py \
  tests/test_universal_consumer.py \
  tests/test_universal_validator.py \
  tests/test_universal_executor.py \
  tests/test_universal_memory.py
git commit -m "refactor: remove universal planning orchestration"
```

Before running this commit command, inspect `git diff --cached --name-only`. In
particular, do not stage the existing changes in `app/defaults/developer_prompt.md`,
`scripts/dingteam_okr_live_source.py`, `tests/test_dingteam_okr_live_source.py`, or
`tests/test_prompt.py` unless a later explicit review proves they belong to this
migration.

## Task 8: Document, Verify, Restart, and Re-run the Approval

**Files:**
- Modify: `docs/reply-worker-reliability.md`
- Test: full repository test suite
- Runtime: `com.ceo-agent-service.main`

- [ ] **Step 1: Update reliability documentation**

Add a section with this contract:

```markdown
## Single Agent decision

Each reply-task generation has one persisted Agent decision and one execution state.
The Agent owns evidence use, judgment, wording, and the final action. The service does
not append actions, convert outcomes to `blocked`, or interpret confidence scores.

The service owns only DWS readiness, trusted destination IDs, permission checks,
exactly-once claims, external receipts, and unknown-outcome handling. A retry reuses
the persisted decision. A manual rerun creates a new execution generation and asks
the Agent for a new decision.

OA approval/comment is complete by itself. If a real OA return target is unavailable,
the Agent can choose an OA comment that tells the applicant what to supplement; the
service must not synthesize a blocked result or a companion `no_reply` action.
```

- [ ] **Step 2: Run static checks**

Run:

```bash
python -m compileall -q app tests
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass. If unrelated pre-existing failures remain, record their exact test names and prove every suite touched by this plan is green; do not call the migration complete while a touched failure remains.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: describe single agent decision execution"
```

- [ ] **Step 5: Verify no unresolved runtime backlog before restart**

Run read-only checks against the configured SQLite database for `reply_tasks.status in ('failed', 'processing')`. Record counts and task IDs. Do not bulk-rerun unrelated tasks.

- [ ] **Step 6: Restart the main service after explicit service-control confirmation**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: launchd reports `state = running` with a new PID.

- [ ] **Step 7: Re-run only approval attempt #3339's task**

Use the existing rerun endpoint/CLI with `--force-new-decision` for the exact conversation and trigger linked to attempt `3339`. Do not enqueue the surrounding backlog.

Verify all of the following from the new attempt and live DWS read-back:

- the Agent input contains the folder and all four material bodies;
- the new Agent output contains one decision object and no action array;
- an executable OA return uses a verified return target;
- otherwise the Agent posts a concrete OA comment to the applicant instead of producing `blocked`;
- the execution row reaches `succeeded` and the reply task reaches `done`;
- the DWS OA receipt reports success and the audit page displays the resulting comment/action.

- [ ] **Step 8: Final completion evidence**

Report commit hashes, focused/full test counts, old/new service PID, exact rerun attempt ID, single Agent action, execution status, and DWS receipt status. Do not report the bug fixed if the live attempt still says the material could not be read.

## Self-Review

- Spec coverage: Tasks 1–2 remove the Planner contract; Tasks 3–5 replace multi-action orchestration with one decision while preserving hard safety boundaries; Task 6 covers the real unreadable-material failure; Task 7 deletes the old architecture; Task 8 documents and proves the live result.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, placeholder method body, or unspecified error-handling step remains.
- Type consistency: all runtime paths use `AgentAction`, `AgentDecision`, `AgentDecisionExecution`, and `AgentDecisionExecutionState`; runner method is `decide()`, persisted attempt key is `agent_decision_execution_id`, and worker dispatcher is `execute_agent_decision()`.
- YAGNI check: there is no compatibility adapter from `UniversalPlan`, no multi-action wrapper containing one item, no generic dependency framework, no confidence threshold, and no replacement plan UI.
