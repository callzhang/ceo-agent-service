import json
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
        timeout_seconds: int = 420,
        idle_timeout_seconds: int = 180,
    ):
        from app.codex_decision import (
            _subprocess_failure_reason,
            extract_codex_audit_events,
            extract_codex_session_id,
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
        self._subprocess_failure_reason = _subprocess_failure_reason
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []

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
        raw = self._execute(prompt)
        self.last_session_id = self._extract_codex_session_id(raw)
        self.last_audit_tool_events = self._extract_codex_audit_events(raw)
        return parse_project_memory_context(raw)

    def _execute(self, prompt: str) -> str:
        command = self.runner.build_command(
            prompt,
            session_id=None,
            image_paths=None,
            output_schema_path=PROJECT_MEMORY_CONTEXT_SCHEMA_PATH,
            ignore_user_config=True,
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
                completed.timeout_reason or "project memory backfill codex timed out"
            )
        if completed.returncode != 0:
            raise RuntimeError(
                self._subprocess_failure_reason(completed.stderr, completed.stdout)
            )
        return completed.stdout


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
        raise ValueError("project memory context requires query and summary or memories")
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
    raise ValueError("project memory context backfill requires memory_recall tool event")


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
