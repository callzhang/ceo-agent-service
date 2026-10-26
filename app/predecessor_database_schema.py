"""Pinned Store 2026-10-21.1 Task20/Run11 source declaration for releases.

This factory is migration-only. Runtime construction uses the assembled native
Task/Run declaration in ``current_database_schema``.
"""
from app.business_legacy_fields import current_business_declaration
from app.current_action_schema import create_source_current_action_schema
from app.business_schema_helpers import _create_auxiliary
from app.configs_storage import create_configs_schema
from app.schedules_storage import create_schedules_schema
from app.provider_state_storage import create_provider_state_schema
from app.state_storage import create_state_schema
from app.task_lifecycle_current_schema import create_current_task20_schema, install_current_task_child_guards
from app.logical_run_current_schema import create_current_run11_schema
from app.run_storage import install_run_child_guards
from app.email_canonical_storage import _create_emails
from app.provenance_storage import create_provenance_schema

PREDECESSOR_STORE_VERSION = "2026-10-21.1"

PREDECESSOR_ACTION_OPTIONS = dict(
    include_reply=True, include_email=True, include_skill_config_receipts=True,
    include_followup_sync=True, include_business_sync=True, include_task_delivery=True,
    include_errors=True, include_feedback=True, include_business_actions=True,
    include_email_history=True, inline_legacy_owner=True, canonical_email=True,
    physical_provenance=True, compact_payload=True, selection_references=True,
    result_references=True, execution_owner=True, sent_result_references=True,
    shared_lifecycle=True, specialized_claims=True, postfix_owner=True,
    logical_owner=True, action_entity=True,
)


def create_store_2026_10_21_1_schema(db):
    """Create the exact Task20/Run11 source used before native Phase 2."""
    create_schedules_schema(db)
    create_configs_schema(db)
    create_state_schema(db)
    create_provider_state_schema(db)
    create_current_task20_schema(db)
    create_current_run11_schema(db)
    create_provenance_schema(db)
    _create_emails(db, metadata_owner=True, action_owner=True)
    create_source_current_action_schema(db)
    business = current_business_declaration()
    db.execute(business["sql"])
    _create_auxiliary(db, business)
    install_run_child_guards(db)
    install_current_task_child_guards(db)
