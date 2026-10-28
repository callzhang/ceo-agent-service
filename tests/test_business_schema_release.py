"""Finite seven-kind Business schema release against native SQLite."""
from tests.business_schema_fixture import create_business_schema

from contextlib import closing
import json
from pathlib import Path
import math
import sqlite3

import pytest


FRESH_DECLARATIONS = Path(__file__).parent / "fixtures/business_four_kind_fresh_declarations.json"
WORK_SOURCE_DECLARATIONS = Path(__file__).parent / "fixtures/business_work_projects_source_declarations.json"
SOURCE_ORACLE = (Path(__file__).parents[1] / "docs/superpowers/specs/"
                 "2026-10-15-database-redesign-business-core-native-source.json")


def _source_declarations(variant):
    source = json.loads(FRESH_DECLARATIONS.read_text())
    source["parents"].update(
        json.loads(WORK_SOURCE_DECLARATIONS.read_text())["fresh"]["parents"]
    )
    work_source = json.loads(WORK_SOURCE_DECLARATIONS.read_text())[variant]
    source["parents"].update(work_source["parents"])
    if variant == "actual":
        actual = json.loads(SOURCE_ORACLE.read_text())["actual"]
        for name in ("business_tasks", "business_projects", "business_attention_items"):
            section = "parents" if name in source["parents"] else "children"
            oracle_table = actual["tables"].get(name, actual["external_endpoint_tables"].get(name))
            source[section][name]["sql"] = oracle_table["sql"]
        source["parents"].update(work_source["parents"])
        source["variant"] = "actual"
    return source


def _seed_complete_source(db):
    db.execute("insert into business_tasks(id,title,stage,status,merged_into_task_id,"
               "owner_name) values(-11,'cycle a','candidate','merged',-12,?)", (b"\x00\xff",))
    db.execute("insert into business_tasks(id,title,stage,status,merged_into_task_id) "
               "values(-12,'cycle b','candidate','merged',-11)")
    db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
               "values(-11,'project','anchor a','anchor a')")
    db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
               "values(-21,'project','anchor b','anchor b')")
    db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
               "values(-11,-11,'project a','test')")
    db.execute("insert into business_project_context_revisions(id,project_id,context_json,"
               "evidence_json) values(-11,-11,'{}','[]')")
    db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
               "values(-21,-21,'project b','test')")
    db.execute(
        "insert into service_bugfix_candidates(id,feedback_event_key,feedback_token,attempt_id,"
        "status,title,reason,feedback_comment,conversation_title,trigger_text) "
        "values(-71,'feedback:source','token:source',19,'pending','candidate','reason',"
        "'comment','conversation','trigger')"
    )
    db.execute(
        "insert into service_bugfix_candidates(id,feedback_event_key,title,reason,feedback_comment) "
        "values(171,'feedback:deleted','deleted','reason','comment')"
    )
    db.execute("delete from service_bugfix_candidates where id=171")
    db.execute("insert into todo_evidence_candidates(id,project_id,todo_id,source_type,source_ref,"
               "evidence_text,dedupe_key) values(172,-31,-32,'minutes','deleted','deleted','deleted')")
    db.execute("delete from todo_evidence_candidates where id=172")
    db.execute("insert into business_attention_items(id,stable_key,category,title,"
               "why_attention,current_state,ceo_action,anchor_id,evidence_signal_id) "
               "values(-41,'attention','fyi','attention','why','state','action',-11,-31)")
    db.execute("insert into business_project_candidates(id,cluster_id,title,reason,status,"
               "confirmed_project_id,confirmation_signal_id) "
               "values(-42,-31,'candidate','reason','confirmed',-11,-31)")
    db.execute("insert into business_task_evidence(rowid,task_id,signal_id,evidence_role) "
               "values(81,-11,-31,'discovery')")
    db.execute("insert into business_project_evidence(rowid,project_id,signal_id) "
               "values(82,-11,-31)")
    db.execute("insert into business_task_relations(rowid,from_task_id,to_task_id,"
               "relation_type,supporting_signal_id) values(83,-11,-12,'related_to',-31)")
    db.execute("insert into business_work_cluster_tasks(rowid,cluster_id,task_id) "
               "values(84,-31,-11)")
    db.execute("insert into business_attention_tasks(rowid,attention_item_id,task_id) "
               "values(85,-41,-11)")
    db.execute("insert into business_attention_proposal_tasks(rowid,attention_item_id,task_id) "
               "values(86,-41,-11)")
    db.execute("insert into business_task_date_evidence(id,task_id,date_type,raw_phrase,"
               "source_signal_id,actor_kind) "
               "values(-43,-11,'next_check_at','tomorrow',-31,'unknown')")
    db.execute("insert into business_task_events(id,task_id,event_type,before_json,"
               "after_json,reason) values(-44,-11,'created','{}','{}','test')")
    db.execute("insert into business_task_dingtalk_links(id,business_task_id,status) "
               "values(-45,-11,'creating')")
    db.execute("update business_task_dingtalk_links set last_error=? where id=-45",
               (b"\x00\xff",))
    db.execute("insert into business_task_follow_ups(id,business_task_id,source_signal_id,"
               "target_conversation_id,target_kind,question_text,scheduled_at,dedupe_key) "
               "values(-46,-11,-31,'conversation','direct','question','now','followup')")
    db.execute("insert into business_task_follow_ups(id,business_task_id,source_signal_id,"
               "target_conversation_id,target_kind,question_text,scheduled_at,dedupe_key) "
               "values(146,-11,-31,'conversation','direct','question','now','high')")
    db.execute("delete from business_task_follow_ups where id=146")
    db.execute("update business_task_follow_ups set revision=? where id=-46", (math.inf,))
    db.execute("insert into business_task_todo_sync_outbox(id,operation_key,"
               "business_task_id,operation) values(-47,'operation',-11,'create')")
    db.execute("insert into business_task_anchor_links(id,task_id,anchor_id,"
               "evidence_signal_id) values(-48,-11,-11,-31)")
    for record_id, column, value, work_column, work_id in (
        (-51, "task_id", -11, "work_project_id", -31),
        (-52, "anchor_id", -11, "work_todo_id", -31),
        (-53, "project_id", -11, "work_update_id", -31),
        (-54, "cluster_id", -31, "work_todo_id", -32),
    ):
        db.execute(f"insert into business_legacy_links(id,{column},{work_column}) "
                   "values(?,?,?)", (record_id, value, work_id))
    db.commit()


@pytest.mark.parametrize(
    ("kind", "alias"),
    (("task", "task_public_id"), ("project", "project_public_id"),
     ("anchor", "anchor_public_id"), ("work_cluster", "work_cluster_public_id")),
)
def test_typed_public_parent_key(kind, alias):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("begin")
        create_business_schema(db)
        xinfo = {row[1]: row for row in db.execute("pragma table_xinfo(business)")}
        assert xinfo[alias][6] == 2
        index = db.execute(
            "select sql from sqlite_schema where name=?",
            (f"idx_business_{kind}_public_id",),
        ).fetchone()[0]
        assert index.lower() == (
            f"create unique index idx_business_{kind}_public_id on business({alias})"
        )


def _isolated_parent_source(db):
    for task_id in (-101, -102, -103):
        db.execute("insert into business_tasks(id,title,stage) values(?,?,'candidate')",
                   (task_id, f"task {task_id}"))
    for anchor_id in (-201, -202, -203, -204):
        db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
                   "values(?,'project',?,?)", (anchor_id, f"ref {anchor_id}", f"anchor {anchor_id}"))
    for project_id, anchor_id in ((-301, -202), (-303, -203)):
        db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
                   "values(?,?,?,'test')", (project_id, anchor_id, f"project {project_id}"))
    for cluster_id in (-401, -402, -403):
        db.execute("insert into business_work_clusters(id,title) values(?,?)",
                   (cluster_id, f"cluster {cluster_id}"))
    for project_id in (-501, -502, -503):
        db.execute("insert into work_projects(id,title) values(?,?)",
                   (project_id, f"work project {project_id}"))
    for todo_id, project_id in ((-601, -501), (-602, -502), (-603, -503)):
        db.execute("insert into work_todos(id,project_id,title) values(?,?,?)",
                   (todo_id, project_id, f"work todo {todo_id}"))
    db.commit()


def _isolated_child_values(table, column):
    values = {
        "business_task_evidence": dict(task_id=-101, signal_id=-31, evidence_role="discovery"),
        "business_task_date_evidence": dict(task_id=-101, date_type="next_check_at",
                                            raw_phrase="tomorrow", source_signal_id=-31,
                                            actor_kind="unknown"),
        "business_task_events": dict(task_id=-101, event_type="created", before_json="{}",
                                     after_json="{}", reason="test"),
        "business_project_evidence": dict(project_id=-301, signal_id=-31),
        "business_task_dingtalk_links": dict(business_task_id=-101, status="creating"),
        "business_task_follow_ups": dict(business_task_id=-101, source_signal_id=-31,
                                         target_conversation_id="conversation", target_kind="direct",
                                         question_text="question", scheduled_at="now",
                                         dedupe_key="isolated"),
        "business_task_todo_sync_outbox": dict(operation_key="isolated",
                                               business_task_id=-101, operation="create"),
        "business_task_relations": dict(from_task_id=-101, to_task_id=-102,
                                        relation_type="related_to", supporting_signal_id=-31),
        "business_work_cluster_tasks": dict(cluster_id=-401, task_id=-101),
        "business_task_anchor_links": dict(task_id=-101, anchor_id=-201,
                                           evidence_signal_id=-31),
        "business_project_candidates": dict(cluster_id=-401, title="candidate", reason="reason",
                                            status="confirmed", confirmed_project_id=-301,
                                            confirmation_signal_id=-31),
        "business_attention_items": dict(id=-41, stable_key="isolated", category="fyi",
                                         title="attention", why_attention="why",
                                         current_state="state", ceo_action="action",
                                         anchor_id=-201, evidence_signal_id=-31),
        "business_attention_tasks": dict(attention_item_id=-41, task_id=-101),
        "business_attention_proposal_tasks": dict(attention_item_id=-41, task_id=-101),
    }
    if table == "business_legacy_links":
        if column == "work_project_id":
            return {"task_id": -101, "work_project_id": -501}
        if column == "work_todo_id":
            return {"task_id": -101, "work_todo_id": -601}
        return {column: {"task_id": -101, "cluster_id": -401,
                         "anchor_id": -201, "project_id": -301}[column],
                "work_project_id": -501}
    return values[table]


def _insert_isolated_child(db, table, column):
    if table in ("business_attention_tasks", "business_attention_proposal_tasks"):
        _insert_isolated_child(db, "business_attention_items", "anchor_id")
    values = _isolated_child_values(table, column)
    db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def test_native_unqualified_index_ddl_binds_temp_relation_first():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table main.business(public_id integer)")
        db.execute("create temp table business(shadow_marker text)")
        with pytest.raises(sqlite3.OperationalError, match="no such column: public_id"):
            db.execute("create index native_binding_probe on business(public_id)")
        assert db.execute("select 1 from main.sqlite_schema "
                          "where name='native_binding_probe'").fetchone() is None
