"""Scheduled daily report receipts preserve the resolved folder evidence."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.agent_cli import _write_bound_report_document, build_role_server
from app.weekly_report_materials import MANAGEMENT_WIKI_NAME


def test_daily_report_facts_tool_uses_schema_only_email_validation(
    tmp_path, monkeypatch
):
    import app.daily_report_facts as daily_report_facts
    import app.email_store as email_store_module

    validations = []
    real_email_store = email_store_module.EmailStore

    def track_email_store(path, **kwargs):
        validations.append(kwargs.get("validate_rows", True))
        return real_email_store(path, **kwargs)

    monkeypatch.setattr(email_store_module, "EmailStore", track_email_store)
    monkeypatch.setattr(
        "app.agent_cli._bound_report",
        lambda db_path, task_id: (object(), SimpleNamespace(id=42), "daily"),
    )
    monkeypatch.setattr(
        daily_report_facts,
        "report_window_for_run",
        lambda store, run_id: object(),
    )
    monkeypatch.setattr(
        daily_report_facts,
        "collect_daily_report_facts",
        lambda store, mail_store, window: {"validated": True},
    )

    tool = build_role_server(
        "audit", db_path=tmp_path / "runtime-report.sqlite3"
    )._tool_manager.get_tool("daily_report_facts").fn
    result = tool()

    assert result == {"validated": True}
    assert validations == [False]


def test_daily_report_facts_command_uses_schema_only_email_validation(
    tmp_path, monkeypatch
):
    import app.cli as cli
    import app.daily_report_facts as daily_report_facts
    import app.email_store as email_store_module

    validations = []
    real_email_store = email_store_module.EmailStore

    def track_email_store(path, **kwargs):
        validations.append(kwargs.get("validate_rows", True))
        return real_email_store(path, **kwargs)

    monkeypatch.setattr(email_store_module, "EmailStore", track_email_store)
    monkeypatch.setattr(
        daily_report_facts,
        "report_window_for_run",
        lambda store, run_id: object(),
    )
    monkeypatch.setattr(
        daily_report_facts,
        "collect_daily_report_facts",
        lambda store, mail_store, window: {"validated": True},
    )

    result = cli.daily_report_facts_command(
        SimpleNamespace(db_path=tmp_path / "runtime-report-cli.sqlite3"),
        scheduled_run_id=42,
    )

    assert result == {"validated": True}
    assert validations == [False]


@pytest.mark.parametrize("document_exists", [False, True])
@pytest.mark.parametrize("url_field", ["url", "docUrl", None])
def test_daily_report_returns_exact_provider_folder(
    tmp_path, monkeypatch, document_exists, url_field,
):
    folder = {
        "nodeId": "daily-folder",
        "name": "CEO 每日总结",
        "type": "folder",
        "workspaceId": "management-wiki",
    }
    if url_field:
        folder[url_field] = "https://alidocs.dingtalk.com/provider-returned-folder-link"
    title = "CEO 每日总结 2026-10-06"
    document = {"nodeId": "daily-document", "name": title}
    writes = []

    class Provider:
        dws_bin = "dws"

        def run_json(self, command):
            if "+space-list" in command:
                return {"data": {"spaces": [{
                    "name": MANAGEMENT_WIKI_NAME, "workspaceId": "management-wiki",
                }]}}
            assert "+node-list" in command
            assert "--page-all" in command
            if "--folder" not in command:
                return {"data": {"nodes": [folder]}}
            assert command[command.index("--folder") + 1] == folder["nodeId"]
            return {"data": {"nodes": [document] if document_exists or writes else []}}

        def create_report_document(self, **kwargs):
            writes.append(("created", kwargs))
            return {"nodeId": document["nodeId"]}

        def overwrite_report_document(self, **kwargs):
            writes.append(("updated", kwargs))
            return {"nodeId": document["nodeId"]}

        def read_doc(self, node_id):
            assert node_id == document["nodeId"]
            return {"content": "# Verified report"}

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    monkeypatch.setattr(
        "app.agent_cli._bound_report",
        lambda db_path, task_id: (object(), SimpleNamespace(id=42), "daily"),
    )
    monkeypatch.setattr(
        "app.daily_report_facts.report_window_for_run",
        lambda store, run_id: SimpleNamespace(report_date=date(2026, 10, 6)),
    )
    result = _write_bound_report_document(
        db_path=tmp_path / "service.sqlite3", task_id=7,
        content="# Verified report", expected_revision=None,
    )

    assert result["folder"] == folder
    assert result["node_id"] == document["nodeId"]
    assert result["title"] == title
    assert result["readback"] == {"content": "# Verified report"}
    assert writes[0][0] == ("updated" if document_exists else "created")
    assert len(writes) == 1
    assert writes[0][1]["content"] == "# Verified report"
    if url_field is None:
        assert "url" not in result["folder"] and "docUrl" not in result["folder"]
