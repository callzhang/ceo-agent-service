"""Explicit preceding Store parent shape for migration/inverse regression fixtures."""

import sqlite3

import app.store as store_module
from app.store import AutoReplyStore

LEGACY_REPLY_TASKS_SQL = "CREATE TABLE reply_tasks (\n                    id integer primary key autoincrement,\n                    channel text not null default 'dingtalk',\n                    conversation_id text not null,\n                    conversation_title text not null,\n                    single_chat integer not null,\n                    trigger_message_id text not null,\n                    trigger_create_time text not null,\n                    trigger_sender text not null,\n                    trigger_text text not null,\n                    trigger_message_json text not null default '{}',\n                    available_at text not null default '',\n                    force_new_decision integer not null default 0,\n                    oa_url text not null default '',\n                    manual_rerun_attempt_id integer not null default 0,\n                    manual_rerun_revision_key text not null default '',\n                    execution_generation text not null default 'initial',\n                    recovery_code text not null default '',\n                    business_object_key text not null default '',\n                    input_version integer not null default 1,\n                    claimed_input_version integer not null default 0,\n                    input_provenance_json text not null default '{}',\n                    input_compacted integer not null default 0,\n                    status text not null default 'pending',\n                    attempts integer not null default 0,\n                    locked_at text,\n                    error text not null default '',\n                    created_at text not null default current_timestamp,\n                    updated_at text not null default current_timestamp,\n                    unique(channel, conversation_id, trigger_message_id)\n                );"


def restore_legacy_reply_parent(store):
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        indexes = [
            row[0]
            for row in db.execute(
                "select sql from sqlite_master where type='index' and tbl_name='reply_tasks' and sql is not null"
            )
        ]
        AutoReplyStore.restore_reply_task_input_schema(
            db, original_tasks_sql=LEGACY_REPLY_TASKS_SQL, original_indexes=indexes
        )
    store_module._INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    return LEGACY_REPLY_TASKS_SQL, indexes


def migrate_legacy_reply_parent(store):
    """Exercise the historical reply-parent migration explicitly on current core."""
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        AutoReplyStore._migrate_reply_task_input_references(db)
        violations = db.execute("pragma foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"reply parent migration introduced {violations!r}")
    store_module._INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    return AutoReplyStore(store.path)


def restore_cross_channel_reply_parent(store):
    """Rebuild only the historical cross-channel parent on current core."""
    old_sql = LEGACY_REPLY_TASKS_SQL.replace(
        "unique(channel, conversation_id, trigger_message_id)",
        "unique(conversation_id, trigger_message_id)",
    )
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=OFF")
        db.execute("begin immediate")
        AutoReplyStore.restore_reply_task_input_schema(
            db, original_tasks_sql=old_sql, original_indexes=(),
        )
        db.execute(
            'create unique index "legacy user\'s trigger" '
            "on reply_tasks(conversation_id, trigger_message_id)"
        )
    store_module._INITIALIZED_STORE_PATHS.discard(store.path.resolve())
