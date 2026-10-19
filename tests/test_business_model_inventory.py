import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


TOOL = Path(__file__).resolve().parents[1] / "tools/business_model_inventory.py"


def module():
    spec = importlib.util.spec_from_file_location("business_model_inventory", TOOL)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app/store.py").write_text(
        'def save():\n    return "reply_tasks SECRET_SOURCE_VALUE"\n', encoding="utf-8"
    )
    router = root / "frontend/src/app/router.tsx"
    router.parent.mkdir(parents=True)
    router.write_text(
        '<Route path="/tasks" /><Route path="*" />\n'
        '<Route path="/tasks/item/:taskId" /><Route path="/tasks" />\n',
        encoding="utf-8",
    )
    path = tmp_path / "source.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        create table tasks(id integer primary key, record_type text, parent_id integer,
            generated text generated always as (record_type || '_generated') virtual);
        insert into tasks(id, record_type, parent_id) values
            (1, 'reply_tasks', null), (2, 'business_object_tasks', 1),
            (3, 'business_object_tasks', 1);
        create table runs(id integer primary key, record_type text, parent_id integer,
            structured_error_json text);
        insert into runs values (1, 'agent_runs', null, 'private parent error'),
            (2, 'agent_run_state_events', 1, 'private event error');
        create table business(id integer primary key, record_type text, import_task_id integer);
        insert into business values (1, 'work_todo', null);
        create table emails(id integer primary key, classification_id integer,
            class_sender text, message_sender text, class_subject text, message_subject text,
            class_received_at text, message_received_at text, class_folder text, message_folder text,
            class_rfc_message_id text, message_rfc_message_id text, body text);
        insert into emails values
            (1, 1, 'PRIVATE_SENDER', 'PRIVATE_SENDER', 'PRIVATE_SUBJECT', 'PRIVATE_SUBJECT',
             't', 't', 'in', 'in', null, 'PRIVATE_RFC_ONE', 'PRIVATE_BODY'),
            (2, 2, 'PRIVATE_DIFFERENT', 'PRIVATE_SENDER', 'b', 'b', 't', 't', 'in', 'in',
             'PRIVATE_RFC_TWO', 'PRIVATE_RFC_TWO', 'PRIVATE_BODY');
        """)
    return root, path


def test_inventory_reports_schema_types_and_divergences_without_values(corpus):
    root, path = corpus
    before = path.read_bytes()
    report = module().inventory(path, root)
    assert path.read_bytes() == before
    assert report["database_name"] == path.name
    assert report["types"]["tasks"] == {"reply_tasks": 1, "business_object_tasks": 2}
    assert report["checks"]["business_aliases"] == {
        "rows": 2, "parents": 1, "multi_key_parents": 1, "max_keys": 2
    }
    pairs = report["checks"]["email_pairs"]
    assert pairs["class_sender"] == {"rows": 2, "equal": 1, "different": 1}
    assert pairs["class_rfc_message_id"] == {"rows": 2, "equal": 1, "different": 1}
    for name in ("class_subject", "class_received_at", "class_folder"):
        assert pairs[name] == {"rows": 2, "equal": 2, "different": 0}
    assert report["checks"]["work_todo_import"] == {"rows": 1, "mapped": 0}
    assert report["checks"]["run_state_errors"] == {"rows": 1, "equal": 0}
    assert report["routes"] == ["*", "/tasks", "/tasks/item/:taskId"]
    generated = next(c for c in report["tables"]["tasks"] if c["name"] == "generated")
    assert generated == {"cid": 3, "name": "generated", "affinity": "TEXT", "not_null": 0,
                         "default": None, "pk": 0, "hidden": 2}
    encoded = json.dumps(report)
    assert "PRIVATE_" not in encoded
    assert "SECRET_SOURCE_VALUE" not in encoded


def test_references_are_navigation_locations_and_do_not_contain_source(corpus):
    root, path = corpus
    report = module().inventory(path, root)
    assert report["references"]["reply_tasks"] == [
        {"file": "app/store.py", "function": "save", "line": 2}
    ]


def test_missing_source_is_not_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        module().inventory(missing, tmp_path)
    assert not missing.exists()


def test_missing_optional_schema_is_explicit(tmp_path):
    path = tmp_path / "minimal.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table unrelated(id integer)")
    checks = module().inventory(path, tmp_path)["checks"]
    assert checks == {"email_pairs": None, "business_aliases": None,
                      "work_todo_import": None, "run_state_errors": None}


def test_cli_emits_only_json(corpus):
    root, path = corpus
    result = subprocess.run([sys.executable, str(TOOL), str(path), "--root", str(root)],
                            check=True, capture_output=True, text=True)
    assert result.stderr == ""
    assert json.loads(result.stdout)["checks"]["work_todo_import"]["rows"] == 1


def test_routes_read_literal_path_regardless_of_attribute_order(tmp_path):
    router = tmp_path / "frontend/src/app/router.tsx"
    router.parent.mkdir(parents=True)
    router.write_text("""
      <Route element={<Page />} path='/later' />
      <Route\n path="/first" element={<Page />} />
      <Route path={dynamic} />
    """, encoding="utf-8")
    assert module().routes(tmp_path) == ["/first", "/later"]


def test_partial_email_schema_and_null_equality(tmp_path):
    path = tmp_path / "partial.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        create table emails(classification_id integer, class_sender text, message_sender text);
        insert into emails values(1, null, null), (null, 'message-only', 'message-only');
        """)
    pairs = module().inventory(path, tmp_path)["checks"]["email_pairs"]
    assert pairs["class_sender"] == {"rows": 1, "equal": 1, "different": 0}
    assert pairs["class_subject"] is None
