"""The three existing Postfix child facts, owned by their prepared message."""

PARENT = "outbound_postfixes"
CHILDREN = ("outbound_postfix_receipts", "native_reply_dispatches")
SLOTS = {
    "outbound_postfix_receipts": {
        "provider_result_json": "postfix_receipt_provider_result_json",
        "created_at": "postfix_receipt_created_at",
    },
    "native_reply_dispatches": {"created_at": "postfix_dispatch_created_at"},
}
COLUMNS = tuple(column for fields in SLOTS.values() for column in fields.values())


def prepared_schema_parts():
    # The source payload CHECKs admit only JSON text in these fields. SQL NULL
    # records absence; literal provider JSON "null" and "{}" remain text.
    columns = tuple(f"{name} text" for name in COLUMNS)
    checks = (
        "check(record_type='outbound_postfixes' or ("
        + " and ".join(f"{name} is null" for name in COLUMNS) + "))",
        "check((postfix_receipt_provider_result_json is null)="
        "(postfix_receipt_created_at is null))",
        *(f"check(typeof({name}) in ('null','text'))" for name in COLUMNS),
    )
    return columns, checks


def prepared_child_projection(kind):
    fields = SLOTS[kind]
    return (
        "select channel,delivery_key,"
        + ",".join(f"{column} as {field}" for field, column in fields.items())
        + " from actions where record_type='outbound_postfixes' and "
        + fields["created_at"] + " is not null"
    )
