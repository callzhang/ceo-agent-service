# Memory Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give CEO service Codex sessions explicit `memory_connector` MCP access and persist each real reply/review event as a durable memory episode through a retryable local outbox.

**Architecture:** Keep memory recall as an agent tool, not a pre-injected prompt block. Reply delivery enqueues complete event episodes into SQLite after the service knows the final outcome; a separate flush command calls `memory_write` and records success or failure without changing DingTalk reply state.

**Tech Stack:** Python, SQLite, pytest, Codex CLI config flags, MCP streamable HTTP JSON-RPC.

---

## File Structure

- Modify `apps/local-service/ceo_agent_service/codex_runner.py`
  - Owns `codex exec` command generation.
  - Add explicit `memory_connector` MCP config flags while preserving `--ignore-user-config` and global plugin isolation.
  - Add environment loading from `~/.codex/memory_connector.env`.

- Modify `apps/local-service/ceo_agent_service/store.py`
  - Owns SQLite schema and persistence methods.
  - Add `MemoryWriteEvent` model, table migration, enqueue/list/claim/success/failure helpers.

- Create `apps/local-service/ceo_agent_service/memory_events.py`
  - Owns construction of complete JSON episode payloads from `ReplyAttempt`, `SentReply`, and conversation metadata.
  - Keeps event formatting out of `worker.py`, `audit_web.py`, and `cli.py`.

- Create `apps/local-service/ceo_agent_service/memory_connector.py`
  - Owns the minimal MCP JSON-RPC client for `memory_write`.
  - Uses installed env values: `MEMORY_CONNECTOR_URL`, `CONNECTOR_API_KEY`, `MEMORY_CONNECTOR_USER_ID`.

- Create `apps/local-service/ceo_agent_service/memory_flush.py`
  - Owns `flush_memory_events(store, client, limit)` orchestration.
  - Converts outbox rows into `memory_write` calls and updates status.

- Modify `apps/local-service/ceo_agent_service/worker.py`
  - Enqueue `reply_sent` events after real DingTalk send success and after successful handoff ack.
  - Do not enqueue for dry-run, failed, blocked, stop-with-error, or no-reply.

- Modify `apps/local-service/ceo_agent_service/audit_web.py`
  - Enqueue `review_correction` after feedback is recorded.
  - Show memory event state on attempt detail.

- Modify `apps/local-service/ceo_agent_service/cli.py`
  - Add `flush-memory-events`.
  - Enqueue `review_correction` from CLI feedback.

- Modify tests:
  - `apps/local-service/tests/test_codex_runner.py`
  - `apps/local-service/tests/test_store.py`
  - `apps/local-service/tests/test_worker.py`
  - `apps/local-service/tests/test_audit_web.py`
  - `apps/local-service/tests/test_cli.py`
  - Add `apps/local-service/tests/test_memory_connector.py`
  - Add `apps/local-service/tests/test_memory_flush.py`

---

## Task 1: Expose Memory MCP In Codex Exec

**Files:**
- Modify: `apps/local-service/ceo_agent_service/codex_runner.py`
- Test: `apps/local-service/tests/test_codex_runner.py`

- [ ] **Step 1: Write failing command test**

Add this test to `apps/local-service/tests/test_codex_runner.py`:

```python
def test_codex_command_exposes_memory_connector_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONNECTOR_URL", "https://memory.example/mcp/")
    monkeypatch.setenv("CONNECTOR_API_KEY", "secret-token")
    monkeypatch.setenv("MEMORY_CONNECTOR_USER_ID", "principal")

    runner = CodexRunner(workspace=tmp_path, codex_bin="codex")
    command = runner.build_command("prompt", session_id=None)

    assert "--ignore-user-config" in command
    assert command[command.index("--disable") + 1] == "plugins"
    joined = "\n".join(command)
    assert 'mcp_servers.memory_connector.url="https://memory.example/mcp/"' in joined
    assert 'mcp_servers.memory_connector.bearer_token_env_var="CONNECTOR_API_KEY"' in joined
    assert 'mcp_servers.memory_connector.env_http_headers={"x-memory-user-id" = "MEMORY_CONNECTOR_USER_ID"}' in joined
```

- [ ] **Step 2: Write failing env test**

Add this test to `apps/local-service/tests/test_codex_runner.py`:

```python
def test_codex_runner_env_loads_memory_connector_env_file(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "memory_connector.env").write_text(
        "\n".join(
            [
                "export CONNECTOR_API_KEY='secret-token'",
                "export MEMORY_CONNECTOR_URL='https://memory.example/mcp/'",
                "export MEMORY_CONNECTOR_USER_ID='principal'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_URL", raising=False)
    monkeypatch.delenv("MEMORY_CONNECTOR_USER_ID", raising=False)

    env = CodexRunner(workspace=tmp_path).build_env()

    assert env["CONNECTOR_API_KEY"] == "secret-token"
    assert env["MEMORY_CONNECTOR_URL"] == "https://memory.example/mcp/"
    assert env["MEMORY_CONNECTOR_USER_ID"] == "principal"
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_codex_runner.py -q
```

Expected: FAIL because the command still lacks memory MCP config and `build_env()` does not load `memory_connector.env`.

- [ ] **Step 4: Implement command and env support**

In `apps/local-service/ceo_agent_service/codex_runner.py`, add imports:

```python
import shlex
```

Add helpers near `_config_string()`:

```python
def _codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))


def _read_export_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in {
            "CONNECTOR_API_KEY",
            "MEMORY_CONNECTOR_URL",
            "MEMORY_CONNECTOR_USER_ID",
        }:
            continue
        try:
            parts = shlex.split(raw_value)
        except ValueError:
            parts = [raw_value.strip().strip("'\"")]
        values[key] = parts[0] if parts else ""
    return values


def memory_connector_env() -> dict[str, str]:
    values = _read_export_env_file(_codex_home() / "memory_connector.env")
    for key in (
        "CONNECTOR_API_KEY",
        "MEMORY_CONNECTOR_URL",
        "MEMORY_CONNECTOR_USER_ID",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    values.setdefault("MEMORY_CONNECTOR_USER_ID", "principal")
    return values


def memory_connector_config_options() -> list[str]:
    env = memory_connector_env()
    url = env.get("MEMORY_CONNECTOR_URL", "").strip()
    if not url:
        return []
    return [
        "-c",
        _config_string("mcp_servers.memory_connector.url", url),
        "-c",
        _config_string(
            "mcp_servers.memory_connector.bearer_token_env_var",
            "CONNECTOR_API_KEY",
        ),
        "-c",
        'mcp_servers.memory_connector.env_http_headers={"x-memory-user-id" = "MEMORY_CONNECTOR_USER_ID"}',
    ]
```

Change `CodexRunner.build_env()`:

```python
    def build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(memory_connector_env())
        return env
```

Add `*memory_connector_config_options(),` inside `common_options` after `--ignore-rules`.

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_codex_runner.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/codex_runner.py apps/local-service/tests/test_codex_runner.py
git commit -m "Expose memory MCP to Codex runner"
```

---

## Task 2: Add Memory Outbox Store

**Files:**
- Modify: `apps/local-service/ceo_agent_service/store.py`
- Test: `apps/local-service/tests/test_store.py`

- [ ] **Step 1: Write failing store tests**

Add these tests to `apps/local-service/tests/test_store.py`:

```python
def test_memory_write_event_round_trip_and_dedupes_by_attempt_event(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event_id = store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    duplicate_id = store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent","updated":true}',
    )

    events = store.list_memory_write_events(statuses=("pending",))

    assert duplicate_id == event_id
    assert len(events) == 1
    assert events[0].attempt_id == 7
    assert events[0].event_type == "reply_sent"
    assert events[0].payload_json == '{"event":"reply_sent","updated":true}'
    assert events[0].status == "pending"


def test_claim_memory_write_events_marks_processing(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event_id = store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )

    claimed = store.claim_memory_write_events(limit=1)
    second_claim = store.claim_memory_write_events(limit=1)

    assert [event.id for event in claimed] == [event_id]
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert second_claim == []


def test_memory_write_event_success_and_failure_updates_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    success_id = store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    failure_id = store.enqueue_memory_write_event(
        attempt_id=8,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    store.claim_memory_write_events(limit=2)

    store.mark_memory_write_event_sent(success_id, memory_episode_id="episode-1")
    store.mark_memory_write_event_failed(failure_id, "HTTP 502")

    events = {event.id: event for event in store.list_memory_write_events()}
    assert events[success_id].status == "sent"
    assert events[success_id].memory_episode_id == "episode-1"
    assert events[failure_id].status == "failed"
    assert events[failure_id].last_error == "HTTP 502"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_store.py -q
```

Expected: FAIL because `MemoryWriteEvent` and store methods do not exist.

- [ ] **Step 3: Implement model, table, and methods**

In `apps/local-service/ceo_agent_service/store.py`, add model after `ReplyTask`:

```python
class MemoryWriteEvent(BaseModel):
    id: int
    attempt_id: int
    event_type: str
    payload_json: str
    status: str
    attempts: int
    last_error: str = ""
    memory_episode_id: str = ""
    created_at: str
    updated_at: str
```

Add table creation in `_initialize()`:

```sql
                create table if not exists memory_write_events (
                    id integer primary key autoincrement,
                    attempt_id integer not null,
                    event_type text not null,
                    payload_json text not null,
                    status text not null default 'pending',
                    attempts integer not null default 0,
                    last_error text not null default '',
                    memory_episode_id text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    unique(attempt_id, event_type)
                );
                create index if not exists idx_memory_write_events_status
                    on memory_write_events(status, id);
```

Add methods near other reply attempt helpers:

```python
    def enqueue_memory_write_event(
        self,
        *,
        attempt_id: int,
        event_type: str,
        payload_json: str,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into memory_write_events (
                    attempt_id, event_type, payload_json, status, last_error,
                    memory_episode_id
                )
                values (?, ?, ?, 'pending', '', '')
                on conflict(attempt_id, event_type) do update set
                    payload_json=excluded.payload_json,
                    status='pending',
                    last_error='',
                    updated_at=current_timestamp
                returning id
                """,
                (attempt_id, event_type, payload_json),
            )
            return int(cursor.fetchone()["id"])

    def list_memory_write_events(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[MemoryWriteEvent]:
        query = "select * from memory_write_events"
        args: list[object] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" where status in ({placeholders})"
            args.extend(statuses)
        query += " order by id"
        if limit is not None:
            query += " limit ?"
            args.append(limit)
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
            return [MemoryWriteEvent.model_validate(dict(row)) for row in rows]

    def get_memory_write_events_for_attempt(
        self, attempt_id: int
    ) -> list[MemoryWriteEvent]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from memory_write_events
                where attempt_id=?
                order by id
                """,
                (attempt_id,),
            ).fetchall()
            return [MemoryWriteEvent.model_validate(dict(row)) for row in rows]

    def claim_memory_write_events(self, *, limit: int) -> list[MemoryWriteEvent]:
        with self._connect() as db:
            rows = db.execute(
                """
                select id
                from memory_write_events
                where status in ('pending', 'failed')
                order by id
                limit ?
                """,
                (limit,),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return []
            placeholders = ", ".join("?" for _ in ids)
            db.execute(
                f"""
                update memory_write_events
                set status='processing',
                    attempts=attempts + 1,
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                ids,
            )
            rows = db.execute(
                f"""
                select *
                from memory_write_events
                where id in ({placeholders})
                order by id
                """,
                ids,
            ).fetchall()
            return [MemoryWriteEvent.model_validate(dict(row)) for row in rows]

    def mark_memory_write_event_sent(
        self,
        event_id: int,
        *,
        memory_episode_id: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                update memory_write_events
                set status='sent',
                    memory_episode_id=?,
                    last_error='',
                    updated_at=current_timestamp
                where id=?
                """,
                (memory_episode_id, event_id),
            )

    def mark_memory_write_event_failed(self, event_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update memory_write_events
                set status='failed',
                    last_error=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (error, event_id),
            )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/store.py apps/local-service/tests/test_store.py
git commit -m "Add memory write outbox store"
```

---

## Task 3: Build Complete Memory Episode Payloads

**Files:**
- Create: `apps/local-service/ceo_agent_service/memory_events.py`
- Test: `apps/local-service/tests/test_memory_events.py`

- [ ] **Step 1: Write failing payload tests**

Create `apps/local-service/tests/test_memory_events.py`:

```python
from ceo_agent_service.memory_events import (
    build_reply_sent_memory_payload,
    build_review_correction_memory_payload,
)
from ceo_agent_service.store import ReplyAttempt, SentReply


def make_attempt(**overrides):
    payload = {
        "id": 7,
        "conversation_id": "cid-1",
        "conversation_title": "Friday",
        "trigger_message_id": "msg-1",
        "trigger_sender": "Han Lu",
        "trigger_text": "@Alex Chen 看下这个方案",
        "action": "send_reply",
        "sensitivity_kind": "general",
        "codex_reason": "需要给出判断",
        "draft_reply_text": "可以推进",
        "direct_user_id": "",
        "direct_open_dingtalk_id": "",
        "codex_session_id": "session-1",
        "codex_transcript_start_line": 10,
        "codex_transcript_end_line": 20,
        "audit_documents_json": "[]",
        "audit_tool_events_json": "[]",
        "audit_summary": "基于当前上下文判断。",
        "oa_process_instance_id": "",
        "oa_task_id": "",
        "oa_url": "",
        "oa_action": "",
        "oa_remark": "",
        "oa_action_result_json": "",
        "final_reply_text": "可以推进，先把边界收清楚。",
        "permission_action": "",
        "permission_reason": "",
        "send_status": "sent",
        "send_error": "",
        "retry_count": 0,
        "reviewed_at": None,
        "reviewer_feedback": "",
        "corrected_reply_text": "",
        "created_at": "2026-05-29 10:00:00",
        "updated_at": "2026-05-29 10:01:00",
    }
    payload.update(overrides)
    return ReplyAttempt.model_validate(payload)


def test_build_reply_sent_memory_payload_records_complete_event():
    attempt = make_attempt()
    sent = SentReply(
        id=3,
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="可以推进，先把边界收清楚。",
        send_result_json='{"result":{"processQueryKey":"key-1"}}',
        recall_key="key-1",
        recall_status="",
        recall_error="",
        recalled_at=None,
        sent_at="2026-05-29 10:01:30",
    )

    payload = build_reply_sent_memory_payload(attempt, sent)

    assert payload["event"] == "reply_sent"
    assert payload["conversation"]["title"] == "Friday"
    assert payload["trigger"]["sender"] == "Han Lu"
    assert payload["decision"]["action"] == "send_reply"
    assert payload["result"]["final_reply_text"] == "可以推进，先把边界收清楚。"
    assert payload["provenance"]["attempt_id"] == 7
    assert payload["provenance"]["codex_session_id"] == "session-1"


def test_build_review_correction_memory_payload_records_feedback():
    attempt = make_attempt(
        reviewer_feedback="应该先让对方 public repo",
        corrected_reply_text="先 public repo，把私有数据拆出去。",
        reviewed_at="2026-05-29 11:00:00",
    )

    payload = build_review_correction_memory_payload(attempt)

    assert payload["event"] == "review_correction"
    assert payload["original"]["final_reply_text"] == "可以推进，先把边界收清楚。"
    assert payload["review"]["reviewer_feedback"] == "应该先让对方 public repo"
    assert payload["review"]["corrected_reply_text"] == "先 public repo，把私有数据拆出去。"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_memory_events.py -q
```

Expected: FAIL because `memory_events.py` does not exist.

- [ ] **Step 3: Implement payload builders**

Create `apps/local-service/ceo_agent_service/memory_events.py`:

```python
from __future__ import annotations

import json
from typing import Any

from ceo_agent_service.store import ReplyAttempt, SentReply


def build_reply_sent_memory_payload(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None = None,
) -> dict[str, Any]:
    return {
        "event": "reply_sent",
        "conversation": _conversation_payload(attempt),
        "trigger": _trigger_payload(attempt),
        "decision": _decision_payload(attempt),
        "result": {
            "final_reply_text": attempt.final_reply_text,
            "send_status": attempt.send_status,
            "sent_at": sent_reply.sent_at if sent_reply is not None else attempt.updated_at,
        },
        "provenance": _provenance_payload(attempt, sent_reply),
    }


def build_review_correction_memory_payload(attempt: ReplyAttempt) -> dict[str, Any]:
    return {
        "event": "review_correction",
        "conversation": _conversation_payload(attempt),
        "trigger": _trigger_payload(attempt),
        "original": {
            "action": attempt.action,
            "sensitivity_kind": attempt.sensitivity_kind,
            "codex_reason": attempt.codex_reason,
            "draft_reply_text": attempt.draft_reply_text,
            "final_reply_text": attempt.final_reply_text,
            "send_status": attempt.send_status,
        },
        "review": {
            "reviewer_feedback": attempt.reviewer_feedback,
            "corrected_reply_text": attempt.corrected_reply_text,
            "reviewed_at": attempt.reviewed_at,
        },
        "provenance": _provenance_payload(attempt, None),
    }


def memory_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _conversation_payload(attempt: ReplyAttempt) -> dict[str, Any]:
    return {
        "conversation_id": attempt.conversation_id,
        "title": attempt.conversation_title,
    }


def _trigger_payload(attempt: ReplyAttempt) -> dict[str, Any]:
    return {
        "message_id": attempt.trigger_message_id,
        "sender": attempt.trigger_sender,
        "text": attempt.trigger_text,
    }


def _decision_payload(attempt: ReplyAttempt) -> dict[str, Any]:
    return {
        "action": attempt.action,
        "sensitivity_kind": attempt.sensitivity_kind,
        "codex_reason": attempt.codex_reason,
        "audit_summary": attempt.audit_summary,
    }


def _provenance_payload(
    attempt: ReplyAttempt,
    sent_reply: SentReply | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attempt_id": attempt.id,
        "codex_session_id": attempt.codex_session_id,
        "codex_transcript_start_line": attempt.codex_transcript_start_line,
        "codex_transcript_end_line": attempt.codex_transcript_end_line,
    }
    if sent_reply is not None:
        payload["sent_reply_id"] = sent_reply.id
        payload["recall_key"] = sent_reply.recall_key
        payload["send_result_json"] = sent_reply.send_result_json
    return payload
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_memory_events.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/memory_events.py apps/local-service/tests/test_memory_events.py
git commit -m "Build memory episode payloads"
```

---

## Task 4: Enqueue Reply Events From Worker

**Files:**
- Modify: `apps/local-service/ceo_agent_service/worker.py`
- Test: `apps/local-service/tests/test_worker.py`

- [ ] **Step 1: Write failing worker tests**

Add this test near existing send reply tests in `apps/local-service/tests/test_worker.py`:

```python
def test_sent_reply_enqueues_memory_event(tmp_path: Path, monkeypatch):
    dws = FakeDws()
    dws.send_reply_result = {"result": {"processQueryKey": "key-1"}}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进，先收清楚边界。",
            reason="需要回复",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    conversation = group_conversation()
    message = message_from(
        "msg-1",
        "@Alex Chen 看一下这个方案",
        sender="Han Lu",
    )

    worker._process_batch(conversation, [message], [message])

    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        conversation.open_conversation_id,
        "msg-1",
    )
    assert attempt is not None
    events = worker.store.get_memory_write_events_for_attempt(attempt.id)
    assert len(events) == 1
    assert events[0].event_type == "reply_sent"
    assert '"event": "reply_sent"' in events[0].payload_json
    assert "可以推进" in events[0].payload_json


def test_dry_run_reply_does_not_enqueue_memory_event(tmp_path: Path, monkeypatch):
    dws = FakeDws()
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进，先收清楚边界。",
            reason="需要回复",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    conversation = group_conversation()
    message = message_from("msg-1", "@Alex Chen 看一下这个方案", sender="Han Lu")

    worker._process_batch(conversation, [message], [message])

    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        conversation.open_conversation_id,
        "msg-1",
    )
    assert attempt is not None
    assert worker.store.get_memory_write_events_for_attempt(attempt.id) == []
```

Adjust helper names if the file uses different existing fixture helpers. Keep the assertions unchanged.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_worker.py -q -k "memory_event or dry_run_reply"
```

Expected: FAIL because worker does not enqueue memory events.

- [ ] **Step 3: Implement enqueue helper**

In `apps/local-service/ceo_agent_service/worker.py`, import:

```python
from ceo_agent_service.memory_events import (
    build_reply_sent_memory_payload,
    memory_payload_json,
)
```

Add a helper method to `DingTalkAutoReplyWorker`:

```python
    def _enqueue_reply_sent_memory_event(self, attempt_id: int) -> None:
        attempt = self.store.get_reply_attempt(attempt_id)
        if attempt is None or attempt.send_status != "sent":
            return
        sent_reply = self.store.get_sent_reply(
            attempt.conversation_id,
            attempt.trigger_message_id,
        )
        payload = build_reply_sent_memory_payload(attempt, sent_reply)
        self.store.enqueue_memory_write_event(
            attempt_id=attempt.id,
            event_type="reply_sent",
            payload_json=memory_payload_json(payload),
        )
```

Call it at the end of `_deliver_final_reply()` immediately after `record_sent_reply()` and `_mark_seen(new_messages)`:

```python
        self._enqueue_reply_sent_memory_event(attempt_id)
```

Call it in the handoff branch after the attempt is updated to `send_status="sent"` and before return:

```python
            self._enqueue_reply_sent_memory_event(attempt_id)
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_worker.py -q -k "memory_event or dry_run_reply"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/worker.py apps/local-service/tests/test_worker.py
git commit -m "Enqueue memory events for sent replies"
```

---

## Task 5: Enqueue Review Correction Events

**Files:**
- Modify: `apps/local-service/ceo_agent_service/cli.py`
- Modify: `apps/local-service/ceo_agent_service/audit_web.py`
- Test: `apps/local-service/tests/test_cli.py`
- Test: `apps/local-service/tests/test_audit_web.py`

- [ ] **Step 1: Write failing CLI feedback test**

Extend `test_record_feedback_command_updates_reply_attempt` in `apps/local-service/tests/test_cli.py` with:

```python
    events = store.get_memory_write_events_for_attempt(attempt_id)
    assert len(events) == 1
    assert events[0].event_type == "review_correction"
    assert "需要更严谨" in events[0].payload_json
    assert "先看材料再判断" in events[0].payload_json
```

- [ ] **Step 2: Write failing audit web feedback test**

Extend `test_handle_feedback_post_updates_attempt_and_redirects` in `apps/local-service/tests/test_audit_web.py` with:

```python
    events = store.get_memory_write_events_for_attempt(attempt_id)
    assert len(events) == 1
    assert events[0].event_type == "review_correction"
    assert "需要更严谨" in events[0].payload_json
    assert "先看材料" in events[0].payload_json
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_cli.py::test_record_feedback_command_updates_reply_attempt tests/test_audit_web.py::test_handle_feedback_post_updates_attempt_and_redirects -q
```

Expected: FAIL because feedback paths do not enqueue memory events.

- [ ] **Step 4: Implement shared enqueue function**

In `apps/local-service/ceo_agent_service/memory_events.py`, add:

```python
def enqueue_review_correction_memory_event(store, attempt_id: int) -> bool:
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        return False
    if not (attempt.reviewer_feedback.strip() or attempt.corrected_reply_text.strip()):
        return False
    payload = build_review_correction_memory_payload(attempt)
    store.enqueue_memory_write_event(
        attempt_id=attempt.id,
        event_type="review_correction",
        payload_json=memory_payload_json(payload),
    )
    return True
```

In `apps/local-service/ceo_agent_service/cli.py`, import and call it after successful `record_reply_feedback()`:

```python
from ceo_agent_service.memory_events import enqueue_review_correction_memory_event
```

```python
    enqueue_review_correction_memory_event(store, attempt_id)
```

In `apps/local-service/ceo_agent_service/audit_web.py`, import and call it in `handle_feedback_post()` after the store update succeeds:

```python
from ceo_agent_service.memory_events import enqueue_review_correction_memory_event
```

```python
    enqueue_review_correction_memory_event(store, attempt_id)
```

In `handle_reviewed_message_reply()`, after `store.record_reply_feedback(...)`, call:

```python
        enqueue_review_correction_memory_event(store, attempt_id)
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_cli.py::test_record_feedback_command_updates_reply_attempt tests/test_audit_web.py::test_handle_feedback_post_updates_attempt_and_redirects -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/memory_events.py apps/local-service/ceo_agent_service/cli.py apps/local-service/ceo_agent_service/audit_web.py apps/local-service/tests/test_cli.py apps/local-service/tests/test_audit_web.py
git commit -m "Enqueue memory events for review feedback"
```

---

## Task 6: Implement Memory Connector Client And Flush Command

**Files:**
- Create: `apps/local-service/ceo_agent_service/memory_connector.py`
- Create: `apps/local-service/ceo_agent_service/memory_flush.py`
- Modify: `apps/local-service/ceo_agent_service/cli.py`
- Test: `apps/local-service/tests/test_memory_connector.py`
- Test: `apps/local-service/tests/test_memory_flush.py`
- Test: `apps/local-service/tests/test_cli.py`

- [ ] **Step 1: Write failing MCP client tests**

Create `apps/local-service/tests/test_memory_connector.py`:

```python
import json

from ceo_agent_service.memory_connector import extract_memory_episode_id


def test_extract_memory_episode_id_from_common_shapes():
    assert extract_memory_episode_id({"episode_uuid": "ep-1"}) == "ep-1"
    assert extract_memory_episode_id({"episode_id": "ep-2"}) == "ep-2"
    assert extract_memory_episode_id({"source_episode_uuid": "ep-3"}) == "ep-3"
    assert (
        extract_memory_episode_id(
            {"result": {"episode_uuids": ["ep-4"], "processing": {"status": "queued"}}}
        )
        == "ep-4"
    )
    assert extract_memory_episode_id({"ok": True}) == ""
```

- [ ] **Step 2: Write failing flush tests**

Create `apps/local-service/tests/test_memory_flush.py`:

```python
from ceo_agent_service.memory_flush import flush_memory_events
from ceo_agent_service.store import AutoReplyStore


class FakeMemoryClient:
    def __init__(self, result=None, error=None):
        self.result = result or {"episode_uuid": "ep-1"}
        self.error = error
        self.calls = []

    def memory_write(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def test_flush_memory_events_marks_success(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    event_id = store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    client = FakeMemoryClient({"episode_uuid": "ep-1"})

    count = flush_memory_events(store, client, limit=10)

    events = store.list_memory_write_events()
    assert count == 1
    assert client.calls[0]["data"] == '{"event":"reply_sent"}'
    assert client.calls[0]["type"] == "json"
    assert client.calls[0]["user_id"] == "principal"
    assert events[0].id == event_id
    assert events[0].status == "sent"
    assert events[0].memory_episode_id == "ep-1"


def test_flush_memory_events_records_failure_without_touching_reply_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_memory_write_event(
        attempt_id=7,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )
    client = FakeMemoryClient(error=RuntimeError("HTTP 502"))

    count = flush_memory_events(store, client, limit=10)

    events = store.list_memory_write_events()
    assert count == 0
    assert events[0].status == "failed"
    assert events[0].attempts == 1
    assert events[0].last_error == "HTTP 502"
```

- [ ] **Step 3: Write failing CLI parser test**

Add to `apps/local-service/tests/test_cli.py`:

```python
def test_parser_supports_flush_memory_events_command():
    parser = build_parser()

    args = parser.parse_args(["flush-memory-events", "--limit", "5"])

    assert args.command == "flush-memory-events"
    assert args.limit == 5
```

- [ ] **Step 4: Run tests and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_memory_connector.py tests/test_memory_flush.py tests/test_cli.py::test_parser_supports_flush_memory_events_command -q
```

Expected: FAIL because modules and parser support do not exist.

- [ ] **Step 5: Implement memory connector client**

Create `apps/local-service/ceo_agent_service/memory_connector.py`:

```python
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class MemoryConnectorError(RuntimeError):
    pass


def memory_connector_user_id() -> str:
    return os.getenv("MEMORY_CONNECTOR_USER_ID", "principal")


def memory_connector_url() -> str:
    value = os.getenv("MEMORY_CONNECTOR_URL", "").strip()
    if not value:
        raise MemoryConnectorError("MEMORY_CONNECTOR_URL is not configured")
    return value


def memory_connector_token() -> str:
    value = os.getenv("CONNECTOR_API_KEY", "").strip()
    if not value:
        raise MemoryConnectorError("CONNECTOR_API_KEY is not configured")
    return value


class MemoryConnectorClient:
    def __init__(self, *, url: str | None = None, token: str | None = None, user_id: str | None = None):
        self.url = url or memory_connector_url()
        self.token = token or memory_connector_token()
        self.user_id = user_id or memory_connector_user_id()

    def memory_write(
        self,
        *,
        data: str,
        type: str,
        created_at: str,
        user_id: str,
        source_description: str,
        source_metadata: dict[str, Any],
        provenance_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._call_tool(
            "memory_write",
            {
                "data": data,
                "type": type,
                "created_at": created_at,
                "user_id": user_id,
                "source_description": source_description,
                "source_metadata": source_metadata,
                "provenance_metadata": provenance_metadata,
                "wait_for_processing": False,
            },
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "x-memory-user-id": self.user_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise MemoryConnectorError(str(exc)) from exc
        return _parse_mcp_tool_response(raw_body)


def _parse_mcp_tool_response(raw_body: str) -> dict[str, Any]:
    text = raw_body.strip()
    if text.startswith("event:") or text.startswith("data:") or "\ndata:" in text:
        data_lines = [
            line.removeprefix("data:").strip()
            for line in text.splitlines()
            if line.strip().startswith("data:")
        ]
        text = next((line for line in data_lines if line and line != "[DONE]"), "")
    payload = json.loads(text)
    if "error" in payload:
        raise MemoryConnectorError(json.dumps(payload["error"], ensure_ascii=False))
    result = payload.get("result", payload)
    if isinstance(result, dict) and result.get("isError"):
        raise MemoryConnectorError(json.dumps(result, ensure_ascii=False))
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return json.loads(item["text"])
    return result if isinstance(result, dict) else {"result": result}


def extract_memory_episode_id(payload: dict[str, Any]) -> str:
    for key in ("episode_uuid", "episode_id", "source_episode_uuid"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        episode_uuids = result.get("episode_uuids")
        if isinstance(episode_uuids, list) and episode_uuids:
            first = episode_uuids[0]
            return first if isinstance(first, str) else ""
    return ""
```

- [ ] **Step 6: Implement flush orchestration and CLI**

Create `apps/local-service/ceo_agent_service/memory_flush.py`:

```python
from __future__ import annotations

import json

from ceo_agent_service.memory_connector import (
    MemoryConnectorClient,
    extract_memory_episode_id,
    memory_connector_user_id,
)
from ceo_agent_service.store import AutoReplyStore


def flush_memory_events(
    store: AutoReplyStore,
    client: MemoryConnectorClient,
    *,
    limit: int,
) -> int:
    sent_count = 0
    events = store.claim_memory_write_events(limit=limit)
    for event in events:
        try:
            response = client.memory_write(
                data=event.payload_json,
                type="json",
                created_at=event.created_at,
                user_id=memory_connector_user_id(),
                source_description=f"ceo-agent-service:{event.event_type}:{event.attempt_id}",
                source_metadata={
                    "service": "ceo-agent-service",
                    "event_type": event.event_type,
                    "attempt_id": event.attempt_id,
                    "outbox_event_id": event.id,
                },
                provenance_metadata={
                    "source": "ceo-agent-service.memory_write_events",
                    "outbox_event_id": event.id,
                },
            )
        except Exception as exc:
            store.mark_memory_write_event_failed(event.id, str(exc))
            continue
        store.mark_memory_write_event_sent(
            event.id,
            memory_episode_id=extract_memory_episode_id(response),
        )
        sent_count += 1
    return sent_count
```

In `apps/local-service/ceo_agent_service/cli.py`, add command name to the command tuple:

```python
        "flush-memory-events",
```

Add per-command parser args:

```python
        if command == "flush-memory-events":
            subparser.add_argument("--limit", type=_positive_int, default=20)
```

Add command function:

```python
def flush_memory_events_command(settings: WorkerSettings, limit: int) -> int:
    from ceo_agent_service.memory_connector import MemoryConnectorClient
    from ceo_agent_service.memory_flush import flush_memory_events

    store = AutoReplyStore(settings.db_path)
    sent_count = flush_memory_events(store, MemoryConnectorClient(), limit=limit)
    print(f"flush-memory-events sent={sent_count}", flush=True)
    return sent_count
```

Add dispatch in `main()`:

```python
    elif args.command == "flush-memory-events":
        flush_memory_events_command(settings, args.limit)
```

- [ ] **Step 7: Run tests and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_memory_connector.py tests/test_memory_flush.py tests/test_cli.py::test_parser_supports_flush_memory_events_command -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/memory_connector.py apps/local-service/ceo_agent_service/memory_flush.py apps/local-service/ceo_agent_service/cli.py apps/local-service/tests/test_memory_connector.py apps/local-service/tests/test_memory_flush.py apps/local-service/tests/test_cli.py
git commit -m "Flush memory outbox events"
```

---

## Task 7: Show Memory Event State In Audit UI

**Files:**
- Modify: `apps/local-service/ceo_agent_service/audit_web.py`
- Test: `apps/local-service/tests/test_audit_web.py`

- [ ] **Step 1: Write failing audit UI test**

Add to `apps/local-service/tests/test_audit_web.py`:

```python
def test_render_attempt_detail_shows_memory_write_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Han Lu",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.enqueue_memory_write_event(
        attempt_id=attempt_id,
        event_type="reply_sent",
        payload_json='{"event":"reply_sent"}',
    )

    html = render_attempt_detail(store, attempt_id)

    assert "Memory write" in html
    assert "reply_sent" in html
    assert "pending" in html
```

If `render_attempt_detail` has a different existing signature, keep the store setup and assert the returned attempt detail HTML contains these strings.

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_audit_web.py::test_render_attempt_detail_shows_memory_write_state -q
```

Expected: FAIL because the page does not render memory event state.

- [ ] **Step 3: Implement memory card**

In `apps/local-service/ceo_agent_service/audit_web.py`, add a helper:

```python
def _memory_write_card(store: AutoReplyStore, attempt_id: int) -> str:
    events = store.get_memory_write_events_for_attempt(attempt_id)
    if not events:
        return (
            '<section class="card" id="memory-write">'
            "<h2>Memory write</h2><p>No memory event recorded.</p></section>"
        )
    rows = []
    for event in events:
        detail = event.memory_episode_id or event.last_error
        rows.append(
            "<tr>"
            f"<td>{escape(event.event_type)}</td>"
            f"<td>{escape(event.status)}</td>"
            f"<td>{event.attempts}</td>"
            f"<td>{escape(detail)}</td>"
            f"<td>{escape(event.updated_at)}</td>"
            "</tr>"
        )
    return (
        '<section class="card" id="memory-write">'
        "<h2>Memory write</h2>"
        "<table><tr><th>Event</th><th>Status</th><th>Attempts</th>"
        "<th>Result</th><th>Updated</th></tr>"
        + "".join(rows)
        + "</table></section>"
    )
```

Add `_memory_write_card(store, attempt.id)` to the attempt detail body near feedback/audit sections.

- [ ] **Step 4: Run test and verify pass**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_audit_web.py::test_render_attempt_detail_shows_memory_write_state -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add apps/local-service/ceo_agent_service/audit_web.py apps/local-service/tests/test_audit_web.py
git commit -m "Show memory outbox state in audit UI"
```

---

## Task 8: Add Launchd Flush Job And Final Verification

**Files:**
- Inspect existing launchd scripts under `scripts/` and repo docs.
- Modify only the existing launchd install script or plist template that manages CEO service jobs.
- Test: related launchd/script tests if present, otherwise `tests/test_cli.py`, `tests/test_store.py`, `tests/test_memory_flush.py`, and `tests/test_codex_runner.py`.

- [ ] **Step 1: Locate launchd job source**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
rg -n "reply-producer|reply-consumer|launchd|plist|StartInterval|audit-web" scripts apps/local-service tests
```

Expected: Find the script or plist source that installs current producer/consumer/audit-web jobs.

- [ ] **Step 2: Write failing launchd test if a launchd test exists**

If a test file already asserts launchd content, add an assertion like:

```python
assert "flush-memory-events" in content
assert "memory_connector.env" in content
```

Run that test and verify it fails.

- [ ] **Step 3: Add flush job**

In the existing launchd install surface, add a job equivalent to:

```sh
if [ -f "/Users/principal/.codex/memory_connector.env" ]; then
  . "/Users/principal/.codex/memory_connector.env"
fi
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m ceo_agent_service.cli flush-memory-events \
  --db /Users/principal/Documents/Projects/ceo-agent-service/data/auto-reply.sqlite3 \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus \
  --limit 20
```

Use a low frequency such as every 60 seconds. Do not put this inside reply consumer.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest tests/test_codex_runner.py tests/test_store.py tests/test_memory_events.py tests/test_memory_connector.py tests/test_memory_flush.py tests/test_cli.py tests/test_audit_web.py -q
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/pytest -q
```

Expected: PASS or only known unrelated skipped tests.

- [ ] **Step 6: Run formatter/linter if configured**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/ruff check .
```

Expected: PASS.

- [ ] **Step 7: Manual command smoke**

Run:

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service/apps/local-service
.venv/bin/python -m ceo_agent_service.cli flush-memory-events \
  --db /Users/principal/Documents/Projects/ceo-agent-service/data/auto-reply.sqlite3 \
  --workspace /Users/principal/Documents/memory \
  --corpus-dir /Users/principal/Documents/Projects/ceo-agent-service/corpus \
  --limit 1
```

Expected: Command exits without sending DingTalk messages. If memory backend returns 502, a pending event may become failed; reply attempts remain unchanged.

- [ ] **Step 8: Commit**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git add scripts apps/local-service/tests
git commit -m "Schedule memory outbox flush"
```

- [ ] **Step 9: Push**

```bash
cd /Users/principal/Documents/Projects/ceo-agent-service
git push
```

Expected: push succeeds.

---

## Self-Review

- Spec coverage:
  - Explicit Codex exec MCP exposure: Task 1.
  - Retryable outbox schema and helpers: Task 2.
  - Complete event episode payloads: Task 3.
  - Reply sent and handoff enqueue: Task 4.
  - Feedback correction enqueue: Task 5.
  - Separate flush command with memory_write: Task 6.
  - Audit visibility: Task 7.
  - Launchd/operational flush and verification: Task 8.

- Placeholder scan:
  - No `TODO`, `TBD`, or unspecified "handle edge cases" steps remain.
  - Launchd source path is intentionally discovered in Task 8 because the exact installer surface must be read before editing; the command and expected content are specified.

- Type consistency:
  - `MemoryWriteEvent` fields match the SQL table and store methods.
  - `event_type` values match the design: `reply_sent`, `review_correction`.
  - Flush calls `memory_write(type="json", user_id="principal")` through `memory_connector_user_id()`.
