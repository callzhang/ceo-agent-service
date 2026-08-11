import json
import os
from pathlib import Path

from app.dws_client import dws_noninteractive_environment
from app.prompt import ceo_agent_thread_prompt


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
DingTalk material access

- Use the exact read command supplied in the task context without rewriting or substituting it.
- Operation discovery and syntax belong to the loaded operation Skill.
- Never run `dws auth login`, `dws auth reset`, `dws auth logout`, or any command that asks for interactive/browser authorization.
- If DWS reports not_authenticated, not authenticated, exit code 2, or a login/session problem, classify it as a DWS login/tool issue, not as missing material from the sender.
- If DWS reports AGENT_CODE_NOT_EXISTS, openBrowser, personalAuthorization, PAT permission failure, or a CLI authorization page, stop that tool path and classify it as DWS authorization/configuration unavailable; do not retry the command and do not start a login flow.
- Do not expose tokens, cookies, OAuth codes, signed URLs, local credential paths, or raw secret-bearing commands.
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
    return (
        "Memory connector runtime\n\n"
        "- This invocation inherits the principal's configured Codex MCP servers, "
        "plugins, and skills, including memory_connector when it is installed.\n"
        "- Use memory_connector when durable context is relevant. If the tool itself "
        "reports unavailable or unauthorized, classify that as a tool dependency "
        "issue; do not start an interactive login flow or infer missing user facts."
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
    """Do not infer native OAuth availability from a copied service config."""
    return ""


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
        approval_policy: str = "untrusted",
        developer_instructions: str | None = None,
        use_approval_bypass: bool = True,
        preserve_native_model_config: bool = False,
    ) -> list[str]:
        if approval_policy not in {"untrusted", "never"}:
            raise ValueError("unsupported approval policy")
        effective_approval_bypass = (
            use_approval_bypass and approval_policy != "never"
        )
        effective_developer_instructions = (
            developer_instructions
            if developer_instructions is not None
            else codex_developer_instructions()
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
                effective_developer_instructions,
            ),
            "-c",
            "include_permissions_instructions=false",
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
