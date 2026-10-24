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


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("stage", ["verify_write", "readback"])
def test_applied_write_returns_unverified_result_without_replay(
    tmp_path, monkeypatch, create, stage
):
    error = verification_error(operation="doc.create" if create else "doc.update")
    writes = []
    reads = []

    class Provider:
        dws_bin = "dws"

        def run_json(self, command):
            assert command[1:3] == ["doc", "+list"]
            assert command[command.index("--folder") + 1] == "folder"
            return {
                "status": "success",
                "complete": True,
                "documents": [],
                "hasMore": False,
                "failures": [],
            }

        def create_report_document(self, **kwargs):
            writes.append(kwargs)
            if stage == "verify_write":
                raise DwsError("provider failure", provider_error=error)
            return {
                "status": "success",
                "complete": True,
                "data": {"nodeId": "doc-1", "verified": True},
            }

        def overwrite_report_document(self, **kwargs):
            writes.append(kwargs)
            if stage == "verify_write":
                raise DwsError("provider failure", provider_error=error)
            return {"nodeId": "doc-1", "verified": True}

        def read_doc(self, node):
            reads.append(node)
            raise DwsError("read unavailable", provider_error={"reason": "read_failed"})

    monkeypatch.setattr("app.dws_client.DwsClient", Provider)
    (tmp_path / "report.md").write_text("# Report\n| Value |\n| ---: |\n| 1 |")
    target = {"parent_id": "folder", "workspace_id": "wiki"} if create else {"node_id": "doc-1"}
    result = _write_document_artifact(
        root=tmp_path, artifact_name="report.md", title="Report", doc_format="markdown", **target
    )
    assert result["status"] == "partial_success"
    assert result["ok"] is False and result["verified"] is False
    assert result["node_id"] == "doc-1"
    assert result["readback"] is None
    assert result["error"] == (error if stage == "verify_write" else {"reason": "read_failed"})
    assert len(writes) == 1
    assert len(reads) == (stage == "readback")
    assert not list(tmp_path.glob("*.readback.json"))


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
    assert result["ok"] is False
    assert result["status"] == "partial_success"
    assert result["node_id"] == "created-doc"
    assert result["provider_result"] is None
    assert result["error"] == error
    assert result["readback"] is None
    assert len(writes) == 1
    assert len(listings) == 1


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
                "ok": False, "status": "partial_success", "verified": False,
                "operation": "updated", "node_id": "doc-1", "title": "Report",
                "readback": None, "error": verification_error(),
            }},
        }}],
    )
    assert _scheduled_consumer_work_receipts(task, run) == ()
