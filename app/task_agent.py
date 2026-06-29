import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.store import AutoReplyStore, RecentFollowUpCandidate
from app.codex_runner import memory_connector_config_issue
from app.task_models import (
    FollowUpDraftChange,
    FollowUpDraftDecision,
    TaskAgentDecision,
    TodoChange,
    TodoStatus,
    WorkItem,
    WorkItemSourceType,
    WorkSummaryInput,
)
from app.task_retrieval import render_candidate_prompt, retrieve_project_candidates
from app.todo_sync import maybe_create_dingtalk_todo, sync_completed_todo_to_dingtalk


TASK_AGENT_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "task_agent_decision.schema.json"
)
TASK_AGENT_AUDIT_EVENT_LIMIT = 200
RECENT_FOLLOW_UP_CONTEXT_WINDOW = timedelta(days=7)
FOLLOW_UP_WORK_START_HOUR = 9
FOLLOW_UP_WORK_END_HOUR = 18


class TaskCodex(Protocol):
    last_session_id: str
    last_transcript_start_line: int
    last_transcript_end_line: int

    def decide(
        self,
        *,
        prompt: str,
        session_id: str | None = None,
    ) -> TaskAgentDecision: ...


class TaskAgentRunner:
    def __init__(self, codex: TaskCodex):
        self.codex = codex

    def decide(
        self,
        work_item: WorkItem,
        candidate_prompt: str,
        *,
        memory_issue: str = "",
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_task_agent_prompt(
                work_item,
                candidate_prompt,
                memory_issue=memory_issue,
            ),
            session_id=None,
        )


class TaskAgentCodexRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor=None,
        timeout_seconds: int = 420,
        idle_timeout_seconds: int = 180,
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

    def decide(
        self,
        *,
        prompt: str,
        session_id: str | None = None,
    ) -> TaskAgentDecision:
        self.last_transcript_start_line = self._session_line_count(session_id)
        raw = self._execute(prompt=prompt, session_id=session_id)
        self.last_session_id = self._extract_codex_session_id(raw) or session_id
        self.last_transcript_end_line = self._session_line_count(self.last_session_id)
        session_events = []
        if self.last_session_id:
            session_events = self._extract_codex_audit_events_from_session(
                self.last_session_id,
                start_line=self.last_transcript_start_line,
                end_line=self.last_transcript_end_line,
                limit=TASK_AGENT_AUDIT_EVENT_LIMIT,
            )
        self.last_audit_tool_events = (
            session_events or self._extract_codex_audit_events(raw)
        )
        return _parse_task_agent_decision(raw)

    def _execute(self, *, prompt: str, session_id: str | None) -> str:
        command = self.runner.build_command(
            prompt,
            session_id,
            image_paths=None,
            output_schema_path=TASK_AGENT_DECISION_SCHEMA_PATH,
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
            raise RuntimeError(completed.timeout_reason or "task agent codex timed out")
        if completed.returncode != 0:
            raise RuntimeError(
                self._subprocess_failure_reason(completed.stderr, completed.stdout)
            )
        return completed.stdout


def build_task_agent_prompt(
    work_item: WorkItem,
    candidate_prompt: str,
    *,
    memory_issue: str = "",
) -> str:
    work_item_json = json.dumps(
        work_item.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    memory_status = _memory_connector_prompt_status(memory_issue)
    return f"""你是 CEO Agent task agent。

职责边界：
- 你只更新工作项目和 TODO，不回复当前消息。
- Work Item 是一个输入片段，不是已经抽取好的事实；必须判断其是否足够支撑稳定项目、TODO 或完成证据。
- Task 只记录需要持续管理的公司事项；一次性工具、账号、权限、订阅或行政操作默认不创建 task，也不生成 follow-up，除非它明确影响已有项目、关键交付、成本风险或管理决策。
- 每次必须评估 failure_risk 和 failure_risk_score：failure_risk 说明如果不跟进会发生什么；failure_risk_score 是 0 到 1 的失败风险，0 表示几乎无业务影响，1 表示会直接影响关键交付、收入、合规或管理决策。
- BM25 候选项目只是初始线索，不是权威匹配结果。
- 如果候选项目为空或你判断不匹配，可以使用 dws 或 memory_connector 恢复更多上下文；这是提示，不是硬性要求。
- 近期 follow-up 候选只是上下文线索。你必须自己判断当前 Work Item 是否真的回应了某条 follow-up；不能因为候选存在就关闭 TODO 或 suppress follow-up。
- 如果 Work Item 明确说明追错 owner、重复追问或不应继续跟进，可以通过 follow_up_changes 更新已有 follow_up_draft；不要生成新的 follow_up_draft 来继续追同一个错误 owner。
- 只有当前消息和候选上下文共同明确证明 TODO 完成时，才把 todo_changes 写成 close 并提供 completion_evidence。
- 已完成、已取消、已删除或用户明确表示不应继续跟进的 TODO 是负向证据；不要为了同一事项新建 TODO 或 follow_up_draft，优先关闭/取消/抑制已有项或仅更新项目背景。
- 同一事项从不同会议听记、文档或消息重复出现时，只能合并到既有 TODO；不要换标题重新创建。
- memory_connector 是外部辅助服务，不能成为 task agent 的运行依赖。
- 如果 memory_connector 状态为可用，create_project 或 update_project 前必须直接调用 memory_recall MCP 工具查历史背景；不要传入或编造 user_id。
- list_mcp_resources、list_mcp_resource_templates、memory_get、timeline_get 或本地搜索都不能替代 memory_recall；只有实际调用 memory_recall 并获得可用记忆结果后，memory_recall_used 才能为 true。
- 如果当前运行时确实没有暴露可直接调用的 memory_recall 工具，先用工具发现结果证明不可用，再继续处理；此时 memory_recall_used=false，并在 project.memory_context.memories 写入一条 source="memory_connector_runtime_unavailable" 的证据说明。
- 如果实际调用了 memory_recall 但该工具超时或传输失败，继续处理；此时 memory_recall_used=false，并在 project.memory_context.memories 写入一条 source="memory_recall_runtime_failure" 的证据说明。
- 如果 memory_connector 状态为不可用，不要因此停止任务、不要输出 critical_info_unavailable、不要把任务转人工；改用 Work Item、候选项目、DWS 或本地上下文判断。此时 memory_recall_used=false，project.memory_context 写明原本会查询什么、memory_connector 不可用的原因，以及你实际采用的替代证据。
- project.memory_context 必须写入本次记忆查询或替代依据：memory_recall 有命中时写查询、摘要和关键记忆证据；没有命中时写查询和无命中结论；memory_connector 不可用时写查询意图、不可用原因和替代证据。
- 如果上下文无法支撑稳定项目名称，不要创建模糊项目；生成 follow_up_draft 询问项目、目标、owner。
- AI听记或本地听记的说话人标签只能作为弱证据；如果多人会议的 transcript 大段只有同一个 speaker，说明说话人标注不可信，不能据此认定 owner，也不能直接私聊该 speaker。
- 行政、工商、法务、财务、人事合规类事项必须区分汇报人、推动人和实际执行 owner；只有材料明确写出“某人负责/待办/owner/由某人完成”且不是低可信说话人标签推导时，才能给该人生成 follow_up_draft。否则只更新项目背景或生成需要确认真实 owner 的 TODO，不要直接私聊。
- 只有消息、会议纪要或文档明确证明 TODO 完成时，才能自动清理 TODO，并写入 completion_evidence。
- 生成 follow_up_draft 前必须确定 owner_user_id；只有 owner_name 不够。如果上下文缺少 userId，先用 dws 或已有联系人信息补齐；仍无法唯一确定时，不要生成 follow_up_draft。
- 每个 follow_up_draft 必须绑定一个 TODO：跟进已有 TODO 时填写 todo_id；跟进本次新建 TODO 时，todo_changes.create 和 follow_up_drafts 使用相同的 todo_ref，系统会把 todo_ref 转成真实 todo_id。不能生成没有 TODO 绑定的 follow_up_draft。
- follow_up_draft.status 固定填 draft；不要用 approved 表达“需审批”。项目跟进发送必须依赖 risk_check 审计：sensitive=true 表示不能在群里公开追问，发送端会优先转私聊或延后。
- follow_up_draft.target_kind 只表示实际发送位置：能回到来源群聊就用 group，并且 target_conversation_id 必须填写 DWS 可直接发送的 openConversationId（通常以 cid 开头）；不要填 AI 搜问或业务搜索结果里的普通群号/数字群号。不能确定 openConversationId 但已确定 owner_user_id 时用 direct。不要把“没有群上下文”写成 owner_in_group=false 来阻断发送。
- risk_check 是结构化输出必填的审计说明。涉及人事、试用期、转正、绩效、薪酬、offer、候选人隐私、客户敏感承诺或财务敏感信息时，必须设置 sensitive=true，并优先使用 direct target。
- risk_check.owner_in_group 只记录 group target 是否包含 owner；direct target 可填 false 表示不适用，但不能用它阻断发送。
- follow_up_draft.question_text 必须包含一句简短来源或依据，例如“基于某群/某会议/某文档提到的事项”，避免让 owner 不知道 AI 为什么突然追问；措辞必须是确认进展，不要像分配新任务。
- 跟进时间指导：P0 今天跟进；P1 在 3 天内跟进；P2 在上下文或 OKR 暗示需要时本周内跟进。scheduled_at 和 next_follow_up_at 必须落在工作日 09:00-18:00；夜间或周末不要安排发送。

输出要求：
- 只输出 TaskAgentDecision JSON。
- action 只能是 discard、create_project 或 update_project。
- failure_risk 和 failure_risk_score 必须始终填写；低风险一次性事项通常 action=discard。
- update_project 必须引用候选或已确认项目 id。
- todo_changes 的 close/cancel/update 必须引用 todo_id。
- follow_up_drafts 的 owner_user_id 不能为空，且必须有 todo_id 或 todo_ref。
- follow_up_drafts 不需要人工审批字段；可发送性由 scheduled_at、target_kind、target_conversation_id 和 owner_user_id 决定。
- follow_up_changes 用于更新已有 follow_up_drafts；必须引用 follow_up_id，且只能在当前 Work Item 明确支持时使用。
- memory_connector 可用时，非 discard 决策的 memory_recall_used 必须为 true，且 project.memory_context 不能为空。
- memory_connector 不可用时，非 discard 决策的 memory_recall_used 必须为 false，且 project.memory_context 仍不能为空。

Memory connector 状态:
{memory_status}

Work Item JSON:
{work_item_json}

候选上下文:
{candidate_prompt}
"""


def build_candidate_context_prompt(
    *,
    project_candidates: str,
    follow_up_candidates: str,
) -> str:
    return (
        "候选项目:\n"
        f"{project_candidates}\n\n"
        "近期 follow-up 候选:\n"
        f"{follow_up_candidates}"
    )


def render_follow_up_candidate_prompt(
    candidates: list[RecentFollowUpCandidate],
) -> str:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "id": candidate.follow_up_id,
                "follow_up_id": candidate.follow_up_id,
                "project_id": candidate.project_id,
                "project_title": candidate.project_title,
                "project_status": candidate.project_status,
                "project_priority": candidate.project_priority,
                "project_risk_level": candidate.project_risk_level,
                "todo_id": candidate.todo_id,
                "todo_title": candidate.todo_title,
                "todo_status": candidate.todo_status,
                "todo_priority": candidate.todo_priority,
                "todo_deadline_at": candidate.todo_deadline_at,
                "todo_next_follow_up_at": candidate.todo_next_follow_up_at,
                "owner_user_id": candidate.owner_user_id,
                "owner_name": candidate.owner_name,
                "target_conversation_id": candidate.target_conversation_id,
                "target_kind": candidate.target_kind,
                "question_text": candidate.question_text,
                "scheduled_at": candidate.scheduled_at,
                "sent_at": candidate.sent_at,
                "status": candidate.status,
                "reaction_status": candidate.reaction_status,
                "reaction_summary": candidate.reaction_summary,
                "suppressed_reason": candidate.suppressed_reason,
                "evidence_check_json": candidate.evidence_check_json,
                "risk_check_json": candidate.risk_check_json,
                "send_result_json": candidate.send_result_json,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _recent_follow_up_context_since(created_at: str) -> str:
    created_at = created_at.strip()
    if not created_at:
        return ""
    for parser in (
        lambda text: datetime.fromisoformat(text.replace("Z", "+00:00")),
        lambda text: datetime.strptime(text, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            parsed = parser(created_at)
            return (parsed - RECENT_FOLLOW_UP_CONTEXT_WINDOW).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            continue
    return ""


def _memory_connector_prompt_status(memory_issue: str) -> str:
    issue = memory_issue.strip()
    if issue:
        return (
            f"不可用：{issue}\n"
            "- 继续处理 Work Item；不要因为 memory_recall 不可用而失败。\n"
            "- 不能调用 memory_recall 时，在 project.memory_context 记录查询意图、不可用原因和替代证据。"
        )
    return "可用：需要用 memory_recall 补足非 discard 决策的历史背景。"


def process_work_item(
    store: AutoReplyStore,
    runner: TaskAgentRunner,
    work_input: WorkSummaryInput,
    *,
    dws=None,
    now: str = "",
) -> None:
    try:
        memory_issue = memory_connector_config_issue()
        work_item = WorkItem.model_validate_json(work_input.payload_json)
        candidates = retrieve_project_candidates(
            store,
            summary=work_item.summary,
            project_name=work_item.project_name,
        )
        follow_up_candidates = store.list_recent_follow_up_candidates(
            conversation_id=work_item.source.conversation_id,
            owner_user_id=work_item.context.sender_user_id,
            since=_recent_follow_up_context_since(work_item.source.created_at),
            limit=10,
        )
        candidate_prompt = build_candidate_context_prompt(
            project_candidates=render_candidate_prompt(candidates),
            follow_up_candidates=render_follow_up_candidate_prompt(
                follow_up_candidates
            ),
        )
        decision = runner.decide(
            work_item,
            candidate_prompt,
            memory_issue=memory_issue,
        )
        codex_session_id = getattr(runner.codex, "last_session_id", None) or ""
        store.record_task_agent_run(
            summary_input_id=work_input.id,
            codex_session_id=codex_session_id,
            decision_json=_json_dumps(decision.model_dump(mode="json")),
            audit_summary=decision.update_summary,
            memory_recall_used=decision.memory_recall_used,
        )
        audit_tool_events = getattr(runner.codex, "last_audit_tool_events", None)
        memory_recall_attempted = _audit_events_include_memory_recall(
            audit_tool_events
        )
        memory_runtime_unavailable = (
            _audit_events_include_memory_tool_discovery(audit_tool_events)
            and _decision_reports_memory_runtime_unavailable(decision)
        )
        _validate_memory_recall_tool_event(
            decision,
            audit_tool_events,
            memory_issue=memory_issue,
            memory_runtime_unavailable=memory_runtime_unavailable,
        )
        apply_task_agent_decision(
            store,
            summary_input_id=work_input.id,
            work_item=work_item,
            decision=decision,
            codex_session_id=codex_session_id,
            memory_issue=memory_issue,
            memory_recall_attempted=memory_recall_attempted,
            memory_runtime_unavailable=memory_runtime_unavailable,
            record_run=False,
            dws=dws,
            now=now,
        )
        if decision.action == "discard":
            store.mark_work_summary_input_discarded(
                work_input.id,
                decision.discard_reason or decision.update_summary,
            )
        else:
            store.mark_work_summary_input_done(work_input.id)
    except Exception as exc:
        store.mark_work_summary_input_failed(work_input.id, str(exc))
        raise


def apply_task_agent_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    codex_session_id: str = "",
    memory_issue: str = "",
    memory_recall_attempted: bool = False,
    memory_runtime_unavailable: bool = False,
    record_run: bool = True,
    dws=None,
    now: str = "",
) -> int | None:
    if record_run:
        store.record_task_agent_run(
            summary_input_id=summary_input_id,
            codex_session_id=codex_session_id,
            decision_json=_json_dumps(decision.model_dump(mode="json")),
            audit_summary=decision.update_summary,
            memory_recall_used=decision.memory_recall_used,
        )
    _validate_task_agent_decision(
        decision,
        memory_issue=memory_issue,
        memory_recall_attempted=memory_recall_attempted,
        memory_runtime_unavailable=memory_runtime_unavailable,
    )

    if decision.action == "discard":
        return None

    _validate_follow_up_change_targets(store, decision.follow_up_changes)

    if decision.project is None:
        raise ValueError(f"{decision.action} requires project")

    project_id = _apply_project(store, decision)
    update_id = store.create_work_update(
        project_id=project_id,
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        summary=decision.update_summary,
        changes_json=_json_dumps(
            {
                "action": decision.action,
                "todo_changes": [
                    _todo_change_audit_payload(change)
                    for change in decision.todo_changes
                ],
                "follow_up_drafts": [
                    draft.model_dump(mode="json")
                    for draft in decision.follow_up_drafts
                ],
                "follow_up_changes": [
                    change.model_dump(mode="json")
                    for change in decision.follow_up_changes
                ],
            }
        ),
        merge_reason=decision.merge_reason,
        confidence=decision.confidence,
    )
    todo_refs: dict[str, int] = {}
    create_sync_todo_ids: list[int] = []
    sync_now = ""
    for todo_change in decision.todo_changes:
        todo_id = _apply_todo_change(
            store,
            project_id=project_id,
            update_id=update_id,
            change=todo_change,
        )
        if (
            dws is not None
            and todo_change.action == "close"
            and bool(todo_change.completion_evidence)
        ):
            sync_now = sync_now or now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sync_completed_todo_to_dingtalk(
                store,
                dws,
                work_todo_id=todo_id,
                evidence=todo_change.completion_evidence,
                now=sync_now,
            )
        if todo_change.action in {"create", "update"}:
            create_sync_todo_ids.append(todo_id)
        if todo_change.action == "create" and todo_change.todo_ref.strip():
            todo_refs[todo_change.todo_ref.strip()] = todo_id
    for draft in decision.follow_up_drafts:
        if _suppress_unreliable_minutes_direct_follow_up(work_item, draft):
            continue
        _create_follow_up_draft(
            store,
            project_id=project_id,
            draft=draft,
            todo_refs=todo_refs,
        )
    for change in decision.follow_up_changes:
        _apply_follow_up_change(store, change)
    if dws is not None:
        sync_now = sync_now or now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for todo_id in create_sync_todo_ids:
            maybe_create_dingtalk_todo(
                store,
                dws,
                work_todo_id=todo_id,
                now=sync_now,
            )
    return project_id


def _suppress_unreliable_minutes_direct_follow_up(
    work_item: WorkItem,
    draft: FollowUpDraftDecision,
) -> bool:
    if draft.target_kind != "direct":
        return False
    if work_item.source.type not in {
        WorkItemSourceType.AI_MINUTES,
        WorkItemSourceType.LOCAL_FILE,
    }:
        return False
    if work_item.source.conversation_id.strip():
        return False
    return _has_unreliable_minutes_speaker_labels(work_item)


def _has_unreliable_minutes_speaker_labels(work_item: WorkItem) -> bool:
    summary = work_item.summary
    participants = _minutes_participants(work_item)
    if len(participants) < 2:
        return False
    speakers = _minutes_transcript_speakers(summary)
    return len(speakers) >= 5 and len(set(speakers)) == 1


def _minutes_participants(work_item: WorkItem) -> list[str]:
    participants = [
        str(participant).strip()
        for participant in work_item.context.participants
        if str(participant).strip()
    ]
    if participants:
        return participants
    match = re.search(r"参与人[\s*_]*[:：]\s*([^\n\r]+)", work_item.summary)
    if not match:
        return []
    raw = match.group(1)
    return [
        value.strip()
        for value in re.split(r"[,，、]", raw)
        if value.strip()
    ]


def _minutes_transcript_speakers(summary: str) -> list[str]:
    speakers: list[str] = []
    patterns = (
        re.compile(r"^\[[^\]]+\]\s*([^:：\n]+)\s*[:：]", re.MULTILINE),
        re.compile(r"^-\s*([^:：\n]+)\s*[:：]", re.MULTILINE),
    )
    for pattern in patterns:
        for match in pattern.finditer(summary):
            speaker = match.group(1).strip()
            if speaker:
                speakers.append(speaker)
    return speakers


def _validate_task_agent_decision(
    decision: TaskAgentDecision,
    *,
    memory_issue: str = "",
    memory_recall_attempted: bool = False,
    memory_runtime_unavailable: bool = False,
) -> None:
    for todo_change in decision.todo_changes:
        if todo_change.action != "create" and todo_change.todo_id is None:
            raise ValueError(f"{todo_change.action} requires todo_id")
    for draft in decision.follow_up_drafts:
        if not draft.owner_user_id.strip():
            raise ValueError("follow_up_draft.owner_user_id is required")
        if draft.todo_id is None and not draft.todo_ref.strip():
            raise ValueError("follow_up_draft requires todo_id or todo_ref")
    for change in decision.follow_up_changes:
        if change.follow_up_id <= 0:
            raise ValueError("follow_up_change.follow_up_id is required")
        if change.action == "reschedule" and not (
            change.next_due_at and change.next_due_at.strip()
        ):
            raise ValueError(
                "follow_up_change.next_due_at is required for reschedule"
            )
        if change.action == "reassign" and not (
            (change.owner_user_id and change.owner_user_id.strip())
            or (change.owner_name and change.owner_name.strip())
        ):
            raise ValueError(
                "follow_up_change.owner_user_id or owner_name is required for reassign"
            )
    if decision.action == "discard":
        return
    if (
        not memory_issue.strip()
        and not memory_recall_attempted
        and not memory_runtime_unavailable
        and not decision.memory_recall_used
    ):
        raise ValueError("non-discard task decision requires memory_recall_used")
    if decision.project is None:
        raise ValueError(f"{decision.action} requires project")
    memory_context = decision.project.memory_context
    if not memory_context.query.strip() or (
        not memory_context.summary.strip() and not memory_context.memories
    ):
        raise ValueError("non-discard task decision requires project.memory_context")
    if decision.action == "update_project" and decision.project.id is None:
        raise ValueError("update_project requires project.id")


def _validate_memory_recall_tool_event(
    decision: TaskAgentDecision,
    audit_tool_events: object,
    *,
    memory_issue: str = "",
    memory_runtime_unavailable: bool = False,
) -> None:
    if decision.action == "discard" or audit_tool_events is None:
        return
    if not isinstance(audit_tool_events, list):
        return
    if _audit_events_include_memory_recall(audit_tool_events):
        return
    if memory_runtime_unavailable:
        return
    if memory_issue.strip():
        return
    raise ValueError("non-discard task decision requires memory_recall tool event")


def _audit_events_include_memory_recall(audit_tool_events: object) -> bool:
    if not isinstance(audit_tool_events, list):
        return False
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        tool = str(event.get("tool") or "")
        if "memory_recall" in tool:
            return True
    return False


def _audit_events_include_memory_tool_discovery(audit_tool_events: object) -> bool:
    if not isinstance(audit_tool_events, list):
        return False
    discovery_tools = {
        "tool_search_call",
        "list_mcp_resources",
        "list_mcp_resource_templates",
    }
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        tool = str(event.get("tool") or "")
        if tool in discovery_tools:
            return True
    return False


def _decision_reports_memory_runtime_unavailable(
    decision: TaskAgentDecision,
) -> bool:
    if decision.project is None:
        return False
    return any(
        item.source == "memory_connector_runtime_unavailable"
        for item in decision.project.memory_context.memories
    )


def _apply_project(store: AutoReplyStore, decision: TaskAgentDecision) -> int:
    project = decision.project
    if project is None:
        raise ValueError(f"{decision.action} requires project")
    if decision.action == "create_project":
        return store.create_work_project(**_project_values(project))
    if project.id is None:
        raise ValueError("update_project requires project.id")
    values = _project_values(project, only_fields=project.model_fields_set - {"id"})
    store.update_work_project(project.id, **values)
    return project.id


def _project_values(project, only_fields: set[str] | None = None) -> dict[str, object]:
    fields = {
        "title": "title",
        "category": "category",
        "tags": "tags_json",
        "status": "status",
        "priority": "priority",
        "risk_level": "risk_level",
        "needs_derek_attention": "needs_derek_attention",
        "owner_user_id": "owner_user_id",
        "owner_name": "owner_name",
        "related_people": "related_people_json",
        "goal": "goal",
        "background": "background",
        "memory_context": "memory_context_json",
        "facts": "facts_json",
        "current_state": "current_state",
        "blocker": "blocker",
        "next_step": "next_step",
        "next_follow_up_at": "next_follow_up_at",
        "follow_up_mode": "follow_up_mode",
        "source_conversations": "source_conversations_json",
    }
    values: dict[str, object] = {}
    for model_field, store_field in fields.items():
        if only_fields is not None and model_field not in only_fields:
            continue
        value = getattr(project, model_field)
        if model_field in {
            "tags",
            "related_people",
            "memory_context",
            "facts",
            "source_conversations",
        }:
            values[store_field] = _json_dumps(_jsonable(value))
        elif model_field == "needs_derek_attention":
            values[store_field] = int(bool(value))
        else:
            values[store_field] = _enum_value(value)
    return values


def _apply_todo_change(
    store: AutoReplyStore,
    *,
    project_id: int,
    update_id: int,
    change: TodoChange,
) -> int:
    if change.action == "create":
        values = _todo_values(change)
        return store.create_work_todo(
            project_id=project_id,
            created_from_update_id=update_id,
            **values,
        )
    if change.todo_id is None:
        raise ValueError(f"{change.action} requires todo_id")
    values = _todo_values(
        change,
        only_fields=change.model_fields_set - {"action", "todo_id"},
    )
    if change.action == "close":
        values["status"] = "done"
    elif change.action == "cancel":
        values["status"] = "cancelled"
    store.update_work_todo(change.todo_id, **values)
    return change.todo_id


def _todo_values(
    change: TodoChange,
    only_fields: set[str] | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {}
    fields = [
        "title",
        "owner_user_id",
        "owner_name",
        "status",
        "priority",
        "deadline_at",
        "next_follow_up_at",
        "follow_up_question",
        "blocker",
    ]
    for field in fields:
        if only_fields is not None and field not in only_fields:
            continue
        value = getattr(change, field)
        if value not in ("", None):
            if field == "next_follow_up_at":
                value = _normalize_follow_up_time(str(value))
            values[field] = _enum_value(value)
    if (
        only_fields is None or "completion_evidence" in only_fields
    ) and change.completion_evidence is not None:
        values["completion_evidence_json"] = _json_dumps(change.completion_evidence)
    return values


def _todo_change_audit_payload(change: TodoChange) -> dict[str, object]:
    payload: dict[str, object] = {"action": change.action}
    if change.todo_id is not None:
        payload["todo_id"] = change.todo_id
    if change.todo_ref:
        payload["todo_ref"] = change.todo_ref
    if change.action == "create":
        payload.update(_todo_values(change))
        return payload

    for field, value in _todo_values(
        change,
        only_fields=change.model_fields_set - {"action", "todo_id"},
    ).items():
        payload[field] = value
    if change.action == "close":
        payload["status"] = "done"
    elif change.action == "cancel":
        payload["status"] = "cancelled"
    return payload


def _create_follow_up_draft(
    store: AutoReplyStore,
    *,
    project_id: int,
    draft: FollowUpDraftDecision,
    todo_refs: dict[str, int],
) -> int:
    todo_id = _resolve_follow_up_todo_id(
        store,
        project_id=project_id,
        draft=draft,
        todo_refs=todo_refs,
    )
    todo = store.get_work_todo(todo_id)
    if todo is not None and (
        todo.status in {TodoStatus.DONE, TodoStatus.CANCELLED}
        or _has_json_content(todo.completion_evidence_json)
    ):
        return 0
    return store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id=draft.owner_user_id,
        owner_name=draft.owner_name,
        target_conversation_id=draft.target_conversation_id,
        target_kind=draft.target_kind,
        question_text=draft.question_text,
        risk_check_json=_json_dumps(draft.risk_check),
        status=_enum_value(draft.status),
        scheduled_at=_normalize_follow_up_time(draft.scheduled_at),
    )


def _apply_follow_up_change(
    store: AutoReplyStore,
    change: FollowUpDraftChange,
) -> None:
    current = store.get_follow_up_draft(change.follow_up_id)
    if current is None:
        raise ValueError(
            f"follow_up_change.follow_up_id not found: {change.follow_up_id}"
        )
    evidence = {
        "source": "task_agent",
        "action": change.action,
        "reason": change.reason,
        "evidence": change.evidence_check,
    }
    values: dict[str, object] = {
        "evidence_check_json": _json_dumps(evidence),
    }
    if change.todo_id is not None:
        values["todo_id"] = change.todo_id

    if change.action == "suppress":
        values["status"] = "skipped"
        values["suppressed_reason"] = change.reason or "task_agent_suppressed"
    elif change.action == "close":
        if _enum_value(current.status) in {"draft", "approved"}:
            values["status"] = "skipped"
            values["suppressed_reason"] = change.reason or "task_agent_closed"
        values["reaction_status"] = "completed"
        values["reaction_summary"] = change.reason
    elif change.action == "reschedule":
        values["status"] = "draft"
        if change.next_due_at and change.next_due_at.strip():
            values["scheduled_at"] = change.next_due_at.strip()
    elif change.action == "reassign":
        if change.owner_user_id is not None:
            values["owner_user_id"] = change.owner_user_id.strip()
        if change.owner_name is not None:
            values["owner_name"] = change.owner_name.strip()
        values["reaction_status"] = "redirect_owner"
        values["reaction_summary"] = change.reason
    elif change.action == "keep_open":
        values["reaction_summary"] = change.reason

    store.update_follow_up_draft(change.follow_up_id, **values)


def _validate_follow_up_change_targets(
    store: AutoReplyStore,
    changes: list[FollowUpDraftChange],
) -> None:
    for change in changes:
        if store.get_follow_up_draft(change.follow_up_id) is None:
            raise ValueError(
                f"follow_up_change.follow_up_id not found: {change.follow_up_id}"
            )


def _resolve_follow_up_todo_id(
    store: AutoReplyStore,
    *,
    project_id: int,
    draft: FollowUpDraftDecision,
    todo_refs: dict[str, int],
) -> int:
    todo_id = draft.todo_id
    if todo_id is None and draft.todo_ref.strip():
        todo_id = todo_refs.get(draft.todo_ref.strip())
        if todo_id is None:
            raise ValueError(f"unknown follow_up_draft.todo_ref: {draft.todo_ref}")
    if todo_id is None or todo_id <= 0:
        raise ValueError("follow_up_draft requires todo_id or todo_ref")
    todo = store.get_work_todo(todo_id)
    if todo is None:
        raise ValueError(f"follow_up_draft.todo_id not found: {todo_id}")
    if todo.project_id != project_id:
        raise ValueError(
            f"follow_up_draft.todo_id {todo_id} does not belong to project {project_id}"
        )
    return todo_id


def _has_json_content(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return True
    return bool(parsed)


def _normalize_follow_up_time(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        scheduled = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return value

    adjusted = scheduled
    if adjusted.weekday() >= 5:
        days_until_monday = 7 - adjusted.weekday()
        adjusted = adjusted + timedelta(days=days_until_monday)
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif adjusted.hour < FOLLOW_UP_WORK_START_HOUR:
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif adjusted.hour >= FOLLOW_UP_WORK_END_HOUR:
        adjusted = adjusted + timedelta(days=1)
        while adjusted.weekday() >= 5:
            adjusted = adjusted + timedelta(days=1)
        adjusted = adjusted.replace(
            hour=FOLLOW_UP_WORK_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )

    if adjusted == scheduled:
        return value
    return adjusted.isoformat(timespec="seconds")


def _json_dumps(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return _enum_value(value)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _task_decision_text_candidates(payload: object) -> list[str]:
    candidates: list[str] = []
    if not isinstance(payload, dict):
        return candidates
    for key in ("message", "last_agent_message", "content", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    candidates.append(item["text"])
    item = payload.get("item")
    if isinstance(item, dict):
        candidates.extend(_task_decision_text_candidates(item))
    nested = payload.get("payload")
    if isinstance(nested, dict):
        candidates.extend(_task_decision_text_candidates(nested))
    return candidates


def _parse_task_agent_decision(raw: str) -> TaskAgentDecision:
    stripped = raw.strip()
    try:
        return TaskAgentDecision.model_validate_json(stripped)
    except (ValueError, ValidationError):
        pass

    payloads: list[object] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    for payload in reversed(payloads):
        try:
            return TaskAgentDecision.model_validate(payload)
        except (ValueError, ValidationError):
            pass
        for text in _task_decision_text_candidates(payload):
            try:
                return TaskAgentDecision.model_validate_json(text)
            except (ValueError, ValidationError):
                continue
    raise ValueError("No TaskAgentDecision JSON found")
