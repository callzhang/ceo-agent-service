import json
import os
from pathlib import Path

from app.dws_client import dws_noninteractive_environment
from app.dingtalk_models import CodexDecision
from app.prompt import ceo_agent_thread_prompt


CODEX_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "codex_decision.schema.json"
)
AGENT_ENVELOPE_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "agent_envelope.schema.json"
)
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


def codex_developer_instructions() -> str:
    schema = json.dumps(
        CodexDecision.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    prompt = ceo_agent_thread_prompt().rstrip("\n")
    return f"{prompt}\n\n## Pydantic Wire/Result Contract\n{schema}"


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
        preserve_native_instructions: bool = False,
        preserve_native_approval_config: bool = False,
    ) -> list[str]:
        if approval_policy not in {"untrusted", "never"}:
            raise ValueError("unsupported approval policy")
        effective_approval_bypass = (
            use_approval_bypass and approval_policy != "never"
        )
        if preserve_native_instructions:
            effective_developer_instructions = ""
        else:
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
        instruction_options = (
            []
            if preserve_native_instructions
            else [
                "-c",
                _config_string(
                    "developer_instructions",
                    effective_developer_instructions,
                ),
                "-c",
                "include_permissions_instructions=false",
            ]
        )
        approval_options = (
            []
            if preserve_native_approval_config
            else [
                "-c",
                _config_string("approval_policy", approval_policy),
                *(
                    ["-c", 'approvals_reviewer="auto_review"']
                    if approval_policy == "untrusted"
                    else []
                ),
            ]
        )
        common_options = [
            "--json",
            *(
                []
                if preserve_native_model_config
                else codex_model_config_options()
            ),
            *approval_options,
            *instruction_options,
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
