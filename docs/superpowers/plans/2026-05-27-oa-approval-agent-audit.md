# OA Approval Agent Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route DingTalk OA approval tasks through an agent using the `dingtalk-oa-approval` skill and record the OA action, remark, URL, and result in existing local audit history.

**Architecture:** Keep the service as the orchestrator and audit recorder. Let a task-specific OA Codex runner read the `dingtalk-oa-approval` skill and call `dws` or authorized DingTalk OA API directly; do not add OA methods to `DwsClient` unless the service itself later owns OA API calls. Store OA metadata on existing `reply_attempts` rows and render those fields in existing attempt detail pages, without creating a separate OA page.

**Tech Stack:** Python 3, Pydantic, SQLite, pytest, Codex CLI `codex exec`, existing `dws` CLI, existing FastAPI audit web.

---

## Scope Decisions

- Use one audit row per OA handling attempt in `reply_attempts`.
- Add OA metadata columns to `reply_attempts`; do not create `oa_review_attempts` or `oa_action_attempts`.
- The final OA action recorded by the system is exactly one of `通过`, `拒绝`, or `退回`.
- Do not add an OA-specific audit page. Show OA fields on the existing attempt detail page.
- Do not add OA wrappers to `DwsClient`. The OA agent calls `dws` and authorized DingTalk OA API directly because current OA detail requires API fallback.
- Update the global `dingtalk-oa-approval` skill to allow authorized OA API detail reads when DWS cannot return complete detail.

## File Structure

- Modify `apps/local-service/ceo_agent_service/store.py`
  - Add OA metadata fields to `ReplyAttempt`.
  - Add SQLite migrations for OA columns.
  - Add optional OA parameters to `record_reply_attempt()` and `update_reply_attempt()`.

- Create `apps/local-service/ceo_agent_service/oa_approval.py`
  - Define `OaApprovalAction` and `OaApprovalResult`.
  - Build OA-specific Codex commands with the global skill injected into developer instructions.
  - Parse Codex JSON output and keep session id / transcript line range / audit tool events.
  - Extract OA URLs from DingTalk cards.

- Create `apps/local-service/ceo_agent_service/schemas/oa_approval.schema.json`
  - Require `oa_action` as `通过`, `拒绝`, or `退回`.
  - Require `oa_remark`, `oa_url`, `process_instance_id`, `task_id`, `audit_summary`, and `action_result`.

- Modify `apps/local-service/ceo_agent_service/worker.py`
  - Detect OA approval messages before the generic Codex reply path.
  - Invoke the OA runner.
  - Record one audit row with `action="oa_approval"` and the OA metadata fields.
  - Mark the trigger message seen after the OA task is recorded.

- Modify `apps/local-service/ceo_agent_service/audit_web.py`
  - Add OA metadata fields to existing attempt detail rendering.
  - Keep the existing Codex session and tool event cards.

- Modify `apps/local-service/ceo_agent_service/cli.py`
  - Wire the OA runner into `create_worker()` with the skill path default.

- Modify `/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md`
  - Replace the DWS-only constraint with DWS-first plus authorized OA API fallback.
  - Add `--yes` to approval action examples.
  - State that service logs must not contain tokens, AppKey, AppSecret, cookies, OAuth code, or signed URLs.

- Modify `docs/product-logic.md` and `docs/message-routing-rules.md`
  - Document that OA approval cards use the OA agent route and are recorded in existing audit history.

- Test `apps/local-service/tests/test_store.py`
  - OA columns migrate and persist.

- Test `apps/local-service/tests/test_oa_approval.py`
  - OA runner injects the skill, parses output, extracts URLs, and does not require `DwsClient`.

- Test `apps/local-service/tests/test_worker.py`
  - OA approval cards call the OA runner instead of generic reply Codex and record OA fields.

- Test `apps/local-service/tests/test_audit_web.py`
  - Existing attempt detail page shows OA action, remark, URL, and result.

---

### Task 1: Add OA Audit Fields To Existing Reply Attempts

**Files:**
- Modify: `apps/local-service/ceo_agent_service/store.py`
- Test: `apps/local-service/tests/test_store.py`

- [ ] **Step 1: Write the failing persistence test**

Append this test to `apps/local-service/tests/test_store.py`:

```python
def test_reply_attempt_records_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]张静提醒您审批他的录用申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        codex_session_id="session-1",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/query/formService#/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充试用期考核标准和完整面试记录后再提交。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.action == "oa_approval"
    assert loaded.oa_process_instance_id == "proc-1"
    assert loaded.oa_task_id == "task-1"
    assert loaded.oa_url.startswith("https://aflow.dingtalk.com/")
    assert loaded.oa_action == "退回"
    assert loaded.oa_remark == "请补充试用期考核标准和完整面试记录后再提交。"
    assert loaded.oa_action_result_json == '{"errcode":0,"errmsg":"ok"}'
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_store.py::test_reply_attempt_records_oa_metadata -v
```

Expected: FAIL with a `TypeError` for unexpected keyword argument `oa_process_instance_id` or an `AttributeError` for missing `ReplyAttempt.oa_process_instance_id`.

- [ ] **Step 3: Add OA fields to the `ReplyAttempt` model**

In `apps/local-service/ceo_agent_service/store.py`, add these fields to `ReplyAttempt` after `audit_summary`:

```python
    oa_process_instance_id: str = ""
    oa_task_id: str = ""
    oa_url: str = ""
    oa_action: str = ""
    oa_remark: str = ""
    oa_action_result_json: str = ""
```

- [ ] **Step 4: Add SQLite columns and migration**

In the `reply_attempts` table definition in `AutoReplyStore._initialize()`, add the OA columns after `audit_summary`:

```sql
                    oa_process_instance_id text not null default '',
                    oa_task_id text not null default '',
                    oa_url text not null default '',
                    oa_action text not null default '',
                    oa_remark text not null default '',
                    oa_action_result_json text not null default '',
```

In the `reply_attempt_columns` migration loop, add these entries:

```python
                ("oa_process_instance_id", "text not null default ''"),
                ("oa_task_id", "text not null default ''"),
                ("oa_url", "text not null default ''"),
                ("oa_action", "text not null default ''"),
                ("oa_remark", "text not null default ''"),
                ("oa_action_result_json", "text not null default ''"),
```

- [ ] **Step 5: Extend `record_reply_attempt()`**

In `AutoReplyStore.record_reply_attempt()`, add these keyword parameters after `audit_summary`:

```python
        oa_process_instance_id: str = "",
        oa_task_id: str = "",
        oa_url: str = "",
        oa_action: str = "",
        oa_remark: str = "",
        oa_action_result_json: str = "",
```

Add the columns to the insert statement after `audit_summary`:

```sql
                    oa_process_instance_id,
                    oa_task_id,
                    oa_url,
                    oa_action,
                    oa_remark,
                    oa_action_result_json,
```

Add six `?` placeholders to the `values (...)` list after the `audit_summary` placeholder.

Add the values to the parameter tuple after `audit_summary`:

```python
                    oa_process_instance_id,
                    oa_task_id,
                    oa_url,
                    oa_action,
                    oa_remark,
                    oa_action_result_json,
```

- [ ] **Step 6: Extend `update_reply_attempt()`**

Add these optional keyword parameters to `AutoReplyStore.update_reply_attempt()`:

```python
        oa_process_instance_id: str | None = None,
        oa_task_id: str | None = None,
        oa_url: str | None = None,
        oa_action: str | None = None,
        oa_remark: str | None = None,
        oa_action_result_json: str | None = None,
```

Add them to the `(column, value)` loop:

```python
            ("oa_process_instance_id", oa_process_instance_id),
            ("oa_task_id", oa_task_id),
            ("oa_url", oa_url),
            ("oa_action", oa_action),
            ("oa_remark", oa_remark),
            ("oa_action_result_json", oa_action_result_json),
```

- [ ] **Step 7: Run the store test**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_store.py::test_reply_attempt_records_oa_metadata -v
```

Expected: PASS.

- [ ] **Step 8: Run all store tests**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_store.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/store.py apps/local-service/tests/test_store.py
git commit -m "feat: record OA approval metadata on reply attempts"
```

---

### Task 2: Add OA Agent Runner Without DwsClient OA Wrapper

**Files:**
- Create: `apps/local-service/ceo_agent_service/oa_approval.py`
- Create: `apps/local-service/ceo_agent_service/schemas/oa_approval.schema.json`
- Test: `apps/local-service/tests/test_oa_approval.py`

- [ ] **Step 1: Add the OA result schema test**

Create `apps/local-service/tests/test_oa_approval.py` with this initial content:

```python
import json
from pathlib import Path

from ceo_agent_service.oa_approval import (
    OaApprovalCodexRunner,
    OaApprovalResult,
    extract_oa_url,
)


def test_oa_approval_result_requires_three_chinese_actions():
    result = OaApprovalResult.model_validate(
        {
            "process_instance_id": "proc-1",
            "task_id": "task-1",
            "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
            "oa_action": "通过",
            "oa_remark": "材料完整，同意。",
            "action_result": {"errcode": 0, "errmsg": "ok"},
            "audit_summary": "已读取审批详情、流水、任务和附件。",
            "audit_documents": [
                {
                    "title": "审批详情",
                    "url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
                    "relevance": "审批表单事实源",
                }
            ],
        }
    )

    assert result.oa_action == "通过"
    assert result.oa_remark == "材料完整，同意。"


def test_extract_oa_url_prefers_aflow_url_inside_dingtalk_card():
    text = (
        "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery%2Fpchomepage.htm"
        "%3Fswfrom%3Doa%26dinghash%3Dapproval]"
        "(dingtalk://dingtalkclient/action/open_platform_link?x=1)"
    )

    assert extract_oa_url(text).startswith(
        "https://aflow.dingtalk.com/dingtalk/pc/query/pchomepage.htm"
    )


def test_oa_runner_injects_skill_and_schema(tmp_path: Path):
    skill_path = tmp_path / "dingtalk-oa-approval" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        "# DingTalk OA Approval Review\n\n必须完整读取审批详情。",
        encoding="utf-8",
    )
    output = json.dumps(
        {
            "type": "session_meta",
            "payload": {"id": "session-1"},
        }
    ) + "\n" + json.dumps(
        {
            "process_instance_id": "proc-1",
            "task_id": "task-1",
            "oa_url": "https://aflow.dingtalk.com/detail?procInstId=proc-1",
            "oa_action": "退回",
            "oa_remark": "请补充预算来源后重新提交。",
            "action_result": {"errcode": 0, "errmsg": "ok"},
            "audit_summary": "已使用 OA skill 审阅详情并执行退回。",
            "audit_documents": [],
        },
        ensure_ascii=False,
    )
    calls: list[tuple[list[str], str]] = []

    def fake_executor(command: list[str], prompt: str) -> str:
        calls.append((command, prompt))
        return output

    runner = OaApprovalCodexRunner(
        workspace=tmp_path,
        skill_path=skill_path,
        executor=fake_executor,
    )

    result = runner.handle(
        trigger_text="[Ding]审批提醒",
        context_text="上下文",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
    )

    command, prompt = calls[0]
    joined_command = " ".join(command)
    assert "--output-schema" in command
    assert "oa_approval.schema.json" in joined_command
    assert "必须完整读取审批详情" in joined_command
    assert "上下文" in prompt
    assert result.oa_action == "退回"
    assert runner.last_session_id == "session-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_oa_approval.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ceo_agent_service.oa_approval'`.

- [ ] **Step 3: Create the JSON schema**

Create `apps/local-service/ceo_agent_service/schemas/oa_approval.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CEO Agent OA Approval Result",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "process_instance_id",
    "task_id",
    "oa_url",
    "oa_action",
    "oa_remark",
    "action_result",
    "audit_summary",
    "audit_documents"
  ],
  "properties": {
    "process_instance_id": {
      "type": "string"
    },
    "task_id": {
      "type": "string"
    },
    "oa_url": {
      "type": "string"
    },
    "oa_action": {
      "type": "string",
      "enum": ["通过", "拒绝", "退回"]
    },
    "oa_remark": {
      "type": "string",
      "minLength": 1
    },
    "action_result": {
      "type": "object"
    },
    "audit_summary": {
      "type": "string",
      "minLength": 1
    },
    "audit_documents": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "url", "relevance"],
        "properties": {
          "title": {
            "type": "string"
          },
          "url": {
            "type": "string"
          },
          "relevance": {
            "type": "string"
          }
        }
      }
    }
  }
}
```

- [ ] **Step 4: Create `oa_approval.py`**

Create `apps/local-service/ceo_agent_service/oa_approval.py`:

```python
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, Field, ValidationError

from ceo_agent_service.codex_decision import (
    extract_codex_audit_events,
    extract_codex_session_id,
)
from ceo_agent_service.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
)
from ceo_agent_service.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    _config_string,
)


OA_APPROVAL_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "oa_approval.schema.json"
)
DEFAULT_OA_APPROVAL_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtalk-oa-approval" / "SKILL.md"
)
AFLOW_URL_PATTERN = re.compile(r"https?://aflow\.dingtalk\.com/[^\s)\]]+")
ENCODED_AFLOW_URL_PATTERN = re.compile(
    r"https%3A%2F%2Faflow\.dingtalk\.com[^\s)\]]+",
    re.IGNORECASE,
)


class OaApprovalResult(BaseModel):
    process_instance_id: str = ""
    task_id: str = ""
    oa_url: str = ""
    oa_action: Literal["通过", "拒绝", "退回"]
    oa_remark: str
    action_result: dict[str, Any] = Field(default_factory=dict)
    audit_summary: str
    audit_documents: list[dict[str, str]] = Field(default_factory=list)


def extract_oa_url(text: str) -> str:
    direct = AFLOW_URL_PATTERN.search(text)
    if direct:
        return direct.group(0)
    encoded = ENCODED_AFLOW_URL_PATTERN.search(text)
    if encoded:
        return unquote(encoded.group(0))
    return ""


def _oa_developer_instructions(skill_path: Path) -> str:
    skill = skill_path.read_text(encoding="utf-8")
    return (
        "You are the local CEO DingTalk OA approval worker. "
        "Use the embedded dingtalk-oa-approval skill exactly for this task. "
        "Return only the requested JSON. Do not expose tokens, AppKey, AppSecret, "
        "cookies, OAuth code, signed download URLs, local paths, or tool credentials.\n\n"
        "# Embedded Skill: dingtalk-oa-approval\n\n"
        f"{skill}"
    )


def _decision_from_payload(payload: Any) -> OaApprovalResult | None:
    if isinstance(payload, dict):
        try:
            return OaApprovalResult.model_validate(payload)
        except ValidationError:
            pass
        for text in _text_candidates(payload):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            try:
                return OaApprovalResult.model_validate(parsed)
            except ValidationError:
                continue
    return None


def _text_candidates(payload: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("message", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    candidates.append(item["text"])
    item = payload.get("item")
    if isinstance(item, dict) and isinstance(item.get("text"), str):
        candidates.append(item["text"])
    nested = payload.get("payload")
    if isinstance(nested, dict):
        candidates.extend(_text_candidates(nested))
    return candidates


def parse_oa_approval_json(raw: str) -> OaApprovalResult:
    payloads: list[Any] = []
    stripped = raw.strip()
    if not stripped:
        raise json.JSONDecodeError("No OA approval JSON found", raw, 0)
    try:
        payloads.append(json.loads(stripped))
    except json.JSONDecodeError:
        for line in stripped.splitlines():
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for payload in reversed(payloads):
        decision = _decision_from_payload(payload)
        if decision is not None:
            return decision
    raise json.JSONDecodeError("No OA approval JSON found", raw, 0)


class OaApprovalCodexRunner:
    def __init__(
        self,
        workspace: Path,
        skill_path: Path = DEFAULT_OA_APPROVAL_SKILL_PATH,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str], str] | None = None,
        timeout_seconds: int = 600,
        codex_home: Path | None = None,
    ):
        self.workspace = workspace
        self.skill_path = skill_path
        self.codex_bin = codex_bin
        self.executor = executor or self._subprocess_executor
        self.timeout_seconds = timeout_seconds
        self.codex_home = codex_home
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def handle(
        self,
        *,
        trigger_text: str,
        context_text: str,
        oa_url: str,
    ) -> OaApprovalResult:
        self.last_session_id = None
        self.last_audit_tool_events = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0
        prompt = self._prompt(
            trigger_text=trigger_text,
            context_text=context_text,
            oa_url=oa_url,
        )
        command = self._build_command()
        raw = self.executor(command, prompt)
        self.last_session_id = extract_codex_session_id(raw)
        self.last_transcript_end_line = self._session_line_count(self.last_session_id)
        self._remember_audit_tool_events(raw)
        return parse_oa_approval_json(raw)

    def _build_command(self) -> list[str]:
        return [
            self.codex_bin,
            "exec",
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
            _config_string(
                "developer_instructions",
                _oa_developer_instructions(self.skill_path),
            ),
            "-c",
            'model_reasoning_summary="concise"',
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--output-schema",
            str(OA_APPROVAL_SCHEMA_PATH),
            "--cd",
            str(self.workspace),
            "-",
        ]

    @staticmethod
    def _prompt(*, trigger_text: str, context_text: str, oa_url: str) -> str:
        return "\n".join(
            [
                "当前任务：处理一条钉钉 OA 审批任务。",
                "必须使用 embedded dingtalk-oa-approval skill 的流程。",
                "最终 oa_action 只能是：通过、拒绝、退回。",
                "如果材料不足但流程需要处理，oa_action 使用 退回，并在 oa_remark 写清需要补充的材料。",
                "如果已执行审批动作，action_result 填写实际命令或 API 返回 JSON；如果只完成审阅未执行，action_result 填写 {}。",
                f"审批链接：{oa_url}",
                "触发消息：",
                trigger_text,
                "上下文消息：",
                context_text,
            ]
        )

    def _subprocess_executor(self, command: list[str], prompt: str) -> str:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            input=prompt,
            env=os.environ.copy(),
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = " ".join(line.strip() for line in completed.stderr.splitlines() if line.strip())
            raise RuntimeError(stderr[:1200] or f"codex exec failed with return code {completed.returncode}")
        return completed.stdout.strip()

    def _remember_audit_tool_events(self, raw: str) -> None:
        session_events = []
        if self.last_session_id:
            session_events = extract_codex_audit_events_from_session(
                self.last_session_id,
                codex_home=self.codex_home,
                start_line=self.last_transcript_start_line,
                end_line=self.last_transcript_end_line,
            )
        self.last_audit_tool_events = session_events or extract_codex_audit_events(raw)

    def _session_line_count(self, session_id: str | None) -> int:
        if not session_id:
            return 0
        return count_codex_session_lines(session_id, codex_home=self.codex_home)
```

- [ ] **Step 5: Run OA runner tests**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_oa_approval.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/oa_approval.py apps/local-service/ceo_agent_service/schemas/oa_approval.schema.json apps/local-service/tests/test_oa_approval.py
git commit -m "feat: add OA approval agent runner"
```

---

### Task 3: Route OA Approval Messages To The OA Agent

**Files:**
- Modify: `apps/local-service/ceo_agent_service/cli.py`
- Modify: `apps/local-service/ceo_agent_service/worker.py`
- Test: `apps/local-service/tests/test_worker.py`

- [ ] **Step 1: Write the worker routing test**

Append this fake runner class near the other fake helpers in `apps/local-service/tests/test_worker.py`:

```python
class FakeOaApprovalRunner:
    def __init__(self):
        self.calls: list[dict[str, str]] = []
        self.last_session_id = "oa-session-1"
        self.last_transcript_start_line = 2
        self.last_transcript_end_line = 9
        self.last_audit_tool_events = [
            {
                "event_type": "item.completed",
                "tool": "exec_command",
                "command": "dws oa approval tasks --instance-id proc-1 --format json",
            }
        ]

    def handle(self, *, trigger_text: str, context_text: str, oa_url: str):
        self.calls.append(
            {
                "trigger_text": trigger_text,
                "context_text": context_text,
                "oa_url": oa_url,
            }
        )
        from ceo_agent_service.oa_approval import OaApprovalResult

        return OaApprovalResult(
            process_instance_id="proc-1",
            task_id="task-1",
            oa_url=oa_url,
            oa_action="退回",
            oa_remark="请补充预算来源和项目归属后重新提交。",
            action_result={"errcode": 0, "errmsg": "ok"},
            audit_summary="已按 OA skill 审阅并执行退回。",
            audit_documents=[
                {
                    "title": "审批详情",
                    "url": oa_url,
                    "relevance": "审批事实源",
                }
            ],
        )
```

Add this test next to `test_structured_approval_card_is_processed_by_codex`:

```python
def test_structured_approval_card_is_processed_by_oa_runner(tmp_path: Path, monkeypatch):
    trigger = message(
        "\n".join(
            [
                "闫成成提交的项目立项全流程（第一曲线）",
                "项目经理: 闫成成",
                "销售经理: 曹宇航",
                "项目类型: 点云;图片;视频",
                "总预估数据量: 2546573",
                "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
                "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
                "%2Fpchomepage.htm%3Fswfrom%3Doa%26dinghash%3Dapproval]"
                "(dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    oa_runner = FakeOaApprovalRunner()
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.oa_approval_runner = oa_runner

    worker.run_once()

    assert codex.calls == []
    assert len(oa_runner.calls) == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "oa_approval"
    assert attempt.send_status == "skipped"
    assert attempt.codex_session_id == "oa-session-1"
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_action == "退回"
    assert attempt.oa_remark == "请补充预算来源和项目归属后重新提交。"
    assert attempt.oa_url.startswith("https://aflow.dingtalk.com/")
    assert attempt.oa_action_result_json == '{"errcode": 0, "errmsg": "ok"}'
    assert worker.store.has_seen("msg-1") is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_worker.py::test_structured_approval_card_is_processed_by_oa_runner -v
```

Expected: FAIL because `DingTalkAutoReplyWorker` does not call `oa_approval_runner`.

- [ ] **Step 3: Add OA runner dependency to the worker**

In `apps/local-service/ceo_agent_service/worker.py`, import the OA helpers:

```python
from ceo_agent_service.oa_approval import OaApprovalCodexRunner, extract_oa_url
```

Add an optional constructor parameter after `codex`:

```python
        oa_approval_runner: OaApprovalCodexRunner | None = None,
```

Assign it in `__init__` after `self.codex = codex`:

```python
        self.oa_approval_runner = oa_approval_runner
```

- [ ] **Step 4: Route OA messages before generic Codex reply handling**

In `_process_queued_task()`, insert this block after the calendar handler and before `_is_system_or_notification_message(trigger)`:

```python
        if self._handle_oa_approval_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
        ):
            return
```

In `rerun_message()`, insert this block after the calendar handler and before `_is_system_or_notification_message(trigger)`:

```python
        if self._handle_oa_approval_if_actionable(
            conversation,
            trigger,
            prompt_context_messages,
        ):
            return trigger.open_message_id
```

Add this method to `DingTalkAutoReplyWorker` near the other `_handle_*_if_actionable` methods:

```python
    def _handle_oa_approval_if_actionable(
        self,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        context_messages: list[DingTalkMessage],
    ) -> bool:
        if not DINGTALK_APPROVAL_LINK_PATTERN.search(trigger.content):
            return False
        oa_runner = self.oa_approval_runner
        if oa_runner is None:
            return False
        if self._handle_existing_attempt(conversation, trigger, [trigger]):
            return True
        oa_url = extract_oa_url(trigger.content)
        context_text = "\n".join(
            "\n".join(message_lines(message)) for message in context_messages
        )
        result = oa_runner.handle(
            trigger_text=trigger.content,
            context_text=context_text,
            oa_url=oa_url,
        )
        attempt_id = self.store.record_reply_attempt(
            conversation_id=conversation.open_conversation_id,
            conversation_title=conversation.title,
            trigger_message_id=trigger.open_message_id,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            action="oa_approval",
            sensitivity_kind="internal_personnel",
            codex_reason="oa approval handled by dingtalk-oa-approval skill",
            draft_reply_text=result.oa_remark,
            codex_session_id=getattr(oa_runner, "last_session_id", "") or "",
            codex_transcript_start_line=getattr(
                oa_runner, "last_transcript_start_line", 0
            ),
            codex_transcript_end_line=getattr(
                oa_runner, "last_transcript_end_line", 0
            ),
            audit_documents_json=json.dumps(
                result.audit_documents,
                ensure_ascii=False,
            ),
            audit_tool_events_json=json.dumps(
                getattr(oa_runner, "last_audit_tool_events", []),
                ensure_ascii=False,
            ),
            audit_summary=result.audit_summary,
            oa_process_instance_id=result.process_instance_id,
            oa_task_id=result.task_id,
            oa_url=result.oa_url or oa_url,
            oa_action=result.oa_action,
            oa_remark=result.oa_remark,
            oa_action_result_json=json.dumps(
                result.action_result,
                ensure_ascii=False,
            ),
            send_status="skipped",
        )
        self.store.update_reply_attempt(
            attempt_id,
            final_reply_text=result.oa_remark,
        )
        self._mark_seen([trigger])
        return True
```

Also import `message_lines` from `ceo_agent_service.prompt` in the existing prompt import:

```python
from ceo_agent_service.prompt import LinkedDocumentContext, build_turn_prompt, message_lines
```

- [ ] **Step 5: Wire the OA runner in `create_worker()`**

In `apps/local-service/ceo_agent_service/cli.py`, import:

```python
from ceo_agent_service.oa_approval import OaApprovalCodexRunner
```

In `create_worker()`, instantiate the runner after `codex = CodexDecisionRunner(...)`:

```python
    oa_approval_runner = OaApprovalCodexRunner(
        workspace=settings.workspace,
        timeout_seconds=settings.codex_timeout_seconds,
    )
```

Pass it to the worker constructor:

```python
        oa_approval_runner=oa_approval_runner,
```

- [ ] **Step 6: Run the OA worker test**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_worker.py::test_structured_approval_card_is_processed_by_oa_runner -v
```

Expected: PASS.

- [ ] **Step 7: Run worker routing tests around OA and ordinary approval reminders**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest \
  tests/test_worker.py::test_structured_approval_card_is_processed_by_oa_runner \
  tests/test_worker.py::test_structured_link_card_is_skipped_before_codex \
  tests/test_worker.py::test_ding_approval_reminder_is_processed_by_codex \
  -v
```

Expected: PASS. If `test_ding_approval_reminder_is_processed_by_codex` now routes to the OA runner by design, update that test to install `FakeOaApprovalRunner` and assert `action == "oa_approval"` because approval reminders should use the OA route once the OA runner is available.

- [ ] **Step 8: Commit**

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/worker.py apps/local-service/ceo_agent_service/cli.py apps/local-service/tests/test_worker.py
git commit -m "feat: route OA approval cards through skill agent"
```

---

### Task 4: Show OA Metadata In Existing Attempt Detail

**Files:**
- Modify: `apps/local-service/ceo_agent_service/audit_web.py`
- Test: `apps/local-service/tests/test_audit_web.py`

- [ ] **Step 1: Write the audit rendering test**

Append this test to `apps/local-service/tests/test_audit_web.py`:

```python
def test_attempt_detail_renders_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]审批提醒",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1",
        oa_action="通过",
        oa_remark="材料完整，同意。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    status, html = render_attempt_detail(store, attempt_id)

    assert status == 200
    assert "OA approval" in html
    assert "proc-1" in html
    assert "task-1" in html
    assert "通过" in html
    assert "材料完整，同意。" in html
    assert "https://aflow.dingtalk.com/detail?procInstId=proc-1" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_audit_web.py::test_attempt_detail_renders_oa_metadata -v
```

Expected: FAIL because attempt detail does not render OA metadata.

- [ ] **Step 3: Add an OA metadata card**

In `apps/local-service/ceo_agent_service/audit_web.py`, add this helper near `_quality_warning_card()`:

```python
def _oa_metadata_card(attempt: ReplyAttempt) -> str:
    if not any(
        value.strip()
        for value in (
            attempt.oa_process_instance_id,
            attempt.oa_task_id,
            attempt.oa_url,
            attempt.oa_action,
            attempt.oa_remark,
            attempt.oa_action_result_json,
        )
    ):
        return ""
    rows = "".join(
        f"<div class=\"muted\">{escape(label)}</div><div>{escape(value)}</div>"
        for label, value in (
            ("process instance", attempt.oa_process_instance_id),
            ("task id", attempt.oa_task_id),
            ("url", attempt.oa_url),
            ("action", attempt.oa_action),
            ("remark", attempt.oa_remark),
        )
    )
    return (
        "<section class=\"card compact-card\"><h2>OA approval</h2>"
        f"<div class=\"grid\">{rows}</div>"
        f"{_json_card('OA action result', attempt.oa_action_result_json)}"
        "</section>"
    )
```

In `_attempt_detail_body()`, insert the OA card after `_quality_warning_card(attempt)`:

```python
        f"{_oa_metadata_card(attempt)}"
```

- [ ] **Step 4: Run the audit rendering test**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_audit_web.py::test_attempt_detail_renders_oa_metadata -v
```

Expected: PASS.

- [ ] **Step 5: Run focused audit web tests**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest tests/test_audit_web.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/audit_web.py apps/local-service/tests/test_audit_web.py
git commit -m "feat: show OA approval metadata in audit detail"
```

---

### Task 5: Align The OA Skill With Current API Reality

**Files:**
- Modify: `/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md`

- [ ] **Step 1: Update the DWS/OpenAPI rule text**

In `/Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md`, replace the `OpenAPI 详情补读` intro line with:

```markdown
当 DWS 详情为空、字段丢失、或任务归属异常时，允许调用已授权的钉钉 OA OpenAPI/API 作为补充事实源。当前 OA 详情不完整时不能强行只用 DWS；以后如果 DWS 新增完整详情能力，再优先收敛到 DWS。前提是用户已授权，且应用已开通 `qyapi_aflow` 权限。
```

- [ ] **Step 2: Add explicit secret logging rule**

Under the existing OpenAPI section, keep the existing secret warning and add:

```markdown
- 在 `ceo-agent-service` 自动化里，API token、AppKey、AppSecret、cookie、OAuth code、签名下载 URL 不得写入 SQLite、日志、报告、DingTalk 回复、agent 输出或 `audit_summary`；只能记录“已使用授权 API 补读详情”这类事实。
```

- [ ] **Step 3: Add `--yes` to mutating command examples**

Change the approve example to:

```bash
dws oa approval approve --instance-id <processInstanceId> --task-id <taskId> --remark "<审批意见>" --format json --yes
```

Change the reject/return example to:

```bash
dws oa approval reject --instance-id <processInstanceId> --task-id <taskId> --remark "<明确理由和需补充材料>" --format json --yes
```

- [ ] **Step 4: Verify the skill contains the aligned rules**

Run:

```bash
rg -n "允许调用已授权的钉钉 OA OpenAPI|不得写入 SQLite|--format json --yes" /Users/derek/.agents/skills/dingtalk-oa-approval/SKILL.md
```

Expected: three matching lines or more.

- [ ] **Step 5: Commit repository changes only**

The skill file is outside this Git repository. Do not include it in the repo commit. Record in the final implementation summary that the global skill file was updated outside the repo.

---

### Task 6: Document The Route And Run The Focused Suite

**Files:**
- Modify: `docs/product-logic.md`
- Modify: `docs/message-routing-rules.md`

- [ ] **Step 1: Update product logic**

In `docs/product-logic.md`, replace the OA paragraph under `Safety Defaults` with:

```markdown
- DingTalk media/calendar placeholders and DingTalk internal link-only cards are
  skipped before Codex, except approval/OA links.
- OA approval cards and reminders are routed to a dedicated OA approval agent.
  The service injects the `dingtalk-oa-approval` skill into that agent, records
  the Codex session, tool events, approval URL, approval action, approval remark,
  and action result on the existing reply attempt audit row, and does not create
  a separate OA audit page.
- The OA agent may use authorized DingTalk OA API detail reads when DWS does not
  return complete approval detail. Secrets and signed URLs must not be written
  to logs, SQLite, audit summaries, reports, or DingTalk replies.
```

- [ ] **Step 2: Update message routing rules**

In `docs/message-routing-rules.md`, in the `审批/OA 链接例外` section, replace `当前行为：agent_review` with:

```markdown
当前行为：`oa_agent_review` when the OA runner is configured; otherwise `agent_review`.
```

Replace the route requirements list with:

```markdown
- 不在 agent 前跳过。
- 配置了 OA runner 时，交给专用 OA agent；该 agent 注入 `dingtalk-oa-approval` skill。
- OA agent 最终记录的审批动作只能是 `通过`、`拒绝`、`退回`。
- 服务在既有 `reply_attempts` 审计记录中保存审批 URL、审批动作、审批留言、执行结果、Codex session 和工具事件。
- 不新增 OA 页面；从既有 attempt detail 查看处理过程。
- DWS 详情不完整时，OA agent 可使用已授权的钉钉 OA API 补读详情，但不得记录 token、AppKey、AppSecret、cookie、OAuth code 或签名 URL。
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest \
  tests/test_store.py \
  tests/test_oa_approval.py \
  tests/test_worker.py::test_structured_approval_card_is_processed_by_oa_runner \
  tests/test_audit_web.py::test_attempt_detail_renders_oa_metadata \
  -v
```

Expected: PASS.

- [ ] **Step 4: Run the broader local-service suite**

Run:

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m pytest -v
```

Expected: PASS. If the full suite is too slow for the current run, finish with the focused suite result and explicitly report that the full suite was not run.

- [ ] **Step 5: Commit**

```bash
cd /Users/derek/Documents/Projects/ceo-agent-service
git add docs/product-logic.md docs/message-routing-rules.md
git commit -m "docs: document OA approval agent route"
```

---

## Self-Review

- Spec coverage:
  - Uses `dingtalk-oa-approval` skill through a task-specific agent: Task 2 and Task 3.
  - Records OA action, remark, URL, result, Codex session, and tool events: Task 1, Task 3, Task 4.
  - Keeps final action to `通过` / `拒绝` / `退回`: Task 2 schema and tests.
  - Does not add OA page: Task 4 only extends existing attempt detail.
  - Does not add DWS client OA methods: Task 2 runner lets the agent call tools directly.
  - Allows authorized OA API when DWS detail is incomplete: Task 5 and Task 6.

- Placeholder scan:
  - The plan contains no banned placeholder markers, no open-ended validation instruction, and no step that asks for unspecified tests.

- Type consistency:
  - Store fields use `oa_process_instance_id`, `oa_task_id`, `oa_url`, `oa_action`, `oa_remark`, and `oa_action_result_json` across model, migration, insertion, update, worker, and audit rendering.
  - Agent result fields use `process_instance_id`, `task_id`, `oa_url`, `oa_action`, `oa_remark`, `action_result`, `audit_summary`, and `audit_documents`; worker maps these to store fields.
