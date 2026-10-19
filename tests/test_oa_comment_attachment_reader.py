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


def test_oa_comment_image_url_is_downloaded_into_existing_bounded_ocr_path(
    tmp_path, monkeypatch
):
    image_bytes = b"meeting screenshot image"
    requested = []

    class Response:
        status = 200
        headers = {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size):
            return image_bytes

        def geturl(self):
            return "https://static.dingtalk.com/media/evidence.png"

    class Opener:
        def open(self, request, timeout):
            requested.append((request.full_url, timeout))
            return Response()

    monkeypatch.setattr(agent_cli, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(agent_cli, "read_text_file", lambda path: {
        "format": "image",
        "ocr_required": True,
        "ocr_input_base64": "fixture-image",
        "ocr_mime_type": "image/png",
        "path": path,
    })

    result = agent_cli._download_dingtalk_oa_comment_image(
        process_instance_id="process-1",
        image_url="https://static.dingtalk.com/media/evidence.png",
        material_root=tmp_path,
    )

    assert requested == [("https://static.dingtalk.com/media/evidence.png", 15)]
    assert result["ocr_required"] is True
    assert result["image_url"] == "https://static.dingtalk.com/media/evidence.png"
    assert result["sha256"] == hashlib.sha256(image_bytes).hexdigest()


def test_oa_comment_image_reader_rejects_non_dingtalk_origins_before_fetch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        agent_cli, "build_opener",
        lambda *_handlers: pytest.fail("untrusted origin must not be fetched"),
    )

    with pytest.raises(agent_cli.AgentReadOnlyViolationError, match="url_invalid"):
        agent_cli._download_dingtalk_oa_comment_image(
            process_instance_id="process-1",
            image_url="https://169.254.169.254/latest/meta-data/",
            material_root=tmp_path,
        )


def test_oa_comment_image_tool_is_exposed_for_record_image_urls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_cli, "TEXT_MATERIAL_ROOTS", (tmp_path,))
    monkeypatch.setattr(agent_cli.tempfile, "tempdir", str(tmp_path))

    class Response:
        status = 200
        headers = {"Content-Type": "image/png", "Content-Length": "8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b"not png!"

        def geturl(self):
            return "https://static.dingtalk.com/media/evidence.png"

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 15
            return Response()

    monkeypatch.setattr(agent_cli, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(agent_cli, "read_text_file", lambda path: {
        "format": "image",
        "ocr_required": True,
        "ocr_input_base64": "fixture-image",
        "ocr_mime_type": "image/png",
        "path": path,
    })
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool(
        "read_dingtalk_oa_comment_image"
    )

    result = asyncio.run(tool.run({
        "process_instance_id": "process-1",
        "image_url": "https://static.dingtalk.com/media/evidence.png",
    }, convert_result=True))

    payload = json.loads(result[0][0].text)
    assert payload["process_instance_id"] == "process-1"
    assert payload["ocr_required"] is True
    assert payload["sha256"] == hashlib.sha256(b"not png!").hexdigest()


def test_oa_comment_image_reader_does_not_follow_redirects(tmp_path, monkeypatch):
    import urllib.error

    class Opener:
        def open(self, request, timeout):
            assert timeout == 15
            raise urllib.error.HTTPError(
                request.full_url, 302, "redirect", {}, None,
            )

    monkeypatch.setattr(agent_cli, "build_opener", lambda *_handlers: Opener())
    with pytest.raises(agent_cli.AgentReadOnlyViolationError, match="http_302"):
        agent_cli._download_dingtalk_oa_comment_image(
            process_instance_id="process-1",
            image_url="https://static.dingtalk.com/media/evidence.png",
            material_root=tmp_path,
        )


def test_oa_comment_image_reader_rejects_oversize_before_read(tmp_path, monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Type": "image/png", "Content-Length": "20971521"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            pytest.fail("oversized response must be rejected before body read")

        def geturl(self):
            return "https://static.dingtalk.com/media/evidence.png"

    class Opener:
        def open(self, _request, timeout):
            return Response()

    monkeypatch.setattr(agent_cli, "build_opener", lambda *_handlers: Opener())
    with pytest.raises(agent_cli.AgentReadOnlyViolationError, match="file_too_large"):
        agent_cli._download_dingtalk_oa_comment_image(
            process_instance_id="process-1",
            image_url="https://static.dingtalk.com/media/evidence.png",
            material_root=tmp_path,
        )
