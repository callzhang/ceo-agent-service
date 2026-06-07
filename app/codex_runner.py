import json
import os
import shlex
from pathlib import Path

from app.prompt import ceo_agent_thread_prompt


CODEX_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "codex_decision.schema.json"
)
CODEX_DEVELOPER_INSTRUCTIONS_PREFIX = (
    "You are the local CEO DingTalk reply worker. Inspect the workspace before "
    "answering. Return only the requested JSON."
)
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


def codex_developer_instructions() -> str:
    return f"{CODEX_DEVELOPER_INSTRUCTIONS_PREFIX}\n\n{ceo_agent_thread_prompt()}"


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
    env = {**whitelisted_file_env, **os.environ}
    env.pop("MEMORY_CONNECTOR_USER_ID", None)
    return env


def memory_connector_config_options() -> list[str]:
    env = _memory_connector_env()
    url = env.get(MEMORY_CONNECTOR_URL_ENV)
    if not url:
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


class CodexRunner:
    def __init__(self, workspace: Path, codex_bin: str = "codex"):
        self.workspace = workspace
        self.codex_bin = codex_bin

    def build_env(self) -> dict[str, str]:
        env = _memory_connector_env()
        return env.copy()

    def build_command(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
        output_schema_path: Path | None = CODEX_DECISION_SCHEMA_PATH,
        ignore_user_config: bool = False,
    ) -> list[str]:
        image_options: list[str] = []
        for image_path in image_paths or []:
            image_options.extend(["--image", str(image_path)])
        common_options = [
            "--json",
            "-m",
            "gpt-5.5",
            *(["--ignore-user-config"] if ignore_user_config else []),
            "--ignore-rules",
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
                *image_options,
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            *(
                ["--output-schema", str(output_schema_path)]
                if output_schema_path
                else []
            ),
            *image_options,
            "--cd",
            str(self.workspace),
            "-",
        ]
