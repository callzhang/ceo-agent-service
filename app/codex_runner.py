import base64
import json
import os
import time
from pathlib import Path

from app.dws_client import dws_noninteractive_environment
from app.prompt import ceo_agent_thread_prompt
from app.service_codex_config import (
    ServiceMcpConfigError,
    load_service_mcp_servers,
    service_mcp_config_options,
)


CODEX_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "codex_decision.schema.json"
)
AGENT_ENVELOPE_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "agent_envelope.schema.json"
)
CODEX_DEVELOPER_INSTRUCTIONS_PREFIX = (
    "You are the local CEO DingTalk reply worker. Inspect the workspace before "
    "answering. Return only the requested JSON."
)
DWS_MATERIAL_READING_INSTRUCTIONS = """
DingTalk material reading

- When judgment depends on DingTalk documents, AI minutes, or files, inspect material before deciding.
- Use DWS read-only commands with `--timeout 900 --format json` so unstable network reads can wait up to fifteen minutes.
- Docs: `dws doc info --node <URL> --format json`; if online doc and content needed, `dws doc read --node <URL> --format json`.
- Minutes: `dws minutes get info --id <MINUTES_ID> --format json`.
- Ordinary files: use relevant DWS file/drive read/download capability only when text context is insufficient.
- Never run `dws auth login`, `dws auth reset`, `dws auth logout`, or any command that asks for interactive/browser authorization.
- If DWS reports not_authenticated, not authenticated, exit code 2, or a login/session problem, classify it as a DWS login/tool issue, not as missing material from the sender.
- If DWS reports AGENT_CODE_NOT_EXISTS, openBrowser, personalAuthorization, PAT permission failure, or a CLI authorization page, stop that tool path and classify it as DWS authorization/configuration unavailable; do not retry the command and do not start a login flow.
- If permission fails, state the missing permission/material and do not invent contents.
- If a required DWS read still fails and no other material supports the judgment, return an error envelope whose audit summary starts with `dws_transient_dependency_unavailable:`; do not send a refusal, handoff, clarification, or unsupported answer.
- If some materials fail but others are readable, use readable materials and mention limitation.
- record why each material command was used.
- Do not expose tokens, cookies, OAuth codes, signed URLs, local credential paths, or raw secret-bearing commands.

DingTalk mail handling

- A truncated mail card or quoted mail preview is only a locator. Do not treat its visible excerpt as the complete message and do not ask the sender to paste the body before trying mail lookup.
- Start with `dws mail mailbox list --format json`, choose the mailbox matching the principal, then locate the original with `dws mail message search --email <MAILBOX> --query '<KQL>' --format json` using the quoted subject and sender.
- Read the complete original with `dws mail message get --email <MAILBOX> --id <MESSAGE_ID> --format json`. Inspect linked documents or sheets when the requested approval depends on them.
- Before replying, inspect the current mail thread or sent state to avoid duplicate replies.
- When the trigger explicitly authorizes replying and the review is complete, emit one `dws_mail_reply` system action containing mailbox, original message_id, reply subject, and reply content, plus a normal DingTalk acknowledgement in user_response.text.
- The worker owns externally visible mail delivery and retry deduplication: do not execute `dws mail message reply` directly from the decision agent.
""".strip()
XIAOQING_INTERVIEW_READING_INSTRUCTIONS = """
Xiaoqing interview material reading

- Candidate links under `https://interview.hr.startask.net/candidates/` are Xiaoqing interview-system records, not ordinary DingTalk docs or webpages.
- When a candidate or hiring judgment depends on a Xiaoqing link, candidate name, interview record, resume, offer, hiring approval, or candidate comparison, use the `xiaoqing_interview` MCP tools before deciding.
- If a Xiaoqing candidate URL is absent but a candidate name is present, call `search_candidates` with that name, pick the matching candidate, then call `get_interview_context` before making the hiring judgment.
- For task/project/follow-up decisions about a candidate's process status, treat Xiaoqing's current stage, final_decision/current decision, decision time, and decision note as the current source of truth before asking HR to confirm status.
- If Xiaoqing already shows a terminal final decision such as rejected/eliminated/pass/talent-pool or a clear closed stage, close or suppress the follow-up instead of asking HR whether to continue or close.
- Do not use curl, browser scraping, DWS doc commands, or local search as substitutes for the Xiaoqing candidate record.
- If `xiaoqing_interview` is unavailable, unauthorized, or cannot return the review package, classify it as a blocking tool/auth issue with `critical_info_unavailable:xiaoqing_interview ...`; do not tell HR the sender failed to provide the interview text when the link itself was provided.
- Only ask HR to paste interview text after the Xiaoqing tool confirms the record lacks that content or the current user truly lacks access.
""".strip()
# The CEO worker owns DWS readiness and authorization gating. Codex exec resume
# does not support `-s`, so use the explicit bypass flag for both new and resumed
# decision threads.
CODEX_BYPASS_APPROVALS_AND_SANDBOX = "--dangerously-bypass-approvals-and-sandbox"
DWS_CLI_AUTH_ENV_KEYS = {
    "DWS_CLIENT_ID",
    "DWS_CLIENT_SECRET",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
}
CODEX_MODEL_ENV = "CEO_CODEX_MODEL"
CODEX_MODEL_PROVIDER_ENV = "CEO_CODEX_MODEL_PROVIDER"
CODEX_MODEL_REASONING_EFFORT_ENV = "CEO_CODEX_MODEL_REASONING_EFFORT"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_MODEL_REASONING_EFFORT = "medium"


def _memory_connector_runtime_instructions() -> str:
    issue = memory_connector_config_issue()
    if not issue:
        return (
            "Memory connector runtime\n\n"
            "- memory_connector MCP is available in this Codex invocation."
        )
    return (
        "Memory connector runtime\n\n"
        f"- memory_connector MCP is unavailable in this Codex invocation: {issue}.\n"
        "- Do not call memory_connector MCP tools. Use current prompt material, "
        "DWS, Exa, Lark CLI, Xiaoqing MCP, or local files when available; if "
        "critical information is still missing, return the appropriate blocked "
        "or stop_with_error result."
    )


def codex_developer_instructions() -> str:
    return (
        f"{CODEX_DEVELOPER_INSTRUCTIONS_PREFIX}\n\n"
        f"{DWS_MATERIAL_READING_INSTRUCTIONS}\n\n"
        f"{XIAOQING_INTERVIEW_READING_INSTRUCTIONS}\n\n"
        f"{ceo_agent_thread_prompt()}\n\n"
        f"{_memory_connector_runtime_instructions()}"
    )


def _config_string(key: str, value: object) -> str:
    return f"{key}={_config_value(value)}"


def _config_value(value: object) -> str:
    if isinstance(value, dict):
        items: list[str] = []
        for item_key, item_value in value.items():
            if not isinstance(item_key, str) or not isinstance(item_value, str):
                raise TypeError("config inline table values must be string keyed strings")
            items.append(
                f"{json.dumps(item_key, ensure_ascii=False)} = "
                f"{json.dumps(item_value, ensure_ascii=False)}"
            )
        return "{" + ", ".join(items) + "}"
    return json.dumps(value, ensure_ascii=False)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def selected_codex_model_provider() -> str:
    provider = os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()
    return provider or "openai"


def codex_model_config_options() -> list[str]:
    model = os.environ.get(CODEX_MODEL_ENV, DEFAULT_CODEX_MODEL).strip()
    provider = os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()
    reasoning_effort = os.environ.get(
        CODEX_MODEL_REASONING_EFFORT_ENV,
        DEFAULT_CODEX_MODEL_REASONING_EFFORT,
    ).strip()
    options: list[str] = []
    if model:
        options.extend(["-m", model])
        if provider:
            options.extend(["-c", _config_string("model_provider", provider)])
    if reasoning_effort:
        options.extend(
            [
                "-c",
                _config_string("model_reasoning_effort", reasoning_effort),
            ]
        )
    return options


def memory_connector_config_issue() -> str:
    try:
        servers = load_service_mcp_servers()
    except ServiceMcpConfigError as exc:
        return f"service MCP configuration is invalid: {exc.reason}"
    memory_connector = next(
        (server for server in servers if server.name == "memory_connector"),
        None,
    )
    if memory_connector is None:
        return "memory_connector is not present in the service MCP manifest"
    token_env = memory_connector.bearer_token_env_var
    if token_env and _jwt_token_is_expired(os.environ.get(token_env, "")):
        return "memory connector token is expired"
    return ""


def _jwt_token_is_expired(token: str, *, now: float | None = None) -> bool:
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload_segment = parts[1]
    try:
        padded = payload_segment + "=" * ((4 - len(payload_segment) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return False
    return exp <= (time.time() if now is None else now)


class CodexRunner:
    def __init__(self, workspace: Path, codex_bin: str = "codex"):
        self.workspace = workspace
        self.codex_bin = codex_bin

    def build_env(
        self,
        *,
        preserve_local_cli_auth: bool = False,
    ) -> dict[str, str]:
        base_env = os.environ.copy()
        env = (
            base_env
            if preserve_local_cli_auth
            else dws_noninteractive_environment(base_env)
        )
        if preserve_local_cli_auth:
            env.pop("DINGTALK_DWS_AGENTCODE", None)
            env.pop("CEO_DWS_AGENT_CODE", None)
        for key in DWS_CLI_AUTH_ENV_KEYS:
            env.pop(key, None)
        env.pop("MEMORY_CONNECTOR_USER_ID", None)
        return env.copy()

    def build_command(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
        output_schema_path: Path | None = None,
        use_output_schema: bool = True,
        ignore_user_config: bool = True,
        approval_policy: str = "untrusted",
        developer_instructions: str | None = None,
        use_approval_bypass: bool = True,
        preserve_native_model_config: bool = False,
    ) -> list[str]:
        if not ignore_user_config:
            raise ValueError("service Codex runs require user config isolation")
        if approval_policy not in {"untrusted", "never"}:
            raise ValueError("unsupported approval policy")
        effective_approval_bypass = (
            use_approval_bypass and approval_policy != "never"
        )
        image_options: list[str] = []
        for image_path in image_paths or []:
            image_options.extend(["--image", str(image_path)])
        if not use_output_schema:
            schema_options: list[str] = []
        elif output_schema_path is not None:
            schema_options = ["--output-schema", str(output_schema_path)]
        else:
            schema_options = ["--output-schema", str(CODEX_DECISION_SCHEMA_PATH)]
        common_options = [
            "--json",
            *(
                []
                if preserve_native_model_config
                else codex_model_config_options()
            ),
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "hooks",
            *service_mcp_config_options(),
            "-c",
            _config_string("approval_policy", approval_policy),
            *(
                ["-c", 'approvals_reviewer="auto_review"']
                if approval_policy == "untrusted"
                else []
            ),
            "-c",
            _config_string(
                "developer_instructions",
                developer_instructions or codex_developer_instructions(),
            ),
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
        ]
        if session_id:
            return [
                self.codex_bin,
                "exec",
                "resume",
                *common_options,
                *(
                    [CODEX_BYPASS_APPROVALS_AND_SANDBOX]
                    if effective_approval_bypass
                    else []
                ),
                *(
                    ["--output-schema", str(output_schema_path)]
                    if use_output_schema and output_schema_path is not None
                    else []
                ),
                *image_options,
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            *(
                [CODEX_BYPASS_APPROVALS_AND_SANDBOX]
                if effective_approval_bypass
                else []
            ),
            *schema_options,
            *image_options,
            "--cd",
            str(self.workspace),
            "-",
        ]
