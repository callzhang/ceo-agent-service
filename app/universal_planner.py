import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.codex_decision import (
    _subprocess_failure_reason,
    extract_codex_audit_events,
    extract_codex_session_id,
)
from app.codex_runner import (
    CodexRunner,
    _config_string,
    codex_developer_instructions,
    codex_model_config_options,
    memory_connector_config_options,
    passthrough_mcp_server_config_options,
)
from app.process_runner import run_process_with_idle_timeout
from app.task_retrieval import tokenize
from app.universal_context import UniversalTaskContext
from app.universal_plan import UniversalPlan

UNIVERSAL_PLANNER_RAW_OUTPUT_LIMIT = 12_000

UNIVERSAL_PLANNER_DEVELOPER_OVERLAY = (
    "For this Universal Planner invocation, the UniversalPlan output contract "
    "overrides any legacy AgentEnvelope output protocol in the shared CEO rules. "
    "Apply all shared business, permission, evidence, style, privacy, and safety "
    "rules when deciding the plan. Return UniversalPlan JSON only. Plan external "
    "side effects but never execute them yourself."
)

UNIVERSAL_PLANNER_RETRIEVED_EXAMPLES = (
    {
        "name": "recover_downloadable_material",
        "text": (
            "Example: if the task context references a DingTalk document, file, "
            "attachment, or material that is not already expanded, first use the "
            "available read-only DWS or document tool to download or read it. If "
            "the read fails because access is missing, ask the requester for access "
            "or the exact material. Do not return blocked while a trusted material "
            "download path still exists."
        ),
    },
    {
        "name": "windows_persona_request",
        "text": (
            "Example: when asked to compare, confirm, or recommend a Windows "
            "分身、persona, avatar, or multi-account tool, gather the referenced "
            "资料 or material before answering. If the material is available, send "
            "a substantive reply with the concrete conclusion; if the key file is "
            "unreadable, ask for that file or access instead of sending a generic "
            "failure note."
        ),
    },
)


def universal_planner_developer_instructions() -> str:
    shared = codex_developer_instructions()
    shared = shared.split("\n输出协议：", 1)[0].rstrip()
    shared = shared.replace(
        "在输出最终 JSON 前调用 memory_write 记录一条业务 episode",
        "在 UniversalPlan 中追加 memory_write action 记录一条业务 episode",
    )
    shared = shared.replace("调用 memory_write", "规划 memory_write action")
    shared = shared.replace("system_actions", "UniversalPlan actions")
    shared = shared.replace("kind=okr_review", "queue_okr_review action")
    shared = shared.replace("user_response.mode", "planned action kind")
    shared = shared.replace(
        "user_response.text",
        "send_reply/ask_clarifying_question payload.text",
    )
    return f"{shared}\n\n{UNIVERSAL_PLANNER_DEVELOPER_OVERLAY}"


def render_universal_planner_examples(context_text: str, *, limit: int = 2) -> str:
    query_terms = set(tokenize(context_text))
    if not query_terms:
        return ""
    ranked = []
    for example in UNIVERSAL_PLANNER_RETRIEVED_EXAMPLES:
        terms = set(tokenize(example["text"]))
        score = len(query_terms & terms)
        if score > 1:
            ranked.append((score, example["name"], example["text"]))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return "\n".join(text for _, _, text in ranked[:limit])

UNIVERSAL_PLAN_SCHEMA_HINT = (
    "UniversalPlan JSON contract: "
    '{"planner_version":"2026-07-20",'
    '"task_kind":"non-empty string",'
    '"reason":"non-empty string",'
    '"dependencies":["memory"],'
    '"actions":[{"kind":"send_reply|ask_clarifying_question|oa_approval|mail_reply|calendar_response|dws_markdown_document_reply|dws_message_reaction|queue_okr_review|memory_write|no_reply|handoff_to_human|blocked|stop_with_error",'
    '"reason":"non-empty string",'
    '"sensitivity_kind":"general|internal_personnel|external_candidate|null",'
    '"personnel_subject_user_id":"string|null",'
    '"candidate_context_known":false,"candidate_department_ids":[],'
    '"target":{},"payload":{}}],'
    '"audit":{"summary":"non-empty string","documents":[{"key":"value"}],'
    '"confidence":0.0}}. '
    "No fields beyond this contract are allowed. actions must contain at least "
    "one action. audit.confidence must be in the range 0.0..1.0. Action payload "
    "requirements: "
    "send_reply and ask_clarifying_question require payload.text and an explicit "
    "non-null sensitivity_kind; mail_reply requires target.mailbox, "
    "target.message_id, target.subject, and payload.content copied exactly from "
    "the trusted mail target in task context; calendar_response requires "
    "target.event_id copied exactly from the trusted calendar target and "
    "payload.response_status of accepted, tentative, or declined; oa_approval "
    "requires payload.action of 同意, 拒绝, 退回, or comment and a non-empty "
    "payload.remark. For oa_approval, copy target.process_instance_id and "
    "target.task_id exactly from the trusted OA IDs in task context. If either "
    "trusted ID is missing and the requester can supply the approval link or "
    "materials, emit ask_clarifying_question with the specific missing item; "
    "emit blocked only when no reliable requester or recovery path exists. "
    "If a DingTalk document, ordinary file body, or required review material "
    "cannot be read, but the requester is known and can grant access or provide "
    "the readable content, emit ask_clarifying_question with the exact missing "
    "access or material; use stop_with_error only when the missing evidence is "
    "unrecoverable or asking the requester would be impossible or unsafe. "
    "Use oa_approval action=退回 only for a real OA revert when the target "
    "return activity is known; it requires payload.target_activity_id and "
    "payload.revert_action of REVERT_FOR_APPROVAL or REVERT_FOR_RESUBMIT. "
    "When the review conclusion is only '补充材料/建议退回/暂不通过' and no "
    "verified revert target is available, use oa_approval action=comment with "
    "the complete return-or-supplement remark instead; memory_write requires payload "
    "with exactly data and type; type must be text or message. Never provide "
    "created_at, source_description, user_id, graph_id, secrets, raw logs, or "
    "temporary runtime errors; "
    "dws_markdown_document_reply requires target.conversation_id and "
    "target.trigger_message_id copied exactly from task context, optional "
    "target.document_url copied exactly from Trusted document URL, and "
    "payload.text; dws_message_reaction requires target.conversation_id and "
    "target.message_id copied exactly from the immutable trigger plus either "
    "payload.reaction_type=emoji with payload.emoji or "
    "payload.reaction_type=text_emotion with payload.text."
    " Choose exactly one terminal conversation-control action per plan: "
    "send_reply, ask_clarifying_question, handoff_to_human, blocked, "
    "stop_with_error, no_reply, or queue_okr_review. Do not combine a chat reply "
    "with handoff_to_human; if a human must take over, use only "
    "handoff_to_human and put the handoff note in payload.text. "
    "Non-terminal follow-up actions such as memory_write may follow the single "
    "terminal action when needed. If an oa_approval action fully responds to "
    "the trigger and no chat text should be sent, pair it with no_reply only; "
    "do not pair no_reply with send_reply or ask_clarifying_question."
    " queue_okr_review requires target.conversation_id and "
    "target.trigger_message_id copied exactly from task context and an empty "
    "payload. Use it only when the sender explicitly asks Derek to review, "
    "evaluate, verify, or score that sender's own OKR/KR progress; do not combine "
    "it with reply, document, reaction, or memory actions because the executor "
    "sends the acknowledgement and queues the dedicated OKR workflow."
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
        self.last_audit_tool_events: list[dict[str, Any]] = []

    def build_prompt(self, context: UniversalTaskContext) -> str:
        context_text = context.render_for_agent()
        retrieved_examples = render_universal_planner_examples(context_text)
        example_prompt = (
            "Retrieved planning examples. Use the decision pattern only; do not "
            f"copy field values:\n{retrieved_examples}"
            if retrieved_examples
            else ""
        )
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
                "When task context includes Material references, treat each supplied "
                "read_command as the trusted read-only path for that material. If "
                "your decision depends on the material body, use that read_command "
                "or an equivalent read-only CLI/tool before planning blocked, "
                "stop_with_error, or an ask for access. Do not say a material link "
                "is inaccessible until the supplied read path has been tried or the "
                "tool returns a concrete permission, login, or not-found error. For "
                "DingTalk folder or document references, prefer dws doc info/list "
                "style read-only inspection over service-side material expansion.",
                "For Feishu/Lark material, use the available read-only lark CLI. "
                "For external web retrieval, use the configured Exa MCP. These are "
                "planning-time evidence tools: when a read succeeds, do not declare "
                "lark or exa as an execution dependency. If required evidence cannot "
                "be read, return a blocked or stop_with_error action with the concrete "
                "reason instead of guessing or attempting login.",
                "dependencies is only for service-gated execution dependencies. The "
                "only supported value is memory, and it is required for memory_write. "
                "Do not list dws, lark, exa, xiaoqing_interview, mail, or calendar; "
                "those are prechecked, planning-time, or executor-owned capabilities.",
                "The service owns Memory OAuth and memory_write execution. Declare "
                "the memory dependency only when the plan includes memory_write; do "
                "not start login or open a browser.",
                "For internal_personnel reply actions, personnel_subject_user_id "
                "must be copied only from a Recent messages sender_user_id or a "
                "trusted organization user_id explicitly shown in context. Never use "
                "calendar participant uid values, open_dingtalk_id values, display "
                "names, or other raw payload ids as personnel_subject_user_id. If the "
                "subject is the sender but no trusted user_id is available, leave "
                "personnel_subject_user_id null and avoid concrete personnel claims.",
                "Trusted task details in task context are read-only internal project "
                "and TODO records from the service. When the sender asks about a "
                "task, TODO, follow-up, owner, deadline, progress, status, blocker, "
                "or task detail link, use this section as primary evidence. If "
                "multiple task details are shown, choose the one best supported by "
                "the conversation title, sender identity, message text, and match "
                "reasons; ask a clarifying question only when the task still cannot "
                "be identified reliably.",
                "If Trusted task details are missing or too shallow for a task-related "
                "request, you may query the local read-only Task Management API before "
                "planning: GET http://127.0.0.1:8765/api/task-management/search?q=<text>"
                "&conversation_id=<conversation_id>&owner_user_id=<sender_user_id>&limit=3, "
                "or GET http://127.0.0.1:8765/api/task-management/projects/<project_id>. "
                "Use the API only for evidence gathering; do not mutate tasks from the "
                "planner.",
                example_prompt,
                UNIVERSAL_PLAN_SCHEMA_HINT,
                "Return only UniversalPlan JSON. Do not use Markdown fences or add "
                "explanatory text.",
                "Task context:\n" + context_text,
            ]
        )

    def plan(
        self,
        context: UniversalTaskContext,
        session_id: str | None = None,
    ) -> UniversalPlan:
        prompt = self.build_prompt(context)
        self.last_audit_tool_events = []
        supplied_session_id = _usable_session_id(session_id)
        self.last_session_id = supplied_session_id
        raw = self._execute(
            self._build_command(supplied_session_id, context.image_paths),
            prompt,
        )
        current_session_id = self.last_session_id
        try:
            return self._finalize_plan(parse_universal_plan_json(raw))
        except (ValueError, json.JSONDecodeError):
            if not current_session_id:
                raise

        repair_prompt = _repair_prompt(raw)
        repair_raw = self._execute(
            self._build_command(current_session_id, context.image_paths),
            repair_prompt,
        )
        return self._finalize_plan(parse_universal_plan_json(repair_raw))

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

        self.last_audit_tool_events.extend(extract_codex_audit_events(raw))

    def _finalize_plan(self, plan: UniversalPlan) -> UniversalPlan:
        tool_events = [
            {
                key: str(event[key])
                for key in ("event_type", "tool", "call_id")
                if event.get(key)
            }
            for event in self.last_audit_tool_events
        ]
        tool_events = [event for event in tool_events if event]
        audit = plan.audit.model_copy(update={"tool_events": tool_events})
        finalized = plan.model_copy(update={"audit": audit})
        if (
            "critical_info_unavailable:xiaoqing_interview"
            in finalized.model_dump_json()
            and not any(
                "xiaoqing_interview" in str(event.get("tool") or "")
                for event in tool_events
            )
        ):
            raise RuntimeError("xiaoqing_interview_required_but_not_called")
        return finalized

    def _build_command(
        self,
        session_id: str | None,
        image_paths: tuple[str, ...] = (),
    ) -> list[str]:
        image_options = [
            option
            for path in image_paths
            for option in ("--image", path)
        ]
        common_options = [
            "--json",
            *codex_model_config_options(ignore_user_config=False),
            "--ignore-rules",
            "--disable",
            "hooks",
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
                universal_planner_developer_instructions(),
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
                *image_options,
                session_id,
                "-",
            ]
        return [
            self.runner.codex_bin,
            "exec",
            *common_options,
            *image_options,
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
