"""Positive account mutations against the genuine published fresh constructor."""

from contextlib import closing
import sqlite3

import pytest

from app.schedules_storage import create_schedules_schema
from app.configs_definitions import config_projection
from app.configs_storage import (
    create_configs_schema, delete_config, insert_config, update_config,
)
from tests.configs_source_fixtures import capture, insert as source_insert
from tests.test_configs_storage import VALID
from tests.test_email_action_writes import _databases

pytest_plugins = ("test_native_copy_lifecycle_harness",)

ACCOUNT_ROWID = -1000007
CATEGORY_ROWID = -1000008
BINDING_ROWID = -1000009
OLD_KEY = "storage-positive-account"
NEW_KEY = "storage-positive-renamed"
CATEGORY_KEY = "storage-positive-category"


def _assert_account_state(source, target, *, key, display, bindings):
    fields = tuple(
        column["name"] for column in
        capture()["fresh"]["tables"]["email_accounts"]["columns"]
    )
    params = (OLD_KEY, NEW_KEY)
    source_rows = source.execute(
        "select rowid," + ",".join(fields) +
        " from email_accounts where account_id in (?,?) order by rowid", params,
    ).fetchall()
    projection = config_projection(
        "email_accounts", fresh=True, include_rowid=True,
    )
    target_rows = target.execute(
        projection + " and public_key in (?,?) order by rowid", params,
    ).fetchall()
    assert source_rows == target_rows
    expected_count = int(key is not None)
    assert len(source_rows) == expected_count
    if key is not None:
        assert source_rows[0][:3] == (ACCOUNT_ROWID, key, display)

    source_types = source.execute(
        "select typeof(rowid)," + ",".join(f"typeof({field})" for field in fields) +
        " from email_accounts where account_id in (?,?) order by rowid", params,
    ).fetchall()
    target_types = target.execute(
        "select " + ",".join(f"typeof({field})" for field in ("rowid", *fields)) +
        " from (" + projection + " and public_key in (?,?)) order by rowid", params,
    ).fetchall()
    assert source_types == target_types

    source_count = source.execute(
        "select count(*) from email_accounts where account_id in (?,?)", params,
    ).fetchone()
    target_count = target.execute(
        "select count(*) from configs where record_type='email_accounts' "
        "and public_key in (?,?)", params,
    ).fetchone()
    assert source_count == target_count == (expected_count,)
    binding_params = (CATEGORY_KEY, *params)
    source_binding_count = source.execute(
        "select count(*) from email_category_folder_bindings "
        "where category_key=? and account_id in (?,?)", binding_params,
    ).fetchone()
    target_binding_count = target.execute(
        "select count(*) from configs where record_type='email_category_folder_bindings' "
        "and binding_category_key=? and binding_account_id in (?,?)",
        binding_params,
    ).fetchone()
    assert source_binding_count == target_binding_count == (bindings,)


def test_published_fresh_account_update_and_delete_cascade(old_email_copy):
    _, source_path = old_email_copy
    old_stub, target = _databases(include_skill_config_receipts=True)
    with closing(old_stub), closing(target), closing(sqlite3.connect(source_path)) as source:
        source.execute("pragma foreign_keys=on")
        predecessors = {
            "email_actions", "email_reply_dispatch_claims",
            "email_unsubscribe_claims",
        }
        assert {row[0] for row in source.execute(
            "select name from sqlite_master where type='table' "
            "and name in ('email_actions','email_reply_dispatch_claims',"
            "'email_unsubscribe_claims')"
        )} == predecessors
        assert len(source.execute(
            "select name from sqlite_master where type='trigger' "
            "and tbl_name='email_accounts' and name like '%blocks_account_%'"
        ).fetchall()) == 6

        create_schedules_schema(target)
        create_configs_schema(target)
        target.commit()
        target.execute("begin")
        account = VALID["email_accounts"] | {"account_id": OLD_KEY}
        category = VALID["email_category_configs"] | {"category_key": CATEGORY_KEY}
        binding = VALID["email_category_folder_bindings"] | {
            "account_id": OLD_KEY, "category_key": CATEGORY_KEY,
        }
        for kind, values, rowid in (
            ("email_accounts", account, ACCOUNT_ROWID),
            ("email_category_configs", category, CATEGORY_ROWID),
            ("email_category_folder_bindings", binding, BINDING_ROWID),
        ):
            native = source_insert(source, kind, {"rowid": rowid, **values})
            stored = insert_config(target, kind, values, source_rowid=rowid)
            assert (native.rowcount, native.lastrowid) == (
                stored.rowcount, stored.lastrowid,
            ) == (1, rowid)
        _assert_account_state(source, target, key=OLD_KEY, display="A", bindings=1)

        assert source.execute(
            "update email_accounts set display_name='B' where rowid=?",
            (ACCOUNT_ROWID,),
        ).rowcount == update_config(
            target, "email_accounts", ACCOUNT_ROWID, {"display_name": "B"},
        ) == 1
        _assert_account_state(source, target, key=OLD_KEY, display="B", bindings=1)

        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            source.execute(
                "update email_accounts set account_id=? where rowid=?",
                (NEW_KEY, ACCOUNT_ROWID),
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            update_config(
                target, "email_accounts", ACCOUNT_ROWID, {"account_id": NEW_KEY},
            )
        _assert_account_state(source, target, key=OLD_KEY, display="B", bindings=1)

        assert source.execute(
            "delete from email_category_folder_bindings where rowid=?",
            (BINDING_ROWID,),
        ).rowcount == delete_config(
            target, "email_category_folder_bindings", BINDING_ROWID,
        ) == 1
        _assert_account_state(source, target, key=OLD_KEY, display="B", bindings=0)

        assert source.execute(
            "update email_accounts set account_id=? where rowid=?",
            (NEW_KEY, ACCOUNT_ROWID),
        ).rowcount == update_config(
            target, "email_accounts", ACCOUNT_ROWID, {"account_id": NEW_KEY},
        ) == 1
        _assert_account_state(source, target, key=NEW_KEY, display="B", bindings=0)

        renamed_binding = binding | {"account_id": NEW_KEY}
        native = source_insert(
            source, "email_category_folder_bindings",
            {"rowid": BINDING_ROWID, **renamed_binding},
        )
        stored = insert_config(
            target, "email_category_folder_bindings", renamed_binding,
            source_rowid=BINDING_ROWID,
        )
        assert (native.rowcount, native.lastrowid) == (
            stored.rowcount, stored.lastrowid,
        ) == (1, BINDING_ROWID)
        _assert_account_state(source, target, key=NEW_KEY, display="B", bindings=1)

        assert source.execute(
            "delete from email_accounts where rowid=?", (ACCOUNT_ROWID,),
        ).rowcount == delete_config(
            target, "email_accounts", ACCOUNT_ROWID,
        ) == 1
        _assert_account_state(source, target, key=None, display=None, bindings=0)
        assert target.execute("pragma foreign_key_check").fetchall() == []
