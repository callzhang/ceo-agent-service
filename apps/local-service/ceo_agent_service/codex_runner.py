import json
import os
from pathlib import Path

from ceo_agent_service.prompt import ceo_agent_thread_prompt


CODEX_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "codex_decision.schema.json"
)
CODEX_DEVELOPER_INSTRUCTIONS_PREFIX = (
    "You are the local CEO DingTalk reply worker. Inspect the workspace before "
    "answering. Return only the requested JSON."
)
# The CEO worker must call DWS; read-only/workspace-write sandboxes can block
# local auth decryption even when HOME and token files are visible.
CODEX_SANDBOX_MODE = "danger-full-access"


def codex_developer_instructions() -> str:
    return f"{CODEX_DEVELOPER_INSTRUCTIONS_PREFIX}\n\n{ceo_agent_thread_prompt()}"


def _config_string(key: str, value: str) -> str:
    return f"{key}={json.dumps(value, ensure_ascii=False)}"


class CodexRunner:
    def __init__(self, workspace: Path, codex_bin: str = "codex"):
        self.workspace = workspace
        self.codex_bin = codex_bin

    def build_env(self) -> dict[str, str]:
        return os.environ.copy()

    def build_command(self, prompt: str, session_id: str | None) -> list[str]:
        common_options = [
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-user-config",
            "--ignore-rules",
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
                "-c",
                _config_string("sandbox_mode", CODEX_SANDBOX_MODE),
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            "-s",
            CODEX_SANDBOX_MODE,
            "--output-schema",
            str(CODEX_DECISION_SCHEMA_PATH),
            "--cd",
            str(self.workspace),
            "-",
        ]
