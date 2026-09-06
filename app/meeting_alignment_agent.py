import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.agent_runtime_router import (
    ApprovedCodexCommandFactory,
    READ_ONLY_BACKGROUND_AGENT_BOUNDARY,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
)
from app.config import principal_display_name, work_profile_path
from app.external_retry import ExternalDependencyError
from app.meeting_alignment_models import (
    MeetingAlignmentDecision,
    MeetingSource,
)
from app.prompt import work_profile_instruction
from app.routed_result_privacy import audit_references_from_full_events
from app.store import CodexSessionSearchResult

MEETING_ALIGNMENT_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "meeting_alignment_decision.schema.json"
)
MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT = 200
MEETING_RUNTIME_CAPABILITIES = frozenset(
    {
        "structured_output",
        "local_schema_validation",
        "reviewed_read_tools",
    }
)
MEETING_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="meeting_alignment.decision.v1"
)


class MeetingAlignmentTargetError(ValueError):
    """A decision target contradicts the authoritative meeting roster."""


class MeetingAlignmentCodex(Protocol):
    last_session_id: str | None
    last_transcript_start_line: int
    last_transcript_end_line: int
    last_audit_tool_events: list[dict[str, str]]

    def decide(self, *, prompt: str, run_id: int) -> MeetingAlignmentDecision: ...


class MeetingAlignmentAgent:
    """Build one isolated meeting prompt and ask Codex for a strict decision."""

    def __init__(self, codex: MeetingAlignmentCodex):
        self.codex = codex

    def decide(
        self,
        source: MeetingSource,
        *,
        similar_sessions: list[CodexSessionSearchResult] | None = None,
        run_id: int | None = None,
    ) -> MeetingAlignmentDecision:
        prompt = build_meeting_alignment_prompt(
            source,
            work_profile=work_profile_instruction(),
            work_profile_source=str(work_profile_path()),
            similar_sessions=similar_sessions or [],
        )
        decision = (
            self.codex.decide(prompt=prompt, run_id=run_id)
            if run_id is not None
            else self.codex.decide(prompt=prompt)
        )
        _validate_source_aware_target(source, decision)
        return decision


class MeetingAlignmentCodexRunner:
    def __init__(
        self,
        *,
        routed_execution: RoutedCodexExecution,
        work_profile_source: str | None = None,
    ):
        from app.codex_decision import extract_codex_audit_events
        from app.codex_history import (
            extract_codex_audit_events_from_session,
        )

        self.routed_execution = routed_execution
        self.work_profile_source = work_profile_source or str(work_profile_path())
        self._extract_codex_audit_events = extract_codex_audit_events
        self._extract_codex_audit_events_from_session = (
            extract_codex_audit_events_from_session
        )
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(self, *, prompt: str, run_id: int) -> MeetingAlignmentDecision:
        # Meeting decisions are intentionally isolated: never resume a reply,
        # task, or earlier meeting session.
        self.last_session_id = None
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0
        self.last_audit_tool_events = []
        try:
            result = self.routed_execution.execute(
                workload_kind="meeting",
                workload_key=str(run_id),
                prompt=prompt,
                command_factory=ApprovedCodexCommandFactory.read_only_meeting(
                    developer_instructions=(
                        "Return exactly one MeetingAlignmentDecision JSON object. "
                        "Use only reviewed read tools.\n\n"
                        + READ_ONLY_BACKGROUND_AGENT_BOUNDARY
                    ),
                    output_schema_path=MEETING_ALIGNMENT_DECISION_SCHEMA_PATH,
                    use_output_schema=True,
                ),
                parser=_encode_meeting_alignment_result,
                result_codec=MEETING_RESULT_CODEC,
                conversation_id=None,
                required_capabilities=MEETING_RUNTIME_CAPABILITIES,
            )
        except RoutedCodexExecutionError as exc:
            if not exc.retryable_external_dependency:
                raise
            raise ExternalDependencyError(
                "codex meeting alignment", exc, dependency="codex"
            ) from exc
        payload = json.loads(result.value)
        self.last_session_id = result.session_id or None
        self.last_transcript_start_line = result.transcript_start
        self.last_transcript_end_line = result.transcript_end
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
            or payload["audit_tool_events"]
        )
        try:
            decision = MeetingAlignmentDecision.model_validate(payload["decision"])
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


def _encode_meeting_alignment_result(raw: str) -> str:
    from app.codex_decision import extract_codex_audit_events

    decision = parse_meeting_alignment_decision(raw)
    return json.dumps(
        {
            "decision": decision.model_dump(mode="json"),
            "audit_tool_events": audit_references_from_full_events(
                extract_codex_audit_events(
                    raw, limit=MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT
                ),
                limit=MEETING_ALIGNMENT_AUDIT_EVENT_LIMIT,
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_meeting_alignment_prompt(
    source: MeetingSource,
    *,
    work_profile: str,
    work_profile_source: str,
    similar_sessions: list[CodexSessionSearchResult] | None = None,
) -> str:
    source_json = json.dumps(
        source.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    target_contract = """内容优先于参会人数：客户、项目、产品、需求、交付、排期、测试、部署、客户沟通或跨团队行动一律是业务内容。
- 业务内容必须返回 audience_scope=business。仅当 action=send 时，必须使用 DWS 做群发现、按业务承接证据给候选群排序，并以最强候选作为 target.kind=group。
- 不能因为是 1:1、群可访问、议题相似或参会人部分重合而私信；没有证据支持的群时返回 action=no_action，绝不使用 direct target。
- personal 只适用于真正个人事项，必须返回 audience_scope=personal。仅当 attendee_evidence=calendar、attendee_roster_complete=true 且恰好两名参会人时，action=send 才能使用 target.kind=direct，并且目标只能是另一位参会人。
- personal 不满足完整日历 1:1 来源时返回 action=no_action；不得以转写、不完整 roster 或多人会议发送 direct。
- action=no_action 时 target=null。"""

    similar_sessions_text = _similar_sessions_prompt_block(similar_sessions or [])

    return f"""你是 Meeting Alignment Agent。你分析已经结束的会议，但不直接发送消息。

范围门禁：
- 先用会议标题、摘要、参会人和完整转写判断它是否是实际候选人面试，也就是面试官正在针对具体岗位询问或评估候选人的会议。
- 招聘站会、招聘计划、人才讨论或招聘需求对齐不属于候选人面试，仍按普通业务会议分析；不要因为讨论招聘就跳过。
- 如果是实际候选人面试，立即返回 action=no_action，并在 audit_summary 说明“实际候选人面试，按范围规则跳过”。action=no_action 时 target=null；不要搜索群、解析 @ 或生成消息。

触发边界：
- 只有出现实质观点分歧，或 {principal_display_name()} 的观点在后续讨论中没有被完整还原、需要做“{principal_display_name()} 的观点输出解读”时，action=send；否则保持安静，action=no_action。
- 只要会议中曾经出现实质观点分歧，后来明确对齐也仍然触发发布；必须总结对齐过程和结论，不能因为最终已对齐而改成 no_action。
- 措辞不同、补充信息、探索性讨论或已经自然顺畅推进，不算实质分歧。
- 沉默不算对齐。只有相关各方明确同意、承诺或复述一致，才把议题标为 aligned；主持人单方面宣布结论不够。
- topics 中有 aligned 时，trigger_reasons 必须包含 aligned_disagreement；topics 中有 unresolved 时，trigger_reasons 必须包含 unresolved_disagreement。两类议题同时存在时两个 trigger 都必须包含。
- 每场会议最多生成一条合并消息；多个议题或同时存在分歧和观点解读时必须合并，不得拆成多条。

内容合同：
- aligned 议题：简述各方观点，并总结最终结论及对齐原因。
- unresolved 议题：简述各方观点和理由，提出完成对齐所需的最小集合。可以提出多个问题，但每个问题必须对应不同且不可合并的关键取舍；不要为了显得完整而堆问题。
- 取舍问题应把“选择什么、牺牲什么、承担什么后果”压缩为可回答的问题；回答最小集合后应能直接导出结论或明确下一步。
- key_questions.answer_owner_names 必须写真正能回答/拍板的人。mention_names 默认只覆盖参会 owner；如果 owner 不是参会人，只有会议中明确说到这是他的任务、由他负责、交给他确认或跟进时，才可以放进 mention_names 并在 final_message 中真实 @。否则可以在正文里写“需要后续同步某某确认”，但不要把这个非参会人放进 mention_names，也不要写成真实 @。
- 每个真实 @ 都必须放在对应的任务、问题或信息所在句子中，让收件人直接看到自己需要处理或知晓的内容；禁止在消息开头集中列一排 @ 人员。mention_names 中的每个人都必须在 final_message 的对应位置以 `@姓名` 出现，发送层会在原位置转换成真实钉钉 @，不会自动补到开头。
- “{principal_display_name()} 的观点输出解读”只能解释 {principal_display_name()} 在会议中明确表达的观点，meeting_evidence 必须引用会议原话或可核验片段。
- 可以结合工作人格和 memory_recall 找到的历史案例、信息来打比方、举例和补全解释，但不能用历史信息发明或替换 {principal_display_name()} 的立场，也不能让历史材料覆盖会议证据。
- 使用历史内容时，historical_sources 必须逐项记录来源。未经 memory_recall 核验时，唯一允许的历史来源是服务端注入的工作人格来源 `{work_profile_source}`；不使用历史内容则返回空列表。
- 能只靠会议证据解释时，historical_sources 必须为空数组。只有实际引用了工作人格中的具体判断或案例时才记录工作人格来源。
- 记录注入的工作人格来源时，historical_sources 的数组元素必须逐字填写 `{work_profile_source}`，不得改写、加标题或写成说明性文字。
- final_message 不要暴露工具、审计过程、本地路径或置信度。

目标合同：
{target_contract}

输出合同：
- 只输出 MeetingAlignmentDecision JSON，严格遵守 schema，不添加字段。
- no_action 时分析和发送字段必须为空，只保留 audit_summary 与 confidence。
- send 时 final_message、trigger_reasons、audience_scope 和明确 target 必须完整，并遵守内容优先于参会人数的目标合同。
- 最终只生成一条可直接发送的合并消息。

服务端注入的工作人格（仅作解释辅助，不能创造会议立场）：
{work_profile or "（无可用工作人格）"}

相似历史 Codex sessions（仅作上下文复用；当前会议证据优先）：
{similar_sessions_text}

完整会议来源 JSON：
{source_json}
"""


def _similar_sessions_prompt_block(
    sessions: list[CodexSessionSearchResult],
) -> str:
    if not sessions:
        return "（无）"
    lines = []
    for index, session in enumerate(sessions, start=1):
        lines.append(
            "\n".join(
                [
                    f"{index}. session_id: {session.session_id}",
                    f"   title: {session.title}",
                    f"   source: {session.source_type}:{session.source_id}",
                    f"   summary: {session.summary_text}",
                    f"   codex_url: /codex/{session.session_id}",
                ]
            )
        )
    return "\n".join(lines)


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

    target = decision.target
    if target is None:
        raise MeetingAlignmentTargetError("send requires an explicit delivery target")
    if decision.audience_scope == "business":
        if target.kind != "group":
            raise MeetingAlignmentTargetError(
                "business send requires a group target"
            )
        return
    if (
        source.attendee_evidence != "calendar"
        or not source.attendee_roster_complete
        or len(source.participants) != 2
    ):
        raise MeetingAlignmentTargetError(
            "personal direct send requires a complete calendar-backed two-person roster"
        )
    other_participants = [
        participant
        for participant in source.participants
        if participant.user_id != source.current_user_id
    ]
    if len(other_participants) != 1:
        raise MeetingAlignmentTargetError(
            "personal 1:1 meeting source must identify exactly one other participant"
        )
    if target.kind != "direct":
        raise MeetingAlignmentTargetError(
            "personal send requires a direct target for the other participant"
        )
    counterpart = other_participants[0]
    expected_user_id = counterpart.user_id
    if expected_user_id and target.direct_user_id != expected_user_id:
        raise MeetingAlignmentTargetError(
            "personal direct target must target the other participant: "
            f"expected {expected_user_id!r}, got {target.direct_user_id!r}"
        )
    if not expected_user_id:
        if target.direct_user_id:
            raise MeetingAlignmentTargetError(
                "unresolved personal 1:1 identity must leave direct_user_id empty; "
                "delivery resolves it from source evidence"
            )
        if _canonical_person_name(target.title) != _canonical_person_name(
            counterpart.name
        ):
            raise MeetingAlignmentTargetError(
                "unresolved personal 1:1 target title must identify the other "
                f"participant: expected {counterpart.name!r}, "
                f"got {target.title!r}"
            )


def _canonical_person_name(value: str) -> str:
    return " ".join(value.split()).casefold()
