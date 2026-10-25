"""Exact Reply Attempt references to immutable provider result receipts."""

DDL = (
    "create table reply_attempt_receipt_refs ("
    "attempt_id integer primary key references actions(id), "
    "receipt_id integer not null references action_receipts(id))",
    "create index idx_reply_attempt_receipt_refs_receipt on reply_attempt_receipt_refs(receipt_id)",
    "create trigger trg_reply_attempt_receipt_ref_insert before insert on reply_attempt_receipt_refs "
    "when not exists(select 1 from actions where id=new.attempt_id and record_type='reply_attempts') "
    "or not exists(select 1 from action_receipts where id=new.receipt_id "
    "and provider_result_json is not null) begin "
    "select raise(abort,'Reply Attempt receipt reference owner is invalid'); end",
    "create trigger trg_reply_attempt_receipt_ref_update before update on reply_attempt_receipt_refs "
    "begin select raise(abort,'Reply Attempt receipt references are immutable'); end",
)


def add_reference(db, *, attempt_id: int, receipt_id: int, expected_body: str) -> None:
    owner = db.execute(
        "select id from actions where record_type='reply_attempts' "
        "and cast(legacy_key as integer)=?", (attempt_id,),
    ).fetchone()
    if owner is None:
        raise ValueError("Reply Attempt public identity has no Action owner")
    row = db.execute(
        "select provider_result_json from action_receipts where id=?", (receipt_id,)
    ).fetchone()
    if row is None or row[0] != expected_body:
        raise ValueError("Reply Attempt receipt body does not match the recorded result")
    db.execute(
        "insert into reply_attempt_receipt_refs(attempt_id,receipt_id) values(?,?)",
        (int(owner[0]), receipt_id),
    )


def delete_reference(db, *, attempt_id: int) -> None:
    owner = db.execute(
        "select id from actions where record_type='reply_attempts' "
        "and cast(legacy_key as integer)=?", (attempt_id,),
    ).fetchone()
    if owner is None:
        raise ValueError("Reply Attempt public identity has no Action owner")
    db.execute("delete from reply_attempt_receipt_refs where attempt_id=?", (int(owner[0]),))
