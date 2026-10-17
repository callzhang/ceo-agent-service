import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from mcp.server.fastmcp.exceptions import ToolError

import app.agent_cli as agent_cli
from app.agent_result import EffectKind
from app.feedback_spike import prepare_outgoing_reply_text
from app.native_cli_metadata import NativeCliMetadataClassifier
from app.native_cli_metadata import AgentReadOnlyViolationError


def test_weekly_report_tool_declares_metric_array_and_rejects_object_mapping():
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    arguments = {"manifest": {}, "report": {"company_metrics": {"orders": {"current": "无数据"}}}, "previous_issues": []}
    with pytest.raises(ValidationError) as error:
        tool.fn_metadata.arg_model.model_validate(arguments)
    assert ("report", "company_metrics") in [entry["loc"] for entry in error.value.errors()]
    report_schema = tool.parameters["$defs"]["WeeklyReport"]
    assert report_schema["properties"]["company_metrics"]["type"] == "array"


def test_weekly_report_tool_preserves_complete_input_and_business_checks():
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    arguments = {
        "manifest": {"future_metadata": {"nested": [None, True, 3]}},
        "report": {"company_metrics": [{"key": "orders", "current": "无数据", "future_value": {"a": [1, None]}}], "future_section": {"data": True}},
        "previous_issues": [{"id": "COMP-001", "future_evidence": [False]}],
    }
    assert tool.fn_metadata.arg_model.model_validate(arguments).model_dump() == arguments


@pytest.mark.parametrize("field", ["minutes", "messages", "target", "previous", "business_reports"])
def test_weekly_report_tool_rejects_list_where_manifest_requires_object(field):
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    with pytest.raises(ValidationError) as error:
        tool.fn_metadata.arg_model.model_validate({"manifest": {field: []}, "report": {}, "previous_issues": []})
    assert ("manifest", field) in [entry["loc"] for entry in error.value.errors()]


def test_weekly_report_native_tool_reports_shape_error_before_business_call(monkeypatch):
    def unexpected_business_call(*args):
        pytest.fail("malformed input reached business validation")

    monkeypatch.setattr(agent_cli, "_bound_report", unexpected_business_call)
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    with pytest.raises(ToolError, match=r"report.company_metrics"):
        asyncio.run(tool.run({"manifest": {}, "report": {"company_metrics": {"orders": {}}}, "previous_issues": []}))


def test_weekly_report_tool_preserves_nullable_business_values():
    arguments = {"manifest": {}, "report": {
        "company_metrics": [{"key": "orders", "current": "无数据", "data_date": None, "decision_critical": False, "responsible_user_id": None}],
        "issues": [{"id": "COMP-001", "deadline": None, "as_of": None, "status": None}],
        "claims": [{"type": "hypothesis_pending_validation", "evidence": None}],
    }, "previous_issues": []}
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    assert tool.fn_metadata.arg_model.model_validate(arguments).model_dump() == arguments


def test_weekly_report_render_and_write_use_service_bound_snapshot_schema():
    server = agent_cli.build_role_server("consumer")
    render = server._tool_manager.get_tool("render_weekly_report")
    write = server._tool_manager.get_tool("consumer_document_write")
    assert set(render.fn_metadata.arg_model.model_fields) == {"report"}
    assert set(write.fn_metadata.arg_model.model_fields) == {"content"}


@pytest.mark.parametrize(
    ("target_exists", "expected_source", "expected_revision"),
    [(True, "target-node", 42), (False, "previous-node", None)],
)
def test_weekly_report_render_source_uses_complete_bound_document(
    monkeypatch, target_exists, expected_source, expected_revision,
):
    import app.weekly_report_materials as materials_module

    materials = {
        "documents": {
            "target": {
                "title": "target meeting",
                "exists": target_exists,
                "node_id": "target-node" if target_exists else None,
            },
            "previous": {
                "title": "previous meeting",
                "exists": True,
                "node_id": "previous-node",
            },
        }
    }
    monkeypatch.setattr(
        materials_module,
        "resolve_meeting_documents",
        lambda _dws, _window: materials["documents"],
    )
    monkeypatch.setattr(materials_module, "weekly_window", lambda _run_at: "window")

    class Dws:
        def __init__(self):
            self.requested = []

        def fetch_report_document_full(self, node_id):
            self.requested.append(node_id)
            return {
                "complete": True,
                "content": {
                    "success": True,
                    "nodeId": node_id,
                    "revision": "42",
                    "jsonml": json.dumps(["root", {}, ["h1", {}, "Heading"]]),
                },
            }

    dws = Dws()
    before, actual_exists, revision, title = agent_cli._weekly_report_render_source(
        type("Run", (), {"scheduled_for": object()})(), dws
    )
    assert dws.requested == [expected_source]
    assert before[0] == "root"
    assert actual_exists is target_exists
    assert revision == expected_revision
    assert title == "target meeting"


@pytest.mark.parametrize(
    ("target_exists", "expected_source", "expected_revision"),
    [(True, "target-node", 42), (False, "previous-node", None)],
)
def test_weekly_report_render_and_write_bind_source_revision_and_content(
    monkeypatch, tmp_path, target_exists, expected_source, expected_revision,
):
    import types
    import app.dws_client as dws_module
    import app.weekly_report_materials as materials_module

    materials = {
        "target": {
            "title": "target meeting",
            "exists": target_exists,
            "node_id": "target-node" if target_exists else None,
        },
        "previous": {
            "title": "previous meeting",
            "exists": True,
            "node_id": "previous-node",
        },
    }
    monkeypatch.setattr(
        materials_module, "resolve_meeting_documents", lambda *_args: materials
    )
    monkeypatch.setattr(materials_module, "weekly_window", lambda _run_at: "window")

    class Dws:
        requested = None

        def fetch_report_document_full(self, node_id):
            self.requested = node_id
            return {
                "complete": True,
                "content": {
                    "success": True,
                    "nodeId": node_id,
                    "revision": "42",
                    "jsonml": json.dumps(["root", {}, ["h1", {}, "Heading"]]),
                },
            }

        def validate_report_jsonml(self, _content):
            return {"assessment": "passed"}

    dws = Dws()
    monkeypatch.setattr(dws_module, "DwsClient", lambda: dws)
    monkeypatch.setattr(
        agent_cli,
        "_bound_report",
        lambda *_args: (object(), type("Run", (), {"scheduled_for": "now"})(), "weekly"),
    )
    monkeypatch.setattr(agent_cli, "_current_task_generation", lambda *_args: "generation")
    rendered_jsonml = ["root", {}, ["h1", {}, "Rendered"]]
    renderer = types.SimpleNamespace(
        render_meeting_document=lambda before, _report: rendered_jsonml,
        render_draft=lambda _report: ["root", {}],
    )
    checker = types.SimpleNamespace(check_document=lambda _document: [])
    monkeypatch.setattr(
        agent_cli,
        "_weekly_skill_module",
        lambda name: renderer if name == "render_dingtalk_jsonml" else checker,
    )
    writes = []
    monkeypatch.setattr(
        agent_cli,
        "_write_bound_report_document",
        lambda **kwargs: writes.append(kwargs) or {"operation": "created"},
    )

    server = agent_cli.build_role_server(
        "consumer", task_id=1, db_path=tmp_path / "db.sqlite", execution_generation="generation"
    )
    rendered = server._tool_manager.get_tool("render_weekly_report").fn(report={})
    assert dws.requested == expected_source
    assert rendered["target_exists"] is target_exists
    assert rendered["target_title"] == "target meeting"
    assert rendered["expected_revision"] == expected_revision
    content = json.dumps(rendered_jsonml, ensure_ascii=False)
    write = server._tool_manager.get_tool("consumer_document_write").fn
    with pytest.raises(ValueError, match="differs from rendered result"):
        write(content=json.dumps(["root", {}, ["h1", {}, "different"]]))
    assert writes == []
    write(content=content)
    assert writes[0]["expected_revision"] == expected_revision
    assert writes[0]["expected_target_exists"] is target_exists
    assert writes[0]["content"] == content


@pytest.mark.parametrize(
    ("expected_exists", "currently_exists"), [(True, False), (False, True)]
)
def test_weekly_report_write_rejects_target_existence_change(
    monkeypatch, tmp_path, expected_exists, currently_exists,
):
    import types
    import app.config as config_module
    import app.dws_client as dws_module
    import app.weekly_report_materials as materials_module

    target = {
        "title": "target meeting",
        "year_page_id": "year-page",
        "year_page_name": "2026 年：管理层周会",
        "exists": currently_exists,
    }
    materials = {
        "documents": {
            "workspace_id": "workspace",
            "meeting_folder_id": "meeting-folder",
            "target": target,
            "previous": {"exists": True},
        },
    }
    monkeypatch.setattr(config_module, "workspace_path", lambda: tmp_path)
    monkeypatch.setattr(
        materials_module,
        "collect_weekly_report_materials",
        lambda *_args: materials,
    )
    monkeypatch.setattr(
        agent_cli,
        "_bound_report",
        lambda *_args: (object(), type("Run", (), {"scheduled_for": "now"})(), "weekly"),
    )
    monkeypatch.setattr(
        agent_cli,
        "_weekly_skill_module",
        lambda _name: types.SimpleNamespace(check_document=lambda _document: []),
    )

    class Dws:
        dws_bin = "dws"

        def __init__(self):
            self.commands = []

        def run_json(self, command):
            self.commands.append(command)
            return {
                "data": {
                    "nodes": ([{"nodeId": "target-node", "name": "target meeting"}]
                              if currently_exists else [])
                }
            }

        def validate_report_jsonml(self, _content):
            return {"assessment": "passed"}

    dws = Dws()
    monkeypatch.setattr(dws_module, "DwsClient", lambda: dws)
    with pytest.raises(ValueError, match="target existence changed"):
        agent_cli._write_bound_report_document(
            db_path=None,
            task_id=None,
            content='["root",{}]',
            expected_revision=42 if expected_exists else None,
            expected_target_exists=expected_exists,
        )
    assert len(dws.commands) == 1


@pytest.mark.parametrize("exists", [True, False])
def test_weekly_report_manifest_schema_exposes_target_existence(exists):
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
    arguments = {
        "manifest": {"target": {"title": "Meeting", "exists": exists, "year_page_id": None}},
        "report": {},
        "previous_issues": [],
    }
    validated = tool.fn_metadata.arg_model.model_validate(arguments).model_dump()
    assert validated["manifest"]["target"]["exists"] is exists


def test_weekly_report_complete_skill_fixture_remains_publishable():
    # Isolate the standalone Skill's `scripts` package from the service package.
    code = '''
import pathlib, runpy, sys
from app.agent_cli import build_role_server
root = pathlib.Path("ci/shared-skills/ceo-weekly-report").resolve()
sys.path.insert(0, str(root))
fixture = runpy.run_path(str(root / "tests/test_report_contract.py"))
sample = fixture["ReportContractTests"]()
report = sample.complete_report()
report["company_metrics"][0]["data_date"] = None
report["issues"] = [{"id": "COMP-001", "deadline": None}]
report["claims"] = [{"type": "hypothesis_pending_validation", "evidence": None}]
arguments = {"manifest": sample.complete_manifest(), "report": report, "previous_issues": []}
tool = build_role_server("consumer")._tool_manager.get_tool("validate_weekly_report")
validated = tool.fn_metadata.arg_model.model_validate(arguments).model_dump()
assert validated == arguments
assert fixture["validate_run"](**arguments) == fixture["validate_run"](**validated) == {"publishable": True, "errors": []}
'''
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)


def test_agent_cli_mcp_tools_publish_searchable_descriptions():
    tools = asyncio.run(agent_cli.server.list_tools())
    descriptions = {tool.name: tool.description for tool in tools}

    assert set(descriptions) == {
        "read_skill",
        "read_text_file",
        "read_spreadsheet",
    }
    assert all(description.strip() for description in descriptions.values())
    assert "PDF" in descriptions["read_text_file"]
    assert "task-bound server catalog" in agent_cli.server.instructions
    assert "execute_email_unsubscribe" not in descriptions


def test_audited_email_unsubscribe_tool_reaches_worker_helper(monkeypatch, tmp_path):
    import app.config as config
    import app.email_worker as email_worker

    calls = []
    accepted_action = {"operation": "unsubscribe", "target": {"id": "opaque"}}
    expected = {"status": "done", "summary": "fake result"}
    db_path = tmp_path / "worker.sqlite3"
    monkeypatch.setattr(config, "worker_db_path", lambda: db_path)

    def run_helper(
        received_db_path,
        task_id,
        execution_generation,
        *,
        audit_agent_run_id,
        accepted_action,
    ):
        calls.append(
            (
                received_db_path,
                task_id,
                execution_generation,
                audit_agent_run_id,
                accepted_action,
            )
        )
        return expected

    monkeypatch.setattr(email_worker, "run_audited_email_unsubscribe", run_helper)

    result = asyncio.run(
        agent_cli.execute_audited_email_unsubscribe_tool(
            17,
            "generation-17",
            29,
            accepted_action,
        )
    )

    assert result is expected
    assert calls == [(db_path, 17, "generation-17", 29, accepted_action)]


def test_unregistered_audited_unsubscribe_helper_does_not_block_event_loop(
    monkeypatch,
    tmp_path,
):
    import app.config as config
    import app.email_worker as email_worker

    release = threading.Event()
    db_path = tmp_path / "worker.sqlite3"
    monkeypatch.setattr(config, "worker_db_path", lambda: db_path)

    def run_helper(*_args, **_kwargs):
        assert release.wait(timeout=2)
        return {"status": "done", "summary": "fake result"}

    monkeypatch.setattr(email_worker, "run_audited_email_unsubscribe", run_helper)

    async def exercise():
        started = time.monotonic()
        timer = threading.Timer(0.3, release.set)
        timer.start()
        try:
            call = asyncio.create_task(
                agent_cli.execute_audited_email_unsubscribe_tool(
                    17, "generation-17", 29,
                    {"operation": "unsubscribe", "target": {"id": "opaque"}},
                )
            )
            await asyncio.sleep(0.05)
            responsive_after = time.monotonic() - started
            result = await call
        finally:
            release.set()
            timer.join(timeout=1)
        return responsive_after, result

    responsive_after, result = asyncio.run(exercise())

    assert responsive_after < 0.15
    assert result == {"status": "done", "summary": "fake result"}


def test_registered_reaction_write_is_accepted_when_dws_schema_is_incomplete(monkeypatch):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda name: f"/test/bin/{name}")
    class Unclassified:
        @staticmethod
        def classify(_item):
            return None

    argv = [
        "dws",
        "chat",
        "message",
        "reaction",
        "add",
        "--message-id",
        "msg-1",
        "--emoji",
        "👍",
        "--yes",
    ]

    classifier = Unclassified()
    canonical, command = agent_cli._classify_reviewed_write(argv, classifier=classifier)

    assert canonical == tuple(argv)
    assert command.cli == "dws"
    assert command.command_path == "chat message reaction add"
    assert command.effect is EffectKind.EFFECTFUL

    authorization = agent_cli.review_write_authorization(
        argv,
        authorization_id="reaction-authorization",
        action_index=0,
        classifier=classifier,
    )
    receipt = agent_cli.execute_reviewed_write(
        argv,
        authorization_id="reaction-authorization",
        action_index=0,
        authorization=authorization,
        authorization_consumer=lambda consumed: None,
        classifier=classifier,
        process_runner=lambda command, **_: subprocess.CompletedProcess(
            command, 0, '{"success":true}', ""
        ),
    )

    assert receipt["operation"] == "chat message reaction add"
    assert receipt["authorization_id"] == "reaction-authorization"


def test_dws_message_allows_exact_service_feedback_callbacks_in_content(
    monkeypatch: pytest.MonkeyPatch,
):
    base_url = "https://feedback.example.test"
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", base_url)
    content = prepare_outgoing_reply_text(
        reply_text="已完成复核。",
        original_text="触发消息",
        feedback_base_url=base_url,
        feedback_token="spike_1787501265_e576c9d1",
    ).text
    argv = [
        "dws",
        "chat",
        "+send-to-group",
        "--group",
        "cid-agent",
        "--content",
        content,
        "--yes",
    ]

    assert agent_cli._validate_reviewed_argv(argv) == tuple(argv)


def test_dws_message_rejects_non_service_tokenized_url_in_content(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://feedback.example.test"
    )
    argv = [
        "dws",
        "chat",
        "+send-to-group",
        "--group",
        "cid-agent",
        "--content",
        "请访问 https://attacker.example/api?feedback_token=opaque-token&rating=up",
        "--yes",
    ]

    with pytest.raises(AgentReadOnlyViolationError, match="sensitive_argument"):
        agent_cli._validate_reviewed_argv(argv)


def test_read_text_file_reads_bounded_temp_material(tmp_path: Path):
    material = tmp_path / "material.md"
    material.write_text("# Verified material", encoding="utf-8")

    result = agent_cli.read_text_file(str(material))

    assert result["content"] == "# Verified material"
    assert result["path"] == str(material.resolve())
    assert result["sha256"]


def test_dingtalk_drive_file_downloads_exact_node_and_extracts_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Dws:
        def download_drive_file(self, node, *, file_name, space_id, max_bytes):
            assert (node, file_name, space_id) == ("node-1", "proof.pdf", "space-1")
            assert max_bytes == agent_cli.MAX_PDF_BYTES
            return b"pdf-bytes"

    monkeypatch.setattr(
        agent_cli,
        "read_text_file",
        lambda path: {"format": "pdf", "content": "settlement evidence", "path": path},
    )
    result = agent_cli._download_dingtalk_drive_file(
        dws=Dws(), node_id="node-1", space_id="space-1",
        file_name="proof.pdf", material_root=tmp_path,
    )

    assert result["content"] == "settlement evidence"
    assert result["node_id"] == "node-1"
    assert Path(result["path"]).read_bytes() == b"pdf-bytes"
    assert result.get("ocr_required") is None


def test_dingtalk_drive_file_propagates_ocr_failure_and_cleans_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Dws:
        def download_drive_file(self, *_args, **_kwargs):
            return b"scan"

    def unreadable(_path):
        raise AgentReadOnlyViolationError("pdf_material_ocr_unavailable")

    monkeypatch.setattr(agent_cli, "read_text_file", unreadable)
    with pytest.raises(AgentReadOnlyViolationError, match="pdf_material_ocr_unavailable"):
        agent_cli._download_dingtalk_drive_file(
            dws=Dws(), node_id="scan-1", space_id="space-1",
            file_name="scan.pdf", material_root=tmp_path,
        )

    assert list(tmp_path.glob("*.pdf")) == []


@pytest.mark.parametrize("file_name", ["../proof.pdf", "", ".", "a/b.pdf"])
def test_dingtalk_drive_file_rejects_unscoped_names(tmp_path: Path, file_name: str):
    class Dws:
        def download_drive_file(self, *_args, **_kwargs):
            pytest.fail("invalid file name reached DWS")

    with pytest.raises(AgentReadOnlyViolationError, match="drive_file_name_invalid"):
        agent_cli._download_dingtalk_drive_file(
            dws=Dws(), node_id="node-1", space_id="space-1",
            file_name=file_name, material_root=tmp_path,
        )


def test_read_text_file_rejects_non_utf8_material(tmp_path: Path):
    material = tmp_path / "material.bin"
    material.write_bytes(b"\xff\xfe")

    with pytest.raises(
        AgentReadOnlyViolationError,
        match="text_material_invalid_utf8",
    ):
        agent_cli.read_text_file(str(material))


def test_read_text_file_returns_image_bytes_for_agent_ocr_mcp(tmp_path: Path):
    material = tmp_path / "settlement.jpg"
    material.write_bytes(b"\xff\xd8\xffimage-bytes")

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "image"
    assert result["ocr_required"] is True
    assert result["ocr_mime_type"] == "image/jpeg"
    assert result["ocr_input_base64"] == base64.b64encode(material.read_bytes()).decode()
    assert result["sha256"]


def test_read_text_file_returns_scanned_pdf_bytes_and_exact_page_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    material = tmp_path / "settlement.pdf"
    material.write_bytes(b"%PDF-1.7 scan")

    class Page:
        def extract_text(self):
            return ""

    class Reader:
        is_encrypted = False
        pages = [Page(), Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda _stream: Reader())
    result = agent_cli.read_text_file(str(material))

    assert result["ocr_required"] is True
    assert result["ocr_page_numbers"] == [1, 2]
    assert result["ocr_mime_type"] == "application/pdf"
    assert result["ocr_input_base64"] == base64.b64encode(material.read_bytes()).decode()
    assert result["sha256"] == hashlib.sha256(material.read_bytes()).hexdigest()


def test_read_text_file_marks_large_scan_for_page_image_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    material = tmp_path / "settlement.pdf"
    material.write_bytes(b"%PDF-1.7 scan")

    class Page:
        def extract_text(self):
            return ""

    class Reader:
        is_encrypted = False
        pages = [Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda _stream: Reader())
    monkeypatch.setattr(agent_cli, "MAX_OCR_AGENT_INPUT_BYTES", 4)
    result = agent_cli.read_text_file(str(material))
    assert result["ocr_page_image_required"] is True
    assert result["ocr_page_numbers"] == [1]
    assert "ocr_input_base64" not in result


def test_read_text_file_extracts_bounded_pdf_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    material = tmp_path / "business-plan.pdf"
    material.write_bytes(b"%PDF-1.7\xff")

    class Page:
        def extract_text(self):
            return "Verified plan evidence"

    class Reader:
        is_encrypted = False
        pages = [Page()]

    monkeypatch.setattr(agent_cli, "PdfReader", lambda _stream: Reader())

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "pdf"
    assert result["page_count"] == 1
    assert result["pages"] == [{"index": 1, "text": "Verified plan evidence"}]
    assert result["content"] == "Verified plan evidence"
    assert result["sha256"]


def test_read_text_file_detects_xlsx_without_filename_extension(tmp_path: Path):
    material = tmp_path / "downloaded-material"
    with zipfile.ZipFile(material, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types/>")
        workbook.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" r:id="rId1"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        workbook.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Verified</t></is></c></row></sheetData></worksheet>',
        )

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "xlsx"
    assert result["sheets"][0]["rows"][0]["cells"] == {"A": "Verified"}


def test_read_text_file_detects_pptx_without_filename_extension(tmp_path: Path):
    material = tmp_path / "downloaded-deck"
    with zipfile.ZipFile(material, "w") as presentation:
        presentation.writestr("[Content_Types].xml", "<Types/>")
        presentation.writestr("ppt/presentation.xml", "<presentation/>")
        presentation.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:t>Verified deck</p:t></p:sld>',
        )

    result = agent_cli.read_text_file(str(material))

    assert result["format"] == "pptx"
    assert result["slides"] == [{"index": 1, "text": "Verified deck"}]


def test_read_skill_allows_markdown_referenced_by_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "dingtalk-misc"
    reference = skill / "references" / "oa.md"
    reference.parent.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# DingTalk misc", encoding="utf-8")
    reference.write_text("# OA reference", encoding="utf-8")
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    result = agent_cli.read_skill(str(reference))

    assert result["content"] == "# OA reference"
    assert result["sha256"]
    assert result["path"] == str(reference.resolve())
    assert result["name"] == "dingtalk-misc"


def test_read_skill_ignores_missing_configured_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Business review", encoding="utf-8")
    monkeypatch.setattr(
        "app.agent_skill_usage.AGENT_SKILL_ROOTS",
        (tmp_path / "missing-skills", root),
    )

    result = agent_cli.read_skill(str(skill_path))

    assert result["path"] == str(skill_path.resolve())
    assert result["name"] == "business-review"


def test_read_skill_tool_discovers_metadata_and_reads_exact_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: business-review\n"
        "description: Review business material: with evidence.\n"
        "metadata:\n  requires:\n    bins:\n      - tool\n"
        "---\n\nEXACT BODY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool("read_skill").fn
    descriptor = next(
        item
        for item in asyncio.run(agent_cli.build_role_server("audit").list_tools())
        if item.name == "read_skill"
    )

    discovered = tool()
    from tests.test_skill_discovery_transport import decode_catalog

    assert decode_catalog(discovered) == [{
        "name": "business-review",
        "description": "Review business material: with evidence.",
        "path": str(skill_path.resolve()),
    }]
    assert "EXACT BODY" not in json.dumps(discovered)
    assert set(descriptor.inputSchema["properties"]) == {"path", "name"}
    assert "required" not in descriptor.inputSchema
    by_name = tool(name="business-review")
    by_path = tool(path=str(skill_path))
    assert by_name == by_path
    assert tool(name="business-review", path=None) == by_name
    assert tool(path=str(skill_path), name=None) == by_path
    assert by_name["content"].endswith("EXACT BODY\n")
    with pytest.raises(ValueError, match="only one"):
        tool(path=str(skill_path), name="business-review")
    with pytest.raises(ValueError, match="unknown"):
        tool(name="missing")


def test_read_skill_tool_rejects_ambiguous_installed_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    roots = (tmp_path / "one", tmp_path / "two")
    for root in roots:
        path = root / "same" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: same\ndescription: Same.\n---\n\nBODY\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", roots)
    tool = agent_cli.build_role_server("consumer")._tool_manager.get_tool("read_skill").fn

    with pytest.raises(ValueError, match="ambiguous"):
        tool(name="same")


def test_read_skill_discovery_excludes_skill_symlink_outside_authorized_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\nname: secret-skill\ndescription: SECRET SENTINEL\n---\n",
        encoding="utf-8",
    )
    linked = root / "linked" / "SKILL.md"
    linked.parent.mkdir()
    linked.symlink_to(outside)
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    tool = agent_cli.build_role_server("audit")._tool_manager.get_tool("read_skill").fn

    discovered = tool()

    from tests.test_skill_discovery_transport import decode_catalog

    assert decode_catalog(discovered) == []
    assert "SECRET SENTINEL" not in json.dumps(discovered)
    with pytest.raises(ValueError, match="unknown"):
        tool(name="secret-skill")


@pytest.mark.parametrize("alias", ("tilde", "symlink"))
def test_read_skill_returns_canonical_identity_for_supported_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
):
    home = tmp_path / "home"
    root = home / ".agents" / "skills"
    skill_path = root / "business-review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Business review", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))
    if alias == "tilde":
        requested = "~/.agents/skills/business-review/SKILL.md"
    else:
        link = tmp_path / "skill-alias.md"
        link.symlink_to(skill_path)
        requested = str(link)

    result = agent_cli.read_skill(requested)

    assert result == {
        "content": "# Business review",
        "sha256": result["sha256"],
        "path": str(skill_path.resolve()),
        "name": "business-review",
    }


def test_read_skill_rejects_symlink_escape_from_authorized_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "skills"
    skill = root / "business-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Business review", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    escaped = skill / "escape.md"
    escaped.symlink_to(outside)
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(escaped))


@pytest.mark.parametrize("filename", ("notes.md", "references/oa.txt"))
def test_read_skill_rejects_files_outside_an_installed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
):
    root = tmp_path / "skills"
    target = root / filename
    target.parent.mkdir(parents=True)
    target.write_text("not an installed skill document", encoding="utf-8")
    monkeypatch.setattr("app.agent_skill_usage.AGENT_SKILL_ROOTS", (root,))

    with pytest.raises(AgentReadOnlyViolationError, match="skill_path_forbidden"):
        agent_cli.read_skill(str(target))


def test_execute_reviewed_read_allows_unclassified_cli(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/bin/python3")

    receipt = agent_cli.execute_reviewed_read(
        ["python3", "-c", "print('read')"],
        process_runner=lambda argv, **_: subprocess.CompletedProcess(
            argv, 0, "read\n", ""
        ),
    )
    assert receipt["stdout"] == "read\n"


def test_execute_reviewed_read_classifies_dws_from_exact_schema_without_prewarm(
    monkeypatch: pytest.MonkeyPatch,
):
    classifier = NativeCliMetadataClassifier()
    metadata_calls: list[list[str]] = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    monkeypatch.setattr(
        classifier,
        "prewarm",
        lambda: pytest.fail("DWS command validation must not preload every tool"),
    )

    def exact_schema(argv, *, timeout):
        metadata_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, '{"effect":"read"}', "")

    monkeypatch.setattr("app.native_cli_metadata.run_bounded_process", exact_schema)

    receipt = agent_cli.execute_reviewed_read(
        ["dws", "chat", "message", "get", "--message-id", "message-1"],
        classifier=classifier,
        process_runner=lambda argv, **_: subprocess.CompletedProcess(argv, 0, "{}", ""),
    )

    assert metadata_calls == []
    assert receipt["operation"] == "chat message get"


def test_execute_reviewed_write_preserves_provider_error_instead_of_read_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    classifier = NativeCliMetadataClassifier(
        reviewed_effects={
            ("dws", "chat message send"): EffectKind.EFFECTFUL,
        }
    )

    receipt = agent_cli.execute_reviewed_write(
        [
            "dws",
            "chat",
            "message",
            "send",
            "--user",
            "user-1",
            "--text",
            "hello",
            "--yes",
        ],
        process_runner=lambda argv, **_: subprocess.CompletedProcess(
            argv, 1, '{"code":"1001","message":"provider rejected request"}', ""
        ),
        classifier=classifier,
    )

    assert receipt["error"] == {
        "channel": "dws",
        "code": "1001",
        "retryable": False,
        "gate_state": "blocked",
        "detail": '{"code": "1001"}',
    }


def _write_classifier() -> NativeCliMetadataClassifier:
    return NativeCliMetadataClassifier(
        reviewed_effects={
            ("dws", "chat message send"): EffectKind.EFFECTFUL,
            ("dws", "calendar event create"): EffectKind.EFFECTFUL,
        }
    )


def test_explicit_reviewed_write_authorization_is_exact_and_does_not_touch_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    argv = [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--text",
        "hello",
        "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        argv,
        authorization_id="confirmation-1",
        action_index=0,
        classifier=_write_classifier(),
    )
    before = dict(os.environ)
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    consumed = False

    def consume(reviewed):
        nonlocal consumed
        if consumed:
            raise AgentReadOnlyViolationError("reviewed_write_authorization_consumed")
        assert reviewed == authorization
        consumed = True

    receipt = agent_cli.execute_reviewed_write(
        argv,
        authorization=authorization,
        authorization_id="confirmation-1",
        action_index=0,
        authorization_consumer=consume,
        classifier=_write_classifier(),
        process_runner=lambda command, **_: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 0, '{"ok":true}', "")
        ),
    )

    assert len(calls) == 1
    assert receipt["authorization_id"] == "confirmation-1"
    assert receipt["action_index"] == 0
    assert dict(os.environ) == before

    with pytest.raises(
        AgentReadOnlyViolationError, match="reviewed_write_authorization_consumed"
    ):
        agent_cli.execute_reviewed_write(
            argv,
            authorization=authorization,
            authorization_id="confirmation-1",
            action_index=0,
            authorization_consumer=consume,
            classifier=_write_classifier(),
            process_runner=lambda command, **_: subprocess.CompletedProcess(
                command, 0, "", ""
            ),
        )
    assert len(calls) == 1


def test_explicit_authorization_requires_a_consuming_boundary_before_runner(
    monkeypatch: pytest.MonkeyPatch,
):
    argv = [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--text",
        "hello",
        "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        argv, "confirmation-1", 0, classifier=_write_classifier()
    )
    calls = []
    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    with pytest.raises(
        AgentReadOnlyViolationError,
        match="reviewed_write_authorization_consumer_required",
    ):
        agent_cli.execute_reviewed_write(
            argv,
            authorization=authorization,
            authorization_id="confirmation-1",
            action_index=0,
            classifier=_write_classifier(),
            process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == []


@pytest.mark.parametrize("argv", ("dws", ["dws", "bad\0argument"]))
def test_review_write_authorization_rejects_unsafe_argv(argv):
    with pytest.raises(AgentReadOnlyViolationError, match="agent_cli_command_invalid"):
        agent_cli.review_write_authorization(
            argv,
            authorization_id="confirmation-1",
            action_index=0,
            classifier=_write_classifier(),
        )


@pytest.mark.parametrize(
    ("changed_argv", "authorization_id", "action_index"),
    [
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-2",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-1",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "changed",
                "--yes",
            ],
            "confirmation-1",
            0,
        ),
        (
            ["dws", "calendar", "event", "create", "--user", "user-1", "--yes"],
            "confirmation-1",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-2",
            0,
        ),
        (
            [
                "dws",
                "chat",
                "message",
                "send",
                "--user",
                "user-1",
                "--text",
                "hello",
                "--yes",
            ],
            "confirmation-1",
            1,
        ),
    ],
)
def test_explicit_reviewed_write_authorization_rejects_any_change_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    changed_argv: list[str],
    authorization_id: str,
    action_index: int,
):
    original = [
        "dws",
        "chat",
        "message",
        "send",
        "--user",
        "user-1",
        "--text",
        "hello",
        "--yes",
    ]
    authorization = agent_cli.review_write_authorization(
        original,
        authorization_id="confirmation-1",
        action_index=0,
        classifier=_write_classifier(),
    )
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("runner must not execute")

    monkeypatch.setattr(agent_cli.shutil, "which", lambda _: "/usr/local/bin/dws")
    with pytest.raises(
        AgentReadOnlyViolationError, match="reviewed_write_authorization_mismatch"
    ):
        agent_cli.execute_reviewed_write(
            changed_argv,
            authorization=authorization,
            authorization_id=authorization_id,
            action_index=action_index,
            classifier=_write_classifier(),
            process_runner=runner,
        )
    assert calls == 0


def test_oa_revert_task_passes_write_review():
    """Sending an approval back must be executable, not just decidable.

    DWS publishes no runtime schema for `oa approval revert-task`, so the
    metadata classifier cannot type it and the only thing standing between the
    agent and `agent_cli_command_unreviewed` is the registered-write fallback.
    Without the registration the skill's "prefer a real revert over parking a
    comment" rule could never run: every revert was refused before it reached
    DingTalk, and the turn fell back to a comment.
    """

    descriptor = agent_cli.describe_native_command(
        {
            "type": "command_execution",
            "argv": [
                "dws",
                "oa",
                "approval",
                "revert-task",
                "--instance-id",
                "inst-1",
                "--task-id",
                "task-1",
                "--target-activity-id",
                "sid-startevent",
                "--action",
                "REVERT_FOR_RESUBMIT",
                "--yes",
            ],
        }
    )

    assert descriptor is not None
    assert descriptor.command_path == "oa approval revert-task"
    assert agent_cli._is_registered_native_write(descriptor)
