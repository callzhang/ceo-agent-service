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


def test_mcp_doctor_reports_native_memory_and_passthrough_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.memory_connector]
url = "https://memory.example/mcp/"

[mcp_servers.xiaoqing_interview]
url = "https://xiaoqing.example/mcp"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("CEO_CODEX_PASSTHROUGH_MCP_SERVERS", raising=False)

    statuses = check_mcp_statuses(
        codex_config_path=config,
    )
    by_name = {status.name: status for status in statuses}

    assert by_name["memory_connector"].state == "ready"
    assert by_name["memory_connector"].authorization_required is False
    assert by_name["memory_connector"].recover_command == ""
    assert by_name["exa"].ready is True
    assert by_name["xiaoqing_interview"].ready is True


def test_mcp_doctor_reports_missing_memory_config(tmp_path: Path) -> None:
    statuses = check_mcp_statuses(
        codex_config_path=tmp_path / "missing.toml",
    )

    assert statuses[0] == McpStatus(
        name="memory_connector",
        state="missing_config",
        ready=False,
        reason="[mcp_servers.memory_connector] is missing from Codex config",
        recover_command="ceo-agent setup-memory-connector --memory-url <memory-mcp-url>",
    )


def test_mcp_doctor_marks_disabled_passthrough_as_tool_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("CEO_CODEX_PASSTHROUGH_MCP_SERVERS", "exa")

    statuses = check_mcp_statuses(
        codex_config_path=config,
    )
    by_name = {status.name: status for status in statuses}

    assert by_name["exa"].ready is True
    assert by_name["xiaoqing_interview"].state == "tool_not_found"


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
        codex_config_path=tmp_path / "missing.toml",
        notify=False,
    )

    assert report["ok"] is False
    assert not (tmp_path / "mcp-doctor-state.json").exists()
