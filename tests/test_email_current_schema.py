"""Exact retained current Email DDL, without selecting either constructor."""

import json
from pathlib import Path
import re
import sqlite3


from app.email_store import EmailStore
from app.email_current_schema import (
    create_current_email_migration_ledger,
    create_current_email_non_action_schema,
)
from app.store import AutoReplyStore
from app.store import STORE_SCHEMA_VERSION
from app.state_definitions import state_projection
from app.provider_state_schema_current import provider_state_schema_is_current


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
            if item["name"] in {
                "email_provider_observations",
                "email_provider_folder_observation_generations",
                "idx_email_provider_observations_folder",
            }:
                continue  # Frozen native Email51 metadata belongs to the source.
            sql = db.execute("select sql from sqlite_schema where type=? and name=?", (item["kind"], item["name"])).fetchone()
            assert sql is not None, item["name"]
            assert _normalized(sql[0]) == _normalized(item["sql"]), item["name"]
            if item["kind"] == "table":
                assert [list(row) for row in db.execute(f"pragma table_xinfo({item['name']})")] == item["columns"]
                assert [list(row) for row in db.execute(f"pragma foreign_key_list({item['name']})")] == item["fks"]
        counts = dict(db.execute("select type,count(*) from sqlite_schema where name like 'email_%' or name like 'idx_email_%' or name like 'trg_email_%' group by type"))
        assert counts == {"table": 18, "index": 9, "trigger": 19}
        assert provider_state_schema_is_current(db)
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


def test_default_constructors_select_current_email_actions(tmp_path: Path) -> None:
    path = tmp_path / "default.sqlite"
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            f"select value from ({state_projection('service_state')}) where key='store_schema_version'"
        ).fetchone() == (STORE_SCHEMA_VERSION,)
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (54,)
        assert db.execute(
            "select 1 from sqlite_schema where type='table' and name='email_action_plans'"
        ).fetchone() is None
