"""Confined Tasks20 coupled forward/inverse and genuine predecessor proof.

Only the SQLite backup API reads the source. Every write and cold constructor
runs inside one owned TemporaryDirectory, with no worker or provider invocation.
The optional receipt contains counts and digests only, never business rows.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import time

from app.action_email_definitions import EMAIL_COMPOSITE_KEYS
from app.provider_state_schema_release import SOURCE_TABLES as PROVIDER_SOURCE_TABLES

WITNESS_EXCLUSIONS = {
    "wechat_memory_import_jobs": ("import_run_id not in (?,?,?)",
                                  ("tasks20-copy-native", "tasks20-copy-current", "tasks20-copy-predecessor")),
    "service_state": ("key is not ? and key is not ?",
                      ("state_copy_current", "state_copy_predecessor")),
    "email_scan_cursors": ("not(account_id is ? and folder is ?)",
                           ("copy-only-state-account", "copy-only-state-folder")),
}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def run(command, *, root, env=None):
    return subprocess.run(command, cwd=root, env=env, check=True,
                          capture_output=True, text=True, timeout=300).stdout.strip()


def wechat_facts(path, *, current):
    from app.task20_definitions import TASK20_FIELDS, task20_projection
    fields = TASK20_FIELDS["wechat_memory_import_jobs"]
    columns = ",".join(map(quote,fields))
    types = ",".join("typeof("+quote(name)+")" for name in fields)
    source = "("+task20_projection("wechat_memory_import_jobs")+")" if current else "main.wechat_memory_import_jobs"
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
        return tuple(db.execute("select "+columns+","+types+" from "+source+" order by id"))


def inventory(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        assert db.execute("pragma integrity_check").fetchall() == [("ok",)]
        tables = sorted(row[1] for row in db.execute("pragma main.table_list")
                        if row[2] == "table" and not row[1].startswith("sqlite_"))
        declarations = tuple(db.execute("select type,name,tbl_name,sql from main.sqlite_schema "
                                        "where name not like 'sqlite_%' order by type,name"))
        facts = {}
        original_witness_facts = {}
        for name in tables:
            fields = [row[1] for row in db.execute(f"pragma main.table_xinfo({quote(name)})")
                      if row[6] == 0]
            columns = ','.join(map(quote, fields))
            types = ','.join(f"typeof({quote(field)})" for field in fields)
            without_rowid = next(row[4] for row in db.execute("pragma main.table_list")
                                 if row[1] == name)
            # These accepted source families expose composite business keys,
            # not the engine's incidental hidden ROWID. All other rowid tables
            # (including all State families and public Email ROWIDs) stay exact.
            composite = name in EMAIL_COMPOSITE_KEYS or name in PROVIDER_SOURCE_TABLES
            identity = "" if without_rowid or composite else "rowid,"
            primary_key = [row[1] for row in sorted(
                db.execute(f"pragma main.table_xinfo({quote(name)})"), key=lambda row: row[5])
                if row[5]]
            if composite:
                assert primary_key, name
            order = ','.join(map(quote, primary_key)) if composite else columns if without_rowid else "rowid"
            selections = [(facts, "", ())]
            if name in WITNESS_EXCLUSIONS:
                predicate, arguments = WITNESS_EXCLUSIONS[name]
                selections.append((original_witness_facts, " where " + predicate, arguments))
            for destination, predicate, arguments in selections:
                digest = hashlib.sha256()
                count = 0
                for row in db.execute(f"select {identity}{columns},{types} "
                                      f"from main.{quote(name)}{predicate} order by {order}", arguments):
                    digest.update(pickle.dumps(row, protocol=4))
                    count += 1
                destination[name] = [count, digest.hexdigest()]
        sequence = tuple(db.execute("select name,seq from sqlite_sequence order by name"))
        return {"ordinary_tables": tables, "facts": facts, "sequence": sequence,
                "original_witness_facts": original_witness_facts,
                "declarations": declarations,
                "foreign_keys": tuple(db.execute("pragma foreign_key_check")),
                "page_count": db.execute("pragma page_count").fetchone()[0],
                "freelist_count": db.execute("pragma freelist_count").fetchone()[0]}


COLD = '''
import json,sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
p=Path(sys.argv[1])
s=AutoReplyStore(p)
e=EmailStore(p)
assert s.get_service_state("state_copy_current") == "current"
assert e.get_scan_cursor("copy-only-state-account", "copy-only-state-folder")["last_seen_uid"] == 7
s.set_service_state("state_copy_predecessor", "predecessor")
e.record_scan_cursor(account_id="copy-only-state-account", folder="copy-only-state-folder",
                     uidvalidity=1,last_seen_uid=11,expected_uidvalidity=1)
assert s.get_service_state("state_copy_predecessor") == "predecessor"
assert e.get_scan_cursor("copy-only-state-account", "copy-only-state-folder")["last_seen_uid"] == 11
with s._connect() as db:
    facts=tuple(tuple(r) for r in db.execute("select id,import_run_id,status,error from wechat_memory_import_jobs order by id"))
assert facts==((1,"tasks20-copy-native","completed","copy-only-native"),(2,"tasks20-copy-current","completed","copy-only-current"))
j=s.begin_wechat_memory_import_job(import_run_id="tasks20-copy-predecessor",account_id="copy-only")
assert j==3
s.finish_wechat_memory_import_job(j,status="completed",error="copy-only-predecessor")
print(json.dumps({"public_store": True,"public_email_cursor": True,"public_wechat_import_job":True}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    production = args.production_root.resolve()
    source = args.source.resolve()
    predecessor = run(["git", "rev-parse", "HEAD"], root=production)
    candidate = os.environ["TASK20_PROPOSAL_BASE"]
    start = time.monotonic()
    with TemporaryDirectory(prefix="ceo-state-current-copy-") as owned:
        temporary = Path(owned)
        copy = temporary / "copy.sqlite3"
        old_root = temporary / "predecessor"
        old_root.mkdir()
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                       "CEO_WORKER_DB": str(copy), "CEO_ENV_FILE": str(temporary / "absent.env"),
                       "CEO_SERVICE_ROOT": str(temporary / "absent-service"), "PYTHONPATH": str(root)}
        # These settings are inherited by the real paired child constructors.
        os.environ.update({key: environment[key] for key in
                           ("CEO_WORKER_DB", "CEO_ENV_FILE", "CEO_SERVICE_ROOT")})
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(copy)) as destination:
                original.backup(destination)
        before = inventory(copy)
        assert all(before["facts"][name] == value for name, value in
                   before["original_witness_facts"].items()), "proof witness already exists in source"
        with copy.open("rb") as frozen:
            frozen_sha = hashlib.file_digest(frozen, "sha256").hexdigest()
        print(json.dumps({"phase": "copied", "tables": len(before["ordinary_tables"]),
                          "predecessor": predecessor}), flush=True)
        archive = subprocess.run(["git", "archive", predecessor, "app", "data/config",
                                  "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json"],
                                 cwd=production, check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(old_root, filter="data")
        old_env = {**environment, "PYTHONPATH": str(old_root)}
        from app.store_schema_release import prepare_database_schema_release
        from app.state_schema_release import state_schema_is_current
        from app.store import AutoReplyStore
        from app.email_store import EmailStore
        release = prepare_database_schema_release(root, copy)
        assert release is not None
        assert not release.source_schema["tasks"]["sequence_present"]["wechat_memory_import_jobs"]
        native_write = 'from pathlib import Path\nimport sys\nfrom app.store import AutoReplyStore\ns=AutoReplyStore(Path(sys.argv[1]))\nj=s.begin_wechat_memory_import_job(import_run_id="tasks20-copy-native",account_id="copy-only")\nassert j==1\ns.finish_wechat_memory_import_job(j,status="completed",error="copy-only-native")\n'
        run([sys.executable,"-c",native_write,str(copy)],root=old_root,env=old_env)
        original_marker = release.original_version
        original_ledger = release.versions
        release.publish()  # Real JSON-pipe subprocess plus both real constructors.
        target = inventory(copy)
        assert len(before["ordinary_tables"]) - len(target["ordinary_tables"]) == 61
        with closing(sqlite3.connect(copy)) as db:
            assert state_schema_is_current(db)
        assert target["foreign_keys"] == before["foreign_keys"]
        store = AutoReplyStore(copy)
        email = EmailStore(copy)
        job = store.begin_wechat_memory_import_job(import_run_id="tasks20-copy-current",account_id="copy-only")
        assert job == 2
        store.finish_wechat_memory_import_job(job,status="completed",error="copy-only-current")
        current_wechat_facts = wechat_facts(copy,current=True)
        store.set_service_state("state_copy_current", "current")
        email.record_scan_cursor(account_id="copy-only-state-account", folder="copy-only-state-folder",
                                 uidvalidity=1, last_seen_uid=7)
        assert store.get_service_state("state_copy_current") == "current"
        assert email.get_scan_cursor("copy-only-state-account", "copy-only-state-folder")["last_seen_uid"] == 7
        print(json.dumps({"phase": "cold_current_and_public_writes", "tables": len(target["ordinary_tables"])}), flush=True)
        release.rollback()
        restored = inventory(copy)
        assert wechat_facts(copy,current=False)==current_wechat_facts, "current task field/type loss in inverse"
        assert restored["ordinary_tables"] == before["ordinary_tables"]
        assert restored["declarations"] == before["declarations"], "source declarations changed"
        assert restored["foreign_keys"] == before["foreign_keys"]
        changed = {name for name in before["facts"] if before["facts"][name] != restored["facts"][name]}
        assert changed == {"service_state", "email_scan_cursors", "wechat_memory_import_jobs"}, changed
        assert restored["original_witness_facts"] == before["original_witness_facts"], "original witness-family rows changed"
        expected_sequences = dict(before["sequence"])
        expected_sequences["wechat_memory_import_jobs"] = 2
        assert dict(restored["sequence"]) == expected_sequences, "current source allocator waters changed"
        with closing(sqlite3.connect(copy)) as db:
            assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == original_marker
            assert tuple(db.execute("select version,applied_at from email_schema_migrations order by version")) == original_ledger
        cold = json.loads(run([sys.executable, "-c", COLD, str(copy)], root=old_root, env=old_env))
        after_old = inventory(copy)
        predecessor_wechat_facts = wechat_facts(copy,current=False)
        assert after_old["ordinary_tables"] == before["ordinary_tables"]
        assert after_old["declarations"] == before["declarations"], "predecessor changed original declarations"
        expected_sequences["wechat_memory_import_jobs"] = 3
        assert dict(after_old["sequence"]) == expected_sequences, "predecessor current allocator waters changed"
        assert after_old["foreign_keys"] == before["foreign_keys"]
        assert {name for name in before["facts"] if before["facts"][name] != after_old["facts"][name]} == changed
        assert after_old["original_witness_facts"] == before["original_witness_facts"], "predecessor changed original witness-family rows"
        print(json.dumps({"phase": "genuine_predecessor_cold_read_write", **cold}), flush=True)
        repeated = prepare_database_schema_release(root, copy)
        repeated.publish()
        # A real fresh interpreter avoids initializer-cache assumptions after inverse.
        final_script = COLD.replace('== 7', '== 11').replace(
            's.set_service_state("state_copy_predecessor", "predecessor")',
            'assert s.get_service_state("state_copy_predecessor") == "predecessor"')
        final_script = final_script[:final_script.index("with s._connect() as db:")] + 'from app.task20_definitions import task20_projection\nwith s._connect() as db:\n    facts=tuple(tuple(r) for r in db.execute("select id,import_run_id,status,error from ("+task20_projection("wechat_memory_import_jobs")+") order by id"))\nassert facts==((1,"tasks20-copy-native","completed","copy-only-native"),(2,"tasks20-copy-current","completed","copy-only-current"),(3,"tasks20-copy-predecessor","completed","copy-only-predecessor"))\nprint(json.dumps({"public_store": True,"public_email_cursor": True,"public_wechat_import_job":True}))\n'
        assert json.loads(run([sys.executable, "-c", final_script, str(copy)], root=root,
                              env=environment)) == cold
        repeated.verify_loaded()
        final = inventory(copy)
        assert wechat_facts(copy,current=True)==predecessor_wechat_facts, "predecessor task field/type loss in reforward"
        assert final["ordinary_tables"] == target["ordinary_tables"]
        assert final["foreign_keys"] == before["foreign_keys"]
        assert run(["git", "rev-parse", "HEAD"], root=production) == predecessor, "production version moved during proof"
        receipt = {"status": "passed", "at_utc": datetime.now(timezone.utc).isoformat(),
                   "candidate_commit": candidate, "genuine_predecessor_commit": predecessor,
                   "source_backup_sha256": frozen_sha,
                   "source_ordinary_table_count": len(before["ordinary_tables"]),
                   "target_ordinary_table_count": len(target["ordinary_tables"]),
                   "restored_ordinary_table_count": len(restored["ordinary_tables"]),
                   "target_ordinary_tables": target["ordinary_tables"],
                   "unchanged_restored_tables": len(before["facts"]) - len(changed),
                   "public_witness_tables": sorted(changed),
                   "all_original_rows_preserved_in_three_witness_families": True,
                   "identity_comparison": "Source ROWID/value/typeof exact except accepted composite Email and Provider families, which use their declared primary keys/value/typeof. Only exact proof-owned witness keys excluded in three native families; source checked for witness collisions.",
                   "source_foreign_key_findings": before["foreign_keys"],
                   "exact_source_declarations_restored": True, "current_source_allocators_preserved": True,
                   "original_absent_wechat_stream_native_write_after_prepare": True,
                   "current_target_and_genuine_predecessor_wechat_writes_survive_inverse_reforward": True,
                   "original_store_marker_and_full_email_ledger_restored": True,
                   "real_paired_json_child_and_both_current_constructors": True,
                   "current_public_state_and_cursor_writes_survive_inverse": True,
                   "genuine_current_production_predecessor_cold_read_write": cold,
                   "predecessor_public_writes_survive_real_cold_reforward": True,
                   "elapsed_seconds": round(time.monotonic() - start, 3),
                   "boundary": "No native Agent CLI, task/audit/business/outbound/UI acceptance; no production write or deployment. Final ten-core-table consolidation remains open."}
    receipt["owned_temporary_directory_removed"] = not temporary.exists()
    assert receipt["owned_temporary_directory_removed"]
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
