import asyncio
import hashlib
import json

import pytest

from app import agent_cli


def test_oa_comment_attachment_is_read_through_premium_endpoint_and_ocr_input(
    tmp_path, monkeypatch
):
    image_bytes = b"comment image fixture"
    calls = []

    class FakeDws:
        def download_oa_process_comment_attachment(self, process_instance_id, file_id):
            calls.append((process_instance_id, file_id))
            return image_bytes

    monkeypatch.setattr(agent_cli, "read_text_file", lambda path: {
        "format": "image",
        "ocr_required": True,
        "ocr_input_base64": "fixture-image",
        "ocr_mime_type": "image/png",
        "path": path,
    })

    result = agent_cli._download_dingtalk_oa_comment_attachment(
        dws=FakeDws(),
        process_instance_id="process-1",
        file_id="comment-file-1",
        file_name="meeting.png",
        material_root=tmp_path,
    )

    assert calls == [("process-1", "comment-file-1")]
    assert result["process_instance_id"] == "process-1"
    assert result["file_id"] == "comment-file-1"
    assert result["file_name"] == "meeting.png"
    assert result["ocr_required"] is True
    assert result["sha256"] == hashlib.sha256(image_bytes).hexdigest()


def test_oa_comment_attachment_rejects_path_like_name_before_download(tmp_path):
    class FakeDws:
        def download_oa_process_comment_attachment(self, *_args):
            pytest.fail("invalid filename must not trigger provider access")

    with pytest.raises(agent_cli.AgentReadOnlyViolationError, match="file_name_invalid"):
        agent_cli._download_dingtalk_oa_comment_attachment(
            dws=FakeDws(),
            process_instance_id="process-1",
            file_id="comment-file-1",
            file_name="../secret.png",
            material_root=tmp_path,
        )


def test_comment_attachment_tool_keeps_large_scanned_pdf_for_paged_ocr(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))

    class FakeDws:
        def download_oa_process_comment_attachment(self, *_args):
            return b"PDF fixture bytes"

    monkeypatch.setattr("app.dws_client.DwsClient", lambda: FakeDws())

    class Page:
        def extract_text(self):
            return ""

    class Reader:
        is_encrypted = False
        pages = [Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda *_args: Reader())
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool(
        "read_dingtalk_oa_comment_attachment"
    )
    first = asyncio.run(tool.run({
        "process_instance_id": "process-1",
        "file_id": "comment-file-1",
        "file_name": "meeting.pdf",
    }, convert_result=True))
    first_payload = json.loads(first[0][0].text)
    assert first_payload["ocr_required"] is True
    assert first_payload["ocr_page_numbers"] == [1]

    calls = []
    monkeypatch.setattr(agent_cli, "_pdf_page_ocr_input", lambda path, **kwargs: (
        calls.append((path, kwargs)) or {"format": "image", "ocr_required": True}
    ))
    second = asyncio.run(tool.run({
        "process_instance_id": "process-1",
        "file_id": "comment-file-1",
        "file_name": "meeting.pdf",
        "page_number": 1,
    }, convert_result=True))
    assert json.loads(second[0][0].text)["ocr_required"] is True
    assert len(calls) == 1
    assert calls[0][1]["source_sha256"] == hashlib.sha256(b"PDF fixture bytes").hexdigest()
