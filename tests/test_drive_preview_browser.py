import base64
from contextlib import contextmanager
from io import BytesIO

import pytest
from PIL import Image

from app import agent_cli, drive_preview_browser
from app.drive_preview_browser import DrivePreview, DrivePreviewError, capture_drive_preview


def _png(width=600, height=850) -> bytes:
    encoded = BytesIO()
    Image.new("RGB", (width, height), "white").save(encoded, format="PNG")
    return encoded.getvalue()


class _Element:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.scrolled = False

    def scroll_into_view_if_needed(self) -> None:
        self.scrolled = True

    def screenshot(self, *, type: str) -> bytes:
        assert type == "png"
        assert self.scrolled, "a page renders only after it is scrolled into view"
        return self.image


class _Page:
    def __init__(self, *, final_url: str, elements: list[_Element]) -> None:
        self.final_url = final_url
        self.elements = elements
        self.url = "about:blank"
        self.visited: list[str] = []

    def goto(self, url, **_kwargs) -> None:
        self.visited.append(url)
        self.url = self.final_url if url != "about:blank" else url

    def wait_for_timeout(self, _ms) -> None:
        pass

    def title(self) -> str:
        return "结算单.pdf"

    def query_selector_all(self, selector: str):
        assert selector == ".pdf-container .page"
        return self.elements

    def close(self) -> None:
        pass


class _Context:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.pages: list[_Page] = []
        self.closed = False

    def new_page(self) -> _Page:
        self.pages.append(self.page)
        return self.page

    def close(self) -> None:
        self.closed = True


def _fake_browser(monkeypatch, page: _Page):
    launches: list[dict] = []
    contexts: list[_Context] = []

    def launch(playwright, profile, **kwargs):
        launches.append(kwargs)
        context = _Context(page)
        contexts.append(context)
        return context

    monkeypatch.setattr("app.service_browser.launch_service_chrome", launch)

    @contextmanager
    def factory():
        yield object()

    return factory, launches, contexts


def test_capture_scrolls_and_shoots_every_page(monkeypatch, tmp_path) -> None:
    images = [_png(), _png(610, 860)]
    page = _Page(
        final_url="https://alidocs.dingtalk.com/uni-preview?extension=pdf",
        elements=[_Element(image) for image in images],
    )
    factory, launches, contexts = _fake_browser(monkeypatch, page)

    preview = capture_drive_preview(
        "AbCdEf0123456789AbCdEf0123456789",
        profile_dir=tmp_path / "profile", playwright_factory=factory,
    )

    assert preview == DrivePreview(title="结算单.pdf", page_count=2, pages=tuple(images))
    assert page.visited[0] == "https://alidocs.dingtalk.com/i/nodes/AbCdEf0123456789AbCdEf0123456789"
    # A new profile is initialized once, then opened with the restored session.
    assert launches[0] == {}
    assert launches[-1]["args"] == ["--restore-last-session"]
    assert all(context.closed for context in contexts)


def test_capture_bounds_page_count(monkeypatch, tmp_path) -> None:
    page = _Page(
        final_url="https://alidocs.dingtalk.com/uni-preview",
        elements=[_Element(_png(40, 40)) for _ in range(drive_preview_browser.MAX_PREVIEW_PAGES + 2)],
    )
    factory, _launches, _contexts = _fake_browser(monkeypatch, page)

    preview = capture_drive_preview(
        "AbCdEf0123456789AbCdEf0123456789",
        profile_dir=tmp_path / "profile", playwright_factory=factory,
    )

    assert len(preview.pages) == drive_preview_browser.MAX_PREVIEW_PAGES
    assert preview.truncated


def test_capture_reports_a_missing_login(monkeypatch, tmp_path) -> None:
    page = _Page(final_url="https://login.dingtalk.com/oauth2/challenge", elements=[])
    factory, _launches, contexts = _fake_browser(monkeypatch, page)

    with pytest.raises(DrivePreviewError, match="drive_preview_login_required"):
        capture_drive_preview(
            "AbCdEf0123456789AbCdEf0123456789",
            profile_dir=tmp_path / "profile", playwright_factory=factory,
        )
    assert all(context.closed for context in contexts)


@pytest.mark.parametrize("node_id", ["", "../x", "abc", "AbCdEf0123?x=1"])
def test_capture_refuses_a_node_id_that_is_not_one(node_id, tmp_path) -> None:
    with pytest.raises(DrivePreviewError, match="node_id_invalid"):
        capture_drive_preview(node_id, profile_dir=tmp_path / "profile")


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_preview_tool_returns_pages_then_bounded_ocr_input(monkeypatch, role) -> None:
    captures: list[str] = []

    def capture(node_id):
        captures.append(node_id)
        return DrivePreview(title="结算单.pdf", page_count=1, pages=(_png(2400, 3400),))

    monkeypatch.setattr(drive_preview_browser, "capture_drive_preview", capture)
    tool = agent_cli.build_role_server(role)._tool_manager.get_tool(
        "preview_dingtalk_drive_file"
    )

    assert tool.annotations.readOnlyHint is True
    summary = tool.fn(node_id="AbCdEf0123456789AbCdEf0123456789")
    assert summary == {
        "node_id": "AbCdEf0123456789AbCdEf0123456789", "title": "结算单.pdf",
        "page_count": 1, "ocr_page_numbers": [1], "truncated": False,
    }
    page = tool.fn(node_id="AbCdEf0123456789AbCdEf0123456789", page_number=1)
    assert page["ocr_required"] is True and page["ocr_mime_type"] == "image/jpeg"
    assert page["ocr_original_page_number"] == 1
    assert len(base64.b64decode(page["ocr_input_base64"])) <= agent_cli.MAX_OCR_AGENT_INPUT_BYTES
    # The pages are captured once per MCP process.
    assert captures == ["AbCdEf0123456789AbCdEf0123456789"]
    with pytest.raises(agent_cli.AgentReadOnlyViolationError):
        tool.fn(node_id="AbCdEf0123456789AbCdEf0123456789", page_number=2)
