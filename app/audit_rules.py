from __future__ import annotations

import os
import re
from html import unescape
from pathlib import Path
from unicodedata import category, normalize
from uuid import uuid4

from app.config import principal_display_name, repo_root
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
_AUDIT_VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ALLOWED_AUDIT_VARIABLES = frozenset({"principal"})
_MAX_ENTITY_DECODE_PASSES = 4
_ALLOWED_CONTROL_CHARACTERS = frozenset({"\n", "\t"})

# Unicode 15.0 DerivedCoreProperties.txt: Default_Ignorable_Code_Point.
# Keep this table centralized so a Unicode-version update is a single audited change.
_DEFAULT_IGNORABLE_CODE_POINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
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
    body = _render_audit_variables(read_audit_rules_template(path))
    custom = body if body.strip() else EMPTY_AUDIT_RULES
    wrapper = (
        CONSUMER_RULE_WRAPPER
        if role is AgentRole.CONSUMER
        else AUDIT_RULE_WRAPPER
    )
    return f"{wrapper}\n\n{custom}"


def _render_audit_variables(body: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in _ALLOWED_AUDIT_VARIABLES:
            raise DeveloperPromptTemplateError(
                f"unsupported Audit Rules variable: {name}"
            )
        return principal_display_name().strip() or "the principal"

    return _AUDIT_VARIABLE_RE.sub(replace, body)


def validate_audit_rules_text(text: str) -> None:
    structural_text = _normalize_for_structural_validation(text)
    if TAG_RE.search(structural_text):
        raise DeveloperPromptTemplateError(
            "Audit Rules must be plain text; template tags are not allowed"
        )
    normalized_text = _normalize_markdown_inline(structural_text)
    if _RESERVED_MARKER_RE.search(normalized_text):
        raise DeveloperPromptTemplateError(
            "Audit Rules contain the reserved structural marker [dynamic-skill]"
        )
    if _STRUCTURAL_HTML_RE.search(structural_text) or _HTML_COMMENT_RE.search(
        structural_text
    ):
        raise DeveloperPromptTemplateError(
            "Audit Rules cannot contain structural HTML blocks or comments"
        )

    if "{{" in structural_text or "}}" in structural_text:
        for match in _AUDIT_VARIABLE_RE.finditer(structural_text):
            if match.group(1) not in _ALLOWED_AUDIT_VARIABLES:
                raise DeveloperPromptTemplateError(
                    f"unsupported Audit Rules variable: {match.group(1)}"
                )
        remaining = _AUDIT_VARIABLE_RE.sub("", structural_text)
        if "{{" in remaining or "}}" in remaining:
            raise DeveloperPromptTemplateError(
                "Audit Rules contain an invalid template variable"
            )

    lines = structural_text.splitlines()
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
    normalized = _MARKDOWN_LINK_RE.sub(r"\1", text)
    normalized = _MARKDOWN_REFERENCE_LINK_RE.sub(r"\1", normalized)
    normalized = _MARKDOWN_CODE_RE.sub(r"\1", normalized)
    normalized = _INLINE_HTML_RE.sub("", normalized)
    normalized = normalized.replace("\\", "")
    return normalized.translate(str.maketrans("", "", "*_~"))


def _normalize_for_structural_validation(text: str) -> str:
    decoded = text
    for _ in range(_MAX_ENTITY_DECODE_PASSES):
        next_value = unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        if unescape(decoded) != decoded:
            raise DeveloperPromptTemplateError(
                "Audit Rules entity decoding did not stabilize"
            )

    normalized = normalize("NFKC", decoded)
    for character in normalized:
        if _is_default_ignorable_or_noncharacter(character):
            raise DeveloperPromptTemplateError(
                "Audit Rules contain a Unicode default-ignorable or "
                "noncharacter code point"
            )
        character_category = category(character)
        if character_category == "Cf" or (
            character_category == "Cc"
            and character not in _ALLOWED_CONTROL_CHARACTERS
        ):
            raise DeveloperPromptTemplateError(
                "Audit Rules contain an unsafe invisible or control character"
            )
    return normalized


def _is_default_ignorable_or_noncharacter(character: str) -> bool:
    code_point = ord(character)
    if any(
        first <= code_point <= last
        for first, last in _DEFAULT_IGNORABLE_CODE_POINT_RANGES
    ):
        return True
    return 0xFDD0 <= code_point <= 0xFDEF or (code_point & 0xFFFF) in {
        0xFFFE,
        0xFFFF,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(text, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
