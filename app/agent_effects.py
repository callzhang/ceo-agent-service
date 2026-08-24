import json
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


from app.agent_result import EffectKind
from app.leak_check import is_sensitive_field_name
from app.native_cli_metadata import structured_target_identifiers


DEFAULT_MCP_EFFECTS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "mcp-tool-effects.json"
)
TOTAL_TIMEOUT_SECONDS = 1200
IDLE_TIMEOUT_SECONDS = 900
LEASE_SECONDS = TOTAL_TIMEOUT_SECONDS + IDLE_TIMEOUT_SECONDS + 300
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "password",
        "secret",
        "signature",
        "signedurl",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "apikey",
        "clientsecret",
        "privatekey",
        "webhook",
    }
)
_MAX_MCP_RESULT_DEPTH = 32
_MAX_MCP_RESULT_NODES = 2048
_MAX_MCP_RESULT_JSON_STRINGS = 64
_MAX_MCP_RESULT_JSON_BYTES = 256 * 1024


@dataclass(frozen=True)
class McpToolCall:
    server: str
    tool: str
    effect: EffectKind
    operation: str
    operation_digest: str
    target_identifiers: dict[str, str]
    native_cli: str = ""


class McpToolEffectRegistry:
    """Exact reviewed MCP capabilities; unknown server/tool pairs fail closed."""

    def __init__(
        self,
        effects: dict[tuple[str, str], EffectKind],
        *,
        dry_run_arguments: dict[tuple[str, str], str] | None = None,
        readbacks: dict[tuple[str, str], set[tuple[str, str]]] | None = None,
        readback_target_modes: dict[tuple[str, str, str, str], str] | None = None,
        readback_operation_modes: dict[tuple[str, str, str, str], str] | None = None,
        readback_operation_relations: dict[
            tuple[str, str, str, str], set[tuple[str, str]]
        ]
        | None = None,
        readback_operation_target_modes: dict[tuple[str, str, str, str, str, str], str]
        | None = None,
        readback_identity_matches: dict[
            tuple[str, str, str, str, str, str], tuple[dict[str, str], ...]
        ]
        | None = None,
        readback_result_constraints: dict[
            tuple[str, str, str, str, str, str], tuple[dict[str, str], ...]
        ]
        | None = None,
    ) -> None:
        self._effects = dict(effects)
        self._dry_run_arguments = dict(dry_run_arguments or {})
        self._readbacks = {
            key: frozenset(values) for key, values in (readbacks or {}).items()
        }
        self._readback_target_modes = dict(readback_target_modes or {})
        self._readback_operation_modes = dict(readback_operation_modes or {})
        self._readback_operation_relations = {
            key: frozenset(values)
            for key, values in (readback_operation_relations or {}).items()
        }
        self._readback_operation_target_modes = dict(
            readback_operation_target_modes or {}
        )
        self._readback_identity_matches = dict(readback_identity_matches or {})
        self._readback_result_constraints = dict(readback_result_constraints or {})

    @classmethod
    def from_path(cls, path: Path) -> "McpToolEffectRegistry":
        if not path.exists():
            return cls({})
        payload = json.loads(path.read_text(encoding="utf-8"))
        tools = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(tools, list):
            raise ValueError("MCP effect registry must contain a tools list")
        effects: dict[tuple[str, str], EffectKind] = {}
        dry_run_arguments: dict[tuple[str, str], str] = {}
        readbacks: dict[tuple[str, str], set[tuple[str, str]]] = {}
        readback_target_modes: dict[tuple[str, str, str, str], str] = {}
        readback_operation_modes: dict[tuple[str, str, str, str], str] = {}
        readback_operation_relations: dict[
            tuple[str, str, str, str], set[tuple[str, str]]
        ] = {}
        readback_operation_target_modes: dict[
            tuple[str, str, str, str, str, str], str
        ] = {}
        readback_identity_matches: dict[
            tuple[str, str, str, str, str, str], tuple[dict[str, str], ...]
        ] = {}
        readback_result_constraints: dict[
            tuple[str, str, str, str, str, str], tuple[dict[str, str], ...]
        ] = {}
        for item in tools:
            if not isinstance(item, dict):
                raise ValueError("MCP effect registry tools must be objects")
            server = item.get("server")
            tool = item.get("tool")
            effect = item.get("effect")
            if not isinstance(server, str) or not server.strip():
                raise ValueError("MCP effect registry server must be non-empty")
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("MCP effect registry tool must be non-empty")
            if effect not in {EffectKind.READ_ONLY.value, EffectKind.EFFECTFUL.value}:
                raise ValueError("MCP effect registry effect is invalid")
            key = (server.strip(), tool.strip())
            parsed_effect = EffectKind(effect)
            if key in effects and effects[key] is not parsed_effect:
                raise ValueError("MCP effect registry contains a conflicting tool")
            effects[key] = parsed_effect
            dry_run_argument = item.get("dry_run_argument")
            if dry_run_argument is not None:
                if (
                    parsed_effect is not EffectKind.EFFECTFUL
                    or not isinstance(dry_run_argument, str)
                    or not dry_run_argument.strip()
                ):
                    raise ValueError("MCP effect registry dry-run argument is invalid")
                dry_run_arguments[key] = dry_run_argument.strip()
            readback_for = item.get("readback_for", [])
            if not isinstance(readback_for, list):
                raise ValueError("MCP effect registry readback_for must be a list")
            for target in readback_for:
                if (
                    parsed_effect is not EffectKind.READ_ONLY
                    or not isinstance(target, dict)
                    or not isinstance(target.get("server"), str)
                    or not isinstance(target.get("tool"), str)
                ):
                    raise ValueError("MCP effect registry readback relation is invalid")
                readbacks.setdefault(key, set()).add(
                    (target["server"].strip(), target["tool"].strip())
                )
                target_match = target.get("target_match", "exact")
                if target_match not in {"exact", "shared"}:
                    raise ValueError("MCP readback target match is invalid")
                readback_target_modes[
                    relation_key := (
                        key[0],
                        key[1],
                        target["server"].strip(),
                        target["tool"].strip(),
                    )
                ] = target_match
                operation_match = target.get("operation_match", "outer")
                if operation_match not in {"outer", "registered"}:
                    raise ValueError("MCP readback operation match is invalid")
                readback_operation_modes[relation_key] = operation_match
                operation_relations = target.get("operation_relations", [])
                if not isinstance(operation_relations, list):
                    raise ValueError("MCP readback operation relations must be a list")
                for relation in operation_relations:
                    if (
                        not isinstance(relation, dict)
                        or not isinstance(relation.get("read"), str)
                        or not relation["read"].strip()
                        or not isinstance(relation.get("write"), str)
                        or not relation["write"].strip()
                    ):
                        raise ValueError("MCP readback operation relation is invalid")
                    readback_operation_relations.setdefault(relation_key, set()).add(
                        (relation["read"].strip(), relation["write"].strip())
                    )
                    relation_target_match = relation.get("target_match")
                    if relation_target_match is not None:
                        if relation_target_match not in {
                            "exact",
                            "shared",
                            "created_resource",
                        }:
                            raise ValueError(
                                "MCP readback operation target match is invalid"
                            )
                        readback_operation_target_modes[
                            (
                                *relation_key,
                                relation["read"].strip(),
                                relation["write"].strip(),
                            )
                        ] = relation_target_match
                    identity_matches = relation.get("identity_matches", [])
                    if not isinstance(identity_matches, list):
                        raise ValueError("MCP readback identity matches must be a list")
                    normalized_matches: list[dict[str, str]] = []
                    for identity in identity_matches:
                        if not isinstance(identity, dict):
                            raise ValueError("MCP readback identity match is invalid")
                        write_result = identity.get("write_result")
                        read_target = identity.get("read_target")
                        read_result = identity.get("read_result")
                        if (
                            not isinstance(write_result, str)
                            or not write_result.strip()
                            or not any(
                                isinstance(value, str) and value.strip()
                                for value in (read_target, read_result)
                            )
                            or any(
                                value is not None
                                and (not isinstance(value, str) or not value.strip())
                                for value in (read_target, read_result)
                            )
                        ):
                            raise ValueError("MCP readback identity match is invalid")
                        normalized = {"write_result": write_result.strip()}
                        if isinstance(read_target, str):
                            normalized["read_target"] = read_target.strip()
                        if isinstance(read_result, str):
                            normalized["read_result"] = read_result.strip()
                        normalized_matches.append(normalized)
                    if normalized_matches:
                        identity_key = (
                            *relation_key,
                            relation["read"].strip(),
                            relation["write"].strip(),
                        )
                        readback_identity_matches[identity_key] = tuple(
                            normalized_matches
                        )
                    result_constraints = relation.get("read_result_constraints", [])
                    if not isinstance(result_constraints, list):
                        raise ValueError(
                            "MCP readback result constraints must be a list"
                        )
                    normalized_constraints: list[dict[str, str]] = []
                    for constraint in result_constraints:
                        if (
                            not isinstance(constraint, dict)
                            or set(constraint) != {"path", "equals", "comparison"}
                            or not isinstance(constraint.get("path"), str)
                            or not constraint["path"].strip()
                            or not isinstance(constraint.get("equals"), str)
                            or not isinstance(constraint.get("comparison"), str)
                            or constraint["comparison"] not in {"exact", "casefold"}
                        ):
                            raise ValueError(
                                "MCP readback result constraint is invalid"
                            )
                        normalized_constraints.append(
                            {
                                "path": constraint["path"].strip(),
                                "equals": constraint["equals"],
                                "comparison": constraint["comparison"],
                            }
                        )
                    if normalized_constraints:
                        constraint_key = (
                            *relation_key,
                            relation["read"].strip(),
                            relation["write"].strip(),
                        )
                        readback_result_constraints[constraint_key] = tuple(
                            normalized_constraints
                        )
                if operation_match == "registered" and not operation_relations:
                    raise ValueError(
                        "registered MCP readback requires operation relations"
                    )
        for read_key, write_keys in readbacks.items():
            if any(
                effects.get(write_key) is not EffectKind.EFFECTFUL
                for write_key in write_keys
            ):
                raise ValueError("MCP readback target must be effectful")
            if effects.get(read_key) is not EffectKind.READ_ONLY:
                raise ValueError("MCP readback source must be read-only")
        return cls(
            effects,
            dry_run_arguments=dry_run_arguments,
            readbacks=readbacks,
            readback_target_modes=readback_target_modes,
            readback_operation_modes=readback_operation_modes,
            readback_operation_relations=readback_operation_relations,
            readback_operation_target_modes=readback_operation_target_modes,
            readback_identity_matches=readback_identity_matches,
            readback_result_constraints=readback_result_constraints,
        )

    @classmethod
    def default(cls) -> "McpToolEffectRegistry":
        configured = os.environ.get("CEO_AGENT_MCP_EFFECTS_PATH", "").strip()
        return cls.from_path(
            Path(configured) if configured else DEFAULT_MCP_EFFECTS_PATH
        )

    def classify(self, item: dict[str, object]) -> McpToolCall | None:
        if item.get("type") != "mcp_tool_call":
            return None
        server = item.get("server")
        tool = item.get("tool")
        if not isinstance(server, str) or not isinstance(tool, str):
            return None
        effect = self._effects.get((server, tool))
        if effect is None:
            return None
        arguments = item.get("arguments")
        dry_run_argument = self._dry_run_arguments.get((server, tool))
        if (
            effect is EffectKind.EFFECTFUL
            and dry_run_argument
            and isinstance(arguments, dict)
            and arguments.get(dry_run_argument) is True
        ):
            effect = EffectKind.READ_ONLY
        canonical = json.dumps(
            {"server": server, "tool": tool, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return McpToolCall(
            server=server,
            tool=tool,
            effect=effect,
            operation=tool,
            operation_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            target_identifiers=structured_target_identifiers(arguments),
        )

    def reviewed_read_tools(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for (server, tool), effect in self._effects.items():
            if effect is EffectKind.READ_ONLY:
                grouped.setdefault(server, []).append(tool)
        return {server: tuple(sorted(tools)) for server, tools in grouped.items()}

    def reviewed_tools(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for server, tool in self._effects:
            grouped.setdefault(server, []).append(tool)
        return {server: tuple(sorted(tools)) for server, tools in grouped.items()}

    def is_registered_write_operation(self, operation: str) -> bool:
        """Return whether a controlled CLI write has a configured readback relation."""
        return any(
            write_operation == operation
            for relations in self._readback_operation_relations.values()
            for _, write_operation in relations
        )

    def can_readback(
        self,
        *,
        read_server: str,
        read_tool: str,
        write_server: str,
        write_tool: str,
    ) -> bool:
        return (write_server, write_tool) in self._readbacks.get(
            (read_server, read_tool), ()
        )

    def has_readback_for(
        self,
        *,
        write_server: str,
        write_tool: str,
        write_operation: str = "",
    ) -> bool:
        write = (write_server, write_tool)
        relation_keys = [
            (*read, *write)
            for read, targets in self._readbacks.items()
            if write in targets
        ]
        # A configured relation establishes the readback requirement. Registered
        # operations constrain which read can satisfy it; an unknown inner write
        # must not turn a controlled wrapper into a no-readback effect.
        return bool(relation_keys)

    def has_registered_readback_for(
        self,
        *,
        write_server: str,
        write_tool: str,
        write_operation: str,
    ) -> bool:
        """Return whether this concrete inner operation has a readback relation."""
        write = (write_server, write_tool)
        relation_keys = [
            (*read, *write)
            for read, targets in self._readbacks.items()
            if write in targets
        ]
        if not relation_keys:
            return False
        return any(
            self._readback_operation_modes.get(relation_key, "outer") == "outer"
            or any(
                write_operation == registered_write
                for _read_operation, registered_write in self._readback_operation_relations.get(
                    relation_key, ()
                )
            )
            for relation_key in relation_keys
        )

    def readback_requires_completed_write(
        self,
        *,
        write_server: str,
        write_tool: str,
        write_operation: str,
    ) -> bool:
        return any(
            mode == "created_resource"
            and key[2:4] == (write_server, write_tool)
            and key[5] == write_operation
            for key, mode in self._readback_operation_target_modes.items()
        )

    def readback_targets_match(
        self,
        *,
        read_server: str,
        read_tool: str,
        write_server: str,
        write_tool: str,
        read_targets: dict[str, object],
        write_targets: dict[str, object],
        read_operation: str = "",
        write_operation: str = "",
    ) -> bool:
        relation_key = (read_server, read_tool, write_server, write_tool)
        mode = self._readback_operation_target_modes.get(
            (*relation_key, read_operation, write_operation),
            self._readback_target_modes.get(relation_key, "exact"),
        )
        if mode == "created_resource":
            # Creation operations may not have a resource ID until the write
            # returns. A subsequent reviewed read can safely bind that newly
            # returned ID to the completed write lifecycle.
            return not write_targets and bool(read_targets)
        if mode == "exact":
            return bool(read_targets) and read_targets == write_targets
        normalized_read_targets = _normalized_shared_targets(read_targets)
        normalized_write_targets = _normalized_shared_targets(write_targets)
        if normalized_read_targets is None or normalized_write_targets is None:
            return False
        shared_keys = normalized_read_targets.keys() & normalized_write_targets.keys()
        return bool(shared_keys) and all(
            normalized_read_targets[key] == normalized_write_targets[key]
            for key in shared_keys
        )

    def readback_operations_match(
        self,
        *,
        read_server: str,
        read_tool: str,
        write_server: str,
        write_tool: str,
        read_operation: str,
        write_operation: str,
    ) -> bool:
        relation_key = (read_server, read_tool, write_server, write_tool)
        if self._readback_operation_modes.get(relation_key, "outer") == "outer":
            return True
        return (
            read_operation,
            write_operation,
        ) in self._readback_operation_relations.get(
            relation_key,
            (),
        )

    def result_identifiers(
        self,
        *,
        server: str,
        tool: str,
        operation: str,
        result: object,
    ) -> dict[str, str]:
        paths: set[str] = set()
        for key, matches in self._readback_identity_matches.items():
            (
                read_server,
                read_tool,
                write_server,
                write_tool,
                read_operation,
                write_operation,
            ) = key
            if (server, tool, operation) == (read_server, read_tool, read_operation):
                paths.update(
                    match["read_result"] for match in matches if "read_result" in match
                )
            if (server, tool, operation) == (write_server, write_tool, write_operation):
                paths.update(match["write_result"] for match in matches)
        for key, constraints in self._readback_result_constraints.items():
            (
                read_server,
                read_tool,
                _write_server,
                _write_tool,
                read_operation,
                _write_operation,
            ) = key
            if (server, tool, operation) == (read_server, read_tool, read_operation):
                paths.update(constraint["path"] for constraint in constraints)
        return {
            path: value
            for path in paths
            if isinstance((value := _json_path_string(result, path)), str)
        }

    def readback_identities_match(
        self,
        *,
        read_server: str,
        read_tool: str,
        write_server: str,
        write_tool: str,
        read_operation: str,
        write_operation: str,
        read_targets: dict[str, object],
        read_result_identifiers: dict[str, object],
        write_result_identifiers: dict[str, object],
    ) -> bool:
        matches = self._readback_identity_matches.get(
            (
                read_server,
                read_tool,
                write_server,
                write_tool,
                read_operation,
                write_operation,
            ),
            (),
        )
        for match in matches:
            write_value = write_result_identifiers.get(match["write_result"])
            if not isinstance(write_value, str) or not write_value:
                return False
            read_target = match.get("read_target")
            if (
                read_target is not None
                and _target_value(read_targets, read_target) != write_value
            ):
                return False
            read_result = match.get("read_result")
            if (
                read_result is not None
                and read_result_identifiers.get(read_result) != write_value
            ):
                return False
        constraints = self._readback_result_constraints.get(
            (
                read_server,
                read_tool,
                write_server,
                write_tool,
                read_operation,
                write_operation,
            ),
            (),
        )
        if any(
            not _result_constraint_matches(
                read_result_identifiers.get(constraint["path"]),
                constraint["equals"],
                constraint["comparison"],
            )
            for constraint in constraints
        ):
            return False
        return True


def _json_path_string(value: object, path: str) -> str | None:
    current = value
    for part in path.split("."):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except (json.JSONDecodeError, ValueError, TypeError):
                return None
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) and current else None


def _target_value(targets: dict[str, object], expected_key: str) -> object:
    normalized_expected = expected_key.replace("_", "-").casefold()
    for key, value in targets.items():
        if key.replace("_", "-").casefold() == normalized_expected:
            return value
    return None


def _result_constraint_matches(
    actual: object,
    expected: str,
    comparison: str,
) -> bool:
    if not isinstance(actual, str):
        return False
    if comparison == "casefold":
        return actual.casefold() == expected.casefold()
    return actual == expected


def _normalized_shared_targets(
    targets: dict[str, object],
) -> dict[str, object] | None:
    normalized: dict[str, object] = {}
    conversation_keys = {
        "chat-id",
        "conversation",
        "conversation-id",
        "group",
        "open-conversation-id",
    }
    mail_keys = {"email", "from"}
    for key, value in targets.items():
        normalized_key = key.replace("_", "-").casefold()
        namespace = (
            "conversation"
            if normalized_key in conversation_keys
            else "email"
            if normalized_key in mail_keys
            else normalized_key
        )
        if namespace in normalized and normalized[namespace] != value:
            return None
        normalized[namespace] = value
    return normalized


def _controlled_cli_receipt(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or value.get("isError") is True:
        return None
    candidates = [value]
    structured = value.get("structuredContent") or value.get("structured_content")
    if isinstance(structured, dict):
        candidates.append(structured)
    for candidate in candidates:
        if not isinstance(candidate.get("result_digest"), str):
            continue
        if not isinstance(candidate.get("operation_digest"), str):
            continue
        if not isinstance(candidate.get("operation"), str):
            continue
        if not isinstance(candidate.get("target_identifiers"), dict):
            continue
        return candidate
    return None


def _mcp_call_completed(payload: dict[str, object]) -> bool:
    if payload.get("type") != "item.completed":
        return False
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("status") != "completed":
        return False
    result = item.get("result")
    return _mcp_result_explicitly_succeeded(result)


def _mcp_result_explicitly_succeeded(value: object) -> bool:
    """Accept only a valid top-level MCP CallToolResult without error evidence."""
    decoded_strings = 0
    decoded_bytes = 0
    if isinstance(value, str):
        if len(value) > _MAX_MCP_RESULT_JSON_BYTES:
            return False
        try:
            encoded_size = len(value.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return False
        if encoded_size > _MAX_MCP_RESULT_JSON_BYTES:
            return False
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return False
        decoded_strings = 1
        decoded_bytes = encoded_size
    if not isinstance(value, dict) or not value:
        return False

    if "content" not in value:
        return False
    content = value["content"]
    if not isinstance(content, list) or not all(
        _valid_mcp_content_block(block) for block in content
    ):
        return False

    if "isError" in value:
        flag = value["isError"]
        if not isinstance(flag, bool) or flag:
            return False

    structured_keys = ("structured_content", "structuredContent")
    for key in structured_keys:
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            return False

    stack: list[tuple[object, int, bool, bool]] = [(value, 0, True, False)]
    node_count = 0
    while stack:
        current, depth, inspect_errors, decode_json_strings = stack.pop()
        node_count += 1
        if node_count > _MAX_MCP_RESULT_NODES or depth > _MAX_MCP_RESULT_DEPTH:
            return False

        if isinstance(current, dict):
            if len(current) > _MAX_MCP_RESULT_NODES - node_count - len(stack):
                return False
            if inspect_errors and _mcp_mapping_has_error(current):
                return False
            for key, nested in current.items():
                if depth == 0:
                    child_errors = key in {"result", *structured_keys}
                    child_decode = child_errors
                else:
                    child_errors = inspect_errors
                    child_decode = decode_json_strings
                stack.append((nested, depth + 1, child_errors, child_decode))
            continue
        if isinstance(current, list):
            if len(current) > _MAX_MCP_RESULT_NODES - node_count - len(stack):
                return False
            for nested in current:
                stack.append((nested, depth + 1, inspect_errors, decode_json_strings))
            continue
        if not decode_json_strings or not isinstance(current, str):
            continue

        stripped = current.lstrip()
        if not stripped.startswith(("{", "[")):
            continue
        remaining_bytes = _MAX_MCP_RESULT_JSON_BYTES - decoded_bytes
        if len(current) > remaining_bytes:
            return False
        try:
            encoded_size = len(current.encode("utf-8"))
        except (UnicodeError, MemoryError):
            return False
        if (
            decoded_strings >= _MAX_MCP_RESULT_JSON_STRINGS
            or encoded_size > remaining_bytes
        ):
            return False
        try:
            decoded = json.loads(current)
        except (json.JSONDecodeError, ValueError, RecursionError, MemoryError):
            return False
        if not isinstance(decoded, (dict, list)):
            continue
        decoded_strings += 1
        decoded_bytes += encoded_size
        stack.append((decoded, depth + 1, inspect_errors, True))

    return True


def _mcp_mapping_has_error(value: dict[str, object]) -> bool:
    if "isError" in value:
        flag = value["isError"]
        if not isinstance(flag, bool) or flag:
            return True
    for key, nested in value.items():
        normalized_key = key.replace("_", "").lower()
        if normalized_key == "error" and nested not in (None, False, ""):
            return True
        if normalized_key in {"errorcode", "errcode"} and nested not in (
            None,
            False,
            0,
            "",
            "0",
        ):
            return True
    return False


def _valid_mcp_content_block(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    block_type = value.get("type")
    if block_type == "text":
        return isinstance(value.get("text"), str)
    if block_type in {"image", "audio"}:
        mime_type = value.get("mimeType", value.get("mime_type"))
        return isinstance(value.get("data"), str) and isinstance(mime_type, str)
    if block_type == "resource_link":
        return isinstance(value.get("name"), str) and isinstance(value.get("uri"), str)
    if block_type != "resource":
        return False
    resource = value.get("resource")
    if not isinstance(resource, dict) or not isinstance(resource.get("uri"), str):
        return False
    return isinstance(resource.get("text"), str) or isinstance(
        resource.get("blob"), str
    )


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _is_sensitive_key(normalized_key: str) -> bool:
    if normalized_key in _SENSITIVE_KEY_NAMES:
        return True
    if normalized_key.startswith("x") and normalized_key[1:] in _SENSITIVE_KEY_NAMES:
        return True
    return is_sensitive_field_name(normalized_key)


def _is_signed_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.query:
        return False
    return any(
        _is_sensitive_key(_normalized_key(name))
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True)
    )
