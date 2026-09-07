"""Codex runner for creating a typed WeChat reply decision."""
from __future__ import annotations

from app.codex_decision import CodexDecisionRunner
WECHAT_DECISION_DEVELOPER_INSTRUCTIONS = """You are a WeChat reply decision worker.

- Use the supplied WeChat context and the capabilities available in the runtime.
- Decide the requested reply; delivery remains a separate persisted service step.
- Return only the requested AgentEnvelope JSON.
"""


class WechatDecisionRunner(CodexDecisionRunner):
    """A replay-safe decision step before the persisted WeChat delivery stage."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("approval_policy", "on-failure")
        kwargs.setdefault("use_approval_bypass", False)
        kwargs.setdefault(
            "developer_instructions", WECHAT_DECISION_DEVELOPER_INSTRUCTIONS
        )
        super().__init__(*args, **kwargs)

    def _routed_command_factory(self, image_paths):
        from app.agent_runtime_router import CodexCommandFactory

        return CodexCommandFactory.standard(
            developer_instructions=WECHAT_DECISION_DEVELOPER_INSTRUCTIONS,
            image_paths=image_paths,
        )
