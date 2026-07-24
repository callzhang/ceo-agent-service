import re
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit_web import (
    DINGTALK_JSAPI_URL,
    ECHARTS_JS_URL,
    TABULATOR_CSS_URL,
    TABULATOR_JS_URL,
    create_audit_app,
    render_page,
)
from app.store import AutoReplyStore


_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_REMOTE_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*['\"]https?://",
    re.IGNORECASE,
)
_REMOTE_STYLESHEET_RE = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*['\"]stylesheet['\"])[^>]*"
    r"\bhref\s*=\s*['\"]https?://",
    re.IGNORECASE,
)
_INLINE_EVENT_HANDLER_RE = re.compile(r"\s+on[a-z]+\s*=", re.IGNORECASE)
_CSP_NONCE_RE = re.compile(r"\bscript-src 'self' 'nonce-([^']+)'")
_VENDOR_ASSETS = (
    (ECHARTS_JS_URL, "text/javascript"),
    (TABULATOR_JS_URL, "text/javascript"),
    (TABULATOR_CSS_URL, "text/css"),
    (DINGTALK_JSAPI_URL, "text/javascript"),
)


def _local_client(app) -> TestClient:
    return TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )


def _response_csp_nonce(response) -> str:
    match = _CSP_NONCE_RE.search(response.headers["content-security-policy"])
    assert match is not None
    return match.group(1)


def _assert_protected_html(response) -> None:
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    nonce = _response_csp_nonce(response)
    assert f"script-src 'self' 'nonce-{nonce}'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "connect-src 'self'" in policy
    assert "form-action 'self'" in policy
    assert "frame-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'none'" in policy
    assert "https:" not in policy
    assert "http:" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"

    script_tags = _SCRIPT_TAG_RE.findall(response.text)
    assert script_tags
    expected_nonce = f'nonce="{nonce}"'
    assert all(expected_nonce in tag for tag in script_tags)
    assert _REMOTE_SCRIPT_RE.search(response.text) is None
    assert _REMOTE_STYLESHEET_RE.search(response.text) is None
    assert _INLINE_EVENT_HANDLER_RE.search(response.text) is None


def test_render_page_does_not_authorize_script_tags_from_body():
    rendered = render_page(
        "Injection boundary",
        '<script id="untrusted-script">window.untrusted = true;</script>',
    )

    injected = next(
        tag for tag in _SCRIPT_TAG_RE.findall(rendered) if 'id="untrusted-script"' in tag
    )
    authored = [tag for tag in _SCRIPT_TAG_RE.findall(rendered) if tag != injected]
    assert "nonce=" not in injected
    assert authored
    assert all("nonce=" in tag for tag in authored)


def test_audit_pages_use_local_assets_nonce_and_strict_csp(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-security",
        conversation_title="Security review",
        trigger_message_id="msg-security",
        trigger_sender="Reviewer",
        trigger_text="show the history chart",
        action="send_reply",
        sensitivity_kind="general",
    )
    store.update_reply_attempt(attempt_id, send_status="sent")
    store.create_work_project(
        title="CSP rollout",
        category="dev",
        status="active",
        priority="P1",
        risk_level="low",
    )
    client = _local_client(create_audit_app(db_path))

    history = client.get("/")
    tasks = client.get("/tasks")
    bridge = client.get(
        "/dingtalk/open-chat-bridge?conversation_id=cid-security"
    )
    popup = client.get(
        "/open-dingtalk-popup?conversation_id=cid-security"
    )

    for response in (history, tasks, bridge, popup):
        _assert_protected_html(response)

    second_history = client.get("/")
    _assert_protected_html(second_history)
    assert _response_csp_nonce(second_history) != _response_csp_nonce(history)

    assert ECHARTS_JS_URL in history.text
    assert "historyEventChartData" in history.text
    assert "echarts.init" in history.text
    assert TABULATOR_CSS_URL in tasks.text
    assert TABULATOR_JS_URL in tasks.text
    assert 'id="tasks-data"' in tasks.text
    assert "new Tabulator" in tasks.text
    assert DINGTALK_JSAPI_URL in bridge.text
    assert "dd.openChatByConversationId" in bridge.text
    assert "/dingtalk/bridge-status" in bridge.text
    assert "window.dd.ready" in bridge.text
    assert 'fetch("/open-dingtalk?conversation_id=cid-security"' in popup.text


@pytest.mark.parametrize(("asset_url", "expected_media_type"), _VENDOR_ASSETS)
def test_packaged_vendor_assets_are_served_with_safe_mime_types(
    tmp_path: Path,
    asset_url: str,
    expected_media_type: str,
):
    package_asset = resources.files("app").joinpath(asset_url.removeprefix("/"))
    assert package_asset.is_file()
    assert package_asset.read_bytes()

    response = _local_client(create_audit_app(tmp_path / "worker.sqlite3")).get(asset_url)

    assert response.status_code == 200
    media_type = response.headers["content-type"].split(";", 1)[0]
    if expected_media_type == "text/javascript":
        assert media_type in {"text/javascript", "application/javascript"}
    else:
        assert media_type == expected_media_type
    assert response.headers.get("x-content-type-options") is None


def test_static_mount_does_not_expose_application_source(tmp_path: Path):
    client = _local_client(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/static/%2e%2e/audit_web.py")

    assert response.status_code == 404
