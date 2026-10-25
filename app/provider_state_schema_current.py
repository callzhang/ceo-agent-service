"""Current-declaration check for the provider state tables."""

import sqlite3
from app.action_reply_storage import _fingerprint, _source_declaration


OBS = "email_provider_observations"


GEN = "email_provider_folder_observation_generations"


TARGET = "provider_state"


SOURCE_TABLES = (OBS, GEN)


TARGET_FINGERPRINT = "7e314cb5425c81e126e59e5626d4a18cb0ddaa956f2125c27d5842b97c264cbf"


def _present(db, schema, name):
    return db.execute(
        f"select 1 from {schema}.sqlite_schema where name=? collate nocase limit 1",
        (name,),
    ).fetchone() is not None


def _target_shape(db):
    if any(_present(db, "main", name) for name in SOURCE_TABLES):
        raise ValueError("provider-state source name remains in target")
    if _fingerprint(_source_declaration(db, TARGET)) != TARGET_FINGERPRINT:
        raise ValueError("unsupported provider-state target declaration")


def provider_state_schema_is_current(db: sqlite3.Connection) -> bool:
    """Recognize only the accepted target declaration without changing the DB."""
    try:
        _target_shape(db)
    except (ValueError, sqlite3.Error):
        return False
    return True
