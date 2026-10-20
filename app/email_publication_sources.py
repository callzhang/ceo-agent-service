"""Finite Email action sources for current readers and deployment quiet checks."""
from app.state_publication_sources import publication_store_version

import sqlite3

from app.action_email_schema_release import (
    SOURCE_EMAIL_VERSION,
    SOURCE_STORE_VERSION,
)
from app.action_storage import email_action_projection
from app.sent_result_schema_release import (
    TARGET_STORE_VERSION, TARGET_EMAIL_VERSION, PREVIOUS_STORE_VERSIONS, PREVIOUS_EMAIL_PAIRS,
)


def _markers(db: sqlite3.Connection) -> tuple[str | None, int | None]:
    tables = {
        row[0] for row in db.execute("select name from sqlite_schema where type='table'")
    }
    store = publication_store_version(db, current_version=TARGET_STORE_VERSION,
                                        previous_versions=PREVIOUS_STORE_VERSIONS)
    email = None
    if "email_schema_migrations" in tables:
        email = db.execute(
            "select max(version) from email_schema_migrations"
        ).fetchone()[0]
    return store, email


def current_email_actions_available(db: sqlite3.Connection) -> bool:
    """A Store-only fresh database has an actions table but no Email runtime."""
    return _markers(db) == (TARGET_STORE_VERSION, TARGET_EMAIL_VERSION)


def publication_email_action_source(db: sqlite3.Connection) -> str:
    """Read the exact old or current action statuses during deployment quiet wait."""
    pair = _markers(db)
    if pair == (SOURCE_STORE_VERSION, SOURCE_EMAIL_VERSION):
        return "select status from email_actions"
    if pair == (TARGET_STORE_VERSION, TARGET_EMAIL_VERSION) or pair in PREVIOUS_EMAIL_PAIRS:
        return email_action_projection("email_actions")
    raise ValueError("unknown or mixed Email action publication source schema")
