"""Approved-only, claimed and audited writes to Friday Memory."""
from __future__ import annotations

import json
import re
from pathlib import Path

WRITE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "wechat_memory_write_result.schema.json"
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)


class MemoryWriteOutcomeUnknown(RuntimeError):
    pass


class CodexMemoryWriteBackend:
    def __init__(self, workspace: Path, codex_bin: str = "codex", executor=None,
                 timeout_seconds: int = 1200, idle_timeout_seconds: int = 900):
        from app.codex_runner import CodexRunner
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def write(self, statement: str, *, source_time_start: str, source_time_end: str) -> str:
        prompt = (
            "必须且只能调用一次 memory_write。data 只传下面 final_statement；"
            "type 使用 text；created_at 使用 source_time_start（为空才使用 source_time_end）。"
            "不得调用任何其他工具。绝不传 user_id、graph_id、graph_ids 或聊天 evidence。"
            "调用后只输出 {\"status\":\"attempted\"}。\n"
            + json.dumps({"final_statement": statement,
                          "source_time_start": source_time_start,
                          "source_time_end": source_time_end}, ensure_ascii=False)
        )
        command = self.runner.build_command(prompt, None, output_schema_path=WRITE_SCHEMA_PATH,
                                            ignore_user_config=True)
        from app.codex_runner import _passthrough_mcp_server_names
        for server_name in _passthrough_mcp_server_names():
            command[-1:-1] = ["-c", f"mcp_servers.{server_name}.enabled=false"]
        if self.executor is not None:
            raw = self.executor(command, prompt)
        else:
            from app.codex_decision import _subprocess_failure_reason
            from app.process_runner import run_process_with_idle_timeout
            completed = run_process_with_idle_timeout(
                command, prompt=prompt, env=self.runner.build_env(),
                total_timeout_seconds=self.timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
            )
            if completed.timed_out:
                raise MemoryWriteOutcomeUnknown(completed.timeout_reason or "memory write outcome unknown")
            if completed.returncode != 0:
                reason = _subprocess_failure_reason(completed.stderr, completed.stdout)
                raise MemoryWriteOutcomeUnknown(
                    f"memory write outcome unknown: {reason}"
                )
            raw = completed.stdout
        return self._memory_id_from_audit(raw)

    @staticmethod
    def _memory_id_from_audit(raw: str) -> str:
        from app.codex_decision import extract_codex_audit_events
        events = extract_codex_audit_events(raw, limit=100)
        calls = [event for event in events if event.get("tool", "") != "tool_output"]
        memory_calls = [event for event in calls
                        if "memory_write" in event.get("tool", "").casefold()]
        if len(calls) != 1 or len(memory_calls) != 1:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: expected one tool call")
        call = memory_calls[0]
        outputs = []
        if call.get("output"):
            outputs.append(call["output"])
        elif call.get("call_id"):
            outputs.extend(
                event.get("output", "") for event in events
                if event.get("call_id") == call["call_id"] and event.get("output")
            )
        if len(outputs) != 1:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: missing tool result")
        output = outputs[0]
        lowered = output.casefold()
        if any(marker in lowered for marker in ('"iserror": true', '"status": "error"', "failed")):
            raise RuntimeError("memory_write failed")
        match = _UUID.search(output)
        if match:
            return match.group(0)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        stable_id = ""
        if isinstance(payload, dict):
            for key in ("memory_id", "uuid", "id"):
                value = payload.get(key)
                if isinstance(value, str) and len(value.strip()) >= 8:
                    stable_id = value.strip()
                    break
        if not stable_id:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: missing memory id")
        return stable_id


class WechatMemoryWriter:
    def __init__(self, store, memory_backend):
        self.store = store
        self.memory_backend = memory_backend

    def write(self, candidate_id: int) -> str:
        claim = self.store.claim_wechat_memory_candidate_write(candidate_id)
        if claim["outcome"] == "written":
            return claim["memory_id"]
        if claim["outcome"] == "writing":
            raise RuntimeError("memory write already in progress")
        if claim["outcome"] != "claimed":
            raise ValueError(claim["reason"])
        row = claim["candidate"]
        try:
            memory_id = self.memory_backend.write(
                row["edited_statement"], source_time_start=row["source_time_start"],
                source_time_end=row["source_time_end"],
            )
        except MemoryWriteOutcomeUnknown:
            self.store.finish_wechat_memory_candidate_write(
                candidate_id, status="unknown", error="memory write outcome unknown")
            raise
        except Exception as exc:
            if "outcome unknown" in str(exc).casefold():
                self.store.finish_wechat_memory_candidate_write(
                    candidate_id, status="unknown", error=str(exc))
            else:
                self.store.finish_wechat_memory_candidate_write(
                    candidate_id, status="failed", error=str(exc))
            raise
        self.store.finish_wechat_memory_candidate_write(
            candidate_id, status="written", memory_id=memory_id)
        return memory_id
