"""Discover reviewed system actions from their canonical Markdown contract."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


SYSTEM_ACTION_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "system-action-contracts.md"
)


@dataclass(frozen=True)
class ActionContractEntry:
    capability: str
    operation: str


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _code_name(cell: str, *, field: str) -> str:
    value = cell.strip()
    if len(value) < 3 or not (value.startswith("`") and value.endswith("`")):
        raise ValueError(f"system action {field} must be an inline-code name")
    name = value[1:-1].strip()
    if not name:
        raise ValueError(f"system action {field} is empty")
    return name


def _code_names(cell: str, *, field: str) -> tuple[str, ...]:
    return tuple(_code_name(part, field=field) for part in cell.split(","))


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        cell and not (set(cell) - {"-", ":"}) for cell in cells
    )


def parse_action_contracts(document: str) -> tuple[ActionContractEntry, ...]:
    """Read executable capability/operation pairs from the canonical table."""
    lines = document.splitlines()
    table_start = None
    for index, line in enumerate(lines[:-1]):
        cells = _markdown_cells(line)
        if [cell.casefold() for cell in cells[:2]] == ["capability", "operation"]:
            separator = _markdown_cells(lines[index + 1])
            if len(separator) == len(cells) and _is_separator(separator):
                table_start = index + 2
                break
    if table_start is None:
        raise ValueError("system action contract table is missing")

    entries: list[ActionContractEntry] = []
    seen: set[tuple[str, str]] = set()
    for line in lines[table_start:]:
        cells = _markdown_cells(line)
        if not cells:
            break
        if len(cells) < 2:
            raise ValueError("system action contract row is incomplete")
        capability = _code_name(cells[0], field="capability")
        for operation in _code_names(cells[1], field="operation"):
            entry = ActionContractEntry(capability=capability, operation=operation)
            identity = (entry.capability, entry.operation)
            if identity in seen:
                raise ValueError(
                    "duplicate system action contract: "
                    f"{entry.capability}/{entry.operation}"
                )
            seen.add(identity)
            entries.append(entry)
    if not entries:
        raise ValueError("system action contract table has no actions")
    return tuple(entries)


def _contract_document() -> str:
    return SYSTEM_ACTION_CONTRACT_PATH.read_text(encoding="utf-8").strip()


def system_action_contract_document() -> str:
    """Canonical rule content also used for the conversation-contract fingerprint."""
    return _contract_document()


def action_contract_catalog(document: str | None = None) -> str:
    """Render the prompt-sized catalog from the canonical contract table."""
    entries = parse_action_contracts(document if document is not None else _contract_document())
    operations: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        operations[entry.capability].append(entry.operation)

    lines = [
        "## System Action Capability Catalog",
        "Consumer proposes these registered reviewed actions; Audit reviews them and system code executes approved actions.",
        "Before proposing or reviewing one, call `agent_cli.read_system_action_contract` with exact `capability` and `operation` values below. Omit `operation` to validate a capability and list its operations. The lookup returns the complete canonical contract, including shared role, action_identity, target, payload, and completion requirements.",
    ]
    lines.extend(
        f"- `{capability}`: " + ", ".join(f"`{operation}`" for operation in names)
        for capability, names in operations.items()
    )
    return "\n".join(lines)


def read_system_action_contract(
    capability: str,
    operation: str | None = None,
) -> dict[str, object]:
    """Return the complete canonical contract after exact name validation."""
    document = _contract_document()
    entries = parse_action_contracts(document)
    by_capability: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        by_capability[entry.capability].append(entry.operation)

    selected_capability = capability.strip() if isinstance(capability, str) else ""
    if selected_capability not in by_capability:
        raise ValueError(
            f"unknown system action capability: {selected_capability or capability}"
        )
    selected_operation = operation.strip() if isinstance(operation, str) else None
    if selected_operation == "":
        selected_operation = None
    if (
        selected_operation is not None
        and selected_operation not in by_capability[selected_capability]
    ):
        raise ValueError(
            "unknown system action operation for "
            f"{selected_capability}: {selected_operation}"
        )
    supported = (
        [selected_operation]
        if selected_operation is not None
        else list(by_capability[selected_capability])
    )
    return {
        "capability": selected_capability,
        "operation": selected_operation,
        "supported_operations": supported,
        "contract": document,
    }
