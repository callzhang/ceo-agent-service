from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from app.audit_rules import validate_audit_rules_text


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
    "Runtime Invariants",
    "Audit Rules",
    "Dynamic Skill",
    "Pydantic Wire Contract",
    "Pydantic Result Contract",
    "Pydantic Wire/Result Contract",
    "Capability Instructions",
    "Shared Agent Rules",
    "Role Boundary",
    "Context Facts",
}
SKILL_INSTRUCTION_MARKER = "[dynamic-skill]"
_SECTION_RE = re.compile(
    r"^## (?P<title>"
    + "|".join(re.escape(title) for title in sorted(PERMITTED_CORE_SECTIONS))
    + r")$",
    re.MULTILINE,
)
_INVARIANT_RE = re.compile(
    r"(?P<number>\d+)\. \[(?P<identifier>[a-z_]+)] "
    r"(?P<title>[^:]+): (?P<body>.+)",
)
_ROLE_BOUNDARY_RE = re.compile(
    r"Consumer Agent A is (?P<principal>.+)'s read-only representative; "
    r"Audit Agent B is the only role allowed to execute an accepted candidate\."
)


def validate_prompt_structure(
    text: str,
    *,
    contract_models: Sequence[tuple[str, type[Any]]],
    dynamic_skill_body: str,
    audit_rules: str | None,
    context_facts: str | None,
    size_limit: int,
    require_runtime_safety_sections: bool = False,
) -> None:
    if audit_rules is not None:
        validate_audit_rules_text(audit_rules)
    section_matches = list(_SECTION_RE.finditer(text))
    assert section_matches and section_matches[0].start() == 0
    sections = [match.group("title") for match in section_matches]
    expected_sections = {
        "Runtime Invariants",
        "Dynamic Skill",
        *(section_title for section_title, _model in contract_models),
    }
    safety_sections = {
        "Capability Instructions",
        "Role Boundary",
        "Shared Agent Rules",
    }
    if require_runtime_safety_sections:
        expected_sections.update(safety_sections)
    else:
        assert not set(sections).intersection(safety_sections)
    if audit_rules is not None:
        expected_sections.add("Audit Rules")
    if context_facts is not None:
        expected_sections.add("Context Facts")
    assert set(sections) == expected_sections
    assert all(sections.count(section) == 1 for section in expected_sections)
    assert text.count(SKILL_INSTRUCTION_MARKER) == 1
    assert _exact_section_body(text, "Dynamic Skill") == dynamic_skill_body
    if audit_rules is not None:
        assert _exact_section_body(text, "Audit Rules") == audit_rules
    if context_facts is not None:
        assert _exact_section_body(text, "Context Facts") == context_facts
        _validate_context_facts_shape(context_facts)

    invariant_body = _exact_section_body(text, "Runtime Invariants")
    invariant_lines = invariant_body.splitlines()
    matches = [_INVARIANT_RE.fullmatch(line) for line in invariant_lines]
    assert len(invariant_lines) == len(INVARIANT_IDENTITIES)
    assert all(match is not None for match in matches)
    invariants = [
        (
            int(match.group("number")),
            match.group("identifier"),
            match.group("title"),
        )
        for match in matches
        if match is not None
    ]
    assert invariants == [
        (index, identifier, title)
        for index, (identifier, title) in enumerate(INVARIANT_IDENTITIES, start=1)
    ]
    role_boundary = matches[0]
    assert role_boundary is not None
    role_fields = _ROLE_BOUNDARY_RE.fullmatch(role_boundary.group("body"))
    assert role_fields is not None
    assert role_fields.group("principal").strip()

    for section_title, model in contract_models:
        assert sections.count(section_title) == 1
        expected_schema = json.dumps(
            model.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert _exact_section_body(text, section_title) == expected_schema

    assert len(text) < size_limit


def _exact_section_body(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n", text, re.MULTILINE)
    assert match is not None, f"missing section: {title}"
    next_section = _SECTION_RE.search(text, match.end())
    if next_section is None:
        body = text[match.end() :]
        if body.endswith("\n"):
            body = body[:-1]
    else:
        raw_body = text[match.end() : next_section.start()]
        assert raw_body.endswith("\n\n")
        body = raw_body[:-2]
    assert body and body == body.strip("\n")
    return body


def _validate_context_facts_shape(body: str) -> None:
    blocks = body.split("\n\n")
    labels: list[str] = []
    values: dict[str, object] = {}
    for block in blocks:
        label, separator, payload = block.partition("\n")
        assert separator and payload
        labels.append(label)
        values[label] = (
            payload
            if label == "Current turn execution time"
            else json.loads(payload)
        )

    assert labels[:4] == [
        "Current turn execution time",
        "Original trigger",
        "Recent conversation context",
        "Raw material references and exact read commands",
    ]
    allowed_optional = [
        "Actual Codex image inputs",
        "Safe prior execution receipts",
        "Manual rerun instruction",
        "Verified Skills read by Consumer A",
        "Candidate revision",
    ]
    assert labels[4:] == [label for label in allowed_optional if label in labels]

    _assert_object_keys(
        values["Original trigger"],
        {
            "task_id",
            "channel",
            "conversation_id",
            "conversation_title",
            "single_chat",
            "message_id",
            "sender",
            "sender_user_id",
            "sender_open_dingtalk_id",
            "mentioned_user_ids",
            "text",
            "create_time",
            "raw_payload",
        },
    )
    _assert_array_item_keys(
        values["Recent conversation context"],
        {"message_id", "sender", "text", "create_time"},
    )
    _assert_array_item_keys(
        values["Raw material references and exact read commands"],
        {"kind", "reference", "source_message_id", "read_commands"},
    )
    optional_shapes = {
        "Actual Codex image inputs": {"path", "sha256"},
        "Safe prior execution receipts": {
            "receipt_id",
            "operation",
            "summary",
            "completed",
        },
        "Verified Skills read by Consumer A": {"name", "path", "sha256"},
    }
    for label, keys in optional_shapes.items():
        if label in values:
            _assert_array_item_keys(values[label], keys)
    if "Manual rerun instruction" in values:
        _assert_object_keys(
            values["Manual rerun instruction"],
            {"source_attempt_id", "reviewer_feedback", "suggested_reply_text"},
        )
    if "Candidate revision" in values:
        _assert_object_keys(
            values["Candidate revision"],
            {"proposal_revision", "operation_id", "proposal"},
        )


def _assert_object_keys(value: object, keys: set[str]) -> None:
    assert isinstance(value, dict) and set(value) == keys


def _assert_array_item_keys(value: object, keys: set[str]) -> None:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) and set(item) == keys for item in value)
