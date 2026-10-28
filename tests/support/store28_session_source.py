"""Frozen immediately previous Store28.1 source, only for migration fixtures."""
from app.model_retirement_schema_release import retirement_target_objects
from app.task8_storage import register_task8_functions


def create_store28_1_source(db):
    register_task8_functions(db)
    for kind in ('table', 'index', 'trigger'):
        for row in retirement_target_objects():
            if row[0] == kind:
                db.execute(row[3])
    db.execute("insert into state(id,record_type,legacy_key,payload_json) values(1,'service_state','1',?)",
               ('{"key":"store_schema_version","value":"2026-10-28.1","updated_at":"source-clock"}',))
    db.execute("insert into email_schema_migrations values(54,'source-clock')")
    db.commit()


def create_store28_2_source(db):
    from app.conversation_session_schema_release import session_target_objects
    register_task8_functions(db)
    for kind in ('table','index','trigger'):
        for row in session_target_objects():
            if row[0] == kind:
                db.execute(row[3])
    db.execute("insert into state(record_type,legacy_key,payload_json) values('service_state','1',?)",
               ('{"key":"store_schema_version","value":"2026-10-28.2","updated_at":"source-clock"}',))
    db.execute("insert into email_schema_migrations values(54,'source-clock')")
    db.commit()
