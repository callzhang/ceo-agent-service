"""Local persistence for email classifications and category configuration.

The store persists classifier decisions and immutable action-plan snapshots. It
does not connect to an email provider, create Agent tasks, or record execution
receipts.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_email_action_plan,
)


_UNREDACTED_EMAIL = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)


class EmailClassificationConflict(RuntimeError):
    """The classification was already resolved by another confirmation."""


def _validate_model_text(model_text: str) -> None:
    lowered = model_text.lower()
    if (
        _UNREDACTED_EMAIL.search(model_text)
        or "http://" in lowered
        or "https://" in lowered
    ):
        raise ValueError("model_text must be redacted")


class EmailStore:
    """Persist classifier results, user feedback, and local category config."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("pragma busy_timeout = 30000")
        db.execute("pragma foreign_keys = on")
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("pragma journal_mode = wal")
            db.executescript(
                """
                create table if not exists email_classifications (
                    id integer primary key,
                    account_id text not null,
                    folder text not null,
                    uidvalidity integer not null,
                    uid integer not null,
                    rfc_message_id text,
                    thread_id text,
                    stable_message_identity text not null unique,
                    sender text not null default '',
                    subject text not null default '',
                    preview text not null default '',
                    model_text text not null default '',
                    received_at text not null default '',
                    category text not null,
                    confidence real not null,
                    margin real not null,
                    probabilities_json text not null,
                    model_id text not null,
                    config_version text not null,
                    status text not null,
                    classification_source text not null,
                    action_plan_json text not null default 'null',
                    confirmed_at text not null default '',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );
                create index if not exists idx_email_classifications_status
                    on email_classifications(status, updated_at desc, id desc);
                create table if not exists email_category_configs (
                    category text primary key,
                    description text not null default '',
                    threshold real not null,
                    actions_json text not null,
                    action_parameters_json text not null default '{}',
                    enabled integer not null default 1,
                    config_version text not null,
                    updated_at text not null default current_timestamp
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _classification_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "folder": row["folder"],
            "uidvalidity": row["uidvalidity"],
            "uid": row["uid"],
            "rfc_message_id": row["rfc_message_id"],
            "thread_id": row["thread_id"],
            "stable_message_identity": row["stable_message_identity"],
            "sender": row["sender"],
            "subject": row["subject"],
            "preview": row["preview"],
            "received_at": row["received_at"],
            "category": row["category"],
            "confidence": row["confidence"],
            "margin": row["margin"],
            "probabilities": json.loads(row["probabilities_json"]),
            "model_id": row["model_id"],
            "config_version": row["config_version"],
            "status": row["status"],
            "classification_source": row["classification_source"],
            "action_plan": json.loads(row["action_plan_json"]),
            "confirmed_at": row["confirmed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_classification(
        self,
        classification: EmailClassification,
        *,
        sender: str = "",
        subject: str = "",
        preview: str = "",
        model_text: str = "",
        received_at: str = "",
    ) -> dict[str, Any]:
        _validate_model_text(model_text)
        locator = classification.provider_locator
        stable_message_identity = classification.stable_message_identity
        now = self._now()
        action_plan_json = (
            "null"
            if classification.action_plan is None
            else classification.action_plan.model_dump_json()
        )
        with self._connect() as db:
            db.execute(
                """
                insert into email_classifications (
                    id, account_id, folder, uidvalidity, uid, rfc_message_id,
                    thread_id, stable_message_identity, sender, subject, preview,
                    model_text, received_at, category, confidence, margin,
                    probabilities_json, model_id, config_version, status,
                    classification_source, action_plan_json, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(stable_message_identity) do update set
                    account_id=excluded.account_id,
                    folder=excluded.folder,
                    uidvalidity=excluded.uidvalidity,
                    uid=excluded.uid,
                    rfc_message_id=excluded.rfc_message_id,
                    thread_id=excluded.thread_id,
                    sender=excluded.sender,
                    subject=excluded.subject,
                    preview=excluded.preview,
                    model_text=case when email_classifications.classification_source='user'
                                    and email_classifications.model_text != ''
                                    then email_classifications.model_text
                                    else excluded.model_text end,
                    received_at=excluded.received_at,
                    category=case when email_classifications.classification_source='user'
                                  then email_classifications.category
                                  else excluded.category end,
                    confidence=case when email_classifications.classification_source='user'
                                    then email_classifications.confidence
                                    else excluded.confidence end,
                    margin=excluded.margin,
                    probabilities_json=excluded.probabilities_json,
                    model_id=case when email_classifications.classification_source='user'
                                  then email_classifications.model_id
                                  else excluded.model_id end,
                    config_version=case when email_classifications.classification_source='user'
                                        then email_classifications.config_version
                                        else excluded.config_version end,
                    status=case when email_classifications.classification_source='user'
                                then email_classifications.status
                                else excluded.status end,
                    classification_source=case when email_classifications.classification_source='user'
                                               then email_classifications.classification_source
                                               else excluded.classification_source end,
                    action_plan_json=case when email_classifications.classification_source='user'
                                          then email_classifications.action_plan_json
                                          else excluded.action_plan_json end,
                    updated_at=excluded.updated_at
                """,
                (
                    classification.classification_id,
                    locator.account_id,
                    locator.folder,
                    locator.uidvalidity,
                    locator.uid,
                    locator.rfc_message_id,
                    locator.thread_id,
                    stable_message_identity,
                    sender,
                    subject,
                    preview,
                    model_text,
                    received_at,
                    classification.category.value,
                    classification.confidence,
                    classification.margin,
                    json.dumps(classification.probabilities, ensure_ascii=False),
                    classification.model_id,
                    classification.config_version,
                    classification.status.value,
                    classification.classification_source,
                    action_plan_json,
                    now,
                ),
            )
            row = db.execute(
                """
                select * from email_classifications
                where stable_message_identity=?
                """,
                (stable_message_identity,),
            ).fetchone()
        assert row is not None
        return self._classification_row(row)

    def list_classifications(
        self, *, status: EmailClassificationStatus, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connect() as db:
            total = int(
                db.execute(
                    "select count(*) from email_classifications where status=?",
                    (status.value,),
                ).fetchone()[0]
            )
            rows = db.execute(
                """
                select * from email_classifications
                where status=? order by updated_at desc, id desc
                limit ? offset ?
                """,
                (status.value, limit, offset),
            ).fetchall()
        return [self._classification_row(row) for row in rows], total

    def list_training_examples(self) -> list[dict[str, str]]:
        """Return only user-confirmed, redacted texts for local retraining."""
        with self._connect() as db:
            rows = db.execute(
                """
                select stable_message_identity, model_text, category
                from email_classifications
                where classification_source='user' and model_text != ''
                order by id asc
                """
            ).fetchall()
        return [
            {
                "message_id": row["stable_message_identity"],
                "model_text": row["model_text"],
                "label": row["category"],
            }
            for row in rows
        ]

    def confirm_classification(
        self, row_id: int, category: EmailCategory
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                "select * from email_classifications where id=?", (row_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] != EmailClassificationStatus.PENDING_FEEDBACK.value:
                raise EmailClassificationConflict(
                    "email classification is no longer pending feedback"
                )
            selected_config = db.execute(
                """
                select actions_json, action_parameters_json, enabled, config_version
                from email_category_configs
                where category=?
                """,
                (category.value,),
            ).fetchone()
            if selected_config is None:
                actions: tuple[EmailAction, ...] = ()
                action_parameters: dict[EmailAction, dict[str, object]] = {}
                config_version = row["config_version"]
            else:
                actions = (
                    tuple(EmailAction(value) for value in json.loads(selected_config["actions_json"]))
                    if selected_config["enabled"]
                    else ()
                )
                stored_parameters = (
                    json.loads(selected_config["action_parameters_json"])
                    if selected_config["enabled"]
                    else {}
                )
                action_parameters = {
                    EmailAction(action): dict(parameters)
                    for action, parameters in stored_parameters.items()
                }
                config_version = selected_config["config_version"]
            created_at = datetime.now(timezone.utc)
            action_plan = build_email_action_plan(
                classification_id=row["id"],
                account_id=row["account_id"],
                category=category,
                classification_source="user",
                confidence=row["confidence"],
                model_id=row["model_id"],
                config_version=config_version,
                actions=actions,
                action_parameters=action_parameters,
                created_at=created_at,
            )
            now = self._now()
            updated_count = db.execute(
                """
                update email_classifications
                set category=?, status=?, classification_source='user',
                    config_version=?, action_plan_json=?, confirmed_at=?, updated_at=?
                where id=? and status=?
                """,
                (
                    category.value,
                    EmailClassificationStatus.PROCESSED.value,
                    config_version,
                    action_plan.model_dump_json(),
                    now,
                    now,
                    row_id,
                    EmailClassificationStatus.PENDING_FEEDBACK.value,
                ),
            ).rowcount
            if updated_count != 1:
                raise EmailClassificationConflict(
                    "email classification was confirmed concurrently"
                )
            updated = db.execute(
                "select * from email_classifications where id=?", (row_id,)
            ).fetchone()
        assert updated is not None
        return self._classification_row(updated)

    def list_configs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "select * from email_category_configs order by category"
            ).fetchall()
        return [self._config_row(row) for row in rows]

    def upsert_config(
        self,
        *,
        category: EmailCategory,
        description: str,
        threshold: float,
        actions: tuple[EmailAction, ...],
        action_parameters: Mapping[EmailAction, Mapping[str, object]],
        enabled: bool,
        config_version: str,
    ) -> dict[str, Any]:
        _validate_config(
            category=category,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        now = self._now()
        with self._connect() as db:
            db.execute(
                """
                insert into email_category_configs
                    (category, description, threshold, actions_json,
                     action_parameters_json, enabled, config_version, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(category) do update set
                    description=excluded.description,
                    threshold=excluded.threshold,
                    actions_json=excluded.actions_json,
                    action_parameters_json=excluded.action_parameters_json,
                    enabled=excluded.enabled,
                    config_version=excluded.config_version,
                    updated_at=excluded.updated_at
                """,
                (
                    category.value,
                    description,
                    threshold,
                    json.dumps(
                        [action.value for action in actions], ensure_ascii=False
                    ),
                    json.dumps(
                        {
                            action.value: dict(parameters)
                            for action, parameters in action_parameters.items()
                        },
                        ensure_ascii=False,
                    ),
                    int(enabled),
                    config_version,
                    now,
                ),
            )
            row = db.execute(
                "select * from email_category_configs where category=?",
                (category.value,),
            ).fetchone()
        assert row is not None
        return self._config_row(row)

    @staticmethod
    def _config_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "category": row["category"],
            "description": row["description"],
            "threshold": row["threshold"],
            "actions": json.loads(row["actions_json"]),
            "action_parameters": json.loads(row["action_parameters_json"]),
            "enabled": bool(row["enabled"]),
            "config_version": row["config_version"],
            "updated_at": row["updated_at"],
        }


def _validate_config(
    *,
    category: EmailCategory,
    threshold: float,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    config_version: str,
) -> None:
    build_email_action_plan(
        classification_id=1,
        account_id="configuration-validation",
        category=category,
        classification_source="model",
        confidence=threshold,
        model_id="configuration-validation",
        config_version=config_version,
        actions=actions,
        action_parameters={
            action: dict(parameters)
            for action, parameters in action_parameters.items()
        },
        created_at=datetime.now(timezone.utc),
    )
