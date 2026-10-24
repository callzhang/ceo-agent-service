"""Install the final current schema on a genuinely blank database.

The caller owns its FK-off schema transaction. Historical migrations are not
part of blank construction. Existing current declarations are the source of
truth; source factories remain available only to explicit forward/inverse work.
"""

FINAL_ACTION_OPTIONS = dict(include_reply=True, include_email=True,
    include_skill_config_receipts=True, include_followup_sync=True,
    include_business_sync=True, include_task_delivery=True, include_errors=True,
    include_feedback=True, include_business_actions=True, include_email_history=True,
    inline_legacy_owner=True, canonical_email=True, physical_provenance=True, compact_payload=True,
    selection_references=True, result_references=True, execution_owner=True, sent_result_references=True, shared_lifecycle=True, specialized_claims=True, postfix_owner=True, logical_owner=True, action_entity=True)


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
