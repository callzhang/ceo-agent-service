"""Opt-in canonical Email conversion against explicit historical current13 SQL."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import action_storage as actions
from app.store import AutoReplyStore
from app.email_canonical_storage import _action_objects, _create_actions, _create_source_table


@pytest.fixture(scope="module")
def current13(tmp_path_factory):
    path = tmp_path_factory.mktemp("email-canonical-source") / "base.sqlite3"
    AutoReplyStore(path)
    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                          "2026-10-17-email-canonical-current13-source.json").read_text())
    db = sqlite3.connect(path)
    try:
        db.execute("pragma foreign_keys=off")
        # The current constructor selects the inline owner. Rebuild the
        # independent historical source declarations before its old Action
        # guard is installed; do not use the conversion under test here.
        db.execute("alter table business drop column import_task_id")
        legacy_capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                                     "2026-10-17-email-canonical-current-copy.json").read_text())
        for table in ("business_source_documents", "business_legacy_links"):
            legacy_objects = legacy_capsule[
                "post_target_remaining_business_declarations"
            ][table]["objects"]
            for kind in ("table", "index", "trigger"):
                for obj_kind, _name, _owner, sql in legacy_objects:
                    if obj_kind == kind and sql:
                        db.execute(sql)
        db.execute("drop table actions")
        for kind in ("emails", "email_classifications", "email_messages"):
            db.execute(f"drop table if exists {kind}")
        for kind in ("email_messages", "email_classifications"):
            _create_source_table(db, capsule["fresh_native_declarations"][kind])
        _create_actions(db, _action_objects("fresh_memory_fk", target=False))
        db.commit()
    finally:
        db.close()
    return path


def _db(tmp_path, current13):
    path = tmp_path / "email.sqlite3"
    shutil.copyfile(current13, path)
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=off")
    return db


def _message(stable="stable-1", **overrides):
    return {
        "account_id": "account", "stable_message_identity": stable,
        "folder": " INBOX ", "uidvalidity": 10, "uid": 20,
        "rfc_message_id": "<message>", "in_reply_to": "", "references_json": "[]",
        "thread_identity": "thread", "sender": "sender", "recipients_json": "[]",
        "subject": "subject", "normalized_text": "body", "preview": "preview",
        "attachment_metadata_json": "[]", "received_at": "received",
        "created_at": "message-created", "updated_at": "message-updated",
    } | overrides


def _classification(stable="stable-1", **overrides):
    return {
        "id": 101, "account_id": "account", "folder": "INBOX",
        "uidvalidity": 10, "uid": 20, "stable_message_identity": stable,
        "rfc_message_id": None, "thread_id": "class-thread", "sender": "class-sender",
        "subject": "class-subject", "preview": "class-preview", "model_text": "model-body",
        "received_at": "class-received", "category": None, "predicted_category": None,
        "confirmed_category": None, "confidence": 0.5, "margin": 0.1,
        "probabilities_json": "{}", "model_id": "model", "config_version": "v1",
        "status": "pending_feedback", "classification_source": "model",
        "agent_result_json": "null", "current_action_plan_id": None,
        "included_in_model_id": None, "legacy_processed_without_plan": 0,
        "confirmed_at": "", "created_at": "class-created", "updated_at": "class-updated",
    } | overrides


def _insert(db, table, values):
    db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _insert_source_action(db, kind, public_key, values):
    """Historical object-payload fixture, independent of the final vector DAO."""
    from app.action_email_definitions import EMAIL_PUBLIC_FIELDS

    assert values[EMAIL_PUBLIC_FIELDS[kind]] == public_key
    rowid = db.execute("select coalesce(max(cast(legacy_key as integer)),0)+1 from actions "
                       "where record_type=?", (kind,)).fetchone()[0]
    db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
               (kind, str(rowid), actions._native_payload(values)))

