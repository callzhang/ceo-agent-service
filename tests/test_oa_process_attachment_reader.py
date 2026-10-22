import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import agent_cli
from app import dws_client
from app.dws_client import DwsClient


def test_process_attachment_cli_download_uses_exact_instance_and_bounded_signed_url(monkeypatch):
    commands = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size):
            assert size == 6
            return b"abcdef"

    class FakeDws(DwsClient):
        def run_json(self, command):
            commands.append(command)
            return {"ok": True, "data": {"downloadUri": "https://signed.example/oa"}}

    monkeypatch.setattr(dws_client, "urlopen", lambda url, timeout: FakeResponse())
    with pytest.raises(ValueError, match="too_large"):
        FakeDws().download_oa_process_attachment_via_cli(
            "process-1", "file-1", max_bytes=5,
        )
    assert commands == [[
        "dws", "oa", "approval", "attachment", "download-url",
        "--instance-id", "process-1", "--file-id", "file-1", "--format", "json",
    ]]


def test_oa_form_attachment_uses_process_scoped_reader_when_drive_denies(tmp_path, monkeypatch):
    body = b"Project scope and price evidence"

    class FakeDws:
        def download_oa_process_attachment_via_cli(self, process_instance_id, file_id, *, max_bytes):
            assert (process_instance_id, file_id) == ("process-1", "file-1")
            assert max_bytes >= len(body)
            return body

        def download_drive_file(self, *_args, **_kwargs):
            raise AssertionError("OA form attachment must not use generic Drive access")

    monkeypatch.setattr(agent_cli, "read_text_file", lambda path: {
        "format": "text", "content": open(path, encoding="utf-8").read(), "path": path,
    })
    result = agent_cli._download_dingtalk_oa_process_attachment(
        dws=FakeDws(), process_instance_id="process-1", file_id="file-1",
        file_name="plan.md", material_root=tmp_path,
    )

    assert result["content"] == body.decode()
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["process_instance_id"] == "process-1"
    assert result["file_id"] == "file-1"


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_oa_xlsx_attachment_preserves_download_path_for_role_cache(role, tmp_path, monkeypatch):
    workbook = BytesIO()
    with ZipFile(workbook, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Verified</t></is></c></row></sheetData></worksheet>',
        )
    body = workbook.getvalue()
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))

    class FakeDws:
        def download_oa_process_attachment_via_cli(self, process_instance_id, file_id, *, max_bytes):
            assert (process_instance_id, file_id) == ("process-1", "file-1")
            assert len(body) <= max_bytes
            return body

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
        "read_dingtalk_oa_process_attachment"
    )
    response = asyncio.run(tool.run({
        "process_instance_id": "process-1", "file_id": "file-1",
        "file_name": "quote.xlsx",
    }, convert_result=True))
    payload = json.loads(response[0][0].text)
    assert payload["format"] == "xlsx"
    assert payload["sheets"][0]["rows"][0]["cells"] == {"A": "Verified"}
    assert payload["sha256"] == hashlib.sha256(body).hexdigest()
    assert Path(payload["path"]).read_bytes() == body
    assert (payload["process_instance_id"], payload["file_id"]) == (
        "process-1", "file-1"
    )


def test_both_roles_expose_process_attachment_reader_with_paged_pdf_input(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))

    class FakeDws:
        def download_oa_process_attachment_via_cli(self, *_args, **_kwargs):
            return b"PDF fixture bytes"

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())

    class Page:
        def extract_text(self):
            return ""

    class Reader:
        is_encrypted = False
        pages = [Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda *_args: Reader())
    for role in ("consumer", "audit"):
        tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
            "read_dingtalk_oa_process_attachment"
        )
        response = asyncio.run(tool.run({
            "process_instance_id": "process-1", "file_id": "file-1",
            "file_name": "quote.pdf",
        }, convert_result=True))
        payload = json.loads(response[0][0].text)
        assert payload["ocr_required"] is True
        assert payload["ocr_page_numbers"] == [1]


def test_process_attachment_reader_is_registered_once(caplog):
    agent_cli.build_role_server("audit")
    assert not any(
        "Tool already exists: read_dingtalk_oa_process_attachment" in record.message
        for record in caplog.records
    )
