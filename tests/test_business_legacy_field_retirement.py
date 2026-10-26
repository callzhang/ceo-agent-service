"""Nine approved legacy assessments retire; current business facts survive."""
from contextlib import closing
import io
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest

from app.store import AutoReplyStore
from app.task_models import WorkProject, WorkTodo, WorkUpdate
from tests.test_shared_action_release import _old_process

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEADS = {
    "2026-10-19.8": "c4a9e794d0fe7b8954a8746b01fec118337dc188",
    "2026-10-19.9": "45f3324be3c568b9f91709c25306e19d73a15087",
    "2026-10-19.10": "8ff29625154988e4ff32f6a076ffc7595a1af120",
}


RETIRED = {
    "work_project": ("priority", "risk_level", "needs_derek_attention", "next_follow_up_at", "follow_up_mode"),
    "work_todo": ("priority", "next_follow_up_at"),
    "work_updates": ("merge_reason", "confidence"),
}


@pytest.mark.parametrize("kind,model", [
    ("work_project", WorkProject), ("work_todo", WorkTodo), ("work_updates", WorkUpdate),
])
def test_history_dto_has_only_retained_business_fields(kind, model):
    assert not set(RETIRED[kind]).intersection(model.model_fields)
    assert len(model.model_fields) == {"work_project": 19, "work_todo": 16, "work_updates": 7}[kind]


def test_fresh_schema_has_no_retired_controls_or_operational_indexes(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    AutoReplyStore(path)
    from app.email_store import EmailStore
    from app.workbench.store import WorkbenchStore
    EmailStore(path)
    WorkbenchStore(path)
    with closing(sqlite3.connect(path)) as db:
        action_columns = {row[1] for row in db.execute("pragma table_xinfo(actions)")}
        assert not {"sync_merge_reason", "sync_confidence"}.intersection(action_columns)
        columns = {row[1] for row in db.execute("pragma table_xinfo(business)")}
        assert not {f"{kind}_{field}" for kind in ("work_project", "work_todo")
                    for field in RETIRED[kind]}.intersection(columns)
        names = {row[0] for row in db.execute("select name from sqlite_schema")}
        assert "idx_business_work_project_status_priority" not in names
        assert "idx_business_work_todo_follow_up" not in names
        assert "idx_business_work_todo_project_status" in names
        assert "business_legacy_links" not in names


def test_current_update_writer_layout_cannot_store_retired_assessments():
    from app.action_storage import _action_layout
    assert _action_layout("work_updates").fields == (
        "project_id", "source_type", "source_ref", "summary", "changes_json", "created_at",
    )


@pytest.fixture(scope="module")
def archived_sources(tmp_path_factory):
    roots = {}
    for version, commit in SOURCE_HEADS.items():
        root = tmp_path_factory.mktemp("legacy-fields-" + version)
        archive = subprocess.check_output(["git", "archive", commit, "app"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(root, filter="data")
        roots[version] = root
    return roots


def seed_old(root, path, variant):
    _old_process(root, r'''
from pathlib import Path
import sqlite3,sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.business_storage import insert_business
from app.action_storage import insert_sync_action,insert_action
from app.state_storage import update_state_rows
from app.run_storage import insert_run
s=AutoReplyStore(Path(sys.argv[1])); EmailStore(s.path)
s.enqueue_reply_task(conversation_id='fixture',conversation_title='Fixture',single_chat=True,trigger_message_id='fixture',trigger_create_time='',trigger_sender='Fixture',trigger_text='Fixture',trigger_message_json='{}')
with sqlite3.connect(s.path) as db:
 if sys.argv[2]=='actual_no_memory_fk':
  from app import action_storage as actions
  from app.current_database_schema import FINAL_ACTION_OPTIONS
  db.execute('drop table actions')
  actions.create_action_schema(db,reply_variant='actual_no_memory_fk',**FINAL_ACTION_OPTIONS)
 db.execute('begin')
 parent=db.execute("select id from tasks where record_type='reply_tasks'").fetchone()[0]
 insert_run(db,'agent_runs',dict(id=7,task_id=parent,role='consumer',execution_generation='fixture'))
 p=insert_business(db,'work_project',dict(id=17,title='Retained legacy project',priority='high',risk_level='high',needs_derek_attention=1,next_follow_up_at='2027-03-04',follow_up_mode='monitor',facts_json=' { "business_fact" : 7 } ',source_conversations_json='["original-conversation"]')).lastrowid
 t=insert_business(db,'work_todo',dict(id=17,project_id=p,title='Retained legacy todo',priority='high',next_follow_up_at='2027-04-05',deadline_at='2027-05-06',follow_up_question='Unique unsent question',description='Actual business detail',created_from_update_id=17)).lastrowid
 insert_sync_action(db,'work_updates',dict(id=17,project_id=p,source_type='local_file',source_ref='source:17',summary='Actual business history',changes_json=' { "provider_change" : [1] } ',merge_reason='RETIRED-UNIQUE-MERGE',confidence=0.8125))
 # These retained values exercise the actual SQLite codec and affinities.
 insert_business(db,'work_todo',dict(id=18,project_id=p,title=b'blob-title\x00\xff',description=b'blob-description',created_from_update_id=91.125))
 insert_sync_action(db,'work_updates',dict(id=18,project_id=p,source_type=b'blob-source',source_ref='source:18',summary=b'blob-summary\x00',changes_json=b'blob-changes',merge_reason='RETIRED-OTHER-MERGE',confidence=0.5))
 insert_business(db,'task',dict(id=17,title='Independent semantic task',stage='candidate',formal_basis=None,owner_name='Current owner'))
 insert_action(db,'external_action_results',dict(external_action_key='receipt:17',business_object_key='object:17',action_identity='seed',operation='send',target_identifiers_json='{}',result_digest='seed-digest',first_agent_run_id=7,provider_result_json=' { "provider_receipt" : "immutable" } '))
 update_state_rows(db,'service_state',{'updated_at':b'original-marker-clock'},where_sql='key=?',parameters=('store_schema_version',))
 db.execute("insert into sqlite_sequence(rowid,name,seq) values(901,'unrelated-allocator',777)")
''', path, variant)












@pytest.mark.parametrize("surface", ["api", "html", "import"])
def test_current_history_consumers_read_retained_fields_without_removed_controls(tmp_path, surface):
    from app.business_storage import insert_business
    from app.action_storage import insert_sync_action
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    with store._connect() as db:
        db.execute("begin")
        insert_business(db, "work_project", {"id": 1, "title": "Kept project", "facts_json": '[{"description":"Kept fact"}]'})
        insert_business(db, "work_todo", {"id": 1, "project_id": 1, "title": "Kept todo", "follow_up_question": "Unique unsent question"})
        insert_sync_action(db, "work_updates", {"id": 1, "project_id": 1, "source_type": "local_file", "source_ref": "source:1", "summary": "Kept update", "changes_json": '{"actual_change":1}'})
    if surface == "api":
        from app.web_api.tasks import ConsoleTaskDetail, task_detail
        item = ConsoleTaskDetail.model_validate(task_detail(store, 1)).model_dump()
        assert not set(RETIRED["work_project"]).intersection(item["project"])
        assert not set(RETIRED["work_todo"]).intersection(item["todos"][0])
        assert not set(RETIRED["work_updates"]).intersection(item["updates"][0])
        assert item["todos"][0]["follow_up_question"] == "Unique unsent question"
        assert item["updates"][0]["changes"] == {"actual_change": 1}
    elif surface == "html":
        from app.audit_web import _task_project_detail_rows, _task_todo_detail_item, _task_update_rows
        project, todo = store.get_work_project(1), next((todo for todo in store.list_work_todos() if todo.id == 1), None)
        assert all(row[0] != "Follow-up mode" for row in _task_project_detail_rows(project))
        assert "Unique unsent question" in _task_todo_detail_item(todo, [], {}, [])
        assert len(_task_update_rows(store.list_work_updates(1))[0]) == 4
    else:
        from app.task_semantic_import import build_task_semantic_import_manifest
        manifest = build_task_semantic_import_manifest(store.path)
        assert manifest.version == 4
        assert len(manifest.items) == 3
        assert {item.disposition for item in manifest.items} == {"history_only"}


def test_frozen_historical_factories_still_match_actual_19_10(archived_sources):
    from app.business_legacy_owner_storage import _business_declaration
    from app.logical_run_definitions import logical_action_objects
    actual = _old_process(archived_sources["2026-10-19.10"], """
import json
from app.business_legacy_owner_storage import _business_declaration
from app.logical_run_definitions import logical_action_objects
print(json.dumps([_business_declaration(target=True),[logical_action_objects(v) for v in ('fresh_memory_fk','actual_no_memory_fk')]]))
""")
    assert actual == [_business_declaration(target=True), [logical_action_objects(v) for v in ("fresh_memory_fk", "actual_no_memory_fk")]]


def test_current_writers_and_payload_constraints_reject_retired_fields(tmp_path):
    from app.business_storage import insert_business, update_business
    from app.action_storage import insert_sync_action, update_sync_action
    store = AutoReplyStore(tmp_path / "strict.sqlite3")
    with store._connect() as db:
        db.execute("begin")
        insert_business(db, "work_project", {"id": 1, "title": "Fact"})
        insert_business(db, "work_todo", {"id": 1, "project_id": 1, "title": "Fact"})
        insert_sync_action(db, "work_updates", {"id": 1, "project_id": 1, "source_type": "message", "source_ref": "m1", "summary": "Fact"})
        for kind, fields in RETIRED.items():
            writer = update_sync_action if kind == "work_updates" else update_business
            for field in fields:
                with pytest.raises(ValueError):
                    writer(db, kind, 1, {field: "retired"})
            if kind != "work_updates":
                payload = db.execute("select payload_json from business where record_type=?", (kind,)).fetchone()[0]
                assert not set(fields).intersection(json.loads(payload))
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute("update business set payload_json=json_set(payload_json,?,?) where record_type=?", ("$."+fields[0], "retired", kind))
        assert update_sync_action(db, "work_updates", 1, {"summary": "Kept changed fact"}, where_sql="source_ref=?", parameters=("m1",)).rowcount == 1
        assert db.execute("select sync_summary from actions where record_type='work_updates'").fetchone()[0] == "Kept changed fact"
        assert db.execute("select json_array_length(payload_json) from actions where record_type='work_updates'").fetchone()[0] == 6
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update actions set payload_json=jsonb_insert(payload_json,'$[#]','retired') where record_type='work_updates'")


def test_current_constructors_require_explicit_release_for_actual_old_source(archived_sources, tmp_path):
    from app.email_store import EmailStore
    from app.workbench.store import WorkbenchStore
    path = tmp_path / "old.sqlite3"
    seed_old(archived_sources["2026-10-19.10"], path, "fresh_memory_fk")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for constructor in (AutoReplyStore, EmailStore, WorkbenchStore):
        with pytest.raises(
            sqlite3.IntegrityError,
            match="unsupported or malformed Store source schema; explicit paired schema release required",
        ):
            constructor(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
