"""Fixed current Email table and associated objects."""

EMAIL_TABLE_SQL = "CREATE TABLE emails (\n    id integer primary key,\n    classification_id integer unique,\n    message_id integer not null unique,\n    account_id text not null,\n    stable_message_identity text not null unique,\n    uidvalidity integer not null check(uidvalidity > 0),\n    uid integer not null check(uid > 0),\n    class_rfc_message_id text,\n    class_thread_id text,\n    class_model_text text,\n    class_category text,\n    class_predicted_category text,\n    class_confirmed_category text,\n    class_confidence real,\n    class_margin real,\n    class_probabilities_json text,\n    class_model_id text,\n    class_config_version text,\n    class_status text,\n    class_classification_source text,\n    class_agent_result_json text,\n    class_current_action_plan_id text,\n    class_included_in_model_id text,\n    class_confirmed_at text,\n    class_created_at text,\n    class_updated_at text,\n    message_folder text not null,\n    message_rfc_message_id text not null,\n    message_in_reply_to text not null default '',\n    message_references_json text not null default '[]' check(json_valid(message_references_json)),\n    message_thread_identity text not null,\n    message_sender text not null,\n    message_recipients_json text not null check(json_valid(message_recipients_json)),\n    message_subject text not null,\n    message_normalized_text text not null,\n    message_source_reference_json text check(message_source_reference_json is null or json_valid(message_source_reference_json)),\n    message_preview text not null check(typeof(message_preview)='text' and length(cast(unhex(replace(hex(message_preview),'00','01')) as text))<=1000),\n    message_attachment_metadata_json text not null check(json_valid(message_attachment_metadata_json)),\n    message_received_at text not null,\n    message_created_at text not null,\n    message_updated_at text not null,\n    check(classification_id is null or (\n        class_model_text is not null and\n        class_confidence is not null and class_margin is not null and\n        class_probabilities_json is not null and class_model_id is not null and\n        class_config_version is not null and class_status is not null and\n        class_classification_source is not null and class_agent_result_json is not null and\n        class_confirmed_at is not null and class_created_at is not null and\n        class_updated_at is not null\n    ))\n)"
EMAIL_OBJECT_SQL = ('CREATE INDEX idx_emails_class_account_locator on '
 'emails(account_id,message_folder,uidvalidity,class_status,class_updated_at desc,uid) where '
 'classification_id is not null',
 'CREATE INDEX idx_emails_class_account_status on emails(account_id,class_status,class_updated_at desc) '
 'where classification_id is not null',
 'CREATE INDEX idx_emails_class_status on emails(class_status,class_updated_at desc,classification_id desc) '
 'where classification_id is not null',
 'CREATE INDEX idx_emails_message_account_locator on emails(account_id,message_folder,uidvalidity,uid)',
 'CREATE TRIGGER trg_emails_class_source_insert before insert on emails when new.classification_id is not '
 "null and new.class_classification_source not in ('model','user','agent') begin select raise(abort,'invalid "
 "email classification source'); end",
 'CREATE TRIGGER trg_emails_class_source_update before update of class_classification_source on emails when '
 "new.classification_id is not null and new.class_classification_source not in ('model','user','agent') "
 "begin select raise(abort,'invalid email classification source'); end",
 'CREATE TRIGGER trg_emails_class_status_insert before insert on emails when new.classification_id is not '
 "null and new.class_status not in ('pending_feedback','processed') begin select raise(abort,'invalid email "
 "classification status'); end",
 'CREATE TRIGGER trg_emails_class_status_update before update of class_status on emails when '
 "new.classification_id is not null and new.class_status not in ('pending_feedback','processed') begin "
 "select raise(abort,'invalid email classification status'); end",
 'CREATE TRIGGER trg_emails_direct_action_blocks_plan_switch before update of '
 'class_status,class_current_action_plan_id on emails when old.classification_id is not null and '
 'new.classification_id is not null and (old.class_status is not new.class_status or '
 'old.class_current_action_plan_id is not new.class_current_action_plan_id) and exists(select 1 from actions '
 "a where a.record_type='action' and a.action_classification_id=old.classification_id and "
 "a.action_status='processing' and a.action_operation in "
 "('archive','flag_important','mark_read','move','trash','label')) begin select "
 "raise(abort,'email_direct_action_in_flight'); end",
 'CREATE TRIGGER trg_emails_reply_dispatch_blocks_message_delete before delete on emails when exists(select '
 "1 from actions a where a.record_type='email_reply_dispatch_claims' and a.email_account_id=old.account_id "
 "and a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin "
 "select raise(abort,'email_reply_dispatch_in_flight'); end",
 'CREATE TRIGGER trg_emails_reply_dispatch_blocks_plan_switch before update of class_current_action_plan_id '
 'on emails when old.classification_id is not null and new.classification_id is not null and '
 'old.class_current_action_plan_id is not new.class_current_action_plan_id and exists(select 1 from actions '
 "a where a.record_type='email_reply_dispatch_claims' and a.email_classification_id=old.classification_id "
 "and a.email_status='dispatching') begin select raise(abort,'email_reply_dispatch_in_flight'); end",
 'CREATE TRIGGER trg_emails_reply_dispatch_blocks_thread_update before update of '
 'account_id,stable_message_identity,message_thread_identity on emails when (old.account_id is not '
 'new.account_id or old.stable_message_identity is not new.stable_message_identity or '
 'old.message_thread_identity is not new.message_thread_identity) and exists(select 1 from actions a where '
 "a.record_type='email_reply_dispatch_claims' and a.email_account_id=old.account_id and "
 "a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select "
 "raise(abort,'email_reply_dispatch_in_flight'); end",
 'CREATE TRIGGER trg_emails_training_inclusion_invalidate after update of '
 'class_confirmed_category,class_model_text,class_confirmed_at,class_classification_source,class_status on '
 'emails when old.classification_id is not null and old.class_included_in_model_id is not null and '
 '(old.class_confirmed_category is not new.class_confirmed_category or old.class_model_text is not '
 'new.class_model_text or old.class_confirmed_at is not new.class_confirmed_at or '
 'old.class_classification_source is not new.class_classification_source or old.class_status is not '
 'new.class_status) begin update emails set class_included_in_model_id=null where id=new.id; end',
 'CREATE TRIGGER trg_emails_unsubscribe_blocks_message_delete before delete on emails when exists(select 1 '
 "from actions a where a.record_type='email_unsubscribe_claims' and a.email_account_id=old.account_id and "
 "a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select "
 "raise(abort,'email_unsubscribe_write_in_flight'); end",
 'CREATE TRIGGER trg_emails_unsubscribe_blocks_message_update before update of '
 'account_id,stable_message_identity,message_thread_identity on emails when (old.account_id is not '
 'new.account_id or old.stable_message_identity is not new.stable_message_identity or '
 'old.message_thread_identity is not new.message_thread_identity) and exists(select 1 from actions a where '
 "a.record_type='email_unsubscribe_claims' and a.email_account_id=old.account_id and "
 "a.email_stable_message_identity=old.stable_message_identity and a.email_status='dispatching') begin select "
 "raise(abort,'email_unsubscribe_write_in_flight'); end",
 'CREATE TRIGGER trg_emails_unsubscribe_blocks_plan_switch before update of '
 'class_status,class_current_action_plan_id on emails when old.classification_id is not null and '
 'new.classification_id is not null and (old.class_status is not new.class_status or '
 'old.class_current_action_plan_id is not new.class_current_action_plan_id) and exists(select 1 from actions '
 "a where a.record_type='email_unsubscribe_claims' and a.email_classification_id=old.classification_id and "
 "a.email_status='dispatching') begin select raise(abort,'email_unsubscribe_write_in_flight'); end")

def create_current_emails(db):
    db.execute(EMAIL_TABLE_SQL)
    for sql in EMAIL_OBJECT_SQL:
        db.execute(sql)
