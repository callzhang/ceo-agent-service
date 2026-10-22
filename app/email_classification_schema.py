"""Unselected post-Email51 classification-parent DDL; caller owns transaction."""

import sqlite3

_CURRENT_CLASSIFICATION_SQL = """CREATE TABLE "email_classifications" (
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
                category text,
                predicted_category text,
                confirmed_category text,
                confidence real not null,
                margin real not null,
                probabilities_json text not null,
                model_id text not null,
                config_version text not null,
                status text not null,
                classification_source text not null,
                agent_result_json text not null default 'null',
                current_action_plan_id text,
                included_in_model_id text,
                legacy_processed_without_plan integer not null default 0
                    check(legacy_processed_without_plan in (0, 1)),
                confirmed_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            )"""


def create_current_email_classifications(db: sqlite3.Connection) -> None:
    """Create the full current classification parent if absent."""
    if (
        db.execute(
            "select 1 from sqlite_schema where type='table' and name='email_classifications'"
        ).fetchone()
        is None
    ):
        db.execute(_CURRENT_CLASSIFICATION_SQL)
