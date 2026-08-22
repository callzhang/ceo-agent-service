from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.native_cli_metadata import AgentReadOnlyViolationError


@dataclass(frozen=True)
class LoadedSkillReceipt:
    name: str
    path: str
    sha256: str


AGENT_SKILL_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".codex" / "plugins",
)
REVIEWED_SKILL_CAPABILITY_PREFIX = "reviewed_skill:"
REVIEWED_SKILL_RECEIPT_VALIDATION_CAPABILITY = "reviewed_skill_receipt_validation"


def is_reviewed_skill_capability(capability: str) -> bool:
    """Whether a route requirement names one concrete, persisted Skill receipt."""
    parts = capability.split(":")
    return (
        len(parts) == 3
        and parts[0] == REVIEWED_SKILL_CAPABILITY_PREFIX.removesuffix(":")
        and bool(parts[1])
        and bool(parts[2])
    )


@dataclass(frozen=True)
class AuthorizedSkillPath:
    name: str
    path: Path


def resolve_authorized_skill_path(
    path: str,
    *,
    authorized_roots: tuple[Path, ...] | None = None,
) -> AuthorizedSkillPath:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
        roots: list[Path] = []
        roots_source = AGENT_SKILL_ROOTS if authorized_roots is None else authorized_roots
        for root in roots_source:
            try:
                roots.append(root.expanduser().resolve(strict=True))
            except (OSError, RuntimeError, UnicodeError):
                continue
        if resolved.suffix.casefold() != ".md":
            raise AgentReadOnlyViolationError("skill_path_forbidden")
        for root in roots:
            if not resolved.is_relative_to(root):
                continue
            parent = resolved.parent
            while parent != root:
                if (parent / "SKILL.md").is_file():
                    return AuthorizedSkillPath(name=parent.name, path=resolved)
                parent = parent.parent
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise AgentReadOnlyViolationError("skill_path_forbidden") from exc
    raise AgentReadOnlyViolationError("skill_path_forbidden")


def loaded_skill_receipts(
    events: Iterable[dict[str, object]],
) -> tuple[LoadedSkillReceipt, ...]:
    """Build trusted context evidence for Audit B from its exact Consumer parent.

    Audit B remains the configured execution authority and must reread these paths.
    The runtime verifies the exact reread receipts before Audit B starts a write;
    receipts do not replace operation-specific review or external readback.
    """
    receipts: dict[str, LoadedSkillReceipt] = {}
    for event in events:
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "mcp_tool_call"
            or item.get("server") != "agent_cli"
            or item.get("tool") != "read_skill"
            or item.get("status") != "completed"
        ):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        path = _validated_persisted_path(metadata.get("skill_path"))
        name = _validated_skill_name(metadata.get("skill_name"))
        digest = _validated_digest(metadata.get("skill_sha256"))
        if path is None or name is None or digest is None:
            continue
        receipts[path] = LoadedSkillReceipt(name=name, path=path, sha256=digest)
    return tuple(receipts[path] for path in sorted(receipts))

def normalized_read_skill_metadata(
    arguments: object,
    result: object,
    *,
    authorized_roots: tuple[Path, ...] | None = None,
) -> dict[str, str] | None:
    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        return None
    requested = arguments.get("path")
    if not isinstance(requested, str) or not requested:
        return None
    try:
        skill = resolve_authorized_skill_path(
            requested,
            authorized_roots=authorized_roots,
        )
        receipt = _read_skill_result(result)
        if receipt is None:
            return None
        content, digest, result_path, result_name = receipt
        encoded_content = content.encode("utf-8")
        content_digest = hashlib.sha256(encoded_content).hexdigest()
    except (
        AgentReadOnlyViolationError,
        MemoryError,
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
    ):
        return None
    if result_path != str(skill.path) or result_name != skill.name:
        return None
    if content_digest != digest:
        return None
    return {
        "skill_path": str(skill.path),
        "skill_name": skill.name,
        "skill_sha256": digest,
    }


def _read_skill_result(value: object) -> tuple[str, str, str, str] | None:
    if (
        not isinstance(value, dict)
        or ("isError" in value and value.get("isError") is not False)
    ):
        return None
    candidates: list[dict[str, object]] = []
    for key in ("structuredContent", "structured_content"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            candidates.append(candidate)
    content_blocks = value.get("content")
    if isinstance(content_blocks, list):
        for block in content_blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                candidate = json.loads(text)
            except (json.JSONDecodeError, ValueError, RecursionError):
                continue
            if isinstance(candidate, dict):
                candidates.append(candidate)
    receipts = {
        (
            candidate.get("content"),
            candidate.get("sha256"),
            candidate.get("path"),
            candidate.get("name"),
        )
        for candidate in candidates
        if set(candidate) == {"content", "sha256", "path", "name"}
        and isinstance(candidate.get("content"), str)
        and _validated_digest(candidate.get("sha256")) is not None
        and isinstance(candidate.get("path"), str)
        and _validated_skill_name(candidate.get("name")) is not None
    }
    if len(receipts) != 1:
        return None
    content, digest, path, name = receipts.pop()
    assert all(isinstance(value, str) for value in (content, digest, path, name))
    return content, digest, path, name


def _validated_persisted_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    try:
        return str(Path(value).resolve(strict=False))
    except (OSError, RuntimeError):
        return None


def _validated_skill_name(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or Path(value).name != value
    ):
        return None
    return value


def _validated_digest(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in value):
        return None
    return value
