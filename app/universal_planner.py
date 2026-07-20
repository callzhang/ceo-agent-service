import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.codex_decision import _subprocess_failure_reason, extract_codex_session_id
from app.codex_runner import (
    CodexRunner,
    _config_string,
    codex_model_config_options,
    memory_connector_config_options,
    passthrough_mcp_server_config_options,
)
from app.process_runner import run_process_with_idle_timeout
from app.universal_context import UniversalTaskContext
from app.universal_plan import UniversalPlan

UNIVERSAL_PLANNER_RAW_OUTPUT_LIMIT = 12_000

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
                "available tools and CLI when it is needed to make the plan. You must "
                "not run mutating MCP or CLI operations; service executors own all "
                "writes.",
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
        self.last_session_id = supplied_session_id
        raw = self._execute(self._build_command(supplied_session_id), prompt)
        current_session_id = self.last_session_id
        try:
            return parse_universal_plan_json(raw)
        except (ValueError, json.JSONDecodeError):
            if not current_session_id:
                raise

        repair_prompt = _repair_prompt(raw)
        repair_raw = self._execute(self._build_command(current_session_id), repair_prompt)
        return parse_universal_plan_json(repair_raw)

    def _execute(self, command: list[str], prompt: str) -> str:
        env = self.runner.build_env()
        if self.executor is not None:
            raw = self.executor(command, prompt, env)
            self._remember_output(raw)
            return raw

        completed = self._run_process_with_idle_timeout(
            command,
            prompt=prompt,
            env=env,
            total_timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds,
        )
        raw = completed.stdout
        self._remember_output(raw)
        if completed.timed_out:
            raise RuntimeError(
                completed.timeout_reason or "universal planner codex timed out"
            )
        if completed.returncode != 0:
            raise RuntimeError(_subprocess_failure_reason(completed.stderr, raw))
        return raw

    def _remember_output(self, raw: str) -> None:
        self.last_raw_output = _bounded_stdout(raw)
        session_id = _usable_session_id(extract_codex_session_id(raw))
        if session_id:
            self.last_session_id = session_id

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
            'sandbox_mode="read-only"',
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
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
                session_id,
                "-",
            ]
        return [
            self.runner.codex_bin,
            "exec",
            *common_options,
            "--cd",
            str(self.runner.workspace),
            "-",
        ]


def parse_universal_plan_json(raw: str) -> UniversalPlan:
    payloads = _json_payloads(raw)
    for payload in reversed(payloads):
        candidate = _authoritative_plan_candidate(payload)
        if candidate is not None:
            return UniversalPlan.model_validate(candidate)
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


def _authoritative_plan_candidate(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if _looks_like_universal_plan(payload):
        return payload

    item = payload.get("item")
    if isinstance(item, dict) and "text" in item:
        return _model_output_object(item["text"], "item.text")
    if "message" in payload:
        return _model_output_object(payload["message"], "message")
    return None


def _model_output_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"Codex {field_name} must contain a JSON object")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Codex {field_name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Codex {field_name} must contain a JSON object")
    return payload


def _looks_like_universal_plan(payload: dict[str, Any]) -> bool:
    return bool({"planner_version", "task_kind", "actions", "audit"} & payload.keys())


def _usable_session_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _bounded_stdout(stdout: str) -> str:
    if len(stdout) <= UNIVERSAL_PLANNER_RAW_OUTPUT_LIMIT:
        return stdout
    return stdout[:UNIVERSAL_PLANNER_RAW_OUTPUT_LIMIT] + "\n...[truncated]"


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
