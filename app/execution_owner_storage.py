"""Native execution progress writes."""

def update_native_execution(db, consumer_run_id, values):
    """Update native Task Run execution progress by its Consumer Run owner."""
    allowed = {"status", "lease_owner", "lease_expires_at", "result_json", "updated_at"}
    if not values or set(values) - allowed:
        raise ValueError("unknown native execution update")
    assignments = ",".join(f"{field}=?" for field in values)
    cursor = db.execute(
        f"update run_execution_progress set {assignments} where consumer_run_id=?",
        (*values.values(), consumer_run_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("native execution progress is missing")
    return cursor.rowcount


def create_native_execution(db, *, consumer_run_id, approved_audit_run_id,
                             selection_id, status, lease_owner,
                             lease_expires_at):
    """Create native progress; caller owns the transaction and typed owners."""
    if not db.in_transaction:
        raise ValueError("native execution requires caller-owned transaction")
    clock = db.execute("select current_timestamp").fetchone()[0]
    db.execute(
        "insert into run_execution_progress(consumer_run_id,approved_audit_run_id,"
        "selection_id,status,lease_owner,lease_expires_at,result_json,created_at,updated_at) "
        "values(?,?,?,?,?,?,?, ?,?)",
        (consumer_run_id, approved_audit_run_id, selection_id, status,
         lease_owner, lease_expires_at, "{}", clock, clock),
    )
    return consumer_run_id
