"""Pinned options for explicit historical Action source factories."""

FINAL_ACTION_OPTIONS = dict(include_reply=True, include_email=True,
    include_skill_config_receipts=True, include_followup_sync=True,
    include_business_sync=True, include_task_delivery=True, include_errors=True,
    include_feedback=True, include_business_actions=True, include_email_history=True,
    inline_legacy_owner=True, canonical_email=True, physical_provenance=True, compact_payload=True,
    selection_references=True, result_references=True, execution_owner=True, sent_result_references=True, shared_lifecycle=True, specialized_claims=True, postfix_owner=True, logical_owner=True, action_entity=True)

