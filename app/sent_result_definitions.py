"""Sent receipt ownership uses only its existing external action key."""

from app.storage_payload import ACTION_RECORD_TYPES


def sent_result_schema_parts(payloads):
    sent, receipt = (ACTION_RECORD_TYPES.code(kind) for kind in ('sent_replies', 'external_action_results'))
    path = repr(payloads.path('sent_replies', 'send_result_json'))
    generated = (
        f'sent_result_owner_key text generated always as (case when _kind={sent} and send_result_json is null then external_action_key end) virtual',
        f'sent_result_receipt_key text generated always as (case when _kind={receipt} then legacy_key end) virtual',
    )
    checks = (
        'foreign key(sent_result_owner_key) references actions(sent_result_receipt_key) deferrable initially deferred',
    )
    # SQLite requires a nonpartial unique parent index for a foreign key. This
    # single derived key keeps unrelated entries NULL instead of indexing bodies.
    indexes = (
        'create unique index idx_actions_sent_result_receipt on actions(sent_result_receipt_key)',
        'create index idx_actions_sent_result_owner on actions(sent_result_owner_key) where sent_result_owner_key is not null',
    )
    equal_owner = ("exists(select 1 from actions r where r.record_type='external_action_results' "
                   "and r.legacy_key=new.external_action_key "
                   "and typeof(r.provider_result_json)=typeof(new.send_result_json) "
                   "and r.provider_result_json is new.send_result_json)")
    triggers = tuple(
        f'create trigger trg_actions_sent_result_{suffix} after {event} on actions '
        f"when new._kind={sent} and new.external_action_key<>'' and new.send_result_json is not null and {equal_owner} "
        f'begin update actions set payload_json=jsonb_set(payload_json,{path},null) where id=new.id; end'
        for suffix, event in (('insert', 'insert'), ('update', 'update of payload_json'))
    )
    triggers += (
        f'create trigger trg_actions_sent_result_receipt_insert after insert on actions when new._kind={receipt} '
        f'begin update actions set payload_json=jsonb_set(payload_json,{path},null) '
        "where record_type='sent_replies' and external_action_key<>'' and external_action_key=new.legacy_key "
        'and send_result_json is not null and typeof(send_result_json)=typeof(new.provider_result_json) '
        'and send_result_json is new.provider_result_json; end',
    )
    # A key-only edit had no effect on the source row's private result body.
    # Restore that value before the new key can change its historical meaning.
    # The deferred FK also rejects blank-key NULL inserts at commit, while
    # permitting this key-only edit to restore its body within the statement.
    triggers += (
        f'create trigger trg_actions_sent_result_key_update after update of payload_json on actions '
        f'when old._kind={sent} and old.send_result_json is null and new.send_result_json is null '
        'and new.external_action_key is not old.external_action_key '
        f'begin update actions set payload_json=jsonb_set(payload_json,{path},'
        "(select r.provider_result_json from actions r where r.record_type='external_action_results' and r.legacy_key=old.external_action_key)) "
        'where id=new.id; end',
    )
    # AFTER is essential: Sent's exact-equality trigger must see the changed (or
    # deleted) owner, otherwise it would discard the old body immediately again.
    # A repair that explicitly updates Sent afterwards adopts its new receipt.
    for suffix, event, changed in (
        ('update', 'update of payload_json,legacy_key',
         ' and (new.provider_result_json is not old.provider_result_json or new.legacy_key is not old.legacy_key)'),
        ('delete', 'delete', ''),
    ):
        triggers += (
            f'create trigger trg_actions_sent_result_receipt_{suffix} after {event} on actions when old._kind={receipt}{changed} '
            f'begin update actions set payload_json=jsonb_set(payload_json,{path},old.provider_result_json) '
            "where record_type='sent_replies' and external_action_key<>'' and external_action_key=old.legacy_key "
            'and send_result_json is null; end',
        )
    return generated, checks, indexes, triggers
