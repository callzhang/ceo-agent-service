from __future__ import annotations

import asyncio
import atexit
import base64
import argparse
import hashlib
import errno
import json
import mimetypes
import os
import posixpath
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
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pydantic_core import to_json

from app.action_contract_catalog import (
    read_system_action_contract as load_system_action_contract,
)
from app.agent_result import EffectKind
from app.agent_effects import McpToolEffectRegistry
from app.agent_skill_usage import resolve_authorized_skill_path
from app.bounded_process import (
    ProcessOutputLimitError,
    run_bounded_process,
)
from app.channel_gate import classify_cli_read_failure, classify_cli_write_failure
from app.config import feedback_spike_vercel_base_url
from app.dingtalk_message_payload_sharing import share_message_raw_payload
from app.dingtalk_sheet_style_sharing import share_sheet_cell_styles
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
from app.task_artifacts import migrate_legacy_task_artifacts, task_artifact_directory


# Codex truncates one MCP text block at 1 MiB. Keep command output below that
# boundary even after JSON escaping so the controlled receipt remains valid.
MAX_CLI_OUTPUT_BYTES = 128 * 1024
MAX_MCP_MATERIAL_RESPONSE_BYTES = 1024 * 1024 - 1024
MAX_SKILL_BYTES = 256 * 1024
MAX_TEXT_MATERIAL_BYTES = 512 * 1024
MAX_SPREADSHEET_BYTES = 20 * 1024 * 1024
MAX_PPTX_BYTES = 128 * 1024 * 1024
MAX_PPTX_EXPANDED_BYTES = 160 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_SPREADSHEET_ROWS = 200
MAX_SPREADSHEET_COLUMNS = 64
MAX_SPREADSHEET_PREVIEW_CHARS = 128 * 1024
MAX_PDF_PAGES = 100
MAX_OCR_AGENT_INPUT_BYTES = 700 * 1024
MAX_OA_COMMENT_IMAGE_BYTES = 20 * 1024 * 1024
CLI_TIMEOUT_SECONDS = 15 * 60
CliOutputLimitError = ProcessOutputLimitError
SPREADSHEET_MATERIAL_ROOTS = (
    Path("/tmp").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)
TEXT_MATERIAL_ROOTS = SPREADSHEET_MATERIAL_ROOTS
MAX_TASK_FILE_BYTES = 2 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 95 * 1024 * 1024  # ai_services create_audio_upload max_bytes
_TASK_ARTIFACT_TEMP_PREFIX = ".task-artifact-"
CONSUMER_SCRIPT_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "ci" / "shared-skills"


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


class _MaterialZipFile(zipfile.ZipFile):
    def __init__(self, file, *, max_expanded_bytes=MAX_SPREADSHEET_BYTES):
        super().__init__(file)
        self._remaining_material_bytes = max_expanded_bytes

    def read(self, name, pwd=None):
        member = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        if member.file_size > self._remaining_material_bytes:
            raise AgentReadOnlyViolationError("material_expanded_too_large")
        if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise AgentReadOnlyViolationError("material_compression_unsupported")
        if member.compress_type == zipfile.ZIP_STORED and member.file_size != member.compress_size:
            raise AgentReadOnlyViolationError("material_compression_size_invalid")
        with self.open(member, pwd=pwd) as source:
            content = source.read(self._remaining_material_bytes + 1)
            if len(content) > self._remaining_material_bytes:
                raise AgentReadOnlyViolationError("material_expanded_too_large")
            # ZipExtFile slices output to the archive's declared size. Check the
            # actual compressed stream termination before trusting that slice.
            if (source._compress_left != 0
                    or (source._decompressor is not None and not source._decompressor.eof)
                    or len(content) != member.file_size):
                raise AgentReadOnlyViolationError("material_compression_size_invalid")
        self._remaining_material_bytes -= len(content)
        return content


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
            with _MaterialZipFile(material_file) as workbook:
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
    """Read bounded text, image, PDF, or OOXML material without shell access."""
    material_path = Path(path).expanduser().resolve(strict=True)
    if not any(material_path.is_relative_to(root) for root in TEXT_MATERIAL_ROOTS):
        raise AgentReadOnlyViolationError("text_material_path_forbidden")
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(material_path, flags), "rb") as material_file:
        file_stat = os.fstat(material_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise AgentReadOnlyViolationError("text_material_file_not_regular")
        material_file.seek(0)
        is_pdf = (
            material_path.suffix.casefold() == ".pdf"
            or material_file.read(5) == b"%PDF-"
        )
        is_pptx = material_path.suffix.casefold() == ".pptx"
        material_file.seek(0)
        if is_pdf:
            if file_stat.st_size > MAX_PDF_BYTES:
                raise AgentReadOnlyViolationError("material_file_too_large")
        elif is_pptx:
            if file_stat.st_size > MAX_PPTX_BYTES:
                raise AgentReadOnlyViolationError("material_file_too_large")
        elif file_stat.st_size > MAX_TEXT_MATERIAL_BYTES:
            if file_stat.st_size > MAX_SPREADSHEET_BYTES:
                raise AgentReadOnlyViolationError("material_file_too_large")
        if is_pdf:
            return _read_pdf_material(material_file, material_path)
        material_file.seek(0)
        if zipfile.is_zipfile(material_file):
            material_file.seek(0)
            try:
                with _MaterialZipFile(
                    material_file,
                    max_expanded_bytes=(
                        MAX_PPTX_EXPANDED_BYTES if is_pptx else MAX_SPREADSHEET_BYTES
                    ),
                ) as workbook:
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
        image_mime, _encoding = mimetypes.guess_type(material_path.name)
        if image_mime and image_mime.startswith("image/"):
            return _read_image_material(material_file, material_path)
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


def _read_image_material(material_file, material_path: Path) -> dict[str, object]:
    material_file.seek(0)
    source_sha256 = hashlib.file_digest(material_file, "sha256").hexdigest()
    material_file.seek(0)
    original = material_file.read(MAX_OCR_AGENT_INPUT_BYTES + 1)
    if len(original) <= MAX_OCR_AGENT_INPUT_BYTES:
        return {
            "format": "image",
            "ocr_required": True,
            "ocr_input_base64": base64.b64encode(original).decode("ascii"),
            "ocr_mime_type": mimetypes.guess_type(material_path.name)[0]
            or "application/octet-stream",
            "path": str(material_path),
            "sha256": source_sha256,
        }

    try:
        from PIL import Image, ImageOps

        material_file.seek(0)
        with Image.open(material_file) as source:
            width, height = source.size
            image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image.close()
        if max(background.size) > 3000:
            background.thumbnail((3000, 3000))
        try:
            for scale in (1.0, 0.85, 0.7, 0.55):
                current = background.copy()
                try:
                    if scale < 1.0:
                        current.thumbnail((
                            int(background.width * scale),
                            int(background.height * scale),
                        ))
                    for quality in (88, 80, 72, 64):
                        encoded = BytesIO()
                        current.save(encoded, format="JPEG", quality=quality, optimize=True)
                        payload = encoded.getvalue()
                        if len(payload) <= MAX_OCR_AGENT_INPUT_BYTES:
                            return {
                                "format": "image",
                                "ocr_required": True,
                                "ocr_input_base64": base64.b64encode(payload).decode("ascii"),
                                "ocr_mime_type": "image/jpeg",
                                "ocr_source_mime_type": mimetypes.guess_type(material_path.name)[0]
                                or "application/octet-stream",
                                "ocr_source_dimensions": {"width": width, "height": height},
                                "ocr_input_transformed": True,
                                "path": str(material_path),
                                "sha256": source_sha256,
                            }
                finally:
                    current.close()
        finally:
            background.close()
        raise AgentReadOnlyViolationError("image_ocr_bounded_render_too_large")
    except AgentReadOnlyViolationError:
        raise
    except Exception as exc:
        raise AgentReadOnlyViolationError("image_ocr_bounded_render_failed") from exc


def _read_pdf_material(material_file, material_path: Path) -> dict[str, object]:
    try:
        reader = PdfReader(material_file)
    except (PdfReadError, ValueError) as exc:
        raise AgentReadOnlyViolationError("pdf_material_invalid") from exc
    if reader.is_encrypted:
        raise AgentReadOnlyViolationError("pdf_material_encrypted")
    if not reader.pages:
        raise AgentReadOnlyViolationError("pdf_material_empty_pages")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise AgentReadOnlyViolationError("pdf_material_page_limit_exceeded")

    extracted_pages: list[dict[str, object]] = []
    ocr_page_numbers: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except (PdfReadError, ValueError):
            page_text = ""
        extracted_pages.append({"index": index, "text": page_text})
        if not page_text.strip():
            ocr_page_numbers.append(index)

    if ocr_page_numbers:
        material_file.seek(0)
        digest = hashlib.file_digest(material_file, "sha256").hexdigest()
        material_file.seek(0)
        binary = material_file.read(MAX_OCR_AGENT_INPUT_BYTES + 1)
        result = {
            "format": "pdf",
            "page_count": len(reader.pages),
            "pages": extracted_pages,
            "ocr_required": True,
            "ocr_page_numbers": ocr_page_numbers,
            "path": str(material_path),
            "sha256": digest,
        }
        if len(binary) <= MAX_OCR_AGENT_INPUT_BYTES:
            result["ocr_input_base64"] = base64.b64encode(binary).decode("ascii")
            result["ocr_mime_type"] = "application/pdf"
        else:
            result["ocr_page_image_required"] = True
        return result

    previews: list[dict[str, object]] = []
    remaining_chars = MAX_TEXT_MATERIAL_BYTES
    truncated_text = False
    for page in extracted_pages:
        text = page["text"]
        if len(text) > remaining_chars:
            truncated_text = True
            text = text[:remaining_chars]
        previews.append({**page, "text": text})
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
        "truncated": truncated_text or len(previews) < len(reader.pages),
        "path": str(material_path),
        "sha256": digest,
    }


def _ocr_input_for_agent(material_file, material_path: Path, *, file_format: str):
    material_file.seek(0)
    binary = material_file.read(MAX_OCR_AGENT_INPUT_BYTES + 1)
    if len(binary) > MAX_OCR_AGENT_INPUT_BYTES:
        raise AgentReadOnlyViolationError("image_material_ocr_input_too_large")
    return {
        "format": file_format,
        "ocr_required": True,
        "ocr_input_base64": base64.b64encode(binary).decode("ascii"),
        "ocr_mime_type": mimetypes.guess_type(material_path.name)[0]
        or "application/octet-stream",
        "path": str(material_path),
        "sha256": hashlib.sha256(binary).hexdigest(),
    }


def _pdf_page_ocr_input(
    material_path: Path, *, page_number: int, source_sha256: str,
) -> dict[str, object]:
    """Render one original PDF page as a bounded image for the OCR MCP."""
    if type(page_number) is not int or page_number < 1:
        raise AgentReadOnlyViolationError("pdf_ocr_page_number_invalid")
    document = None
    page = None
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(material_path))
        if page_number > len(document):
            raise AgentReadOnlyViolationError("pdf_ocr_page_number_invalid")
        page = document[page_number - 1]
        page_width, page_height = page.get_size()
        scale = min(1.5, 2200 / max(page_width, page_height))
        image = page.render(scale=scale).to_pil().convert("RGB")
        for scale in (1.0, 0.85, 0.7):
            current = image.copy()
            if scale < 1.0:
                current.thumbnail((int(image.width * scale), int(image.height * scale)))
            for quality in (85, 75, 65):
                encoded = BytesIO()
                current.save(encoded, format="JPEG", quality=quality, optimize=True)
                payload = encoded.getvalue()
                if len(payload) <= MAX_OCR_AGENT_INPUT_BYTES:
                    return {
                        "format": "image",
                        "ocr_required": True,
                        "ocr_input_base64": base64.b64encode(payload).decode("ascii"),
                        "ocr_mime_type": "image/jpeg",
                        "ocr_original_page_number": page_number,
                        "ocr_source_sha256": source_sha256,
                    }
        raise AgentReadOnlyViolationError("pdf_ocr_page_image_too_large")
    except AgentReadOnlyViolationError:
        raise
    except Exception as exc:
        raise AgentReadOnlyViolationError("pdf_ocr_page_render_failed") from exc
    finally:
        if page is not None:
            page.close()
        if document is not None:
            document.close()


def _download_dingtalk_drive_file(
    *,
    dws,
    node_id: str,
    space_id: str,
    file_name: str,
    material_root: Path,
) -> dict[str, object]:
    """Download one named DingTalk Drive node and extract its bounded contents."""
    if not node_id.strip() or not space_id.strip():
        raise AgentReadOnlyViolationError("drive_file_identity_required")
    if not _task_file_component(file_name) or len(file_name) > 255:
        raise AgentReadOnlyViolationError("drive_file_name_invalid")
    material_root.mkdir(parents=True, exist_ok=True)
    is_pdf = Path(file_name).suffix.casefold() == ".pdf"
    max_bytes = MAX_PDF_BYTES if is_pdf else MAX_SPREADSHEET_BYTES
    contents = dws.download_drive_file(
        node_id, file_name=file_name, space_id=space_id,
        max_bytes=max_bytes,
    )
    if len(contents) > max_bytes:
        raise AgentReadOnlyViolationError("drive_file_too_large")
    package_source = BytesIO(contents)
    if zipfile.is_zipfile(package_source):
        with zipfile.ZipFile(package_source) as package:
            if sum(member.file_size for member in package.infolist()) > MAX_SPREADSHEET_BYTES:
                raise AgentReadOnlyViolationError("drive_file_expanded_too_large")
    extension = Path(file_name).suffix
    suffix = extension if extension[1:].isalnum() and len(extension) <= 16 else ""
    prefix = hashlib.sha256(node_id.encode()).hexdigest()[:12] + "-"
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=prefix, suffix=suffix, dir=material_root, delete=False
    ) as material_file:
        material_file.write(contents)
        material_path = Path(material_file.name)
    try:
        extracted = read_text_file(str(material_path))
        result = {
            "node_id": node_id,
            "space_id": space_id,
            "file_name": file_name,
            **extracted,
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
        if len(to_json(result, indent=2)) > MAX_MCP_MATERIAL_RESPONSE_BYTES:
            raise AgentReadOnlyViolationError("drive_material_response_too_large")
        return result
    except BaseException:
        material_path.unlink()
        raise


def _download_dingtalk_oa_comment_attachment(
    *,
    dws,
    process_instance_id: str,
    file_id: str,
    file_name: str,
    material_root: Path,
) -> dict[str, object]:
    """Read one exact OA operation-record attachment through its premium endpoint."""
    if not process_instance_id.strip() or not file_id.strip():
        raise AgentReadOnlyViolationError("oa_comment_attachment_identity_required")
    if not _task_file_component(file_name) or len(file_name) > 255:
        raise AgentReadOnlyViolationError("oa_comment_attachment_file_name_invalid")
    is_pdf = Path(file_name).suffix.casefold() == ".pdf"
    max_bytes = MAX_PDF_BYTES if is_pdf else MAX_SPREADSHEET_BYTES
    contents = dws.download_oa_process_comment_attachment(
        process_instance_id, file_id,
    )
    return _materialize_oa_attachment(
        contents, process_instance_id=process_instance_id, file_id=file_id,
        file_name=file_name, material_root=material_root,
        error_prefix="oa_comment_attachment", max_bytes=max_bytes,
    )


def _download_dingtalk_oa_process_attachment(
    *, dws, process_instance_id: str, file_id: str, file_name: str,
    material_root: Path,
) -> dict[str, object]:
    """Read an OA form attachment without requiring Drive node permissions."""
    if not process_instance_id.strip() or not file_id.strip():
        raise AgentReadOnlyViolationError("oa_process_attachment_identity_required")
    if not _task_file_component(file_name) or len(file_name) > 255:
        raise AgentReadOnlyViolationError("oa_process_attachment_file_name_invalid")
    suffix = Path(file_name).suffix.casefold()
    is_pdf = suffix == ".pdf"
    max_bytes = (
        MAX_PDF_BYTES if is_pdf
        else MAX_PPTX_BYTES if suffix == ".pptx"
        else MAX_SPREADSHEET_BYTES
    )
    contents = dws.download_oa_process_attachment_via_cli(
        process_instance_id, file_id, max_bytes=max_bytes,
    )
    return _materialize_oa_attachment(
        contents, process_instance_id=process_instance_id, file_id=file_id,
        file_name=file_name, material_root=material_root,
        error_prefix="oa_process_attachment", max_bytes=max_bytes,
    )


def _materialize_oa_attachment(
    contents: bytes, *, process_instance_id: str, file_id: str,
    file_name: str, material_root: Path, error_prefix: str, max_bytes: int,
) -> dict[str, object]:
    material_root.mkdir(parents=True, exist_ok=True)
    if len(contents) > max_bytes:
        raise AgentReadOnlyViolationError(f"{error_prefix}_file_too_large")
    is_pptx = Path(file_name).suffix.casefold() == ".pptx"
    package_source = BytesIO(contents)
    if zipfile.is_zipfile(package_source):
        with zipfile.ZipFile(package_source) as package:
            expanded_limit = (
                MAX_PPTX_EXPANDED_BYTES if is_pptx else MAX_SPREADSHEET_BYTES
            )
            if sum(member.file_size for member in package.infolist()) > expanded_limit:
                raise AgentReadOnlyViolationError(
                    f"{error_prefix}_expanded_package_too_large"
                )
    extension = Path(file_name).suffix
    suffix = extension if extension[1:].isalnum() and len(extension) <= 16 else ""
    prefix = hashlib.sha256(file_id.encode()).hexdigest()[:12] + "-"
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=prefix, suffix=suffix, dir=material_root, delete=False
    ) as material_file:
        material_file.write(contents)
        material_path = Path(material_file.name)
    try:
        extracted = read_text_file(str(material_path))
        result = {
            "process_instance_id": process_instance_id,
            "file_id": file_id,
            "file_name": file_name,
            **extracted,
            "path": str(material_path),
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
        if len(to_json(result, indent=2)) > MAX_MCP_MATERIAL_RESPONSE_BYTES:
            raise AgentReadOnlyViolationError(f"{error_prefix}_response_too_large")
        return result
    except BaseException:
        material_path.unlink()
        raise


def _contains_exact_form_value(value: object, expected: str) -> bool:
    """Match an exact string in nested OA form data, including JSON values."""
    if isinstance(value, str):
        if value == expected:
            return True
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return False
        return decoded != value and _contains_exact_form_value(decoded, expected)
    if isinstance(value, dict):
        return any(_contains_exact_form_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_form_value(item, expected) for item in value)
    return False


def _download_dingtalk_oa_comment_image(
    *,
    process_instance_id: str,
    image_url: str,
    material_root: Path,
) -> dict[str, object]:
    """Read one exact DingTalk OA comment image URL into bounded OCR input."""
    if not process_instance_id.strip():
        raise AgentReadOnlyViolationError("oa_comment_image_identity_required")
    try:
        parsed = urlsplit(image_url)
        port = parsed.port
    except ValueError as exc:
        raise AgentReadOnlyViolationError("oa_comment_image_url_invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "static.dingtalk.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or not parsed.path.startswith("/media/")
    ):
        raise AgentReadOnlyViolationError("oa_comment_image_url_invalid")

    class RejectRedirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    material_root.mkdir(parents=True, exist_ok=True)
    request = Request(image_url, headers={"User-Agent": "ceo-agent-service"})
    try:
        with build_opener(RejectRedirects()).open(request, timeout=15) as response:
            if response.status != 200:
                raise AgentReadOnlyViolationError(
                    f"oa_comment_image_http_{response.status}"
                )
            if urlsplit(response.geturl()).hostname != "static.dingtalk.com":
                raise AgentReadOnlyViolationError("oa_comment_image_origin_changed")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                raise AgentReadOnlyViolationError("oa_comment_image_content_type_invalid")
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    declared_bytes = int(declared_size)
                except ValueError as exc:
                    raise AgentReadOnlyViolationError(
                        "oa_comment_image_content_length_invalid"
                    ) from exc
                if declared_bytes > MAX_OA_COMMENT_IMAGE_BYTES:
                    raise AgentReadOnlyViolationError("oa_comment_image_file_too_large")
            contents = response.read(MAX_OA_COMMENT_IMAGE_BYTES + 1)
    except HTTPError as exc:
        raise AgentReadOnlyViolationError(
            f"oa_comment_image_http_{exc.code}"
        ) from exc
    except OSError as exc:
        raise AgentReadOnlyViolationError("oa_comment_image_download_failed") from exc
    if len(contents) > MAX_OA_COMMENT_IMAGE_BYTES:
        raise AgentReadOnlyViolationError("oa_comment_image_file_too_large")

    suffix = mimetypes.guess_extension(content_type) or ".img"
    digest = hashlib.sha256(contents).hexdigest()
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=digest[:12] + "-", suffix=suffix,
        dir=material_root, delete=False,
    ) as material_file:
        material_file.write(contents)
        material_path = Path(material_file.name)
    try:
        extracted = read_text_file(str(material_path))
        result = {
            "process_instance_id": process_instance_id,
            "image_url": image_url,
            "file_name": material_path.name,
            **extracted,
            "sha256": digest,
        }
        if len(to_json(result, indent=2)) > MAX_MCP_MATERIAL_RESPONSE_BYTES:
            raise AgentReadOnlyViolationError("oa_comment_image_response_too_large")
        return result
    except BaseException:
        material_path.unlink()
        raise


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
    truncated_text = False
    ocr_page_sources: list[dict[str, object]] = []
    slide_names = _presentation_slide_names(workbook)
    for index, name in enumerate(slide_names, start=1):
        root = ElementTree.fromstring(workbook.read(name))
        text = "\n".join(
            node.text or ""
            for node in root.iter()
            if node.tag.endswith("}t") and node.text
        )
        truncated_text |= len(text) > remaining_chars
        text = text[:remaining_chars]
        remaining_chars -= len(text)
        slides.append({"index": index, "text": text})
        for member in _presentation_slide_images(workbook, name, root):
            ocr_page_sources.append({
                "page_number": len(ocr_page_sources) + 1,
                "slide_number": index,
                "member": member,
            })
        if remaining_chars == 0:
            break
    return {
        "format": "pptx",
        "slides": slides,
        "ocr_required": bool(ocr_page_sources),
        "ocr_page_numbers": [entry["page_number"] for entry in ocr_page_sources],
        "ocr_page_sources": ocr_page_sources,
        "truncated": truncated_text or len(slides) < len(slide_names),
    }


def _presentation_slide_names(workbook: zipfile.ZipFile) -> list[str]:
    names = [
        name for name in workbook.namelist()
        if name.startswith("ppt/slides/slide") and name.endswith(".xml")
    ]

    def slide_number(name: str) -> int:
        suffix = Path(name).stem.removeprefix("slide")
        if not suffix.isdecimal():
            raise AgentReadOnlyViolationError("pptx_slide_name_invalid")
        return int(suffix)

    return sorted(names, key=slide_number)


def _presentation_slide_images(
    workbook: zipfile.ZipFile, slide_name: str, slide_root: ElementTree.Element,
) -> list[str]:
    relationships_name = posixpath.join(
        posixpath.dirname(slide_name), "_rels", posixpath.basename(slide_name) + ".rels",
    )
    try:
        relationships = ElementTree.fromstring(workbook.read(relationships_name))
    except KeyError:
        return []
    targets = {
        relation.attrib.get("Id", ""): relation.attrib.get("Target", "")
        for relation in relationships
        if relation.tag.endswith("}Relationship")
        and relation.attrib.get("Type", "").endswith("/image")
        and relation.attrib.get("TargetMode") != "External"
    }
    members: list[str] = []
    for node in slide_root.iter():
        if not node.tag.endswith("}blip"):
            continue
        relationship_id = next(
            (value for key, value in node.attrib.items() if key.endswith("}embed")),
            "",
        )
        target = targets.get(relationship_id, "")
        if not target:
            continue
        member = posixpath.normpath(posixpath.join(posixpath.dirname(slide_name), target))
        if member.startswith("../") or member.startswith("/"):
            raise AgentReadOnlyViolationError("pptx_image_path_invalid")
        members.append(member)
    return members


def _pptx_page_ocr_input(
    material_path: Path, *, page_number: int, source_sha256: str,
) -> dict[str, object]:
    """Render one embedded presentation image as a bounded OCR MCP input."""
    if type(page_number) is not int or page_number < 1:
        raise AgentReadOnlyViolationError("pptx_ocr_page_number_invalid")
    try:
        with material_path.open("rb") as source, _MaterialZipFile(
            source, max_expanded_bytes=MAX_PPTX_EXPANDED_BYTES,
        ) as workbook:
            slide_names = _presentation_slide_names(workbook)
            image_sources: list[tuple[int, str]] = []
            for slide_number, slide_name in enumerate(slide_names, start=1):
                root = ElementTree.fromstring(workbook.read(slide_name))
                image_sources.extend(
                    (slide_number, member)
                    for member in _presentation_slide_images(workbook, slide_name, root)
                )
            if page_number > len(image_sources):
                raise AgentReadOnlyViolationError("pptx_ocr_page_number_invalid")
            slide_number, image_member = image_sources[page_number - 1]
            image_bytes = workbook.read(image_member)
    except AgentReadOnlyViolationError:
        raise
    except Exception as exc:
        raise AgentReadOnlyViolationError("pptx_ocr_page_read_failed") from exc

    try:
        from PIL import Image, ImageOps

        with Image.open(BytesIO(image_bytes)) as source_image:
            image = ImageOps.exif_transpose(source_image).convert("RGB")
        if max(image.size) > 3000:
            image.thumbnail((3000, 3000))
        try:
            for scale in (1.0, 0.85, 0.7, 0.55):
                current = image.copy()
                try:
                    if scale < 1.0:
                        current.thumbnail((int(image.width * scale), int(image.height * scale)))
                    for quality in (88, 80, 72, 64):
                        encoded = BytesIO()
                        current.save(encoded, format="JPEG", quality=quality, optimize=True)
                        payload = encoded.getvalue()
                        if len(payload) <= MAX_OCR_AGENT_INPUT_BYTES:
                            return {
                                "format": "image",
                                "ocr_required": True,
                                "ocr_input_base64": base64.b64encode(payload).decode("ascii"),
                                "ocr_mime_type": "image/jpeg",
                                "ocr_original_page_number": page_number,
                                "ocr_source_slide_number": slide_number,
                                "ocr_source_sha256": source_sha256,
                            }
                finally:
                    current.close()
        finally:
            image.close()
    except AgentReadOnlyViolationError:
        raise
    except Exception as exc:
        raise AgentReadOnlyViolationError("pptx_ocr_page_render_failed") from exc
    raise AgentReadOnlyViolationError("pptx_ocr_page_image_too_large")


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
    path: str | None = None, name: str | None = None,
    query: str | None = None,
) -> dict[str, object]:
    """Read an installed Agent skill or its referenced Markdown safely.

    With no argument, return all names and paths without descriptions. `query`
    returns full metadata for entries whose name/description contains every query term. `path` identifies
    Markdown inside an installed Skill; `name` reads one exact SKILL.md.
    """
    if sum(value is not None for value in (path, name, query)) > 1:
        raise ValueError("read_skill accepts only one of path, name or query")
    from app import agent_skill_usage
    from app.business_skills import (
        installed_skill_catalog,
        render_skill_catalog_metadata,
        resolve_installed_skill_name,
    )

    if path is None and name is None:
        if query is not None:
            terms = query.casefold().split()
            if not terms:
                raise ValueError("read_skill query must not be empty")
        catalog = installed_skill_catalog(roots=agent_skill_usage.AGENT_SKILL_ROOTS)
        if query is not None:
            catalog = tuple(
                entry for entry in catalog
                if all(
                    term in f"{entry.name} {entry.description}".casefold()
                    for term in terms
                )
            )
        return {
            "catalog": render_skill_catalog_metadata(
                catalog, include_descriptions=query is not None
            )
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
    """Read bounded user-provided text, PDF, workbook, or presentation material.

    For frozen task Skills use read_task_skill. For files in the current task
    generation use list_task_artifacts and read_task_artifact. Do not use this
    tool to inspect repository code or local Skill implementation files.
    """
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
                "select execution_generation from tasks where id=? and kind='reply'", (task_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("task file binding is unavailable") from exc
    if row is None or not _task_file_component(row[0]):
        raise ValueError("task file generation is unavailable")
    return row[0]


def _current_task_channel(db_path: Path | None, task_id: int | None) -> str | None:
    """Read the channel bound to the current Task input without Store migration."""
    if db_path is None or type(task_id) is not int or task_id <= 0:
        return None
    database = Path(db_path).resolve()
    if not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True)) as connection:
            row = connection.execute(
                """select input.channel from tasks task
                   join task_inputs input on input.id=task.current_input_id
                   where task.id=? and task.kind='reply'""",
                (task_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("task file channel is unavailable") from exc
    if row is None or not isinstance(row[0], str) or not row[0]:
        return None
    return row[0]


def _bound_wechat_conversation(
    db_path: Path | None, task_id: int | None, generation: str | None,
) -> tuple[str, str]:
    """The conversation id and type of the claimed WeChat task.

    A WeChat task may read only its own conversation, so the tool takes no
    conversation argument.
    """
    if db_path is None or task_id is None:
        raise ValueError("a WeChat read needs a claimed WeChat task")
    from app.store import NATIVE_REPLY_TASK_CLAIMED_SQL

    database = Path(db_path).resolve()
    with closing(sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "select tasks.execution_generation, tasks.channel, tasks.single_chat, "
            "tasks.conversation_id "
            f"from ({NATIVE_REPLY_TASK_CLAIMED_SQL}) tasks where tasks.id=?",
            (task_id,),
        ).fetchone()
    if row is None or row[0] != generation:
        raise ValueError("bound task generation or input changed")
    if row[1] != "wechat" or not row[3]:
        raise ValueError("the bound task is not a WeChat conversation")
    return row[3], "direct" if row[2] else "group"


def _bound_direct_chat_sender(
    db_path: Path | None,
    task_id: int | None,
    generation: str | None,
    conversation_id: str,
) -> str | None:
    """Use the claimed task's exact trigger as a direct-chat read target."""
    if db_path is None or task_id is None:
        return None
    database = Path(db_path).resolve()
    with closing(sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "select tasks.execution_generation, inputs.channel, inputs.single_chat, "
            "inputs.conversation_id, inputs.trigger_message_id, "
            "inputs.trigger_message_json, inputs.input_compacted "
            "from tasks join task_inputs inputs "
            "on inputs.id=tasks.claimed_input_id and inputs.task_id=tasks.id "
            "where tasks.id=? and tasks.kind='reply'",
            (task_id,),
        ).fetchone()
    if row is None or row[0] != generation:
        raise ValueError("bound task generation or input changed")
    if row[1] != "dingtalk" or not row[2] or row[3] != conversation_id:
        return None
    if row[6]:
        raise ValueError("bound direct trigger identity is unavailable")
    from app.dingtalk_models import DingTalkMessage

    source = DingTalkMessage.model_validate_json(row[5])
    if (
        source.open_message_id != row[4]
        or source.open_conversation_id != conversation_id
        or not source.sender_open_dingtalk_id
    ):
        raise ValueError("bound direct trigger identity does not match the task")
    return source.sender_open_dingtalk_id


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
                "select tasks.execution_generation, "
                "case when inputs.input_compacted=1 "
                "then json_extract(inputs.input_provenance_json,'$.source') "
                "else inputs.trigger_message_json end "
                "from tasks join task_inputs inputs "
                "on inputs.id=tasks.claimed_input_id and inputs.task_id=tasks.id "
                "where tasks.id=? and tasks.kind='reply'",
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


def _approved_consumer_skill_script(name: str, script: str) -> Path:
    """Resolve a reviewed script from the service-managed Skill package.

    The task binding selects a Skill, not every executable shipped with it.
    A package's explicit Consumer entrypoints and script-source digest
    keep sender/approval helpers outside the Consumer tool surface.
    """
    source = CONSUMER_SCRIPT_SOURCE_ROOT / name
    installed = Path.home() / ".agents" / "skills" / name
    manifest_path = source / "consumer-scripts.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Consumer Skill script is not reviewed") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
        or not isinstance(manifest.get("entrypoints"), list)
        or script not in manifest["entrypoints"]
        or not isinstance(manifest.get("script_tree_sha256"), str)
    ):
        raise ValueError("Consumer Skill script is not reviewed")

    def script_tree_digest(root: Path) -> str:
        scripts_root = root / "scripts"
        if not scripts_root.is_dir() or scripts_root.is_symlink():
            raise ValueError("Consumer Skill scripts are unavailable")
        digest = hashlib.sha256()
        paths = sorted({
            path for path in (
                *scripts_root.rglob("*"),
                *(path for path in root.rglob("*.py")
                  if path.relative_to(root).parts[0] != "tests"),
            )
            if "__pycache__" not in path.relative_to(root).parts
            and (path.is_file() or path.is_symlink())
        })
        if not paths:
            raise ValueError("Consumer Skill scripts are unavailable")
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Consumer Skill scripts are unavailable")
            relative = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    selected = (installed / script).resolve()
    if (
        not selected.is_relative_to((installed / "scripts").resolve())
        or selected.suffix != ".py"
        or not selected.is_file()
        or script_tree_digest(source) != manifest["script_tree_sha256"]
        or script_tree_digest(installed) != manifest["script_tree_sha256"]
    ):
        raise ValueError("Consumer Skill script is not reviewed")
    return selected


def _task_file_root(
    db_path: Path | None, task_id: int | None, generation: str | None, *, create: bool,
) -> Path:
    if generation is None:
        raise ValueError("task file binding is missing")
    if _current_task_generation(db_path, task_id) != generation:
        raise ValueError("task file generation changed")
    migrate_legacy_task_artifacts(task_id)
    return task_artifact_directory(task_id, generation, create=create)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _write_task_file(root: Path, name: str, data: bytes) -> None:
    path = _task_file_path(root, name)
    descriptor, temporary = tempfile.mkstemp(prefix=_TASK_ARTIFACT_TEMP_PREFIX, dir=root)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def _read_task_file(
    root: Path, name: str, offset: int = 0, max_chars: int = 20000
) -> dict[str, object]:
    path = _task_file_path(root, name)
    if not path.is_file():
        raise ValueError("task file is unavailable")
    data = path.read_bytes()
    if len(data) > MAX_TASK_FILE_BYTES:
        raise ValueError("task file is too large")
    text = data.decode("utf-8")
    if type(offset) is not int or offset < 0 or not 1 <= max_chars <= 20000:
        raise ValueError("invalid artifact read interval")
    item = {
        "name": name,
        "content": text[offset : offset + max_chars],
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    item.update(
        offset=offset,
        total_chars=len(text),
        next_offset=offset + max_chars if offset + max_chars < len(text) else None,
    )
    if len(json.dumps(item).encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("task file readback is too large")
    return item


def _bound_scheduled_run(db_path: Path | None, task_id: int | None):
    from app.store import AutoReplyStore

    if db_path is None or type(task_id) is not int or task_id <= 0:
        raise ValueError("scheduled task binding is missing")
    store = AutoReplyStore(db_path)
    task = store.get_reply_task(task_id)
    if task is None or task.channel != "scheduled":
        raise ValueError("scheduled task required")
    run = store.get_scheduled_task_run_for_reply_execution(task_id)
    if run is None:
        raise ValueError("scheduled run is missing")
    return store, run


def _scheduled_read_cutoff(db_path: Path | None, task_id: int | None):
    """Return the trigger time for scheduled tasks, leaving other tasks unchanged."""
    if db_path is None or type(task_id) is not int or task_id <= 0:
        return None
    from app.store import AutoReplyStore

    store = AutoReplyStore(db_path)
    task = store.get_reply_task(task_id)
    if task is None or task.channel != "scheduled":
        return None
    run = store.get_scheduled_task_run_for_reply_execution(task_id)
    if run is None:
        raise ValueError("scheduled run is missing")
    return run.scheduled_for


def _validate_scheduled_read_window(
    db_path: Path | None,
    task_id: int | None,
    start: str,
    end: str,
    *,
    scheduled_for: datetime | None = None,
) -> None:
    cutoff = scheduled_for or _scheduled_read_cutoff(db_path, task_id)
    if cutoff is None:
        return
    from app.service_activity import validate_scheduled_read_window

    validate_scheduled_read_window(
        datetime.fromisoformat(start), datetime.fromisoformat(end), cutoff
    )


def _write_document_artifact(
    *,
    root: Path,
    artifact_name: str,
    title: str,
    doc_format: str,
    node_id: str = "",
    parent_id: str = "",
    workspace_id: str = "",
    expected_revision: int | None = None,
) -> dict[str, object]:
    """Submit an exact task artifact to an explicit provider target."""
    from app.dws_client import DwsClient, DwsError

    material = _task_file_path(root, artifact_name)
    if not material.is_file() or material.stat().st_size > MAX_TASK_FILE_BYTES:
        raise ValueError("document artifact is unavailable or too large")
    content = material.read_bytes().decode("utf-8")
    if not content.strip() or not title.strip():
        raise ValueError("document title and content are required")
    if doc_format not in {"markdown", "jsonml"}:
        raise ValueError("unsupported document format")
    dws = DwsClient()
    if doc_format == "jsonml":
        parsed = dws.validate_report_jsonml(content)
        assessment = parsed.get(
            "assessment", (parsed.get("data") or {}).get("assessment")
        )
        if assessment != "passed":
            raise ValueError("document JSONML parser did not pass")
    version = None
    operation = "updated" if node_id else "created"
    result = None
    try:
        if node_id:
            if parent_id:
                raise ValueError("document update takes node_id, not parent_id")
            if doc_format == "jsonml":
                if type(expected_revision) is not int or expected_revision < 0:
                    raise ValueError("JSONML update requires expected_revision")
                version = dws.save_report_document_version(node_id)
            result = dws.overwrite_report_document(
                node_id=node_id,
                content=content,
                doc_format=doc_format,
                expected_revision=expected_revision,
            )
        else:
            if not parent_id or not workspace_id or expected_revision is not None:
                raise ValueError(
                    "document creation requires parent_id and workspace_id without revision"
                )
            def matching_nodes():
                payload = dws.run_json(
                    [
                        dws.dws_bin,
                        "doc",
                        "+list",
                        "--folder",
                        parent_id,
                        "--page-all",
                        "--format",
                        "json",
                    ]
                )
                if (
                    payload.get("status") != "success"
                    or payload.get("complete") is not True
                    or payload.get("hasMore") is not False
                    or payload.get("failures") != []
                    or not isinstance(payload.get("nodes"), list)
                ):
                    raise ValueError("document parent listing is incomplete")
                return [item for item in payload["nodes"] if item.get("name") == title]

            if matching_nodes():
                raise ValueError("document target appeared before creation; reread target")
            result = dws.create_report_document(
                name=title, content=content, doc_format=doc_format, folder_id=parent_id
            )
            created_data = result.get("data", result)
            if isinstance(created_data, dict) and isinstance(created_data.get("nodeId"), str):
                node_id = created_data["nodeId"]
            found = matching_nodes()
            if len(found) != 1:
                raise ValueError("created document target is ambiguous")
            node_id = found[0]["nodeId"]
    except (DwsError, ValueError) as exc:
        provider_error = exc.provider_error if isinstance(exc, DwsError) else {}
        details = provider_error.get("details", {})
        if not isinstance(details, dict):
            details = {}
        partial_write = (
            details.get("contractVersion") == "doc.operation.v1"
            and details.get("status") == "partial_success"
            and provider_error.get("operation") == ("doc.update" if operation == "updated" else "doc.create")
        )
        if result is None and not partial_write:
            raise
        data = details.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("nodeId"), str):
            node_id = data["nodeId"] or node_id
        return {
            "ok": True,
            "status": "success",
            "operation": operation,
            "node_id": node_id,
            "title": title,
            "provider_result": result,
            "error": provider_error or {"message": str(exc)},
            "saved_version": version,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "readback": None,
        }
    return {
        "ok": True,
        "status": "success",
        "operation": operation,
        "node_id": node_id,
        "title": title,
        "provider_result": result,
        "saved_version": version,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "readback": None,
    }


def build_role_server(
    role: str,
    *,
    task_id: int | None = None,
    db_path: Path | None = None,
    execution_generation: str | None = None,
) -> FastMCP:
    """Expose only the operations owned by one Agent role."""
    if role not in {"consumer", "audit"}:
        raise ValueError("unsupported agent role")
    artifact_generation = (
        _current_task_generation(db_path, task_id)
        if task_id is not None and db_path is not None
        else None
    )
    if execution_generation is not None and artifact_generation != execution_generation:
        raise ValueError("task file generation changed")
    if (
        role == "consumer"
        and task_id is not None
        and db_path is not None
        and _current_task_channel(db_path, task_id) == "scheduled"
    ):
        from app.task_artifacts import (
            carry_forward_task_artifacts,
            migrate_legacy_task_artifacts,
        )

        migrate_legacy_task_artifacts(task_id)
        carry_forward_task_artifacts(task_id, artifact_generation)
    bound = FastMCP(
        "agent_cli",
        instructions="Task-bound Agent reads, artifacts and explicit documents",
    )
    read = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    bound.add_tool(read_skill_tool, name="read_skill", annotations=read)
    bound.add_tool(read_text_file_tool, name="read_text_file", annotations=read)
    bound.add_tool(read_spreadsheet_tool, name="read_spreadsheet", annotations=read)
    drive_material_directory: tempfile.TemporaryDirectory[str] | None = None
    drive_material_paths: dict[tuple[str, str, str], Path] = {}
    oa_comment_material_paths: dict[tuple[str, str, str], Path] = {}
    oa_process_material_paths: dict[tuple[str, str, str], Path] = {}

    def read_dingtalk_drive_file(
        node_id: str, space_id: str, file_name: str,
        page_number: int | None = None,
    ) -> dict[str, object]:
        """Read the body of one exact DingTalk Drive attachment for OA review.

        Use after drive metadata identifies an accessible attachment node. Returns
        bounded image, PDF, workbook, presentation, or text contents. For a
        scanned PDF above the inline OCR input limit, first read it normally,
        then pass each listed one-based ocr_page_numbers value as page_number to
        receive a bounded JPEG for ai_services.ocr_document. Map its OCR text
        back to ocr_original_page_number. The exact PDF is retained only for
        this MCP process lifetime. OCR failures are technical read failures.
        """
        nonlocal drive_material_directory
        from app.dws_client import DwsClient
        if drive_material_directory is None:
            drive_material_directory = tempfile.TemporaryDirectory(
                prefix="oa-review-"
            )
            atexit.register(drive_material_directory.cleanup)
        key = (node_id, space_id, file_name)
        if page_number is not None:
            material_path = drive_material_paths.get(key)
            if material_path is None or not material_path.is_file():
                result = _download_dingtalk_drive_file(
                    dws=DwsClient(), node_id=node_id, space_id=space_id,
                    file_name=file_name,
                    material_root=Path(drive_material_directory.name),
                )
                material_path = Path(str(result["path"]))
                drive_material_paths[key] = material_path
            if material_path.suffix.casefold() != ".pdf":
                raise ValueError("page_number is supported only for PDF attachments")
            with material_path.open("rb") as source:
                source_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
            return _pdf_page_ocr_input(
                material_path, page_number=page_number,
                source_sha256=source_sha256,
            )
        result = _download_dingtalk_drive_file(
            dws=DwsClient(),
            node_id=node_id,
            space_id=space_id,
            file_name=file_name,
            material_root=Path(drive_material_directory.name),
        )
        drive_material_paths[key] = Path(str(result["path"]))
        return result

    bound.add_tool(
        read_dingtalk_drive_file,
        name="read_dingtalk_drive_file",
        annotations=read,
    )

    def read_dingtalk_oa_process_attachment(
        process_instance_id: str,
        file_id: str,
        file_name: str,
        page_number: int | None = None,
    ) -> dict[str, object]:
        """Read one exact OA form attachment through the process-scoped download.

        Use the process instance and file ID from read_dingtalk_oa, even when
        generic Drive access is denied. Scanned PDFs and image-bearing PPTX
        files return one-based ocr_page_numbers; pass a listed page_number for
        bounded OCR input. PPTX page numbers follow embedded-image reading order.
        """
        nonlocal drive_material_directory
        if not process_instance_id.strip() or not file_id.strip():
            raise AgentReadOnlyViolationError("oa_process_attachment_identity_required")
        if not _task_file_component(file_name) or len(file_name) > 255:
            raise AgentReadOnlyViolationError("oa_process_attachment_file_name_invalid")
        key = (process_instance_id, file_id, file_name)
        if page_number is not None:
            material_path = oa_process_material_paths.get(key)
            if material_path is None or not material_path.is_file():
                raise AgentReadOnlyViolationError("oa_process_attachment_page_source_unavailable")
            with material_path.open("rb") as source:
                source_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
            if material_path.suffix.casefold() == ".pdf":
                return _pdf_page_ocr_input(
                    material_path, page_number=page_number,
                    source_sha256=source_sha256,
                )
            if material_path.suffix.casefold() == ".pptx":
                return _pptx_page_ocr_input(
                    material_path, page_number=page_number,
                    source_sha256=source_sha256,
                )
            raise AgentReadOnlyViolationError("oa_process_attachment_page_requires_pdf_or_pptx")
        if drive_material_directory is None:
            drive_material_directory = tempfile.TemporaryDirectory(prefix="oa-review-")
            atexit.register(drive_material_directory.cleanup)
        from app.dws_client import DwsClient
        result = _download_dingtalk_oa_process_attachment(
            dws=DwsClient(), process_instance_id=process_instance_id,
            file_id=file_id, file_name=file_name,
            material_root=Path(drive_material_directory.name),
        )
        oa_process_material_paths[key] = Path(str(result["path"]))
        return result

    bound.add_tool(
        read_dingtalk_oa_process_attachment,
        name="read_dingtalk_oa_process_attachment",
        annotations=read,
    )

    def read_dingtalk_oa_form_image(
        process_instance_id: str,
        image_url: str,
    ) -> dict[str, object]:
        """Read a DingTalk image URL that is present in this exact OA form.

        Use when the live form detail contains a static DingTalk media URL
        instead of a file ID. The exact URL must occur in the form values for
        the supplied process instance; only then is it fetched through the
        bounded image and OCR-input path. This is read-only and does not alter
        attachment permissions or OA state.
        """
        nonlocal drive_material_directory
        if not process_instance_id.strip():
            raise AgentReadOnlyViolationError("oa_form_image_process_identity_required")
        from app.dws_client import DwsClient
        detail = DwsClient().read_oa_approval_detail(process_instance_id)
        process = detail.get("result") if isinstance(detail, dict) else None
        if (
            not isinstance(process, dict)
            or process.get("processInstanceId") != process_instance_id
            or not isinstance(process.get("formValueVOS"), list)
        ):
            raise AgentReadOnlyViolationError("oa_form_image_process_readback_mismatch")
        if not _contains_exact_form_value(process["formValueVOS"], image_url):
            raise AgentReadOnlyViolationError("oa_form_image_not_in_process")
        if drive_material_directory is None:
            drive_material_directory = tempfile.TemporaryDirectory(prefix="oa-review-")
            atexit.register(drive_material_directory.cleanup)
        return _download_dingtalk_oa_comment_image(
            process_instance_id=process_instance_id,
            image_url=image_url,
            material_root=Path(drive_material_directory.name),
        )

    bound.add_tool(
        read_dingtalk_oa_form_image,
        name="read_dingtalk_oa_form_image",
        annotations=read,
    )

    def read_dingtalk_oa_comment_attachment(
        process_instance_id: str,
        file_id: str,
        file_name: str,
        page_number: int | None = None,
    ) -> dict[str, object]:
        """Read one exact premium OA comment attachment and return bounded OCR input.

        Use the file_id and file_name from read_dingtalk_oa_records. Scanned PDFs
        return one-based ocr_page_numbers; pass a listed page_number to receive
        a bounded JPEG for ai_services.ocr_document. The exact file remains only
        for this MCP process lifetime. An unavailable premium download is a
        technical read failure, never evidence that the applicant omitted it.
        """
        nonlocal drive_material_directory
        if not process_instance_id.strip() or not file_id.strip():
            raise AgentReadOnlyViolationError("oa_comment_attachment_identity_required")
        if not _task_file_component(file_name) or len(file_name) > 255:
            raise AgentReadOnlyViolationError("oa_comment_attachment_file_name_invalid")
        key = (process_instance_id, file_id, file_name)
        if page_number is not None:
            material_path = oa_comment_material_paths.get(key)
            if material_path is None or not material_path.is_file():
                raise AgentReadOnlyViolationError(
                    "oa_comment_attachment_page_source_unavailable"
                )
            if material_path.suffix.casefold() != ".pdf":
                raise AgentReadOnlyViolationError(
                    "oa_comment_attachment_page_requires_pdf"
                )
            with material_path.open("rb") as source:
                source_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
            return _pdf_page_ocr_input(
                material_path, page_number=page_number,
                source_sha256=source_sha256,
            )

        if drive_material_directory is None:
            drive_material_directory = tempfile.TemporaryDirectory(
                prefix="oa-review-"
            )
            atexit.register(drive_material_directory.cleanup)
        from app.dws_client import DwsClient
        result = _download_dingtalk_oa_comment_attachment(
            dws=DwsClient(),
            process_instance_id=process_instance_id,
            file_id=file_id,
            file_name=file_name,
            material_root=Path(drive_material_directory.name),
        )
        oa_comment_material_paths[key] = Path(str(result["path"]))
        return result

    bound.add_tool(
        read_dingtalk_oa_comment_attachment,
        name="read_dingtalk_oa_comment_attachment",
        annotations=read,
    )

    def read_dingtalk_oa_comment_image(
        process_instance_id: str,
        image_url: str,
    ) -> dict[str, object]:
        """Read one image URL returned by read_dingtalk_oa_records for OCR.

        Use this when an OA operation-record comment exposes an image URL
        instead of a file_id. The fetch is limited to DingTalk's media origin,
        does not follow redirects, and returns bounded OCR input and a digest.
        The image remains only for this MCP process lifetime. Read failures are
        technical evidence gaps, never evidence that material is missing.
        """
        nonlocal drive_material_directory
        if drive_material_directory is None:
            drive_material_directory = tempfile.TemporaryDirectory(
                prefix="oa-review-"
            )
            atexit.register(drive_material_directory.cleanup)
        return _download_dingtalk_oa_comment_image(
            process_instance_id=process_instance_id,
            image_url=image_url,
            material_root=Path(drive_material_directory.name),
        )

    bound.add_tool(
        read_dingtalk_oa_comment_image,
        name="read_dingtalk_oa_comment_image",
        annotations=read,
    )

    def read_system_action_contract(
        capability: str,
        operation: str | None = None,
    ) -> dict[str, object]:
        """Read canonical shared rules and the requested reviewed system action rows.

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
        """Read an exact selected Skill frozen in this task's input.

        Pass a Skill name from the scheduled prompt catalog. The returned content
        is the immutable managed revision or operation snapshot saved for this
        review input; this lookup never rereads an installed Skill file.
        """
        return _read_bound_task_skill(db_path, task_id, artifact_generation, name)

    bound.add_tool(
        read_task_skill, name="read_task_skill", annotations=read,
        structured_output=False,
    )

    def read_task_artifact(
        name: str, offset: int = 0, max_chars: int = 20000
    ) -> dict[str, object]:
        """Read a text artifact using a zero-based character offset (default 0).

        Start at 0 for the full document; offset=1 omits its first character.
        Follow next_offset until null. Every response includes offset and
        total_chars; sha256 identifies the entire file, not the returned slice.
        """
        return _read_task_file(
            _task_file_root(db_path, task_id, artifact_generation, create=False),
            name,
            offset,
            max_chars,
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

    def read_dingtalk_document_permissions(
        node_id: str, next_token: str = ""
    ) -> dict[str, object]:
        """Read one page of an exact document's current collaborators and roles."""
        from app.dws_client import DwsClient
        if not node_id.strip():
            raise ValueError("document node is required")
        dws = DwsClient()
        command = [
            dws.dws_bin, "drive", "permission", "list", "--node", node_id,
            "--limit", "50",
        ]
        if next_token.strip():
            command.extend(("--next-token", next_token))
        return dws.run_json(command + ["--format", "json"])

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
        """Read a DWS-bounded A1 range. cellStyleRef indexes sharedCellStyles."""
        from app.dws_client import DwsClient
        if not node_id.strip() or not sheet_id.strip() or not cell_range.strip():
            raise ValueError("sheet node, ID, and range are required")
        dws = DwsClient()
        return share_sheet_cell_styles(dws.run_json([
            dws.dws_bin, "sheet", "range", "read", "--node", node_id,
            "--sheet-id", sheet_id, "--range", cell_range, "--format", "json",
        ]))

    def read_dingtalk_aitable_base(base_id: str):
        """Read one DingTalk AI table Base as compact JSON text."""
        from app.dws_client import DwsClient
        if not base_id.strip():
            raise ValueError("AI table base ID is required")
        return json.dumps(
            DwsClient().get_aitable_base(base_id), ensure_ascii=False,
            separators=(",", ":"),
        )

    def read_dingtalk_aitable_tables(
        base_id: str, table_ids: list[str] | None = None,
    ):
        """Read selected table metadata from one DingTalk AI table Base."""
        from app.dws_client import DwsClient
        if not base_id.strip():
            raise ValueError("AI table base ID is required")
        if table_ids is not None and any(not item.strip() for item in table_ids):
            raise ValueError("AI table IDs must be non-empty")
        return json.dumps(
            DwsClient().get_aitable_tables(base_id, table_ids), ensure_ascii=False,
            separators=(",", ":"),
        )

    def read_dingtalk_aitable_records(
        base_id: str, table_id: str, limit: int = 10,
    ):
        """Read a bounded page of records from one DingTalk AI table."""
        from app.dws_client import DwsClient
        if not base_id.strip() or not table_id.strip():
            raise ValueError("AI table base and table IDs are required")
        if type(limit) is not int or limit < 1:
            raise ValueError("AI table record limit must be a positive integer")
        return json.dumps(
            DwsClient().query_aitable_records(base_id, table_id, limit),
            ensure_ascii=False, separators=(",", ":"),
        )

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

    def convert_dingtalk_calendar_time(
        local_time: str, source_timezone: str, target_timezone: str
    ) -> dict[str, object]:
        """Convert one local wall time using IANA zones and date-specific offsets.

        Pass an ISO local datetime without an offset and explicit IANA timezone
        names. Ambiguous and nonexistent local times need clarification. This
        conversion does not check calendar or participant availability.
        """
        from app.calendar_timezone import convert_calendar_timezone
        return convert_calendar_timezone(local_time, source_timezone, target_timezone)

    def list_dingtalk_mailboxes() -> dict[str, object]:
        """Read the current identity's DingTalk mailboxes."""
        from app.dws_client import DwsClient
        dws = DwsClient()
        return dws.run_json([dws.dws_bin, "mail", "mailbox", "list", "--format", "json"])

    def read_dingtalk_mail_thread(email: str, conversation_id: str) -> dict[str, object]:
        """Read the complete original mail thread by mailbox and conversationId."""
        from app.dws_client import DwsClient
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "mail", "+thread", "--email", email,
            "--id", conversation_id, "--format", "json",
        ])

    def read_dingtalk_mail_message(email: str, message_id: str) -> dict[str, object]:
        """Read original message body and attachment metadata by exact messageId."""
        from app.dws_client import DwsClient
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "mail", "+message", "--email", email,
            "--id", message_id, "--format", "json",
        ])

    def read_dingtalk_mail_sent_status(email: str, internet_message_id: str) -> dict[str, object]:
        """Read sent status using the provider's internetMessageId."""
        from app.dws_client import DwsClient
        dws = DwsClient()
        return dws.run_json([
            dws.dws_bin, "mail", "message", "verify", "--email", email,
            "--internet-message-id", internet_message_id, "--format", "json",
        ])

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

    def search_dingtalk_documents(
        query: str, page_size: Annotated[int, Field(ge=1, le=20)] = 10,
    ) -> dict[str, object]:
        """Find DingTalk documents by title before reading an exact node."""
        from app.dws_client import DwsClient
        if not query.strip() or type(page_size) is not int or not 1 <= page_size <= 20:
            raise ValueError("document search query or page size is invalid")
        matches = DwsClient().search_documents(query, page_size)
        return {"documents": [item.model_dump(mode="json") for item in matches]}

    def read_wechat_messages(limit: int = 20) -> dict[str, object]:
        """Read the recent messages of the WeChat conversation this task is bound to.

        Messages are oldest first. The conversation cannot be chosen: a WeChat
        task reads only its own. Links in recent messages carry the article text.
        """
        from app.wechat import service
        from app.wechat.article import enrich_context

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("message read limit is invalid")
        conversation_id, conversation_type = _bound_wechat_conversation(
            db_path, task_id, artifact_generation,
        )
        from app.store import AutoReplyStore

        state = service.ready_account_state(AutoReplyStore(db_path))
        if state is None:
            raise ValueError("no ready WeChat account")
        messages = service.build_reader().read_messages(
            service.account_from_state(state),
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            limit=limit,
        )
        return {
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "messages": [
                {
                    "message_id": item.message_id,
                    "sender": item.sender_display_name,
                    "direction": item.direction,
                    "sent_at": item.sent_at,
                    "kind": item.kind,
                    "text": item.text,
                }
                for item in enrich_context(messages)
            ],
        }

    def save_wechat_voice(message_id: str) -> dict[str, object]:
        """Save the audio of a voice message of this task's WeChat conversation.

        Pass the message_id of a "[语音…]" message. The audio is stored as a WAV
        task file; the result names it, ready for upload_audio_file. An
        unreadable voice raises an error; it is neither empty nor answered.
        """
        from app.wechat import service
        from app.wechat.models import WechatVoiceUnavailable
        from app.wechat.reader_ipc import ReaderIpcError
        from app.wechat.voice import silk_to_wav

        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 128:
            raise ValueError("voice message id is invalid")
        conversation_id, _ = _bound_wechat_conversation(
            db_path, task_id, artifact_generation,
        )
        from app.store import AutoReplyStore

        state = service.ready_account_state(AutoReplyStore(db_path))
        if state is None:
            raise ValueError("no ready WeChat account")
        try:
            silk = service.build_reader().read_voice(
                service.account_from_state(state),
                conversation_id=conversation_id, message_id=message_id,
            )
            wav, seconds = silk_to_wav(silk)
        except WechatVoiceUnavailable as exc:
            raise ValueError(f"could not read the voice message: {exc}") from exc
        except ReaderIpcError as exc:
            if exc.code != "voice_unavailable":
                raise
            raise ValueError(f"could not read the voice message: {exc}") from exc
        name = f"wechat-voice-{message_id}.wav"
        root = _task_file_root(db_path, task_id, artifact_generation, create=True)
        _write_task_file(root, name, wav)
        return {
            "message_id": message_id,
            "name": name,
            "duration_seconds": round(seconds, 1),
        }

    def upload_audio_file(name: str, upload_url: str) -> dict[str, object]:
        """Send one audio task file to a one-time upload URL, without reading it.

        `name` is a task file (for example the one save_wechat_voice returned);
        `upload_url` is the one-time URL a transcription service handed out for
        it (ai_services create_audio_upload). The audio travels from the task
        directory to that URL and never passes through you. The result is the
        service's answer, normally a job_id to poll.
        """
        parsed = urlsplit(upload_url) if isinstance(upload_url, str) else None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("upload url is invalid")
        root = _task_file_root(db_path, task_id, artifact_generation, create=False)
        path = _task_file_path(root, name)
        if not path.is_file():
            raise ValueError("task file is unavailable")
        if path.stat().st_size > MAX_AUDIO_UPLOAD_BYTES:
            raise ValueError("audio file is too large to upload")
        boundary = hashlib.sha256(os.urandom(16)).hexdigest()
        body = b"".join((
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{quote(name)}\"\r\nContent-Type: application/octet-stream"
            "\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ))
        request = Request(upload_url, data=body, method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "ceo-agent-service",
        })
        try:
            with build_opener(_NoRedirects()).open(request, timeout=60) as response:
                answer = response.read(MAX_CLI_OUTPUT_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            raise ValueError(f"audio upload failed: HTTP {exc.code} {detail}") from exc
        if len(answer) > MAX_CLI_OUTPUT_BYTES:
            raise ValueError("audio upload answer is too large")
        try:
            return json.loads(answer)
        except ValueError as exc:
            raise ValueError("audio upload answer is not JSON") from exc

    def read_dingtalk_messages(
        conversation_id: str, title: str = "", single_chat: bool = False,
        limit: int = 50, direct_open_dingtalk_id: str = "",
    ) -> dict[str, object]:
        """Read recent messages of an identified DingTalk conversation.

        For the current bound direct-chat task, the exact trigger supplies the
        sender's openDingTalkId when omitted. Other direct chats require
        direct_open_dingtalk_id or a title. Groups need only conversation_id.

        For repeated raw strings, raw_payload_field_refs maps each null raw
        field to the same message's top-level field holding its exact value.
        Unreferenced raw fields retain their original values.
        """
        from app.dingtalk_models import DingTalkConversation
        from app.dws_client import DwsClient
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("message read limit is invalid")
        bound_sender = None
        if not direct_open_dingtalk_id.strip() and not title.strip():
            bound_sender = _bound_direct_chat_sender(
                db_path, task_id, artifact_generation, conversation_id,
            )
            direct_open_dingtalk_id = bound_sender or ""
        is_direct_chat = single_chat or bool(direct_open_dingtalk_id.strip())
        if is_direct_chat and not direct_open_dingtalk_id.strip() and not title.strip():
            raise ValueError("direct chat sender openDingTalkId or title is required")
        conversation = DingTalkConversation(
            open_conversation_id=conversation_id, title=title,
            single_chat=is_direct_chat, unread_point=0,
            direct_open_dingtalk_id=direct_open_dingtalk_id,
        )
        messages = DwsClient().read_recent_messages(conversation, limit=limit)
        if bound_sender and any(
            message.open_conversation_id != conversation_id for message in messages
        ):
            raise ValueError("bound direct messages belong to another conversation")
        return {"messages": [
            share_message_raw_payload(item.model_dump(mode="json")) for item in messages
        ]}

    def search_dingtalk_messages(
        query: str, start: str, end: str, limit: int = 100,
    ) -> dict[str, object]:
        """Search messages in an explicit window, capped at a scheduled trigger."""
        from app.dws_client import DwsClient
        if not query.strip() or not start.strip() or not end.strip():
            raise ValueError("message search query and window are required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("message search limit is invalid")
        _validate_scheduled_read_window(db_path, task_id, start, end)
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
        """Read complete conversation discovery, capped at a scheduled trigger."""
        from app.dws_client import DwsClient
        if not start.strip() or not end.strip():
            raise ValueError("conversation window is required")
        _validate_scheduled_read_window(db_path, task_id, start, end)
        return DwsClient().read_complete_recent_conversations(start, end)

    def read_dingtalk_messages_in_window(
        conversation_id: str, start: str, end: str,
    ) -> dict[str, object]:
        """Read one conversation in an explicit window, capped for scheduled tasks."""
        from app.dingtalk_models import DingTalkConversation
        from app.dws_client import DwsClient
        if not conversation_id.strip() or not start.strip() or not end.strip():
            raise ValueError("conversation and window are required")
        _validate_scheduled_read_window(db_path, task_id, start, end)
        dws = DwsClient()
        bound_sender = _bound_direct_chat_sender(
            db_path, task_id, artifact_generation, conversation_id,
        )
        if bound_sender:
            conversation = DingTalkConversation(
                open_conversation_id=conversation_id, title="", single_chat=True,
                unread_point=0, direct_open_dingtalk_id=bound_sender,
            )
            messages = dws.read_complete_message_window(conversation, start, end)
            if any(message.open_conversation_id != conversation_id for message in messages):
                raise ValueError("bound direct messages belong to another conversation")
            return {
                "complete": True, "hasMore": False,
                "messages": [
                    share_message_raw_payload(item.model_dump(mode="json"))
                    for item in messages
                ],
            }
        return dws.run_json([
            dws.dws_bin, "chat", "+chat-messages", "--group", conversation_id,
            "--start", start, "--end", end, "--order", "asc", "--page-all",
            "--page-limit", "50", "--max-items", "1000", "--format", "json",
        ])

    def read_scheduled_context() -> dict[str, object]:
        """Read trigger facts and, for Consumer, the current task artifact manifest."""
        from app.service_activity import scheduled_context

        store, run = _bound_scheduled_run(db_path, task_id)
        context = scheduled_context(store, run.id)
        context["artifact_directory"] = str(
            _task_file_root(
                db_path, task_id, artifact_generation, create=role == "consumer"
            )
        )
        if role == "consumer":
            context["task_artifacts"] = list_task_artifacts()["files"]
        return context

    def read_service_activity(
        start: str, end: str, sources: list[str] | None = None
    ) -> dict[str, object]:
        """Read selected service facts in an explicit aware [start, end) interval.

        sources: meetings, tasks, attention, emails, attempts, waiting. Attention
        and waiting return current unresolved records with their timestamps. For
        scheduled tasks, end cannot exceed the triggering run's scheduled_for.
        """
        from datetime import datetime
        from app.service_activity import (
            collect_service_activity,
            validate_scheduled_read_window,
        )
        from app.email_store import EmailStore

        store, run = _bound_scheduled_run(db_path, task_id)
        start_at, end_at = datetime.fromisoformat(start), datetime.fromisoformat(end)
        validate_scheduled_read_window(
            start_at, end_at, run.scheduled_for
        )
        return collect_service_activity(
            store,
            EmailStore(db_path, validate_rows=False),
            start_at,
            end_at,
            sources,
        )

    def _source_artifact(name: str, payload: dict) -> dict[str, object]:
        if role == "audit":
            name = "audit-" + name
        root = _task_file_root(db_path, task_id, artifact_generation, create=True)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        _task_file_path(root, name).write_bytes(raw)
        return {
            "artifact_name": name,
            "path": str(root / name),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def read_okr_roster(group_name: str) -> dict[str, object]:
        """Read a group's stable roster; save complete source JSON as a task artifact."""
        from app.cli import build_parser, settings_from_args
        from app.weekly_okr_report import weekly_okr_roster

        payload = weekly_okr_roster(
            settings_from_args(
                build_parser().parse_args(
                    [
                        "weekly-okr-roster",
                        "--group-name",
                        group_name,
                        "--db",
                        str(db_path),
                    ]
                )
            ),
            group_name=group_name,
        )
        return {
            **_source_artifact("okr-roster.json", payload),
            "member_count": len(payload["managers"]),
        }

    def read_okr_member(
        user_id: str,
        name: str,
        period_label: str,
        title: str = "",
        open_dingtalk_id: str = "",
    ) -> dict[str, object]:
        """Read one member's live OKR and provenance, bounded to one source request."""
        from app.cli import build_parser, settings_from_args
        from app.weekly_okr_report import weekly_okr_member

        filename = (
            "okr-member-" + hashlib.sha256(user_id.encode()).hexdigest() + ".json"
        )
        root = _task_file_root(
            db_path, task_id, artifact_generation, create=False
        )
        artifact_path = _task_file_path(root, filename)
        if artifact_path.is_file():
            cached = json.loads(artifact_path.read_text(encoding="utf-8"))
            manager = cached.get("manager") or {}
            outcome = cached.get("sourceOutcome") or {}
            cached_period = (
                (cached.get("liveOkr") or {}).get("periodLabel")
                or (cached.get("availabilityReceipt") or {}).get("periodLabel")
            )
            if (
                manager.get("userId") == user_id
                and manager.get("name") == name
                and manager.get("title", "") == title
                and manager.get("openDingtalkId", "") == open_dingtalk_id
                and cached_period == period_label
                and outcome.get("user_id") == user_id
                and outcome.get("kind") in {"collected", "goals_missing"}
            ):
                raw = artifact_path.read_bytes()
                return {
                    "artifact_name": filename,
                    "path": str(artifact_path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "user_id": user_id,
                    "source_outcome": outcome,
                }

        payload = weekly_okr_member(
            settings_from_args(
                build_parser().parse_args(
                    [
                        "weekly-okr-member",
                        "--user-id",
                        user_id,
                        "--name",
                        name,
                        "--period-label",
                        period_label,
                        "--db",
                        str(db_path),
                    ]
                )
            ),
            user_id=user_id,
            name=name,
            period_label=period_label,
            title=title,
            open_dingtalk_id=open_dingtalk_id,
        )
        return {
            **_source_artifact(filename, payload),
            "user_id": user_id,
            "source_outcome": payload.get("sourceOutcome"),
        }

    def read_dingtang_okr_score_detail(
        period_id: str, objective_id: str
    ) -> dict[str, object]:
        """Read the current score batch and evaluator details from Dingteam OKR."""
        from app.dingtang_okr_action import DingtangOkrBrowserClient

        payload = DingtangOkrBrowserClient().read_score_detail(
            period_id=period_id, objective_id=objective_id
        )
        filename = (
            "okr-score-detail-"
            + hashlib.sha256((period_id + ":" + objective_id).encode()).hexdigest()
            + ".json"
        )
        return {
            **_source_artifact(filename, payload),
            "period_id": period_id,
            "objective_id": objective_id,
            "batch_id": payload.get("batch_id"),
        }

    def read_meeting_archive(meeting_id: str) -> dict[str, str]:
        """Read the archive attached to a discovered meeting's stable source ID."""
        from app.config import workspace_path
        from app.minutes_sync import MINUTES_ARCHIVE_DIRECTORY, archive_path_for

        store, _run = _bound_scheduled_run(db_path, task_id)
        job = store.get_meeting_alignment_job_by_meeting_id(meeting_id)
        if job is None:
            raise ValueError("meeting archive source is missing")
        source = json.loads(job.source_json or "{}")
        basic = (source.get("minutes_info") or {}).get("result") or {}
        path = archive_path_for(
            workspace_path() / MINUTES_ARCHIVE_DIRECTORY, basic, task_uuid=meeting_id
        )
        if (
            path is None
            or not path.is_file()
            or path.stat().st_size > MAX_TEXT_MATERIAL_BYTES
        ):
            raise ValueError("meeting archive is unavailable")
        return {
            "meeting_id": meeting_id,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }

    for name, tool in (
        ("read_dingtalk_document", read_dingtalk_document),
        ("read_dingtalk_document_full", read_dingtalk_document_full),
        ("read_dingtalk_document_info", read_dingtalk_document_info),
        ("read_dingtalk_document_permissions", read_dingtalk_document_permissions),
        ("read_dingtalk_drive_info", read_dingtalk_drive_info),
        ("read_dingtalk_drive_file", read_dingtalk_drive_file),
        ("list_dingtalk_sheets", list_dingtalk_sheets),
        ("read_dingtalk_sheet_info", read_dingtalk_sheet_info),
        ("read_dingtalk_sheet_range", read_dingtalk_sheet_range),
        ("read_dingtalk_aitable_base", read_dingtalk_aitable_base),
        ("read_dingtalk_aitable_tables", read_dingtalk_aitable_tables),
        ("read_dingtalk_aitable_records", read_dingtalk_aitable_records),
        ("read_dingtalk_oa", read_dingtalk_oa),
        ("list_dingtalk_pending_oa", list_dingtalk_pending_oa),
        ("read_dingtalk_oa_records", read_dingtalk_oa_records),
        ("read_dingtalk_oa_tasks", read_dingtalk_oa_tasks),
        ("read_dingtalk_oa_revert_activities", read_dingtalk_oa_revert_activities),
        ("read_dingtalk_calendar_event", read_dingtalk_calendar_event),
        ("list_dingtalk_mailboxes", list_dingtalk_mailboxes),
        ("read_dingtalk_mail_thread", read_dingtalk_mail_thread),
        ("read_dingtalk_mail_message", read_dingtalk_mail_message),
        ("read_dingtalk_mail_sent_status", read_dingtalk_mail_sent_status),
        ("list_dingtalk_calendar_events", list_dingtalk_calendar_events),
        ("convert_dingtalk_calendar_time", convert_dingtalk_calendar_time),
        ("search_dingtalk_contacts", search_dingtalk_contacts),
        ("read_dingtalk_group_members", read_dingtalk_group_members),
        ("read_dingtalk_user_profiles", read_dingtalk_user_profiles),
        ("search_dingtalk_documents", search_dingtalk_documents),
        ("read_dingtalk_messages", read_dingtalk_messages),
        ("read_wechat_messages", read_wechat_messages),
        ("save_wechat_voice", save_wechat_voice),
        ("upload_audio_file", upload_audio_file),
        ("search_dingtalk_messages", search_dingtalk_messages),
        ("read_dingtalk_thread_replies", read_dingtalk_thread_replies),
        ("search_dingtalk_conversations", search_dingtalk_conversations),
        ("list_dingtalk_minutes", list_dingtalk_minutes),
        ("read_dingtalk_minutes", read_dingtalk_minutes),
        ("recent_dingtalk_conversations", recent_dingtalk_conversations),
        ("read_dingtalk_messages_in_window", read_dingtalk_messages_in_window),
        ("read_scheduled_context", read_scheduled_context),
        ("read_okr_roster", read_okr_roster),
        ("read_okr_member", read_okr_member),
        ("read_dingtang_okr_score_detail", read_dingtang_okr_score_detail),
        ("read_service_activity", read_service_activity),
        ("read_meeting_archive", read_meeting_archive),
    ):
        bound.add_tool(tool, name=name, annotations=read)

    if role == "consumer":
        def consumer_artifact_write(name: str, content: str) -> dict[str, object]:
            """Write a text artifact only in this task's current workspace generation."""
            if (
                not isinstance(content, str)
                or len(content.encode("utf-8")) > MAX_TASK_FILE_BYTES
            ):
                raise ValueError("task file content is invalid or too large")
            root = _task_file_root(db_path, task_id, artifact_generation, create=True)
            _write_task_file(root, name, content.encode("utf-8"))
            return _read_task_file(root, name)

        bound.add_tool(
            consumer_artifact_write, name="consumer_artifact_write", annotations=write
        )

        def consumer_run_skill_script(
            name: str, script: str, arguments: list[str] | None = None
        ) -> dict[str, object]:
            """Run a selected Skill's Python script in this task's artifact directory.

            Use relative script paths such as scripts/window.py. Arguments are
            passed directly without a shell. Scripts should emit compact JSON
            and save large inputs/outputs as task artifacts.
            """
            _read_bound_task_skill(db_path, task_id, artifact_generation, name)
            path = _approved_consumer_skill_script(name, script)
            skill_root = Path.home() / ".agents" / "skills" / name
            root = _task_file_root(db_path, task_id, artifact_generation, create=True)
            result = subprocess.run(
                [sys.executable, str(path), *(arguments or [])],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(skill_root)},
                capture_output=True,
                text=True,
                timeout=120,
            )
            if len((result.stdout + result.stderr).encode()) > MAX_CLI_OUTPUT_BYTES:
                raise ValueError(
                    "Skill script output is too large; save output as an artifact"
                )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        bound.add_tool(
            consumer_run_skill_script,
            name="consumer_run_skill_script",
            annotations=write,
        )

        def consumer_document_write(
            artifact_name: str,
            title: str,
            doc_format: str = "markdown",
            node_id: str = "",
            parent_id: str = "",
            workspace_id: str = "",
            expected_revision: int | None = None,
        ) -> dict[str, object]:
            """Write a task artifact to an explicit document and return the provider receipt.

            Update: node_id, plus expected_revision for JSONML. Create: parent_id
            and workspace_id. Decide titles, destinations and business readiness
            using the selected Skill. Successful writes do not trigger content readback.
            """
            _bound_scheduled_run(db_path, task_id)
            root = _task_file_root(db_path, task_id, artifact_generation, create=False)
            return _write_document_artifact(
                root=root,
                artifact_name=artifact_name,
                title=title,
                doc_format=doc_format,
                node_id=node_id,
                parent_id=parent_id,
                workspace_id=workspace_id,
                expected_revision=expected_revision,
            )

        bound.add_tool(
            consumer_document_write, name="consumer_document_write", annotations=write
        )
    return bound


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("consumer", "audit"), required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--execution-generation", required=True)
    args = parser.parse_args()
    build_role_server(
        args.role,
        task_id=args.task_id,
        db_path=args.db,
        execution_generation=args.execution_generation,
    ).run(transport="stdio")
