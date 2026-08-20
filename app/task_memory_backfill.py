import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.task_models import ProjectMemoryContext, WorkProject, WorkTodo, WorkUpdate

PROJECT_MEMORY_CONTEXT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "project_memory_context.schema.json"
)


class ProjectMemoryContextCodexRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor=None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
        store=None,
        routed_execution=None,
    ):
        from app.agent_runtime_config import load_runtime_config
        from app.agent_runtime_router import (
            AgentRuntimeRouter,
            RoutedCodexExecution,
            local_codex_session_effect_probe,
        )
        from app.codex_decision import (
            extract_codex_audit_events,
        )
        from app.codex_runtime_adapter import CodexRuntimeAdapter

        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self._extract_codex_audit_events = extract_codex_audit_events
        if routed_execution is None:
            if store is None:
                raise ValueError("store is required for routed project memory backfill")
            runtime_config = load_runtime_config(os.environ)
            adapter = CodexRuntimeAdapter(
                workspace, runtime_config, codex_bin=codex_bin
            )
            routed_kwargs = {
                "store": store,
                "config": runtime_config,
                "router": AgentRuntimeRouter(
                    routes=runtime_config.routes,
                    store=store,
                    snapshots={},
                ),
                "adapter": adapter,
                "session_effect_probe": local_codex_session_effect_probe(),
                "total_timeout_seconds": timeout_seconds,
                "idle_timeout_seconds": idle_timeout_seconds,
            }
            if executor is not None:
                routed_kwargs["executor"] = executor
            routed_execution = RoutedCodexExecution(**routed_kwargs)
        self.routed_execution = routed_execution
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] | None = None

    def build(
        self,
        *,
        project: WorkProject,
        todos: list[WorkTodo],
        updates: list[WorkUpdate],
    ) -> ProjectMemoryContext:
        prompt = build_project_memory_context_prompt(
            project=project,
            todos=todos,
            updates=updates,
        )
        from app.agent_runtime_router import (
            ApprovedCodexCommandFactory,
            RoutedResultCodec,
        )

        def parse_and_validate(raw: str) -> str:
            context = parse_project_memory_context(raw)
            audit_events = self._extract_codex_audit_events(raw)
            validate_project_memory_context(context, audit_events)
            self.last_audit_tool_events = audit_events
            return context.model_dump_json()

        result = self.routed_execution.execute(
            workload_kind="task",
            workload_key=f"{project.id}:memory_backfill",
            prompt=prompt,
            command_factory=ApprovedCodexCommandFactory.read_only(
                developer_instructions=(
                    "Only read reviewed Memory Connector evidence and return the "
                    "requested structured project-memory context. Do not write data."
                ),
                output_schema_path=PROJECT_MEMORY_CONTEXT_SCHEMA_PATH,
                use_output_schema=True,
            ),
            parser=parse_and_validate,
            result_codec=RoutedResultCodec.text(
                schema_id="project_memory_context.v1"
            ),
            conversation_id=None,
            required_capabilities=frozenset(
                {"structured_output", "memory_connector_read"}
            ),
        )
        self.last_session_id = result.session_id or None
        return ProjectMemoryContext.model_validate_json(result.value)

def build_project_memory_context_prompt(
    *,
    project: WorkProject,
    todos: list[WorkTodo],
    updates: list[WorkUpdate],
) -> str:
    payload = {
        "project": _project_payload(project),
        "todos": [todo.model_dump(mode="json") for todo in todos],
        "updates": [update.model_dump(mode="json") for update in updates],
    }
    project_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""你是 CEO Agent 的 task memory backfill agent。

职责：
- 只为已有 work project 补充 project.memory_context。
- 必须调用 memory_recall 查历史背景；不要传入或编造 user_id。
- query 应该结合项目标题、category、goal、background、facts、todo 和最近 updates。
- 如果 memory_recall 没有命中，仍然输出 query，并在 summary 里写明没有找到可用历史背景。
- memories 只放 memory_recall 返回的关键证据；不要把当前项目字段伪装成 memory 证据。
- 只输出 ProjectMemoryContext JSON，不要更新项目、TODO 或发送消息。

Project/TODO/Update JSON:
{project_json}
"""


def parse_project_memory_context(raw: str) -> ProjectMemoryContext:
    stripped = raw.strip()
    try:
        payload = json.loads(stripped)
        if _looks_like_project_memory_context(payload):
            return ProjectMemoryContext.model_validate(payload)
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
        if _looks_like_project_memory_context(payload):
            try:
                return ProjectMemoryContext.model_validate(payload)
            except (ValueError, ValidationError):
                pass
        for text in _memory_context_text_candidates(payload):
            try:
                return ProjectMemoryContext.model_validate_json(text)
            except (ValueError, ValidationError):
                continue
    raise ValueError("No ProjectMemoryContext JSON found")


def _looks_like_project_memory_context(payload: object) -> bool:
    return isinstance(payload, dict) and bool(
        {"query", "summary", "memories"} & set(payload)
    )


def validate_project_memory_context(
    context: ProjectMemoryContext,
    audit_tool_events: object,
) -> None:
    if not context.query.strip() or (
        not context.summary.strip() and not context.memories
    ):
        raise ValueError(
            "project memory context requires query and summary or memories"
        )
    if audit_tool_events is None:
        return
    if not isinstance(audit_tool_events, list):
        return
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        event_text = json.dumps(event, ensure_ascii=False)
        if (
            "connector_auth_failure" in event_text
            or "reauthentication_required" in event_text
        ):
            raise ValueError("memory_recall authentication failed")
    for event in audit_tool_events:
        if not isinstance(event, dict):
            continue
        tool = str(event.get("tool") or "")
        if "memory_recall" in tool:
            return
    raise ValueError(
        "project memory context backfill requires memory_recall tool event"
    )


def _project_payload(project: WorkProject) -> dict[str, Any]:
    payload = project.model_dump(mode="json")
    for field, default in (
        ("tags_json", []),
        ("related_people_json", []),
        ("facts_json", []),
        ("source_conversations_json", []),
    ):
        payload[field.removesuffix("_json")] = _parse_json_value(
            payload.pop(field, ""),
            default,
        )
    payload.pop("memory_context_json", None)
    return payload


def _parse_json_value(value: str, default: object) -> object:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _memory_context_text_candidates(payload: object) -> list[str]:
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
        candidates.extend(_memory_context_text_candidates(item))
    nested = payload.get("payload")
    if isinstance(nested, dict):
        candidates.extend(_memory_context_text_candidates(nested))
    return candidates
