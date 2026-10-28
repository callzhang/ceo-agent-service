"""Current-data session ownership cycles; every application execution is isolated."""
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from app.conversation_session_schema_release import prepare_conversation_session_release
from app.task8_storage import register_task8_functions
from tools import validate_model_retirement_copy as native


def fact_digest(path, *, original_ceilings=None):
    facts = native.fact_digest(path, original_ceilings=original_ceilings)
    digest = hashlib.sha256()
    count = 0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        where = "not(record_type='service_state' and key='store_schema_version')"
        args = ()
        if original_ceilings is not None:
            where += ' and id<=?'
            args = (original_ceilings['state'],)
        for row in db.execute("select id,record_type,legacy_key,case when record_type='conversations' "
                              "then json_remove(payload_json,'$.codex_session_id','$.codex_session_contract_hash') "
                              f"else payload_json end from state where {where} order by id", args):
            digest.update(json.dumps([native._scalar(v) for v in row], ensure_ascii=False,
                                     separators=(',', ':')).encode())
            digest.update(b'\n')
            count += 1
    facts['state'] = {'rows': count, 'sha256': digest.hexdigest()}
    return facts


def _canonical(path, ceiling):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        return tuple(db.execute("select id,payload_json from state "
                                "where record_type='conversation_runtime_sessions' and id<=? order by id", (ceiling,)))


def _session_seed(root, path, expected, *, predecessor=False):
    program = r'''
import json,socket,subprocess,sys
from pathlib import Path
from uuid import uuid4
def refused(*args,**kwargs):
    raise AssertionError('session acceptance attempted network or CLI')
socket.socket.connect=refused
socket.socket.connect_ex=refused
import croniter
subprocess.Popen.__init__=refused
from app.store import AutoReplyStore
store=AutoReplyStore(Path(sys.argv[1]))
for prior in json.loads(sys.argv[2]):
    assert store.get_conversation(prior['key']).title == 'Copy session'
    assert store.get_conversation_runtime_session(prior['key'],'codex_oauth') == prior['session_id']
    assert store.get_conversation_runtime_session_contract_hash(prior['key'],'codex_oauth') == 'copy-hash'
key='session-copy:'+str(uuid4())
METADATA_WRITE
session='native-copy:'+str(uuid4())
store.upsert_conversation_runtime_session(key,'codex_oauth',session,'copy-hash')
store.upsert_conversation_runtime_session(key,'other-route','other-session','other-hash')
assert store.clear_conversation_runtime_session_if_matches(key,'codex_oauth','mismatch') == 0
assert store.get_conversation_runtime_session(key,'codex_oauth') == session
assert store.clear_conversation_runtime_session_if_matches(key,'codex_oauth',session) == 1
assert store.get_conversation_runtime_session(key,'other-route') == 'other-session'
store.upsert_conversation_runtime_session(key,'codex_oauth',session,'copy-hash')
print(json.dumps({'key':key,'session_id':session}))
'''
    # The archived predecessor fixture uses its actual four-argument API. This
    # is test-only source construction, never a runtime compatibility adapter.
    write = "store.upsert_conversation(key,'Copy session',True,None)" if predecessor else "store.upsert_conversation(key,'Copy session',True)"
    program = program.replace('METADATA_WRITE', write)
    env = {k: os.environ[k] for k in ('PATH','HOME','USER','LANG','LC_ALL','TMPDIR') if k in os.environ}
    env.update(PYTHONPATH=str(root),CEO_WORKER_DB=str(path),CEO_SERVICE_ROOT=str(root),
               PYTHONDONTWRITEBYTECODE='1',CEO_DATABASE_BACKUP_PAUSED='1')
    run = subprocess.run([sys.executable,'-c',program,str(path),json.dumps(expected)],
                         cwd=root,env=env,capture_output=True,text=True,timeout=120,check=True)
    return json.loads(run.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    for field in ('source', 'copy', 'old-root', 'report'):
        parser.add_argument('--'+field, type=Path, required=True)
    args = parser.parse_args()
    source, copy, old = args.source.resolve(), args.copy.resolve(), args.old_root.resolve()
    current = Path(__file__).resolve().parents[1]
    native._copy_source(source, copy)
    before = fact_digest(copy)
    ceilings = native._ceilings(copy)
    original = fact_digest(copy, original_ceilings=ceilings)
    canonical = _canonical(copy, ceilings['state'])
    release = prepare_conversation_session_release(copy)
    release.publish()
    release.verify_loaded()
    assert fact_digest(copy) == before
    assert _canonical(copy, ceilings['state']) == canonical
    # This fixture also proves ordinary Task/Run/Action/provider-double facts
    # remain usable and readable by real predecessor code through the inverse.
    first = native._seed(current, copy, [])
    session_first = _session_seed(current, copy, [])
    present = fact_digest(copy)
    release.rollback()
    assert fact_digest(copy) == present
    assert _canonical(copy, ceilings['state']) == canonical
    second = native._seed(old, copy, [first])
    session_second = _session_seed(old, copy, [session_first], predecessor=True)
    after_old = fact_digest(copy)
    again = prepare_conversation_session_release(copy)
    again.publish()
    again.finalize()
    assert fact_digest(copy) == after_old
    third = native._seed(current, copy, [first, second])
    session_third = _session_seed(current, copy, [session_first,session_second])
    assert fact_digest(copy, original_ceilings=ceilings) == original
    assert _canonical(copy, ceilings['state']) == canonical
    result = {
        'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'passed',
        'source': str(source), 'copy': str(copy), 'old_root': str(old),
        'native_tables_including_sequence': len(before), 'source_fact_hashes': before,
        'forward_preserved': True, 'inverse_retained_new_facts': True,
        'old_code_cold_read_write': True, 'reforward_preserved': True,
        'all_canonical_session_payloads_byte_equal': True,
        'original_rows_unchanged': True, 'provider': 'double; network and CLI refused',
        'fixture_identities': [first, second, third],
        'new_session_identities': [session_first,session_second,session_third],
    }
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_fact_hashes', 'fixture_identities')}))


if __name__ == '__main__':
    main()
