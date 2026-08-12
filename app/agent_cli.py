from __future__ import annotations

import hashlib
import errno
import json
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.etree import ElementTree

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.agent_result import EffectKind
from app.agent_skill_usage import resolve_authorized_skill_path
from app.bounded_process import (
    MAX_PROCESS_OUTPUT_BYTES,
    ProcessOutputLimitError,
    run_bounded_process,
)
from app.channel_gate import classify_cli_read_failure, classify_cli_write_failure
from app.leak_check import is_sensitive_field_name
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
    has_noninteractive_confirmation,
    prepare_material_output_root,
)

MAX_CLI_OUTPUT_BYTES = MAX_PROCESS_OUTPUT_BYTES
MAX_SKILL_BYTES = 256 * 1024
MAX_TEXT_MATERIAL_BYTES = 512 * 1024
MAX_SPREADSHEET_BYTES = 20 * 1024 * 1024
MAX_SPREADSHEET_ROWS = 200
MAX_SPREADSHEET_COLUMNS = 64
MAX_SPREADSHEET_PREVIEW_CHARS = 128 * 1024
CLI_TIMEOUT_SECONDS = 15 * 60
RECOVERY_WRITE_ALLOWLIST_ENV = "CEO_AGENT_RECOVERY_WRITE_ALLOWLIST"
CliOutputLimitError = ProcessOutputLimitError
SPREADSHEET_MATERIAL_ROOTS = (
    Path("/tmp").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)
TEXT_MATERIAL_ROOTS = SPREADSHEET_MATERIAL_ROOTS


def _process_failure_receipt(
    command, *, code: str, retryable: bool
) -> dict[str, object]:
    return {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(b"").hexdigest(),
        "stdout": "",
        "error": {
            "channel": command.cli,
            "code": code,
            "retryable": retryable,
            "gate_state": "unavailable",
        },
    }


def execute_reviewed_read(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    prepare_material_output_root()
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.READ_ONLY,
        classifier=classifier,
        process_runner=process_runner,
    )


def execute_reviewed_write(
    argv: Sequence[str],
    *,
    authorization_id: str | None = None,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.EFFECTFUL,
        authorization_id=authorization_id,
        classifier=classifier,
        process_runner=process_runner,
    )

def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recovery_write_authorization(
    command,
    argv: Sequence[str],
    *,
    authorization_id: str | None,
) -> dict[str, object] | None:
    raw_allowlist = os.environ.get(RECOVERY_WRITE_ALLOWLIST_ENV, "")
    if not raw_allowlist:
        return None
    try:
        allowlist = json.loads(raw_allowlist)
    except json.JSONDecodeError as exc:
        raise AgentReadOnlyViolationError("recovery_write_allowlist_invalid") from exc
    if not isinstance(allowlist, list) or not isinstance(authorization_id, str):
        raise AgentReadOnlyViolationError("recovery_write_not_authorized")
    actual = {
        "authorization_id": authorization_id,
        "capability": f"agent_cli.{command.cli}",
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "arguments_digest": _json_digest({"argv": list(argv)}),
    }
    match = next(
        (
            entry
            for entry in allowlist
            if isinstance(entry, dict)
            and all(entry.get(key) == value for key, value in actual.items())
            and isinstance(entry.get("action_index"), int)
        ),
        None,
    )
    if match is None:
        raise AgentReadOnlyViolationError("recovery_write_not_authorized")
    return match


def read_skill(path: str) -> dict[str, str]:
    authorized = resolve_authorized_skill_path(path)
    skill_path = authorized.path
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(skill_path, flags), "rb") as skill_file:
        file_stat = os.fstat(skill_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise AgentReadOnlyViolationError("skill_file_not_regular")
        if file_stat.st_size > MAX_SKILL_BYTES:
            raise AgentReadOnlyViolationError("skill_content_too_large")
        content_bytes = skill_file.read(MAX_SKILL_BYTES + 1)
        if len(content_bytes) > MAX_SKILL_BYTES:
            raise AgentReadOnlyViolationError("skill_content_too_large")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentReadOnlyViolationError("skill_content_invalid_utf8") from exc
    digest = hashlib.sha256(content_bytes).hexdigest()
    return {
        "content": content,
        "sha256": digest,
        "path": str(skill_path),
        "name": authorized.name,
    }


def read_spreadsheet(
    path: str,
    *,
    max_rows: int = MAX_SPREADSHEET_ROWS,
    max_columns: int = MAX_SPREADSHEET_COLUMNS,
) -> dict[str, object]:
    """Read a downloaded xlsx workbook without granting an Agent shell access."""
    if not 1 <= max_rows <= MAX_SPREADSHEET_ROWS:
        raise AgentReadOnlyViolationError("spreadsheet_row_limit_invalid")
    if not 1 <= max_columns <= MAX_SPREADSHEET_COLUMNS:
        raise AgentReadOnlyViolationError("spreadsheet_column_limit_invalid")
    material_path = Path(path).expanduser().resolve(strict=True)
    if material_path.suffix.casefold() not in {".xlsx", ".xlsm"}:
        raise AgentReadOnlyViolationError("spreadsheet_format_unsupported")
    if not any(material_path.is_relative_to(root) for root in SPREADSHEET_MATERIAL_ROOTS):
        raise AgentReadOnlyViolationError("spreadsheet_path_forbidden")
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(material_path, flags), "rb") as material_file:
        file_stat = os.fstat(material_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise AgentReadOnlyViolationError("spreadsheet_file_not_regular")
        if file_stat.st_size > MAX_SPREADSHEET_BYTES:
            raise AgentReadOnlyViolationError("spreadsheet_file_too_large")
        try:
            with zipfile.ZipFile(material_file) as workbook:
                return _read_xlsx_workbook(
                    workbook,
                    max_rows=max_rows,
                    max_columns=max_columns,
                )
        except zipfile.BadZipFile as exc:
            raise AgentReadOnlyViolationError("spreadsheet_file_invalid") from exc
        except ElementTree.ParseError as exc:
            raise AgentReadOnlyViolationError("spreadsheet_xml_invalid") from exc


def read_text_file(path: str) -> dict[str, object]:
    """Read one bounded UTF-8 material file without granting shell access."""
    material_path = Path(path).expanduser().resolve(strict=True)
    if not any(material_path.is_relative_to(root) for root in TEXT_MATERIAL_ROOTS):
        raise AgentReadOnlyViolationError("text_material_path_forbidden")
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(material_path, flags), "rb") as material_file:
        file_stat = os.fstat(material_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise AgentReadOnlyViolationError("text_material_file_not_regular")
        if file_stat.st_size > MAX_TEXT_MATERIAL_BYTES:
            raise AgentReadOnlyViolationError("text_material_file_too_large")
        content_bytes = material_file.read(MAX_TEXT_MATERIAL_BYTES + 1)
    if len(content_bytes) > MAX_TEXT_MATERIAL_BYTES:
        raise AgentReadOnlyViolationError("text_material_file_too_large")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentReadOnlyViolationError("text_material_invalid_utf8") from exc
    return {
        "content": content,
        "path": str(material_path),
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
    }


def _read_xlsx_workbook(
    workbook: zipfile.ZipFile,
    *,
    max_rows: int,
    max_columns: int,
) -> dict[str, object]:
    shared_strings = _xlsx_shared_strings(workbook)
    sheets = _xlsx_sheet_parts(workbook)
    previews: list[dict[str, object]] = []
    remaining_chars = MAX_SPREADSHEET_PREVIEW_CHARS
    for name, part in sheets:
        preview, remaining_chars = _xlsx_sheet_preview(
            workbook,
            name=name,
            part=part,
            shared_strings=shared_strings,
            max_rows=max_rows,
            max_columns=max_columns,
            remaining_chars=remaining_chars,
        )
        previews.append(preview)
        if remaining_chars == 0:
            break
    return {"format": "xlsx", "sheets": previews}


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        for item in root
        if item.tag.endswith("}si")
    ]


def _xlsx_sheet_parts(workbook: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
    relationships_root = ElementTree.fromstring(
        workbook.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        relation.attrib.get("Id", ""): relation.attrib.get("Target", "")
        for relation in relationships_root
        if relation.tag.endswith("}Relationship")
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook_root.iter():
        if not sheet.tag.endswith("}sheet"):
            continue
        relationship_id = next(
            (value for key, value in sheet.attrib.items() if key.endswith("}id")),
            "",
        )
        target = targets.get(relationship_id, "")
        if target:
            sheets.append((sheet.attrib.get("name", "Sheet"), f"xl/{target}"))
    if sheets:
        return sheets
    return [
        (Path(name).stem, name)
        for name in sorted(workbook.namelist())
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    ]


def _xlsx_sheet_preview(
    workbook: zipfile.ZipFile,
    *,
    name: str,
    part: str,
    shared_strings: list[str],
    max_rows: int,
    max_columns: int,
    remaining_chars: int,
) -> tuple[dict[str, object], int]:
    root = ElementTree.fromstring(workbook.read(part))
    rows: list[dict[str, object]] = []
    truncated = False
    for row in (node for node in root.iter() if node.tag.endswith("}row")):
        if len(rows) >= max_rows:
            truncated = True
            break
        cells: dict[str, str] = {}
        for cell in (node for node in row if node.tag.endswith("}c")):
            reference = cell.attrib.get("r", "")
            column = "".join(character for character in reference if character.isalpha())
            if not column or _xlsx_column_number(column) > max_columns:
                continue
            value = _xlsx_cell_value(cell, shared_strings)
            if value:
                value = value[:remaining_chars]
                cells[column] = value
                remaining_chars -= len(value)
                if remaining_chars == 0:
                    truncated = True
                    break
        if cells:
            rows.append({"row": int(row.attrib.get("r", len(rows) + 1)), "cells": cells})
        if truncated:
            break
    return {"name": name, "rows": rows, "truncated": truncated}, remaining_chars


def _xlsx_column_number(column: str) -> int:
    result = 0
    for character in column.upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    text = "".join(
        node.text or "" for node in cell.iter() if node.tag.endswith("}t")
    )
    if text:
        return text
    raw_value = next(
        (node.text or "" for node in cell if node.tag.endswith("}v")),
        "",
    )
    if cell_type == "s" and raw_value.isdigit():
        index = int(raw_value)
        return shared_strings[index] if index < len(shared_strings) else ""
    return raw_value


def _execute_reviewed(
    argv: Sequence[str],
    *,
    expected_effect: EffectKind,
    classifier: NativeCliMetadataClassifier | None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None,
    authorization_id: str | None = None,
) -> dict[str, object]:
    if not argv:
        raise AgentReadOnlyViolationError("agent_cli_command_invalid")
    if any(
        argument.startswith("--")
        and is_sensitive_field_name(argument[2:].partition("=")[0])
        for argument in argv
    ):
        raise AgentReadOnlyViolationError("agent_cli_sensitive_argument")
    reviewed = classifier or NativeCliMetadataClassifier()
    item = {"type": "command_execution", "argv": list(argv)}
    descriptor = describe_native_command(item)
    if descriptor is None or descriptor.cli == "local-shell":
        command = reviewed.classify(item)
    else:
        try:
            reviewed.prewarm()
            command = reviewed.classify(item)
        except NativeCliMetadataUnavailableError as exc:
            return _process_failure_receipt(
                descriptor,
                code=exc.code,
                retryable=exc.retryable,
            )
    if command is None:
        raise AgentReadOnlyViolationError("agent_cli_command_unreviewed")
    if command.effect is not expected_effect:
        raise AgentReadOnlyViolationError("reviewed_cli_effect_mismatch")
    if (
        expected_effect is EffectKind.EFFECTFUL
        and command.cli == "dws"
        and not has_noninteractive_confirmation(tuple(argv))
    ):
        raise AgentReadOnlyViolationError("agent_cli_confirmation_required")
    authorization = None
    if expected_effect is EffectKind.EFFECTFUL:
        authorization = _recovery_write_authorization(
            command,
            argv,
            authorization_id=authorization_id,
        )
    executable_name = argv[0] if command.cli == "local-shell" else command.cli
    executable = shutil.which(executable_name)
    if executable is None:
        return _process_failure_receipt(
            command,
            code="agent_cli_start_unavailable",
            retryable=True,
        )
    reviewed_argv = [executable, *argv[1:]]
    try:
        process = (
            run_bounded_process(reviewed_argv, timeout=CLI_TIMEOUT_SECONDS)
            if process_runner is None
            else process_runner(
                reviewed_argv,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
            )
        )
        if (
            len(process.stdout.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
            or len(process.stderr.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES
        ):
            raise CliOutputLimitError(stdout_bytes=0, stderr_bytes=0)
    except CliOutputLimitError:
        return {
            "cli": command.cli,
            "operation": command.command_path,
            "operation_digest": command.command_digest,
            "target_identifiers": command.target_identifiers,
            "result_digest": hashlib.sha256(b"").hexdigest(),
            "stdout": "",
            "error": {
                "channel": command.cli,
                "code": "agent_cli_output_limit_exceeded",
                "retryable": False,
                "gate_state": "blocked",
            },
        }
    except subprocess.TimeoutExpired:
        return _process_failure_receipt(
            command,
            code="agent_cli_timeout",
            retryable=True,
        )
    except OSError as exc:
        retryable = exc.errno not in {errno.EINVAL, errno.EACCES, errno.ENOEXEC}
        return _process_failure_receipt(
            command,
            code=(
                "agent_cli_start_unavailable"
                if retryable
                else "agent_cli_start_invalid"
            ),
            retryable=retryable,
        )
    receipt: dict[str, object] = {
        "cli": command.cli,
        "operation": command.command_path,
        "operation_digest": command.command_digest,
        "target_identifiers": command.target_identifiers,
        "result_digest": hashlib.sha256(process.stdout.encode("utf-8")).hexdigest(),
        "stdout": process.stdout,
    }
    if authorization is not None:
        receipt["authorization_id"] = authorization["authorization_id"]
        receipt["action_index"] = authorization["action_index"]
    if process.returncode != 0:
        failure = (
            classify_cli_read_failure(command.cli, process)
            if expected_effect is EffectKind.READ_ONLY
            else classify_cli_write_failure(command.cli, process)
        )
        receipt["error"] = {
            "channel": failure.channel,
            "code": failure.code,
            "retryable": failure.retryable,
            "gate_state": failure.gate_state.value,
            "detail": failure.detail,
        }
    return receipt


server = FastMCP(
    "agent_cli",
    instructions=(
        "Read installed Agent skills and local materials with dedicated readers, "
        "and run DWS or Lark commands only after reviewing effect metadata."
    ),
)


@server.tool(
    name="read_skill",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def read_skill_tool(path: str) -> dict[str, str]:
    """Read an installed Agent skill or its referenced Markdown safely.

    Use this before following product-specific DWS procedures. The path must
    identify a Markdown file inside an installed skill directory.
    """
    return read_skill(path)


@server.tool(
    name="read_text_file",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def read_text_file_tool(path: str) -> dict[str, object]:
    """Read a downloaded UTF-8 text material from the service temp directory."""
    return read_text_file(path)


@server.tool(
    name="read_spreadsheet",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
def read_spreadsheet_tool(
    path: str,
    max_rows: int = MAX_SPREADSHEET_ROWS,
    max_columns: int = MAX_SPREADSHEET_COLUMNS,
) -> dict[str, object]:
    """Read a bounded preview of a downloaded xlsx workbook without shell access."""
    return read_spreadsheet(path, max_rows=max_rows, max_columns=max_columns)


@server.tool(
    name="execute_reviewed_read",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def execute_reviewed_read_tool(argv: list[str]) -> dict[str, object]:
    """Run one reviewed DWS, Lark, or fixed service read and return a receipt.

    Use the provided argv for live enterprise evidence such as a message,
    calendar event, document, file, approval, person, mail, or meeting. DWS
    and Lark use published effect metadata. Arbitrary local executables are
    forbidden; use the dedicated text and spreadsheet readers for local files.
    """
    return execute_reviewed_read(argv)


@server.tool(
    name="execute_reviewed_write",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
def execute_reviewed_write_tool(
    argv: list[str], authorization_id: str | None = None
) -> dict[str, object]:
    """Execute an Audit-approved external write and return its receipt.

    This tool accepts only reviewed commands with a matching authorization.
    Consumer Agents must describe writes as proposal data and never call it.
    """
    return execute_reviewed_write(argv, authorization_id=authorization_id)


if __name__ == "__main__":
    server.run(transport="stdio")
