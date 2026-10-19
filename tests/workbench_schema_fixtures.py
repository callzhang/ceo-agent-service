"""Independent source13 DDL, captured from deployed 44a1a4e (metadata only)."""

import json
from pathlib import Path
import sqlite3
import os
import subprocess
import sys

SOURCE = json.loads(
    (Path(__file__).parent / "fixtures/store13-workbench-schema.json").read_text()
)
SOURCES = (
    "scheduled_task_runs",
    "workbench_tasks",
    "workbench_turns",
    "workbench_events",
)
CHILDREN = ("workbench_attachments", "workbench_artifacts", "workbench_confirmations")


def source_database(path):
    from tests.configs_source_fixtures import capture

    db = sqlite3.connect(path)
    for state in SOURCE["tables"].values():
        for kind, _, sql in state["objects"]:
            if kind == "table":
                db.execute(sql)
    for name, state in SOURCE["tables"].items():
        # The object capture is name-sorted. SQLite reports newest indexes
        # first; restore the actual Config profile's schedule index order.
        objects = reversed(state["objects"]) if name == "scheduled_tasks" else state["objects"]
        for kind, _, sql in objects:
            if kind != "table" and sql:
                db.execute(sql)
    # Companion State declarations must also be genuine. The original row's
    # key, value and timestamp stay unchanged under the frozen native shape.
    db.execute(capture()["fresh"]["tables"]["service_state"]["sql"])
    db.executescript("""
        insert into service_state values('store_schema_version','2026-10-11.3','source time');
        insert into scheduled_tasks(id,name,prompt,cron_expression,timezone,runtime_id,enabled)
            values(7,'schedule','prompt','* * * * *','UTC','route',1);
        insert into scheduled_task_config_versions values(9,' {"command":"", "required_runtime_capabilities":[]} ');
        insert into scheduled_task_runs(id,event_id,scheduled_task_id,trigger_kind,scheduled_for,snapshot_id,occurrence_count,execution_kind,execution_id,lease_expires_at,result_summary)
            values(1,'event-one',7,'scheduled','instant',9,0,'reply_task','1',NULL,' retained summary '),
                  (2,'event-two',7,'manual','instant',NULL,-1,'service_command','command:name','',x'ff00');
        insert into workbench_tasks(id,title,runtime_kind) values('01','leading','codex'),('1','numeric','friday'),('uuid-A','uuid','other'),('',x'00ff','blob'),(NULL,'null one','codex'),(NULL,'null two','codex');
        insert into workbench_turns(id,task_id,client_request_id,task_sequence,user_text,status,error_detail,resume_context)
            values('turn-A','01','req-A',1,x'00ff','completed',' retained error ',' {"resume": true} '),
                  ('01','1','req-B',1,'text','failed','',''),
                  ('1','uuid-A','req-C',1,'text','stopped','',''),
                  (NULL,'','req-D',1,'null one','completed','',''),
                  (NULL,'','req-E',2,'null two','completed','','');
        insert into workbench_events(id,turn_id,sequence,event_type,payload_json) values(1,'turn-A',0,'unknown',' {"retained": [2,1]} '),(2,'01',-1,'',x'00ff');
        insert into workbench_attachments values('attach','01','request','name','mime',2,'sha','path','attachment time');
        insert into workbench_artifacts values('artifact','turn-A','label','path','mime','artifact time');
        insert into workbench_confirmations(id,turn_id,action_kind,target,summary,risk,arguments_json,status,result_json)
            values('confirmation','turn-A','kind','target','summary','risk',x'00ff','executed',' {"result": [1,2]} ');
        update workbench_tasks set rowid=rowid*7+10;
        update workbench_turns set rowid=rowid*11+5;
        update workbench_tasks set rowid=9223372036854775807 where title='null two';
        update workbench_turns set rowid=9223372036854775807 where client_request_id='req-E';
        update workbench_attachments set rowid=31;
        update workbench_artifacts set rowid=47;
        update workbench_confirmations set rowid=59;
        insert into codex_session_search_index(id,session_id,source_type,source_id,title,created_at,updated_at)
            values(3,'session','workbench','turn-A','needle title','created exact','updated exact');
        insert into codex_session_search_fts(codex_session_search_fts) values('rebuild');
    """)
    from app.task_storage import insert_task
    from app.run_storage import insert_run

    insert_task(
        db,
        "work_summary_inputs",
        dict(
            id=10,
            legacy_key="1",
            source_type="test",
            source_ref="retained",
            payload_json=b"WSI1\x00\xff",
        ),
    )
    insert_run(
        db,
        "task_agent_runs",
        dict(
            id=20,
            legacy_key="1",
            summary_input_id=1,
            projection_json=' {"receipt": true} ',
        ),
    )
    # The real source has one missing Meeting parent; reserve its absent physical ID.
    insert_run(
        db,
        "meeting_alignment_runs",
        dict(
            id=190042, legacy_key="2298", task_id=190043, job_id=4908, status="failed"
        ),
    )
    db.execute("update sqlite_sequence set seq=200000 where name='tasks'")
    db.execute("update sqlite_sequence set seq=220000 where name='runs'")
    db.execute("update sqlite_sequence set seq=300000 where name='scheduled_task_runs'")
    db.execute("update sqlite_sequence set seq=400000 where name='workbench_events'")
    db.commit()
    return db


def rows(db, table):
    without_rowid = db.execute(
        "select wr from pragma_table_list(?) where schema='main'", (table,)
    ).fetchone()[0]
    if without_rowid:
        primary_key = [row[1] for row in sorted(
            (row for row in db.execute(f'pragma table_info("{table}")') if row[5]),
            key=lambda row: row[5],
        )]
        order = ",".join(f'"{column}"' for column in primary_key)
    else:
        order = "rowid"
    return db.execute(f'select * from "{table}" order by {order}').fetchall()


def fingerprint(db):
    return dict(
        source_rowids={
            table: db.execute(f"select rowid,* from {table} order by rowid").fetchall()
            for table in ("workbench_tasks", "workbench_turns", *CHILDREN)
            if db.execute(
                "select 1 from sqlite_master where name=?", (table,)
            ).fetchone()
        },
        objects=db.execute(
            "select type,name,sql from sqlite_master where sql is not null order by type,name"
        ).fetchall(),
        sequences=(
            db.execute("select name,seq from sqlite_sequence order by name").fetchall()
            if db.execute(
                "select 1 from sqlite_master where name='sqlite_sequence'"
            ).fetchone()
            else []
        ),
        rows={
            name: rows(db, name)
            for (name,) in db.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like 'codex_session_search_fts_%' order by name"
            ).fetchall()
        },
    )


def full_source_database(path, auxiliary_path, published_source):
    """Independent source13 scope plus archived Store and Email declarations."""
    from app.store import _INITIALIZED_STORE_PATHS

    # The captured source13 scheduling declarations belong to the actual
    # profile. Start its companion account table from the same frozen profile,
    # before the archived constructor fills the remaining Email declarations.
    # A fresh account table plus actual schedules is not a historical schema.
    from tests.configs_source_fixtures import capture

    with sqlite3.connect(path) as source:
        source.execute(capture()["actual"]["tables"]["email_accounts"]["sql"])

    subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; from app.email_store import EmailStore; import sys; EmailStore(Path(sys.argv[1]))", str(path)],
        cwd=published_source,
        env={**os.environ, "PYTHONPATH": str(published_source)},
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; from app.store import AutoReplyStore; import sys; AutoReplyStore(Path(sys.argv[1]))", str(auxiliary_path)],
        cwd=published_source,
        env={**os.environ, "PYTHONPATH": str(published_source)},
        check=True, capture_output=True, text=True,
    )
    with sqlite3.connect(auxiliary_path) as auxiliary:
        objects = auxiliary.execute(
            "select type,name,tbl_name,sql from sqlite_master where sql is not null and tbl_name<>'actions' order by case type when 'table' then 0 when 'index' then 1 else 2 end,rowid"
        ).fetchall()
    # The source13 reply and Email declarations below are actual-profile.
    # Their companion follow-up declarations must be the paired actual set,
    # rather than fresh tables from the auxiliary constructor. Only schema
    # objects are copied from that database; there are no rows to replace.
    followup_source = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json').read_text())['actual']['tables']
    paired_action_sources = {
        **json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-17-task-delivery-source-metadata.json').read_text())['actual'],
        **{name: state['declaration'] for name, state in json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-17-feedback-actions-source-metadata.json').read_text())['actual'].items()},
    }
    db = source_database(path)
    names = {row[0] for row in db.execute("select name from sqlite_master")}
    for kind, name, owner, sql in objects:
        if owner in followup_source or owner in paired_action_sources:
            continue
        if (
            name not in names
            and not name.startswith("sqlite_")
            and not name.startswith("codex_session_search_fts_")
        ):
            # Source13 scope must retain only its independently captured declarations.
            owned = db.execute(
                "select tbl_name from sqlite_master where name=?", (name,)
            ).fetchone()
            if owned is not None:
                continue
            if kind == "table":
                db.execute(sql)
            elif not any(
                f"on {table}" in sql.lower() or f'on "{table}"' in sql.lower()
                for table in SOURCE["tables"]
            ):
                db.execute(sql)
    for state in followup_source.values():
        db.execute(state['sql'])
    for state in followup_source.values():
        for obj in (*reversed(state['indexes']), *state['triggers']):
            if obj['sql']:
                db.execute(obj['sql'])
    for state in paired_action_sources.values():
        db.execute(next(sql for kind, _, _, sql in state['objects'] if kind == 'table'))
    for state in paired_action_sources.values():
        objects_by_name = {name: sql for _, name, _, sql in state['objects']}
        for index in reversed(state['indexes']):
            sql = objects_by_name.get(index['list'][1])
            if sql:
                db.execute(sql)
        for kind, _, _, sql in state['objects']:
            if kind == 'trigger':
                db.execute(sql)
    claims = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-email-actions-published-source.json").read_text())["tables"]["email_unsubscribe_claims"]["declaration"]
    assert db.execute("select count(*) from email_unsubscribe_claims").fetchone() == (0,)
    db.execute("drop table email_unsubscribe_claims")
    db.execute(claims["sql"])
    for obj in reversed(claims["indexes"]):
        if obj["sql"]:
            db.execute(obj["sql"])
    # The archived auxiliary Store owns actions, but source13 still used the
    # six independently captured candidate declarations. Keep this a real old
    # schema instead of copying a new table into a database with an old marker.
    candidate_source = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-13-database-redesign-phase5a-inventory.json').read_text())['tables']
    for state in candidate_source.values():
        for obj in state['objects']:
            if obj['type'] == 'table':
                db.execute(obj['sql'])
    for state in candidate_source.values():
        for obj in state['objects']:
            if obj['type'] != 'table' and obj['sql']:
                db.execute(obj['sql'])
    postfix_source = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-13-database-redesign-phase5c-postfix-source.json').read_text())['tables']
    for state in postfix_source.values():
        for obj in state['objects']:
            if obj['type'] == 'table':
                db.execute(obj['sql'])
    # Old source13 independently retained native receipts, even after the
    # auxiliary archived constructor has retired their physical declarations.
    receipt_source = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-13-database-redesign-phase5b-inventory.json').read_text())['tables']
    for name in ("external_action_results", "agent_effect_intents", "agent_execution_receipts"):
        state = receipt_source[name]
        for obj in state['objects']:
            if obj['type'] == 'table' and not db.execute('select 1 from sqlite_schema where name=?', (obj['name'],)).fetchone():
                db.execute(obj['sql'])
    for name in ("external_action_results", "agent_effect_intents", "agent_execution_receipts"):
        state = receipt_source[name]
        for obj in state['objects']:
            if obj['type'] != 'table' and obj['sql'] and not db.execute('select 1 from sqlite_schema where name=?', (obj['name'],)).fetchone():
                db.execute(obj['sql'])
    # Source13 had these physical tables. The current auxiliary constructor
    # retires them, so restore the independently captured published source DDL.
    reply_source = json.loads((Path(__file__).parents[1] / 'docs/superpowers/specs/2026-10-13-database-redesign-phase5e-reply-source.json').read_text())['actual']['tables']
    for state in reply_source.values():
        db.execute(state['sql'])
    for state in reply_source.values():
        for obj in (*reversed(state['indexes']), *state['triggers']):
            if obj['sql']:
                db.execute(obj['sql'])
    db.commit()
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    return db
