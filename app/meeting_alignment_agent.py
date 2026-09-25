import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.agent_result import agent_message_json_objects
from app.agent_runtime_router import (
    CodexCommandFactory,
    BACKGROUND_AGENT_RUNTIME_BOUNDARY,
    RoutedCodexExecution,
    RoutedCodexExecutionError,
    RoutedResultCodec,
    RoutedResultValidationError,
    RoutedResultValidationRetry,
)
from app.config import principal_display_name, work_profile_path
from app.external_retry import ExternalDependencyError
from app.meeting_alignment_models import (
    DeliveryTarget,
    MeetingAlignmentDecision,
    MeetingSource,
    meeting_alignment_rule_for_message,
    render_meeting_alignment_cross_field_rules,
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
    }
)
MEETING_RESULT_CODEC = RoutedResultCodec.text(
    schema_id="meeting_alignment.decision.v1"
)
def _meeting_alignment_prompt_schema() -> str:
    """Serialize the decision schema for the prompt, minus its root description.

    Third-party providers ignore Codex's --output-schema, so the prompt text is
    the only place the model sees the AlignmentTopic / DeliveryTarget shapes.
    The root description carries the cross-field rules for the routes that do
    read the schema file; both prompts already print the same block above the
    schema, so dropping it here keeps one copy per prompt instead of two.
    """
    schema = MeetingAlignmentDecision.model_json_schema()
    schema.pop("description", None)
    return json.dumps(schema, ensure_ascii=False, indent=2)


MEETING_ALIGNMENT_DECISION_PROMPT_SCHEMA = _meeting_alignment_prompt_schema()
MEETING_ALIGNMENT_SCHEMA_PROBLEM_LIMIT = 12
MEETING_SOURCE_TARGET_REPAIR_LIMIT = 1


class MeetingAlignmentTargetError(ValueError):
    """A decision target contradicts the authoritative meeting roster."""

    def __init__(
        self,
        message: str,
        *,
        decision: MeetingAlignmentDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision


class MeetingOrganizerIdentityError(MeetingAlignmentTargetError):
    """The calendar organizer cannot be addressed from stable source data."""


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
        group_candidates: list[dict[str, Any]] | None = None,
        run_id: int | None = None,
        consumer_prompt: str = "",
        skill_protocol: str = "",
    ) -> MeetingAlignmentDecision:
        prompt = build_meeting_alignment_prompt(
            source,
            work_profile=work_profile_instruction(),
            work_profile_source=str(work_profile_path()),
            similar_sessions=similar_sessions or [],
            group_candidates=group_candidates or [],
            consumer_prompt=consumer_prompt,
            skill_protocol=skill_protocol,
        )
        for repair_attempt in range(MEETING_SOURCE_TARGET_REPAIR_LIMIT + 1):
            decision = (
                self.codex.decide(prompt=prompt, run_id=run_id)
                if run_id is not None
                else self.codex.decide(prompt=prompt)
            )
            try:
                _validate_source_aware_target(
                    source, decision, group_candidates=group_candidates or []
                )
            except MeetingAlignmentTargetError as exc:
                if repair_attempt >= MEETING_SOURCE_TARGET_REPAIR_LIMIT:
                    raise type(exc)(
                        str(exc),
                        decision=decision,
                    ) from exc
                prompt = _meeting_alignment_target_repair_prompt(
                    original_prompt=prompt,
                    decision=decision,
                    problem=str(exc),
                )
                continue
            return decision
        raise RuntimeError("meeting alignment target repair did not return a decision")


class MeetingAlignmentCodexRunner:
    def __init__(
        self,
        *,
        routed_execution: RoutedCodexExecution,
    ):
        from app.codex_decision import extract_codex_audit_events
        from app.codex_history import (
            extract_codex_audit_events_from_session,
        )

        self.routed_execution = routed_execution
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
                command_factory=CodexCommandFactory.standard(
                    developer_instructions=(
                        "Return exactly one MeetingAlignmentDecision JSON object.\n\n"
                        + BACKGROUND_AGENT_RUNTIME_BOUNDARY
                    ),
                    output_schema_path=MEETING_ALIGNMENT_DECISION_SCHEMA_PATH,
                    use_output_schema=True,
                ),
                parser=_encode_meeting_alignment_result,
                result_codec=MEETING_RESULT_CODEC,
                conversation_id=None,
                required_capabilities=MEETING_RUNTIME_CAPABILITIES,
                # Providers that ignore the output schema (third-party APIs)
                # get one same-session correction naming the schema errors.
                result_validation_retry=RoutedResultValidationRetry.same_session_exactly_once(
                    correction_prompt=_meeting_alignment_repair_prompt
                ),
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


def _meeting_alignment_repair_prompt(raw: str) -> str:
    """Tell the model which schema rules its last decision broke, and their shapes."""
    problems = _meeting_alignment_schema_problems(raw)
    detail = (
        "\n".join(_meeting_alignment_problem_line(problem) for problem in problems)
        if problems
        else "- the reply did not contain a MeetingAlignmentDecision JSON object"
    )
    return (
        "上一次输出不是合法的 MeetingAlignmentDecision JSON。请基于同一个上下文重新输出，"
        "只输出一个满足 schema 的 JSON 对象，不要调用工具，不要发送消息。\n\n"
        f"上一次输出的问题：\n{detail}\n\n"
        # A pydantic after-validator stops at the first broken rule and this is
        # the only correction turn, so repeating every rule is what keeps the
        # model from fixing the reported one and breaking the next.
        f"{render_meeting_alignment_cross_field_rules()}\n\n"
        "MeetingAlignmentDecision Pydantic JSON schema:\n"
        f"{MEETING_ALIGNMENT_DECISION_PROMPT_SCHEMA}"
    )


def _meeting_alignment_target_repair_prompt(
    *,
    original_prompt: str,
    decision: MeetingAlignmentDecision,
    problem: str,
) -> str:
    return (
        "上一次目标选择没有通过实时会议来源校验。请基于同一个会议来源重新输出，"
        "只输出一个满足 schema 且目标可执行的 MeetingAlignmentDecision JSON 对象，"
        "不要调用工具，不要发送消息。\n\n"
        f"上一次目标错误：{problem}\n\n"
        "上一次输出：\n"
        f"{decision.model_dump_json()}\n\n"
        "原始任务与完整会议来源如下：\n"
        f"{original_prompt}"
    )


def _meeting_alignment_problem_line(problem: str) -> str:
    """Name the field combination a reported cross-field problem needs."""
    rule = meeting_alignment_rule_for_message(problem)
    return f"- {problem}（需要：{rule.requirement}）" if rule else f"- {problem}"


def _meeting_alignment_schema_problems(raw: str) -> list[str]:
    """List the schema errors of the last decision candidate, one per field path."""
    for payload in reversed(_raw_message_json_objects(raw)):
        try:
            MeetingAlignmentDecision.model_validate(payload)
        except ValidationError as exc:
            # A wrong topic shape repeats the same errors for every topic;
            # collapsing list indices keeps top-level errors within the cap.
            problems = list(
                dict.fromkeys(
                    _schema_problem(error["loc"], error["msg"])
                    for error in exc.errors()
                )
            )
            return problems[:MEETING_ALIGNMENT_SCHEMA_PROBLEM_LIMIT]
        except ValueError as exc:
            return [str(exc)[:300]]
    return []


def _schema_problem(loc: tuple[int | str, ...], message: str) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += "[]"
        else:
            path = f"{path}.{part}" if path else str(part)
    # Model-level validators report an empty location.
    return f"{path}: {message}" if path else message


def _raw_message_json_objects(raw: str) -> list[object]:
    """Collect decision candidates from raw text or a Codex JSONL stream."""
    stripped = raw.strip()
    candidates = agent_message_json_objects(stripped)
    for line in stripped.splitlines():
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            for text in _decision_text_candidates(payload):
                candidates.extend(agent_message_json_objects(text))
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and "action" in candidate
    ]


def build_meeting_alignment_prompt(
    source: MeetingSource,
    *,
    work_profile: str,
    work_profile_source: str,
    similar_sessions: list[CodexSessionSearchResult] | None = None,
    group_candidates: list[dict[str, Any]] | None = None,
    consumer_prompt: str = "",
    skill_protocol: str = "",
) -> str:
    source_json = json.dumps(
        source.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    target_contract = """每场会议都必须生成并发送一条会议总结，action 只能是 send；不得返回 no_action。
- 内容优先于参会人数：客户、项目、产品、需求、交付、排期、测试、部署、客户沟通或跨团队行动一律是业务内容，必须返回 audience_scope=business，并使用 DWS 做群发现、按业务承接证据给候选群排序，以最强候选作为 target.kind=group。
- 会议标题只是线索。优先核对会议材料中明确提及或分享的讨论群，再从会议结论、行动项和负责人提炼业务主题与预期受众。审阅下方预置候选，同时使用原文中的中文业务词与英文术语分别搜索 DWS，搜索会议标题和核心议题对应的群消息；必要时按工作线、项目、交付对象或行动负责人交叉搜索。没有预置候选或首次搜索零命中，不等于业务群发现失败。
- 对每个可能的群，核对群内近期消息是否讨论同一工作线、谁在承接本次行动、群成员是否属于该议题的合理受众，以及群当前是否可发送。为每个有效候选写清候选群的来源、业务承接关系和受众证据；排除仅名称相似、成员重合但业务不符的群。多个合理群按本次议题和行动的实际归属排序，不按群规模或搜索顺序决定。不得为完成发送而选择宽泛群；候选群中含非授权受众时，不得把敏感内容带入群消息。
- 只有在不同业务线索均已搜索、候选的消息与受众证据均已核对，仍没有可核验且可发送的业务群时，才算业务群发现失败；在 audit_summary 简述已核对的线索和排除原因，然后使用日历中已确认的会议组织者作为 direct fallback。不得因 1:1 或未搜索就私信，也不得按姓名模糊搜索目标。
- 候选标记 verified_recurring_group 时，表示同名会议已多次成功投递到该群，且本次完整日历名册中多数可核验参会人仍在群内；仍须比较本次实际议题与群内近期讨论，不得仅凭历史投递或参会人覆盖率选群。
- topic_discussion_evidence 是群内近期消息与本次会议摘要的词项交集线索，必须阅读其具体内容、时间和业务负责人，再判断是否承接本次议题。参会人覆盖率只用于核对受众，不证明业务归属；群成员多不代表更合适。优先选择讨论过同一工作线、负责本次行动且受众合适的群。
- personal 只适用于整场会议均为个人事项，必须返回 audience_scope=personal；只有完整日历 1:1（attendee_evidence=calendar、attendee_roster_complete=true、恰好两名参会人）时，才可以使用 target.kind=direct，目标只能是另一位参会人。
- 业务会议中出现人员评价、绩效、薪酬、晋升、去留、候选人结论、健康或请假等人员敏感内容时，先按受众决定是否拆分：如果 DWS 实时群发现证明目标是 HR 专属或已匹配的群，且讨论是会议中 HR 参会人的共同事项、不是针对未参会的具体个人，可以把敏感详情放进 final_message，并将 sensitive_private_message=null；只要群受众不明确、含非授权成员，或讨论针对具体个人，就必须去掉群消息中的敏感详情，写入 sensitive_private_message。
- sensitive_private_message.target 必须是 direct，并使用参会人中经 DWS 实时身份和职责确认的 HR/人员负责人稳定 user_id；没有可确认的 HR/人员负责人时发给当前用户本人。recipient_evidence 写清实时身份或职责依据。不得按姓名猜测接收人，也不得发给被评价人或无关参会人。
- 普通的工作分工、交付进展、项目风险和业务结果不是人员敏感内容，不得因为出现姓名就从群消息中删除。
- 没有实质观点分歧时，仍须发送简短的会议结论、已确认事项和下一步；不得因议题平稳而跳过。"""

    similar_sessions_text = _similar_sessions_prompt_block(similar_sessions or [])
    group_candidates_text = json.dumps(
        group_candidates or [], ensure_ascii=False, separators=(",", ":")
    )
    scheduled_consumer_block = ""
    if consumer_prompt.strip() or skill_protocol.strip():
        scheduled_consumer_block = f"""## Scheduled Consumer Prompt
{consumer_prompt.strip()}

## Scheduled Consumer Skills
{skill_protocol.strip()}
"""

    return f"""你是 Meeting Alignment Agent。你分析已经结束的会议，但不直接发送消息。

{scheduled_consumer_block}

触发边界：
- 每场会议均须发送一条总结。出现实质观点分歧，或 {principal_display_name()} 的观点在后续讨论中没有被完整还原时，重点说明对齐或待决事项；没有分歧时，简洁归纳已确认事项和下一步。
- 只要会议中曾经出现实质观点分歧，后来明确对齐也仍然触发发布；必须总结对齐过程和结论，不能因为最终已对齐而改成 no_action。
- 措辞不同、补充信息、探索性讨论或已经自然顺畅推进，不算实质分歧。
- 沉默不算对齐。只有相关各方明确同意、承诺或复述一致，才把议题标为 aligned；主持人单方面宣布结论不够。
- topics 中有 aligned 时，trigger_reasons 必须包含 aligned_disagreement；topics 中有 unresolved 时，trigger_reasons 必须包含 unresolved_disagreement。两类议题同时存在时两个 trigger 都必须包含。
- 每场会议最多生成一条业务群消息与一条敏感私聊消息；同一受众的多个议题必须合并，不得按议题拆成多条。HR 群可以承接其共同受众的敏感内容，此时只生成一条 HR 群消息，不再重复生成敏感私聊。

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
- final_message 只写正文；发送层会加会议标题和时间，不要重复会议标题或时间。用 Markdown 把正文结构化，提高可读性和信息密度——钉钉按 Markdown 渲染这些消息，加粗、标题和列表都会生效。按实际内容用加粗小节标签（如 **结论**、**后续行动**、**待确认**），小节之间空一行；没有内容的小节不写。多条并列的结论、事项或行动写成 `- ` 列表，列表前空一行、每条单独一行，写清责任人、动作与时间或验收点；单条内容直接成段即可，不必强行套列表。真实 @ 保留在对应的列表项或句子内，不集中到开头。
- final_message 和 sensitive_private_message.message 都不要暴露工具、审计过程、本地路径或置信度。

目标合同：
{target_contract}

输出合同：
- 只输出 MeetingAlignmentDecision JSON，严格遵守下方 schema，不添加字段。
- action 固定为 send；final_message、trigger_reasons、audience_scope 和明确 target 必须完整，并遵守内容优先于参会人数的目标合同。
- 没有人员敏感内容时 sensitive_private_message 必须为 null；存在混合内容时，按目标群的受众边界选择：匹配的 HR 群共同讨论可将敏感详情放在 final_message 并把 sensitive_private_message 设为 null；针对具体个人或受众不明确时，生成脱敏后的 final_message 和独立 sensitive_private_message。

{render_meeting_alignment_cross_field_rules()}

MeetingAlignmentDecision Pydantic JSON schema:
{MEETING_ALIGNMENT_DECISION_PROMPT_SCHEMA}

服务端注入的工作人格（仅作解释辅助，不能创造会议立场）：
{work_profile or "（无可用工作人格）"}

相似历史 Codex sessions（仅作上下文复用；当前会议证据优先）：
{similar_sessions_text}

实时钉钉群搜索候选（仅供进一步核验，不是直接发送授权）：
{group_candidates_text}

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
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        decision = _normalized_decision(payload)
        if decision is not None:
            return decision
    if decision := _embedded_decision(stripped):
        return decision

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
        decision = _normalized_decision(payload)
        if decision is not None:
            return decision
        for text in _decision_text_candidates(payload):
            if decision := _embedded_decision(text):
                return decision
    # Typed so the router runs the same-session correction turn instead of
    # terminalizing the attempt as runtime_result_invalid. The message is
    # logged by the router, so it carries field paths, never the raw output.
    problems = _meeting_alignment_schema_problems(stripped)
    raise RoutedResultValidationError(
        (
            "MeetingAlignmentDecision schema mismatch: " + "; ".join(problems)
            if problems
            else "No MeetingAlignmentDecision JSON found"
        ),
        raw_output=raw,
    )


def _embedded_decision(text: str) -> MeetingAlignmentDecision | None:
    # Fenced or prose-wrapped output: the last complete decision wins.
    for payload in reversed(agent_message_json_objects(text)):
        try:
            return MeetingAlignmentDecision.model_validate(payload)
        except (ValueError, ValidationError):
            if isinstance(payload, dict):
                decision = _normalized_decision(payload)
                if decision is not None:
                    return decision
    return None


def _normalized_decision(payload: dict[str, object]) -> MeetingAlignmentDecision | None:
    normalized = _normalize_decision_shape(payload)
    if normalized == payload:
        return None
    try:
        return MeetingAlignmentDecision.model_validate(normalized)
    except (ValueError, ValidationError):
        return None


def _normalize_decision_shape(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    trigger_reasons = _normalize_trigger_reasons(
        normalized.get("trigger_reasons"),
        normalized.get("topics"),
        normalized.get("derek_viewpoint"),
    )
    if trigger_reasons is not None:
        normalized["trigger_reasons"] = trigger_reasons
    target = _normalize_delivery_target(normalized.get("target"))
    if target is not None:
        normalized["target"] = target
    private_message = normalized.get("sensitive_private_message")
    if isinstance(private_message, dict):
        private_normalized = dict(private_message)
        private_target = _normalize_delivery_target(private_normalized.get("target"))
        if private_target is not None:
            private_normalized["target"] = private_target
        normalized["sensitive_private_message"] = private_normalized
    return normalized


def _normalize_trigger_reasons(
    raw_trigger_reasons: object,
    raw_topics: object,
    raw_derek_viewpoint: object,
) -> list[object] | None:
    if not isinstance(raw_trigger_reasons, list):
        return None
    topic_states = set()
    if isinstance(raw_topics, list):
        topic_states = {
            topic.get("state")
            for topic in raw_topics
            if isinstance(topic, dict) and isinstance(topic.get("state"), str)
        }
    derived = list(dict.fromkeys(raw_trigger_reasons))
    if "aligned" in topic_states:
        derived = _append_once(derived, "aligned_disagreement")
    else:
        derived = [reason for reason in derived if reason != "aligned_disagreement"]
    if "unresolved" in topic_states:
        derived = _append_once(derived, "unresolved_disagreement")
    else:
        derived = [
            reason for reason in derived if reason != "unresolved_disagreement"
        ]
    if raw_derek_viewpoint is None:
        derived = [reason for reason in derived if reason != "derek_viewpoint"]
    elif isinstance(raw_derek_viewpoint, dict):
        derived = _append_once(derived, "derek_viewpoint")
    return derived


def _append_once(values: list[object], value: str) -> list[object]:
    return values if value in values else [*values, value]


def _normalize_delivery_target(raw_target: object) -> Any:
    if not isinstance(raw_target, dict):
        return raw_target
    target = dict(raw_target)
    kind = target.get("kind")
    if kind == "group":
        candidates = target.get("candidates")
        first_candidate = (
            candidates[0]
            if isinstance(candidates, list)
            and candidates
            and isinstance(candidates[0], dict)
            else None
        )
        if first_candidate is not None:
            if not str(target.get("conversation_id") or "").strip():
                target["conversation_id"] = str(
                    first_candidate.get("conversation_id") or ""
                )
            if not str(target.get("title") or "").strip():
                target["title"] = str(first_candidate.get("title") or "")
        target["direct_user_id"] = ""
    elif kind == "direct":
        target["conversation_id"] = ""
        target["candidates"] = []
    return target


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


def _validate_source_aware_target(
    source: MeetingSource,
    decision: MeetingAlignmentDecision,
    *,
    group_candidates: list[dict[str, Any]] | None = None,
) -> None:
    target = decision.target
    if target is None:
        raise MeetingAlignmentTargetError("send requires an explicit delivery target")
    if decision.audience_scope == "business":
        if target.kind == "direct":
            verified_groups = [
                candidate
                for candidate in group_candidates or []
                if candidate.get("verified_recurring_group") is True
            ]
            if verified_groups:
                raise MeetingAlignmentTargetError(
                    "business direct fallback is invalid with verified recurring group: "
                    + ", ".join(
                        str(candidate["title"]) for candidate in verified_groups
                    )
                )
            _validate_business_direct_fallback(source, target)
        elif target.kind != "group":
            raise MeetingAlignmentTargetError(
                "business send requires a group target or calendar organizer fallback"
            )
        private_message = decision.sensitive_private_message
        if private_message is not None:
            matches = [
                participant
                for participant in source.participants
                if participant.user_id
                == private_message.target.direct_user_id
            ]
            if len(matches) != 1:
                raise MeetingAlignmentTargetError(
                    "sensitive private target must identify one meeting participant"
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


def _validate_business_direct_fallback(
    source: MeetingSource,
    target: DeliveryTarget,
) -> None:
    organizer = source.creator
    if organizer is None or not organizer.name.strip():
        raise MeetingAlignmentTargetError(
            "business direct fallback requires a calendar organizer"
        )
    if _canonical_person_name(target.title) != _canonical_person_name(organizer.name):
        raise MeetingAlignmentTargetError(
            "business direct fallback must target the calendar organizer"
        )
    if organizer.user_id.strip():
        if target.direct_user_id != organizer.user_id.strip():
            raise MeetingAlignmentTargetError(
                "business direct fallback must use the calendar organizer user_id"
            )
        return
    if target.direct_user_id:
        raise MeetingOrganizerIdentityError(
            "business direct fallback cannot supply a guessed user_id"
        )


def _canonical_person_name(value: str) -> str:
    return " ".join(value.split()).casefold()
