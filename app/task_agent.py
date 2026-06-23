import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.store import AutoReplyStore
from app.codex_runner import memory_connector_config_issue
from app.task_models import (
    FollowUpDraftDecision,
    TaskAgentDecision,
    TodoChange,
    WorkItem,
    WorkSummaryInput,
)
from app.task_retrieval import render_candidate_prompt, retrieve_project_candidates


TASK_AGENT_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "task_agent_decision.schema.json"
)
TASK_AGENT_AUDIT_EVENT_LIMIT = 200


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
- memory_connector 是外部辅助服务，不能成为 task agent 的运行依赖。
- 如果 memory_connector 状态为可用，create_project 或 update_project 前必须直接调用 memory_recall MCP 工具查历史背景；不要传入或编造 user_id。
- list_mcp_resources、list_mcp_resource_templates、memory_get、timeline_get 或本地搜索都不能替代 memory_recall；只有实际调用 memory_recall 并获得可用记忆结果后，memory_recall_used 才能为 true。
- 如果当前运行时确实没有暴露可直接调用的 memory_recall 工具，先用工具发现结果证明不可用，再继续处理；此时 memory_recall_used=false，并在 project.memory_context.memories 写入一条 source="memory_connector_runtime_unavailable" 的证据说明。
- 如果实际调用了 memory_recall 但该工具超时或传输失败，继续处理；此时 memory_recall_used=false，并在 project.memory_context.memories 写入一条 source="memory_recall_runtime_failure" 的证据说明。
- 如果 memory_connector 状态为不可用，不要因此停止任务、不要输出 critical_info_unavailable、不要把任务转人工；改用 Work Item、候选项目、DWS 或本地上下文判断。此时 memory_recall_used=false，project.memory_context 写明原本会查询什么、memory_connector 不可用的原因，以及你实际采用的替代证据。
- project.memory_context 必须写入本次记忆查询或替代依据：memory_recall 有命中时写查询、摘要和关键记忆证据；没有命中时写查询和无命中结论；memory_connector 不可用时写查询意图、不可用原因和替代证据。
- 如果上下文无法支撑稳定项目名称，不要创建模糊项目；生成 follow_up_draft 询问项目、目标、owner。
- 只有消息、会议纪要或文档明确证明 TODO 完成时，才能自动清理 TODO，并写入 completion_evidence。
- 生成 follow_up_draft 前必须确定 owner_user_id；只有 owner_name 不够。如果上下文缺少 userId，先用 dws 或已有联系人信息补齐；仍无法唯一确定时，不要生成 follow_up_draft。
- 每个 follow_up_draft 必须绑定一个 TODO：跟进已有 TODO 时填写 todo_id；跟进本次新建 TODO 时，todo_changes.create 和 follow_up_drafts 使用相同的 todo_ref，系统会把 todo_ref 转成真实 todo_id。不能生成没有 TODO 绑定的 follow_up_draft。
- 跟进时间指导：P0 今天跟进；P1 在 3 天内跟进；P2 在上下文或 OKR 暗示需要时本周内跟进。

输出要求：
- 只输出 TaskAgentDecision JSON。
- action 只能是 discard、create_project 或 update_project。
- failure_risk 和 failure_risk_score 必须始终填写；低风险一次性事项通常 action=discard。
- update_project 必须引用候选或已确认项目 id。
- todo_changes 的 close/cancel/update 必须引用 todo_id。
- follow_up_drafts 的 owner_user_id 不能为空，且必须有 todo_id 或 todo_ref。
- memory_connector 可用时，非 discard 决策的 memory_recall_used 必须为 true，且 project.memory_context 不能为空。
- memory_connector 不可用时，非 discard 决策的 memory_recall_used 必须为 false，且 project.memory_context 仍不能为空。

Memory connector 状态:
{memory_status}

Work Item JSON:
{work_item_json}

候选项目:
{candidate_prompt}
"""


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
) -> None:
    try:
        memory_issue = memory_connector_config_issue()
        work_item = WorkItem.model_validate_json(work_input.payload_json)
        candidates = retrieve_project_candidates(
            store,
            summary=work_item.summary,
            project_name=work_item.project_name,
        )
        decision = runner.decide(
            work_item,
            render_candidate_prompt(candidates),
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
            }
        ),
        merge_reason=decision.merge_reason,
        confidence=decision.confidence,
    )
    todo_refs: dict[str, int] = {}
    for todo_change in decision.todo_changes:
        todo_id = _apply_todo_change(
            store,
            project_id=project_id,
            update_id=update_id,
            change=todo_change,
        )
        if todo_change.action == "create" and todo_change.todo_ref.strip():
            todo_refs[todo_change.todo_ref.strip()] = todo_id
    for draft in decision.follow_up_drafts:
        _create_follow_up_draft(
            store,
            project_id=project_id,
            draft=draft,
            todo_refs=todo_refs,
        )
    return project_id


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
        scheduled_at=draft.scheduled_at,
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
