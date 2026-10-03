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


def test_inspect_task_attention_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing ?#.sqlite3"
    result = _inspect(path, 7)
    assert result.returncode != 0
    assert "unable to open database file" in result.stderr
    assert not path.exists()
