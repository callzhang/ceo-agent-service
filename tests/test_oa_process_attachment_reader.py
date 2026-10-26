import asyncio
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import agent_cli
from app import dws_client
from app.dws_client import DwsClient


def _image_only_pptx(*, extra_member_size: int = 0) -> bytes:
    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (32, 24), "white").save(image, format="PNG")
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:cSld><p:spTree><p:pic><p:blipFill><a:blip r:embed="rId1"/>'
            '</p:blipFill></p:pic></p:spTree></p:cSld></p:sld>',
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/image1.png"/>'
            '</Relationships>',
        )
        archive.writestr("ppt/media/image1.png", image.getvalue())
        if extra_member_size:
            archive.writestr(
                "ppt/media/extra.bin", b"\x00" * extra_member_size,
                compress_type=0,
            )
    return package.getvalue()


def test_oa_pptx_attachment_uses_presentation_size_bound(tmp_path, monkeypatch):
    body = _image_only_pptx(extra_member_size=21 * 1024 * 1024)
    assert len(body) > agent_cli.MAX_SPREADSHEET_BYTES
    captured = {}

    class FakeDws:
        def download_oa_process_attachment_via_cli(
            self, _process_instance_id, _file_id, *, max_bytes,
        ):
            captured["max_bytes"] = max_bytes
            return body

    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    result = agent_cli._download_dingtalk_oa_process_attachment(
        dws=FakeDws(), process_instance_id="process-1", file_id="file-1",
        file_name="receipts.pptx", material_root=tmp_path,
    )

    assert captured["max_bytes"] > agent_cli.MAX_SPREADSHEET_BYTES
    assert captured["max_bytes"] == agent_cli.MAX_PPTX_BYTES
    assert result["format"] == "pptx"
    assert result["ocr_required"] is True
    assert result["ocr_page_numbers"] == [1]


def test_pptx_text_and_ocr_pages_follow_numeric_slide_order():
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        for number, label in ((1, "first"), (2, "second"), (10, "tenth")):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f'<a:t>{label}</a:t></p:sld>',
            )

    with ZipFile(BytesIO(package.getvalue())) as archive:
        result = agent_cli._read_presentation_workbook(archive)

    assert [slide["text"] for slide in result["slides"]] == [
        "first", "second", "tenth",
    ]


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_oa_pptx_process_reader_returns_bounded_slide_ocr_input(
    role, tmp_path, monkeypatch,
):
    body = _image_only_pptx()
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))

    class FakeDws:
        def download_oa_process_attachment_via_cli(self, *_args, **_kwargs):
            return body

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
        "read_dingtalk_oa_process_attachment"
    )
    initial = asyncio.run(tool.run({
        "process_instance_id": "process-1", "file_id": "file-1",
        "file_name": "receipts.pptx",
    }, convert_result=True))
    payload = json.loads(initial[0][0].text)
    assert payload["ocr_page_numbers"] == [1]

    page = asyncio.run(tool.run({
        "process_instance_id": "process-1", "file_id": "file-1",
        "file_name": "receipts.pptx", "page_number": 1,
    }, convert_result=True))
    page_payload = json.loads(page[0][0].text)
    image_bytes = base64.b64decode(page_payload["ocr_input_base64"])
    assert image_bytes.startswith(b"\xff\xd8")
    assert page_payload["ocr_original_page_number"] == 1
    assert page_payload["ocr_source_sha256"] == hashlib.sha256(body).hexdigest()
    assert len(image_bytes) <= agent_cli.MAX_OCR_AGENT_INPUT_BYTES


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


def test_both_roles_read_only_expose_process_bound_form_image_reader(
    monkeypatch, tmp_path,
):
    process_id = "process-1"
    image_url = "https://static.dingtalk.com/media/proof.jpg"

    class FakeDws:
        def read_oa_approval_detail(self, requested_process_id):
            assert requested_process_id == process_id
            return {
                "result": {
                    "processInstanceId": process_id,
                    "formValueVOS": [{
                        "id": "proof",
                        "value": json.dumps([image_url]),
                    }],
                }
            }

    class Response:
        status = 200
        headers = {"Content-Type": "image/jpeg", "Content-Length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b"jpegdata"

        def geturl(self):
            return image_url

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == image_url
            assert timeout == 15
            return Response()

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    monkeypatch.setattr(agent_cli, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(agent_cli, "read_text_file", lambda path: {
        "format": "image", "ocr_required": True,
        "ocr_input_base64": "fixture-image", "ocr_mime_type": "image/jpeg",
        "path": path,
    })

    for role in ("consumer", "audit"):
        tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
            "read_dingtalk_oa_form_image"
        )
        response = asyncio.run(tool.run({
            "process_instance_id": process_id,
            "image_url": image_url,
        }, convert_result=True))
        payload = json.loads(response[0][0].text)
        assert payload["process_instance_id"] == process_id
        assert payload["ocr_required"] is True
        assert payload["sha256"] == hashlib.sha256(b"jpegdata").hexdigest()


def test_process_bound_form_image_reader_rejects_url_from_another_process(
    monkeypatch, tmp_path,
):
    class FakeDws:
        def read_oa_approval_detail(self, requested_process_id):
            return {
                "result": {
                    "processInstanceId": requested_process_id,
                    "formValueVOS": [{"id": "proof", "value": "[]"}],
                }
            }

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())
    monkeypatch.setattr(
        agent_cli, "build_opener",
        lambda *_handlers: pytest.fail("unbound form image URL must not be fetched"),
    )
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool(
        "read_dingtalk_oa_form_image"
    )
    with pytest.raises(ToolError, match="oa_form_image_not_in_process"):
        asyncio.run(tool.run({
            "process_instance_id": "process-1",
            "image_url": "https://static.dingtalk.com/media/other.jpg",
        }, convert_result=True))


def test_process_attachment_reader_is_registered_once(caplog):
    agent_cli.build_role_server("audit")
    assert not any(
        "Tool already exists: read_dingtalk_oa_process_attachment" in record.message
        for record in caplog.records
    )
