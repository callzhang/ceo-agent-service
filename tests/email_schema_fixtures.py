"""Explicit legacy schema reconstruction for migration regression fixtures."""
import sqlite3

from app.email_store import EmailStore


def restore_action_plan_snapshot_column(db: sqlite3.Connection) -> None:
    """Reconstruct the <=48 column before declaring a fresh fixture legacy."""
    if 'action_plan_json' in {row[1] for row in db.execute('pragma table_info(email_classifications)')}:
        return
    db.execute("alter table email_classifications add column action_plan_json text not null default 'null'")
    original_factory = db.row_factory
    db.row_factory = sqlite3.Row
    rows = db.execute('select * from email_action_plans').fetchall()
    for row in rows:
        plan = EmailStore._stored_action_plan(row)
        exclude = ({'authorization_snapshot_format', 'action_authorizations'}
                   if row['legacy_serialization_pre_v16'] == 1 and row['authorization_snapshot_json'] is None
                   else set())
        db.execute('update email_classifications set action_plan_json=? where current_action_plan_id=?',
                   (plan.model_dump_json(exclude=exclude), plan.action_plan_id))
    db.row_factory = original_factory


def restore_schema48_action_plan_snapshots(db: sqlite3.Connection) -> None:
    """Return a current fixture to the real48 snapshot representation."""
    restore_action_plan_snapshot_column(db)
    db.execute('delete from email_schema_migrations where version>48')
    db.execute("insert or ignore into email_schema_migrations values (48, 'legacy-fixture')")
