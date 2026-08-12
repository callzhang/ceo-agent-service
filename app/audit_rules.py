from __future__ import annotations

import os
import re
from html import unescape
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
RESERVED_CORE_SECTION_TITLES = frozenset(
    {
        "Runtime Invariants",
        "Dynamic Skill",
        "Audit Rules",
        "Context Facts",
        "Pydantic Wire Contract",
        "Pydantic Result Contract",
        "Pydantic Wire/Result Contract",
        "Consumer Wire Contract",
        "Audit Wire Contract",
        "Consumer Result Contract",
        "Audit Result Contract",
        "Consumer Agent Wire Contract",
        "Audit Agent Wire Contract",
        "Consumer Agent Result Contract",
        "Audit Agent Result Contract",
        "Consumer/Audit Wire Contract",
        "Consumer/Audit Result Contract",
    }
)
_NORMALIZED_RESERVED_CORE_SECTION_TITLES = frozenset(
    " ".join(title.casefold().split()) for title in RESERVED_CORE_SECTION_TITLES
)
_ATX_HEADING_RE = re.compile(
    r"^[ \t]{0,3}(?P<level>#{1,6})(?:[ \t]+(?P<title>.*?))?[ \t]*$"
)
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:`{3,}|~{3,})")
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]+)]\([^)]*\)")
_MARKDOWN_REFERENCE_LINK_RE = re.compile(r"!?\[([^\]]+)]\[[^\]]*]")
_MARKDOWN_CODE_RE = re.compile(r"`+([^`]+?)`+")
_INLINE_HTML_RE = re.compile(r"<[^>]+>")
_STRUCTURAL_HTML_RE = re.compile(
    r"<\s*/?\s*(?:address|article|aside|base|basefont|blockquote|body|caption|center|"
    r"col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|"
    r"footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|"
    r"main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|pre|script|search|"
    r"section|style|summary|table|tbody|td|template|textarea|tfoot|th|thead|title|"
    r"tr|track|ul)\b",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--|-->")
_RESERVED_MARKER_RE = re.compile(
    r"\[\s*dynamic\s*-\s*skill\s*\]",
    re.IGNORECASE,
)


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
    validate_audit_rules_text(text)
    return text


def write_audit_rules_template(text: str, path: Path | None = None) -> Path:
    validate_audit_rules_text(text)
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


def validate_audit_rules_text(text: str) -> None:
    if TAG_RE.search(text):
        raise DeveloperPromptTemplateError(
            "Audit Rules must be plain text; template tags are not allowed"
        )
    normalized_text = _normalize_markdown_inline(text)
    if _RESERVED_MARKER_RE.search(normalized_text):
        raise DeveloperPromptTemplateError(
            "Audit Rules contain the reserved structural marker [dynamic-skill]"
        )
    if _STRUCTURAL_HTML_RE.search(text) or _HTML_COMMENT_RE.search(text):
        raise DeveloperPromptTemplateError(
            "Audit Rules cannot contain structural HTML blocks or comments"
        )

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            raise DeveloperPromptTemplateError(
                "Audit Rules cannot contain fenced Markdown blocks"
            )
        heading = _ATX_HEADING_RE.match(line)
        if heading is not None:
            title = _normalize_atx_title(heading.group("title") or "")
            _validate_heading(title, len(heading.group("level")))
        if index > 0 and _is_setext_underline(line) and lines[index - 1].strip():
            _validate_heading(lines[index - 1].strip(), 1 if "=" in line else 2)


def _normalize_atx_title(title: str) -> str:
    stripped = title.strip()
    closing = len(stripped) - len(stripped.rstrip("#"))
    if closing and stripped[:-closing].endswith((" ", "\t")):
        stripped = stripped[:-closing].rstrip()
    return stripped


def _validate_heading(title: str, level: int) -> None:
    normalized = " ".join(_normalize_markdown_inline(title).casefold().split())
    if normalized in _NORMALIZED_RESERVED_CORE_SECTION_TITLES:
        raise DeveloperPromptTemplateError(
            f"Audit Rules contain reserved core heading: {title}"
        )
    if level < 3:
        raise DeveloperPromptTemplateError(
            "Audit Rules headings must use level 3 or deeper so they remain nested"
        )


def _is_setext_underline(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) in ({"="}, {"-"})


def _normalize_markdown_inline(text: str) -> str:
    normalized = unescape(text)
    normalized = _MARKDOWN_LINK_RE.sub(r"\1", normalized)
    normalized = _MARKDOWN_REFERENCE_LINK_RE.sub(r"\1", normalized)
    normalized = _MARKDOWN_CODE_RE.sub(r"\1", normalized)
    normalized = _INLINE_HTML_RE.sub("", normalized)
    normalized = normalized.replace("\\", "")
    return normalized.translate(str.maketrans("", "", "*_~"))


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(text, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
