from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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


def loaded_skill_receipts(
    events: Iterable[dict[str, object]],
) -> tuple[LoadedSkillReceipt, ...]:
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
) -> dict[str, str] | None:
    if not isinstance(arguments, dict) or set(arguments) != {"path"}:
        return None
    requested = arguments.get("path")
    if not isinstance(requested, str) or not requested:
        return None
    skill = _authorized_skill(requested)
    if skill is None:
        return None
    path, name = skill
    receipt = _read_skill_result(result)
    if receipt is None:
        return None
    content, digest = receipt
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
        return None
    return {
        "skill_path": path,
        "skill_name": name,
        "skill_sha256": digest,
    }


def _authorized_skill(requested: str) -> tuple[str, str] | None:
    try:
        path = Path(requested)
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if requested != str(resolved) or resolved.suffix.casefold() != ".md":
        return None
    for configured_root in AGENT_SKILL_ROOTS:
        try:
            root = configured_root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_relative_to(root):
            continue
        parent = resolved.parent
        while parent != root:
            if (parent / "SKILL.md").is_file():
                return str(resolved), parent.name
            parent = parent.parent
    return None


def _read_skill_result(value: object) -> tuple[str, str] | None:
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
        (candidate.get("content"), candidate.get("sha256"))
        for candidate in candidates
        if set(candidate) == {"content", "sha256"}
        and isinstance(candidate.get("content"), str)
        and _validated_digest(candidate.get("sha256")) is not None
    }
    if len(receipts) != 1:
        return None
    content, digest = receipts.pop()
    assert isinstance(content, str) and isinstance(digest, str)
    return content, digest


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
