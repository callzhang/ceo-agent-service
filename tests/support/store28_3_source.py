"""Frozen immediately previous Store28.3 source for migration fixtures."""
from app.task8_storage import register_task8_functions


def create_store28_3_source(db):
    from app.wechat_voice_transcript_schema_release import source_objects
    register_task8_functions(db)
    for kind in ('table', 'index', 'trigger'):
        for row in source_objects():
            if row[0] == kind:
                db.execute(row[3])
    db.execute("insert into state(record_type,legacy_key,payload_json) values('service_state','1',?)",
               ('{"key":"store_schema_version","value":"2026-10-28.3","updated_at":"source-clock"}',))
    db.execute("insert into email_schema_migrations values(54,'source-clock')")
    db.commit()
