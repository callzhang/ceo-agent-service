"""Applied document writes keep provider failure evidence without replay."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.agent_cli import _write_document_artifact
from app.dws_client import DwsClient, DwsError


def verification_error(node="doc-1", operation="doc.update"):
    return {
        "category": "api", "code": 1,
        "message": "Written, readback did not match",
        "reason": "doc_write_verification_failed", "operation": operation,
        "execution_started": True, "retryable": False,
        "details": {
            "contractVersion": "doc.operation.v1", "status": "partial_success",
            "data": {"nodeId": node, "verified": False},
            "steps": [{"name": "update_document", "status": "success"},
                      {"name": "verify", "status": "failed"}],
        },
    }


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_provider_error_survives_command_error_formatting(monkeypatch, stream):
    error = verification_error()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1, **{stream: json.dumps({"error": error}),
                           ("stderr" if stream == "stdout" else "stdout"): ""}
        )

    monkeypatch.setattr("app.dws_client.DwsClient._run_cli_process", staticmethod(run))
    with pytest.raises(DwsError) as raised:
        DwsClient(dws_bin="dws").overwrite_report_document(
            node_id="doc-1", content="| Value |\n| ---: |\n| 1 |", doc_format="markdown"
        )
    assert raised.value.provider_error == error
    assert len(calls) == 1


@pytest.mark.parametrize("error", [
    {"reason": "permission_denied"},
    {"reason": "doc_write_commit_unknown", "details": {"status": "unknown"}},
    verification_error(operation="doc.version_save"),
])
def test_unapplied_write_remains_an_error(tmp_path, monkeypatch, error):
    class Provider:
        def overwrite_report_document(self, **kwargs):
            raise DwsError("access denied", provider_error=error)

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    (tmp_path / "report.md").write_text("# Report")
    with pytest.raises(DwsError, match="access denied"):
        _write_document_artifact(
            root=tmp_path, artifact_name="report.md", title="Report",
            doc_format="markdown", node_id="doc-1"
        )
    assert not list(tmp_path.glob("*.readback.json"))


def test_partial_create_receipt_keeps_identity_without_post_write_listing(
    tmp_path, monkeypatch
):
    writes = []
    listings = []
    error = verification_error(node="created-doc", operation="doc.create")

    class Provider:
        dws_bin = "dws"

        def run_json(self, command):
            listings.append(command)
            return {
                "status": "success",
                "complete": True,
                "documents": [],
                "hasMore": False,
                "failures": [],
            }

        def create_report_document(self, **kwargs):
            writes.append(kwargs)
            raise DwsError("provider verification failed", provider_error=error)

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    (tmp_path / "report.md").write_text("# Report")
    result = _write_document_artifact(
        root=tmp_path, artifact_name="report.md", title="Report", doc_format="markdown",
        parent_id="folder", workspace_id="wiki"
    )
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["node_id"] == "created-doc"
    assert result["provider_result"] is None
    assert result["error"] == error
    assert result["readback"] is None
    assert len(writes) == 1
    assert len(listings) == 1


@pytest.mark.parametrize(
    "post_listing",
    [
        {
            "status": "success",
            "complete": True,
            "documents": [],
            "hasMore": True,
            "failures": [],
        },
        {
            "status": "success",
            "complete": True,
            "documents": [{"name": "Different", "nodeId": "created-doc"}],
            "hasMore": False,
            "failures": [],
        },
        {
            "status": "success",
            "complete": True,
            "documents": [
                {"name": "Report", "nodeId": "created-doc"},
                {"name": "Report", "nodeId": "duplicate-doc"},
            ],
            "hasMore": False,
            "failures": [],
        },
        None,
    ],
    ids=["incomplete", "wrong-identity", "ambiguous", "listing-error"],
)
def test_created_identity_survives_failed_target_listing(
    tmp_path, monkeypatch, post_listing
):
    listings = []
    provider_result = {
        "status": "success",
        "complete": True,
        "data": {"nodeId": "created-doc"},
    }

    class Provider:
        dws_bin = "dws"

        def run_json(self, command):
            listings.append(command)
            if len(listings) == 1:
                return {
                    "status": "success",
                    "complete": True,
                    "documents": [],
                    "hasMore": False,
                    "failures": [],
                }
            if post_listing is None:
                raise DwsError("listing unavailable")
            return post_listing

        def create_report_document(self, **kwargs):
            return provider_result

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    (tmp_path / "report.md").write_text("# Report")
    result = _write_document_artifact(
        root=tmp_path,
        artifact_name="report.md",
        title="Report",
        doc_format="markdown",
        parent_id="folder",
        workspace_id="wiki",
    )
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["node_id"] == "created-doc"
    assert result["provider_result"] == provider_result
    assert result["readback"] is None
    assert len(listings) == 2
    assert listings == [[
        "dws", "doc", "+list", "--folder", "folder", "--page-all", "--format", "json",
    ]] * 2


def test_unverified_document_result_is_not_a_successful_audit_receipt():
    from app.agent_orchestrator import _scheduled_consumer_work_receipts
    from app.store import AgentRole

    task = SimpleNamespace(id=42, channel="scheduled", execution_generation="g1")
    run = SimpleNamespace(
        id=77, reply_task_id=42, execution_generation="g1",
        role=AgentRole.CONSUMER, status="completed",
        tool_events=[{"type": "item.completed", "item": {
            "type": "mcp_tool_call", "server": "agent_cli", "tool": "consumer_document_write",
            "result": {"structuredContent": {
                "ok": True, "status": "success", "verified": False,
                "operation": "updated", "node_id": "doc-1", "title": "Report",
                "readback": None, "error": verification_error(),
            }},
        }}],
    )
    assert _scheduled_consumer_work_receipts(task, run) == ()


@pytest.mark.parametrize("doc_format", ["markdown", "jsonml"])
@pytest.mark.parametrize("create", [False, True])
def test_successful_write_does_not_read_or_verify_content(tmp_path, monkeypatch, doc_format, create):
    class Provider:
        dws_bin = "dws"

        def __init__(self):
            self.written = False

        def run_json(self, command):
            return {
                "status": "success",
                "complete": True,
                "documents": (
                    [{"name": "Report", "nodeId": "doc-1"}]
                    if self.written else []
                ),
                "hasMore": False,
                "failures": [],
            }

        def create_report_document(self, **kwargs):
            self.written = True
            return {"nodeId": "doc-1", "success": True}

        def validate_report_jsonml(self, content):
            return {"assessment": "passed"}

        def save_report_document_version(self, node):
            return {"version": "v1"}

        def overwrite_report_document(self, **kwargs):
            return {"nodeId": "doc-1", "success": True}

        def read_doc(self, node):
            pytest.fail("successful write must not read back content")

        fetch_report_document_full = read_doc

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    (tmp_path / "body").write_text('["root", {}, ["p", {}, "report"]]' if doc_format == "jsonml" else "# Report")
    result = _write_document_artifact(root=tmp_path, artifact_name="body", title="Report",
        doc_format=doc_format, **({"parent_id": "folder", "workspace_id": "ws"} if create else
            {"node_id": "doc-1", "expected_revision": 7 if doc_format == "jsonml" else None}))
    assert result["ok"] is True and result["status"] == "success"
    assert result["provider_result"] == {"nodeId": "doc-1", "success": True}
    assert result["readback"] is None and "verified" not in result
    assert not list(tmp_path.glob("*.readback.json"))


@pytest.mark.parametrize("create", [False, True])
def test_document_uses_native_commands_without_content_verification(monkeypatch, create):
    calls = []
    monkeypatch.setattr(DwsClient, "run_json", lambda self, command: calls.append(command) or {"nodeId": "doc-1"})
    client = DwsClient(dws_bin="dws")
    if create:
        client.create_report_document(name="Report", content="# Report", doc_format="markdown", folder_id="folder")
    else:
        client.overwrite_report_document(node_id="doc-1", content="[]", doc_format="jsonml", expected_revision=7)
    assert calls[0][1:3] == ["doc", "create" if create else "update"]
    assert "--content-format" in calls[0]
    if not create:
        assert calls[0][calls[0].index("--revision")+1] == "7"
