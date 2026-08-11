from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any


INVARIANT_IDENTITIES = (
    ("role_boundary", "Role Boundary"),
    ("output_contracts", "Output Contracts"),
    ("supported_facts", "Supported Facts"),
    ("meaning_preservation", "Meaning Preservation"),
    ("duplicate_effects", "Duplicate Effects"),
    ("unknown_effects", "Unknown Effects"),
    ("external_secrecy", "External Secrecy"),
    ("dependency_auth", "Dependency Authentication"),
)
PERMITTED_CORE_SECTIONS = {
    "Role",
    "Runtime Invariants",
    "Audit Rules",
    "Dynamic Skill",
    "Pydantic Wire Contract",
    "Pydantic Result Contract",
    "Pydantic Wire/Result Contract",
    "Context Facts",
}
SKILL_INSTRUCTION_MARKER = "[dynamic-skill]"
_SECTION_RE = re.compile(r"^## (?P<title>[^\n]+)$", re.MULTILINE)
_INVARIANT_RE = re.compile(
    r"^(?P<number>\d+)\. \[(?P<identifier>[a-z_]+)] "
    r"(?P<title>[^:]+): (?P<body>.+)$",
    re.MULTILINE,
)
_NUMBERED_LINE_RE = re.compile(r"^\d+\. .+$", re.MULTILINE)


def validate_prompt_structure(
    text: str,
    *,
    contract_models: Sequence[tuple[str, type[Any]]],
    require_audit_rules: bool,
    require_context_facts: bool,
    size_limit: int,
) -> None:
    sections = [match.group("title") for match in _SECTION_RE.finditer(text)]
    unknown_sections = set(sections) - PERMITTED_CORE_SECTIONS
    assert not unknown_sections, f"unexpected core sections: {sorted(unknown_sections)}"
    expected_sections = {
        "Role",
        "Runtime Invariants",
        "Dynamic Skill",
        *(section_title for section_title, _model in contract_models),
    }
    if require_audit_rules:
        expected_sections.add("Audit Rules")
    if require_context_facts:
        expected_sections.add("Context Facts")
    assert set(sections) == expected_sections
    assert all(sections.count(section) == 1 for section in expected_sections)
    assert text.count(SKILL_INSTRUCTION_MARKER) == 1

    invariant_body = _section_body(text, "Runtime Invariants")
    numbered_lines = _NUMBERED_LINE_RE.findall(invariant_body)
    invariants = [
        (
            int(match.group("number")),
            match.group("identifier"),
            match.group("title"),
        )
        for match in _INVARIANT_RE.finditer(invariant_body)
    ]
    assert len(numbered_lines) == len(invariants) == len(INVARIANT_IDENTITIES)
    assert invariants == [
        (index, identifier, title)
        for index, (identifier, title) in enumerate(INVARIANT_IDENTITIES, start=1)
    ]

    for section_title, model in contract_models:
        assert sections.count(section_title) == 1
        expected_schema = json.dumps(
            model.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert _section_body(text, section_title).strip() == expected_schema

    assert len(text) < size_limit


def _section_body(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n", text, re.MULTILINE)
    assert match is not None, f"missing section: {title}"
    next_section = _SECTION_RE.search(text, match.end())
    end = next_section.start() if next_section is not None else len(text)
    return text[match.end() : end].strip()
