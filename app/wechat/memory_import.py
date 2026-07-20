"""Bounded WeChat history reads and pending-only Memory candidate extraction."""
from __future__ import annotations

import hashlib
import heapq
import json
import re
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
MAX_RECALL_CANDIDATES = 100
INPUT_TEXT_LIMIT = 2000
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

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


class DurableMemoryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    relation: Literal["none", "exact", "compatible", "contradiction"]
    memory_id: str = ""
    evidence: str = Field(default="", max_length=240)
    merged_statement: str = ""

    @model_validator(mode="after")
    def validate_relation_fields(self) -> "DurableMemoryMatch":
        memory_id = self.memory_id.strip()
        evidence = " ".join(self.evidence.split())
        merged_statement = " ".join(self.merged_statement.split())
        if self.relation == "none":
            if memory_id or evidence or merged_statement:
                raise ValueError("none match auxiliary fields must be empty")
            return self
        if not memory_id or len(evidence) < 8:
            raise ValueError("non-none match requires memory_id and meaningful evidence")
        if self.relation == "compatible":
            if not merged_statement:
                raise ValueError("compatible match requires merged_statement")
        elif merged_statement:
            raise ValueError("only compatible match may provide merged_statement")
        return self


def _normalized(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _redacted(value: str, limit: int) -> str:
    value = _EMAIL.sub("[redacted-email]", value)
    value = _PHONE.sub("[redacted-phone]", value)
    value = _LONG_NUMBER.sub("[redacted-number]", value)
    return _normalized(value, limit)


def _redacted_excerpt(value: str) -> str:
    return _redacted(value, EVIDENCE_LIMIT)


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
        _parse_instant(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name} date bound") from exc


def _upper_bound(value: str) -> str:
    return value + "T23:59:59.999999" if len(value) == 10 else value


def _parse_instant(value: str, *, end_of_day: bool = False) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if len(normalized) == 10:
        parsed = datetime.combine(
            parsed.date(), time.max if end_of_day else time.min,
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(timezone.utc)


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
        from app.wechat.codex_safety import make_read_only_without_tools
        make_read_only_without_tools(command)
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
        from app.wechat.codex_safety import has_any_tool_event
        if has_any_tool_event(raw):
            raise RuntimeError("WeChat Memory extraction must not call tools")
        return _parse_output(raw)

    @staticmethod
    def _prompt(messages: list[WechatMessage]) -> str:
        payload = []
        for message in messages:
            if message.kind != "text" or not message.text.strip():
                continue
            if _contains_blocked_content(message.text):
                continue
            payload.append({
                "message_id": message.message_id,
                "conversation_id": message.conversation_id,
                "sent_at": message.sent_at,
                "sender_role": "self" if message.direction == "outbound" else "other",
                "text": _redacted(message.text, INPUT_TEXT_LIMIT),
            })
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

    def match(
        self, candidates: list[ExtractedMemoryCandidate]
    ) -> dict[str, DurableMemoryMatch]:
        if len(candidates) > MAX_RECALL_CANDIDATES:
            raise ValueError(
                f"durable Memory matcher accepts at most {MAX_RECALL_CANDIDATES} candidates"
            )
        statements = sorted(validate_final_statement(item.statement) for item in candidates)
        if len(set(statements)) != len(statements):
            raise ValueError("durable Memory matcher requires unique candidate statements")
        result: dict[str, DurableMemoryMatch] = {}
        for statement in statements:
            result[statement] = self._match_statement(statement)
        return result

    def _match_statement(self, statement: str) -> DurableMemoryMatch:
        prompt = (
            "必须且只能调用一次 memory_recall，arguments 只能包含 query，且 query 必须逐字等于：\n"
            + statement
            + "\n只读检查候选是否已存在。禁止 memory_write 和其他工具。"
            "只输出这一条候选的 relation、supporting memory_id、最小 evidence；"
            "relation=none 时 memory_id、evidence、merged_statement 必须全部为空字符串；"
            "compatible 还必须给非空 merged_statement。"
        )
        command = self.runner.build_command(
            prompt, None, output_schema_path=DEDUPE_SCHEMA_PATH,
            ignore_user_config=True)
        from app.wechat.codex_safety import disable_configured_mcp_servers
        disable_configured_mcp_servers(
            command, except_names=frozenset({"memory_connector"}))
        command[-1:-1] = [
            "-c", 'mcp_servers.memory_connector.enabled_tools=["memory_recall"]',
            "-c", 'mcp_servers.memory_connector.disabled_tools=["memory_write"]',
        ]
        raw = self._execute(command, prompt)
        recalled_memories = self._validate_audit(raw, expected_query=statement)
        payload = self._result_payload(raw)
        matches = []
        for raw_match in payload.get("matches", []):
            item = DurableMemoryMatch.model_validate(raw_match)
            evidence = " ".join(item.evidence.split())
            if item.relation != "none" and len(evidence) < 8:
                raise RuntimeError("durable Memory match evidence is too short")
            matches.append(item.model_copy(update={
                "memory_id": item.memory_id.strip(), "evidence": evidence,
            }))
        result = {item.statement: item for item in matches}
        if set(result) != {statement}:
            raise RuntimeError("durable Memory matcher returned incomplete results")
        for item in matches:
            if item.relation == "none":
                if item.memory_id or item.evidence or item.merged_statement:
                    raise RuntimeError("none durable match must not claim support")
                continue
            if not item.memory_id or not item.evidence:
                raise RuntimeError("durable Memory match lacks supporting evidence")
            supported = False
            for memory in recalled_memories:
                memory_id = str(
                    memory.get("memory_id") or memory.get("uuid") or memory.get("id") or ""
                )
                if memory_id != item.memory_id:
                    continue
                texts = [" ".join(text.split()) for text in self._memory_support_texts(memory)]
                if any(item.evidence in text for text in texts):
                    supported = True
                    break
            if not supported:
                raise RuntimeError(
                    "durable Memory match support is absent from the same recalled memory"
                )
            if item.relation == "compatible":
                validate_final_statement(item.merged_statement)
            elif item.merged_statement:
                raise RuntimeError("only compatible match may provide merged statement")
        return result[statement]

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
    def _validate_audit(raw: str, *, expected_query: str) -> list[dict]:
        from app.store import AutoReplyStore
        from app.wechat.codex_safety import completed_mcp_tool_calls, completed_tool_events

        calls = completed_mcp_tool_calls(raw)
        def is_recall(name: str) -> bool:
            normalized = name.strip()
            return normalized == "memory_recall" or normalized.endswith(
                (".memory_recall", "__memory_recall", " memory_recall"))
        if (
            len(completed_tool_events(raw)) != 1
            or len(calls) != 1
            or any(not is_recall(str(call.get("tool") or "")) for call in calls)
        ):
            raise RuntimeError("durable Memory matcher may use only memory_recall")
        call = calls[0]
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise RuntimeError("durable Memory recall query audit is invalid") from exc
        if arguments != {"query": expected_query}:
            raise RuntimeError("durable Memory recall query does not match candidates")
        output = call.get("result")
        if output is None:
            raise RuntimeError("durable Memory recall audit is ambiguous")
        if call.get("isError") is True or call.get("error"):
            raise RuntimeError("durable Memory recall tool error")
        if isinstance(output, str):
            output = AutoReplyStore._load_memory_json(output)
        if isinstance(output, dict) and (
            output.get("isError") is True or output.get("error")
        ):
            raise RuntimeError("durable Memory recall tool error")
        output = CodexMemoryRecallMatcher._unwrap_recall_output(output)
        if not isinstance(output, dict):
            raise RuntimeError("durable Memory recall output is not structured")
        memories = output.get("memories")
        if not isinstance(memories, list) or any(not isinstance(item, dict) for item in memories):
            raise RuntimeError("durable Memory recall output requires a memories list")
        return memories

    @staticmethod
    def _memory_support_texts(memory: dict) -> list[str]:
        allowed = {"text", "summary", "background", "provenance"}
        texts: list[str] = []

        def collect(value: object, depth: int) -> None:
            if depth > 4 or len(texts) >= 64:
                return
            if isinstance(value, str):
                texts.append(value[:2000])
            elif isinstance(value, dict):
                for nested in list(value.values())[:32]:
                    collect(nested, depth + 1)
            elif isinstance(value, list):
                for nested in value[:32]:
                    collect(nested, depth + 1)

        for key in allowed:
            if key in memory:
                collect(memory[key], 0)
        return texts

    @staticmethod
    def _unwrap_recall_output(payload: object) -> object:
        from app.store import AutoReplyStore
        if not isinstance(payload, dict):
            return payload
        structured = payload.get("structured_content") or payload.get("structuredContent")
        if isinstance(structured, dict):
            nested = AutoReplyStore._load_memory_json(str(structured.get("result") or ""))
            return nested if nested is not None else structured
        if isinstance(payload.get("result"), str):
            nested = AutoReplyStore._load_memory_json(payload["result"])
            return nested if nested is not None else payload
        if isinstance(payload.get("content"), list):
            for item in payload["content"]:
                if isinstance(item, dict):
                    nested = AutoReplyStore._load_memory_json(str(item.get("text") or ""))
                    if nested is not None:
                        return nested
        return payload

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
                try:
                    [DurableMemoryMatch.model_validate(item) for item in payload["matches"]]
                except ValidationError:
                    continue
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
            try:
                source_start = _parse_instant(candidate.source_time_start)
                source_end = _parse_instant(candidate.source_time_end)
            except ValueError:
                continue
            if source_start > source_end:
                continue
            if since and source_start < _parse_instant(since):
                continue
            if until and source_end > _parse_instant(until, end_of_day=len(until) == 10):
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
        if since and until and _parse_instant(since) > _parse_instant(
            until, end_of_day=len(until) == 10
        ):
            raise ValueError("since date bound must not be after until")
        if self.reader is None or self.codex is None or account is None:
            raise ValueError("reader, extraction runner, and ready account are required")
        if self.matcher is None:
            raise RuntimeError("durable Memory matcher is required")

        heap: list[tuple[datetime, str, str, int, WechatMessage]] = []
        sequence = 0
        for target_id in targets:
            rows = self.reader.read_messages(
                account, conversation_id=target_id,
                conversation_type="group" if target_id.endswith("@chatroom") else "direct",
                since=since, until=_upper_bound(until) if until else "",
                limit=limit, order="newest",
            )
            target_rows = heapq.nlargest(
                limit, rows, key=lambda row: (_parse_instant(row.sent_at), row.message_id)
            )
            for row in target_rows:
                if row.kind != "text" or not row.text.strip():
                    continue
                row_time = _parse_instant(row.sent_at)
                if until and row_time > _parse_instant(
                    until, end_of_day=len(until) == 10
                ):
                    continue
                item = (row_time, row.message_id, row.conversation_id, sequence, row)
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
                actual_start = min(
                    (row for row in source_rows if row is not None),
                    key=lambda row: _parse_instant(row.sent_at),
                ).sent_at
                actual_end = max(
                    (row for row in source_rows if row is not None),
                    key=lambda row: _parse_instant(row.sent_at),
                ).sent_at
                try:
                    claimed_start = _parse_instant(item.source_time_start)
                    claimed_end = _parse_instant(item.source_time_end)
                except ValueError:
                    continue
                if (
                    claimed_start > _parse_instant(actual_start)
                    or claimed_end < _parse_instant(actual_end)
                ):
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
            match = relations.get(candidate.statement)
            if match == "none":
                match = DurableMemoryMatch(statement=candidate.statement, relation="none")
            if not isinstance(match, DurableMemoryMatch):
                raise RuntimeError("durable Memory matcher returned incomplete results")
            relation = match.relation
            if relation not in {"none", "exact", "compatible", "contradiction"}:
                raise RuntimeError("durable Memory matcher returned incomplete results")
            if relation == "exact":
                durable_duplicates += 1
                continue
            if relation in {"compatible", "contradiction"}:
                if relation == "compatible":
                    candidate = candidate.model_copy(update={
                        "statement": validate_final_statement(match.merged_statement)
                    })
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
