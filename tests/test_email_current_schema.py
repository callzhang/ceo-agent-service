"""Exact retained current Email DDL, without selecting either constructor."""

import json
from pathlib import Path
import re
import sqlite3

import pytest

from app.action_storage import create_action_schema, insert_action, insert_email_action
from app.email_store import EmailStore
from app.email_current_schema import (
    create_current_email_migration_ledger,
    create_current_email_non_action_schema,
)
from tests.test_email_action_storage import _cases
from app.store import AutoReplyStore


_ORACLE = json.loads(
    (Path(__file__).parent / "fixtures/email_current_schema_2773.json").read_text()
)


def _normalized(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).lower().replace(" if not exists ", " ")


def test_current_retained_objects_match_archived_native_metadata() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        create_current_email_non_action_schema(db)
        create_current_email_migration_ledger(db)
        for item in _ORACLE["objects"]:
            sql = db.execute("select sql from sqlite_schema where type=? and name=?", (item["kind"], item["name"])).fetchone()
            assert sql is not None, item["name"]
            assert _normalized(sql[0]) == _normalized(item["sql"]), item["name"]
            if item["kind"] == "table":
                assert [list(row) for row in db.execute(f"pragma table_xinfo({item['name']})")] == item["columns"]
                assert [list(row) for row in db.execute(f"pragma foreign_key_list({item['name']})")] == item["fks"]
        counts = dict(db.execute("select type,count(*) from sqlite_schema where name like 'email_%' or name like 'idx_email_%' or name like 'trg_email_%' group by type"))
        assert counts == {"table": 20, "index": 10, "trigger": 19}
        assert db.execute("pragma foreign_key_check").fetchall() == []
        create_current_email_non_action_schema(db)
        create_current_email_migration_ledger(db)
        assert dict(db.execute("select type,count(*) from sqlite_schema where name like 'email_%' or name like 'idx_email_%' or name like 'trg_email_%' group by type")) == counts


def test_current_retained_schema_is_caller_transactional_and_unseeded() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("begin")
        create_current_email_non_action_schema(db)
        assert db.execute("select 1 from sqlite_schema where name='email_schema_migrations'").fetchone() is None
        assert db.execute("select count(*) from email_category_configs").fetchone() == (0,)
        assert db.execute("select count(*) from email_model_promotion_configs").fetchone() == (0,)
        db.execute("rollback")
        assert db.execute("select 1 from sqlite_schema where name='email_classifications'").fetchone() is None


def test_real_opt_in_actions_use_current_parent_with_fk_enforcement() -> None:
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text, legacy_key text)")
        create_current_email_non_action_schema(db)
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True)
        db.execute("insert into runs(id,record_type,legacy_key) values(1,'agent_runs','1')")
        insert_action(db, "external_action_results", dict(
            external_action_key="older-key", business_object_key="business",
            action_identity="older-identity", operation="send",
            target_identifiers_json="raw targets", provider_result_json="raw result",
            result_digest="digest", first_agent_run_id=1,
        ))
        db.execute("""insert into email_classifications(
            id,account_id,folder,uidvalidity,uid,stable_message_identity,
            confidence,margin,probabilities_json,model_id,config_version,status,
            classification_source
        ) values(11,'account','INBOX',1,1,'message',0.5,0.1,'{}','model',
                 'config','pending_feedback','model')""")
        insert_email_action(db, "email_action_plans", _cases()["email_action_plans"])
        assert db.execute("select email_classification_id from actions where record_type='email_action_plans'").fetchone() == (11,)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            insert_email_action(db, "email_action_plans", _cases()["email_action_plans"] | {
                "action_plan_id": "missing-parent", "classification_id": 999,
            })


def test_default_constructors_select_current_email_actions(tmp_path: Path) -> None:
    path = tmp_path / "default.sqlite"
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select value from service_state where key='store_schema_version'"
        ).fetchone() == ("2026-10-14.1",)
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (52,)
        assert db.execute(
            "select 1 from sqlite_schema where type='table' and name='email_action_plans'"
        ).fetchone() is None
