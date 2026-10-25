"""Version marker and foreign-key helpers shared by the current Action publications."""

from app.state_definitions import state_projection


VERSION_KEY = "store_schema_version"


def _markers(db):
    marker = db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                        "where key=?", (VERSION_KEY,)).fetchone()
    versions = tuple(tuple(row) for row in db.execute(
        "select version,applied_at from main.email_schema_migrations order by version"))
    return tuple(marker) if marker is not None else None, versions


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))
