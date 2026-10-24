"""Generic activity validation and Skill-selected document destinations/readback."""

from datetime import UTC, datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.agent_cli import _write_document_artifact, build_role_server
from app.store import AutoReplyStore


_destination_path = (
    Path(__file__).resolve().parents[1]
    / "ci/shared-skills/ceo-daily-report/scripts/destination.py"
)
_destination_spec = importlib.util.spec_from_file_location(
    "daily_report_destination_tests", _destination_path
)
_destination = importlib.util.module_from_spec(_destination_spec)
sys.modules[_destination_spec.name] = _destination
_destination_spec.loader.exec_module(_destination)


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_service_activity_tool_uses_schema_only_email_validation(
    tmp_path, monkeypatch, role
):
    import app.email_store as email_store_module
    import app.service_activity as service_activity

    validations = []
    real_email_store = email_store_module.EmailStore
    db_path = tmp_path / "runtime-activity.sqlite3"
    store = AutoReplyStore(db_path)

    def track_email_store(path, **kwargs):
        validations.append(kwargs.get("validate_rows", True))
        return real_email_store(path, **kwargs)

    monkeypatch.setattr(email_store_module, "EmailStore", track_email_store)
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (
            store,
            SimpleNamespace(id=42, scheduled_for=datetime(2026, 10, 7, tzinfo=UTC)),
        ),
    )
    calls = []

    def collect(bound_store, mail_store, start, end, sources):
        calls.append(
            (bound_store, isinstance(mail_store, real_email_store), start, end, sources)
        )
        return {"validated": True}

    monkeypatch.setattr(service_activity, "collect_service_activity", collect)
    tool = (
        build_role_server(role, db_path=db_path)
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )
    result = tool("2026-10-06T00:00:00+00:00", "2026-10-07T00:00:00+00:00", ["emails"])

    assert result == {"validated": True}
    assert validations == [False]
    assert calls == [
        (
            store,
            True,
            datetime(2026, 10, 6, tzinfo=UTC),
            datetime(2026, 10, 7, tzinfo=UTC),
            ["emails"],
        )
    ]


@pytest.mark.parametrize(
    "start,end,sources,error",
    [
        ("2026-10-06T00:00:00", "2026-10-07T00:00:00+00:00", [], "aware"),
        ("2026-10-07T00:00:00+00:00", "2026-10-06T00:00:00+00:00", [], "start < end"),
        (
            "2026-10-06T00:00:00+00:00",
            "2026-10-07T00:00:00+00:00",
            ["unknown"],
            "unknown activity source",
        ),
    ],
)
def test_generic_activity_tool_rejects_invalid_interval_or_source(
    tmp_path, monkeypatch, start, end, sources, error
):
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (
            object(),
            SimpleNamespace(id=42, scheduled_for=datetime(2026, 10, 7, tzinfo=UTC)),
        ),
    )
    monkeypatch.setattr("app.email_store.EmailStore", lambda *args, **kwargs: object())
    tool = (
        build_role_server("audit", db_path=tmp_path / "activity.sqlite3")
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )
    with pytest.raises(ValueError, match=error):
        tool(start, end, sources)


def test_scheduled_activity_rejects_window_after_trigger_before_read(
    tmp_path, monkeypatch
):
    scheduled_for = datetime(2026, 10, 7, 13, tzinfo=UTC)
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (object(), SimpleNamespace(id=42, scheduled_for=scheduled_for)),
    )
    monkeypatch.setattr(
        "app.email_store.EmailStore",
        lambda *args, **kwargs: pytest.fail("provider access must not begin"),
    )
    tool = (
        build_role_server("consumer", db_path=tmp_path / "activity.sqlite3")
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )

    with pytest.raises(ValueError, match="cannot end after scheduled_for"):
        tool(
            "2026-10-06T13:00:00+00:00",
            "2026-10-07T16:00:00+00:00",
            ["meetings"],
        )


@pytest.mark.parametrize("document_exists", [False, True])
@pytest.mark.parametrize("url_field", ["url", "docUrl", None])
def test_skill_destination_preserves_exact_provider_folder_and_generic_write_readback(
    tmp_path,
    monkeypatch,
    document_exists,
    url_field,
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
                return {
                    "data": {
                        "spaces": [
                            {"name": "🎯  目标与执行", "workspaceId": "management-wiki"}
                        ]
                    }
                }
            if command[1:3] == ["doc", "+list"]:
                return {
                    "status": "success",
                    "complete": True,
                    "documents": [document] if document_exists or writes else [],
                    "hasMore": False,
                    "failures": [],
                }
            assert "+node-list" in command
            assert "--page-all" in command
            if "--folder" not in command:
                return {"data": {"nodes": [folder]}}
            assert command[command.index("--folder") + 1] == folder["nodeId"]
            return {"data": {"nodes": [document] if document_exists or writes else []}}

        def create_report_document(self, **kwargs):
            writes.append(("created", kwargs))
            return {
                "status": "success",
                "complete": True,
                "data": {"nodeId": document["nodeId"], "verified": True},
            }

        def overwrite_report_document(self, **kwargs):
            writes.append(("updated", kwargs))
            return {"nodeId": document["nodeId"]}

        def read_doc(self, node_id):
            assert node_id == document["nodeId"]
            return {"content": "# Verified report"}

    monkeypatch.setattr(_destination, "run", Provider().run_json)
    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    destination = _destination.resolve("2026-10-06")
    (tmp_path / "report.md").write_text("# Verified report", encoding="utf-8")
    target = (
        {"node_id": destination["node_id"]}
        if document_exists
        else {
            "parent_id": destination["parent_id"],
            "workspace_id": destination["workspace_id"],
        }
    )
    result = _write_document_artifact(
        root=tmp_path,
        artifact_name="report.md",
        title=destination["title"],
        doc_format="markdown",
        **target,
    )

    assert destination["folder"] == folder
    assert result["node_id"] == document["nodeId"]
    assert result["title"] == title
    readback = (tmp_path / result["readback"]["artifact_name"]).read_bytes()
    assert json.loads(readback) == {"content": "# Verified report"}
    assert result["readback"]["sha256"] == hashlib.sha256(readback).hexdigest()
    assert result["readback"]["bytes"] == len(readback)
    assert result["content_sha256"] == hashlib.sha256(b"# Verified report").hexdigest()
    assert result["operation"] == ("updated" if document_exists else "created")
    assert writes[0][0] == result["operation"]
    assert len(writes) == 1
    assert writes[0][1]["content"] == "# Verified report"
    if url_field is None:
        assert (
            "url" not in destination["folder"] and "docUrl" not in destination["folder"]
        )
