import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.codex_decision import extract_codex_session_id
from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CodexRunner,
    _config_string,
    codex_model_config_options,
    memory_connector_config_options,
    passthrough_mcp_server_config_options,
)
from app.process_runner import run_process_with_idle_timeout
from app.universal_context import UniversalTaskContext
from app.universal_plan import UniversalPlan


UNIVERSAL_PLAN_SCHEMA_HINT = (
    "UniversalPlan JSON contract: "
    '{"planner_version":"2026-07-20",'
    '"task_kind":"non-empty string",'
    '"reason":"non-empty string",'
    '"dependencies":["dws|lark|exa|memory|xiaoqing_interview|mail|calendar"],'
    '"actions":[{"kind":"send_reply|ask_clarifying_question|oa_approval|mail_reply|calendar_response|dws_markdown_document_reply|dws_message_reaction|memory_write|no_reply|handoff_to_human|blocked|stop_with_error",'
    '"reason":"non-empty string","target":{},"payload":{}}],'
    '"audit":{"summary":"non-empty string","documents":[{"key":"value"}],'
    '"confidence":0.0}}. '
    "No fields beyond this contract are allowed. actions must contain at least "
    "one action. audit.confidence must be in the range 0.0..1.0. Action payload "
    "requirements: "
    "send_reply and ask_clarifying_question require payload.text; mail_reply "
    "requires target.mailbox, target.message_id, and payload.content; oa_approval "
    "requires payload.action of 同意, 拒绝, 退回, or comment and a non-empty "
    "payload.remark."
)


class UniversalPlanner:
    def __init__(
        self,
        *,
        workspace: Path,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str, dict[str, str]], str] | None = None,
        timeout_seconds: int = 1200,
        idle_timeout_seconds: int = 900,
    ):
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self._run_process_with_idle_timeout = run_process_with_idle_timeout
        self.last_raw_output = ""
        self.last_session_id: str | None = None

    def build_prompt(self, context: UniversalTaskContext) -> str:
        return "\n\n".join(
            [
                "You are the Universal Planner. Classify and plan this task, but "
                "must not directly execute externally visible side effects. Produce "
                "actions for the service to execute later; do not send messages, "
                "approve requests, modify calendars, write memory, or change external "
                "systems yourself.",
                "DWS is blocking for DingTalk and must already be service-checked "
                "before this planner is invoked. Do not run dws auth login, dws auth "
                "reset, or dws auth logout. You may gather read-only evidence through "
                "available tools and CLI when it is needed to make the plan.",
                UNIVERSAL_PLAN_SCHEMA_HINT,
                "Return only UniversalPlan JSON. Do not use Markdown fences or add "
                "explanatory text.",
                "Task context:\n" + context.render_for_agent(),
            ]
        )

    def plan(
        self,
        context: UniversalTaskContext,
        session_id: str | None = None,
    ) -> UniversalPlan:
        prompt = self.build_prompt(context)
        supplied_session_id = _usable_session_id(session_id)
        raw = self._execute(self._build_command(supplied_session_id), prompt)
        current_session_id = _usable_session_id(extract_codex_session_id(raw))
        if current_session_id is None:
            current_session_id = supplied_session_id
        self.last_session_id = current_session_id
        try:
            return parse_universal_plan_json(raw)
        except (ValueError, json.JSONDecodeError):
            if not current_session_id:
                raise

        repair_prompt = _repair_prompt(raw)
        repair_raw = self._execute(self._build_command(current_session_id), repair_prompt)
        self.last_session_id = _usable_session_id(
            extract_codex_session_id(repair_raw)
        ) or current_session_id
        return parse_universal_plan_json(repair_raw)

    def _execute(self, command: list[str], prompt: str) -> str:
        env = self.runner.build_env()
        if self.executor is not None:
            raw = self.executor(command, prompt, env)
        else:
            completed = self._run_process_with_idle_timeout(
                command,
                prompt=prompt,
                env=env,
                total_timeout_seconds=self.timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
            )
            if completed.timed_out:
                raise RuntimeError(
                    completed.timeout_reason or "universal planner codex timed out"
                )
            if completed.returncode != 0:
                raise RuntimeError(
                    "universal planner codex exited with status "
                    f"{completed.returncode}"
                )
            raw = completed.stdout
        self.last_raw_output = raw
        return raw

    def _build_command(self, session_id: str | None) -> list[str]:
        common_options = [
            "--json",
            *codex_model_config_options(ignore_user_config=True),
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "hooks",
            "--disable",
            "plugins",
            *memory_connector_config_options(),
            *passthrough_mcp_server_config_options(),
            "-c",
            'approval_policy="never"',
            "-c",
            _config_string(
                "developer_instructions",
                "You are a planning-only agent. Return only the requested JSON.",
            ),
            "-c",
            'model_reasoning_summary="concise"',
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
        ]
        if session_id:
            return [
                self.runner.codex_bin,
                "exec",
                "resume",
                *common_options,
                CODEX_BYPASS_APPROVALS_AND_SANDBOX,
                session_id,
                "-",
            ]
        return [
            self.runner.codex_bin,
            "exec",
            *common_options,
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--cd",
            str(self.runner.workspace),
            "-",
        ]


def parse_universal_plan_json(raw: str) -> UniversalPlan:
    payloads = _json_payloads(raw)
    validation_error: ValidationError | None = None
    for payload in reversed(payloads):
        for candidate in _plan_candidates(payload):
            if not _looks_like_universal_plan(candidate):
                continue
            try:
                return UniversalPlan.model_validate(candidate)
            except ValidationError as exc:
                validation_error = exc
    if validation_error is not None:
        raise validation_error
    raise ValueError("No valid UniversalPlan JSON found in Codex output")


def _json_payloads(raw: str) -> list[Any]:
    stripped = raw.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except json.JSONDecodeError:
        payloads: list[Any] = []
        for line in stripped.splitlines():
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payloads


def _plan_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if _looks_like_universal_plan(payload):
            return [payload]

        candidates: list[dict[str, Any]] = []
        item = payload.get("item")
        if isinstance(item, dict):
            _append_json_object(candidates, item.get("text"))
        _append_json_object(candidates, payload.get("message"))
        return candidates
    return []


def _append_json_object(candidates: list[dict[str, Any]], value: Any) -> None:
    if not isinstance(value, str):
        return
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        candidates.append(payload)


def _looks_like_universal_plan(payload: dict[str, Any]) -> bool:
    return bool({"planner_version", "task_kind", "actions", "audit"} & payload.keys())


def _usable_session_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _repair_prompt(raw: str) -> str:
    excerpt = raw.strip()
    if len(excerpt) > 4000:
        excerpt = excerpt[:4000] + "\n...[truncated]"
    return "\n\n".join(
        [
            "The previous output was not valid UniversalPlan JSON. Return only "
            "corrected UniversalPlan JSON that conforms exactly to this contract. "
            "Do not call tools, do not execute externally visible actions, and must "
            "not re-run business side effects.",
            UNIVERSAL_PLAN_SCHEMA_HINT,
            "Previous output:\n" + excerpt,
        ]
    )
