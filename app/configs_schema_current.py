"""Current-declaration check for the Configs content tables."""

import sqlite3
from app.action_reply_storage import _source_declaration
from app import action_storage
from app.configs_schema_transfer import (
    SOURCE_TABLES, _target_declarations, target_declaration_matches,
)


_OBSERVATIONS = 'email_training_snapshot_observations'


_ACTION_VARIANTS = ('actual_no_memory_fk', 'fresh_memory_fk')


def _actions(
    variant: str,
    *,
    skill: bool,
    include_followup_sync: bool = False,
    include_business_sync: bool = False,
    include_task_delivery: bool = False,
    include_errors: bool = False,
    include_feedback: bool = False,
    include_business_actions: bool = False,
    include_email_history: bool = False,
    inline_legacy_owner: bool = False,
    physical_provenance: bool = False,
    compact_payload: bool = False,
    selection_references: bool = False,
    result_references: bool = False, execution_owner: bool = False, sent_result_references: bool = False, shared_lifecycle: bool = False, specialized_claims: bool = False, postfix_owner: bool = False, logical_owner=False,
) -> list[dict]:
    return action_storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True,
        include_skill_config_receipts=skill,
        include_followup_sync=include_followup_sync,
        include_business_sync=include_business_sync,
        include_task_delivery=include_task_delivery,
        include_errors=include_errors,
        include_feedback=include_feedback,
        include_business_actions=include_business_actions,
        include_email_history=include_email_history,
        inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload,
        selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner,
    )


def configs_content_schema_is_current(
    db: sqlite3.Connection,
    *,
    action_variant: str,
    include_followup_sync: bool = False,
    include_business_sync: bool = False,
    include_task_delivery: bool = False,
    include_errors: bool = False,
    include_feedback: bool = False,
    include_business_actions: bool = False,
    include_email_history: bool = False,
    canonical_email: bool = False,
    inline_legacy_owner: bool = False,
    physical_provenance: bool = False,
    compact_payload: bool = False,
    selection_references: bool = False,
    result_references: bool = False, execution_owner: bool = False, sent_result_references: bool = False, shared_lifecycle: bool = False, specialized_claims: bool = False, postfix_owner: bool = False, logical_owner=False, expected_action_objects=None,
) -> bool:
    """Read-only config/action predicate, independent of the Runs source profile."""
    if action_variant not in _ACTION_VARIANTS:
        raise ValueError('unsupported action declaration variant')
    expected_actions = expected_action_objects if expected_action_objects is not None else _actions(
        action_variant, skill=True,
        include_followup_sync=include_followup_sync,
        include_business_sync=include_business_sync,
        include_task_delivery=include_task_delivery,
        include_errors=include_errors,
        include_feedback=include_feedback,
        include_business_actions=include_business_actions,
        include_email_history=include_email_history,
        inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload,
        selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner,
    )
    if canonical_email:
        expected_actions = [({**obj, 'sql': obj['sql'].replace(
            'references email_classifications(id)', 'references emails(classification_id)'
        )} if obj['type'] == 'table' else obj) for obj in expected_actions]
    return (all(target_declaration_matches(db, kind, declaration)
                for kind, declaration in _target_declarations().items())
            and action_storage._objects(db, 'actions') == expected_actions
            and action_storage.action_schema_is_current(
                db, include_reply=True, reply_variant=action_variant,
                include_email=True, include_skill_config_receipts=True,
                include_followup_sync=include_followup_sync,
                include_business_sync=include_business_sync,
                include_task_delivery=include_task_delivery,
                include_errors=include_errors,
                include_feedback=include_feedback,
                include_business_actions=include_business_actions,
                include_email_history=include_email_history,
                canonical_email=canonical_email,
                inline_legacy_owner=inline_legacy_owner,
                physical_provenance=physical_provenance, compact_payload=compact_payload,
                selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner, expected_objects=expected_action_objects)
            and all(_source_declaration(db, kind)['sql'] is None
                    for kind in (*SOURCE_TABLES, _OBSERVATIONS)))
