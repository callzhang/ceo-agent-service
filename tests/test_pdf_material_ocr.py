import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app import agent_cli
from app.native_cli_metadata import AgentReadOnlyViolationError


class Page:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self):
        return self.text


def reader_with_pages(monkeypatch, *text: str):
    class Reader:
        is_encrypted = False
        pages = [Page(value) for value in text]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda _stream: Reader())


def test_scanned_pdf_returns_original_bytes_and_page_identity_for_ocr_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "")
    pdf_path = tmp_path / "settlement.pdf"
    pdf_path.write_bytes(b"%PDF-scanned-example")
    with pdf_path.open("rb") as pdf:
        result = agent_cli._read_pdf_material(pdf, pdf_path)

    assert result["page_count"] == 1
    assert result["pages"] == [{"index": 1, "text": ""}]
    assert result["ocr_page_numbers"] == [1]
    assert result["ocr_mime_type"] == "application/pdf"
    assert base64.b64decode(result["ocr_input_base64"]) == pdf_path.read_bytes()
    assert result["sha256"] == hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def test_mixed_pdf_ocrs_only_scanned_page_and_keeps_original_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "Cover agreement", " ", "Third page terms")
    source = b"pdf fixture"
    result = agent_cli._read_pdf_material(BytesIO(source), tmp_path / "mixed.pdf")

    assert [page["index"] for page in result["pages"]] == [1, 2, 3]
    assert [page["text"] for page in result["pages"]] == [
        "Cover agreement", " ", "Third page terms",
    ]
    assert result["ocr_page_numbers"] == [2]
    assert base64.b64decode(result["ocr_input_base64"]) == source


def test_scanned_pdf_is_marked_for_ocr_not_missing_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    reader_with_pages(monkeypatch, "")
    result = agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "scan.pdf")
    assert result["ocr_required"] is True
    assert result["ocr_page_numbers"] == [1]
    assert result["pages"] == [{"index": 1, "text": ""}]


def test_text_pdf_does_not_call_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reader_with_pages(monkeypatch, "A complete text layer")
    monkeypatch.setattr(
        agent_cli, "run_bounded_process",
        lambda *_args, **_kwargs: pytest.fail("text PDF sent to OCR"),
    )
    result = agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "text.pdf")
    assert result["pages"] == [{"index": 1, "text": "A complete text layer"}]


def test_drive_scanned_pdf_returns_bounded_ocr_input_for_exact_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "")
    document = b"%PDF-scanned-drive-attachment"

    class Dws:
        def download_drive_file(self, node, *, file_name, space_id, max_bytes):
            assert (node, file_name, space_id) == (
                "node-237492518210", "settlement.pdf", "space-21349694622"
            )
            assert max_bytes == agent_cli.MAX_PDF_BYTES
            return document

    result = agent_cli._download_dingtalk_drive_file(
        dws=Dws(), node_id="node-237492518210", space_id="space-21349694622",
        file_name="settlement.pdf", material_root=tmp_path,
    )

    assert result["ocr_page_numbers"] == [1]
    assert result["ocr_required"] is True
    assert base64.b64decode(result["ocr_input_base64"]) == document
    assert result["sha256"] == hashlib.sha256(document).hexdigest()
    assert result["node_id"] == "node-237492518210"
    assert result["space_id"] == "space-21349694622"
    assert result["file_name"] == "settlement.pdf"


def test_large_scanned_pdf_uses_pdf_limit_and_reaches_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "")
    document = b"%PDF-" + b"x" * agent_cli.MAX_SPREADSHEET_BYTES

    class Dws:
        def download_drive_file(self, node, *, file_name, space_id, max_bytes):
            assert (node, file_name, space_id) == ("node-large", "deck.pdf", "space-1")
            assert max_bytes == agent_cli.MAX_PDF_BYTES
            return document

    result = agent_cli._download_dingtalk_drive_file(
        dws=Dws(), node_id="node-large", space_id="space-1",
        file_name="deck.pdf", material_root=tmp_path,
    )

    assert result["format"] == "pdf"
    assert result["page_count"] == 1
    assert result["ocr_page_numbers"] == [1]
    assert result["ocr_page_image_required"] is True
    assert "ocr_input_base64" not in result


def test_large_non_pdf_keeps_existing_download_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Dws:
        def download_drive_file(self, node, *, file_name, space_id, max_bytes):
            assert file_name == "notes.txt"
            assert max_bytes == agent_cli.MAX_SPREADSHEET_BYTES
            return b"x" * (agent_cli.MAX_SPREADSHEET_BYTES + 1)

    with pytest.raises(AgentReadOnlyViolationError, match="drive_file_too_large"):
        agent_cli._download_dingtalk_drive_file(
            dws=Dws(), node_id="node-large", space_id="space-1",
            file_name="notes.txt", material_root=tmp_path,
        )


def test_large_pdf_page_renderer_returns_bounded_identity_bound_jpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from PIL import Image
    import sys
    import types

    class Rendered:
        def to_pil(self):
            return Image.new("RGB", (1200, 800), "white")

    class Page:
        def get_size(self):
            return (960, 540)

        def render(self, *, scale):
            assert scale == 1.5
            return Rendered()

        def close(self):
            return None

    class Document:
        def __len__(self):
            return 3

        def __getitem__(self, index):
            assert index == 1
            return Page()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "pypdfium2", types.SimpleNamespace(
        PdfDocument=lambda path: Document()
    ))
    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"pdf")

    result = agent_cli._pdf_page_ocr_input(
        pdf, page_number=2, source_sha256="f" * 64
    )

    payload = base64.b64decode(result["ocr_input_base64"], validate=True)
    assert len(payload) <= agent_cli.MAX_OCR_AGENT_INPUT_BYTES
    assert result["format"] == "image"
    assert result["ocr_mime_type"] == "image/jpeg"
    assert result["ocr_original_page_number"] == 2
    assert result["ocr_source_sha256"] == "f" * 64
