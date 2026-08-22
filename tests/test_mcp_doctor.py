import base64
import json
from pathlib import Path

import pytest

from app.mcp_doctor import (
    McpDoctorState,
    McpStatus,
    check_mcp_statuses,
    mcp_doctor_report,
    record_and_notify_mcp_doctor,
)


class FakeStore:
    rows: list[tuple[str | None, str | None, str, str]] = []

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def record_error(
        self,
        conversation_id: str | None,
        message_id: str | None,
        kind: str,
        detail: str,
    ) -> None:
        self.rows.append((conversation_id, message_id, kind, detail))


@pytest.fixture(autouse=True)
def clear_fake_store() -> None:
    FakeStore.rows = []


def _write_manifest(path: Path, servers: dict[str, object]) -> Path:
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return path


def _expired_jwt() -> str:
    payload = base64.urlsafe_b64encode(b'{"exp":1}').decode().rstrip("=")
    return f"header.{payload}.signature"


def test_mcp_doctor_reports_service_manifest_transports_ready(
    tmp_path: Path,
) -> None:
    config = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "memory_connector": {"url": "https://memory.example/mcp/"},
            "exa": {"url": "https://mcp.exa.ai/mcp"},
            "xiaoqing_interview": {
                "command_env": "CEO_XIAOQING_MCP_COMMAND",
                "args_env": "CEO_XIAOQING_MCP_ARGS_JSON",
            },
        },
    )

    statuses = check_mcp_statuses(
        service_config_path=config,
        env={
            "CEO_XIAOQING_MCP_COMMAND": "/opt/service/xiaoqing-mcp",
            "CEO_XIAOQING_MCP_ARGS_JSON": "[]",
        },
    )
    by_name = {status.name: status for status in statuses}

    assert by_name["memory_connector"].state == "ready"
    assert by_name["memory_connector"].authorization_required is False
    assert by_name["memory_connector"].recover_command == ""
    assert by_name["exa"].ready is True
    assert by_name["xiaoqing_interview"].ready is True


def test_mcp_doctor_reports_missing_service_manifest(tmp_path: Path) -> None:
    statuses = check_mcp_statuses(
        service_config_path=tmp_path / "missing.json",
        env={},
    )

    assert statuses[0] == McpStatus(
        name="service_mcp_config",
        state="missing_config",
        ready=False,
        reason=(
            "service MCP manifest is missing; set CEO_SERVICE_MCP_CONFIG_PATH "
            "to an existing JSON file"
        ),
        recover_command="configure CEO_SERVICE_MCP_CONFIG_PATH",
    )


def test_mcp_doctor_reports_invalid_utf8_manifest_as_malformed_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "service-mcp.json"
    config.write_bytes(b'{"servers":"must-not-appear\xff"}')

    statuses = check_mcp_statuses(service_config_path=config, env={})

    assert statuses == [
        McpStatus(
            name="service_mcp_config",
            state="missing_config",
            ready=False,
            reason="service MCP manifest is not valid UTF-8",
            recover_command="configure CEO_SERVICE_MCP_CONFIG_PATH",
        )
    ]
    assert "must-not-appear" not in repr(statuses)


def test_mcp_doctor_reports_missing_xiaoqing_command_without_secret_values(
    tmp_path: Path,
) -> None:
    config = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "memory_connector": {
                "url_env": "MEMORY_CONNECTOR_URL",
                "bearer_token_env_var": "CONNECTOR_API_KEY",
            },
            "xiaoqing_interview": {
                "command_env": "CEO_XIAOQING_MCP_COMMAND",
                "args_env": "CEO_XIAOQING_MCP_ARGS_JSON",
            },
        },
    )
    secret = "must-not-appear"

    statuses = check_mcp_statuses(
        service_config_path=config,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CONNECTOR_API_KEY": secret,
            "CEO_XIAOQING_MCP_ARGS_JSON": "[]",
        },
    )
    by_name = {status.name: status for status in statuses}

    assert by_name["memory_connector"].ready is False
    assert by_name["memory_connector"].state == "missing_config"
    assert by_name["xiaoqing_interview"] == McpStatus(
        name="xiaoqing_interview",
        state="missing_config",
        ready=False,
        reason="service transport command is not configured",
        recover_command="fix xiaoqing_interview in the service MCP manifest",
    )
    assert secret not in repr(statuses)


def test_mcp_doctor_reports_expired_manifest_bearer_token(tmp_path: Path) -> None:
    config = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "memory_connector": {
                "url": "https://memory.example/mcp/",
                "bearer_token_env_var": "CONNECTOR_API_KEY",
            }
        },
    )

    statuses = check_mcp_statuses(
        service_config_path=config,
        env={"CONNECTOR_API_KEY": _expired_jwt()},
    )

    assert statuses == [
        McpStatus(
            name="memory_connector",
            state="token_expired",
            ready=False,
            reason="configured bearer token is expired",
            authorization_required=True,
            recover_command="configure CONNECTOR_API_KEY",
        )
    ]


def test_mcp_doctor_live_probe_uses_bearer_and_rejects_unauthorized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "memory_connector": {
                "url": "https://memory.example/mcp/",
                "bearer_token_env_var": "CONNECTOR_API_KEY",
            }
        },
    )
    observed: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://memory.example/mcp/")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("secret response", request=request, response=response)

    class Client:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            observed["url"] = url
            observed["headers"] = kwargs.get("headers")
            assert kwargs["json"]["method"] == "initialize"
            return Response()

    import httpx

    monkeypatch.setattr("app.mcp_doctor.httpx.Client", Client)

    statuses = check_mcp_statuses(
        service_config_path=config,
        env={"CONNECTOR_API_KEY": "must-not-appear"},
        verify_live=True,
    )

    assert observed["headers"]["Authorization"] == "Bearer must-not-appear"
    assert statuses[0].state == "needs_login"
    assert statuses[0].reason == "HTTP authorization failed"
    assert "must-not-appear" not in repr(statuses)


def test_mcp_doctor_live_probe_checks_xiaoqing_url(tmp_path: Path, monkeypatch) -> None:
    config = _write_manifest(
        tmp_path / "service-mcp.json",
        {"xiaoqing_interview": {"url": "https://xiaoqing.example/mcp"}},
    )

    class Response:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://xiaoqing.example/mcp")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError(
                "authorization required",
                request=request,
                response=response,
            )

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            assert url == "https://xiaoqing.example/mcp"
            assert kwargs["json"]["method"] == "initialize"
            return Response()

    import httpx

    monkeypatch.setattr("app.mcp_doctor.httpx.Client", Client)

    [status] = check_mcp_statuses(
        service_config_path=config,
        env={},
        verify_live=True,
    )

    assert status == McpStatus(
        name="xiaoqing_interview",
        state="needs_login",
        ready=False,
        reason="HTTP authorization failed",
        authorization_required=True,
        recover_command="ceo-agent doctor-mcp --verify-live",
    )


def test_mcp_doctor_notification_is_sent_once(tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []
    status = McpStatus(
        name="memory_connector",
        state="needs_login",
        ready=False,
        reason="authorization required",
        authorization_required=True,
        recover_command="codex mcp login memory_connector",
    )

    for _ in range(2):
        record_and_notify_mcp_doctor(
            db_path=tmp_path / "auto-reply.sqlite3",
            statuses=[status],
            notification_sender=lambda title, message: sent.append((title, message)),
            store_factory=FakeStore,
        )

    assert len(sent) == 1
    assert sent[0][0] == "CEO MCP needs authorization: memory_connector"
    assert len(FakeStore.rows) == 1
    assert McpDoctorState(tmp_path / "mcp-doctor-state.json").should_notify(status) is False


def test_mcp_doctor_report_is_read_only_without_notify(tmp_path: Path) -> None:
    report = mcp_doctor_report(
        db_path=tmp_path / "auto-reply.sqlite3",
        service_config_path=tmp_path / "missing.json",
        env={},
        notify=False,
    )

    assert report["ok"] is False
    assert not (tmp_path / "mcp-doctor-state.json").exists()
