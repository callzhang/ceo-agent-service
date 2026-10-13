"""Build the recorded 11.1 source independently of the current Store."""

import json
from pathlib import Path
import sqlite3

from app.store import AutoReplyStore

INVENTORY = Path(__file__).parents[1] / (
    "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json"
)
SOURCE_TABLES = (
    "reply_tasks",
    "reply_task_inputs",
    "agent_runs",
    "agent_runtime_attempts",
    "agent_run_state_events",
    "agent_run_events",
)


def previous_run_schema_store(path: Path, *, task_args: dict | None = None) -> Path:
    """Create an exact 11.1 core source, including both sides of the task FK."""
    inventory = json.loads(INVENTORY.read_text())["tables"]
    with sqlite3.connect(path) as db:
        for name in SOURCE_TABLES:
            source = inventory[name]
            db.execute(source["sql"])
            for _, _, sql in source["indexes_and_triggers"]:
                if sql is not None:
                    db.execute(sql)
        db.execute(
            "create table service_state(key text primary key,value text,updated_at text)"
        )
        db.execute(
            "insert into service_state values('store_schema_version','2026-10-11.1','source-time')"
        )
        if task_args is not None:
            args = {
                "conversation_id": task_args["conversation_id"],
                "conversation_title": task_args["conversation_title"],
                "single_chat": int(task_args["single_chat"]),
                "trigger_message_id": task_args["trigger_message_id"],
                "trigger_create_time": task_args["trigger_create_time"],
                "trigger_sender": task_args["trigger_sender"],
                "execution_generation": task_args.get("execution_generation", "initial"),
            }
            db.execute(
                f"insert into reply_tasks({','.join(args)}) values({','.join('?' for _ in args)})",
                tuple(args.values()),
            )
            input_args = {
                "reply_task_id": 1,
                "channel": "dingtalk",
                **{key: args[key] for key in (
                    "conversation_id", "conversation_title", "single_chat",
                    "trigger_message_id", "trigger_create_time", "trigger_sender",
                )},
                "trigger_text": task_args["trigger_text"],
                "business_object_key": "",
            }
            db.execute(
                f"insert into reply_task_inputs({','.join(input_args)}) values({','.join('?' for _ in input_args)})",
                tuple(input_args.values()),
            )
            db.execute("update reply_tasks set current_input_id=1 where id=1")
    return path


def add_source_agent_execution_receipts(db: sqlite3.Connection) -> None:
    """The deployed receipt declaration with its preceding core-run parent FK."""
    db.executescript("""
        create table agent_execution_receipts (
            id integer primary key autoincrement,
            agent_run_id integer not null,
            receipt_id text not null,
            operation_id text not null,
            cli text not null,
            command_path text not null,
            command_digest text not null,
            exit_code integer not null,
            completed integer not null,
            persisted integer not null,
            safe_to_confirm integer not null,
            effect_counted integer not null default 0,
            created_at text not null default current_timestamp,
            unique(agent_run_id, operation_id),
            foreign key(agent_run_id) references agent_runs(id)
        );
        create index idx_agent_execution_receipts_run
            on agent_execution_receipts(agent_run_id, id);
    """)


def migrate_previous_run_schema(path: Path) -> None:
    """Apply only the 11.1 core migration in its own atomic transaction."""
    from app.run_storage import migrate_run_schema

    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_run_schema(db)
        db.execute(
            "update service_state set value='2026-10-11.2' "
            "where key='store_schema_version'"
        )
        assert db.execute("pragma foreign_key_check").fetchall() == []


def current_store_with_migrated_core(
    tmp_path: Path,
    source_path: Path,
    *,
    include_receipts: bool = False,
) -> AutoReplyStore:
    """Open a fresh current Store with the independently migrated core rows."""
    path = tmp_path / "current-runs.db"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid",
        conversation_title="title",
        single_chat=False,
        trigger_message_id="msg",
        trigger_create_time="time",
        trigger_sender="sender",
        trigger_text="task",
        execution_generation="source",
    )
    with store._connect() as db:
        db.execute("attach database ? as source", (str(source_path),))
        source_columns = {
            row[1] for row in db.execute("pragma source.table_info(runs)")
        }
        columns = [
            row[1]
            for row in db.execute("pragma table_info(runs)")
            if row[1] in source_columns
        ]
        names = ",".join(f'"{name}"' for name in columns)
        db.execute(f"insert into runs({names}) select {names} from source.runs order by id")
        if include_receipts:
            from app.action_storage import insert_action
            for row in db.execute("select * from source.agent_execution_receipts order by id").fetchall():
                insert_action(db, "agent_execution_receipts", dict(row))
        db.commit()
        db.execute("detach database source")
    return store
