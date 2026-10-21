"""Codex runner for creating a typed WeChat reply decision."""

from __future__ import annotations

from app.codex_decision import CodexDecisionRunner
from app.agent_runtime_contracts import ROLE_BOUND_AGENT_TOOLS
from app.wechat.codex_safety import make_model_only_agent_command

WECHAT_DECISION_DEVELOPER_INSTRUCTIONS = """You are a WeChat reply decision worker.

- Use the supplied WeChat context and the capabilities available in the runtime.
- Decide the requested reply; delivery remains a separate persisted service step.
- Return only the requested AgentEnvelope JSON.
Example AgentEnvelope for no reply: {"kind":"no_action","user_response":{"mode":"no_reply","text":"","sensitivity_kind":"general"},"system_actions":[],"domain_payload":{},"audit":{"summary":"No reply needed","documents":[],"confidence":1}}
For a reply, use kind=reply, user_response.mode=send_reply, and put the reply in user_response.text. Keep the same envelope fields.
"""


class WechatDecisionRunner(CodexDecisionRunner):
    """A replay-safe decision step before the persisted WeChat delivery stage."""

    def __init__(self, *args, **kwargs):
        kwargs["approval_policy"] = "never"
        kwargs["use_approval_bypass"] = False
        kwargs["command_mutator"] = make_model_only_agent_command
        kwargs.setdefault(
            "developer_instructions", WECHAT_DECISION_DEVELOPER_INSTRUCTIONS
        )
        super().__init__(*args, **kwargs)

    def _routed_command_factory(self, image_paths):
        from app.agent_runtime_router import CodexCommandFactory

        return CodexCommandFactory.standard(
            developer_instructions=WECHAT_DECISION_DEVELOPER_INSTRUCTIONS,
            image_paths=image_paths,
            model_only=True,
        )

    def _required_capabilities(self) -> frozenset[str]:
        return super()._required_capabilities() | {ROLE_BOUND_AGENT_TOOLS}
