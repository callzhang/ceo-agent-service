from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from app.config import repo_root
from app.developer_prompt import DeveloperPromptTemplateError, TAG_RE
from app.store import AgentRole


DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
DEFAULT_AUDIT_RULES_TEMPLATE = repo_root() / "data" / "prompts" / "audit_rules.md"
SEED_AUDIT_RULES_TEMPLATE = DEFAULTS_DIR / "audit_rules.md"

CONSUMER_RULE_WRAPPER = (
    "Use these rules to self-review the complete candidate. You are Consumer "
    "Agent A: do not approve the candidate and do not execute any external action."
)
AUDIT_RULE_WRAPPER = (
    "Independently enforce these rules as Audit Agent B. Execute only the accepted "
    "candidate exactly as authored. If business meaning must change, return concrete "
    "feedback; do not rewrite the candidate yourself."
)
EMPTY_AUDIT_RULES = "No additional configurable Audit Rules."


def audit_rules_template_path() -> Path:
    configured = os.getenv(
        "CEO_AUDIT_RULES_TEMPLATE_PATH",
        str(DEFAULT_AUDIT_RULES_TEMPLATE),
    )
    return Path(os.path.expandvars(configured)).expanduser()


def read_audit_rules_template(path: Path | None = None) -> str:
    template_path = path or audit_rules_template_path()
    if not template_path.exists():
        template_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            template_path,
            SEED_AUDIT_RULES_TEMPLATE.read_text(encoding="utf-8"),
        )
    text = template_path.read_text(encoding="utf-8")
    _validate_plain_text(text)
    return text


def write_audit_rules_template(text: str, path: Path | None = None) -> Path:
    _validate_plain_text(text)
    template_path = path or audit_rules_template_path()
    template_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(template_path, text)
    return template_path


def render_audit_rules(role: AgentRole, path: Path | None = None) -> str:
    body = read_audit_rules_template(path)
    custom = body if body.strip() else EMPTY_AUDIT_RULES
    wrapper = (
        CONSUMER_RULE_WRAPPER
        if role is AgentRole.CONSUMER
        else AUDIT_RULE_WRAPPER
    )
    return f"{wrapper}\n\n{custom}"


def _validate_plain_text(text: str) -> None:
    if TAG_RE.search(text):
        raise DeveloperPromptTemplateError(
            "Audit Rules must be plain text; template tags are not allowed"
        )


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(text, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
