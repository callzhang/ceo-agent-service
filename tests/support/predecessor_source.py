"""A Store 2026-10-21.1 / Email 53 database shaped like the retired predecessor publication source."""
import sqlite3


def source_database(path):
    from app.current_schema_manifest import create_current_email_migration_ledger
    from app.predecessor_database_schema import create_store_2026_10_21_1_schema
    from app.state_storage import upsert_state

    db = sqlite3.connect(path)
    create_store_2026_10_21_1_schema(db)
    create_current_email_migration_ledger(db)
    db.executemany(
        "insert into email_schema_migrations(version,applied_at) values(?,?)",
        ((version, f"email-{version}") for version in range(1, 54)),
    )
    db.commit()
    db.execute("begin")
    upsert_state(db, "service_state",
                 {"key": "store_schema_version", "value": "2026-10-21.1"},
                 {"value": "json_quote(excluded.value)"})
    db.commit()
    db.close()

