"""Regression coverage for task-independent fact/script/document capabilities."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import agent_cli
from app.service_activity import scheduled_context, collect_service_activity

ROOT = Path(__file__).resolve().parents[1]


def script(skill, name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "ci/shared-skills" / skill / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "status,result,offset",
    [("done", "completed", -2), ("done", "skipped", -1), ("failed", "failed", -1)],
)
def test_daily_skill_catchup_window_uses_real_completion_not_done_alone(status, result, offset):
    end = datetime(2026, 10, 19, 13, tzinfo=UTC)
    context = {
        "run_id": 3,
        "scheduled_for": end.isoformat(),
        "history": [
            {
                "run_id": 1,
                "scheduled_for": (end - timedelta(days=2)).isoformat(),
                "task_status": "done",
                "result_status": "completed",
            },
            {
                "run_id": 2,
                "scheduled_for": (end - timedelta(days=1)).isoformat(),
                "task_status": status,
                "result_status": result,
            },
        ],
    }
    window = script("ceo-daily-report", "window").report_window(context)
    expected = end - timedelta(
        days=1 if status == "done" and result == "completed" else 2
    )
    day_start = datetime(2026, 10, 18, 16, tzinfo=UTC)
    assert datetime.fromisoformat(window["window_utc"]["start"]) == day_start
    assert datetime.fromisoformat(window["catchup_window_utc"]["start"]) == expected
    assert datetime.fromisoformat(window["catchup_window_utc"]["end"]) == day_start
    assert window["source_window_utc"] == {
        "start": expected.isoformat(),
        "end": end.isoformat(),
    }
    context["history"].append(
        {
            "run_id": 4,
            "scheduled_for": (end - timedelta(hours=1)).isoformat(),
            "task_status": "done",
            "result_status": "completed",
        }
    )
    rerun = script("ceo-daily-report", "window").report_window(context)
    assert rerun["window_utc"] == window["window_utc"]
    assert rerun["catchup_window_utc"] is None
    assert rerun["source_window_utc"] == rerun["window_utc"]


def test_daily_skill_first_window_and_beijing_date():
    end = datetime(2026, 10, 19, 17, tzinfo=UTC)
    result = script("ceo-daily-report", "window").report_window(
        {"run_id": 1, "scheduled_for": end.isoformat(), "history": []}
    )
    assert result["report_date"] == "2026-10-20"
    assert datetime.fromisoformat(result["window_utc"]["start"]) == datetime(
        2026, 10, 19, 16, tzinfo=UTC
    )
    assert result["catchup_window_utc"] == {
        "start": (end - timedelta(days=1)).isoformat(),
        "end": datetime(2026, 10, 19, 16, tzinfo=UTC).isoformat(),
    }
    assert result["source_window_utc"] == {
        "start": (end - timedelta(days=1)).isoformat(),
        "end": end.isoformat(),
    }


def test_generic_history_exposes_facts_without_report_policy():
    run = SimpleNamespace(
        id=1,
        scheduled_task_id=17,
        scheduled_for=datetime(2026, 10, 19, tzinfo=UTC),
        snapshot=SimpleNamespace(timezone_name="UTC", task_version=9),
        execution_kind="reply_task",
        execution_id="4",
        dispatch_status="dispatched",
        result_summary="",
    )
    store = SimpleNamespace(
        get_scheduled_task_run=lambda _: run,
        list_scheduled_task_runs=lambda _: [run],
        get_reply_task=lambda _: SimpleNamespace(status="done", conversation_id="c"),
        list_reply_attempts_for_conversation=lambda *a, **k: [
            SimpleNamespace(send_status="skipped")
        ],
        get_current_user_id=lambda: "p",
    )
    result = scheduled_context(store, 1)
    assert result["scheduled_task_id"] == 17 and result["task_version"] == 9
    assert result["history"][0]["result_status"] == "skipped"
    assert "report_date" not in result and "window_utc" not in result


def test_generic_sources_are_explicit_and_intervals_are_aware():
    start = datetime(2026, 10, 19, tzinfo=UTC)
    result = collect_service_activity(
        SimpleNamespace(get_current_user_id=lambda: "p"),
        object(),
        start,
        start + timedelta(hours=1),
        sources=[],
    )
    assert result["meetings"] == [] and result["tasks"] == []
    with pytest.raises(ValueError, match="unknown activity source"):
        collect_service_activity(
            object(), object(), start, start + timedelta(hours=1), ["unknown"]
        )
    with pytest.raises(ValueError, match="aware"):
        collect_service_activity(object(), object(), start.replace(tzinfo=None), start)


class FakeDws:
    dws_bin = "dws"

    def __init__(self):
        self.calls = []
        self.nodes = []
        self.parse = "passed"
        self.conflict = False

    def validate_report_jsonml(self, content):
        self.calls.append(("parse", content))
        return {"assessment": self.parse}

    def save_report_document_version(self, node):
        self.calls.append(("save", node))
        return {"version": "v1"}

    def overwrite_report_document(self, **kwargs):
        self.calls.append(("update", kwargs))
        if self.conflict:
            raise ValueError("revision conflict")
        return {"ok": True}

    def create_report_document(self, **kwargs):
        self.calls.append(("create", kwargs))
        self.nodes = [{"name": kwargs["name"], "nodeId": "new"}]
        return {
            "status": "success",
            "complete": True,
            "data": {"nodeId": "new", "verified": True},
        }

    def run_json(self, command):
        self.calls.append(("list", command))
        return {
            "status": "success",
            "complete": True,
            "documents": self.nodes,
            "hasMore": False,
            "failures": [],
        }

    def fetch_report_document_full(self, node):
        return {
            "complete": True,
            "content": {"nodeId": node, "jsonml": ["root", {}, ["p", {}, "real"]]},
        }

    def read_doc(self, node):
        return {"nodeId": node, "text": "real"}


def test_arbitrary_scheduled_document_uses_exact_artifact_and_revision(
    tmp_path, monkeypatch
):
    fake = FakeDws()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: fake)
    content = json.dumps(["root", {}, ["p", {}, "arbitrary report"]])
    (tmp_path / "body.json").write_text(content)
    result = agent_cli._write_document_artifact(
        root=tmp_path,
        artifact_name="body.json",
        title="Any task",
        doc_format="jsonml",
        node_id="node",
        expected_revision=12,
    )
    update = next(c[1] for c in fake.calls if c[0] == "update")
    assert update["content"] == content and update["expected_revision"] == 12
    assert result["operation"] == "updated" and result["node_id"] == "node"
    assert result["ok"] is True and result["readback"] is None
    assert "publishable" not in result


@pytest.mark.parametrize("failure", ["parse", "revision", "appeared"])
def test_generic_document_mechanical_failures_are_not_business_judgments(
    tmp_path, monkeypatch, failure
):
    fake = FakeDws()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: fake)
    (tmp_path / "body.json").write_text('["root",{}]')
    kwargs = dict(
        root=tmp_path,
        artifact_name="body.json",
        title="Any",
        doc_format="jsonml",
        node_id="node",
        expected_revision=2,
    )
    if failure == "parse":
        fake.parse = "failed"
    elif failure == "revision":
        fake.conflict = True
    else:
        fake.nodes = [{"name": "Any", "nodeId": "appeared"}]
        kwargs.update(
            node_id="", expected_revision=None, parent_id="parent", workspace_id="ws"
        )
    with pytest.raises(ValueError):
        agent_cli._write_document_artifact(**kwargs)
    assert not list(tmp_path.glob("*.readback.json"))
    if failure != "revision":
        assert not [x for x in fake.calls if x[0] in {"create", "update"}]


def test_generic_document_creation_preserves_large_body_without_readback(tmp_path, monkeypatch):
    fake = FakeDws()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: fake)
    content = "report body\n" * 15000
    (tmp_path / "body.md").write_text(content)
    result = agent_cli._write_document_artifact(
        root=tmp_path,
        artifact_name="body.md",
        title="Any",
        doc_format="markdown",
        parent_id="parent",
        workspace_id="ws",
    )
    assert next(x[1] for x in fake.calls if x[0] == "create")["content"] == content
    assert result["node_id"] == "new" and result["readback"] is None
    listings = [x[1] for x in fake.calls if x[0] == "list"]
    assert listings == [[
        "dws", "doc", "+list", "--folder", "parent", "--page-all", "--format", "json"
    ]] * 2


def test_tool_catalog_has_no_named_report_gate_and_audit_is_read_only():
    consumer = agent_cli.build_role_server("consumer")._tool_manager
    audit = agent_cli.build_role_server("audit")._tool_manager
    for name in (
        "read_scheduled_context",
        "read_service_activity",
        "read_meeting_archive",
        "read_okr_roster",
        "read_okr_member",
    ):
        assert consumer.get_tool(name) and audit.get_tool(name)
    for name in (
        "daily_report_facts",
        "weekly_report_materials",
        "validate_weekly_report",
        "render_weekly_report",
    ):
        assert consumer.get_tool(name) is None
    assert (
        consumer.get_tool("consumer_run_skill_script")
        and audit.get_tool("consumer_run_skill_script") is None
    )
    params = consumer.get_tool("consumer_document_write").parameters["properties"]
    assert {
        "artifact_name",
        "node_id",
        "parent_id",
        "expected_revision",
    } <= params.keys()
    assert "report" not in params and "manifest" not in params


def test_document_write_preserves_crlf_and_exact_artifact_digest(tmp_path, monkeypatch):
    import hashlib
    fake = FakeDws()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: fake)
    body = b"first\r\nsecond\rthird\n"
    (tmp_path / "body.md").write_bytes(body)
    receipt = agent_cli._write_document_artifact(root=tmp_path, artifact_name="body.md", title="Any", doc_format="markdown", node_id="node")
    sent = next(row[1] for row in fake.calls if row[0] == "update")["content"]
    assert sent.encode("utf-8") == body
    assert receipt["content_sha256"] == hashlib.sha256(body).hexdigest()


def test_document_receipts_preserve_authored_digest_across_rewrites(tmp_path, monkeypatch):
    fake = FakeDws()
    monkeypatch.setattr("app.dws_client.DwsClient", lambda: fake)
    (tmp_path / "body.md").write_bytes(b"first")
    first = agent_cli._write_document_artifact(root=tmp_path, artifact_name="body.md", title="Any", doc_format="markdown", node_id="node")
    saved = dict(first)
    fake.read_doc = lambda node: {"nodeId": node, "text": "changed provider content"}
    (tmp_path / "body.md").write_bytes(b"second")
    second = agent_cli._write_document_artifact(root=tmp_path, artifact_name="body.md", title="Any", doc_format="markdown", node_id="node")
    assert first["content_sha256"] != second["content_sha256"]
    assert first == saved
    assert first["readback"] is None and second["readback"] is None
