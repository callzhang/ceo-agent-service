import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from app.config import codex_model, codex_model_reasoning_effort
from app.dingtalk_models import CodexDecision
from app.dws_client import dws_noninteractive_environment
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


def native_codex_login_available(
    *,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Return whether the native CLI can currently use its persisted login."""
    try:
        completed = executor(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def recover_native_codex_auth_failures(
    store: object,
    *,
    channel: str,
    auth_probe: Callable[[], bool] = native_codex_login_available,
) -> list[int]:
    """Requeue no-effect native-auth failures only after the login recovers."""
    if selected_codex_model_provider() != "openai":
        return []
    has_failures = getattr(store, "has_failed_native_codex_auth_tasks")
    recover = getattr(store, "recover_failed_native_codex_auth_tasks")
    if not has_failures(channel=channel) or not auth_probe():
        return []
    return recover(channel=channel, reason="codex_auth_recovered")


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
                raise TypeError(
                    "config inline table values must be string keyed strings"
                )
            items.append(
                f"{json.dumps(item_key, ensure_ascii=False)} = "
                f"{json.dumps(item_value, ensure_ascii=False)}"
            )
        return "{" + ", ".join(items) + "}"
    return json.dumps(value, ensure_ascii=False)


def _codex_home() -> Path:
    return resolved_codex_home(os.environ)


def resolved_codex_home(environment: Mapping[str, str]) -> Path:
    """Resolve Codex home from the child base environment without creating it."""
    home = Path(environment.get("HOME", str(Path.home())))
    configured = environment.get("CODEX_HOME", "")
    if configured == "~":
        candidate = home
    elif configured.startswith("~/"):
        candidate = home / configured[2:]
    elif configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = home / candidate
    else:
        candidate = home / ".codex"
    return candidate.resolve()


def selected_codex_model_provider() -> str:
    provider = os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()
    return provider or "openai"


def codex_model_config_options(
    *,
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    selected_model = (
        codex_model()
        if model is None
        else model.strip()
    )
    selected_provider = (
        os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()
        if provider is None
        else provider.strip()
    )
    selected_reasoning_effort = (
        codex_model_reasoning_effort()
        if reasoning_effort is None
        else reasoning_effort.strip()
    )
    options: list[str] = []
    if selected_model:
        options.extend(["-m", selected_model])
        if selected_provider:
            options.extend(["-c", _config_string("model_provider", selected_provider)])
    if selected_reasoning_effort:
        options.extend(
            [
                "-c",
                _config_string("model_reasoning_effort", selected_reasoning_effort),
            ]
        )
    return options


def codex_model_provider_settings_options(
    provider: str | None,
    settings: Mapping[str, str] | None,
) -> list[str]:
    if settings is None:
        return []
    if not provider or not provider.isidentifier():
        raise ValueError("model provider settings require an identifier provider")
    allowed = {"name", "base_url", "env_key", "wire_api"}
    unknown = set(settings) - allowed
    if unknown:
        raise ValueError("unsupported model provider setting")
    if any(not isinstance(value, str) for value in settings.values()):
        raise TypeError("model provider settings must be strings")
    options: list[str] = []
    for key in ("name", "base_url", "env_key", "wire_api"):
        if key in settings:
            options.extend(
                [
                    "-c",
                    _config_string(f"model_providers.{provider}.{key}", settings[key]),
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
        ignore_user_config: bool = False,
        model: str | None = None,
        provider: str | None = None,
        reasoning_effort: str | None = None,
        model_provider_settings: Mapping[str, str] | None = None,
        shell_environment_policy_core: bool = False,
        sandbox_mode: str | None = None,
    ) -> list[str]:
        if approval_policy not in {"untrusted", "never"}:
            raise ValueError("unsupported approval policy")
        if sandbox_mode not in {
            None,
            "read-only",
            "workspace-write",
            "danger-full-access",
        }:
            raise ValueError("unsupported sandbox mode")
        effective_approval_bypass = use_approval_bypass and approval_policy != "never"
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
            *(["--sandbox", sandbox_mode] if sandbox_mode else []),
            "--json",
            *(["--ignore-user-config"] if ignore_user_config else []),
            *(
                []
                if preserve_native_model_config
                else codex_model_config_options(
                    model=model,
                    provider=provider,
                    reasoning_effort=reasoning_effort,
                )
            ),
            *codex_model_provider_settings_options(provider, model_provider_settings),
            *(
                [
                    "-c",
                    _config_string("shell_environment_policy.inherit", "core"),
                    "-c",
                    _config_string(
                        "shell_environment_policy.ignore_default_excludes",
                        False,
                    ),
                ]
                if shell_environment_policy_core
                else []
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
