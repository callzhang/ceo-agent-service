import base64
import json
import os
import shlex
import time
import tomllib
from pathlib import Path

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
DingTalk material reading

- When judgment depends on DingTalk documents, AI minutes, or files, inspect material before deciding.
- Use DWS read-only commands with `--format json`.
- Docs: `dws doc info --node <URL> --format json`; if online doc and content needed, `dws doc read --node <URL> --format json`.
- Minutes: `dws minutes get info --id <MINUTES_ID> --format json`.
- Ordinary files: use relevant DWS file/drive read/download capability only when text context is insufficient.
- If DWS reports not_authenticated, not authenticated, exit code 2, or a login/session problem, classify it as a DWS login/tool issue, not as missing material from the sender.
- If permission fails, state the missing permission/material and do not invent contents.
- If some materials fail but others are readable, use readable materials and mention limitation.
- record why each material command was used.
- Do not expose tokens, cookies, OAuth codes, signed URLs, local credential paths, or raw secret-bearing commands.
""".strip()
# The CEO worker must call DWS and open local authorization flows. Codex exec
# resume does not support `-s`, so use the explicit bypass flag for both new and
# resumed decision threads.
CODEX_BYPASS_APPROVALS_AND_SANDBOX = "--dangerously-bypass-approvals-and-sandbox"
MEMORY_CONNECTOR_ENV_FILE = "memory_connector.env"
MEMORY_CONNECTOR_URL_ENV = "MEMORY_CONNECTOR_URL"
MEMORY_CONNECTOR_API_KEY_ENV = "CONNECTOR_API_KEY"
MEMORY_CONNECTOR_ENV_KEYS = {
    MEMORY_CONNECTOR_API_KEY_ENV,
    MEMORY_CONNECTOR_URL_ENV,
}
DWS_CLI_AUTH_ENV_KEYS = {
    "DWS_CLIENT_ID",
    "DWS_CLIENT_SECRET",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
}


def codex_developer_instructions() -> str:
    return (
        f"{CODEX_DEVELOPER_INSTRUCTIONS_PREFIX}\n\n"
        f"{DWS_MATERIAL_READING_INSTRUCTIONS}\n\n"
        f"{ceo_agent_thread_prompt()}"
    )


def _config_string(key: str, value: str) -> str:
    return f"{key}={json.dumps(value, ensure_ascii=False)}"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def _parse_export_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens = shlex.split(line, comments=True, posix=True)
        if not tokens:
            continue
        if tokens[0] == "export":
            tokens = tokens[1:]
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            values[key] = value
    return values


def _memory_connector_env() -> dict[str, str]:
    file_env = _parse_export_env_file(_codex_home() / MEMORY_CONNECTOR_ENV_FILE)
    whitelisted_file_env = {
        key: value for key, value in file_env.items() if key in MEMORY_CONNECTOR_ENV_KEYS
    }
    config_env = _memory_connector_env_from_config(_codex_home() / "config.toml")
    env = {**config_env, **whitelisted_file_env, **os.environ}
    env.pop("MEMORY_CONNECTOR_USER_ID", None)
    token = env.get(MEMORY_CONNECTOR_API_KEY_ENV)
    if token and _jwt_token_is_expired(token):
        env.pop(MEMORY_CONNECTOR_API_KEY_ENV, None)
    return env


def _memory_connector_env_from_config(config_path: Path) -> dict[str, str]:
    if not config_path.exists():
        return {}
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    memory_config = (payload.get("mcp_servers") or {}).get("memory_connector") or {}
    if not isinstance(memory_config, dict):
        return {}
    env: dict[str, str] = {}
    url = memory_config.get("url")
    if isinstance(url, str) and url.strip():
        env[MEMORY_CONNECTOR_URL_ENV] = url.strip()
    headers = memory_config.get("http_headers")
    authorization = headers.get("Authorization") if isinstance(headers, dict) else None
    if isinstance(authorization, str) and authorization.strip():
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            env[MEMORY_CONNECTOR_API_KEY_ENV] = token
    return env


def memory_connector_config_options() -> list[str]:
    env = _memory_connector_env()
    url = env.get(MEMORY_CONNECTOR_URL_ENV)
    token = env.get(MEMORY_CONNECTOR_API_KEY_ENV)
    if not url or not token:
        return []
    return [
        "-c",
        _config_string("mcp_servers.memory_connector.url", url),
        "-c",
        _config_string(
            "mcp_servers.memory_connector.bearer_token_env_var",
            MEMORY_CONNECTOR_API_KEY_ENV,
        ),
    ]


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

    def build_env(self) -> dict[str, str]:
        env = _memory_connector_env()
        for key in DWS_CLI_AUTH_ENV_KEYS:
            env.pop(key, None)
        return env.copy()

    def build_command(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
        output_schema_path: Path | None = None,
        ignore_user_config: bool = False,
    ) -> list[str]:
        image_options: list[str] = []
        for image_path in image_paths or []:
            image_options.extend(["--image", str(image_path)])
        config_isolation_options = (
            ["--ignore-user-config", "--disable", "plugins"]
            if ignore_user_config
            else []
        )
        schema_options = (
            ["--output-schema", str(output_schema_path)]
            if output_schema_path is not None
            else ["--output-schema", str(CODEX_DECISION_SCHEMA_PATH)]
        )
        common_options = [
            "--json",
            "-m",
            "gpt-5.5",
            *config_isolation_options,
            "--ignore-rules",
            "--disable",
            "hooks",
            *memory_connector_config_options(),
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            _config_string("developer_instructions", codex_developer_instructions()),
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
                self.codex_bin,
                "exec",
                "resume",
                *common_options,
                CODEX_BYPASS_APPROVALS_AND_SANDBOX,
                *(
                    ["--output-schema", str(output_schema_path)]
                    if output_schema_path is not None
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
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            *schema_options,
            *image_options,
            "--cd",
            str(self.workspace),
            "-",
        ]
