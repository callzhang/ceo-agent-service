"""Actual c4a9e794 Store19.8 DAO, inverse, strict old cold start and reforward."""
import hashlib
import inspect
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from tests.test_shared_action_release import _old_process
from tests import prepared_action_caller_fixture as calls

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = "c4a9e794d0fe7b8954a8746b01fec118337dc188"


@pytest.fixture(scope="module")
def archived_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("actual-store19-8")
    archive = subprocess.check_output(["git", "archive", SOURCE_HEAD, "app", "tests", "scripts"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    return root


def seed(root, path):
    _old_process(root, """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.action_storage import insert_action, insert_error_action
from app.state_storage import update_state_rows
s=AutoReplyStore(Path(sys.argv[1])); EmailStore(s.path)
with s._immediate_write_transaction() as db:
 update_state_rows(db,'service_state',{'updated_at':b'original-marker-clock'},
  where_sql='key=?',parameters=('store_schema_version',))
 for key in ('before-both','before-claim','before-receipt','before-empty'):
  insert_action(db,'outbound_postfixes',dict(channel='wechat',delivery_key=key,
   final_body='source immutable body\\0'+key,feedback_token='source-token',postfix_version='source-version',created_at='prepare-clock'))
  if key in ('before-both','before-claim'):
   insert_action(db,'native_reply_dispatches',dict(channel='wechat',delivery_key=key,created_at='claim-clock'))
  if key in ('before-both','before-receipt'):
   insert_action(db,'outbound_postfix_receipts',dict(channel='wechat',delivery_key=key,
    provider_result_json=' { \"raw\" : 1 } ',created_at='receipt-clock'))
 insert_error_action(db,{'conversation_id':b'source\\0bytes','kind':'fixture','detail':'source detail','created_at':'error-clock'})
 db.execute("insert into sqlite_sequence(rowid,name,seq) values(911,'actions:agent_effect_intents',812)")
""", path)


def execute(root, path):
    return _old_process(root, inspect.getsource(calls)
        + "\nimport json,sys\nprint(json.dumps(exercise(sys.argv[1]),ensure_ascii=False))", path)


def cold(root, path):
    _old_process(root, """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.workbench.store import WorkbenchStore
p=Path(sys.argv[1]);AutoReplyStore(p);EmailStore(p);WorkbenchStore(p)
""", path)


def test_historical_factory_outputs_and_c1_files_frozen(archived_source):
    code = """
import hashlib,json
from app import action_storage as a
from app.email_canonical_storage import ACTION_OPTIONS
from app.task_lifecycle_current_schema import task_lifecycle_objects
from app.task_storage import TASK_INDEX_DDL, TASK_TABLE_DDL, TASK_TRIGGER_DDL
from app.task20_current_schema import _current_objects
def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
result={'C1_tasks':digest(task_lifecycle_objects()),'source_tasks':digest(_current_objects()),
 'task_storage_ddl':digest((TASK_TABLE_DDL,TASK_INDEX_DDL,TASK_TRIGGER_DDL))}
for variant in a.REPLY_VARIANTS:
 options=dict(reply_variant=variant,inline_legacy_owner=True,physical_provenance=True,
  compact_payload=True,canonical_email=True,**ACTION_OPTIONS)
 for stage,field in enumerate((None,'selection_references','result_references','execution_owner','sent_result_references','shared_lifecycle','specialized_claims')):
  if field:options[field]=True
  result[variant+str(stage)]=digest(a._current_objects(**options))
print(json.dumps(result))
"""
    assert _old_process(archived_source, code) == _old_process(ROOT, code)
    manifest = json.loads((ROOT / "docs/superpowers/specs/2026-10-19-task-lifecycle-frozen-sources.json").read_text())
    paths = {entry["path"] for entry in manifest["files"]} - {
        "app/action_storage.py", "app/task_storage.py",
    }
    # The archived State source remains pinned even though the current State
    # definitions evolved. Generated historical Task DDL is compared above;
    # remaining unchanged sources are compared to the archive below.
    state_entry = next(entry for entry in manifest["files"] if entry["path"] == "app/state_definitions.py")
    assert hashlib.sha256((archived_source / state_entry["path"]).read_bytes()).hexdigest() == state_entry["sha256"]
    additive_helpers = {
        "app/state_definitions.py",
        "app/email_canonical_definitions.py",
        "app/shared_action_definitions.py",
    }
    for name in sorted(paths):
        if name in additive_helpers or not (ROOT / name).is_file():
            continue
        assert hashlib.sha256((ROOT / name).read_bytes()).digest() == hashlib.sha256((archived_source / name).read_bytes()).digest()
