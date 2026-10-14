import hashlib
import json
import subprocess
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


def test_scanned_pdf_page_uses_existing_ocr_client_and_preserves_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "")
    pdf_path = tmp_path / "settlement.pdf"
    pdf_path.write_bytes(b"%PDF-scanned-example")
    calls = []

    def run(argv, *, timeout):
        calls.append((argv, timeout))
        response = {"data": [{"source_id": "input-1:settlement.pdf", "pages": [
            {"page_number": 1, "text": "结算金额 37545 元", "confidence": 0.94,
             "warnings": ["amount requires image check"]}
        ]}]}
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(agent_cli, "run_bounded_process", run)
    with pdf_path.open("rb") as pdf:
        result = agent_cli._read_pdf_material(pdf, pdf_path)

    assert result["page_count"] == 1
    assert result["pages"] == [{
        "index": 1, "text": "结算金额 37545 元", "source": "ocr",
        "confidence": 0.94, "warnings": ["amount requires image check"],
    }]
    assert result["content"] == "结算金额 37545 元"
    assert result["truncated"] is False
    assert result["sha256"] == hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert calls[0][0][-4:] == ["--format", "json", "--pages", "1"]
    assert calls[0][0][1].endswith("/skills/ocr/scripts/ocr.py")


def test_mixed_pdf_ocrs_only_scanned_page_and_keeps_original_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "Cover agreement", " ", "Third page terms")
    requested = []

    def run(argv, *, timeout):
        requested.append(argv[-1])
        response = {"data": [{"source_id": "input-1:mixed.pdf", "pages": [
            {"page_number": 2, "text": "第二页付款条件", "confidence": 0.87}
        ]}]}
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(agent_cli, "run_bounded_process", run)
    result = agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "mixed.pdf")

    assert requested == ["2"]
    assert [page["index"] for page in result["pages"]] == [1, 2, 3]
    assert [page["text"] for page in result["pages"]] == [
        "Cover agreement", "第二页付款条件", "Third page terms",
    ]
    assert result["content"] == "Cover agreement\n\n第二页付款条件\n\nThird page terms"
    assert result["truncated"] is False


@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess([], 1, "", "ocr: service unavailable"),
    subprocess.CompletedProcess([], 0, json.dumps({"data": [{"pages": []}]}), ""),
])
def test_ocr_failure_is_technical_and_not_missing_applicant_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response
):
    reader_with_pages(monkeypatch, "")
    monkeypatch.setattr(agent_cli, "run_bounded_process", lambda *_args, **_kwargs: response)
    with pytest.raises(AgentReadOnlyViolationError, match="pdf_material_ocr_unavailable"):
        agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "scan.pdf")


def test_text_pdf_does_not_call_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reader_with_pages(monkeypatch, "A complete text layer")
    monkeypatch.setattr(
        agent_cli, "run_bounded_process",
        lambda *_args, **_kwargs: pytest.fail("text PDF sent to OCR"),
    )
    result = agent_cli._read_pdf_material(BytesIO(b"pdf fixture"), tmp_path / "text.pdf")
    assert result["pages"] == [{"index": 1, "text": "A complete text layer"}]


def test_drive_scanned_pdf_returns_readable_body_for_exact_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reader_with_pages(monkeypatch, "")
    document = b"%PDF-scanned-drive-attachment"

    class Dws:
        def download_drive_file(self, node, *, file_name, space_id, max_bytes):
            assert (node, file_name, space_id) == (
                "node-237492518210", "settlement.pdf", "space-21349694622"
            )
            assert max_bytes == agent_cli.MAX_SPREADSHEET_BYTES
            return document

    def run(argv, *, timeout):
        response = {"data": [{"source_id": f"input-1:{Path(argv[2]).name}",
                              "pages": [{"page_number": 1, "text": "付款结算单"}]}]}
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(agent_cli, "run_bounded_process", run)
    result = agent_cli._download_dingtalk_drive_file(
        dws=Dws(), node_id="node-237492518210", space_id="space-21349694622",
        file_name="settlement.pdf", material_root=tmp_path,
    )

    assert result["content"] == "付款结算单"
    assert result["pages"] == [{"index": 1, "text": "付款结算单", "source": "ocr"}]
    assert result["sha256"] == hashlib.sha256(document).hexdigest()
    assert result["node_id"] == "node-237492518210"
    assert result["space_id"] == "space-21349694622"
    assert result["file_name"] == "settlement.pdf"
    assert "ocr_required" not in result
