"""Approved-only, claimed and audited writes to Friday Memory."""
from __future__ import annotations

import json
from pathlib import Path

WRITE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "wechat_memory_write_result.schema.json"
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
        return self._memory_id_from_audit(
            raw, statement=statement,
            expected_created_at=source_time_start or source_time_end,
        )

    @staticmethod
    def _memory_id_from_audit(
        raw: str, *, statement: str, expected_created_at: str,
    ) -> str:
        from app.codex_decision import extract_codex_audit_events
        from app.store import AutoReplyStore
        events = extract_codex_audit_events(raw, limit=100)
        calls = [event for event in events if event.get("tool", "") != "tool_output"]
        memory_calls = [event for event in calls
                        if AutoReplyStore._is_memory_write_tool_name(
                            event.get("tool", ""))]
        if len(calls) != 1 or len(memory_calls) != 1:
            raise MemoryWriteOutcomeUnknown("memory write outcome unknown: expected one tool call")
        call = memory_calls[0]
        try:
            arguments = json.loads(call.get("input", ""))
        except json.JSONDecodeError as exc:
            raise MemoryWriteOutcomeUnknown(
                "memory write outcome unknown: invalid arguments"
            ) from exc
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"data", "type", "created_at"}
            or arguments.get("data") != statement
            or arguments.get("type") != "text"
            or arguments.get("created_at") != expected_created_at
        ):
            raise MemoryWriteOutcomeUnknown(
                "memory write outcome unknown: unsafe arguments"
            )
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
        parsed = AutoReplyStore._parse_memory_write_output(outputs[0])
        if parsed.get("status") == "failed":
            raise RuntimeError(parsed.get("last_error") or "memory_write failed")
        stable_id = parsed.get("memory_episode_id", "").strip()
        if parsed.get("status") != "written" or not stable_id:
            raise MemoryWriteOutcomeUnknown(
                "memory write outcome unknown: no explicit successful tool result"
            )
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
