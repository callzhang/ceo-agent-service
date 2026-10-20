"""Install the final current schema on a genuinely blank database.

The caller owns its FK-off schema transaction. Historical migrations are not
part of blank construction. Existing current declarations are the source of
truth; source factories remain available only to explicit forward/inverse work.
"""

from app.action_storage import create_action_schema
from app.business_legacy_owner_storage import _business_declaration
from app.business_schema_release import _create_auxiliary
from app.configs_storage import create_configs_schema
from app.schedules_storage import create_schedules_schema
from app.provider_state_storage import create_provider_state_schema
from app.state_storage import create_state_schema
from app.task20_current_schema import create_current_task20_schema, install_current_task_child_guards
from app.runs11_current_schema import create_current_run11_schema
from app.run_storage import install_run_child_guards
from app.email_canonical_storage import _create_emails
from app.provenance_storage import create_provenance_schema


FINAL_ACTION_OPTIONS = dict(include_reply=True, include_email=True,
    include_skill_config_receipts=True, include_followup_sync=True,
    include_business_sync=True, include_task_delivery=True, include_errors=True,
    include_feedback=True, include_business_actions=True, include_email_history=True,
    inline_legacy_owner=True, canonical_email=True, physical_provenance=True, compact_payload=True,
    selection_references=True, result_references=True, execution_owner=True)


def create_current_database_schema(db):
    create_schedules_schema(db)
    create_configs_schema(db)
    create_state_schema(db)
    create_provider_state_schema(db)
    create_current_task20_schema(db)
    create_current_run11_schema(db)
    create_provenance_schema(db)
    _create_emails(db, metadata_owner=True)
    create_action_schema(db, reply_variant="fresh_memory_fk", **FINAL_ACTION_OPTIONS)
    business = _business_declaration(target=True)
    db.execute(business["sql"])
    _create_auxiliary(db, business)
    install_run_child_guards(db)
    install_current_task_child_guards(db)
