import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.config import work_profile_path
from app.meeting_alignment_models import MeetingAlignmentDecision, MeetingSource
from app.prompt import work_profile_instruction


MEETING_ALIGNMENT_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "meeting_alignment_decision.schema.json"
)
MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT = 200


class MeetingAlignmentTargetError(ValueError):
    """A decision target contradicts the authoritative meeting roster."""


class MeetingAlignmentCodex(Protocol):
    last_session_id: str | None
    last_transcript_start_line: int
    last_transcript_end_line: int
    last_audit_tool_events: list[dict[str, str]]

    def decide(self, *, prompt: str) -> MeetingAlignmentDecision: ...


class MeetingAlignmentAgent:
    """Build one isolated meeting prompt and ask Codex for a strict decision."""

    def __init__(self, codex: MeetingAlignmentCodex):
        self.codex = codex

    def decide(self, source: MeetingSource) -> MeetingAlignmentDecision:
        decision = self.codex.decide(
            prompt=build_meeting_alignment_prompt(
                source,
                work_profile=work_profile_instruction(),
                work_profile_source=str(work_profile_path()),
            )
        )
        _validate_source_aware_target(source, decision)
        return decision


class MeetingAlignmentCodexRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor=None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
        work_profile_source: str | None = None,
    ):
        from app.codex_decision import (
            _subprocess_failure_reason,
            extract_codex_audit_events,
            extract_codex_session_id,
        )
        from app.codex_history import (
            count_codex_session_lines,
            extract_codex_audit_events_from_session,
        )
        from app.codex_runner import CodexRunner
        from app.process_runner import run_process_with_idle_timeout

        self.workspace = workspace
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.work_profile_source = work_profile_source or str(work_profile_path())
        self._run_process_with_idle_timeout = run_process_with_idle_timeout
        self._extract_codex_session_id = extract_codex_session_id
        self._extract_codex_audit_events = extract_codex_audit_events
        self._extract_codex_audit_events_from_session = (
            extract_codex_audit_events_from_session
        )
        self._session_line_count = count_codex_session_lines
        self._subprocess_failure_reason = _subprocess_failure_reason
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(self, *, prompt: str) -> MeetingAlignmentDecision:
        # Meeting decisions are intentionally isolated: never resume a reply,
        # task, or earlier meeting session.
        self.last_session_id = None
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0
        self.last_audit_tool_events = []
        raw = self._execute(prompt=prompt)
        self.last_session_id = self._extract_codex_session_id(raw)
        self.last_transcript_end_line = self._session_line_count(
            self.last_session_id
        )
        session_events: list[dict[str, str]] = []
        if self.last_session_id:
            session_events = self._extract_codex_audit_events_from_session(
                self.last_session_id,
                start_line=0,
                end_line=self.last_transcript_end_line,
                limit=MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT,
            )
        self.last_audit_tool_events = (
            session_events
            or self._extract_codex_audit_events(
                raw,
                limit=MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT,
            )
        )
        try:
            decision = parse_meeting_alignment_decision(raw)
            _validate_historical_sources(
                decision,
                audit_tool_events=self.last_audit_tool_events,
                work_profile_source=self.work_profile_source,
            )
        except ValueError as exc:
            raise RuntimeError(
                "Codex did not return a valid MeetingAlignmentDecision"
            ) from exc
        return decision

    def _execute(self, *, prompt: str) -> str:
        command = self.runner.build_command(
            prompt,
            session_id=None,
            image_paths=None,
            output_schema_path=MEETING_ALIGNMENT_DECISION_SCHEMA_PATH,
        )
        if self.executor is not None:
            return self.executor(command, prompt)
        completed = self._run_process_with_idle_timeout(
            command,
            prompt=prompt,
            env=self.runner.build_env(),
            total_timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds,
        )
        if completed.timed_out:
            raise RuntimeError(
                completed.timeout_reason or "meeting alignment codex timed out"
            )
        if completed.returncode != 0:
            raise RuntimeError(
                self._subprocess_failure_reason(
                    completed.stderr, completed.stdout
                )
            )
        return completed.stdout


def build_meeting_alignment_prompt(
    source: MeetingSource,
    *,
    work_profile: str,
    work_profile_source: str,
) -> str:
    source_json = json.dumps(
        source.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    participants = source.participants
    if len(participants) == 2:
        other = next(
            participant
            for participant in participants
            if participant.user_id != source.current_user_id
        )
        if other.user_id:
            direct_identity_contract = (
                f"direct_user_id={other.user_id}、title={other.name}。"
            )
        else:
            open_id_evidence = (
                f"open_dingtalk_id={other.open_dingtalk_id}"
                if other.open_dingtalk_id
                else "没有 open_dingtalk_id"
            )
            direct_identity_contract = (
                f"当前对方 user_id 未解析：direct_user_id 为空、title={other.name}，"
                f"交给发送层唯一解析身份。{open_id_evidence} 只能作为来源证据；"
                "不要把 open_dingtalk_id 填进 direct_user_id。"
            )
        target_contract = f"""这是 1:1 会议：
- 目标只能是另一位参会人，kind=direct、{direct_identity_contract}
- 1:1 会议必须返回 direct target；不能返回 target=null。
- 禁止搜索或选择群；conversation_id 和 candidates 必须为空。"""
    else:
        target_contract = """这是多人会议：
- 只允许发群，不允许私聊或在找不到强关联群时降级为私聊。
- 必须使用 DWS 做群发现并按证据强弱排序：先找会议内明确提及或分享的群链接；再搜会议标题/核心议题消息；再看参会人近期共同活跃；再看组织者/关键发言人重合；再看会前会后时间邻近性；最后查看近期可访问群。
- 每个 candidate 都必须写具体 evidence。target 必须选择候选列表第 1 个（得分最高的可发送群）；即使关联较弱也选择它，关联较弱也不能降级为私聊。
- 如果已经穷尽上述群发现仍没有任何可访问且可发送的群，但会议确实命中 send 触发条件，必须保留 action=send、trigger_reasons、topics/derek_viewpoint、key_questions、mention_names 和 final_message，并返回 target=null，交给发送层重试。
- target=null 是暂时无法投递的运行状态：不能改成 no_action，也绝不能降级为私聊。"""

    return f"""你是 Meeting Alignment Agent。你分析已经结束的会议，但不直接发送消息。

触发边界：
- 只有出现实质观点分歧，或 Derek 的观点在后续讨论中没有被完整还原、需要做“Derek 的观点输出解读”时，action=send；否则保持安静，action=no_action。
- 只要会议中曾经出现实质观点分歧，后来明确对齐也仍然触发发布；必须总结对齐过程和结论，不能因为最终已对齐而改成 no_action。
- 措辞不同、补充信息、探索性讨论或已经自然顺畅推进，不算实质分歧。
- 沉默不算对齐。只有相关各方明确同意、承诺或复述一致，才把议题标为 aligned；主持人单方面宣布结论不够。
- topics 中有 aligned 时，trigger_reasons 必须包含 aligned_disagreement；topics 中有 unresolved 时，trigger_reasons 必须包含 unresolved_disagreement。两类议题同时存在时两个 trigger 都必须包含。
- 每场会议最多生成一条合并消息；多个议题或同时存在分歧和观点解读时必须合并，不得拆成多条。

内容合同：
- aligned 议题：简述各方观点，并总结最终结论及对齐原因。
- unresolved 议题：简述各方观点和理由，提出完成对齐所需的最小集合。可以提出多个问题，但每个问题必须对应不同且不可合并的关键取舍；不要为了显得完整而堆问题。
- 取舍问题应把“选择什么、牺牲什么、承担什么后果”压缩为可回答的问题；回答最小集合后应能直接导出结论或明确下一步。
- key_questions.answer_owner_names 必须写真正能回答/拍板的人；mention_names 必须覆盖这些 owner。final_message 中使用其真实姓名形成真实 @ 的语义，发送层会解析为钉钉真实 @，不要写泛称“相关同学”。
- “Derek 的观点输出解读”只能解释 Derek 在会议中明确表达的观点，meeting_evidence 必须引用会议原话或可核验片段。
- 可以结合工作人格和 memory_recall 找到的历史案例、信息来打比方、举例和补全解释，但不能用历史信息发明或替换 Derek 的立场，也不能让历史材料覆盖会议证据。
- 使用历史内容时，historical_sources 必须逐项记录来源。未经 memory_recall 核验时，唯一允许的历史来源是服务端注入的工作人格来源 `{work_profile_source}`；不使用历史内容则返回空列表。
- 能只靠会议证据解释时，historical_sources 必须为空数组。只有实际引用了工作人格中的具体判断或案例时才记录工作人格来源。
- 记录注入的工作人格来源时，historical_sources 的数组元素必须逐字填写 `{work_profile_source}`，不得改写、加标题或写成说明性文字。
- final_message 不要暴露工具、审计过程、本地路径或置信度。

目标合同：
{target_contract}

输出合同：
- 只输出 MeetingAlignmentDecision JSON，严格遵守 schema，不添加字段。
- no_action 时分析和发送字段必须为空，只保留 audit_summary 与 confidence。
- send 时 final_message 和 trigger_reasons 必须完整；target 通常必填，唯一例外是多人会议已经穷尽群发现却没有可发送群，此时 target=null 供发送层重试。1:1 会议始终必须返回另一位参会人的 direct target。
- 最终只生成一条可直接发送或等待发送层重试的合并消息。

服务端注入的工作人格（仅作解释辅助，不能创造会议立场）：
{work_profile or "（无可用工作人格）"}

完整会议来源 JSON：
{source_json}
"""


def parse_meeting_alignment_decision(raw: str) -> MeetingAlignmentDecision:
    stripped = raw.strip()
    try:
        return MeetingAlignmentDecision.model_validate_json(stripped)
    except (ValueError, ValidationError):
        pass

    payloads: list[object] = []
    for line in stripped.splitlines():
        try:
            payloads.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
    for payload in reversed(payloads):
        try:
            return MeetingAlignmentDecision.model_validate(payload)
        except (ValueError, ValidationError):
            pass
        if not isinstance(payload, dict):
            continue
        for text in _decision_text_candidates(payload):
            try:
                return MeetingAlignmentDecision.model_validate_json(text)
            except (ValueError, ValidationError):
                continue
    raise ValueError("No MeetingAlignmentDecision JSON found")


def _decision_text_candidates(payload: dict[str, object]) -> list[str]:
    candidates: list[str] = []
    for key in ("text", "output_text"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
    item = payload.get("item")
    if isinstance(item, dict):
        candidates.extend(_decision_text_candidates(item))
    content = payload.get("content")
    if isinstance(content, list):
        for value in content:
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                candidates.append(value["text"])
    return candidates


def _validate_historical_sources(
    decision: MeetingAlignmentDecision,
    *,
    audit_tool_events: list[dict[str, str]],
    work_profile_source: str,
) -> None:
    viewpoint = decision.derek_viewpoint
    if viewpoint is None or not viewpoint.historical_sources:
        return
    used_memory_recall = any(
        "memory_recall" in str(event.get("tool", "")).casefold()
        for event in audit_tool_events
    )
    if used_memory_recall:
        return
    if all(source == work_profile_source for source in viewpoint.historical_sources):
        return
    raise ValueError(
        "historical_sources require memory_recall audit evidence or the "
        "configured work profile source"
    )


def _validate_source_aware_target(
    source: MeetingSource,
    decision: MeetingAlignmentDecision,
) -> None:
    if decision.action == "no_action":
        return

    participant_count = len(source.participants)
    target = decision.target
    if participant_count == 2:
        other_participants = [
            participant
            for participant in source.participants
            if participant.user_id != source.current_user_id
        ]
        if len(other_participants) != 1:
            raise MeetingAlignmentTargetError(
                "1:1 meeting source must identify exactly one other participant"
            )
        if target is None or target.kind != "direct":
            raise MeetingAlignmentTargetError(
                "1:1 send requires a direct target for the other participant"
            )
        counterpart = other_participants[0]
        expected_user_id = counterpart.user_id
        if expected_user_id and target.direct_user_id != expected_user_id:
            raise MeetingAlignmentTargetError(
                "1:1 direct target must target the other participant: "
                f"expected {expected_user_id!r}, got {target.direct_user_id!r}"
            )
        if not expected_user_id:
            if target.direct_user_id:
                raise MeetingAlignmentTargetError(
                    "unresolved 1:1 identity must leave direct_user_id empty; "
                    "delivery resolves it from source evidence"
                )
            if _canonical_person_name(target.title) != _canonical_person_name(
                counterpart.name
            ):
                raise MeetingAlignmentTargetError(
                    "unresolved 1:1 target title must identify the other "
                    f"participant: expected {counterpart.name!r}, "
                    f"got {target.title!r}"
                )
        return

    if participant_count > 2:
        if target is not None and target.kind == "direct":
            raise MeetingAlignmentTargetError(
                "multi-party send cannot use a direct target"
            )
        return

    raise MeetingAlignmentTargetError(
        "send decision requires at least two meeting participants"
    )


def _canonical_person_name(value: str) -> str:
    return " ".join(value.split()).casefold()
