import time

import app.audit_web as audit_web_module
from tests.test_console_web_api import _client


def test_spa_startup_does_not_render_legacy_history_html(tmp_path, monkeypatch):
    legacy_render_calls: list[bool] = []

    def observe_legacy_render(*_args, **_kwargs):
        legacy_render_calls.append(True)
        return "legacy history html"

    monkeypatch.setattr(audit_web_module, "render_attempt_list", observe_legacy_render)

    with _client(tmp_path, spa_enabled=True, asset=b"<!doctype html><title>React console</title>") as client:
        response = client.get("/history")
        time.sleep(0.05)

    assert response.status_code == 200
    assert response.content == b"<!doctype html><title>React console</title>"
    assert legacy_render_calls == []
