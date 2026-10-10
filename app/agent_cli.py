from __future__ import annotations

import asyncio
import argparse
import hashlib
import errno
import importlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.action_contract_catalog import read_system_action_contract as load_system_action_contract
from app.agent_result import EffectKind
from app.agent_effects import McpToolEffectRegistry
from app.agent_skill_usage import resolve_authorized_skill_path
from app.bounded_process import (
    ProcessOutputLimitError,
    run_bounded_process,
)
from app.channel_gate import classify_cli_read_failure, classify_cli_write_failure
from app.config import feedback_spike_vercel_base_url
from app.feedback_spike import sanitize_configured_feedback_links
from app.leak_check import contains_credential, is_sensitive_field_name
from app.native_cli_metadata import (
    AgentReadOnlyViolationError,
    NativeCliCommand,
    NativeCliMetadataClassifier,
    NativeCliMetadataUnavailableError,
    describe_native_command,
    has_noninteractive_confirmation,
    prepare_material_output_root,
)

# Codex truncates one MCP text block at 1 MiB. Keep command output below that
# boundary even after JSON escaping so the controlled receipt remains valid.
MAX_CLI_OUTPUT_BYTES = 128 * 1024
MAX_SKILL_BYTES = 256 * 1024
MAX_TEXT_MATERIAL_BYTES = 512 * 1024
MAX_SPREADSHEET_BYTES = 20 * 1024 * 1024
MAX_SPREADSHEET_ROWS = 200
MAX_SPREADSHEET_COLUMNS = 64
MAX_SPREADSHEET_PREVIEW_CHARS = 128 * 1024
MAX_PDF_PAGES = 100
CLI_TIMEOUT_SECONDS = 15 * 60
CliOutputLimitError = ProcessOutputLimitError
SPREADSHEET_MATERIAL_ROOTS = (
    Path("/tmp").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)
TEXT_MATERIAL_ROOTS = SPREADSHEET_MATERIAL_ROOTS
MAX_TASK_FILE_BYTES = MAX_CLI_OUTPUT_BYTES
_TASK_ARTIFACT_TEMP_PREFIX = ".task-artifact-"


@dataclass(frozen=True, slots=True)
class ReviewedWriteAuthorization:
    authorization_id: str
    action_index: int
    capability: str
    operation: str
    operation_digest: str
    target_identifiers: tuple[str, ...]
    arguments_digest: str


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
    action_index: int | None = None,
    authorization: ReviewedWriteAuthorization | None = None,
    authorization_consumer: Callable[[object], object] | None = None,
    classifier: NativeCliMetadataClassifier | None = None,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    return _execute_reviewed(
        argv,
        expected_effect=EffectKind.EFFECTFUL,
        authorization_id=authorization_id,
        action_index=action_index,
        reviewed_authorization=authorization,
        authorization_consumer=authorization_consumer,
        classifier=classifier,
        process_runner=process_runner,
    )


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _domain_json_digest(value: object, *, domain: str) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _validate_reviewed_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if (
        isinstance(argv, (str, bytes))
        or not argv
        or any(
            not isinstance(argument, str) or not argument or "\0" in argument
            for argument in argv
        )
    ):
        raise AgentReadOnlyViolationError("agent_cli_command_invalid")
    sensitive_check_argv = _argv_with_service_feedback_links_sanitized(argv)
    if any(
        argument.startswith("--")
        and is_sensitive_field_name(argument[2:].partition("=")[0])
        for argument in sensitive_check_argv
    ) or any(contains_credential(argument) for argument in sensitive_check_argv):
        raise AgentReadOnlyViolationError("agent_cli_sensitive_argument")
    return tuple(argv)


def _argv_with_service_feedback_links_sanitized(
    argv: Sequence[str],
) -> tuple[str, ...]:
    """Exclude only exact service-owned feedback callbacks from CLI leak checks.

    Feedback callbacks are intentionally part of a DingTalk message body, but
    their ``feedback_token`` query field resembles a credential to the generic
    argument scanner.  The callback pair is accepted only for a recognized
    ``dws chat`` message content flag and only when it exactly matches the
    configured service base URL and generated-token contract.  All other
    arguments retain the strict credential checks.
    """
    if (
        len(argv) < 3
        or argv[0] != "dws"
        or argv[1] != "chat"
        or not feedback_spike_vercel_base_url()
    ):
        return tuple(argv)
    content_flags = {"--content", "--text"}
    sanitized = list(argv)
    for index, argument in enumerate(argv[:-1]):
        if argument not in content_flags:
            continue
        try:
            sanitized[index + 1] = str(
                sanitize_configured_feedback_links(
                    argv[index + 1],
                    vercel_base_url=feedback_spike_vercel_base_url(),
                )
            )
        except ValueError:
            # A callback-shaped value that fails the exact service contract is
            # still checked as-is and therefore remains rejected.
            sanitized[index + 1] = argv[index + 1]
    return tuple(sanitized)


def _classify_reviewed_write(
    argv: Sequence[str],
    *,
    classifier: NativeCliMetadataClassifier | None,
):
    canonical_argv = _validate_reviewed_argv(argv)
    reviewed = classifier or NativeCliMetadataClassifier()
    item = {"type": "command_execution", "argv": list(canonical_argv)}
    descriptor = describe_native_command(item)
    if descriptor is None or descriptor.cli == "local-shell":
        command = reviewed.classify(item)
    else:
        try:
            command = reviewed.classify(item)
        except NativeCliMetadataUnavailableError:
            command = None
        if command is None and _is_registered_native_write(descriptor):
            command = replace(descriptor, effect=EffectKind.EFFECTFUL)
    if command is None and _is_registered_native_write(descriptor):
        command = replace(descriptor, effect=EffectKind.EFFECTFUL)
    if command is None:
        raise AgentReadOnlyViolationError("agent_cli_command_unreviewed")
    if command.effect is not EffectKind.EFFECTFUL:
        raise AgentReadOnlyViolationError("reviewed_cli_effect_mismatch")
    if command.cli == "dws" and not has_noninteractive_confirmation(canonical_argv):
        raise AgentReadOnlyViolationError("agent_cli_confirmation_required")
    return canonical_argv, command


def _unclassified_read_command(argv: Sequence[str]):
    """Describe a read invocation without applying a CLI review policy."""
    canonical = tuple(argv)
    operation_parts: list[str] = []
    for argument in canonical[1:]:
        if argument.startswith("-"):
            break
        operation_parts.append(argument)
    return NativeCliCommand(
        cli=canonical[0],
        command_path=" ".join(operation_parts) or canonical[0],
        effect=EffectKind.READ_ONLY,
        command_digest=hashlib.sha256(
            json.dumps(
                list(canonical), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        target_identifiers={},
    )


def _is_registered_native_write(command) -> bool:
    return bool(
        command is not None
        and command.cli == "dws"
        and McpToolEffectRegistry.default().is_registered_write_operation(
            command.command_path
        )
    )


def review_write_authorization(
    argv: Sequence[str],
    authorization_id: str,
    action_index: int,
    classifier: NativeCliMetadataClassifier | None = None,
) -> ReviewedWriteAuthorization:
    if (
        not isinstance(authorization_id, str)
        or not authorization_id
        or authorization_id != authorization_id.strip()
    ):
        raise AgentReadOnlyViolationError("reviewed_write_authorization_invalid")
    if (
        isinstance(action_index, bool)
        or not isinstance(action_index, int)
        or action_index < 0
    ):
        raise AgentReadOnlyViolationError("reviewed_write_authorization_invalid")
    canonical_argv, command = _classify_reviewed_write(argv, classifier=classifier)
    capability = f"agent_cli.{command.cli}"
    targets = tuple(
        f"{key}={value}" for key, value in sorted(command.target_identifiers.items())
    )
    return ReviewedWriteAuthorization(
        authorization_id=authorization_id,
        action_index=action_index,
        capability=capability,
        operation=command.command_path,
        operation_digest=_domain_json_digest(
            {"capability": capability, "operation": command.command_path},
            domain="agent-cli-operation-v1",
        ),
        target_identifiers=targets,
        arguments_digest=_domain_json_digest(
            {"argv": list(canonical_argv)}, domain="agent-cli-arguments-v1"
        ),
    )


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
    if not any(
        material_path.is_relative_to(root) for root in SPREADSHEET_MATERIAL_ROOTS
    ):
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
    """Read bounded text, PDF, or OOXML material without shell access."""
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
            if file_stat.st_size > MAX_SPREADSHEET_BYTES:
                raise AgentReadOnlyViolationError("material_file_too_large")
        material_file.seek(0)
        is_pdf = (
            material_path.suffix.casefold() == ".pdf"
            or material_file.read(5) == b"%PDF-"
        )
        material_file.seek(0)
        if is_pdf:
            return _read_pdf_material(material_file, material_path)
        material_file.seek(0)
        if zipfile.is_zipfile(material_file):
            material_file.seek(0)
            try:
                with zipfile.ZipFile(material_file) as workbook:
                    if {"[Content_Types].xml", "xl/workbook.xml"}.issubset(
                        workbook.namelist()
                    ):
                        return _read_xlsx_workbook(
                            workbook,
                            max_rows=MAX_SPREADSHEET_ROWS,
                            max_columns=MAX_SPREADSHEET_COLUMNS,
                        )
                    if "ppt/presentation.xml" in workbook.namelist():
                        return _read_presentation_workbook(workbook)
            except zipfile.BadZipFile as exc:
                raise AgentReadOnlyViolationError("spreadsheet_file_invalid") from exc
            except ElementTree.ParseError as exc:
                raise AgentReadOnlyViolationError("spreadsheet_xml_invalid") from exc
        material_file.seek(0)
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


def _read_pdf_material(material_file, material_path: Path) -> dict[str, object]:
    try:
        reader = PdfReader(material_file)
    except (PdfReadError, ValueError) as exc:
        raise AgentReadOnlyViolationError("pdf_material_invalid") from exc
    if reader.is_encrypted:
        raise AgentReadOnlyViolationError("pdf_material_encrypted")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise AgentReadOnlyViolationError("pdf_material_page_limit_exceeded")

    previews: list[dict[str, object]] = []
    remaining_chars = MAX_TEXT_MATERIAL_BYTES
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except (PdfReadError, ValueError) as exc:
            raise AgentReadOnlyViolationError("pdf_material_text_unavailable") from exc
        if len(text) > remaining_chars:
            text = text[:remaining_chars]
        previews.append({"index": index, "text": text})
        remaining_chars -= len(text)
        if remaining_chars == 0:
            break

    material_file.seek(0)
    digest = hashlib.file_digest(material_file, "sha256").hexdigest()
    return {
        "format": "pdf",
        "page_count": len(reader.pages),
        "pages": previews,
        "content": "\n\n".join(page["text"] for page in previews),
        "path": str(material_path),
        "sha256": digest,
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


def _read_presentation_workbook(workbook: zipfile.ZipFile) -> dict[str, object]:
    """Return bounded visible text from an OOXML presentation package."""
    slides: list[dict[str, object]] = []
    remaining_chars = MAX_SPREADSHEET_PREVIEW_CHARS
    slide_names = sorted(
        name
        for name in workbook.namelist()
        if name.startswith("ppt/slides/slide") and name.endswith(".xml")
    )
    for index, name in enumerate(slide_names, start=1):
        root = ElementTree.fromstring(workbook.read(name))
        text = "\n".join(
            node.text or ""
            for node in root.iter()
            if node.tag.endswith("}t") and node.text
        )
        text = text[:remaining_chars]
        remaining_chars -= len(text)
        slides.append({"index": index, "text": text})
        if remaining_chars == 0:
            break
    return {
        "format": "pptx",
        "slides": slides,
        "truncated": len(slides) < len(slide_names),
    }


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
            column = "".join(
                character for character in reference if character.isalpha()
            )
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
            rows.append(
                {"row": int(row.attrib.get("r", len(rows) + 1)), "cells": cells}
            )
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
    text = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
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
    action_index: int | None = None,
    reviewed_authorization: ReviewedWriteAuthorization | None = None,
    authorization_consumer: Callable[[object], object] | None = None,
) -> dict[str, object]:
    argv = _validate_reviewed_argv(argv)
    reviewed = classifier or NativeCliMetadataClassifier()
    if expected_effect is EffectKind.READ_ONLY:
        command = _unclassified_read_command(argv)
    else:
        item = {"type": "command_execution", "argv": list(argv)}
        descriptor = describe_native_command(item)
        if descriptor is None or descriptor.cli == "local-shell":
            command = reviewed.classify(item)
        else:
            try:
                command = reviewed.classify(item)
            except NativeCliMetadataUnavailableError as exc:
                command = None
                if _is_registered_native_write(descriptor):
                    command = replace(descriptor, effect=EffectKind.EFFECTFUL)
                else:
                    return _process_failure_receipt(
                        descriptor,
                        code=exc.code,
                        retryable=exc.retryable,
                    )
            if command is None and _is_registered_native_write(descriptor):
                command = replace(descriptor, effect=EffectKind.EFFECTFUL)
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
    authorization: dict[str, object] | ReviewedWriteAuthorization | None = None
    if expected_effect is EffectKind.EFFECTFUL:
        if reviewed_authorization is not None:
            if authorization_id is None or action_index is None:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_mismatch"
                )
            actual = review_write_authorization(
                argv,
                authorization_id=authorization_id,
                action_index=action_index,
                classifier=reviewed,
            )
            if actual != reviewed_authorization:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_mismatch"
                )
            if authorization_consumer is None:
                raise AgentReadOnlyViolationError(
                    "reviewed_write_authorization_consumer_required"
                )
            authorization = actual
    executable_name = argv[0] if command.cli == "local-shell" else command.cli
    executable = shutil.which(executable_name)
    if executable is None:
        return _process_failure_receipt(
            command,
            code="agent_cli_start_unavailable",
            retryable=True,
        )
    if authorization is not None and authorization_consumer is not None:
        authorization_consumer(authorization)
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
        if isinstance(authorization, ReviewedWriteAuthorization):
            receipt["authorization_id"] = authorization.authorization_id
            receipt["action_index"] = authorization.action_index
        else:
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
        "Read bounded local materials. Service Agent turns use a required "
        "--role and task-bound server catalog."
    ),
)


async def execute_audited_email_unsubscribe_tool(
    task_id: int,
    execution_generation: str,
    audit_agent_run_id: int,
    accepted_action: dict[str, object],
) -> dict[str, object]:
    """Execute an accepted email unsubscribe from its current Audit run."""

    if isinstance(task_id, bool) or task_id <= 0:
        raise AgentReadOnlyViolationError("email_unsubscribe_task_id_invalid")
    if not isinstance(execution_generation, str) or not execution_generation.strip():
        raise AgentReadOnlyViolationError(
            "email_unsubscribe_execution_generation_invalid"
        )
    if isinstance(audit_agent_run_id, bool) or audit_agent_run_id <= 0:
        raise AgentReadOnlyViolationError("email_unsubscribe_audit_run_id_invalid")
    if not isinstance(accepted_action, dict):
        raise AgentReadOnlyViolationError("email_unsubscribe_action_invalid")
    from app.config import worker_db_path
    from app.email_worker import run_audited_email_unsubscribe

    return await asyncio.to_thread(
        run_audited_email_unsubscribe,
        worker_db_path(),
        task_id,
        execution_generation,
        audit_agent_run_id=audit_agent_run_id,
        accepted_action=accepted_action,
    )


async def unsubscribe_email_tool(task_id: int) -> dict[str, object]:
    """Unsubscribe this email task and return the page's own evidence.

    One call does the whole thing: it opens the entry the ActionPlan already
    authorized, operates what the page offers, and returns the outcome with
    the redacted page text behind it. Calling it again after it returned a
    receipt returns that same receipt instead of unsubscribing twice.
    """

    if isinstance(task_id, bool) or task_id <= 0:
        raise AgentReadOnlyViolationError("email_unsubscribe_task_id_invalid")
    from app.config import worker_db_path
    from app.email_worker import run_email_unsubscribe

    return await asyncio.to_thread(
        run_email_unsubscribe,
        worker_db_path(),
        task_id,
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
def read_skill_tool(
    path: str | None = None, name: str | None = None
) -> dict[str, object]:
    """Read an installed Agent skill or its referenced Markdown safely.

    With no source, return body-free installed name/use/path metadata. Otherwise
    pass exactly one source. `path` identifies a Markdown file inside an
    installed Skill directory. `name` resolves one exact installed Skill's
    metadata name and reads its SKILL.md through the same path authorization.
    """
    if path is not None and name is not None:
        raise ValueError("read_skill accepts only one of path or name")
    from app import agent_skill_usage
    from app.business_skills import (
        installed_skill_catalog,
        resolve_installed_skill_name,
    )

    if path is None and name is None:
        return {
            "skills": [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "path": str(entry.skill_path),
                }
                for entry in installed_skill_catalog(
                    roots=agent_skill_usage.AGENT_SKILL_ROOTS
                )
            ]
        }
    if name is not None:
        path = str(
            resolve_installed_skill_name(
                name, roots=agent_skill_usage.AGENT_SKILL_ROOTS
            ).skill_path
        )
    assert path is not None
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
    """Read bounded text, PDF, workbook, or presentation material from the temp directory."""
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


def _task_file_component(value: str) -> bool:
    return (
        isinstance(value, str) and bool(value) and value not in {".", ".."}
        and Path(value).name == value and "/" not in value
        and "\\" not in value and "\x00" not in value
    )


def _current_task_generation(db_path: Path | None, task_id: int | None) -> str:
    """Read the binding without initializing or migrating the live Store."""
    if db_path is None or type(task_id) is not int or task_id <= 0:
        raise ValueError("task file binding is missing")
    database = Path(db_path).resolve()
    try:
        with closing(sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "select execution_generation from reply_tasks where id=?", (task_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("task file binding is unavailable") from exc
    if row is None or not _task_file_component(row[0]):
        raise ValueError("task file generation is unavailable")
    return row[0]


def _read_bound_task_skill(
    db_path: Path | None,
    task_id: int | None,
    expected_generation: str | None,
    name: str,
) -> dict[str, str]:
    """Read one exact frozen Skill block from the bound task's original input."""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("task Skill name is invalid")
    if db_path is None or type(task_id) is not int or task_id <= 0:
        raise ValueError("task Skill binding is missing")
    database = Path(db_path).resolve()
    try:
        with closing(
            sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                "select execution_generation, trigger_message_json "
                "from reply_tasks where id=?",
                (task_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("task Skill binding is unavailable") from exc
    if row is None:
        raise ValueError("task Skill binding is unavailable")
    if row[0] != expected_generation:
        raise ValueError("task Skill generation changed")
    try:
        json.loads(row[1])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("task Skill material is unavailable") from exc
    from app.business_skills import frozen_task_skill_materials

    materials = frozen_task_skill_materials(row[1])
    if not materials:
        raise ValueError("task Skill material is unavailable")
    matches = [
        material for material in materials if material.name == name
    ]
    if not matches:
        raise ValueError(f"unknown task Skill material: {name}")
    if len(matches) != 1:
        raise ValueError(f"ambiguous task Skill material: {name}")
    content = matches[0].content
    return {
        "name": name,
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def _task_file_root(
    db_path: Path | None, task_id: int | None, generation: str | None, *, create: bool,
) -> Path:
    from app.config import repo_root, workspace_path

    if generation is None:
        raise ValueError("task file binding is missing")
    if _current_task_generation(db_path, task_id) != generation:
        raise ValueError("task file generation changed")
    root = workspace_path().resolve()
    if root.is_relative_to(repo_root().resolve()):
        raise ValueError("task file workspace overlaps service source")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    for component in ("consumer-artifacts", str(task_id), generation):
        root = root / component
        if root.is_symlink():
            raise ValueError("task file directory is linked")
        if create:
            root.mkdir(exist_ok=True)
    return root


def _task_file_path(root: Path, name: str) -> Path:
    if (
        not _task_file_component(name) or len(name) > 255
        or name.startswith(_TASK_ARTIFACT_TEMP_PREFIX)
    ):
        raise ValueError("task file name is invalid")
    path = root / name
    if path.is_symlink():
        raise ValueError("task file is linked")
    return path


def _read_task_file(root: Path, name: str) -> dict[str, str]:
    path = _task_file_path(root, name)
    if not path.is_file():
        raise ValueError("task file is unavailable")
    data = path.read_bytes()
    if len(data) > MAX_TASK_FILE_BYTES:
        raise ValueError("task file is too large")
    item = {"name": name, "content": data.decode("utf-8"),
            "sha256": hashlib.sha256(data).hexdigest()}
    if len(json.dumps(item).encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("task file readback is too large")
    return item


def _bound_report(db_path: Path | None, task_id: int | None):
    from app.store import AutoReplyStore

    if db_path is None or type(task_id) is not int or task_id <= 0:
        raise AgentReadOnlyViolationError("agent_cli_task_binding_missing")
    store = AutoReplyStore(db_path)
    task = store.get_reply_task(task_id)
    if task is None or task.channel != "scheduled":
        raise AgentReadOnlyViolationError("agent_cli_report_task_invalid")
    run = store.get_scheduled_task_run_for_reply_execution(task_id)
    if run is None:
        raise AgentReadOnlyViolationError("agent_cli_report_run_missing")
    skill_names = {ref.skill_name for ref in run.snapshot.skill_refs}
    if "ceo-daily-report" in skill_names:
        return store, run, "daily"
    if "ceo-weekly-report" in skill_names:
        return store, run, "weekly"
    raise AgentReadOnlyViolationError("agent_cli_report_skill_missing")


def _weekly_skill_module(name: str):
    """Import one installed, fixed weekly-report script as a Python module."""
    skill_root = Path.home() / ".agents" / "skills" / "ceo-weekly-report"
    if not (skill_root / "scripts" / f"{name}.py").is_file():
        raise AgentReadOnlyViolationError("weekly_report_script_unavailable")
    skill_path = str(skill_root)
    if skill_path not in sys.path:
        sys.path.insert(0, skill_path)
    module = importlib.import_module(f"scripts.{name}")
    if not Path(module.__file__).resolve().is_relative_to(skill_root.resolve()):
        raise AgentReadOnlyViolationError("weekly_report_script_mismatch")
    return module


def _write_bound_report_document(
    *, db_path: Path | None, task_id: int | None, content: str,
    expected_revision: int | None,
) -> dict[str, object]:
    """Write only the report document resolved from this scheduled run."""
    from app.config import workspace_path
    from app.daily_report_facts import report_window_for_run
    from app.dws_client import DwsClient
    from app.minutes_sync import MINUTES_ARCHIVE_DIRECTORY
    from app.weekly_report_materials import (
        MANAGEMENT_WIKI_NAME,
        _at_most_one, _data_list, _nodes, _single,
        collect_weekly_report_materials,
    )

    store, run, kind = _bound_report(db_path, task_id)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("report document content is required")
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("report document content is too large")
    dws = DwsClient()
    if kind == "weekly":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("weekly report JSONML is invalid") from exc
        if not isinstance(parsed, list):
            raise ValueError("weekly report JSONML must be an array")
        errors = _weekly_skill_module("jsonml_check").check_document(parsed)
        if errors:
            raise ValueError("weekly report JSONML invalid: " + "; ".join(errors))
        native_parse = dws.validate_report_jsonml(content)
        assessment = native_parse.get("assessment")
        if assessment is None and isinstance(native_parse.get("data"), dict):
            assessment = native_parse["data"].get("assessment")
        if assessment != "passed":
            raise ValueError("weekly report document parser did not pass")
    if kind == "daily":
        report_date = report_window_for_run(store, run.id).report_date
        title = f"CEO 每日总结 {report_date.isoformat()}"
        workspace_id = _single(
            [
                item for item in _data_list(dws.run_json([
                    dws.dws_bin, "wiki", "+space-list", "--type", "orgWikiSpace",
                    "--limit", "50", "--page-all", "--format", "json",
                ]), "spaces")
                if item.get("name") == MANAGEMENT_WIKI_NAME
            ],
            "CEO management wiki",
        )["workspaceId"]
        folder = _at_most_one(
            [node for node in _nodes(dws, workspace_id, parent_id=None)
             if node.get("name") == "CEO 每日总结"],
            "CEO daily report folder",
        )
        if folder is None:
            dws.run_json([
                dws.dws_bin, "wiki", "+node-create", "--workspace", workspace_id,
                "--name", "CEO 每日总结", "--type", "folder", "--format", "json",
            ])
            folder = _single(
                [node for node in _nodes(dws, workspace_id, parent_id=None)
                 if node.get("name") == "CEO 每日总结"],
                "CEO daily report folder",
            )
        folder_id = folder["nodeId"]
        doc_format = "markdown"
    else:
        materials = collect_weekly_report_materials(
            store, dws, workspace_path() / MINUTES_ARCHIVE_DIRECTORY,
            run.scheduled_for,
        )
        docs = materials["documents"]
        title = docs["target"]["title"]
        folder_id = docs["target"]["year_page_id"]
        workspace_id = docs["workspace_id"]
        if not folder_id:
            dws.run_json([
                dws.dws_bin, "wiki", "+node-create", "--workspace", workspace_id,
                "--folder", docs["meeting_folder_id"],
                "--name", docs["target"]["year_page_name"],
                "--type", "adoc", "--format", "json",
            ])
            folder_id = _single(
                [node for node in _nodes(dws, workspace_id, parent_id=docs["meeting_folder_id"])
                 if node.get("name") == docs["target"]["year_page_name"]],
                "meeting year page",
            )["nodeId"]
        doc_format = "jsonml"

    found = _at_most_one(
        [node for node in _nodes(dws, workspace_id, parent_id=folder_id)
         if node.get("name") == title],
        "current report document",
    )
    if found is None:
        result = dws.create_report_document(
            name=title, content=content, doc_format=doc_format,
            folder_id=folder_id,
        )
        found = _single(
            [node for node in _nodes(dws, workspace_id, parent_id=folder_id)
             if node.get("name") == title],
            "created report document",
        )
        operation = "created"
    else:
        version = None
        if kind == "weekly":
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError("weekly report requires expected revision")
            version = dws.save_report_document_version(found["nodeId"])
        result = dws.overwrite_report_document(
            node_id=found["nodeId"], content=content, doc_format=doc_format,
            expected_revision=expected_revision,
        )
        operation = "updated"
    readback = (
        dws.fetch_report_document_full(found["nodeId"])
        if kind == "weekly" else dws.read_doc(found["nodeId"])
    )
    receipt = {
        "operation": operation, "node_id": found["nodeId"], "title": title,
        "provider_result": result, "readback": readback,
        "saved_version": version if kind == "weekly" and operation == "updated" else None,
    }
    if kind == "daily":
        receipt["folder"] = folder
    return receipt


def build_role_server(
    role: str, *, task_id: int | None = None, db_path: Path | None = None,
    execution_generation: str | None = None,
) -> FastMCP:
    """Expose only the operations owned by one Agent role."""
    if role not in {"consumer", "audit"}:
        raise ValueError("unsupported agent role")
    artifact_generation = (
        _current_task_generation(db_path, task_id)
        if task_id is not None and db_path is not None else None
    )
    if execution_generation is not None and artifact_generation != execution_generation:
        raise ValueError("task file generation changed")
    bound = FastMCP("agent_cli", instructions="Task-bound Agent reads and Consumer report documents")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                           idempotentHint=True, openWorldHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                            idempotentHint=False, openWorldHint=True)
    bound.add_tool(read_skill_tool, name="read_skill", annotations=read)
    bound.add_tool(read_text_file_tool, name="read_text_file", annotations=read)
    bound.add_tool(read_spreadsheet_tool, name="read_spreadsheet", annotations=read)

    def read_system_action_contract(
        capability: str,
        operation: str | None = None,
    ) -> dict[str, object]:
        """Read the complete canonical contract for an exact reviewed system action.

        Pass a capability from the prompt catalog and optionally one of its
        operations. The result retains every shared, role, identity, payload,
        target, and completion-evidence rule from the canonical contract.
        """
        return load_system_action_contract(capability, operation)

    bound.add_tool(
        read_system_action_contract,
        name="read_system_action_contract",
        annotations=read,
    )

    def read_task_skill(name: str) -> dict[str, str]:
        """Read an exact selected Skill frozen in this task's original input.

        Pass a Skill name from the scheduled prompt catalog. The returned content
        is the immutable managed revision or operation snapshot saved when the
        scheduled command ran; this lookup never rereads an installed Skill file.
        """
        return _read_bound_task_skill(db_path, task_id, artifact_generation, name)

    bound.add_tool(read_task_skill, name="read_task_skill", annotations=read)

    def read_task_artifact(name: str) -> dict[str, str]:
        """Read a text artifact from this task's current execution generation."""
        return _read_task_file(
            _task_file_root(db_path, task_id, artifact_generation, create=False), name
        )

    def list_task_artifacts() -> dict[str, object]:
        """List text artifacts from this task's current execution generation."""
        root = _task_file_root(db_path, task_id, artifact_generation, create=False)
        if not root.is_dir():
            return {"files": []}
        files = []
        for path in sorted(root.iterdir()):
            if (path.is_file() and not path.is_symlink()
                    and not path.name.startswith(_TASK_ARTIFACT_TEMP_PREFIX)):
                if path.stat().st_size > MAX_TASK_FILE_BYTES:
                    raise ValueError("task file is too large")
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        digest.update(chunk)
                files.append({"name": path.name, "sha256": digest.hexdigest()})
                if len(json.dumps({"files": files}).encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
                    raise ValueError("task file listing is too large")
        return {"files": files}

    bound.add_tool(read_task_artifact, name="read_task_artifact", annotations=read)
    bound.add_tool(list_task_artifacts, name="list_task_artifacts", annotations=read)

    def read_dingtalk_document(node_id: str) -> dict[str, object]:
        """Read a DingTalk document and its current content."""
        from app.dws_client import DwsClient
        return DwsClient().read_doc(node_id)

    def read_dingtalk_document_full(node_id: str) -> dict[str, object]:
        """Read a DingTalk document's full JSONML and editing revision."""
        from app.dws_client import DwsClient
        return DwsClient().fetch_report_document_full(node_id)

    def read_dingtalk_document_info(node_id: str) -> dict[str, object]:
        """Read an exact document node's title, type, and owner metadata."""
        from app.dws_client import DwsClient
        return DwsClient().doc_info(node_id)

    def read_dingtalk_document_permissions(node_id: str) -> dict[str, object]:
        """Read an exact document's current collaborators and roles."""
        from app.dws_client import DwsClient
        if not node_id.strip():
            raise ValueError("document node is required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "doc", "permission", "list", "--node", node_id,
            "--limit", "50", "--format", "json",
        ])

    def read_dingtalk_drive_info(node_id: str, space_id: str = "") -> dict[str, object]:
        """Read an exact drive file's metadata before deciding how to open it."""
        from app.dws_client import DwsClient
        if not node_id.strip():
            raise ValueError("drive node is required")
        dws = DwsClient()
        command = [dws.dws_bin, "drive", "+info", "--node", node_id]
        if space_id.strip():
            command.extend(("--space-id", space_id))
        return dws.run_json(command + ["--format", "json"])

    def list_dingtalk_sheets(node_id: str) -> dict[str, object]:
        """List workbook sheets by stable sheet ID."""
        from app.dws_client import DwsClient
        if not node_id.strip():
            raise ValueError("sheet node is required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "sheet", "list", "--node", node_id, "--format", "json",
        ])

    def read_dingtalk_sheet_info(node_id: str, sheet_id: str) -> dict[str, object]:
        """Read one sheet's bounds and merged-cell metadata."""
        from app.dws_client import DwsClient
        if not node_id.strip() or not sheet_id.strip():
            raise ValueError("sheet node and ID are required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "sheet", "info", "--node", node_id,
            "--sheet-id", sheet_id, "--format", "json",
        ])

    def read_dingtalk_sheet_range(
        node_id: str, sheet_id: str, cell_range: str,
    ) -> dict[str, object]:
        """Read an explicit A1 workbook range, bounded by the DWS read API."""
        from app.dws_client import DwsClient
        if not node_id.strip() or not sheet_id.strip() or not cell_range.strip():
            raise ValueError("sheet node, ID, and range are required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "sheet", "range", "read", "--node", node_id,
            "--sheet-id", sheet_id, "--range", cell_range, "--format", "json",
        ])

    def read_dingtalk_oa(process_instance_id: str) -> dict[str, object]:
        """Read the live DingTalk OA process and its current facts."""
        from app.dws_client import DwsClient
        return DwsClient().read_oa_approval_detail(process_instance_id)

    def list_dingtalk_pending_oa(page: int = 1) -> dict[str, object]:
        """Read one unfiltered page of the principal's live OA pending list."""
        from app.dws_client import DwsClient
        if type(page) is not int or page < 1:
            raise ValueError("OA pending-list page is invalid")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "oa", "approval", "list-pending", "--page", str(page),
            "--limit", "20", "--format", "json",
        ])

    def read_dingtalk_oa_records(process_instance_id: str) -> dict[str, object]:
        """Read one OA process's recorded actions."""
        from app.dws_client import DwsClient
        return DwsClient().read_oa_approval_records(process_instance_id)

    def read_dingtalk_oa_tasks(process_instance_id: str) -> dict[str, object]:
        """Read one OA process's current tasks and owners."""
        from app.dws_client import DwsClient
        return DwsClient().read_oa_approval_tasks(process_instance_id)

    def read_dingtalk_oa_revert_activities(oa_task_id: str) -> dict[str, object]:
        """Read legal return destinations for one exact OA task."""
        from app.dws_client import DwsClient
        return DwsClient().read_oa_revert_activities(oa_task_id)

    def read_dingtalk_calendar_event(event_id: str) -> dict[str, object]:
        """Read one live DingTalk calendar event."""
        from app.dws_client import DwsClient
        event = DwsClient().get_calendar_event(event_id)
        return {"event": event.model_dump(mode="json") if event else None}

    def list_dingtalk_calendar_events(start: str, end: str) -> dict[str, object]:
        """Read the principal's events over an explicit time window."""
        from app.dws_client import DwsClient
        events = DwsClient().list_calendar_events(start, end)
        return {"events": [item.model_dump(mode="json") for item in events]}

    def read_dingtalk_group_members(conversation_id: str) -> dict[str, object]:
        """Read the complete bounded users/bots roster for an exact group ID.

        Check complete, hasMore, buckets and failures. Partial results are not
        a complete audience. openDingtalkId is not an organization userId;
        correlate stable identifiers before reading enterprise profiles.
        """
        from app.dws_client import DwsClient
        return DwsClient().read_group_members(conversation_id)

    def read_dingtalk_user_profiles(user_ids: list[str]) -> dict[str, object]:
        """Read exact organization userId profiles and original organization fields.

        Supply verified organization userIds, not display names or group
        openDingtalkIds. Preserve empty profiles as missing evidence; a native
        success flag does not prove identity or title. Preserve native depts,
        labels and manager fields; verify title evidence from actual returned
        data rather than assuming every profile contains a title.
        """
        from app.dws_client import DwsClient
        return DwsClient().read_user_profiles(user_ids)

    def search_dingtalk_contacts(query: str) -> dict[str, object]:
        """Find current organization users by name or stable identifier."""
        from app.dws_client import DwsClient
        if not query.strip():
            raise ValueError("contact query is required")
        profiles = DwsClient().search_user_profiles(query)
        return {"users": [item.model_dump(mode="json") for item in profiles]}

    def search_dingtalk_documents(query: str, page_size: int = 10) -> dict[str, object]:
        """Find DingTalk documents by title before reading an exact node."""
        from app.dws_client import DwsClient
        if not query.strip() or type(page_size) is not int or not 1 <= page_size <= 20:
            raise ValueError("document search query or page size is invalid")
        matches = DwsClient().search_documents(query, page_size)
        return {"documents": [item.model_dump(mode="json") for item in matches]}

    def read_dingtalk_messages(
        conversation_id: str, title: str = "", single_chat: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        """Read recent messages of an identified DingTalk conversation."""
        from app.dingtalk_models import DingTalkConversation
        from app.dws_client import DwsClient
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("message read limit is invalid")
        conversation = DingTalkConversation(
            open_conversation_id=conversation_id, title=title,
            single_chat=single_chat, unread_point=0,
        )
        messages = DwsClient().read_recent_messages(conversation, limit=limit)
        return {"messages": [item.model_dump(mode="json") for item in messages]}

    def search_dingtalk_messages(
        query: str, start: str, end: str, limit: int = 100,
    ) -> dict[str, object]:
        """Search messages by exact keyword in an explicit time window."""
        from app.dws_client import DwsClient
        if not query.strip() or not start.strip() or not end.strip():
            raise ValueError("message search query and window are required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("message search limit is invalid")
        messages = DwsClient().search_messages(query, start, end, limit)
        return {"messages": [item.model_dump(mode="json") for item in messages]}

    def read_dingtalk_thread_replies(message_id: str) -> dict[str, object]:
        """Read the full bounded reply thread for one exact root message."""
        from app.dws_client import DwsClient
        if not message_id.strip():
            raise ValueError("thread root message ID is required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "chat", "+thread-replies", "--message-id", message_id,
            "--page-all", "--page-limit", "100", "--order", "asc",
            "--format", "json",
        ])

    def search_dingtalk_conversations(query: str) -> dict[str, object]:
        """Find an existing DingTalk conversation by title."""
        from app.dws_client import DwsClient
        if not query.strip():
            raise ValueError("conversation query is required")
        matches = DwsClient().search_conversations(query)
        return {"conversations": [item.model_dump(mode="json") for item in matches]}

    def list_dingtalk_minutes(
        query: str = "", start: str = "", end: str = "", scope: str = "all",
    ) -> dict[str, object]:
        """Search accessible AI minutes by title or time range."""
        from app.dws_client import DwsClient
        if not (query.strip() or start.strip() or end.strip()):
            raise ValueError("minutes query or window is required")
        if scope not in {"all", "mine", "shared"}:
            raise ValueError("minutes scope is invalid")
        dws = DwsClient()
        command = [dws.dws_bin, "minutes", "+search", "--scope", scope]
        for flag, value in (("--query", query), ("--start", start), ("--end", end)):
            if value.strip():
                command.extend((flag, value))
        command.extend(("--page-all", "--page-limit", "100", "--format", "json"))
        return dws.run_json(command)

    def read_dingtalk_minutes(task_uuid: str) -> dict[str, object]:
        """Read AI minutes details, summary, and the complete transcript."""
        from app.dws_client import DwsClient
        if not task_uuid.strip():
            raise ValueError("minutes task UUID is required")
        dws = DwsClient()
        return {
            "info": dws.get_minutes_info(task_uuid),
            "summary": dws.get_minutes_summary(task_uuid),
            "transcript": dws.get_all_minutes_transcription(task_uuid),
        }

    def recent_dingtalk_conversations(
        start: str, end: str,
    ) -> dict[str, object]:
        """List conversations active in a bounded report time window."""
        from app.dws_client import DwsClient
        if not start.strip() or not end.strip():
            raise ValueError("conversation window is required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "chat", "+recent-conversations", "--start", start,
            "--end", end, "--page-limit", "50", "--format", "json",
        ])

    def read_dingtalk_messages_in_window(
        conversation_id: str, start: str, end: str,
    ) -> dict[str, object]:
        """Read one identified group's messages over the report window."""
        from app.dws_client import DwsClient
        if not conversation_id.strip() or not start.strip() or not end.strip():
            raise ValueError("group conversation and window are required")
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "chat", "+chat-messages", "--group", conversation_id,
            "--start", start, "--end", end, "--order", "asc", "--page-all",
            "--page-limit", "50", "--max-items", "1000", "--format", "json",
        ])

    def daily_report_facts() -> dict[str, object]:
        """Read the fixed service facts for this scheduled daily report."""
        from app.daily_report_facts import collect_daily_report_facts, report_window_for_run
        from app.email_store import EmailStore
        store, run, kind = _bound_report(db_path, task_id)
        if kind != "daily":
            raise AgentReadOnlyViolationError("daily_report_task_required")
        return collect_daily_report_facts(
            store,
            EmailStore(db_path, validate_rows=False),
            report_window_for_run(store, run.id),
        )

    def weekly_report_materials() -> dict[str, object]:
        """Read the fixed inputs for this scheduled CEO weekly report."""
        from app.config import workspace_path
        from app.dws_client import DwsClient
        from app.minutes_sync import MINUTES_ARCHIVE_DIRECTORY
        from app.weekly_report_materials import collect_weekly_report_materials
        store, run, kind = _bound_report(db_path, task_id)
        if kind != "weekly":
            raise AgentReadOnlyViolationError("weekly_report_task_required")
        return collect_weekly_report_materials(
            store, DwsClient(), workspace_path() / MINUTES_ARCHIVE_DIRECTORY,
            run.scheduled_for,
        )

    def read_weekly_report_archive(path: str) -> dict[str, object]:
        """Read one transcript named in this run's meeting archive index."""
        from app.config import workspace_path
        from app.minutes_sync import MINUTES_ARCHIVE_DIRECTORY
        from app.weekly_report_materials import minutes_index, weekly_window
        store, run, kind = _bound_report(db_path, task_id)
        if kind != "weekly":
            raise AgentReadOnlyViolationError("weekly_report_task_required")
        archive_dir = workspace_path() / MINUTES_ARCHIVE_DIRECTORY
        allowed = {
            item["archive_path"]
            for item in minutes_index(store, archive_dir, weekly_window(run.scheduled_for))
            if item["archive_path"]
        }
        if path not in allowed:
            raise AgentReadOnlyViolationError("weekly_report_archive_not_in_run")
        material = Path(path)
        if not material.is_file() or material.stat().st_size > MAX_TEXT_MATERIAL_BYTES:
            raise AgentReadOnlyViolationError("weekly_report_archive_unavailable")
        return {"path": path, "content": material.read_text(encoding="utf-8")}

    for name, tool in (
        ("read_dingtalk_document", read_dingtalk_document),
        ("read_dingtalk_document_full", read_dingtalk_document_full),
        ("read_dingtalk_document_info", read_dingtalk_document_info),
        ("read_dingtalk_document_permissions", read_dingtalk_document_permissions),
        ("read_dingtalk_drive_info", read_dingtalk_drive_info),
        ("list_dingtalk_sheets", list_dingtalk_sheets),
        ("read_dingtalk_sheet_info", read_dingtalk_sheet_info),
        ("read_dingtalk_sheet_range", read_dingtalk_sheet_range),
        ("read_dingtalk_oa", read_dingtalk_oa),
        ("list_dingtalk_pending_oa", list_dingtalk_pending_oa),
        ("read_dingtalk_oa_records", read_dingtalk_oa_records),
        ("read_dingtalk_oa_tasks", read_dingtalk_oa_tasks),
        ("read_dingtalk_oa_revert_activities", read_dingtalk_oa_revert_activities),
        ("read_dingtalk_calendar_event", read_dingtalk_calendar_event),
        ("list_dingtalk_calendar_events", list_dingtalk_calendar_events),
        ("search_dingtalk_contacts", search_dingtalk_contacts),
        ("read_dingtalk_group_members", read_dingtalk_group_members),
        ("read_dingtalk_user_profiles", read_dingtalk_user_profiles),
        ("search_dingtalk_documents", search_dingtalk_documents),
        ("read_dingtalk_messages", read_dingtalk_messages),
        ("search_dingtalk_messages", search_dingtalk_messages),
        ("read_dingtalk_thread_replies", read_dingtalk_thread_replies),
        ("search_dingtalk_conversations", search_dingtalk_conversations),
        ("list_dingtalk_minutes", list_dingtalk_minutes),
        ("read_dingtalk_minutes", read_dingtalk_minutes),
        ("recent_dingtalk_conversations", recent_dingtalk_conversations),
        ("read_dingtalk_messages_in_window", read_dingtalk_messages_in_window),
        ("daily_report_facts", daily_report_facts),
        ("weekly_report_materials", weekly_report_materials),
        ("read_weekly_report_archive", read_weekly_report_archive),
    ):
        bound.add_tool(tool, name=name, annotations=read)

    if role == "consumer":
        def consumer_artifact_write(name: str, content: str) -> dict[str, str]:
            """Write a text artifact only in this task's current workspace generation."""
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TASK_FILE_BYTES:
                raise ValueError("task file content is invalid or too large")
            root = _task_file_root(db_path, task_id, artifact_generation, create=True)
            path = _task_file_path(root, name)
            if len(json.dumps({"name": name, "content": content,
                               "sha256": "0" * 64}).encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
                raise ValueError("task file readback is too large")
            descriptor, temporary = tempfile.mkstemp(prefix=_TASK_ARTIFACT_TEMP_PREFIX, dir=root)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content.encode("utf-8"))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return _read_task_file(root, name)

        bound.add_tool(consumer_artifact_write, name="consumer_artifact_write", annotations=write)

        def validate_weekly_report(
            manifest: dict, report: dict, previous_issues: list[dict],
        ) -> dict[str, object]:
            """Validate a scheduled CEO weekly report using its installed Skill."""
            _store, _run, kind = _bound_report(db_path, task_id)
            if kind != "weekly":
                raise AgentReadOnlyViolationError("weekly_report_task_required")
            return _weekly_skill_module("validate_run").validate_run(
                manifest, report, previous_issues
            )

        def render_weekly_report(
            report: dict, before: list,
        ) -> dict[str, object]:
            """Render and validate weekly report JSONML and its full draft."""
            from app.dws_client import DwsClient
            _store, _run, kind = _bound_report(db_path, task_id)
            if kind != "weekly":
                raise AgentReadOnlyViolationError("weekly_report_task_required")
            renderer = _weekly_skill_module("render_dingtalk_jsonml")
            checker = _weekly_skill_module("jsonml_check")
            target_after = renderer.render_meeting_document(before, report)
            draft = renderer.render_draft(report)
            errors = checker.check_document(target_after) + checker.check_document(draft)
            if errors:
                raise ValueError("weekly report JSONML invalid: " + "; ".join(errors))
            native_parse = DwsClient().validate_report_jsonml(
                json.dumps(target_after, ensure_ascii=False)
            )
            return {
                "target_after": target_after,
                "draft": draft,
                "native_parse": native_parse,
            }

        def consumer_document_write(
            content: str, expected_revision: int | None = None,
        ) -> dict[str, object]:
            """Publish this scheduled CEO report to its service-resolved document."""
            if artifact_generation is None:
                raise ValueError("task file binding is missing")
            if _current_task_generation(db_path, task_id) != artifact_generation:
                raise ValueError("task file generation changed")
            return _write_bound_report_document(
                db_path=db_path, task_id=task_id, content=content,
                expected_revision=expected_revision,
            )

        bound.add_tool(validate_weekly_report, name="validate_weekly_report", annotations=read)
        bound.add_tool(render_weekly_report, name="render_weekly_report", annotations=read)
        bound.add_tool(consumer_document_write, name="consumer_document_write", annotations=write)
    return bound


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("consumer", "audit"), required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--execution-generation", required=True)
    args = parser.parse_args()
    build_role_server(args.role, task_id=args.task_id, db_path=args.db,
                      execution_generation=args.execution_generation).run(transport="stdio")
