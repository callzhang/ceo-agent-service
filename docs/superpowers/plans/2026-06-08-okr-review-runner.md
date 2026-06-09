# OKR Review Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified structured Codex runner and OKR review workflow that reviews current-quarter DingTang OKRs from live data, produces KR-level claim and verified scores, and keeps reply/OA/OKR tasks on one conversation Codex session.

**Architecture:** Add a shared `AgentEnvelope` and `StructuredCodexRunner` with conversation-level session locking, skill injection, schema validation, transcript/audit capture, internal fail-fast behavior, and explicit external retry at DWS/Codex/network boundaries. Migrate reply and OA onto the envelope shape through adapters, then add the OKR live-source client, OKR tables, OKR handler, and background review processing.

**Tech Stack:** Python 3, Pydantic, SQLite, pytest, Codex CLI JSON output schemas, existing DWS client and DingTalk worker.

---

## Scope

This plan implements the approved design in vertical slices. It does not touch existing OKR export artifacts in `outputs/dingteam-okr-2026q2/`.

## File Structure

- Create `app/agent_envelope.py`: Pydantic models for unified output envelope, user response, audit, typed system actions, and domain payload validation helpers.
- Create `app/schemas/agent_envelope.schema.json`: JSON schema used by Codex for unified structured output.
- Create `app/external_retry.py`: fixed-count retry helper for external I/O only; it records each failed attempt and re-raises the final error.
- Create `app/structured_agent.py`: shared `AgentSpec`, skill loader, command builder, `StructuredCodexRunner`, and fail-fast parsing.
- Create `app/okr_models.py`: OKR request, live OKR source, KR review result, and scoring models.
- Create `app/okr_review.py`: OKR request detection, prompt building, live data orchestration, result persistence, result rendering.
- Modify `app/store.py`: session lock methods and OKR review tables/methods.
- Modify `app/codex_runner.py`: reuse existing config helpers where useful; do not change legacy behavior until callers migrate.
- Modify `app/codex_decision.py`: add reply envelope adapter path.
- Modify `app/oa_approval.py`: migrate OA runner to shared `StructuredCodexRunner` and `AgentEnvelope`.
- Modify `app/worker.py`: route OKR before generic reply, use session locks for reply/OA/OKR, call OKR background processing.
- Modify `app/cli.py`: add `process-okr-reviews` command and run it from service maintenance loop.
- Modify `app/dws_client.py`: add one configured live OKR command entrypoint only if the live source is DWS-backed.
- Test files: `tests/test_agent_envelope.py`, `tests/test_structured_agent.py`, `tests/test_store.py`, `tests/test_okr_review.py`, `tests/test_oa_approval.py`, `tests/test_worker.py`, `tests/test_cli.py`.

## Task 1: Agent Envelope Models And Schema

**Files:**
- Create: `app/agent_envelope.py`
- Create: `app/schemas/agent_envelope.schema.json`
- Test: `tests/test_agent_envelope.py`

- [ ] **Step 1: Write failing envelope model tests**

Add `tests/test_agent_envelope.py`:

```python
import json

import pytest
from pydantic import ValidationError

from app.agent_envelope import (
    AgentAudit,
    AgentEnvelope,
    AgentKind,
    SendDingTalkReplyAction,
    UserResponse,
)


def test_agent_envelope_accepts_typed_system_action():
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "收到，我来处理。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {
                "summary": "只需上下文判断。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )

    assert envelope.kind == AgentKind.REPLY
    assert isinstance(envelope.system_actions[0], SendDingTalkReplyAction)
    assert envelope.user_response.text == "收到，我来处理。"


def test_agent_envelope_requires_non_empty_audit_summary():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "no_reply",
                    "text": "",
                    "sensitivity_kind": "general",
                },
                "system_actions": [],
                "domain_payload": {},
                "audit": {"summary": "", "documents": [], "confidence": 0.5},
            }
        )


def test_agent_envelope_rejects_unknown_system_action():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "send_reply",
                    "text": "ok",
                    "sensitivity_kind": "general",
                },
                "system_actions": [{"type": "unknown_action"}],
                "domain_payload": {},
                "audit": {
                    "summary": "valid summary",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        )


def test_agent_envelope_schema_is_strict():
    schema = json.loads(
        open("app/schemas/agent_envelope.schema.json", encoding="utf-8").read()
    )

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_agent_envelope.py -q`

Expected: fails with `ModuleNotFoundError: No module named 'app.agent_envelope'`.

- [ ] **Step 3: Implement envelope models**

Create `app/agent_envelope.py`:

```python
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class AgentKind(StrEnum):
    REPLY = "reply"
    OA_APPROVAL = "oa_approval"
    OKR_REVIEW = "okr_review"
    NO_ACTION = "no_action"
    ERROR = "error"


class UserResponseMode(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    NO_REPLY = "no_reply"


class AgentSensitivityKind(StrEnum):
    GENERAL = "general"
    INTERNAL_PERSONNEL = "internal_personnel"
    EXTERNAL_CANDIDATE = "external_candidate"


class UserResponse(BaseModel):
    mode: UserResponseMode
    text: str = ""
    sensitivity_kind: AgentSensitivityKind = AgentSensitivityKind.GENERAL


class AgentAuditDocument(BaseModel):
    title: str
    url: str = ""
    relevance: str


class AgentAudit(BaseModel):
    summary: str = Field(min_length=1)
    documents: list[AgentAuditDocument] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class SendDingTalkReplyAction(BaseModel):
    type: Literal["send_dingtalk_reply"]
    reply_text_ref: Literal["user_response.text"]


class DwsOaApprovalAction(BaseModel):
    type: Literal["dws_oa_approval_action"]
    process_instance_id: str
    task_id: str
    action: Literal["通过", "拒绝"]
    remark: str = Field(min_length=1)


class DwsOaApprovalCommentAction(BaseModel):
    type: Literal["dws_oa_approval_comment"]
    process_instance_id: str
    text: str = Field(min_length=1)


class PersistOkrReviewAction(BaseModel):
    type: Literal["persist_okr_review"]
    request_id: int


SystemAction = Annotated[
    Union[
        SendDingTalkReplyAction,
        DwsOaApprovalAction,
        DwsOaApprovalCommentAction,
        PersistOkrReviewAction,
    ],
    Field(discriminator="type"),
]


class AgentEnvelope(BaseModel):
    kind: AgentKind
    user_response: UserResponse
    system_actions: list[SystemAction] = Field(default_factory=list)
    domain_payload: dict[str, Any] = Field(default_factory=dict)
    audit: AgentAudit
```

- [ ] **Step 4: Generate strict schema**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path
from app.agent_envelope import AgentEnvelope

schema = AgentEnvelope.model_json_schema()
schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
schema["title"] = "CEO Agent Unified Envelope"
Path("app/schemas/agent_envelope.schema.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
```

Expected: `app/schemas/agent_envelope.schema.json` exists.

- [ ] **Step 5: Run envelope tests**

Run: `pytest tests/test_agent_envelope.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/agent_envelope.py app/schemas/agent_envelope.schema.json tests/test_agent_envelope.py
git commit -m "feat: add unified agent envelope"
```

## Task 2: Conversation Codex Session Lock

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing lock tests**

Append to `tests/test_store.py`:

```python
def test_codex_session_lock_is_exclusive(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    store.release_codex_session_lock("cid-1", "okr:1")
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_codex_session_lock_release_requires_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.release_codex_session_lock("cid-1", "other") is False
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False
    assert store.release_codex_session_lock("cid-1", "okr:1") is True


def test_codex_session_lock_context_manager_releases_without_swallowing(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store.codex_session_lock("cid-1", "okr:1"):
        assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
```

- [ ] **Step 2: Run failing lock tests**

Run: `pytest tests/test_store.py::test_codex_session_lock_is_exclusive tests/test_store.py::test_codex_session_lock_release_requires_owner tests/test_store.py::test_codex_session_lock_context_manager_releases_without_swallowing -q`

Expected: fails because `AutoReplyStore` has no lock methods.

- [ ] **Step 3: Add lock table**

In `app/store.py`, inside the schema initialization SQL near `service_state`, add:

```sql
create table if not exists codex_session_locks (
    conversation_id text primary key,
    owner text not null,
    locked_at text not null default current_timestamp
);
```

- [ ] **Step 4: Add lock context class and methods**

Add module-level `CodexSessionLock` near the other store helper classes in `app/store.py`:

```python
class CodexSessionLock:
    def __init__(self, store, conversation_id: str, owner: str):
        self.store = store
        self.conversation_id = conversation_id
        self.owner = owner

    def __enter__(self):
        if not self.store.acquire_codex_session_lock(self.conversation_id, self.owner):
            raise RuntimeError(f"codex session locked: {self.conversation_id}")
        return self

    def __exit__(self, exc_type, exc, tb):
        released = self.store.release_codex_session_lock(
            self.conversation_id,
            self.owner,
        )
        if not released and exc_type is None:
            raise RuntimeError(
                f"codex session lock release failed: {self.conversation_id}"
            )
        return False
```

Add methods to `AutoReplyStore` near `get_codex_session_id`:

```python
    def acquire_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        with self._connect() as db:
            cursor = db.execute(
                """
                insert or ignore into codex_session_locks (conversation_id, owner)
                values (?, ?)
                """,
                (conversation_id, owner),
            )
            return cursor.rowcount == 1

    def release_codex_session_lock(self, conversation_id: str, owner: str) -> bool:
        if not conversation_id.strip():
            raise ValueError("missing conversation_id")
        if not owner.strip():
            raise ValueError("missing lock owner")
        with self._connect() as db:
            cursor = db.execute(
                """
                delete from codex_session_locks
                where conversation_id=? and owner=?
                """,
                (conversation_id, owner),
            )
            return cursor.rowcount == 1

    def codex_session_lock(self, conversation_id: str, owner: str) -> CodexSessionLock:
        return CodexSessionLock(self, conversation_id, owner)
```

- [ ] **Step 5: Run lock tests**

Run: `pytest tests/test_store.py::test_codex_session_lock_is_exclusive tests/test_store.py::test_codex_session_lock_release_requires_owner tests/test_store.py::test_codex_session_lock_context_manager_releases_without_swallowing -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: add codex session locks"
```

## Task 2A: External Retry Helper

**Files:**
- Create: `app/external_retry.py`
- Test: `tests/test_external_retry.py`

- [ ] **Step 1: Write failing external retry tests**

Create `tests/test_external_retry.py`:

```python
import pytest

from app.external_retry import ExternalAttempt, run_external


def test_run_external_retries_then_returns_value():
    attempts = []

    def operation():
        attempts.append("call")
        if len(attempts) < 3:
            raise RuntimeError(f"transient {len(attempts)}")
        return {"ok": True}

    failures: list[ExternalAttempt] = []
    result = run_external(
        "dws okr fetch",
        operation,
        max_attempts=3,
        delay_seconds=0,
        sleep=lambda seconds: None,
        on_failure=failures.append,
    )

    assert result == {"ok": True}
    assert len(attempts) == 3
    assert [failure.attempt for failure in failures] == [1, 2]
    assert failures[0].operation == "dws okr fetch"
    assert "transient 1" in failures[0].error


def test_run_external_reraises_final_error_after_max_attempts():
    failures: list[ExternalAttempt] = []

    def operation():
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        run_external(
            "codex exec",
            operation,
            max_attempts=2,
            delay_seconds=0,
            sleep=lambda seconds: None,
            on_failure=failures.append,
        )

    assert [failure.attempt for failure in failures] == [1, 2]


def test_run_external_rejects_invalid_attempt_count():
    with pytest.raises(ValueError, match="max_attempts"):
        run_external("dws", lambda: None, max_attempts=0)
```

- [ ] **Step 2: Run failing external retry tests**

Run: `pytest tests/test_external_retry.py -q`

Expected: fails with `ModuleNotFoundError: No module named 'app.external_retry'`.

- [ ] **Step 3: Implement external retry helper**

Create `app/external_retry.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ExternalAttempt:
    operation: str
    attempt: int
    max_attempts: int
    error: str


def run_external(
    operation: str,
    call: Callable[[], T],
    *,
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    on_failure: Callable[[ExternalAttempt], None] | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            failure = ExternalAttempt(
                operation=operation,
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(exc),
            )
            if on_failure is not None:
                on_failure(failure)
            if attempt == max_attempts:
                raise
            sleep(delay_seconds)
    raise RuntimeError(f"unreachable external retry state: {operation}")
```

- [ ] **Step 4: Run external retry tests**

Run: `pytest tests/test_external_retry.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/external_retry.py tests/test_external_retry.py
git commit -m "feat: add external retry helper"
```

## Task 3: Shared Skill Loader And Structured Runner Skeleton

**Files:**
- Create: `app/structured_agent.py`
- Test: `tests/test_structured_agent.py`

- [ ] **Step 1: Write failing skill loader tests**

Create `tests/test_structured_agent.py`:

```python
from pathlib import Path

import pytest

from app.structured_agent import AgentSpec, SkillLoadError, load_skill_text


def test_load_skill_text_reads_exact_paths(tmp_path: Path):
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("# Test Skill\n\nUse exact rules.", encoding="utf-8")

    assert load_skill_text([skill]) == "# Test Skill\n\nUse exact rules."


def test_load_skill_text_fails_fast_when_missing(tmp_path: Path):
    with pytest.raises(SkillLoadError, match="missing skill file"):
        load_skill_text([tmp_path / "missing" / "SKILL.md"])


def test_agent_spec_developer_instructions_include_skills(tmp_path: Path):
    skill = tmp_path / "skill.md"
    skill.write_text("# OKR Skill", encoding="utf-8")
    spec = AgentSpec(
        name="okr_review",
        schema_path=tmp_path / "schema.json",
        primary_skill_paths=[skill],
        reply_visible_skill_paths=[],
        developer_preamble="Return only JSON.",
    )

    assert "# OKR Skill" in spec.developer_instructions()
    assert "Return only JSON." in spec.developer_instructions()
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_structured_agent.py -q`

Expected: fails with `ModuleNotFoundError: No module named 'app.structured_agent'`.

- [ ] **Step 3: Implement `AgentSpec` and skill loader**

Create `app/structured_agent.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path


class SkillLoadError(RuntimeError):
    pass


def load_skill_text(paths: list[Path]) -> str:
    sections: list[str] = []
    for path in paths:
        if not path.exists():
            raise SkillLoadError(f"missing skill file: {path}")
        if not path.is_file():
            raise SkillLoadError(f"skill path is not a file: {path}")
        sections.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(section for section in sections if section)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    schema_path: Path
    primary_skill_paths: list[Path] = field(default_factory=list)
    reply_visible_skill_paths: list[Path] = field(default_factory=list)
    developer_preamble: str = ""

    def developer_instructions(self) -> str:
        if not self.schema_path.exists():
            raise SkillLoadError(f"missing schema file: {self.schema_path}")
        skill_text = load_skill_text(
            [*self.primary_skill_paths, *self.reply_visible_skill_paths]
        )
        parts = [
            self.developer_preamble.strip(),
            f"# Agent spec\n\nname: {self.name}",
            skill_text,
        ]
        return "\n\n".join(part for part in parts if part)
```

- [ ] **Step 4: Adjust test to create schema**

Modify `test_agent_spec_developer_instructions_include_skills` so it creates the schema file:

```python
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    spec = AgentSpec(
        name="okr_review",
        schema_path=schema,
        primary_skill_paths=[skill],
        reply_visible_skill_paths=[],
        developer_preamble="Return only JSON.",
    )
```

- [ ] **Step 5: Run structured agent tests**

Run: `pytest tests/test_structured_agent.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/structured_agent.py tests/test_structured_agent.py
git commit -m "feat: add structured agent skill loader"
```

## Task 4: StructuredCodexRunner With Lock And Fail-Fast Parsing

**Files:**
- Modify: `app/structured_agent.py`
- Test: `tests/test_structured_agent.py`

- [ ] **Step 1: Write failing runner tests**

Append to `tests/test_structured_agent.py`:

```python
import json

from app.agent_envelope import AgentEnvelope
from app.store import AutoReplyStore
from app.structured_agent import StructuredCodexRunner


def test_structured_runner_uses_conversation_session_lock_and_persists_session(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", True, "session-1")
    calls = []

    def executor(command, prompt, env):
        calls.append((command, prompt, env))
        return "\n".join(
            [
                json.dumps({"type": "session", "id": "session-2"}),
                json.dumps(
                    {
                        "kind": "reply",
                        "user_response": {
                            "mode": "send_reply",
                            "text": "ok",
                            "sensitivity_kind": "general",
                        },
                        "system_actions": [
                            {
                                "type": "send_dingtalk_reply",
                                "reply_text_ref": "user_response.text",
                            }
                        ],
                        "domain_payload": {},
                        "audit": {
                            "summary": "valid",
                            "documents": [],
                            "confidence": 0.8,
                        },
                    }
                ),
            ]
        )

    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(
        store=store,
        workspace=tmp_path,
        spec=spec,
        executor=executor,
    )

    result = runner.run(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        prompt="hello",
        owner="reply:msg-1",
    )

    assert isinstance(result.envelope, AgentEnvelope)
    assert store.get_codex_session_id("cid-1") == "session-2"
    assert calls[0][0][:3] == ["codex", "exec", "resume"]
    assert "session-1" in calls[0][0]


def test_structured_runner_fails_fast_when_lock_is_held(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    skill = tmp_path / "skill.md"
    skill.write_text("# Skill", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.acquire_codex_session_lock("cid-1", "other") is True
    spec = AgentSpec("reply", schema, [skill], [], "Return JSON.")
    runner = StructuredCodexRunner(store=store, workspace=tmp_path, spec=spec)

    with pytest.raises(RuntimeError, match="codex session locked"):
        runner.run("cid-1", "Friday", True, "hello", owner="reply:msg-1")
```

- [ ] **Step 2: Run failing runner tests**

Run: `pytest tests/test_structured_agent.py -q`

Expected: fails because `StructuredCodexRunner` is undefined.

- [ ] **Step 3: Implement runner result and runner**

Add to `app/structured_agent.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.agent_envelope import AgentEnvelope
from app.codex_decision import extract_codex_audit_events, extract_codex_session_id
from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX, _config_string
from app.external_retry import run_external


@dataclass(frozen=True)
class StructuredAgentRun:
    envelope: AgentEnvelope
    codex_session_id: str
    transcript_start_line: int
    transcript_end_line: int
    audit_tool_events: list[dict[str, str]]


class StructuredCodexRunner:
    def __init__(
        self,
        *,
        store,
        workspace: Path,
        spec: AgentSpec,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str, dict[str, str]], str] | None = None,
    ):
        self.store = store
        self.workspace = workspace
        self.spec = spec
        self.codex_bin = codex_bin
        self.executor = executor

    def run(
        self,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        prompt: str,
        *,
        owner: str,
    ) -> StructuredAgentRun:
        with self.store.codex_session_lock(conversation_id, owner):
            session_id = self.store.get_codex_session_id(conversation_id)
            command = self._build_command(prompt, session_id)
            raw = self._execute(command, prompt)
            parsed_session_id = extract_codex_session_id(raw) or session_id or ""
            envelope = parse_agent_envelope(raw)
            if parsed_session_id:
                self.store.upsert_conversation(
                    conversation_id,
                    conversation_title,
                    single_chat,
                    parsed_session_id,
                )
            return StructuredAgentRun(
                envelope=envelope,
                codex_session_id=parsed_session_id,
                transcript_start_line=0,
                transcript_end_line=0,
                audit_tool_events=extract_codex_audit_events(raw),
            )

    def _execute(self, command: list[str], prompt: str) -> str:
        if self.executor is None:
            raise RuntimeError("structured runner executor is not configured")
        return run_external(
            "codex exec",
            lambda: self.executor(command, prompt, {}),
            max_attempts=3,
        )

    def _build_command(self, prompt: str, session_id: str | None) -> list[str]:
        common = [
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            _config_string("developer_instructions", self.spec.developer_instructions()),
            "-c",
            'model_reasoning_summary="concise"',
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
        ]
        if session_id:
            return [
                self.codex_bin,
                "exec",
                "resume",
                *common,
                CODEX_BYPASS_APPROVALS_AND_SANDBOX,
                "--output-schema",
                str(self.spec.schema_path),
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common,
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--output-schema",
            str(self.spec.schema_path),
            "--cd",
            str(self.workspace),
            "-",
        ]


def parse_agent_envelope(raw: str) -> AgentEnvelope:
    payloads = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for payload in reversed(payloads):
        if isinstance(payload, dict):
            if "kind" in payload and "user_response" in payload:
                return AgentEnvelope.model_validate(payload)
            item = payload.get("item")
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return AgentEnvelope.model_validate(json.loads(item["text"]))
            message = payload.get("message")
            if isinstance(message, str) and message.strip().startswith("{"):
                return AgentEnvelope.model_validate(json.loads(message))
    raise ValueError("no valid AgentEnvelope found")
```

- [ ] **Step 4: Run structured runner tests**

Run: `pytest tests/test_structured_agent.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/structured_agent.py tests/test_structured_agent.py
git commit -m "feat: add structured codex runner"
```

## Task 5: OKR Models And Scoring Payload

**Files:**
- Create: `app/okr_models.py`
- Test: `tests/test_okr_review.py`

- [ ] **Step 1: Write failing OKR scoring model tests**

Create `tests/test_okr_review.py`:

```python
import pytest
from pydantic import ValidationError

from app.okr_models import OkrReviewItem, OkrReviewPayload


def test_okr_review_item_requires_two_scores_and_discount_reasons():
    item = OkrReviewItem.model_validate(
        {
            "objective_title": "提升交付质量",
            "objective_weight": 1.0,
            "kr_title": "Q2 完成 3 个客户验收",
            "kr_weight": 0.5,
            "self_progress": "80%",
            "kr_progress_update": "6月20日完成两个客户验收，第三个在推进。",
            "claim_text": "完成两个客户验收，第三个在推进。",
            "claim_completion_time": "2026-06-20",
            "deadline": "2026-06-15",
            "claim_base_score": 80,
            "claim_discount_factor": 0.8,
            "claim_discount_reason": "员工主张完成时间晚于 KR 要求 5 天。",
            "claim_score": 64,
            "verified_completion_time": "2026-06-21",
            "verified_base_score": 60,
            "verified_discount_factor": 0.6,
            "verified_discount_reason": "证据显示实际验收晚于要求且影响交付节奏。",
            "verified_score": 36,
            "evidence_used": [
                {"source": "dws:minutes:abc", "summary": "客户验收会确认两个项目通过。"}
            ],
            "evidence_gap": "缺少第三个客户验收确认。",
            "review_comment": "进展存在，但未完整达到 3 个验收目标。",
            "suggested_follow_up": "补充第三个客户验收记录和客户确认时间。",
        }
    )

    assert item.claim_score == 64
    assert item.verified_score == 36


def test_okr_review_item_rejects_discount_outside_range():
    payload = {
        "objective_title": "提升交付质量",
        "objective_weight": 1.0,
        "kr_title": "Q2 完成 3 个客户验收",
        "kr_weight": 0.5,
        "self_progress": "80%",
        "kr_progress_update": "表达不清。",
        "claim_text": "表达不清。",
        "claim_completion_time": "",
        "deadline": "2026-06-15",
        "claim_base_score": 60,
        "claim_discount_factor": 0.9,
        "claim_discount_reason": "折扣超过允许范围。",
        "claim_score": 54,
        "verified_completion_time": "",
        "verified_base_score": 0,
        "verified_discount_factor": 1.0,
        "verified_discount_reason": "无证据时不适用折扣。",
        "verified_score": 0,
        "evidence_used": [],
        "evidence_gap": "没有独立证据。",
        "review_comment": "证据不足。",
        "suggested_follow_up": "补充可验证材料。",
    }

    with pytest.raises(ValidationError):
        OkrReviewItem.model_validate(payload)


def test_okr_review_payload_contains_items():
    payload = OkrReviewPayload.model_validate(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "共 1 个 KR。",
            "items": [
                {
                    "objective_title": "提升交付质量",
                    "objective_weight": 1.0,
                    "kr_title": "Q2 完成 3 个客户验收",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个客户验收。",
                    "claim_text": "完成两个客户验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现时间或含糊折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少客户验收记录。",
                    "review_comment": "只能确认员工主张，未能核实。",
                    "suggested_follow_up": "提供客户验收材料。",
                }
            ],
        }
    )

    assert payload.items[0].kr_title == "Q2 完成 3 个客户验收"
```

- [ ] **Step 2: Run failing OKR model tests**

Run: `pytest tests/test_okr_review.py -q`

Expected: fails with `ModuleNotFoundError: No module named 'app.okr_models'`.

- [ ] **Step 3: Implement OKR models**

Create `app/okr_models.py`:

```python
from pydantic import BaseModel, Field


class OkrEvidence(BaseModel):
    source: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class OkrReviewItem(BaseModel):
    objective_title: str
    objective_weight: float = Field(ge=0, le=1)
    kr_title: str
    kr_weight: float = Field(ge=0, le=1)
    self_progress: str
    kr_progress_update: str
    claim_text: str
    claim_completion_time: str
    deadline: str
    claim_base_score: float = Field(ge=0, le=100)
    claim_discount_factor: float = Field(ge=0.3, le=1.0)
    claim_discount_reason: str = Field(min_length=1)
    claim_score: float = Field(ge=0, le=100)
    verified_completion_time: str
    verified_base_score: float = Field(ge=0, le=100)
    verified_discount_factor: float = Field(ge=0.3, le=1.0)
    verified_discount_reason: str = Field(min_length=1)
    verified_score: float = Field(ge=0, le=100)
    evidence_used: list[OkrEvidence]
    evidence_gap: str
    review_comment: str
    suggested_follow_up: str


class OkrReviewPayload(BaseModel):
    person_name: str
    period_label: str
    summary: str = Field(min_length=1)
    items: list[OkrReviewItem] = Field(min_length=1)
```

- [ ] **Step 4: Run OKR model tests**

Run: `pytest tests/test_okr_review.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/okr_models.py tests/test_okr_review.py
git commit -m "feat: add okr review models"
```

## Task 6: OKR Store Tables And Methods

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing OKR store tests**

Append to `tests/test_store.py`:

```python
def test_create_and_claim_okr_review_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    claimed = store.claim_okr_review_requests(limit=1)

    assert [item.id for item in claimed] == [request_id]
    assert claimed[0].status == "processing"


def test_record_okr_review_run_and_items(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    run_id = store.record_okr_review_run(
        request_id=request_id,
        codex_session_id="session-1",
        codex_transcript_start_line=1,
        codex_transcript_end_line=10,
        envelope_json='{"kind":"okr_review"}',
        audit_tool_events_json='[]',
        audit_summary="审核完成。",
    )
    item_id = store.record_okr_review_item(
        request_id=request_id,
        objective_title="O",
        objective_weight=1.0,
        kr_title="KR",
        kr_weight=0.5,
        item_json='{"kr_title":"KR"}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert run_id > 0
    assert item_id > 0
```

- [ ] **Step 2: Run failing OKR store tests**

Run: `pytest tests/test_store.py::test_create_and_claim_okr_review_request tests/test_store.py::test_record_okr_review_run_and_items -q`

Expected: fails because store methods do not exist.

- [ ] **Step 3: Add OKR tables**

In `app/store.py` schema initialization, add:

```sql
create table if not exists okr_review_requests (
    id integer primary key autoincrement,
    conversation_id text not null,
    conversation_title text not null,
    trigger_message_id text not null,
    trigger_sender text not null,
    trigger_sender_user_id text not null default '',
    trigger_text text not null,
    period_label text not null,
    period_start text not null,
    period_end text not null,
    okr_source_json text not null default '{}',
    status text not null default 'pending',
    error text not null default '',
    codex_session_id text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(conversation_id, trigger_message_id)
);
create index if not exists idx_okr_review_requests_status
    on okr_review_requests(status, id);
create table if not exists okr_review_runs (
    id integer primary key autoincrement,
    request_id integer not null,
    codex_session_id text not null default '',
    codex_transcript_start_line integer not null default 0,
    codex_transcript_end_line integer not null default 0,
    envelope_json text not null default '{}',
    audit_tool_events_json text not null default '[]',
    audit_summary text not null default '',
    created_at text not null default current_timestamp
);
create table if not exists okr_review_items (
    id integer primary key autoincrement,
    request_id integer not null,
    objective_title text not null,
    objective_weight real not null default 0,
    kr_title text not null,
    kr_weight real not null default 0,
    item_json text not null default '{}',
    created_at text not null default current_timestamp
);
```

- [ ] **Step 4: Add store model classes**

At the top of `app/store.py`, near existing Pydantic store models, add:

```python
class OkrReviewRequest(BaseModel):
    id: int
    conversation_id: str
    conversation_title: str
    trigger_message_id: str
    trigger_sender: str
    trigger_sender_user_id: str = ""
    trigger_text: str
    period_label: str
    period_start: str
    period_end: str
    okr_source_json: str = "{}"
    status: str
    error: str = ""
    codex_session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
```

- [ ] **Step 5: Add store methods**

Add methods to `AutoReplyStore` near work summary methods:

```python
    def create_okr_review_request(
        self,
        *,
        conversation_id: str,
        conversation_title: str,
        trigger_message_id: str,
        trigger_sender: str,
        trigger_sender_user_id: str,
        trigger_text: str,
        period_label: str,
        period_start: str,
        period_end: str,
        okr_source_json: str,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into okr_review_requests (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_sender_user_id, trigger_text,
                    period_label, period_start, period_end, okr_source_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(conversation_id, trigger_message_id) do update set
                    okr_source_json=excluded.okr_source_json,
                    updated_at=current_timestamp
                """,
                (
                    conversation_id,
                    conversation_title,
                    trigger_message_id,
                    trigger_sender,
                    trigger_sender_user_id,
                    trigger_text,
                    period_label,
                    period_start,
                    period_end,
                    okr_source_json,
                ),
            )
            row = db.execute(
                """
                select id from okr_review_requests
                where conversation_id=? and trigger_message_id=?
                """,
                (conversation_id, trigger_message_id),
            ).fetchone()
            return int(row["id"])

    def claim_okr_review_requests(self, limit: int) -> list[OkrReviewRequest]:
        if limit <= 0:
            return []
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select * from okr_review_requests
                where status='pending'
                order by id
                limit ?
                """,
                (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"""
                update okr_review_requests
                set status='processing', error='', updated_at=current_timestamp
                where id in ({placeholders})
                """,
                ids,
            )
            claimed = db.execute(
                f"select * from okr_review_requests where id in ({placeholders}) order by id",
                ids,
            ).fetchall()
            return [OkrReviewRequest.model_validate(dict(row)) for row in claimed]

    def get_okr_review_request(self, request_id: int) -> OkrReviewRequest:
        with self._connect() as db:
            row = db.execute(
                "select * from okr_review_requests where id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"okr review request not found: {request_id}")
            return OkrReviewRequest.model_validate(dict(row))

    def mark_okr_review_request_done(self, request_id: int, *, codex_session_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update okr_review_requests
                set status='done', error='', codex_session_id=?, updated_at=current_timestamp
                where id=?
                """,
                (codex_session_id, request_id),
            )

    def mark_okr_review_request_failed(self, request_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update okr_review_requests
                set status='failed', error=?, updated_at=current_timestamp
                where id=?
                """,
                (error, request_id),
            )

    def record_okr_review_run(
        self,
        *,
        request_id: int,
        codex_session_id: str,
        codex_transcript_start_line: int,
        codex_transcript_end_line: int,
        envelope_json: str,
        audit_tool_events_json: str,
        audit_summary: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into okr_review_runs (
                    request_id, codex_session_id, codex_transcript_start_line,
                    codex_transcript_end_line, envelope_json,
                    audit_tool_events_json, audit_summary
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    codex_session_id,
                    codex_transcript_start_line,
                    codex_transcript_end_line,
                    envelope_json,
                    audit_tool_events_json,
                    audit_summary,
                ),
            )
            return int(cursor.lastrowid)

    def record_okr_review_item(
        self,
        *,
        request_id: int,
        objective_title: str,
        objective_weight: float,
        kr_title: str,
        kr_weight: float,
        item_json: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into okr_review_items (
                    request_id, objective_title, objective_weight,
                    kr_title, kr_weight, item_json
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (request_id, objective_title, objective_weight, kr_title, kr_weight, item_json),
            )
            return int(cursor.lastrowid)
```

- [ ] **Step 6: Run OKR store tests**

Run: `pytest tests/test_store.py::test_create_and_claim_okr_review_request tests/test_store.py::test_record_okr_review_run_and_items -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist okr review requests"
```

## Task 7: OKR Prompt, Detection, And Result Rendering

**Files:**
- Create: `app/okr_review.py`
- Test: `tests/test_okr_review.py`

- [ ] **Step 1: Add failing OKR behavior tests**

Append to `tests/test_okr_review.py`:

```python
from app.dingtalk_models import DingTalkMessage
from app.okr_review import (
    build_okr_review_prompt,
    current_quarter_period,
    is_okr_review_request,
    render_okr_review_reply,
)


def test_is_okr_review_request_matches_review_intent():
    assert is_okr_review_request("帮我审核 OKR")
    assert is_okr_review_request("看看我的 KR 进度")
    assert not is_okr_review_request("今天 OKR 系统打不开")


def test_current_quarter_period_uses_current_date():
    period = current_quarter_period("2026-06-08")
    assert period.period_label == "2026 Q2"
    assert period.period_start == "2026-04-01"
    assert period.period_end == "2026-06-30"


def test_build_okr_review_prompt_includes_live_source_and_claim_scoring():
    prompt = build_okr_review_prompt(
        request_id=7,
        person_name="韩露",
        period_label="2026 Q2",
        okr_source_json='{"objectives":[]}',
        trigger_text="帮我审核 OKR",
    )

    assert "request_id: 7" in prompt
    assert "KR进度更新" in prompt
    assert "员工主张信息打分" in prompt
    assert "事实核实后打分" in prompt


def test_render_okr_review_reply_includes_two_scores():
    payload = OkrReviewPayload.model_validate(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "1 个 KR 已审核。",
            "items": [
                {
                    "objective_title": "O",
                    "objective_weight": 1.0,
                    "kr_title": "KR",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个验收。",
                    "claim_text": "完成两个验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少验收记录。",
                    "review_comment": "证据不足。",
                    "suggested_follow_up": "补充验收记录。",
                }
            ],
        }
    )

    reply = render_okr_review_reply(payload)

    assert "员工主张分: 60" in reply
    assert "事实核实分: 0" in reply
    assert "缺少验收记录" in reply
```

- [ ] **Step 2: Run failing OKR behavior tests**

Run: `pytest tests/test_okr_review.py -q`

Expected: fails because `app.okr_review` does not exist.

- [ ] **Step 3: Implement OKR behavior helpers**

Create `app/okr_review.py`:

```python
import json
from dataclasses import dataclass
from datetime import date

from app.okr_models import OkrReviewPayload


@dataclass(frozen=True)
class OkrPeriod:
    period_label: str
    period_start: str
    period_end: str


def is_okr_review_request(text: str) -> bool:
    normalized = " ".join(text.strip().split()).casefold()
    review_markers = ("审核", "review", "看看", "打分", "评价")
    okr_markers = ("okr", "kr", "目标")
    return any(marker in normalized for marker in review_markers) and any(
        marker in normalized for marker in okr_markers
    )


def current_quarter_period(today: str | None = None) -> OkrPeriod:
    current = date.fromisoformat(today) if today else date.today()
    quarter = (current.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(current.year, start_month, 1)
    if end_month == 12:
        end = date(current.year, 12, 31)
    else:
        end = date(current.year, end_month + 1, 1).replace(day=1)
        end = date.fromordinal(end.toordinal() - 1)
    return OkrPeriod(
        period_label=f"{current.year} Q{quarter}",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
    )


def build_okr_review_prompt(
    *,
    request_id: int,
    person_name: str,
    period_label: str,
    okr_source_json: str,
    trigger_text: str,
) -> str:
    json.loads(okr_source_json)
    return f"""你是 CEO Agent OKR review task。

request_id: {request_id}
person_name: {person_name}
period_label: {period_label}
trigger_text: {trigger_text}

实时叮当 OKR JSON:
{okr_source_json}

任务:
- 逐 KR 阅读 KR进度更新。
- 从 KR进度更新中抽取员工主张、完成时间、产出和指标。
- 给出员工主张信息打分。
- 使用本地文件、memory_recall、DWS 搜索和读取进行事实核实。
- 给出事实核实后打分。
- 两套分数都必须考虑超期、时差、业务影响和表述是否可衡量。
- 只输出 AgentEnvelope JSON，kind=okr_review，domain_payload 必须符合 OkrReviewPayload。
"""


def render_okr_review_reply(payload: OkrReviewPayload) -> str:
    lines = [f"{payload.person_name} {payload.period_label} OKR 审核", payload.summary]
    for index, item in enumerate(payload.items, start=1):
        lines.extend(
            [
                "",
                f"{index}. {item.kr_title}",
                f"- 员工主张分: {item.claim_score:g}（基础 {item.claim_base_score:g}，折扣 {item.claim_discount_factor:g}）",
                f"- 事实核实分: {item.verified_score:g}（基础 {item.verified_base_score:g}，折扣 {item.verified_discount_factor:g}）",
                f"- 依据: {'；'.join(e.summary for e in item.evidence_used) or '无独立证据'}",
                f"- 证据缺口: {item.evidence_gap}",
                f"- 建议: {item.suggested_follow_up}",
            ]
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run OKR behavior tests**

Run: `pytest tests/test_okr_review.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/okr_review.py tests/test_okr_review.py
git commit -m "feat: add okr review prompt helpers"
```

## Task 8: Live OKR Source Interface

**Files:**
- Modify: `app/okr_review.py`
- Test: `tests/test_okr_review.py`

- [ ] **Step 1: Write failing live source tests**

Append to `tests/test_okr_review.py`:

```python
from app.okr_review import DwsLiveOkrSource


class FakeDwsForOkr:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"objectives": []}
        self.error = error
        self.calls = []

    def run_json(self, command):
        self.calls.append(command)
        if self.error:
            raise self.error
        return self.payload


def test_dws_live_okr_source_uses_single_configured_command():
    dws = FakeDwsForOkr(payload={"objectives": [{"title": "O"}]})
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=[
            "dws",
            "api",
            "request",
            "--resource",
            "okr",
            "--user-id",
            "{user_id}",
            "--period",
            "{period_label}",
            "--format",
            "json",
        ],
    )

    payload = source.fetch_user_okr(user_id="user-1", period_label="2026 Q2")

    assert payload["objectives"][0]["title"] == "O"
    assert "{user_id}" not in dws.calls[0]
    assert "user-1" in dws.calls[0]


def test_dws_live_okr_source_retries_then_reraises_source_error():
    dws = FakeDwsForOkr(error=RuntimeError("okr unavailable"))
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=["dws", "api", "--user-id", "{user_id}", "--period", "{period_label}"],
        max_attempts=2,
    )

    with pytest.raises(RuntimeError, match="okr unavailable"):
        source.fetch_user_okr(user_id="user-1", period_label="2026 Q2")

    assert len(dws.calls) == 2
```

- [ ] **Step 2: Run failing live source tests**

Run: `pytest tests/test_okr_review.py::test_dws_live_okr_source_uses_single_configured_command tests/test_okr_review.py::test_dws_live_okr_source_retries_then_reraises_source_error -q`

Expected: fails because `DwsLiveOkrSource` does not exist.

- [ ] **Step 3: Implement live source**

Add to `app/okr_review.py`:

```python
from app.external_retry import run_external


class DwsLiveOkrSource:
    def __init__(self, *, dws, command_template: list[str], max_attempts: int = 3):
        if not command_template:
            raise ValueError("missing OKR live source command template")
        self.dws = dws
        self.command_template = command_template
        self.max_attempts = max_attempts

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        if not user_id.strip():
            raise ValueError("missing OKR user_id")
        command = [
            part.replace("{user_id}", user_id).replace("{period_label}", period_label)
            for part in self.command_template
        ]
        payload = run_external(
            "dws okr live source",
            lambda: self.dws.run_json(command),
            max_attempts=self.max_attempts,
        )
        if not isinstance(payload, dict):
            raise ValueError("invalid OKR live source payload")
        return payload
```

- [ ] **Step 4: Run live source tests**

Run: `pytest tests/test_okr_review.py::test_dws_live_okr_source_uses_single_configured_command tests/test_okr_review.py::test_dws_live_okr_source_retries_then_reraises_source_error -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/okr_review.py tests/test_okr_review.py
git commit -m "feat: add live okr source"
```

## Task 9: OKR Review Processor

**Files:**
- Modify: `app/okr_review.py`
- Test: `tests/test_okr_review.py`

- [ ] **Step 1: Write failing processor test**

Append to `tests/test_okr_review.py`:

```python
import json

from app.agent_envelope import AgentEnvelope
from app.okr_review import process_okr_review_request
from app.store import AutoReplyStore


class FakeStructuredRunnerForOkr:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    def run(self, conversation_id, conversation_title, single_chat, prompt, *, owner):
        self.calls.append((conversation_id, conversation_title, single_chat, prompt, owner))
        return type(
            "Run",
            (),
            {
                "envelope": self.envelope,
                "codex_session_id": "session-okr",
                "transcript_start_line": 1,
                "transcript_end_line": 10,
                "audit_tool_events": [{"tool": "memory_recall"}],
            },
        )()


def test_process_okr_review_request_persists_items_and_marks_done(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    request = store.claim_okr_review_requests(1)[0]
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "okr_review",
            "user_response": {
                "mode": "send_reply",
                "text": "OKR review done",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [{"type": "persist_okr_review", "request_id": request_id}],
            "domain_payload": {
                "person_name": "韩露",
                "period_label": "2026 Q2",
                "summary": "1 个 KR 已审核。",
                "items": [
                    {
                        "objective_title": "O",
                        "objective_weight": 1.0,
                        "kr_title": "KR",
                        "kr_weight": 0.5,
                        "self_progress": "80%",
                        "kr_progress_update": "完成两个验收。",
                        "claim_text": "完成两个验收。",
                        "claim_completion_time": "",
                        "deadline": "",
                        "claim_base_score": 60,
                        "claim_discount_factor": 1.0,
                        "claim_discount_reason": "未发现折扣。",
                        "claim_score": 60,
                        "verified_completion_time": "",
                        "verified_base_score": 0,
                        "verified_discount_factor": 1.0,
                        "verified_discount_reason": "无可核验证据。",
                        "verified_score": 0,
                        "evidence_used": [],
                        "evidence_gap": "缺少验收记录。",
                        "review_comment": "证据不足。",
                        "suggested_follow_up": "补充验收记录。",
                    }
                ],
            },
            "audit": {"summary": "审核完成。", "documents": [], "confidence": 0.8},
        }
    )
    runner = FakeStructuredRunnerForOkr(envelope)

    reply = process_okr_review_request(store=store, runner=runner, request=request)

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert loaded.codex_session_id == "session-okr"
    assert "员工主张分" in reply
    assert runner.calls[0][4] == f"okr_review:{request_id}"
```

- [ ] **Step 2: Run failing processor test**

Run: `pytest tests/test_okr_review.py::test_process_okr_review_request_persists_items_and_marks_done -q`

Expected: fails because `process_okr_review_request` does not exist.

- [ ] **Step 3: Implement processor**

Add to `app/okr_review.py`:

```python
def process_okr_review_request(*, store, runner, request) -> str:
    prompt = build_okr_review_prompt(
        request_id=request.id,
        person_name=request.trigger_sender,
        period_label=request.period_label,
        okr_source_json=request.okr_source_json,
        trigger_text=request.trigger_text,
    )
    run = runner.run(
        request.conversation_id,
        request.conversation_title,
        True,
        prompt,
        owner=f"okr_review:{request.id}",
    )
    payload = OkrReviewPayload.model_validate(run.envelope.domain_payload)
    store.record_okr_review_run(
        request_id=request.id,
        codex_session_id=run.codex_session_id,
        codex_transcript_start_line=run.transcript_start_line,
        codex_transcript_end_line=run.transcript_end_line,
        envelope_json=run.envelope.model_dump_json(),
        audit_tool_events_json=json.dumps(run.audit_tool_events, ensure_ascii=False),
        audit_summary=run.envelope.audit.summary,
    )
    for item in payload.items:
        store.record_okr_review_item(
            request_id=request.id,
            objective_title=item.objective_title,
            objective_weight=item.objective_weight,
            kr_title=item.kr_title,
            kr_weight=item.kr_weight,
            item_json=item.model_dump_json(),
        )
    store.mark_okr_review_request_done(request.id, codex_session_id=run.codex_session_id)
    return render_okr_review_reply(payload)
```

- [ ] **Step 4: Run processor test**

Run: `pytest tests/test_okr_review.py::test_process_okr_review_request_persists_items_and_marks_done -q`

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add app/okr_review.py tests/test_okr_review.py
git commit -m "feat: process okr review requests"
```

## Task 10: Worker OKR Request Routing

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing worker routing test**

Append to `tests/test_worker.py`:

```python
def test_okr_review_request_is_enqueued_before_generic_codex(tmp_path: Path, monkeypatch):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走普通回复"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {"fetch_user_okr": lambda self, user_id, period_label: {"objectives": []}},
    )()

    worker.run_once()

    assert codex.calls == []
    request = worker.store.claim_okr_review_requests(1)[0]
    assert request.trigger_text == "帮我审核 OKR"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "okr_review"
    assert "已受理" in attempt.final_reply_text
```

- [ ] **Step 2: Run failing worker routing test**

Run: `pytest tests/test_worker.py::test_okr_review_request_is_enqueued_before_generic_codex -q`

Expected: fails because worker has no OKR handler.

- [ ] **Step 3: Add OKR handler call before OA/generic reply**

In `app/worker.py`, import:

```python
from app.okr_review import current_quarter_period, is_okr_review_request
```

In `_process_queued_task`, before calendar/OA/generic reply, add:

```python
        if self._handle_okr_review_if_actionable(conversation, trigger):
            return True
```

In `rerun_message`, before OA/generic reply, add the same call with `ignore_existing_attempt=force_new_decision`.

- [ ] **Step 4: Implement `_handle_okr_review_if_actionable`**

Add method near `_handle_oa_approval_if_actionable`:

```python
    def _handle_okr_review_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        *,
        ignore_existing_attempt: bool = False,
    ) -> bool:
        if not is_okr_review_request(trigger.content):
            return False
        if not ignore_existing_attempt and self._handle_existing_attempt(
            conversation,
            trigger,
            [trigger],
            ignore_system_notification_skip=True,
        ):
            return True
        if not hasattr(self, "okr_live_source"):
            raise RuntimeError("OKR live source is not configured")
        period = current_quarter_period()
        okr_payload = self.okr_live_source.fetch_user_okr(
            user_id=trigger.sender_user_id or self.dws.resolve_message_sender(trigger),
            period_label=period.period_label,
        )
        request_id = self.store.create_okr_review_request(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_sender_user_id=trigger.sender_user_id or "",
            trigger_text=trigger.content,
            period_label=period.period_label,
            period_start=period.period_start,
            period_end=period.period_end,
            okr_source_json=json.dumps(okr_payload, ensure_ascii=False),
        )
        reply_text = f"已受理 {period.period_label} OKR 审核请求，正在实时核实 KR 进度和证据。"
        attempt_id = self.store.record_reply_attempt_for_trigger(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action="okr_review",
            sensitivity_kind="internal_personnel",
            codex_reason=f"okr_review_request:{request_id}",
            draft_reply_text=reply_text,
            final_reply_text=reply_text,
            audit_summary="OKR review request accepted and queued.",
            send_status="dry_run" if self.dry_run else "pending",
        )
        self.store.update_reply_attempt(attempt_id, final_reply_text=reply_text)
        if not self.dry_run:
            self._deliver_trigger_reply(
                conversation=conversation,
                trigger=trigger,
                new_messages=[trigger],
                attempt_id=attempt_id,
                reply_text=reply_text,
                feedback_token="",
            )
        else:
            self._mark_seen([trigger])
        return True
```

- [ ] **Step 5: Run worker routing test**

Run: `pytest tests/test_worker.py::test_okr_review_request_is_enqueued_before_generic_codex -q`

Expected: test passes.

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: route okr review requests"
```

## Task 11: CLI And Maintenance Loop Processing

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Add or append to `tests/test_cli.py`:

```python
def test_process_okr_reviews_command_exists():
    from app.cli import parse_args

    args = parse_args(["process-okr-reviews", "--max-batches", "1"])

    assert args.command == "process-okr-reviews"
    assert args.max_batches == 1
```

- [ ] **Step 2: Run failing CLI test**

Run: `pytest tests/test_cli.py::test_process_okr_reviews_command_exists -q`

Expected: fails because command is not registered.

- [ ] **Step 3: Register command**

In `app/cli.py`, add `"process-okr-reviews"` to the command tuple.

- [ ] **Step 4: Add command function**

Add:

```python
def process_okr_reviews_command(settings: WorkerSettings) -> int:
    from app.okr_review import process_okr_review_request
    from app.structured_agent import AgentSpec, StructuredCodexRunner

    store = AutoReplyStore(settings.db_path)
    spec = AgentSpec(
        name="okr_review",
        schema_path=Path("app/schemas/agent_envelope.schema.json"),
        primary_skill_paths=[Path.home() / ".agents" / "skills" / "dingtang-okr-review" / "SKILL.md"],
        reply_visible_skill_paths=[],
        developer_preamble="You are the local CEO Agent OKR review runner. Return only AgentEnvelope JSON.",
    )
    runner = StructuredCodexRunner(store=store, workspace=settings.workspace, spec=spec)
    processed = 0
    for request in store.claim_okr_review_requests(
        20 if settings.max_batches is None else settings.max_batches
    ):
        reply = process_okr_review_request(store=store, runner=runner, request=request)
        conversation = DingTalkConversation(
            open_conversation_id=request.conversation_id,
            title=request.conversation_title,
            single_chat=True,
            unread_point=0,
        )
        dws = DwsClient(
            ding_robot_code=settings.ding_robot_code,
            ding_robot_name=settings.ding_robot_name,
            ding_receiver_user_id=settings.ding_receiver_user_id,
        )
        trigger = DingTalkMessage.model_validate_json(
            json.dumps(
                {
                    "open_conversation_id": request.conversation_id,
                    "open_message_id": request.trigger_message_id,
                    "conversation_title": request.conversation_title,
                    "single_chat": True,
                    "sender_name": request.trigger_sender,
                    "sender_user_id": request.trigger_sender_user_id,
                    "create_time": request.created_at,
                    "content": request.trigger_text,
                },
                ensure_ascii=False,
            )
        )
        dws.send_reply_to_trigger(conversation, trigger, reply)
        processed += 1
    print(f"process-okr-reviews processed={processed}", flush=True)
    return processed
```

- [ ] **Step 5: Wire command dispatch**

In CLI command dispatch, add:

```python
    elif args.command == "process-okr-reviews":
        raise SystemExit(process_okr_reviews_command(settings))
```

In `run_task_maintenance_loop`, call `process_okr_reviews_command(settings)` after `process_work_items_command(settings)`.

- [ ] **Step 6: Run CLI test**

Run: `pytest tests/test_cli.py::test_process_okr_reviews_command_exists -q`

Expected: test passes.

- [ ] **Step 7: Commit**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: process okr reviews from maintenance loop"
```

## Task 12: Reply And OA Envelope Migration

**Files:**
- Modify: `app/codex_decision.py`
- Modify: `app/oa_approval.py`
- Modify: `app/worker.py`
- Test: `tests/test_codex_runner.py`, `tests/test_oa_approval.py`, `tests/test_worker.py`

- [ ] **Step 1: Write failing reply envelope adapter test**

Add to `tests/test_worker.py`:

```python
def test_reply_agent_envelope_send_reply_is_delivered(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 帮我看下", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "可以，我看一下。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {"summary": "普通回复。", "documents": [], "confidence": 0.8},
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)

    worker.run_once()

    assert final_sent(dws)[0].endswith("可以，我看一下。（by明哥分身）")
```

Also add `FakeEnvelopeCodex` next to existing fake codex helpers:

```python
class FakeEnvelopeCodex:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []
        self.last_session_id = "session-envelope"
        self.last_audit_tool_events = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(self, prompt, session_id, image_paths=None):
        self.calls.append((prompt, session_id, image_paths or []))
        return self.envelope
```

- [ ] **Step 2: Implement envelope-to-decision adapter**

In `app/codex_decision.py`, add:

```python
def codex_decision_from_envelope(envelope) -> CodexDecision:
    from app.agent_envelope import AgentKind, UserResponseMode

    if envelope.kind == AgentKind.ERROR:
        return CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason=envelope.audit.summary,
            audit_summary=envelope.audit.summary,
        )
    if envelope.user_response.mode == UserResponseMode.NO_REPLY:
        action = CodexAction.NO_REPLY
    elif envelope.user_response.mode == UserResponseMode.ASK_CLARIFYING_QUESTION:
        action = CodexAction.ASK_CLARIFYING_QUESTION
    else:
        action = CodexAction.SEND_REPLY
    return CodexDecision(
        action=action,
        reply_text=envelope.user_response.text,
        reason=envelope.audit.summary,
        sensitivity_kind=envelope.user_response.sensitivity_kind.value,
        audit_documents=[doc.model_dump() for doc in envelope.audit.documents],
        audit_summary=envelope.audit.summary,
    )
```

In `app/worker.py`, after `decision = self.codex.decide(...)`, normalize:

```python
        if hasattr(decision, "kind") and hasattr(decision, "user_response"):
            decision = codex_decision_from_envelope(decision)
```

Import `codex_decision_from_envelope`.

- [ ] **Step 3: Run reply envelope adapter test**

Run: `pytest tests/test_worker.py::test_reply_agent_envelope_send_reply_is_delivered -q`

Expected: test passes.

- [ ] **Step 4: Migrate OA runner tests to envelope**

Update `tests/test_oa_approval.py` so fake OA runner raw output uses `AgentEnvelope` with:

```json
{
  "kind": "oa_approval",
  "user_response": {"mode": "no_reply", "text": "", "sensitivity_kind": "internal_personnel"},
  "system_actions": [
    {"type": "dws_oa_approval_action", "process_instance_id": "proc-1", "task_id": "task-1", "action": "通过", "remark": "同意。"}
  ],
  "domain_payload": {
    "process_instance_id": "proc-1",
    "task_id": "task-1",
    "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
    "oa_action": "通过",
    "oa_remark": "同意。",
    "action_result": {},
    "audit_summary": "已审阅。",
    "audit_documents": []
  },
  "audit": {"summary": "已审阅。", "documents": [], "confidence": 0.9}
}
```

- [ ] **Step 5: Update OA parser to read envelope domain payload**

In `app/oa_approval.py`, update `parse_oa_approval_json` so it first tries `AgentEnvelope`; when `kind == "oa_approval"`, validate `OaApprovalResult` from `domain_payload`.

- [ ] **Step 6: Run OA tests**

Run: `pytest tests/test_oa_approval.py tests/test_worker.py -q`

Expected: all existing OA and worker tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/codex_decision.py app/oa_approval.py app/worker.py tests/test_oa_approval.py tests/test_worker.py
git commit -m "feat: support unified envelope for reply and oa"
```

## Task 13: Full Verification

**Files:**
- No source changes unless tests expose a narrow bug.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest \
  tests/test_agent_envelope.py \
  tests/test_external_retry.py \
  tests/test_structured_agent.py \
  tests/test_okr_review.py \
  tests/test_oa_approval.py \
  tests/test_worker.py \
  tests/test_store.py \
  tests/test_cli.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run broader tests**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Check worktree for unrelated files**

Run: `git status --short`

Expected: only intended source/test files are modified or staged. Existing `outputs/dingteam-okr-2026q2/` files may still be dirty from prior work and must not be staged.

- [ ] **Step 4: Restart local service after code commits**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: service is running under a fresh process and has no immediate launchd failure.

- [ ] **Step 5: Commit verification fixes if any**

If a narrow verification bug was fixed:

```bash
git add app tests
git commit -m "fix: stabilize okr review runner"
```

If no fixes were needed, do not create an empty commit.
