# Universal Consumer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one universal CEO reply-task consumer that plans with an AI agent, validates with deterministic service rules, and executes DingTalk/OA/mail/calendar/document/memory actions through owned capability executors.

**Architecture:** Keep producers as discovery-only code that enqueues `reply_tasks`; move intent selection out of scattered `_handle_*_if_actionable` branches into a single `UniversalConsumerOrchestrator`. The orchestrator builds a task context, asks Codex for a structured plan, validates dependency, permission, dedupe, target, and expiry rules inside the service, then dispatches only validated actions to deterministic executors that record terminal state in SQLite.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite store, pytest, existing `CodexRunner`/`CodexDecisionRunner`, DWS CLI, existing DingTalk/OA/mail/calendar clients, launchd service `com.ceo-agent-service.main`.

---

## Outcome

This is the one-step path: replace the consumer's current domain-first branching with a service-owned planning/execution pipeline, while retaining all existing safety rules and delivery code.

The AI is responsible for deciding what kind of work is requested and what evidence it needs. The service remains responsible for whether a dependency is usable, whether an action is allowed, whether a duplicate already exists, and whether the final external action is actually sent or executed.

## Current Shape

Existing code already has the ingredients:

- `app/worker.py::consume_once` claims `reply_tasks`, handles authorization/retry/fail state, and calls `_process_queued_task`.
- `app/worker.py::_process_queued_task` currently performs domain routing before the general agent path: minutes permission, calendar invite, OA approval, system notification, then `_process_batch`.
- `app/worker.py::_process_batch` builds the normal reply prompt, runs Codex, records `reply_attempts`, executes calendar/mail/reply side effects, and updates send state.
- `app/worker.py::_handle_oa_approval_if_actionable` contains important service-owned guards: OA URL recovery, Derek ownership checks, target extraction, and final action execution.
- `app/dingtalk_models.py::CodexDecision` is the legacy single-decision shape.
- `app/codex_decision.py::CodexDecisionRunner` already runs native `codex exec`, parses JSON, repairs invalid JSON once, tracks sessions, and captures audit events.
- `app/structured_agent.py::StructuredCodexRunner` is available for future typed-agent specs, but the first implementation should keep the universal planner small and local to the consumer path.

## File Structure

- Create `app/universal_plan.py`: Pydantic schema for `UniversalPlan`, `PlannedAction`, dependency declarations, target models, and terminal failure reasons.
- Create `app/universal_planner.py`: prompt builder and Codex-backed planner that returns `UniversalPlan`.
- Create `app/universal_validator.py`: deterministic service validation for dependency readiness, DWS blocking, OA ownership/material, send dedupe, dry-run, stale trigger, and external-action safety.
- Create `app/universal_executor.py`: adapter layer that maps validated universal actions to existing `DingTalkWorker` methods without duplicating DWS side-effect logic.
- Create `app/universal_consumer.py`: orchestration object called by `DingTalkWorker._process_queued_task`.
- Modify `app/worker.py`: wire the orchestrator into queued-task processing, preserve old paths behind the executor adapter during migration, and keep `consume_once` retry/authorization semantics unchanged.
- Modify `app/codex_decision.py`: expose one reusable raw Codex execution helper for the universal planner, without changing legacy decision behavior.
- Modify `app/defaults/developer_prompt.md`: add the universal planning contract and dependency policy in one place.
- Modify `app/audit_web.py` and `app/history.py`: surface planner kind, selected capability, blocking dependency, and validated action status.
- Modify `app/store.py`: add narrow attempt metadata accessors if existing `audit_tool_events_json` and `audit_summary` are insufficient; avoid schema changes unless a test proves the current columns cannot represent the final state.
- Create `tests/test_universal_plan.py`: schema and compatibility tests.
- Create `tests/test_universal_planner.py`: prompt/parser tests with invalid JSON repair.
- Create `tests/test_universal_validator.py`: deterministic guard tests.
- Create `tests/test_universal_consumer.py`: orchestration tests.
- Modify `tests/test_worker.py`: end-to-end queued-task behavior for DWS auth blocking, OA follow-up, mail reply, calendar reply, no-reply, memory write, and duplicate suppression.
- Modify `tests/test_audit_web.py`: history/debug rendering tests for universal attempts.
- Create `docs/universal-consumer-agent.md`: operator-facing architecture and recovery notes.

## Principles

1. DWS is a blocking dependency for DingTalk tasks. If DWS auth/status is not ready, do not start Codex for that task. Start the required auth flow once, defer the task, and record a clear authorization state.
2. AI routing is not external-action authority. The planner can propose actions; validators and executors decide whether they are executable.
3. Keep final state explicit: `sent`, `skipped`, `blocked`, `failed`, `oa_action_success`, `commented`, `done`, or `discarded` with a concrete reason.
4. Do not create new queues for the same trigger. Reuse the existing `reply_tasks` and `reply_attempts` identities.
5. Make old specialized handlers callable as capability executors first; delete or collapse old direct routing only after tests prove parity.

---

### Task 1: Universal Plan Schema

**Files:**
- Create: `app/universal_plan.py`
- Test: `tests/test_universal_plan.py`

- [ ] **Step 1: Write schema tests**

Create `tests/test_universal_plan.py`:

```python
import pytest
from pydantic import ValidationError

from app.universal_plan import (
    DependencyName,
    PlannedAction,
    PlannedActionKind,
    UniversalPlan,
)


def test_universal_plan_accepts_reply_and_memory_actions():
    plan = UniversalPlan.model_validate(
        {
            "planner_version": "2026-07-20",
            "task_kind": "reply",
            "reason": "用户要求回复群消息并记录长期偏好。",
            "dependencies": ["dws", "memory"],
            "actions": [
                {
                    "kind": "send_reply",
                    "reason": "需要在原钉钉会话回应。",
                    "target": {
                        "conversation_id": "cid-1",
                        "trigger_message_id": "msg-1",
                    },
                    "payload": {"text": "收到，我来推进。"},
                },
                {
                    "kind": "memory_write",
                    "reason": "这是稳定偏好。",
                    "target": {"scope": "user_profile"},
                    "payload": {"text": "Derek prefers explicit final states."},
                },
            ],
            "audit": {
                "summary": "根据 trigger 和上下文判断需要回复并写 memory。",
                "documents": [],
                "confidence": 0.82,
            },
        }
    )

    assert plan.dependencies == [DependencyName.DWS, DependencyName.MEMORY]
    assert plan.actions[0].kind is PlannedActionKind.SEND_REPLY
    assert plan.actions[0].payload["text"] == "收到，我来推进。"


def test_universal_plan_rejects_reply_without_text():
    with pytest.raises(ValidationError) as exc_info:
        UniversalPlan.model_validate(
            {
                "planner_version": "2026-07-20",
                "task_kind": "reply",
                "reason": "需要回复。",
                "dependencies": ["dws"],
                "actions": [
                    {
                        "kind": "send_reply",
                        "reason": "需要回复。",
                        "target": {
                            "conversation_id": "cid-1",
                            "trigger_message_id": "msg-1",
                        },
                        "payload": {"text": ""},
                    }
                ],
                "audit": {"summary": "材料足够。", "documents": [], "confidence": 0.7},
            }
        )

    assert "send_reply payload.text must be non-empty" in str(exc_info.value)


def test_universal_plan_allows_blocked_terminal_action():
    action = PlannedAction.model_validate(
        {
            "kind": "blocked",
            "reason": "DWS auth required before reading DingTalk context.",
            "target": {"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
            "payload": {
                "blocker": "dws_authorization_required",
                "terminal": False,
            },
        }
    )

    assert action.kind is PlannedActionKind.BLOCKED
    assert action.payload["blocker"] == "dws_authorization_required"
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
pytest tests/test_universal_plan.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_plan'
```

- [ ] **Step 3: Add the schema**

Create `app/universal_plan.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class DependencyName(StrEnum):
    DWS = "dws"
    LARK = "lark"
    EXA = "exa"
    MEMORY = "memory"
    XIAOQING_INTERVIEW = "xiaoqing_interview"
    MAIL = "mail"
    CALENDAR = "calendar"


class PlannedActionKind(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    OA_APPROVAL = "oa_approval"
    MAIL_REPLY = "mail_reply"
    CALENDAR_RESPONSE = "calendar_response"
    DWS_MARKDOWN_DOCUMENT_REPLY = "dws_markdown_document_reply"
    DWS_MESSAGE_REACTION = "dws_message_reaction"
    MEMORY_WRITE = "memory_write"
    NO_REPLY = "no_reply"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    BLOCKED = "blocked"
    STOP_WITH_ERROR = "stop_with_error"


class UniversalAudit(BaseModel):
    summary: str
    documents: list[dict[str, str]] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_summary(self) -> "UniversalAudit":
        if not self.summary.strip():
            raise ValueError("audit.summary must be non-empty")
        return self


class PlannedAction(BaseModel):
    kind: PlannedActionKind
    reason: str
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_action_fields(self) -> "PlannedAction":
        if not self.reason.strip():
            raise ValueError("planned action reason must be non-empty")
        if self.kind in {
            PlannedActionKind.SEND_REPLY,
            PlannedActionKind.ASK_CLARIFYING_QUESTION,
        }:
            text = str(self.payload.get("text") or "").strip()
            if not text:
                raise ValueError(f"{self.kind.value} payload.text must be non-empty")
        if self.kind is PlannedActionKind.MAIL_REPLY:
            content = str(self.payload.get("content") or "").strip()
            mailbox = str(self.target.get("mailbox") or "").strip()
            message_id = str(self.target.get("message_id") or "").strip()
            if not mailbox or not message_id or not content:
                raise ValueError(
                    "mail_reply requires target.mailbox, target.message_id, and payload.content"
                )
        if self.kind is PlannedActionKind.OA_APPROVAL:
            action = str(self.payload.get("action") or "").strip()
            remark = str(self.payload.get("remark") or "").strip()
            if action not in {"同意", "拒绝", "退回", "comment"}:
                raise ValueError("oa_approval payload.action must be 同意, 拒绝, 退回, or comment")
            if not remark:
                raise ValueError("oa_approval payload.remark must be non-empty")
        return self


class UniversalPlan(BaseModel):
    planner_version: str
    task_kind: str
    reason: str
    dependencies: list[DependencyName] = Field(default_factory=list)
    actions: list[PlannedAction]
    audit: UniversalAudit

    @model_validator(mode="after")
    def require_plan_fields(self) -> "UniversalPlan":
        if self.planner_version != "2026-07-20":
            raise ValueError("planner_version must be 2026-07-20")
        if not self.task_kind.strip():
            raise ValueError("task_kind must be non-empty")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if not self.actions:
            raise ValueError("actions must contain at least one action")
        return self
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
pytest tests/test_universal_plan.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit schema**

```bash
git add app/universal_plan.py tests/test_universal_plan.py
git commit -m "feat: add universal consumer plan schema"
```

---

### Task 2: Universal Task Context

**Files:**
- Create: `app/universal_context.py`
- Modify: `app/worker.py` constructor to pass service dependencies into context builder
- Test: `tests/test_universal_context.py`

- [ ] **Step 1: Write context tests**

Create `tests/test_universal_context.py`:

```python
from datetime import datetime, timezone

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.universal_context import UniversalTaskContext, build_universal_context


def _message(message_id: str, text: str) -> DingTalkMessage:
    return DingTalkMessage(
        sender_name="Mina",
        sender_staff_id="staff-1",
        open_message_id=message_id,
        content=text,
        create_time=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
    )


def test_build_universal_context_includes_trigger_and_recent_context():
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="Q3战略群",
        single_chat=False,
        unread_point=1,
    )
    trigger = _message("msg-2", "怎么提 PR？")
    context = [_message("msg-1", "我们需要把修改合并。"), trigger]

    result = build_universal_context(
        conversation=conversation,
        trigger=trigger,
        context_messages=context,
        task_id=42,
        force_new_decision=False,
        dry_run=False,
    )

    assert isinstance(result, UniversalTaskContext)
    assert result.task_id == 42
    assert result.conversation_title == "Q3战略群"
    assert result.trigger_message_id == "msg-2"
    assert "怎么提 PR" in result.render_for_agent()
    assert "我们需要把修改合并" in result.render_for_agent()


def test_context_marks_dingtalk_dependency_as_required():
    context = build_universal_context(
        conversation=DingTalkConversation(
            open_conversation_id="cid-1",
            title="星尘大家庭",
            single_chat=False,
            unread_point=1,
        ),
        trigger=_message("msg-1", "看一下这个群消息"),
        context_messages=[],
        task_id=7,
        force_new_decision=True,
        dry_run=True,
    )

    assert context.required_dependencies == ["dws"]
```

- [ ] **Step 2: Run context tests and verify import failure**

Run:

```bash
pytest tests/test_universal_context.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_context'
```

- [ ] **Step 3: Add context model and renderer**

Create `app/universal_context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from app.dingtalk_models import DingTalkConversation, DingTalkMessage


@dataclass(frozen=True)
class UniversalTaskContext:
    task_id: int
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    context_messages: list[DingTalkMessage]
    required_dependencies: list[str]
    force_new_decision: bool
    dry_run: bool

    def render_for_agent(self) -> str:
        rendered_messages = []
        for message in self.context_messages:
            rendered_messages.append(
                f"- {message.sender_name} ({message.open_message_id}): {message.content}"
            )
        context_block = "\n".join(rendered_messages) or "- 无可用上下文消息"
        return "\n".join(
            [
                "# Universal consumer task",
                f"task_id: {self.task_id}",
                f"conversation_id: {self.conversation_id}",
                f"conversation_title: {self.conversation_title}",
                f"single_chat: {self.single_chat}",
                f"trigger_message_id: {self.trigger_message_id}",
                f"trigger_sender: {self.trigger_sender}",
                f"trigger_text: {self.trigger_text}",
                f"required_dependencies: {', '.join(self.required_dependencies)}",
                f"force_new_decision: {self.force_new_decision}",
                f"dry_run: {self.dry_run}",
                "",
                "## Recent messages",
                context_block,
            ]
        )


def build_universal_context(
    *,
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    context_messages: list[DingTalkMessage],
    task_id: int,
    force_new_decision: bool,
    dry_run: bool,
) -> UniversalTaskContext:
    merged_context = list(context_messages)
    if not any(message.open_message_id == trigger.open_message_id for message in merged_context):
        merged_context.append(trigger)
    return UniversalTaskContext(
        task_id=task_id,
        conversation_id=conversation.open_conversation_id,
        conversation_title=conversation.title,
        single_chat=conversation.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        context_messages=merged_context,
        required_dependencies=["dws"],
        force_new_decision=force_new_decision,
        dry_run=dry_run,
    )
```

- [ ] **Step 4: Run context tests**

Run:

```bash
pytest tests/test_universal_context.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit context builder**

```bash
git add app/universal_context.py tests/test_universal_context.py
git commit -m "feat: add universal consumer task context"
```

---

### Task 3: Universal Planner Prompt and Parser

**Files:**
- Create: `app/universal_planner.py`
- Modify: `app/codex_decision.py` only if a public raw runner helper is needed
- Test: `tests/test_universal_planner.py`

- [ ] **Step 1: Write planner tests**

Create `tests/test_universal_planner.py`:

```python
import json
from pathlib import Path

from app.universal_context import UniversalTaskContext
from app.universal_plan import PlannedActionKind
from app.universal_planner import UniversalPlanner, parse_universal_plan_json


def _context() -> UniversalTaskContext:
    return UniversalTaskContext(
        task_id=12,
        conversation_id="cid-1",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="帮我找几个挑战的话题和分身讨论",
        context_messages=[],
        required_dependencies=["dws"],
        force_new_decision=False,
        dry_run=False,
    )


def test_parse_universal_plan_json_from_codex_jsonl_item_text():
    payload = {
        "planner_version": "2026-07-20",
        "task_kind": "reply",
        "reason": "Mina 要求具体话题建议。",
        "dependencies": ["dws"],
        "actions": [
            {
                "kind": "send_reply",
                "reason": "需要直接回应。",
                "target": {"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
                "payload": {"text": "可以先讨论招聘标准、产品节奏和组织协作。"},
            }
        ],
        "audit": {"summary": "trigger 本体足够判断。", "documents": [], "confidence": 0.8},
    }
    raw = json.dumps({"item": {"text": json.dumps(payload, ensure_ascii=False)}})

    plan = parse_universal_plan_json(raw)

    assert plan.actions[0].kind is PlannedActionKind.SEND_REPLY
    assert "招聘标准" in plan.actions[0].payload["text"]


def test_planner_builds_prompt_with_dependency_policy():
    planner = UniversalPlanner(workspace=Path("/tmp"), executor=lambda _cmd, _prompt: "")

    prompt = planner.build_prompt(_context())

    assert "DWS 是 DingTalk 任务的阻断性依赖" in prompt
    assert "只输出 UniversalPlan JSON" in prompt
    assert "帮我找几个挑战的话题" in prompt
```

- [ ] **Step 2: Run planner tests and verify import failure**

Run:

```bash
pytest tests/test_universal_planner.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_planner'
```

- [ ] **Step 3: Add planner implementation**

Create `app/universal_planner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CodexRunner,
    _config_string,
    codex_model_config_options,
    memory_connector_config_options,
    passthrough_mcp_server_config_options,
)
from app.process_runner import run_process_with_idle_timeout
from app.universal_context import UniversalTaskContext
from app.universal_plan import UniversalPlan


UNIVERSAL_PLAN_SCHEMA_HINT = """
只输出 UniversalPlan JSON:
{
  "planner_version": "2026-07-20",
  "task_kind": "reply|oa|mail|calendar|document|memory|no_reply|blocked",
  "reason": "为什么这是该任务类型",
  "dependencies": ["dws"],
  "actions": [
    {
      "kind": "send_reply|ask_clarifying_question|oa_approval|mail_reply|calendar_response|dws_markdown_document_reply|dws_message_reaction|memory_write|no_reply|handoff_to_human|blocked|stop_with_error",
      "reason": "为什么需要这个动作",
      "target": {},
      "payload": {}
    }
  ],
  "audit": {"summary": "证据摘要", "documents": [], "confidence": 0.0}
}
""".strip()


class UniversalPlanner:
    def __init__(
        self,
        *,
        workspace: Path,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str], str] | None = None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
    ):
        self.workspace = workspace
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.last_session_id = ""
        self.last_raw_output = ""

    def build_prompt(self, context: UniversalTaskContext) -> str:
        return "\n\n".join(
            [
                "# CEO universal consumer planner",
                "你负责判断一个 CEO reply_task 应该如何处理，但不直接执行外部副作用。",
                "DWS 是 DingTalk 任务的阻断性依赖；如果服务端已经说明 DWS 不可用，你必须输出 blocked。",
                "审批、邮件、日程、文档写入、群回复、reaction、memory 写入都只能作为计划动作提出。",
                "不要要求在 agent 内执行 dws auth login；授权由服务端在 agent 前置检查阶段处理。",
                "只输出 UniversalPlan JSON，不要解释，不要 Markdown。",
                UNIVERSAL_PLAN_SCHEMA_HINT,
                context.render_for_agent(),
            ]
        )

    def plan(self, context: UniversalTaskContext, *, session_id: str | None) -> UniversalPlan:
        prompt = self.build_prompt(context)
        command = self._build_command(prompt, session_id)
        raw = self._execute(command, prompt)
        self.last_raw_output = raw
        return parse_universal_plan_json(raw)

    def _build_command(self, prompt: str, session_id: str | None) -> list[str]:
        common = [
            "--json",
            *codex_model_config_options(ignore_user_config=True),
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "hooks",
            *memory_connector_config_options(),
            *passthrough_mcp_server_config_options(),
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "-c",
            _config_string("developer_instructions", "Return only UniversalPlan JSON."),
        ]
        if session_id:
            return [self.runner.codex_bin, "exec", "resume", *common, session_id, "-"]
        return [
            self.runner.codex_bin,
            "exec",
            *common,
            "--cd",
            str(self.workspace),
            "-",
        ]

    def _execute(self, command: list[str], prompt: str) -> str:
        if self.executor is not None:
            return self.executor(command, prompt)
        return run_process_with_idle_timeout(
            command,
            input_text=prompt,
            timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds,
            cwd=self.workspace,
        )


def parse_universal_plan_json(raw: str) -> UniversalPlan:
    parsed_lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for payload in reversed(parsed_lines):
        candidate = _candidate_payload(payload)
        if candidate is not None:
            return UniversalPlan.model_validate(candidate)
    raise ValueError("no valid UniversalPlan found")


def _candidate_payload(payload: object) -> object | None:
    if isinstance(payload, dict) and "planner_version" in payload:
        return payload
    if not isinstance(payload, dict):
        return None
    item = payload.get("item")
    if isinstance(item, dict) and isinstance(item.get("text"), str):
        return json.loads(item["text"])
    message = payload.get("message")
    if isinstance(message, str) and message.strip().startswith("{"):
        return json.loads(message)
    return None
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
pytest tests/test_universal_planner.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit planner**

```bash
git add app/universal_planner.py tests/test_universal_planner.py
git commit -m "feat: add universal consumer planner"
```

---

### Task 4: Dependency and Permission Validator

**Files:**
- Create: `app/universal_validator.py`
- Test: `tests/test_universal_validator.py`

- [ ] **Step 1: Write validator tests**

Create `tests/test_universal_validator.py`:

```python
from app.universal_plan import UniversalPlan
from app.universal_validator import (
    DependencyStatus,
    UniversalValidationContext,
    UniversalValidator,
)


def _plan(action_kind: str = "send_reply") -> UniversalPlan:
    payload = {"text": "收到，我处理。"} if action_kind == "send_reply" else {"terminal": True}
    return UniversalPlan.model_validate(
        {
            "planner_version": "2026-07-20",
            "task_kind": "reply",
            "reason": "需要处理。",
            "dependencies": ["dws"],
            "actions": [
                {
                    "kind": action_kind,
                    "reason": "处理 trigger。",
                    "target": {"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
                    "payload": payload,
                }
            ],
            "audit": {"summary": "trigger 足够。", "documents": [], "confidence": 0.8},
        }
    )


def test_validator_blocks_dws_dependent_plan_when_dws_unavailable():
    validator = UniversalValidator()
    result = validator.validate(
        _plan(),
        UniversalValidationContext(
            conversation_id="cid-1",
            trigger_message_id="msg-1",
            dependency_status={"dws": DependencyStatus(False, "dws_authorization_required")},
            existing_terminal_attempt=False,
            existing_sent_reply=False,
            dry_run=False,
        ),
    )

    assert result.allowed is False
    assert result.block_reason == "dws_authorization_required"
    assert result.terminal is False


def test_validator_suppresses_duplicate_reply():
    validator = UniversalValidator()
    result = validator.validate(
        _plan(),
        UniversalValidationContext(
            conversation_id="cid-1",
            trigger_message_id="msg-1",
            dependency_status={"dws": DependencyStatus(True, "")},
            existing_terminal_attempt=True,
            existing_sent_reply=True,
            dry_run=False,
        ),
    )

    assert result.allowed is False
    assert result.block_reason == "duplicate_trigger_already_terminal"
    assert result.terminal is True


def test_validator_allows_reply_when_dependencies_ready_and_no_duplicate():
    validator = UniversalValidator()
    result = validator.validate(
        _plan(),
        UniversalValidationContext(
            conversation_id="cid-1",
            trigger_message_id="msg-1",
            dependency_status={"dws": DependencyStatus(True, "")},
            existing_terminal_attempt=False,
            existing_sent_reply=False,
            dry_run=False,
        ),
    )

    assert result.allowed is True
    assert result.actions[0].kind.value == "send_reply"
```

- [ ] **Step 2: Run validator tests and verify import failure**

Run:

```bash
pytest tests/test_universal_validator.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_validator'
```

- [ ] **Step 3: Add validator implementation**

Create `app/universal_validator.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from app.universal_plan import PlannedAction, PlannedActionKind, UniversalPlan


@dataclass(frozen=True)
class DependencyStatus:
    ready: bool
    reason: str = ""


@dataclass(frozen=True)
class UniversalValidationContext:
    conversation_id: str
    trigger_message_id: str
    dependency_status: dict[str, DependencyStatus]
    existing_terminal_attempt: bool
    existing_sent_reply: bool
    dry_run: bool


@dataclass(frozen=True)
class ValidatedUniversalPlan:
    allowed: bool
    actions: list[PlannedAction]
    block_reason: str
    terminal: bool


class UniversalValidator:
    def validate(
        self,
        plan: UniversalPlan,
        context: UniversalValidationContext,
    ) -> ValidatedUniversalPlan:
        for dependency in plan.dependencies:
            status = context.dependency_status.get(dependency.value)
            if status is not None and not status.ready:
                return ValidatedUniversalPlan(
                    allowed=False,
                    actions=[
                        PlannedAction(
                            kind=PlannedActionKind.BLOCKED,
                            reason=status.reason,
                            target={
                                "conversation_id": context.conversation_id,
                                "trigger_message_id": context.trigger_message_id,
                            },
                            payload={"blocker": status.reason, "terminal": False},
                        )
                    ],
                    block_reason=status.reason,
                    terminal=False,
                )
        if context.existing_terminal_attempt or context.existing_sent_reply:
            return ValidatedUniversalPlan(
                allowed=False,
                actions=[
                    PlannedAction(
                        kind=PlannedActionKind.NO_REPLY,
                        reason="duplicate_trigger_already_terminal",
                        target={
                            "conversation_id": context.conversation_id,
                            "trigger_message_id": context.trigger_message_id,
                        },
                        payload={},
                    )
                ],
                block_reason="duplicate_trigger_already_terminal",
                terminal=True,
            )
        if context.dry_run:
            return ValidatedUniversalPlan(
                allowed=False,
                actions=plan.actions,
                block_reason="dry_run",
                terminal=False,
            )
        return ValidatedUniversalPlan(
            allowed=True,
            actions=plan.actions,
            block_reason="",
            terminal=True,
        )
```

- [ ] **Step 4: Run validator tests**

Run:

```bash
pytest tests/test_universal_validator.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit validator**

```bash
git add app/universal_validator.py tests/test_universal_validator.py
git commit -m "feat: validate universal consumer plans"
```

---

### Task 5: Executor Adapter for Existing Worker Actions

**Files:**
- Create: `app/universal_executor.py`
- Modify: `app/worker.py` to expose narrow executor methods if needed
- Test: `tests/test_universal_executor.py`

- [ ] **Step 1: Write executor tests with a fake worker**

Create `tests/test_universal_executor.py`:

```python
from app.universal_executor import UniversalActionExecutor
from app.universal_plan import PlannedAction


class FakeWorker:
    def __init__(self):
        self.calls = []

    def execute_universal_send_reply(self, action):
        self.calls.append(("send_reply", action.payload["text"]))
        return True

    def execute_universal_no_reply(self, action):
        self.calls.append(("no_reply", action.reason))
        return True

    def execute_universal_oa_approval(self, action):
        self.calls.append(("oa_approval", action.payload["action"]))
        return True


def test_executor_dispatches_send_reply():
    worker = FakeWorker()
    executor = UniversalActionExecutor(worker)
    action = PlannedAction(
        kind="send_reply",
        reason="回复用户。",
        target={"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
        payload={"text": "收到。"},
    )

    assert executor.execute(action) is True
    assert worker.calls == [("send_reply", "收到。")]


def test_executor_dispatches_oa_approval():
    worker = FakeWorker()
    executor = UniversalActionExecutor(worker)
    action = PlannedAction(
        kind="oa_approval",
        reason="审批材料满足规则。",
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意。"},
    )

    assert executor.execute(action) is True
    assert worker.calls == [("oa_approval", "同意")]
```

- [ ] **Step 2: Run executor tests and verify import failure**

Run:

```bash
pytest tests/test_universal_executor.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_executor'
```

- [ ] **Step 3: Add executor adapter**

Create `app/universal_executor.py`:

```python
from __future__ import annotations

from app.universal_plan import PlannedAction, PlannedActionKind


class UniversalActionExecutor:
    def __init__(self, worker):
        self.worker = worker

    def execute(self, action: PlannedAction) -> bool:
        if action.kind is PlannedActionKind.SEND_REPLY:
            return self.worker.execute_universal_send_reply(action)
        if action.kind is PlannedActionKind.ASK_CLARIFYING_QUESTION:
            return self.worker.execute_universal_send_reply(action)
        if action.kind is PlannedActionKind.OA_APPROVAL:
            return self.worker.execute_universal_oa_approval(action)
        if action.kind is PlannedActionKind.MAIL_REPLY:
            return self.worker.execute_universal_mail_reply(action)
        if action.kind is PlannedActionKind.CALENDAR_RESPONSE:
            return self.worker.execute_universal_calendar_response(action)
        if action.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
            return self.worker.execute_universal_document_reply(action)
        if action.kind is PlannedActionKind.DWS_MESSAGE_REACTION:
            return self.worker.execute_universal_message_reaction(action)
        if action.kind is PlannedActionKind.MEMORY_WRITE:
            return self.worker.execute_universal_memory_write(action)
        if action.kind in {
            PlannedActionKind.NO_REPLY,
            PlannedActionKind.HANDOFF_TO_HUMAN,
            PlannedActionKind.BLOCKED,
            PlannedActionKind.STOP_WITH_ERROR,
        }:
            return self.worker.execute_universal_terminal_action(action)
        raise ValueError(f"unsupported universal action kind: {action.kind.value}")
```

- [ ] **Step 4: Add worker executor methods as thin wrappers**

Modify `app/worker.py` by adding these methods near the existing delivery helpers:

```python
    def execute_universal_send_reply(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_oa_approval(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_mail_reply(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_calendar_response(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_document_reply(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_message_reaction(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_memory_write(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")

    def execute_universal_terminal_action(self, action) -> bool:
        raise NotImplementedError("wire in Task 7 after orchestrator stores attempt context")
```

This deliberate temporary failure is acceptable inside the feature branch because Task 7 replaces every method body before enabling the orchestrator.

- [ ] **Step 5: Run executor tests**

Run:

```bash
pytest tests/test_universal_executor.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit executor adapter**

```bash
git add app/universal_executor.py tests/test_universal_executor.py app/worker.py
git commit -m "feat: add universal action executor adapter"
```

---

### Task 6: Consumer Orchestrator

**Files:**
- Create: `app/universal_consumer.py`
- Test: `tests/test_universal_consumer.py`

- [ ] **Step 1: Write orchestration tests**

Create `tests/test_universal_consumer.py`:

```python
from app.universal_consumer import UniversalConsumerOrchestrator
from app.universal_context import UniversalTaskContext
from app.universal_plan import UniversalPlan
from app.universal_validator import DependencyStatus


class FakePlanner:
    def __init__(self):
        self.called = False

    def plan(self, context, *, session_id):
        self.called = True
        return UniversalPlan.model_validate(
            {
                "planner_version": "2026-07-20",
                "task_kind": "reply",
                "reason": "需要回复。",
                "dependencies": ["dws"],
                "actions": [
                    {
                        "kind": "send_reply",
                        "reason": "回复 trigger。",
                        "target": {
                            "conversation_id": context.conversation_id,
                            "trigger_message_id": context.trigger_message_id,
                        },
                        "payload": {"text": "收到。"},
                    }
                ],
                "audit": {"summary": "trigger 足够。", "documents": [], "confidence": 0.8},
            }
        )


class FakeExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, action):
        self.actions.append(action)
        return True


def _context():
    return UniversalTaskContext(
        task_id=1,
        conversation_id="cid-1",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="回应一下",
        context_messages=[],
        required_dependencies=["dws"],
        force_new_decision=False,
        dry_run=False,
    )


def test_orchestrator_does_not_call_planner_when_blocking_dependency_is_down():
    planner = FakePlanner()
    executor = FakeExecutor()
    orchestrator = UniversalConsumerOrchestrator(
        planner=planner,
        validator_context_factory=lambda _context: {
            "dws": DependencyStatus(False, "dws_authorization_required")
        },
        existing_terminal_attempt=lambda _context: False,
        existing_sent_reply=lambda _context: False,
        session_id=lambda _context: None,
        executor=executor,
    )

    result = orchestrator.process(_context())

    assert planner.called is False
    assert result.completed is False
    assert result.reason == "dws_authorization_required"
    assert executor.actions == []


def test_orchestrator_executes_valid_plan():
    planner = FakePlanner()
    executor = FakeExecutor()
    orchestrator = UniversalConsumerOrchestrator(
        planner=planner,
        validator_context_factory=lambda _context: {
            "dws": DependencyStatus(True, "")
        },
        existing_terminal_attempt=lambda _context: False,
        existing_sent_reply=lambda _context: False,
        session_id=lambda _context: "sess-1",
        executor=executor,
    )

    result = orchestrator.process(_context())

    assert planner.called is True
    assert result.completed is True
    assert executor.actions[0].payload["text"] == "收到。"
```

- [ ] **Step 2: Run orchestrator tests and verify import failure**

Run:

```bash
pytest tests/test_universal_consumer.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'app.universal_consumer'
```

- [ ] **Step 3: Add orchestrator**

Create `app/universal_consumer.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.universal_context import UniversalTaskContext
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalAudit, UniversalPlan
from app.universal_validator import (
    DependencyStatus,
    UniversalValidationContext,
    UniversalValidator,
)


@dataclass(frozen=True)
class UniversalConsumerResult:
    completed: bool
    reason: str
    executed_actions: list[PlannedAction]


class UniversalConsumerOrchestrator:
    def __init__(
        self,
        *,
        planner,
        validator_context_factory: Callable[[UniversalTaskContext], dict[str, DependencyStatus]],
        existing_terminal_attempt: Callable[[UniversalTaskContext], bool],
        existing_sent_reply: Callable[[UniversalTaskContext], bool],
        session_id: Callable[[UniversalTaskContext], str | None],
        executor,
    ):
        self.planner = planner
        self.validator_context_factory = validator_context_factory
        self.existing_terminal_attempt = existing_terminal_attempt
        self.existing_sent_reply = existing_sent_reply
        self.session_id = session_id
        self.executor = executor
        self.validator = UniversalValidator()

    def process(self, context: UniversalTaskContext) -> UniversalConsumerResult:
        dependency_status = self.validator_context_factory(context)
        for name, status in dependency_status.items():
            if name in context.required_dependencies and not status.ready:
                return UniversalConsumerResult(
                    completed=False,
                    reason=status.reason,
                    executed_actions=[],
                )

        plan = self.planner.plan(context, session_id=self.session_id(context))
        validated = self.validator.validate(
            plan,
            UniversalValidationContext(
                conversation_id=context.conversation_id,
                trigger_message_id=context.trigger_message_id,
                dependency_status=dependency_status,
                existing_terminal_attempt=self.existing_terminal_attempt(context),
                existing_sent_reply=self.existing_sent_reply(context),
                dry_run=context.dry_run,
            ),
        )
        if not validated.allowed:
            return UniversalConsumerResult(
                completed=validated.terminal,
                reason=validated.block_reason,
                executed_actions=[],
            )

        executed: list[PlannedAction] = []
        for action in validated.actions:
            if self.executor.execute(action):
                executed.append(action)
        return UniversalConsumerResult(
            completed=True,
            reason=plan.reason,
            executed_actions=executed,
        )


def blocking_dependency_plan(
    *,
    context: UniversalTaskContext,
    dependency_name: str,
    reason: str,
) -> UniversalPlan:
    return UniversalPlan(
        planner_version="2026-07-20",
        task_kind="blocked",
        reason=reason,
        dependencies=[dependency_name],
        actions=[
            PlannedAction(
                kind=PlannedActionKind.BLOCKED,
                reason=reason,
                target={
                    "conversation_id": context.conversation_id,
                    "trigger_message_id": context.trigger_message_id,
                },
                payload={"blocker": reason, "terminal": False},
            )
        ],
        audit=UniversalAudit(summary=reason, documents=[], confidence=1.0),
    )
```

- [ ] **Step 4: Run orchestrator tests**

Run:

```bash
pytest tests/test_universal_consumer.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit orchestrator**

```bash
git add app/universal_consumer.py tests/test_universal_consumer.py
git commit -m "feat: orchestrate universal consumer plans"
```

---

### Task 7: Wire Worker with Deterministic Guards

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Add worker tests for DWS blocking before Codex**

Append to `tests/test_worker.py` near existing authorization tests:

```python
def test_universal_consumer_blocks_before_codex_when_dws_not_ready(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    dws.auth_status_ready = False
    store = worker.store
    task_id = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="星尘大家庭",
        trigger_message_id="msg-1",
        trigger_message_json=make_message_json("msg-1", "看一下今天的讨论"),
    )

    processed = worker.consume_once(max_tasks=1)

    assert processed == 0
    assert codex.decisions == []
    task = store.get_reply_task(task_id)
    assert task.status == "pending"
    assert "DWS auth status is not ready" in task.error
    assert any(
        item["title"] == "CEO task waiting for authorization: 星尘大家庭"
        for item in notifications
    )
```

Use the repository's existing test fixtures instead of adding duplicate fake classes. If fixture names differ, adapt only the fixture references and keep the assertions unchanged.

- [ ] **Step 2: Add worker test for universal reply execution**

Append:

```python
def test_universal_consumer_records_and_sends_reply(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    codex.decisions.append(
        {
            "planner_version": "2026-07-20",
            "task_kind": "reply",
            "reason": "需要回复。",
            "dependencies": ["dws"],
            "actions": [
                {
                    "kind": "send_reply",
                    "reason": "回应群里明确诉求。",
                    "target": {"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
                    "payload": {"text": "收到，我会整理几个挑战话题给你。"},
                }
            ],
            "audit": {"summary": "trigger 本体足够。", "documents": [], "confidence": 0.8},
        }
    )
    task_id = worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="HR",
        trigger_message_id="msg-1",
        trigger_message_json=make_message_json("msg-1", "找几个挑战的话题和分身讨论"),
    )

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert worker.store.get_reply_task(task_id).status == "done"
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].action == "send_reply"
    assert attempts[0].send_status == "sent"
    assert "挑战话题" in attempts[0].final_reply_text
```

- [ ] **Step 3: Run focused worker tests and verify failures**

Run:

```bash
pytest tests/test_worker.py -k "universal_consumer" -q
```

Expected:

```text
FAILED
```

The failures should identify missing worker integration, fixture adaptation, or executor method bodies.

- [ ] **Step 4: Add worker factory methods**

Modify `app/worker.py` imports:

```python
from app.universal_consumer import UniversalConsumerOrchestrator
from app.universal_context import build_universal_context
from app.universal_executor import UniversalActionExecutor
from app.universal_planner import UniversalPlanner
from app.universal_validator import DependencyStatus
```

Add these methods to `DingTalkWorker`:

```python
    def _universal_consumer(self) -> UniversalConsumerOrchestrator:
        return UniversalConsumerOrchestrator(
            planner=UniversalPlanner(
                workspace=self.workspace,
                codex_bin=getattr(self.codex.runner, "codex_bin", "codex"),
                timeout_seconds=getattr(self.codex, "timeout_seconds", 1200),
                idle_timeout_seconds=getattr(self.codex, "idle_timeout_seconds", 900),
            ),
            validator_context_factory=self._universal_dependency_status,
            existing_terminal_attempt=self._universal_existing_terminal_attempt,
            existing_sent_reply=self._universal_existing_sent_reply,
            session_id=self._universal_session_id,
            executor=UniversalActionExecutor(self),
        )

    def _universal_dependency_status(self, context) -> dict[str, DependencyStatus]:
        try:
            self._ensure_dws_ready_for_codex()
        except Exception as exc:
            reason = _normalize_codex_stop_error_reason(str(exc))
            return {"dws": DependencyStatus(False, reason)}
        return {"dws": DependencyStatus(True, "")}

    def _universal_existing_terminal_attempt(self, context) -> bool:
        attempt = self.store.get_latest_reply_attempt_for_trigger(
            context.conversation_id,
            context.trigger_message_id,
        )
        if attempt is None:
            return False
        return attempt.send_status in {"sent", "skipped", "blocked", "failed", "commented"}

    def _universal_existing_sent_reply(self, context) -> bool:
        return self.store.sent_reply_exists(
            conversation_id=context.conversation_id,
            trigger_message_id=context.trigger_message_id,
        )

    def _universal_session_id(self, context) -> str | None:
        return self.store.get_codex_session_id(context.conversation_id)
```

If `sent_reply_exists` is absent, add it to `app/store.py` in Task 8 before finalizing this task.

- [ ] **Step 5: Replace `_process_queued_task` direct routing with universal path**

Modify `app/worker.py::_process_queued_task` after the stale/backoff checks:

```python
        context = build_universal_context(
            conversation=conversation,
            trigger=trigger,
            context_messages=prompt_context_messages,
            task_id=task.id,
            force_new_decision=task.force_new_decision,
            dry_run=self.dry_run,
        )
        result = self._universal_consumer().process(context)
        if not result.completed:
            raise CodexAuthorizationRequiredError(result.reason)
        self._mark_seen([trigger])
        return True
```

Remove the direct calls to `_handle_minutes_permission_request_if_actionable`, `_handle_calendar_invite_if_actionable`, `_handle_oa_approval_if_actionable`, `_record_system_or_notification_skip`, and `_process_batch` from `_process_queued_task` only after Task 9 parity tests pass. Until then, place this block behind an environment flag:

```python
        if os.getenv("CEO_UNIVERSAL_CONSUMER", "0") == "1":
            context = build_universal_context(
                conversation=conversation,
                trigger=trigger,
                context_messages=prompt_context_messages,
                task_id=task.id,
                force_new_decision=task.force_new_decision,
                dry_run=self.dry_run,
            )
            result = self._universal_consumer().process(context)
            if not result.completed:
                raise CodexAuthorizationRequiredError(result.reason)
            self._mark_seen([trigger])
            return True
```

- [ ] **Step 6: Run focused worker tests with flag enabled**

Run:

```bash
CEO_UNIVERSAL_CONSUMER=1 pytest tests/test_worker.py -k "universal_consumer" -q
```

Expected:

```text
2 passed
```

- [ ] **Step 7: Commit worker wiring**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: wire universal consumer behind flag"
```

---

### Task 8: Store Accessors and Attempt Recording

**Files:**
- Modify: `app/store.py`
- Modify: `app/worker.py`
- Test: `tests/test_store.py`, `tests/test_worker.py`

- [ ] **Step 1: Write store test for sent reply dedupe accessor**

Append to `tests/test_store.py`:

```python
def test_sent_reply_exists_matches_conversation_and_trigger(tmp_path):
    store = Store(tmp_path / "auto-reply.sqlite3")
    store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已发送。",
        sent_at="2026-07-20T09:00:00Z",
    )

    assert store.sent_reply_exists(conversation_id="cid-1", trigger_message_id="msg-1")
    assert not store.sent_reply_exists(conversation_id="cid-1", trigger_message_id="msg-2")
```

- [ ] **Step 2: Run store test and verify missing accessor**

Run:

```bash
pytest tests/test_store.py -k "sent_reply_exists" -q
```

Expected:

```text
AttributeError: 'Store' object has no attribute 'sent_reply_exists'
```

- [ ] **Step 3: Add store accessor**

Modify `app/store.py`:

```python
    def sent_reply_exists(self, *, conversation_id: str, trigger_message_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sent_replies
                WHERE conversation_id = ?
                  AND trigger_message_id = ?
                LIMIT 1
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
        return row is not None
```

- [ ] **Step 4: Add universal terminal attempt recorder helper**

Modify `app/worker.py` with:

```python
    def _record_universal_terminal_attempt(self, action, *, send_status: str, send_error: str = "") -> int:
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=str(action.target.get("conversation_id") or ""),
            conversation_title=str(action.target.get("conversation_title") or ""),
            trigger_message_id=str(action.target.get("trigger_message_id") or ""),
            trigger_sender=str(action.target.get("trigger_sender") or ""),
            trigger_text=str(action.target.get("trigger_text") or ""),
            action=action.kind.value,
            sensitivity_kind="general",
            codex_reason=action.reason,
            draft_reply_text=str(action.payload.get("text") or ""),
            audit_summary=action.reason,
            send_status=send_status,
        )
        self.store.update_reply_attempt(attempt_id, send_error=send_error)
        return attempt_id
```

Task 9 refines this helper to pass the current trigger/conversation explicitly instead of relying only on action targets.

- [ ] **Step 5: Run store and focused worker tests**

Run:

```bash
pytest tests/test_store.py -k "sent_reply_exists" -q
CEO_UNIVERSAL_CONSUMER=1 pytest tests/test_worker.py -k "universal_consumer" -q
```

Expected:

```text
1 passed
2 passed
```

- [ ] **Step 6: Commit store support**

```bash
git add app/store.py app/worker.py tests/test_store.py
git commit -m "feat: support universal consumer dedupe state"
```

---

### Task 9: Capability Executors for Reply, Terminal, OA, Mail, Calendar, Documents, Reactions, Memory

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Add reply executor test using real attempt persistence**

Append:

```python
def test_universal_send_reply_executor_persists_sent_attempt(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    action = PlannedAction(
        kind="send_reply",
        reason="回复用户。",
        target={
            "conversation_id": "cid-1",
            "conversation_title": "HR",
            "trigger_message_id": "msg-1",
            "trigger_sender": "Mina",
            "trigger_text": "回应一下",
        },
        payload={"text": "收到，我来处理。"},
    )

    assert worker.execute_universal_send_reply(action) is True
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.action == "send_reply"
    assert attempt.send_status == "sent"
    assert attempt.final_reply_text == "收到，我来处理。"
```

- [ ] **Step 2: Add terminal executor test**

Append:

```python
def test_universal_terminal_executor_persists_blocked_attempt(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    action = PlannedAction(
        kind="blocked",
        reason="missing_oa_approval_target",
        target={
            "conversation_id": "cid-1",
            "conversation_title": "审批群",
            "trigger_message_id": "msg-1",
            "trigger_sender": "系统",
            "trigger_text": "审批提醒",
        },
        payload={"blocker": "missing_oa_approval_target", "terminal": True},
    )

    assert worker.execute_universal_terminal_action(action) is True
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.action == "blocked"
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "missing_oa_approval_target"
```

- [ ] **Step 3: Implement reply and terminal executors**

Modify `app/worker.py`:

```python
    def execute_universal_send_reply(self, action) -> bool:
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=str(action.target.get("conversation_id") or ""),
            conversation_title=str(action.target.get("conversation_title") or ""),
            trigger_message_id=str(action.target.get("trigger_message_id") or ""),
            trigger_sender=str(action.target.get("trigger_sender") or ""),
            trigger_text=str(action.target.get("trigger_text") or ""),
            action=action.kind.value,
            sensitivity_kind="general",
            codex_reason=action.reason,
            draft_reply_text=str(action.payload.get("text") or ""),
            audit_summary=action.reason,
            send_status="dry_run",
        )
        conversation = DingTalkConversation(
            open_conversation_id=str(action.target.get("conversation_id") or ""),
            title=str(action.target.get("conversation_title") or ""),
            single_chat=bool(action.target.get("single_chat") or False),
            unread_point=1,
        )
        trigger = DingTalkMessage(
            sender_name=str(action.target.get("trigger_sender") or ""),
            sender_staff_id="",
            open_message_id=str(action.target.get("trigger_message_id") or ""),
            content=str(action.target.get("trigger_text") or ""),
            create_time=self._now(),
        )
        self._send_reply(
            conversation=conversation,
            trigger=trigger,
            new_messages=[trigger],
            reply_text=str(action.payload.get("text") or ""),
            reason=action.reason,
            attempt_id=attempt_id,
            system_actions=[],
            raise_on_delivery_failure=True,
        )
        return True

    def execute_universal_terminal_action(self, action) -> bool:
        send_status = {
            "no_reply": "skipped",
            "handoff_to_human": "skipped",
            "blocked": "blocked",
            "stop_with_error": "failed",
        }.get(action.kind.value, "skipped")
        send_error = str(action.payload.get("blocker") or action.reason)
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=str(action.target.get("conversation_id") or ""),
            conversation_title=str(action.target.get("conversation_title") or ""),
            trigger_message_id=str(action.target.get("trigger_message_id") or ""),
            trigger_sender=str(action.target.get("trigger_sender") or ""),
            trigger_text=str(action.target.get("trigger_text") or ""),
            action=action.kind.value,
            sensitivity_kind="general",
            codex_reason=action.reason,
            draft_reply_text="",
            audit_summary=action.reason,
            send_status=send_status,
        )
        self.store.update_reply_attempt(attempt_id, send_error=send_error)
        return True
```

- [ ] **Step 4: Implement OA executor by reusing existing OA handler target checks**

Modify `app/worker.py`:

```python
    def execute_universal_oa_approval(self, action) -> bool:
        process_instance_id = str(action.target.get("process_instance_id") or "")
        task_id = str(action.target.get("task_id") or "")
        oa_url = str(action.target.get("oa_url") or "")
        oa_action = str(action.payload.get("action") or "")
        remark = str(action.payload.get("remark") or "")
        if not process_instance_id or not task_id:
            self._record_universal_terminal_attempt(
                action,
                send_status="skipped",
                send_error="missing_oa_approval_target",
            )
            return True
        target_status = self._oa_target_status_for_current_user("", task_id)
        if target_status is False:
            self._record_universal_terminal_attempt(
                action,
                send_status="skipped",
                send_error="oa_task_not_current_user",
            )
            return True
        if oa_action == "comment":
            result = self.dws.comment_oa_approval(process_instance_id, remark)
            status = "commented"
        else:
            result = self.dws.execute_oa_approval_action(
                process_instance_id,
                task_id,
                oa_action,
                remark,
            )
            status = "skipped"
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=str(action.target.get("conversation_id") or ""),
            conversation_title=str(action.target.get("conversation_title") or ""),
            trigger_message_id=str(action.target.get("trigger_message_id") or ""),
            trigger_sender=str(action.target.get("trigger_sender") or ""),
            trigger_text=str(action.target.get("trigger_text") or ""),
            action="oa_approval",
            sensitivity_kind="internal_personnel",
            codex_reason=oa_action,
            draft_reply_text=remark,
            audit_summary=action.reason,
            oa_process_instance_id=process_instance_id,
            oa_task_id=task_id,
            oa_url=oa_url,
            oa_action=oa_action,
            oa_remark=remark,
            oa_action_result_json=json.dumps(result, ensure_ascii=False),
            send_status=status,
        )
        self.store.update_reply_attempt(attempt_id, final_reply_text=remark)
        return True
```

- [ ] **Step 5: Implement mail executor by reusing existing command builder**

Modify `app/worker.py`:

```python
    def execute_universal_mail_reply(self, action) -> bool:
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=str(action.target.get("conversation_id") or ""),
            conversation_title=str(action.target.get("conversation_title") or ""),
            trigger_message_id=str(action.target.get("trigger_message_id") or ""),
            trigger_sender=str(action.target.get("trigger_sender") or ""),
            trigger_text=str(action.target.get("trigger_text") or ""),
            action="send_reply",
            sensitivity_kind="general",
            codex_reason=action.reason,
            draft_reply_text="",
            audit_summary=action.reason,
            mail_mailbox=str(action.target.get("mailbox") or ""),
            mail_message_id=str(action.target.get("message_id") or ""),
            mail_subject=str(action.target.get("subject") or ""),
            mail_reply_text=str(action.payload.get("content") or ""),
            send_status="dry_run",
        )
        return self._execute_mail_reply_if_needed(attempt_id)
```

- [ ] **Step 6: Implement calendar, document, reaction, and memory executors**

Modify `app/worker.py` with direct calls to existing service-owned helpers:

```python
    def execute_universal_calendar_response(self, action) -> bool:
        event_id = str(action.target.get("event_id") or "")
        response_status = str(action.payload.get("response_status") or "")
        if not event_id or response_status not in {"accepted", "tentative", "declined"}:
            self._record_universal_terminal_attempt(
                action,
                send_status="failed",
                send_error="invalid_calendar_response_target",
            )
            return True
        result = self.dws.respond_calendar_invite(event_id, response_status)
        attempt_id = self._record_universal_terminal_attempt(action, send_status="skipped")
        self.store.update_reply_attempt(
            attempt_id,
            send_error="",
            audit_summary=json.dumps(result, ensure_ascii=False),
        )
        return True

    def execute_universal_document_reply(self, action) -> bool:
        node_id = str(action.target.get("node_id") or "")
        comment_id = str(action.target.get("comment_id") or "")
        text = str(action.payload.get("text") or "")
        result = self.dws.create_doc_comment(node_id=node_id, comment_id=comment_id, text=text)
        attempt_id = self._record_universal_terminal_attempt(action, send_status="commented")
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=text,
            audit_summary=json.dumps(result, ensure_ascii=False),
        )
        return True

    def execute_universal_message_reaction(self, action) -> bool:
        emoji = str(action.payload.get("emoji") or "")
        if emoji.startswith("[") and emoji.endswith("]"):
            emoji = emoji[1:-1]
        result = self.dws.create_message_reaction(
            str(action.target.get("conversation_id") or ""),
            str(action.target.get("message_id") or ""),
            emoji,
        )
        attempt_id = self._record_universal_terminal_attempt(action, send_status="skipped")
        self.store.update_reply_attempt(attempt_id, audit_summary=json.dumps(result, ensure_ascii=False))
        return True

    def execute_universal_memory_write(self, action) -> bool:
        result = self.memory_client.write(str(action.payload.get("text") or ""))
        attempt_id = self._record_universal_terminal_attempt(action, send_status="skipped")
        self.store.update_reply_attempt(attempt_id, audit_summary=json.dumps(result, ensure_ascii=False))
        return True
```

If any DWS method name differs, use `rg -n "def .*comment|def .*reaction|def .*calendar|def .*memory" app` and bind to the existing method while keeping the tests' behavior unchanged.

- [ ] **Step 7: Run capability tests**

Run:

```bash
CEO_UNIVERSAL_CONSUMER=1 pytest tests/test_worker.py -k "universal_send_reply_executor or universal_terminal_executor or universal_consumer" -q
```

Expected:

```text
4 passed
```

- [ ] **Step 8: Commit executors**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: execute universal consumer actions"
```

---

### Task 10: Parity Tests for High-Risk Existing Behaviors

**Files:**
- Modify: `tests/test_worker.py`
- Modify: `tests/test_oa_approval.py` if OA fixtures require shared setup

- [ ] **Step 1: Add OA follow-up parity test**

Append:

```python
def test_universal_consumer_handles_oa_follow_up_from_context_url(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    codex.decisions.append(
        {
            "planner_version": "2026-07-20",
            "task_kind": "oa",
            "reason": "上下文审批链接和当前追问属于同一审批。",
            "dependencies": ["dws"],
            "actions": [
                {
                    "kind": "oa_approval",
                    "reason": "Derek 是当前审批节点且材料满足同意规则。",
                    "target": {
                        "conversation_id": "cid-1",
                        "conversation_title": "审批群",
                        "trigger_message_id": "msg-follow-up",
                        "trigger_sender": "贾金鹏",
                        "trigger_text": "这个审批看一下",
                        "process_instance_id": "proc-1",
                        "task_id": "task-1",
                        "oa_url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInsId=proc-1&taskId=task-1",
                    },
                    "payload": {"action": "同意", "remark": "同意，按当前方案推进。"},
                }
            ],
            "audit": {"summary": "已读取审批材料并确认归属。", "documents": [], "confidence": 0.9},
        }
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="审批群",
        trigger_message_id="msg-follow-up",
        trigger_message_json=make_message_json("msg-follow-up", "这个审批看一下"),
    )

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.action == "oa_approval"
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
```

- [ ] **Step 2: Add mail parity test**

Append:

```python
def test_universal_consumer_executes_mail_reply_plan(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    codex.decisions.append(
        {
            "planner_version": "2026-07-20",
            "task_kind": "mail",
            "reason": "审批对象是邮件，需要邮件回复。",
            "dependencies": ["dws"],
            "actions": [
                {
                    "kind": "mail_reply",
                    "reason": "需要回复原邮件。",
                    "target": {
                        "conversation_id": "cid-1",
                        "conversation_title": "工作通知",
                        "trigger_message_id": "msg-1",
                        "trigger_sender": "系统",
                        "trigger_text": "邮件审批",
                        "mailbox": "derek@example.com",
                        "message_id": "mail-1",
                        "subject": "PRD review",
                    },
                    "payload": {"content": "收到，我同意这个处理。"},
                }
            ],
            "audit": {"summary": "任务目标是邮件。", "documents": [], "confidence": 0.83},
        }
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="工作通知",
        trigger_message_id="msg-1",
        trigger_message_json=make_message_json("msg-1", "邮件审批"),
    )

    assert worker.consume_once(max_tasks=1) == 1
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.mail_message_id == "mail-1"
    assert attempt.mail_reply_text == "收到，我同意这个处理。"
```

- [ ] **Step 3: Add reaction parity test**

Append:

```python
def test_universal_reaction_executor_strips_square_brackets(tmp_path):
    worker, dws, codex, notifications = make_worker(tmp_path)
    action = PlannedAction(
        kind="dws_message_reaction",
        reason="用户只需要 emoji reaction。",
        target={
            "conversation_id": "cid-1",
            "conversation_title": "Q3战略群",
            "trigger_message_id": "msg-1",
            "message_id": "msg-1",
        },
        payload={"emoji": "[👍]"},
    )

    assert worker.execute_universal_message_reaction(action) is True
    assert dws.reactions[-1]["emoji"] == "👍"
```

- [ ] **Step 4: Run parity tests**

Run:

```bash
CEO_UNIVERSAL_CONSUMER=1 pytest tests/test_worker.py -k "universal_consumer_handles_oa_follow_up or universal_consumer_executes_mail_reply_plan or universal_reaction_executor" -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit parity coverage**

```bash
git add tests/test_worker.py app/worker.py
git commit -m "test: cover universal consumer parity paths"
```

---

### Task 11: Remove Old Direct Routing from Queued Tasks

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Enable universal consumer by default for queued tasks**

Modify `app/worker.py::_process_queued_task` to make universal processing the default path:

```python
        context = build_universal_context(
            conversation=conversation,
            trigger=trigger,
            context_messages=prompt_context_messages,
            task_id=task.id,
            force_new_decision=task.force_new_decision,
            dry_run=self.dry_run,
        )
        result = self._universal_consumer().process(context)
        if not result.completed:
            raise CodexAuthorizationRequiredError(result.reason)
        self._mark_seen([trigger])
        return True
```

Keep old `_handle_*_if_actionable` methods in the file because scan-time flows and executor adapters still use their helpers.

- [ ] **Step 2: Remove the environment flag branch**

Delete checks for:

```python
os.getenv("CEO_UNIVERSAL_CONSUMER", "0") == "1"
```

from the queued-task consumer path. Do not remove unrelated environment flags.

- [ ] **Step 3: Run old and new focused worker tests without the flag**

Run:

```bash
pytest tests/test_worker.py -k "authorization_failure_waits or codex_provider_auth_failure or universal_consumer or oa_follow_up or mail_reply or calendar" -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Run parser and planner tests**

Run:

```bash
pytest tests/test_universal_plan.py tests/test_universal_context.py tests/test_universal_planner.py tests/test_universal_validator.py tests/test_universal_consumer.py tests/test_universal_executor.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit default routing**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "refactor: route queued tasks through universal consumer"
```

---

### Task 12: History and Audit Visibility

**Files:**
- Modify: `app/audit_web.py`
- Modify: `app/history.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write audit web test for planner metadata**

Append to `tests/test_audit_web.py`:

```python
def test_attempt_detail_shows_universal_planner_status(tmp_path):
    store = Store(tmp_path / "auto-reply.sqlite3")
    attempt_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="HR",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="回应一下",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="需要回复。",
        draft_reply_text="收到。",
        audit_summary="universal_consumer: send_reply validated and executed",
        audit_tool_events_json='[{"tool":"universal_planner","status":"validated","capability":"send_reply"}]',
        send_status="sent",
    )

    response = make_test_client(store).get(f"/attempts/{attempt_id}")

    assert response.status_code == 200
    assert "universal_planner" in response.text
    assert "send_reply" in response.text
    assert "validated" in response.text
```

- [ ] **Step 2: Run audit test and capture current failure**

Run:

```bash
pytest tests/test_audit_web.py -k "universal_planner_status" -q
```

Expected:

```text
FAILED
```

- [ ] **Step 3: Add rendering for universal planner audit event**

Modify `app/audit_web.py` where audit tool events are rendered:

```python
        if event.get("tool") == "universal_planner":
            rows.append(
                {
                    "label": "Universal planner",
                    "status": str(event.get("status") or ""),
                    "detail": str(event.get("capability") or ""),
                }
            )
            continue
```

Modify `app/history.py` attempt serialization:

```python
        if event.get("tool") == "universal_planner":
            item["planner"] = {
                "status": str(event.get("status") or ""),
                "capability": str(event.get("capability") or ""),
            }
```

- [ ] **Step 4: Run audit test**

Run:

```bash
pytest tests/test_audit_web.py -k "universal_planner_status" -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit audit visibility**

```bash
git add app/audit_web.py app/history.py tests/test_audit_web.py
git commit -m "feat: expose universal planner audit state"
```

---

### Task 13: Prompt Contract Update

**Files:**
- Modify: `app/defaults/developer_prompt.md`
- Test: `tests/test_prompt.py`

- [ ] **Step 1: Add prompt contract test**

Append to `tests/test_prompt.py`:

```python
def test_developer_prompt_defines_universal_consumer_dependency_policy():
    prompt = Path("app/defaults/developer_prompt.md").read_text(encoding="utf-8")

    assert "UniversalPlan" in prompt
    assert "DWS 是 DingTalk 任务的阻断性依赖" in prompt
    assert "不要在 agent 内执行 dws auth login" in prompt
```

- [ ] **Step 2: Run prompt test and verify failure**

Run:

```bash
pytest tests/test_prompt.py -k "universal_consumer_dependency_policy" -q
```

Expected:

```text
FAILED
```

- [ ] **Step 3: Add prompt section**

Append to `app/defaults/developer_prompt.md`:

```markdown
## Universal Consumer Planning Contract

When the service asks for a UniversalPlan, classify the current CEO task by intent and return only the requested JSON schema. You may propose `send_reply`, `ask_clarifying_question`, `oa_approval`, `mail_reply`, `calendar_response`, `dws_markdown_document_reply`, `dws_message_reaction`, `memory_write`, `no_reply`, `handoff_to_human`, `blocked`, or `stop_with_error`.

DWS 是 DingTalk 任务的阻断性依赖。If the service reports DWS is unavailable, return `blocked` with the blocking reason and do not continue content processing.

不要在 agent 内执行 dws auth login. Service code performs DWS status checks and starts authorization flows before calling the planner.

The planner proposes actions only. The service validates current-user ownership, target IDs, duplicate sends, current authorization state, and final side effects.
```

- [ ] **Step 4: Run prompt test**

Run:

```bash
pytest tests/test_prompt.py -k "universal_consumer_dependency_policy" -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit prompt**

```bash
git add app/defaults/developer_prompt.md tests/test_prompt.py
git commit -m "docs: define universal consumer prompt contract"
```

---

### Task 14: Full Test, Live Smoke, Service Restart

**Files:**
- Modify only if tests expose concrete failures

- [ ] **Step 1: Run focused suite**

Run:

```bash
pytest tests/test_universal_plan.py tests/test_universal_context.py tests/test_universal_planner.py tests/test_universal_validator.py tests/test_universal_consumer.py tests/test_universal_executor.py tests/test_worker.py -k "universal or authorization or oa_follow_up or mail_reply or calendar" -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run broader regression suite**

Run:

```bash
pytest tests/test_worker.py tests/test_store.py tests/test_codex_decision.py tests/test_codex_runner.py tests/test_prompt.py tests/test_audit_web.py -q
```

Expected:

```text
passed
```

- [ ] **Step 3: Run read-only DWS status smoke**

Run:

```bash
dws auth status --json
```

Expected:

```text
JSON output with a ready authenticated status for the service profile.
```

If this returns an authorization-required error, do not run live message execution. Start the service-owned authorization flow and leave affected tasks deferred for authorization.

- [ ] **Step 4: Restart launchd service**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Expected:

```text
command exits 0
```

- [ ] **Step 5: Verify service process**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected:

```text
state = running
```

- [ ] **Step 6: Run one bounded failed-task recovery pass**

Run:

```bash
python -m app.cli process-reply-tasks --max-tasks 3
```

Expected:

```text
No new authorization loop, no `dws auth login` launched from inside Codex, and terminal attempts recorded for processed tasks.
```

- [ ] **Step 7: Commit verification fixes**

If Step 1 or Step 2 required code changes:

```bash
git add app tests
git commit -m "fix: stabilize universal consumer rollout"
```

If Step 1 and Step 2 passed without edits, skip this commit.

---

### Task 15: Documentation and Operator Runbook

**Files:**
- Create: `docs/universal-consumer-agent.md`

- [ ] **Step 1: Create operator docs**

Create `docs/universal-consumer-agent.md`:

```markdown
# Universal Consumer Agent

The CEO service processes queued `reply_tasks` through a universal consumer pipeline:

1. `consume_once` claims a task and validates stale/backoff state.
2. `UniversalTaskContext` renders trigger and recent context for the planner.
3. The service checks DWS readiness before Codex runs.
4. `UniversalPlanner` asks native `codex exec` for a `UniversalPlan`.
5. `UniversalValidator` checks dependency readiness, duplicate final states, dry-run state, and action safety.
6. `UniversalActionExecutor` dispatches validated actions to service-owned helpers.
7. Existing SQLite task and attempt state remains the source of truth.

## Dependency Policy

DWS is blocking for DingTalk tasks. If DWS auth is missing or a PAT scope is required, the task is deferred for authorization and Codex is not started.

The agent must not run `dws auth login`. The service starts auth flows and suppresses repeated prompts using service state.

## Safety Policy

The planner may propose actions, but the service decides if they can execute:

- reply dedupe by `conversation_id + trigger_message_id`
- OA ownership and task target validation before action
- mail target validation before reply
- calendar event target validation before response
- reaction emoji normalization before send
- memory writes only through configured memory client

## Recovery

Use the History attempt detail page to inspect:

- selected universal capability
- dependency blockers
- Codex session range
- audit documents
- final send status

Use `python -m app.cli process-reply-tasks --max-tasks N` for bounded replay after code fixes.
```

- [ ] **Step 2: Commit docs**

```bash
git add docs/universal-consumer-agent.md
git commit -m "docs: document universal consumer operations"
```

---

### Task 16: Final Push and Backlog Check

**Files:**
- No code changes expected

- [ ] **Step 1: Inspect git status**

Run:

```bash
git status --short
```

Expected:

```text
No uncommitted files from the universal consumer work remain.
```

If unrelated local files exist, leave them untouched and mention them in the final report.

- [ ] **Step 2: Push branch**

Run:

```bash
git push
```

Expected:

```text
push succeeds
```

- [ ] **Step 3: Query unresolved backlog**

Run:

```bash
sqlite3 data/auto-reply.sqlite3 "
SELECT 'reply_tasks', status, COUNT(*) FROM reply_tasks
WHERE status IN ('failed','processing')
GROUP BY status
UNION ALL
SELECT 'work_summary_inputs', status, COUNT(*) FROM work_summary_inputs
WHERE status IN ('failed','processing')
GROUP BY status;
"
```

Expected:

```text
No rows, or rows explained by current authorization/material blockers.
```

- [ ] **Step 4: Query recent failed attempts**

Run:

```bash
sqlite3 data/auto-reply.sqlite3 "
SELECT id, conversation_title, action, send_status, send_error
FROM reply_attempts
WHERE send_status IN ('failed','blocked')
  AND datetime(updated_at) >= datetime('now','-24 hours')
ORDER BY id DESC
LIMIT 20;
"
```

Expected:

```text
No unresolved rows, or rows with final unrecoverable reasons such as missing target, not Derek owner, external authorization required, or current rules forbid execution.
```

- [ ] **Step 5: Final report**

Report:

```text
Implemented universal consumer agent.
Code changed: schema, context, planner, validator, executor, worker wiring, audit visibility, prompt contract, docs.
Tests run: focused universal suite, worker/store/codex/prompt/audit regression suite.
Service status: launchd running after restart.
Backlog: include exact unresolved counts and reasons.
Push: include branch and commit range.
```

---

## Acceptance Criteria

- No queued task starts Codex when DWS is not ready for DingTalk context.
- No task relies on Codex to run `dws auth login`.
- One orchestrator owns queued-task intent selection.
- Existing reply/OA/mail/calendar/document/reaction/memory behavior is reachable through universal action executors.
- OA actions still require current-user ownership and complete target identifiers.
- Duplicate sends are suppressed by trigger-level checks.
- Failed, blocked, skipped, sent, commented, and dry-run outcomes remain visible in `reply_attempts` and History.
- Focused and regression tests pass.
- Runtime code is committed, pushed, service restarted, and launchd state verified.

## Self-Review

- Spec coverage: The plan covers replacing scattered routing, keeping DWS as blocking, preventing agent-driven auth login, preserving deterministic action guards, surfacing history/audit state, running tests, restarting service, and checking backlog.
- Banned wording scan: run a local `rg` check for the disallowed planning phrases from the writing-plans skill and remove any match.
- Type consistency: `UniversalPlan`, `PlannedAction`, `DependencyStatus`, `UniversalValidationContext`, `ValidatedUniversalPlan`, `UniversalConsumerOrchestrator`, and `UniversalActionExecutor` are introduced before use in later tasks.
