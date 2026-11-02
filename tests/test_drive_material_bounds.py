import hashlib
import asyncio
import json
import os
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import agent_cli
from app.dws_client import DwsClient, DwsError
from app.native_cli_metadata import AgentReadOnlyViolationError


class MaterialProvider:
    def __init__(self, content):
        self.content = content

    def download_drive_file(self, *args, **kwargs):
        return self.content


def read_attachment(tmp_path, monkeypatch, content, name="evidence.pptx"):
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    return agent_cli._download_dingtalk_drive_file(
        dws=MaterialProvider(content), node_id="node-1", space_id="space-1",
        file_name=name, material_root=tmp_path,
    )


def presentation(text):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("ppt/presentation.xml", "<presentation/>")
        package.writestr("ppt/slides/slide1.xml", '<slide xmlns:a="urn:drawing"><a:t>' + text + "</a:t></slide>")
    return output.getvalue()


@pytest.mark.parametrize("aggregate", [False, True])
def test_attachment_rejects_expanded_package_before_parser(tmp_path, monkeypatch, aggregate):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("ppt/presentation.xml", "<presentation/>")
        if aggregate:
            for index in range(3):
                package.writestr(str(index), b"x" * (agent_cli.MAX_SPREADSHEET_BYTES // 2))
        else:
            package.writestr("oversized", b"x" * (agent_cli.MAX_SPREADSHEET_BYTES + 1))
    parsed = []
    monkeypatch.setattr(agent_cli, "read_text_file", lambda *args: parsed.append(True) or {})
    with pytest.raises(AgentReadOnlyViolationError, match="expanded"):
        read_attachment(tmp_path, monkeypatch, output.getvalue())
    assert parsed == []


def test_presentation_text_overflow_is_marked_truncated(tmp_path, monkeypatch):
    text = "x" * (agent_cli.MAX_SPREADSHEET_PREVIEW_CHARS + 1)
    result = read_attachment(tmp_path, monkeypatch, presentation(text))
    assert len(result["slides"][0]["text"]) == agent_cli.MAX_SPREADSHEET_PREVIEW_CHARS
    assert result["truncated"] is True


@pytest.mark.parametrize("format", ["xlsx", "pptx"])
def test_every_attachment_receipt_has_byte_digest(tmp_path, monkeypatch, format):
    content = b"synthetic provider bytes"
    monkeypatch.setattr(agent_cli, "read_text_file", lambda *args: {"format": format})
    result = read_attachment(tmp_path, monkeypatch, content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()


def test_tool_response_budget_includes_escaped_duplicated_pdf_text(tmp_path, monkeypatch):
    text = '"\\' * 140000
    monkeypatch.setattr(agent_cli, "read_text_file", lambda *args: {
        "format": "pdf", "pages": [{"index": 1, "text": text}],
        "page_count": 1, "content": text,
    })
    assert len(json.dumps({"content": text, "pages": [{"text": text}]}).encode()) > 1024 * 1024
    with pytest.raises(AgentReadOnlyViolationError, match="response_too_large"):
        read_attachment(tmp_path, monkeypatch, b"PDF fixture", name="evidence.pdf")


@pytest.mark.parametrize("text,expected", [("1234567890", False), ("12345678901", True)])
def test_pdf_text_overflow_is_reported(tmp_path, monkeypatch, text, expected):
    class Page:
        def extract_text(self):
            return text

    class Reader:
        is_encrypted = False
        pages = [Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda *args: Reader())
    monkeypatch.setattr(agent_cli, "MAX_TEXT_MATERIAL_BYTES", 10)
    result = agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "evidence.pdf")
    assert result["content"] == "1234567890"
    assert result["truncated"] is expected


def test_package_parser_never_requests_unbounded_member_read(tmp_path, monkeypatch):
    calls = []
    original_read = zipfile.ZipExtFile.read

    def reading(source, count=-1):
        calls.append(count)
        return original_read(source, count)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", reading)
    result = read_attachment(tmp_path, monkeypatch, presentation("Normal document"))
    assert result["slides"][0]["text"] == "Normal document"
    assert calls and all(
        0 < count <= agent_cli.MAX_PPTX_EXPANDED_BYTES + 1 for count in calls
    )


def test_dense_workbook_is_bounded_by_actual_mcp_text_block(tmp_path, monkeypatch):
    columns = [chr(65 + index) for index in range(26)]
    columns += ["A" + chr(65 + index) for index in range(26)]
    columns += ["B" + chr(65 + index) for index in range(12)]
    material = {"format": "xlsx", "sheets": [
        {"name": str(sheet), "rows": [
            {"row": row, "cells": {column: "x" for column in columns}}
            for row in range(200)
        ], "truncated": False}
        for sheet in range(6)
    ]}
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("read_dingtalk_drive_file")
    blocks, _ = tool.fn_metadata.convert_result(material)
    assert len(json.dumps(material).encode()) < agent_cli.MAX_MCP_MATERIAL_RESPONSE_BYTES
    assert len(blocks[0].text.encode()) > 1024 * 1024
    monkeypatch.setattr(DwsClient, "download_drive_file", lambda *args, **kwargs: b"fixture")
    monkeypatch.setattr(agent_cli, "read_text_file", lambda *args: material)
    with pytest.raises(ToolError, match="response_too_large"):
        asyncio.run(tool.run({"node_id": "node-1", "space_id": "space-1", "file_name": "evidence.xlsx"}, convert_result=True))


def test_download_does_not_follow_link_swapped_at_open(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_bytes(b"private fixture")

    def download(self, command):
        path = Path(command[command.index("--output") + 1])
        path.write_bytes(b"expected")

    original_open = os.open
    original_path_open = Path.open

    def swap(path):
        target = Path(path)
        if target.name == "material":
            target.unlink()
            target.symlink_to(outside)

    def opening(path, flags, *args, **kwargs):
        swap(path)
        return original_open(path, flags, *args, **kwargs)

    def path_open(path, *args, **kwargs):
        if args and args[0] == "rb":
            swap(path)
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(DwsClient, "run_text", download)
    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(Path, "open", path_open)
    with pytest.raises((OSError, DwsError)):
        DwsClient().download_drive_file("node-1", file_name="material")
    assert outside.read_bytes() == b"private fixture"


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize("format", ["xlsx", "pptx"])
def test_role_drive_reader_returns_package_body_and_material_path(tmp_path, monkeypatch, role, format):
    """The real package parsers omit path; the download receipt owns it."""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        if format == "xlsx":
            package.writestr("[Content_Types].xml", "<Types/>")
            package.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Scores" r:id="rId1"/></sheets></workbook>')
            package.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            package.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="24"><c r="K24" t="e"><v>#VALUE!</v></c></row></sheetData></worksheet>')
        else:
            package.writestr("ppt/presentation.xml", "<presentation/>")
            package.writestr("ppt/slides/slide1.xml", '<slide xmlns:a="urn:drawing"><a:t>Verified material</a:t></slide>')
    content = output.getvalue()
    monkeypatch.setattr(DwsClient, "download_drive_file", lambda *args, **kwargs: content)
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool("read_dingtalk_drive_file")
    result = asyncio.run(tool.run({"node_id": "node-1", "space_id": "space-1", "file_name": "evidence." + format}, convert_result=False))
    assert result["format"] == format
    assert Path(result["path"]).read_bytes() == content
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert (result["node_id"], result["space_id"], result["file_name"]) == ("node-1", "space-1", "evidence." + format)
    if format == "xlsx":
        assert result["sheets"][0]["rows"][0] == {"row": 24, "cells": {"K": "#VALUE!"}}
    else:
        assert result["slides"][0]["text"] == "Verified material"


def test_spreadsheet_tool_declares_existing_preview_limits():
    tools = asyncio.run(agent_cli.build_role_server("consumer").list_tools())
    schema = next(tool for tool in tools if tool.name == "read_spreadsheet").inputSchema
    for name, maximum in [("max_rows", agent_cli.MAX_SPREADSHEET_ROWS), ("max_columns", agent_cli.MAX_SPREADSHEET_COLUMNS)]:
        assert schema["properties"][name]["minimum"] == 1
        assert schema["properties"][name]["maximum"] == maximum
