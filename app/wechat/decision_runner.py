"""Read-only Codex runner for creating a WeChat reply decision."""
from __future__ import annotations

from app.codex_decision import CodexDecisionRunner
from app.wechat.codex_safety import make_read_only_with_memory_tools


WECHAT_DECISION_DEVELOPER_INSTRUCTIONS = """You are a read-only WeChat reply decision worker.

- Use the supplied WeChat context. When it is genuinely useful, use only the
  configured durable-memory read tools.
- Do not run shell commands or use web search, plugins, apps, DingTalk, Lark,
  browser, approval, document, mail, or messaging tools.
- Do not send, edit, approve, react, write memory, or otherwise cause an
  external side effect. The service persists the decision and owns delivery.
- Return only the requested AgentEnvelope JSON.
"""


class WechatDecisionRunner(CodexDecisionRunner):
    """A replay-safe decision step before the persisted WeChat delivery stage."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("approval_policy", "never")
        kwargs.setdefault("use_approval_bypass", False)
        kwargs.setdefault(
            "developer_instructions", WECHAT_DECISION_DEVELOPER_INSTRUCTIONS
        )
        kwargs.setdefault("command_mutator", make_read_only_with_memory_tools)
        super().__init__(*args, **kwargs)
