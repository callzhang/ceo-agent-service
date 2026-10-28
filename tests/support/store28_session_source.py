"""Frozen immediately previous Store28.2 source for migration fixtures."""
from app.task8_storage import register_task8_functions


def create_store28_2_source(db):
    from app.feedback_owner_schema_source import feedback_source_objects
    register_task8_functions(db)
    for kind in ('table','index','trigger'):
        for row in feedback_source_objects():
            if row[0] == kind:
                db.execute(row[3])
    db.execute("insert into state(record_type,legacy_key,payload_json) values('service_state','1',?)",
               ('{"key":"store_schema_version","value":"2026-10-28.2","updated_at":"source-clock"}',))
    db.execute("insert into email_schema_migrations values(54,'source-clock')")
    db.commit()
