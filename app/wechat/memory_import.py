"""Bounded WeChat history reads and pending-only Memory candidate extraction."""
from __future__ import annotations

import hashlib
import heapq
import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.wechat.models import WechatAccount, WechatMessage

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "wechat_memory_candidates.schema.json"
DEDUPE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "wechat_memory_dedupe.schema.json"
ALLOWED_CATEGORIES = frozenset({
    "fact", "preference", "commitment", "decision", "project_status",
    "relationship", "reusable_experience",
})
STATEMENT_LIMIT = 800
EVIDENCE_LIMIT = 300
BATCH_SIZE = 100
MAX_TARGETS = 100

_BLOCK_PATTERNS = tuple(re.compile(pattern, flags) for pattern, flags in (
    (r"验证码|verification\s*code|一次性密码|one[- ]time password", re.I),
    (r"密码|password|\btoken\b|api[_ -]?key|client[_ -]?secret|\bsecret\b", re.I),
    (r"\b(?:sk-(?:proj-)?|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{12,}\b", re.I),
    (r"\bBearer\s+[A-Za-z0-9._~-]{12,}\b", re.I),
    (r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", 0),
    (r"身份证|银行卡|信用卡|bank\s*card|credit\s*card|\bcvv\b", re.I),
    (r"诊断|病历|处方|化验|medical record|diagnosis|prescription", re.I),
    (r"账户余额|交易流水|工资明细|纳税|account balance|bank statement|tax return", re.I),
    (r"\b\d{6}\b", 0),
    (r"\b\d{15,19}\b", 0),
))
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{8,}(?!\d)")


class ExtractedMemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    category: str
    confidence: float = Field(ge=0, le=1)
    sensitivity: str = "normal"
    source_message_ids: list[str] = Field(default_factory=list)
    source_conversation_ids: list[str] = Field(default_factory=list)
    source_time_start: str = ""
    source_time_end: str = ""
    evidence_excerpt: str = ""
    cleanup_notes: str = ""


def _normalized(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _redacted_excerpt(value: str) -> str:
    value = _EMAIL.sub("[redacted-email]", value)
    value = _PHONE.sub("[redacted-phone]", value)
    value = _LONG_NUMBER.sub("[redacted-number]", value)
    return _normalized(value, EVIDENCE_LIMIT)


def _contains_blocked_content(*values: str) -> bool:
    text = " ".join(values)
    return any(pattern.search(text) for pattern in _BLOCK_PATTERNS)


def validate_final_statement(value: str) -> str:
    statement = " ".join(value.split())
    if not statement:
        raise ValueError("final statement required")
    if len(statement) > STATEMENT_LIMIT:
        raise ValueError(f"final statement exceeds {STATEMENT_LIMIT} characters")
    if _contains_blocked_content(statement):
        raise ValueError("final statement contains blocked sensitive content")
    return statement


def _validate_date_bound(value: str, name: str) -> None:
    if not value:
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {name} date bound") from exc


def _lower_bound(value: str) -> str:
    return value + "T00:00:00" if len(value) == 10 else value


def _upper_bound(value: str) -> str:
    return value + "T23:59:59.999999" if len(value) == 10 else value


def _parse_output(raw: str) -> list[ExtractedMemoryCandidate]:
    from app.codex_decision import _decision_text_candidates, _iter_json_payloads
    payloads = _iter_json_payloads(raw)
    candidates: list[object] = list(payloads)
    for payload in payloads:
        if isinstance(payload, dict):
            for text in _decision_text_candidates(payload):
                try:
                    candidates.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
    for payload in reversed(candidates):
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            continue
        try:
            return [ExtractedMemoryCandidate.model_validate(item) for item in payload["candidates"]]
        except ValidationError as exc:
            raise ValueError(f"invalid WeChat Memory extraction: {exc}") from exc
    raise ValueError("WeChat Memory extraction returned no candidate envelope")


class CodexMemoryExtractionRunner:
    """Structured Codex runner. It has no Memory write permission in its prompt."""

    def __init__(self, workspace: Path, codex_bin: str = "codex", executor=None,
                 timeout_seconds: int = 1200, idle_timeout_seconds: int = 900):
        from app.codex_runner import CodexRunner
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def extract(self, messages: list[WechatMessage]) -> list[ExtractedMemoryCandidate]:
        prompt = self._prompt(messages)
        command = self.runner.build_command(prompt, None, output_schema_path=SCHEMA_PATH,
                                            ignore_user_config=True)
        command[-1:-1] = ["-c", "mcp_servers.memory_connector.enabled=false"]
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
                raise RuntimeError(completed.timeout_reason or "WeChat Memory extraction timed out")
            if completed.returncode != 0:
                raise RuntimeError(_subprocess_failure_reason(completed.stderr, completed.stdout))
            raw = completed.stdout
        return _parse_output(raw)

    @staticmethod
    def _prompt(messages: list[WechatMessage]) -> str:
        payload = [{
            "message_id": m.message_id, "conversation_id": m.conversation_id,
            "sent_at": m.sent_at, "direction": m.direction,
            "sender": m.sender_display_name, "text": m.text,
        } for m in messages]
        return (
            "从下面有界微信消息批次提取长期有价值、可复用且明确的事实。"
            "只输出 schema 指定的 candidates JSON。不要调用任何工具；运行时也不会提供 memory_write。"
            "evidence_excerpt 必须是最小化、脱敏的摘要，不能复制完整聊天；"
            "敏感、猜测、临时事务返回空 candidates。source 字段只能引用输入中的 id 和时间。\n"
            + json.dumps(payload, ensure_ascii=False)
        )


class CodexMemoryRecallMatcher:
    """Read-only durable Memory matcher, hard-limited to memory_recall."""

    def __init__(self, workspace: Path, codex_bin: str = "codex", executor=None,
                 timeout_seconds: int = 1200, idle_timeout_seconds: int = 900):
        from app.codex_runner import CodexRunner
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

    def match(self, candidates: list[ExtractedMemoryCandidate]) -> dict[str, str]:
        statements = [item.statement for item in candidates]
        prompt = (
            "只能调用 memory_recall，只读检查这些候选是否已存在于 durable Memory。"
            "禁止 memory_write 和任何其他工具。每条输出 relation: none/exact/compatible/contradiction。\n"
            + json.dumps(statements, ensure_ascii=False)
        )
        command = self.runner.build_command(
            prompt, None, output_schema_path=DEDUPE_SCHEMA_PATH,
            ignore_user_config=True)
        from app.codex_runner import _passthrough_mcp_server_names
        for name in _passthrough_mcp_server_names():
            command[-1:-1] = ["-c", f"mcp_servers.{name}.enabled=false"]
        command[-1:-1] = [
            "-c", 'mcp_servers.memory_connector.enabled_tools=["memory_recall"]',
            "-c", 'mcp_servers.memory_connector.disabled_tools=["memory_write"]',
        ]
        raw = self._execute(command, prompt)
        self._validate_audit(raw)
        payload = self._result_payload(raw)
        result = {str(item["statement"]): str(item["relation"])
                  for item in payload.get("matches", [])}
        if set(result) != set(statements):
            raise RuntimeError("durable Memory matcher returned incomplete results")
        return result

    def _execute(self, command: list[str], prompt: str) -> str:
        if self.executor is not None:
            return self.executor(command, prompt)
        from app.codex_decision import _subprocess_failure_reason
        from app.process_runner import run_process_with_idle_timeout
        completed = run_process_with_idle_timeout(
            command, prompt=prompt, env=self.runner.build_env(),
            total_timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds)
        if completed.timed_out:
            raise RuntimeError(completed.timeout_reason or "durable Memory matcher timed out")
        if completed.returncode != 0:
            raise RuntimeError(_subprocess_failure_reason(completed.stderr, completed.stdout))
        return completed.stdout

    @staticmethod
    def _validate_audit(raw: str) -> None:
        from app.codex_decision import extract_codex_audit_events
        events = extract_codex_audit_events(raw, limit=100)
        calls = [event for event in events if event.get("tool", "") != "tool_output"]
        def is_recall(name: str) -> bool:
            normalized = name.strip()
            return normalized == "memory_recall" or normalized.endswith(
                (".memory_recall", "__memory_recall", " memory_recall"))
        if not calls or any(not is_recall(event.get("tool", "")) for event in calls):
            raise RuntimeError("durable Memory matcher may use only memory_recall")
        for call in calls:
            outputs = [call.get("output", "")] if call.get("output") else []
            if not outputs and call.get("call_id"):
                outputs = [event.get("output", "") for event in events
                           if event.get("call_id") == call["call_id"] and event.get("output")]
            if len(outputs) != 1 or "error" in outputs[0].casefold():
                raise RuntimeError("durable Memory recall audit is ambiguous")

    @staticmethod
    def _result_payload(raw: str) -> dict:
        from app.codex_decision import _decision_text_candidates, _iter_json_payloads
        values: list[object] = list(_iter_json_payloads(raw))
        for payload in list(values):
            if isinstance(payload, dict):
                for text in _decision_text_candidates(payload):
                    try:
                        values.append(json.loads(text))
                    except json.JSONDecodeError:
                        continue
        for payload in reversed(values):
            if isinstance(payload, dict) and isinstance(payload.get("matches"), list):
                relations = {"none", "exact", "compatible", "contradiction"}
                if all(isinstance(item, dict) and item.get("relation") in relations
                       for item in payload["matches"]):
                    return payload
        raise RuntimeError("durable Memory matcher returned no structured result")


class WechatMemoryImporter:
    def __init__(self, store, reader=None, codex=None, matcher=None):
        self.store = store
        self.reader = reader
        self.codex = codex
        self.matcher = matcher

    @staticmethod
    def import_run_id(account_id: str, target_ids: list[str], since: str,
                      until: str, limit: int) -> str:
        scope = json.dumps({"account": account_id, "targets": sorted(set(target_ids)),
                            "since": since, "until": until, "limit": limit},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "wechat-" + hashlib.sha256(scope.encode()).hexdigest()[:24]

    def clean_candidates(self, candidates: list[ExtractedMemoryCandidate],
                         *, allowed_message_ids: set[str] | None = None,
                         allowed_conversation_ids: set[str] | None = None,
                         since: str = "", until: str = "") -> list[ExtractedMemoryCandidate]:
        seen: set[str] = set()
        cleaned: list[ExtractedMemoryCandidate] = []
        for candidate in candidates:
            statement = _normalized(candidate.statement, STATEMENT_LIMIT)
            source_messages = [v.strip() for v in candidate.source_message_ids if v.strip()]
            source_conversations = [v.strip() for v in candidate.source_conversation_ids if v.strip()]
            if (not statement or not source_messages or candidate.category not in ALLOWED_CATEGORIES
                    or candidate.sensitivity != "normal"):
                continue
            if not candidate.source_time_start or not candidate.source_time_end:
                continue
            if allowed_message_ids is not None and not set(source_messages) <= allowed_message_ids:
                continue
            if allowed_conversation_ids is not None and (
                not source_conversations or not set(source_conversations) <= allowed_conversation_ids
            ):
                continue
            if since and candidate.source_time_start < _lower_bound(since):
                continue
            if until and candidate.source_time_end > _upper_bound(until):
                continue
            if _contains_blocked_content(statement, candidate.evidence_excerpt):
                continue
            if len(" ".join(candidate.evidence_excerpt.split())) > EVIDENCE_LIMIT:
                continue
            key = statement.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(candidate.model_copy(update={
                "statement": statement,
                "source_message_ids": source_messages,
                "source_conversation_ids": source_conversations,
                "evidence_excerpt": _redacted_excerpt(candidate.evidence_excerpt),
                "cleanup_notes": "deterministic_cleanup:v1",
            }))
        return cleaned

    def run(self, *, account: WechatAccount | None = None, account_id: str = "",
            target_ids: list[str], since: str, until: str, limit: int,
            import_run_id: str = "") -> dict:
        resolved_account_id = account.account_id if account else account_id
        targets = list(dict.fromkeys(value.strip() for value in target_ids if value.strip()))
        if not resolved_account_id:
            raise ValueError("account_id required")
        if not targets or not (since or until):
            raise ValueError("bounded scope required: target_ids and a date bound are required")
        if len(targets) > MAX_TARGETS:
            raise ValueError(f"bounded scope required: at most {MAX_TARGETS} targets")
        if not 1 <= limit <= 10000:
            raise ValueError("bounded scope required: 1 <= limit <= 10000")
        _validate_date_bound(since, "since")
        _validate_date_bound(until, "until")
        if since and until and since > until:
            raise ValueError("since date bound must not be after until")
        if self.reader is None or self.codex is None or account is None:
            raise ValueError("reader, extraction runner, and ready account are required")
        if self.matcher is None:
            raise RuntimeError("durable Memory matcher is required")

        heap: list[tuple[str, str, str, int, WechatMessage]] = []
        sequence = 0
        for target_id in targets:
            rows = self.reader.read_messages(
                account, conversation_id=target_id,
                conversation_type="group" if target_id.endswith("@chatroom") else "direct",
                since=since, until=_upper_bound(until) if until else "",
                limit=limit, order="newest",
            )
            target_rows = heapq.nlargest(
                limit, rows, key=lambda row: (row.sent_at, row.message_id)
            )
            for row in target_rows:
                if row.kind != "text" or not row.text.strip():
                    continue
                if until and row.sent_at > _upper_bound(until):
                    continue
                item = (row.sent_at, row.message_id, row.conversation_id, sequence, row)
                sequence += 1
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                elif item[:4] > heap[0][:4]:
                    heapq.heapreplace(heap, item)
        messages = [item[-1] for item in sorted(heap)]
        run_id = import_run_id or self.import_run_id(resolved_account_id, targets, since, until, limit)
        candidates: list[ExtractedMemoryCandidate] = []
        for start in range(0, len(messages), BATCH_SIZE):
            batch = messages[start:start + BATCH_SIZE]
            raw = self.codex.extract(batch)
            parsed = [item if isinstance(item, ExtractedMemoryCandidate)
                      else ExtractedMemoryCandidate.model_validate(item) for item in raw]
            by_id = {message.message_id: message for message in batch}
            validated = []
            for item in parsed:
                source_rows = [by_id.get(message_id) for message_id in item.source_message_ids]
                if not source_rows or any(row is None for row in source_rows):
                    continue
                actual_conversations = {
                    row.conversation_id for row in source_rows if row is not None
                }
                if set(item.source_conversation_ids) != actual_conversations:
                    continue
                actual_start = min(row.sent_at for row in source_rows if row is not None)
                actual_end = max(row.sent_at for row in source_rows if row is not None)
                if item.source_time_start > actual_start or item.source_time_end < actual_end:
                    continue
                validated.append(item.model_copy(update={
                    "source_time_start": actual_start, "source_time_end": actual_end,
                }))
            candidates.extend(self.clean_candidates(
                validated, allowed_message_ids={message.message_id for message in batch},
                allowed_conversation_ids={message.conversation_id for message in batch},
                since=since, until=until,
            ))
        candidates = self.clean_candidates(candidates)
        relations = self.matcher.match(candidates) if candidates else {}
        durable_duplicates = 0
        filtered = []
        for candidate in candidates:
            relation = relations.get(candidate.statement)
            if relation not in {"none", "exact", "compatible", "contradiction"}:
                raise RuntimeError("durable Memory matcher returned incomplete results")
            if relation == "exact":
                durable_duplicates += 1
                continue
            if relation in {"compatible", "contradiction"}:
                candidate = candidate.model_copy(update={
                    "cleanup_notes": f"deterministic_cleanup:v1;dedupe_relation:{relation}"
                })
            filtered.append(candidate)
        candidates = filtered
        written = sum(self.store.add_wechat_memory_candidate(
            import_run_id=run_id, account_id=resolved_account_id, candidate=candidate
        ) is not None for candidate in candidates)
        return {"import_run_id": run_id, "messages": len(messages),
                "candidates": written, "durable_duplicates": durable_duplicates}
