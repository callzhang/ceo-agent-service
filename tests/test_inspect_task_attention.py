import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_task_attention.py"


def _inspect(db_path, input_id):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), "--input-id", str(input_id)],
        capture_output=True, text=True, check=False,
    )


def _database(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            "create table work_summary_inputs (id integer, source_type text, status text, payload_json text);"
            "create table task_agent_runs (id integer, summary_input_id integer, status text, projection_json text, audit_summary text, decision_json text);"
            "insert into work_summary_inputs values (7, 'management_weekly_report', 'completed', 'private payload');"
            "insert into task_agent_runs values (10, 7, 'completed', '{}', 'historical private audit', '{}');"
            "insert into task_agent_runs values (11, 7, 'completed', '{\"status\":\"pending\",\"proposal_count\":2,\"applied_count\":0,\"project_assessments\":[]}', 'current private audit', '{\"project_assessments\":[],\"private\":\"decision\"}');"
            "insert into task_agent_runs values (12, 8, 'failed', '{}', 'other input', '{}');"
        )


def test_inspect_task_attention_reads_exact_input_receipts_without_payload(tmp_path):
    path = tmp_path / "attention ?#% 数据.sqlite3"
    _database(path)
    before = path.read_bytes()
    result = _inspect(path, 7)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["input_id"] == 7
    assert output["source_type"] == "management_weekly_report"
    assert output["input_status"] == "completed"
    assert [run["run_id"] for run in output["runs"]] == [10, 11]
    assert "project_assessments" not in output["runs"][0]
    assert output["runs"][0]["projection"] == {}
    assert output["runs"][1]["project_assessments"] == []
    assert output["runs"][1]["projection"]["proposal_count"] == 2
    assert output["runs"][1]["projection"]["status"] == "pending"
    assert output["runs"][1]["run_status"] == "completed"
    assert "private" not in result.stdout
    assert path.read_bytes() == before


def test_inspect_task_attention_distinguishes_present_empty_assessments_from_old_run(tmp_path):
    path = tmp_path / "attention.sqlite3"
    _database(path)

    result = _inspect(path, 7)

    runs = json.loads(result.stdout)["runs"]
    assert "project_assessments" not in runs[0]
    assert runs[1]["project_assessments"] == []


def test_inspect_task_attention_missing_input_is_json_error(tmp_path):
    path = tmp_path / "attention.sqlite3"
    _database(path)
    result = _inspect(path, 999)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "input_not_found", "input_id": 999}


def test_inspector_reads_actual_zero_task_project_revision_without_rewriting_old_runs(tmp_path):
    from app.store import AutoReplyStore
    from app.project_context_service import ProjectContextService
    from app.task_business_resolution import BusinessResolutionService
    from app.task_semantic_models import ProjectContext
    from app.task_models import TaskAttentionProjectionReceipt

    path = tmp_path / "current-project.sqlite3"
    store = AutoReplyStore(path)
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(anchor_type="project", anchor_ref="project:inspect", title="交付项目")
    project_id = resolver.register_official_project(anchor_id=anchor_id, registry_source="meeting:inspect")
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="m:inspect", evidence_text="李四总负责交付项目。", dedupe_key="inspect:source"
    )
    context = ProjectContext.model_validate({
        "goal": "完成交付", "scope": "一期", "overall_owner": {
            "person_name": "李四", "responsibility": "交付",
            "evidence": [{"signal_id": signal_id, "source_ref": "m:inspect", "source_excerpt": "李四总负责交付项目"}],
        }, "responsibilities": [], "facts": [],
    })
    with store.business_task_transaction() as db:
        revision_id = ProjectContextService(store).apply(project_id=project_id, context=context, signal_ids=(signal_id,), db=db)
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:inspect", '{"private": "input body"}')
    old_id = store.record_task_agent_run(input_id, decision_json='{"private":"old"}')
    decision = {"project_decisions": [], "task_decisions": [], "project_assessments": []}
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps(decision))
    projection = {"project_decisions": [{"project_decision_index": 0, "project_id": project_id, "anchor_id": anchor_id, "revision_id": revision_id, "signal_ids": [signal_id]}], "task_decisions": [], "project_assessments": []}
    projection = TaskAttentionProjectionReceipt(
        status="no_proposal", source_type="reply_attempt", task_decision_count=0,
        project_link_count=0, proposal_count=0, **projection,
    ).model_dump(mode="json")
    store.record_task_agent_projection(run_id, json.dumps(projection))
    with sqlite3.connect(path) as db:
        original = db.execute("select id, decision_json, projection_json from task_agent_runs order by id").fetchall()
    inspected = _inspect(path, input_id)
    assert inspected.returncode == 0, inspected.stderr
    runs = json.loads(inspected.stdout)["runs"]
    assert runs[0]["run_id"] == old_id
    assert "project_decisions" not in runs[0]
    assert "project_contexts" not in runs[0]
    assert runs[1]["project_decisions"] == []
    assert runs[1]["projection"] == projection
    [current] = runs[1]["project_contexts"]
    assert current["project_id"] == project_id
    assert current["anchor_id"] == anchor_id
    assert current["revision_id"] == revision_id
    assert current["context"]["overall_owner"]["person_name"] == "李四"
    assert "private" not in inspected.stdout
    assert store.list_business_tasks() == ()
    with sqlite3.connect(path) as db:
        assert db.execute("select id, decision_json, projection_json from task_agent_runs order by id").fetchall() == original


def test_inspect_task_attention_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing ?#.sqlite3"
    result = _inspect(path, 7)
    assert result.returncode != 0
    assert "unable to open database file" in result.stderr
    assert not path.exists()


def test_inspector_reads_exact_replay_delivery_artifact_without_reconstructing_history(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    _database(path)
    artifact = tmp_path / "result.json"
    artifact.write_text(json.dumps({"input_id": 7, "run_id": 11, "context_deliveries": [{
        "source_metrics": {"signal_count": 3, "document_count": 1, "unique_body_chars": 9000, "visible_body_chars": 2048},
        "source_documents": [{"document_id": 8, "full_length": 9000, "visible_ranges": [{"start": 0, "end": 2048}]}],
    }]}))
    before = path.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), "--db", str(path), "--input-id", "7", "--replay-result", str(artifact)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    [old, current] = json.loads(result.stdout)["runs"]
    assert "context_deliveries" not in old
    assert current["context_deliveries"][0]["source_metrics"]["document_count"] == 1
    assert path.read_bytes() == before
