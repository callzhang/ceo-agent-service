"""Install the final current schema on a genuinely blank database.

The caller owns its FK-off schema transaction. Historical migrations are not
part of blank construction. Existing current declarations are the source of
truth; source factories remain available only to explicit forward/inverse work.
"""



def create_current_database_schema(db):
    from app.task8_storage import register_task8_functions
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    register_task8_functions(db)
    declarations = task_run_entity_assembly_objects()
    for sql in declarations.values():
        if sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    for sql in declarations.values():
        if not sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
