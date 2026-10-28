"""Exercise this paired release on one disposable copy, including real old code."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from app.model_retirement_schema_release import prepare_model_retirement_release
from app.task8_storage import register_task8_functions


_FIXTURE_PROGRAM = r'''
import json, socket, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
def refused(*args, **kwargs):
    raise AssertionError("copy acceptance attempted external network or a subprocess")
socket.socket.connect = refused
socket.socket.connect_ex = refused
# croniter probes the local Python executable through platform.architecture.
# Complete this dependency import before refusing application CLI launches.
import croniter
subprocess.Popen.__init__ = refused
from app.store import AutoReplyStore
from app.task8_storage import register_task8_functions
from app.task_entity_storage import create_task, get_task, associate_business_key
from app.run_entity_storage import create_run, get_run
from app.configs_storage import insert_config
from app.action_entity_storage import create_action, get_action, append_action_receipt
path = Path(sys.argv[1])
expected = json.loads(sys.argv[2])
store = AutoReplyStore(path)
db = sqlite3.connect(path)
db.row_factory = sqlite3.Row
register_task8_functions(db)
db.execute("pragma foreign_keys=on")
for prior in expected:
    assert get_task(db, prior["task_id"])["id"] == prior["task_id"]
    assert get_run(db, prior["run_id"])["task_id"] == prior["task_id"]
    assert get_action(db, prior["action_id"])["external_action_key"] == prior["key"]
    assert db.execute("select action_id from action_receipts where id=?", (prior["receipt_id"],)).fetchone()[0] == prior["action_id"]
db.execute("begin")
clock = datetime.now(timezone.utc).isoformat()
key = "schema-copy:" + str(uuid4())
task = create_task(db, values={"kind":"reply", "status":"pending", "created_at":clock, "updated_at":clock}, parameters={})
run = create_run(db, values={"task_id":task["id"], "status":"completed", "operation_id":key, "created_at":clock})
associate_business_key(db, task_id=task["id"], business_key=key, created_at=clock, updated_at=clock)
insert_config(db, "email_model_promotion_configs", {"config_version":key,"micro_f1_min":0.8,"category_precision_min":0.7,"category_validation_samples_min":5,"p95_latency_max_ms":200.0,"created_at":clock})
action = create_action(db, values={"operation":"send", "parameters_json":"{}", "status":"done", "external_action_key":key,"agent_run_id":run["id"],"created_at":clock,"updated_at":clock})
receipt = append_action_receipt(db, action["id"], values={"operation":"send", "target":"copy-provider-double", "provider_result_json":'{"provider":"test_double","ok":true}', "agent_run_id":run["id"], "created_at":clock})
db.execute("update sqlite_sequence set seq=seq+7 where name='configs'")
db.commit()
assert not list(db.execute("pragma foreign_key_check"))
db.close()
print(json.dumps({"task_id":task["id"],"run_id":run["id"],"action_id":action["id"],"receipt_id":receipt,"key":key}))
'''


def _scalar(value):
    if value is None:
        return ("null", None)
    if isinstance(value, bytes):
        return ("blob", value.hex())
    if isinstance(value, float):
        return ("real", value.hex())
    if isinstance(value, int):
        return ("integer", str(value))
    return ("text", value)


def fact_digest(path, *, original_ceilings=None):
    """Hash native stored values/types and hidden identities; never export bodies."""
    retired = {
        "configs": {"transition_promotion_config_version", "transition_promotion_ref"},
        "run_entity_identity_map": {"allocator_highwater"},
    }
    result = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("pragma query_only=on")
        db.execute("begin")
        table_rows = db.execute("pragma main.table_list").fetchall()
        for schema, table, kind, _, without_rowid, _ in table_rows:
            if schema != "main" or kind != "table" or (table.startswith("sqlite_") and table != "sqlite_sequence"):
                continue
            if original_ceilings is not None and table == "sqlite_sequence":
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = db.execute(f"pragma main.table_xinfo({quoted})").fetchall()
            retained = [row[1] for row in columns if row[6] == 0 and row[1] not in retired.get(table, set())]
            names = ",".join('"' + name.replace('"', '""') + '"' for name in retained)
            projection = names if without_rowid else "rowid," + names
            order = ",".join('"' + row[1] + '"' for row in sorted(columns, key=lambda row: row[5]) if row[5]) if without_rowid else "rowid"
            clauses = ["not(record_type='service_state' and key='store_schema_version')"] if table == "state" else []
            parameters = ()
            if original_ceilings is not None and not without_rowid:
                clauses.append("rowid<=?")
                parameters = (original_ceilings[table],)
            where = " where " + " and ".join(clauses) if clauses else ""
            digest = hashlib.sha256(json.dumps(retained).encode())
            count = 0
            for row in db.execute(f"select {projection} from {quoted}{where} order by {order}", parameters):
                digest.update(json.dumps([_scalar(value) for value in row], ensure_ascii=False,
                                         separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def _ceilings(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        tables = db.execute("pragma main.table_list").fetchall()
        return {row[1]: db.execute('select max(rowid) from "' + row[1] + '"').fetchone()[0]
                for row in tables if row[0] == "main" and row[2] == "table" and not row[4]
                and not row[1].startswith("sqlite_")}


def _allocation(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return {
            "page_size": db.execute("pragma page_size").fetchone()[0],
            "page_count": db.execute("pragma page_count").fetchone()[0],
            "freelist_pages": db.execute("pragma freelist_count").fetchone()[0],
            "allocated_bytes": db.execute("select sum(pgsize) from dbstat").fetchone()[0],
        }


def _copy_source(source, target):
    if source == target or target.exists():
        raise ValueError("validation requires a new disposable copy path")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        src.execute("pragma query_only=on")
        src.execute("begin")
        src.execute("select count(*) from sqlite_schema").fetchone()
        deadline = time.monotonic() + 120

        def bounded_copy(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("validation copy exceeded its deadline")

        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, progress=bounded_copy, sleep=0.01)


def _seed(root, path, expected):
    allowed = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONPATH=str(root), CEO_WORKER_DB=str(path), CEO_SERVICE_ROOT=str(root),
               PYTHONDONTWRITEBYTECODE="1", CEO_DATABASE_BACKUP_PAUSED="1")
    run = subprocess.run([sys.executable, "-c", _FIXTURE_PROGRAM, str(path), json.dumps(expected)],
                         cwd=root, env=env, check=False, capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise RuntimeError(f"cold copy fixture failed at {root}:\n{run.stderr[-4000:]}")
    return json.loads(run.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--copy", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source, copy = args.source.resolve(), args.copy.resolve()
    old = args.old_root.resolve()
    current = Path(__file__).resolve().parents[1]
    _copy_source(source, copy)
    source_allocation = _allocation(copy)
    before = fact_digest(copy)
    ceilings = _ceilings(copy)
    original = fact_digest(copy, original_ceilings=ceilings)
    release = prepare_model_retirement_release(copy)
    release.publish()
    release.verify_loaded()
    assert fact_digest(copy) == before, "forward changed retained native facts"
    target_allocation = _allocation(copy)
    first = _seed(current, copy, [])
    present = fact_digest(copy)
    release.rollback()
    assert fact_digest(copy) == present, "inverse lost post-migration native facts"
    second = _seed(old, copy, [first])
    after_old_write = fact_digest(copy)
    again = prepare_model_retirement_release(copy)
    again.publish()
    again.finalize()
    assert fact_digest(copy) == after_old_write, "reforward lost old-code writes"
    third = _seed(current, copy, [first, second])
    assert fact_digest(copy, original_ceilings=ceilings) == original, "cold open or fixture writes changed original retained facts"
    report = {
        "status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source), "copy": str(copy), "old_root": str(old),
        "native_tables_including_sequence": len(before),
        "source_facts": before, "new_fixture_identities": [first, second, third],
        "forward_preserved": True, "inverse_preserved_new_facts": True,
        "real_old_code_cold_read_write": True, "reforward_preserved": True,
        "original_rows_unchanged_after_all_cold_writes": True,
        "same_cohort_allocation_before_new_fixture_rows": {
            "source": source_allocation, "target": target_allocation,
            "vacuum_performed": False,
        },
        "external_network_and_subprocess_from_fixture": "refused",
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("source_facts", "new_fixture_identities")}))


if __name__ == "__main__":
    main()
