"""Finite opt-in Email action source declarations.

Values below are frozen from the 2026-10-13 nine-table and four-child inventories.
They are explicit target metadata; runtime never loads the source artifacts.
"""

EMAIL_FIELDS = {'email_action_attempts': ('id',
                           'action_id',
                           'attempt_number',
                           'status',
                           'provider_operation',
                           'provider_target',
                           'provider_result_id',
                           'error',
                           'started_at',
                           'finished_at'),
 'email_action_plans': ('action_plan_id',
                        'action_plan_version',
                        'classification_id',
                        'account_id',
                        'category',
                        'classification_source',
                        'confidence',
                        'model_id',
                        'config_version',
                        'actions_json',
                        'action_parameters_json',
                        'authorization_snapshot_json',
                        'legacy_serialization_pre_v16',
                        'created_at'),
 'email_actions': ('action_id',
                   'action_plan_id',
                   'classification_id',
                   'account_id',
                   'action_type',
                   'parameters_json',
                   'config_version',
                   'status',
                   'attempt_count',
                   'started_at',
                   'finished_at',
                   'next_attempt_at',
                   'provider_operation',
                   'provider_target',
                   'provider_result_id',
                   'error',
                   'created_at',
                   'updated_at'),
 'email_unsubscribe_cancellations': ('action_identity', 'decision_json'),
 'email_unsubscribe_claims': ('action_identity',
                              'effect_digest',
                              'action_plan_id',
                              'action_plan_version',
                              'classification_id',
                              'account_id',
                              'stable_message_identity',
                              'thread_identity',
                              'entry_reference',
                              'operations_json',
                              'owner_id',
                              'owner_generation',
                              'lease_token',
                              'account_updated_at',
                              'status',
                              'phase',
                              'claimed_at',
                              'updated_at',
                              'audit_agent_run_id'),
 'email_unsubscribe_continuations': ('action_identity',
                                     'effect_digest',
                                     'observation_reference',
                                     'controls_json',
                                     'created_at',
                                     'updated_at'),
 'email_unsubscribe_effects': ('action_identity',
                               'effect_digest',
                               'previous_effect_digest',
                               'operations_json',
                               'audit_agent_run_id',
                               'created_at'),
 'email_unsubscribe_receipts': ('action_identity',
                                'effect_digest',
                                'action_plan_id',
                                'action_plan_version',
                                'classification_id',
                                'account_id',
                                'stable_message_identity',
                                'thread_identity',
                                'entry_reference',
                                'outcome',
                                'receipt_id',
                                'evidence',
                                'result_text',
                                'observation_digest',
                                'started_at',
                                'completed_at',
                                'created_at',
                                'result_text_truncated',
                                'result_text_digest',
                                'entry_url'),
 'email_unsubscribe_steps': ('id',
                             'action_identity',
                             'effect_digest',
                             'sequence',
                             'operation',
                             'state',
                             'reference',
                             'created_at'),
 'email_direct_retry_requests': ('action_id',
                                 'attempt_number',
                                 'source_readback_digest',
                                 'account_configuration_snapshot',
                                 'requested_at'),
 'email_feedback_requests': ('feedback_request_id',
                             'classification_id',
                             'category',
                             'expected_current_action_plan_id',
                             'resulting_action_plan_id',
                             'applied_at'),
 'email_reply_dispatch_claims': ('action_identity',
                                 'effect_digest',
                                 'action_plan_id',
                                 'classification_id',
                                 'account_id',
                                 'stable_message_identity',
                                 'outgoing_message_id',
                                 'owner_id',
                                 'owner_generation',
                                 'lease_token',
                                 'sender',
                                 'thread_identity',
                                 'account_updated_at',
                                 'account_snapshot_json',
                                 'status',
                                 'claimed_at',
                                 'updated_at'),
 'email_reply_receipts': ('action_identity',
                          'effect_digest',
                          'action_plan_id',
                          'classification_id',
                          'account_id',
                          'stable_message_identity',
                          'outgoing_message_id',
                          'provider_operation',
                          'provider_target',
                          'provider_result_id',
                          'provider_receipt_json',
                          'display_excerpt',
                          'created_at')}

EMAIL_TYPES = {'id': 'integer',
 'action_id': 'text',
 'attempt_number': 'integer',
 'status': 'text',
 'provider_operation': 'text',
 'provider_target': 'text',
 'provider_result_id': 'text',
 'error': 'text',
 'started_at': 'text',
 'finished_at': 'text',
 'action_plan_id': 'text',
 'action_plan_version': 'integer',
 'classification_id': 'integer',
 'account_id': 'text',
 'category': 'text',
 'classification_source': 'text',
 'confidence': 'real',
 'model_id': 'text',
 'config_version': 'text',
 'actions_json': 'text',
 'action_parameters_json': 'text',
 'authorization_snapshot_json': 'text',
 'legacy_serialization_pre_v16': 'integer',
 'created_at': 'text',
 'action_type': 'text',
 'parameters_json': 'text',
 'attempt_count': 'integer',
 'next_attempt_at': 'text',
 'updated_at': 'text',
 'action_identity': 'text',
 'decision_json': 'text',
 'effect_digest': 'text',
 'stable_message_identity': 'text',
 'thread_identity': 'text',
 'entry_reference': 'text',
 'operations_json': 'text',
 'owner_id': 'text',
 'owner_generation': 'integer',
 'lease_token': 'text',
 'account_updated_at': 'text',
 'phase': 'text',
 'claimed_at': 'text',
 'audit_agent_run_id': 'integer',
 'observation_reference': 'text',
 'controls_json': 'text',
 'previous_effect_digest': 'text',
 'outcome': 'text',
 'receipt_id': 'text',
 'evidence': 'text',
 'result_text': 'text',
 'observation_digest': 'text',
 'completed_at': 'text',
 'result_text_truncated': 'integer',
 'result_text_digest': 'text',
 'entry_url': 'text',
 'sequence': 'integer',
 'operation': 'text',
 'state': 'text',
 'reference': 'text',
 'source_readback_digest': 'text',
 'account_configuration_snapshot': 'text',
 'requested_at': 'text',
 'feedback_request_id': 'text',
 'expected_current_action_plan_id': 'text',
 'resulting_action_plan_id': 'text',
 'applied_at': 'text',
 'outgoing_message_id': 'text',
 'sender': 'text',
 'account_snapshot_json': 'text',
 'provider_receipt_json': 'text',
 'display_excerpt': 'text'}

EMAIL_NOT_NULL = {'email_action_attempts': ('action_id',
                           'attempt_number',
                           'status',
                           'provider_operation',
                           'provider_target',
                           'provider_result_id',
                           'error',
                           'started_at',
                           'finished_at'),
 'email_action_plans': ('action_plan_version',
                        'classification_id',
                        'account_id',
                        'category',
                        'classification_source',
                        'confidence',
                        'model_id',
                        'config_version',
                        'actions_json',
                        'action_parameters_json',
                        'legacy_serialization_pre_v16',
                        'created_at'),
 'email_actions': ('action_plan_id',
                   'classification_id',
                   'account_id',
                   'action_type',
                   'parameters_json',
                   'config_version',
                   'status',
                   'attempt_count',
                   'started_at',
                   'finished_at',
                   'next_attempt_at',
                   'provider_operation',
                   'provider_target',
                   'provider_result_id',
                   'error',
                   'created_at',
                   'updated_at'),
 'email_unsubscribe_cancellations': ('decision_json',),
 'email_unsubscribe_claims': ('effect_digest',
                              'action_plan_id',
                              'action_plan_version',
                              'classification_id',
                              'account_id',
                              'stable_message_identity',
                              'thread_identity',
                              'entry_reference',
                              'operations_json',
                              'owner_id',
                              'owner_generation',
                              'lease_token',
                              'account_updated_at',
                              'status',
                              'phase',
                              'claimed_at',
                              'updated_at'),
 'email_unsubscribe_continuations': ('effect_digest',
                                     'observation_reference',
                                     'controls_json',
                                     'created_at',
                                     'updated_at'),
 'email_unsubscribe_effects': ('action_identity',
                               'effect_digest',
                               'previous_effect_digest',
                               'operations_json',
                               'created_at'),
 'email_unsubscribe_receipts': ('effect_digest',
                                'action_plan_id',
                                'action_plan_version',
                                'classification_id',
                                'account_id',
                                'stable_message_identity',
                                'thread_identity',
                                'entry_reference',
                                'outcome',
                                'receipt_id',
                                'evidence',
                                'result_text',
                                'observation_digest',
                                'started_at',
                                'completed_at',
                                'created_at',
                                'result_text_truncated',
                                'result_text_digest',
                                'entry_url'),
 'email_unsubscribe_steps': ('action_identity',
                             'effect_digest',
                             'sequence',
                             'operation',
                             'state',
                             'reference',
                             'created_at'),
 'email_direct_retry_requests': ('action_id',
                                 'attempt_number',
                                 'source_readback_digest',
                                 'account_configuration_snapshot',
                                 'requested_at'),
 'email_feedback_requests': ('classification_id',
                             'category',
                             'resulting_action_plan_id',
                             'applied_at'),
 'email_reply_dispatch_claims': ('effect_digest',
                                 'action_plan_id',
                                 'classification_id',
                                 'account_id',
                                 'stable_message_identity',
                                 'outgoing_message_id',
                                 'owner_id',
                                 'owner_generation',
                                 'lease_token',
                                 'sender',
                                 'thread_identity',
                                 'account_updated_at',
                                 'account_snapshot_json',
                                 'status',
                                 'claimed_at',
                                 'updated_at'),
 'email_reply_receipts': ('effect_digest',
                          'action_plan_id',
                          'classification_id',
                          'account_id',
                          'stable_message_identity',
                          'outgoing_message_id',
                          'provider_operation',
                          'provider_target',
                          'provider_result_id',
                          'provider_receipt_json',
                          'display_excerpt',
                          'created_at')}

EMAIL_CHECKS = {'email_action_attempts': ('email_attempt_number > 0',
                           "email_status in ( 'done' , 'skipped' , 'failed' )"),
 'email_action_plans': ('email_action_plan_version > 0',
                        'email_authorization_snapshot_json is null or json_valid ( '
                        'email_authorization_snapshot_json )',
                        "email_classification_source in ( 'model' , 'user' , 'agent' )",
                        'email_confidence >= 0.0 and email_confidence <= 1.0',
                        'json_valid ( email_action_parameters_json )',
                        'json_valid ( email_actions_json )',
                        'email_legacy_serialization_pre_v16 in ( 0 , 1 )'),
 'email_actions': ("email_action_type in ( 'label' , 'mark_read' , 'archive' , 'move' , 'trash' , "
                   "'flag_important' )",
                   'email_attempt_count >= 0',
                   'json_valid ( email_parameters_json )',
                   "email_status in ( 'pending' , 'processing' , 'done' , 'skipped' , 'failed' )"),
 'email_unsubscribe_cancellations': ('json_valid ( email_decision_json )',
                                     "trim ( email_action_identity ) != ''"),
 'email_unsubscribe_claims': ('email_action_plan_version > 0',
                              'email_audit_agent_run_id is null or email_audit_agent_run_id > 0',
                              'json_valid ( email_operations_json )',
                              'email_owner_generation > 0',
                              "email_phase in ( 'prepared' , 'navigating' , 'effect_uncertain' , "
                              "'terminal' )",
                              "email_status in ( 'dispatching' , 'awaiting_audit' , 'uncertain' , "
                              "'done' )",
                              "trim ( email_account_updated_at ) != ''",
                              "trim ( email_action_identity ) != ''",
                              "trim ( email_claimed_at ) != ''",
                              "trim ( email_effect_digest ) != ''",
                              "trim ( email_entry_reference ) != ''",
                              "trim ( email_lease_token ) != ''",
                              "trim ( email_owner_id ) != ''",
                              "trim ( email_thread_identity ) != ''",
                              "trim ( email_updated_at ) != ''"),
 'email_unsubscribe_continuations': ('json_valid ( email_controls_json )',
                                     "trim ( email_action_identity ) != ''",
                                     "trim ( email_created_at ) != ''",
                                     "trim ( email_effect_digest ) != ''",
                                     "trim ( email_observation_reference ) != ''",
                                     "trim ( email_updated_at ) != ''"),
 'email_unsubscribe_effects': ('email_audit_agent_run_id is null or email_audit_agent_run_id > 0',
                               'json_valid ( email_operations_json )',
                               "email_previous_effect_digest = '' or length ( "
                               'email_previous_effect_digest ) = 64',
                               "trim ( email_action_identity ) != ''",
                               "trim ( email_created_at ) != ''",
                               "trim ( email_effect_digest ) != ''"),
 'email_unsubscribe_receipts': ('email_action_plan_version > 0',
                                "email_entry_url = '' or length ( email_entry_url ) <= 2048",
                                'length ( email_result_text ) <= 16384',
                                "email_observation_digest = '' or length ( "
                                'email_observation_digest ) = 64',
                                "email_outcome in ( 'done' , 'already_unsubscribed' , "
                                "'skipped_no_reliable_entry' , 'skipped_login_required' , "
                                "'skipped_captcha' , 'skipped_payment' , 'failed_browser' , "
                                "'failed_provider_auth' )",
                                "email_result_text_digest = '' or length ( "
                                'email_result_text_digest ) = 64',
                                'email_result_text_truncated in ( 0 , 1 )',
                                "trim ( email_action_identity ) != ''",
                                "trim ( email_created_at ) != ''",
                                "trim ( email_effect_digest ) != ''",
                                "trim ( email_entry_reference ) != ''",
                                "trim ( email_evidence ) != ''",
                                "trim ( email_receipt_id ) != ''",
                                "trim ( email_thread_identity ) != ''"),
 'email_unsubscribe_steps': ('email_sequence > 0',
                             "trim ( email_action_identity ) != ''",
                             "trim ( email_created_at ) != ''",
                             "trim ( email_effect_digest ) != ''",
                             "trim ( email_operation ) != ''",
                             "trim ( email_reference ) != ''",
                             "trim ( email_state ) != ''"),
 'email_direct_retry_requests': ('email_attempt_number > 0',
                                 'json_array_length ( email_account_configuration_snapshot ) = 9',
                                 "json_type ( email_account_configuration_snapshot ) = 'array'",
                                 'json_valid ( email_account_configuration_snapshot )',
                                 'length ( email_source_readback_digest ) = 64',
                                 "trim ( email_action_id ) != ''",
                                 "trim ( email_requested_at ) != ''"),
 'email_feedback_requests': ('email_expected_current_action_plan_id is null or '
                             'email_expected_current_action_plan_id != '
                             'email_resulting_action_plan_id',
                             'email_expected_current_action_plan_id is null or trim ( '
                             "email_expected_current_action_plan_id ) != ''",
                             "trim ( email_applied_at ) != ''",
                             "trim ( email_feedback_request_id ) != ''",
                             "trim ( email_resulting_action_plan_id ) != ''"),
 'email_reply_dispatch_claims': ('json_valid ( email_account_snapshot_json )',
                                 'email_owner_generation > 0',
                                 "email_status in ( 'dispatching' , 'retryable' , 'uncertain' , "
                                 "'done' )",
                                 "trim ( email_account_updated_at ) != ''",
                                 "trim ( email_action_identity ) != ''",
                                 "trim ( email_claimed_at ) != ''",
                                 "trim ( email_effect_digest ) != ''",
                                 "trim ( email_lease_token ) != ''",
                                 "trim ( email_outgoing_message_id ) != ''",
                                 "trim ( email_owner_id ) != ''",
                                 "trim ( email_sender ) != ''",
                                 "trim ( email_thread_identity ) != ''",
                                 "trim ( email_updated_at ) != ''"),
 'email_reply_receipts': ('json_valid ( email_provider_receipt_json )',
                          "trim ( email_action_identity ) != ''",
                          "trim ( email_effect_digest ) != ''",
                          "trim ( email_outgoing_message_id ) != ''",
                          "trim ( email_provider_operation ) != ''",
                          "trim ( email_provider_result_id ) != ''")}

EMAIL_SOURCE_INDEXES = {'email_action_attempts': ((True, ('action_id', 'attempt_number')),),
 'email_action_plans': ((True, ('classification_id', 'action_plan_version')),
                        (True, ('action_plan_id',))),
 'email_actions': ((False, ('classification_id', 'action_plan_id')),
                   (False, ('status', 'updated_at', 'action_id')),
                   (True, ('action_plan_id', 'action_type')),
                   (True, ('action_id',))),
 'email_unsubscribe_cancellations': ((True, ('action_identity',)),),
 'email_unsubscribe_claims': ((False, ('status', 'updated_at', 'action_identity')),
                              (True, ('action_identity',))),
 'email_unsubscribe_continuations': ((True, ('action_identity',)),),
 'email_unsubscribe_effects': ((True, ('action_identity', 'effect_digest')),),
 'email_unsubscribe_receipts': ((False, ('classification_id', 'action_identity')),
                                (True, ('action_identity',))),
 'email_unsubscribe_steps': ((True, ('action_identity', 'sequence')),),
 'email_direct_retry_requests': ((True, ('action_id', 'attempt_number')),),
 'email_feedback_requests': ((True, ('resulting_action_plan_id',)),
                             (True, ('expected_current_action_plan_id',)),
                             (True, ('feedback_request_id',))),
 'email_reply_dispatch_claims': ((False, ('status', 'updated_at', 'action_identity')),
                                 (True, ('outgoing_message_id',)),
                                 (True, ('action_identity',))),
 'email_reply_receipts': ((True, ('outgoing_message_id',)), (True, ('action_identity',)))}

EMAIL_SOURCE_FKS = {'email_action_attempts': (('action_id', 'email_actions', 'action_id', 'NO ACTION', 'RESTRICT'),),
 'email_action_plans': (('classification_id',
                         'email_classifications',
                         'id',
                         'NO ACTION',
                         'RESTRICT'),),
 'email_actions': (('classification_id', 'email_classifications', 'id', 'NO ACTION', 'RESTRICT'),
                   ('action_plan_id',
                    'email_action_plans',
                    'action_plan_id',
                    'NO ACTION',
                    'RESTRICT')),
 'email_unsubscribe_cancellations': (),
 'email_unsubscribe_claims': (('classification_id',
                               'email_classifications',
                               'id',
                               'NO ACTION',
                               'RESTRICT'),
                              ('action_plan_id',
                               'email_action_plans',
                               'action_plan_id',
                               'NO ACTION',
                               'RESTRICT')),
 'email_unsubscribe_continuations': (('action_identity',
                                      'email_unsubscribe_claims',
                                      'action_identity',
                                      'NO ACTION',
                                      'RESTRICT'),),
 'email_unsubscribe_effects': (('action_identity',
                                'email_unsubscribe_claims',
                                'action_identity',
                                'NO ACTION',
                                'RESTRICT'),),
 'email_unsubscribe_receipts': (('classification_id',
                                 'email_classifications',
                                 'id',
                                 'NO ACTION',
                                 'RESTRICT'),
                                ('action_plan_id',
                                 'email_action_plans',
                                 'action_plan_id',
                                 'NO ACTION',
                                 'RESTRICT')),
 'email_unsubscribe_steps': (('action_identity',
                              'email_unsubscribe_claims',
                              'action_identity',
                              'NO ACTION',
                              'RESTRICT'),),
 'email_direct_retry_requests': (('action_id',
                                  'email_actions',
                                  'action_id',
                                  'NO ACTION',
                                  'RESTRICT'),),
 'email_feedback_requests': (('resulting_action_plan_id',
                              'email_action_plans',
                              'action_plan_id',
                              'NO ACTION',
                              'RESTRICT'),
                             ('expected_current_action_plan_id',
                              'email_action_plans',
                              'action_plan_id',
                              'NO ACTION',
                              'RESTRICT'),
                             ('classification_id',
                              'email_classifications',
                              'id',
                              'NO ACTION',
                              'RESTRICT')),
 'email_reply_dispatch_claims': (('classification_id',
                                  'email_classifications',
                                  'id',
                                  'NO ACTION',
                                  'RESTRICT'),
                                 ('action_plan_id',
                                  'email_action_plans',
                                  'action_plan_id',
                                  'NO ACTION',
                                  'RESTRICT')),
 'email_reply_receipts': (('classification_id',
                           'email_classifications',
                           'id',
                           'NO ACTION',
                           'RESTRICT'),
                          ('action_plan_id',
                           'email_action_plans',
                           'action_plan_id',
                           'NO ACTION',
                           'RESTRICT'))}


EMAIL_ROWID_TYPES = (
    "email_action_plans", "email_actions", "email_unsubscribe_cancellations",
    "email_unsubscribe_claims", "email_unsubscribe_continuations",
    "email_unsubscribe_receipts", "email_feedback_requests",
    "email_reply_dispatch_claims", "email_reply_receipts",
)
EMAIL_AUTOINCREMENT_TYPES = ("email_action_attempts", "email_unsubscribe_steps")
EMAIL_COMPOSITE_KEYS = {
    "email_unsubscribe_effects": ("action_identity", "effect_digest"),
    "email_direct_retry_requests": ("action_id", "attempt_number"),
}
POSTFIX_ACTION_TYPES = (
    "outbound_postfixes", "outbound_postfix_receipts", "native_reply_dispatches",
)
EMAIL_NUMERIC_SCOPE = (
    "record_type<>'external_action_results' "
    "and record_type not in (" + ",".join(repr(kind) for kind in POSTFIX_ACTION_TYPES) + ") "
    "and record_type<>'sent_reply_observers' "
    "and record_type not in (" + ",".join(repr(kind) for kind in EMAIL_COMPOSITE_KEYS) + ")"
)
EMAIL_PUBLIC_FIELDS = {kind: EMAIL_FIELDS[kind][0] for kind in EMAIL_ROWID_TYPES}
EMAIL_TYPES_ORDER = tuple(EMAIL_FIELDS)
EMAIL_CLAIM_FRESH_FIELDS = (
    "action_identity", "effect_digest", "action_plan_id", "action_plan_version",
    "classification_id", "account_id", "stable_message_identity", "thread_identity",
    "entry_reference", "operations_json", "owner_id", "owner_generation", "lease_token",
    "account_updated_at", "status", "phase", "audit_agent_run_id", "claimed_at", "updated_at",
)


def _encoded_value(document, path):
    tag = f"json_extract({document},'{path}.t')"
    return (f"case when json_type({document},'{path}')='object' then "
            f"case {tag} when 'blob' then unhex(json_extract({document},'{path}.v')) "
            f"when 'real' then json_extract({document},'{path}.m') "
            f"* pow(2.0,json_extract({document},'{path}.e')) "
            "when 'inf' then 9e999 when '-inf' then -9e999 end "
            f"else json_extract({document},'{path}') end")


def _encoded_value_check(document, path):
    value_type = f"json_type({document},'{path}')"
    tag = f"json_extract({document},'{path}.t')"
    obj = f"json_extract({document},'{path}')"
    blob_value = f"json_extract({document},'{path}.v')"
    blob = (f"({tag}='blob' and json_type({document},'{path}.v')='text' "
            f"and length({blob_value})%2=0 and unhex({blob_value}) is not null "
            f"and json_remove({obj},'$.t','$.v')='{{}}')")
    nonfinite = f"({tag} in ('inf','-inf') and json_remove({obj},'$.t')='{{}}')"
    mantissa = f"json_extract({document},'{path}.m')"
    exponent = f"json_extract({document},'{path}.e')"
    finite = (f"({tag}='real' and json_type({document},'{path}.e')='integer' "
              f"and {exponent} between -1074 and 971 and "
              f"((json_type({document},'{path}.m')='integer' and {mantissa} "
              "between -9007199254740991 and 9007199254740991) or "
              f"(json_type({document},'{path}.m')='real' and {mantissa}=0)) "
              f"and json_remove({obj},'$.t','$.m','$.e')='{{}}')")
    return (f"case when {value_type} in ('null','text','integer','real','true','false') then 1 "
            f"when {value_type}='object' then coalesce((json_type({document},'{path}.t')='text' "
            f"and ({blob} or {nonfinite} or {finite})),0) else 0 end")


def email_schema_parts(reply_value_check, *, payloads=None, shared_lifecycle=False):
    """Return only the finite Email additions to the existing actions declaration."""
    fields_by_kind = EMAIL_FIELDS
    if shared_lifecycle:
        from app.shared_action_definitions import DIRECT_DERIVED_FIELDS
        fields_by_kind = {**EMAIL_FIELDS, 'email_actions': tuple(
            field for field in EMAIL_FIELDS['email_actions'] if field not in DIRECT_DERIVED_FIELDS)}
    generated = []
    for field, affinity in EMAIL_TYPES.items():
        if shared_lifecycle and field == 'attempt_count':
            continue
        if field == "id":
            expr = ("case when record_type in ('email_action_attempts','email_unsubscribe_steps') "
                    "then cast(legacy_key as integer) end")
        else:
            cases = []
            for kind, pair in EMAIL_COMPOSITE_KEYS.items():
                if field in pair:
                    cases.append(f"when record_type='{kind}' then {_encoded_value('legacy_key', '$[' + str(pair.index(field)) + ']')}")
            payload_kinds = tuple(
                kind for kind, fields in fields_by_kind.items()
                if field in fields and field not in EMAIL_COMPOSITE_KEYS.get(kind, ())
            )
            if payload_kinds:
                scope = ",".join(repr(kind) for kind in payload_kinds)
                cases.append(f"when record_type in ({scope}) then " + (payloads.field_value(field, payload_kinds) if payloads else _encoded_value("payload_json", "$." + field)))
            expr = "case " + " ".join(cases) + " end"
        generated.append(f"email_{field} {affinity} generated always as ({expr}) virtual")
    public_cases = " ".join(
        f"when record_type='{kind}' then email_{field}"
        for kind, field in EMAIL_PUBLIC_FIELDS.items()
    )
    generated.append(f"email_public_key text generated always as (case {public_cases} end) virtual")
    for parent, discriminator in (("email_action_plans", "email_plan_parent_type"),
                                  ("email_actions", "email_operation_parent_type"),
                                  ("email_unsubscribe_claims", "email_claim_parent_type")):
        kinds = tuple(kind for kind, fks in EMAIL_SOURCE_FKS.items()
                      if any(fk[1] == parent for fk in fks))
        scope = ",".join(repr(kind) for kind in kinds)
        generated.append(f"{discriminator} text generated always as (case when record_type in ({scope}) then '{parent}' end) virtual")

    checks = []
    for kind, fields in fields_by_kind.items():
        stored = tuple(field for field in fields if field != "id" and field not in EMAIL_COMPOSITE_KEYS.get(kind, ()))
        paths = ",".join(repr("$." + field) for field in stored)
        values = [payloads.check(kind, field) if payloads else reply_value_check(field) for field in stored]
        values.append(payloads.shape(kind) if payloads else f"json_remove(payload_json,{paths})='{{}}'")
        values.extend(f"email_{field} is not null" for field in EMAIL_NOT_NULL[kind] if field in fields)
        values.extend(f"({expr})" for expr in EMAIL_CHECKS[kind]
                      if not (shared_lifecycle and kind == 'email_actions' and 'email_attempt_count' in expr))
        if kind in EMAIL_COMPOSITE_KEYS:
            values.append("json_type(legacy_key)='array' and json_array_length(legacy_key)=2")
            values.extend(_encoded_value_check("legacy_key", f"$[{i}]") for i in range(2))
        checks.append(f"check(record_type<>'{kind}' or ({' and '.join('(' + v + ')' for v in values)}))")

    fks = []
    seen = set()
    for rows in EMAIL_SOURCE_FKS.values():
        for field, parent, _, update, delete in rows:
            if parent == "email_classifications":
                clause = (f"foreign key(email_{field}) references email_classifications(id) "
                          f"on update {update} on delete {delete}")
            else:
                discriminator = {"email_action_plans": "email_plan_parent_type",
                                 "email_actions": "email_operation_parent_type",
                                 "email_unsubscribe_claims": "email_claim_parent_type"}[parent]
                clause = (f"foreign key({discriminator},email_{field}) references "
                          f"actions(record_type,email_public_key) on update {update} on delete {delete}")
            if clause not in seen:
                fks.append(clause)
                seen.add(clause)

    indexes = ["create unique index idx_actions_email_public_key on actions(record_type,email_public_key)"]
    for kind, definitions in EMAIL_SOURCE_INDEXES.items():
        for index, (unique, fields) in enumerate(definitions):
            columns = ",".join("email_" + field for field in fields)
            indexes.append(
                f"create {'unique ' if unique else ''}index idx_actions_email_{kind}_{index} "
                f"on actions({columns}) where record_type='{kind}'"
            )
    triggers = [
        "create trigger trg_actions_email_attempt_count_insert after insert on actions "
        "when new.record_type='email_actions' and exists(select 1 from actions where "
        "record_type='email_action_attempts' and email_action_id=new.email_action_id) "
        "and new.email_attempt_count!=(select count(*) from actions where "
        "record_type='email_action_attempts' and email_action_id=new.email_action_id) "
        "begin select raise(abort,'email_action_attempt_count_drift'); end",
        "create trigger trg_actions_email_attempt_count_update after update on actions "
        "when new.record_type='email_actions' and new.email_attempt_count!=(select count(*) "
        "from actions where record_type='email_action_attempts' and email_action_id=new.email_action_id) "
        "begin select raise(abort,'email_action_attempt_count_drift'); end",
        "create trigger trg_actions_email_retry_immutable_delete before delete on actions "
        "when old.record_type='email_direct_retry_requests' "
        "begin select raise(abort,'email_direct_retry_request_immutable'); end",
        "create trigger trg_actions_email_retry_immutable_update before update on actions "
        "when old.record_type='email_direct_retry_requests' "
        "begin select raise(abort,'email_direct_retry_request_immutable'); end",
        "create trigger trg_actions_email_retry_immutable_insert before insert on actions "
        "when new.record_type='email_direct_retry_requests' and exists(select 1 from actions "
        "where record_type='email_direct_retry_requests' and email_action_id=new.email_action_id "
        "and email_attempt_number=new.email_attempt_number) "
        "begin select raise(abort,'email_direct_retry_request_immutable'); end",
    ]
    if shared_lifecycle:
        triggers = triggers[2:]  # Count is now the attempt ledger's derived value.
        checks.append("check(record_type<>'email_actions' or (email_status='processing' or email_started_at=''))")
    return generated, checks, fks, indexes, triggers


def email_action_projection(kind, *, include_rowid=False, source_variant="actual", include_physical_id=False):
    if kind not in EMAIL_FIELDS:
        raise ValueError(f"unknown Email action kind: {kind}")
    if source_variant not in ("actual", "fresh_candidate"):
        raise ValueError("unknown Email source variant")
    if include_rowid and kind not in EMAIL_ROWID_TYPES:
        raise ValueError("source ROWID is exposed only for the nine rowid families")
    fields = EMAIL_CLAIM_FRESH_FIELDS if kind == "email_unsubscribe_claims" and source_variant == "fresh_candidate" else EMAIL_FIELDS[kind]
    columns = [f"a.email_{field} as {field}" for field in fields]
    if include_rowid:
        columns.insert(0, "cast(a.legacy_key as integer) as rowid")
    if include_physical_id:
        columns.insert(0, "a.id as physical_action_id")
    return f"select {','.join(columns)} from actions a where a.record_type='{kind}'"


EMAIL_ACTION_PROJECTIONS = {kind: email_action_projection(kind) for kind in EMAIL_FIELDS}


EMAIL_DEFAULTS = {'email_action_attempts': {},
 'email_action_plans': {'legacy_serialization_pre_v16': '0'},
 'email_actions': {'attempt_count': '0',
                   'started_at': "''",
                   'finished_at': "''",
                   'next_attempt_at': "''",
                   'provider_operation': "''",
                   'provider_target': "''",
                   'provider_result_id': "''",
                   'error': "''"},
 'email_unsubscribe_cancellations': {},
 'email_unsubscribe_claims': {'phase': "'prepared'"},
 'email_unsubscribe_continuations': {},
 'email_unsubscribe_effects': {'previous_effect_digest': "''"},
 'email_unsubscribe_receipts': {'result_text': "''",
                                'observation_digest': "''",
                                'started_at': "''",
                                'completed_at': "''",
                                'result_text_truncated': '0',
                                'result_text_digest': "''",
                                'entry_url': "''"},
 'email_unsubscribe_steps': {},
 'email_direct_retry_requests': {},
 'email_feedback_requests': {},
 'email_reply_dispatch_claims': {},
 'email_reply_receipts': {}}
