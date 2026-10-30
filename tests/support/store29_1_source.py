"""Exact published Store29.1 source construction for explicit release tests."""

from app.attempt_run_ownership_release import source_objects, SOURCE_STORE_VERSION
from app.task8_storage import register_task8_functions


def create_store29_1_source(db):
    register_task8_functions(db)
    objects = source_objects()
    for kind, _, _, sql in objects:
        if kind == "table":
            db.execute(sql)
    for kind, _, _, sql in objects:
        if kind != "table":
            db.execute(sql)
    db.execute("insert into state(record_type,legacy_key,payload_json) values('service_state','1',?)",
               ('{"key":"store_schema_version","value":"' + SOURCE_STORE_VERSION
                + '","updated_at":"source-time"}',))
    db.execute("insert into email_schema_migrations(version,applied_at) values(54,'source-time')")
