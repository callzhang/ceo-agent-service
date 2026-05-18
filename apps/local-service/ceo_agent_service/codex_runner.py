import os
from pathlib import Path


CODEX_DECISION_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "codex_decision.schema.json"
)
CODEX_DEVELOPER_INSTRUCTIONS = (
    "You are the local CEO DingTalk reply worker. Inspect the workspace before "
    "answering. Return only the requested JSON."
)


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
            f'developer_instructions="{CODEX_DEVELOPER_INSTRUCTIONS}"',
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
                'sandbox_mode="read-only"',
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            "-s",
            "read-only",
            "--output-schema",
            str(CODEX_DECISION_SCHEMA_PATH),
            "--cd",
            str(self.workspace),
            "-",
        ]
