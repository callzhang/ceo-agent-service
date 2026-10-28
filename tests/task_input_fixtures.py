"""Construct a current claimed-input snapshot in native test fixtures."""

def bind_claimed_input(db, *, task_id, input_id, claimed_input_version):
    cursor = db.execute(
        "update tasks set claimed_input_id=?,claimed_input_version=? where id=? "
        "and exists(select 1 from task_inputs where id=? and task_id=tasks.id) returning *",
        (input_id, claimed_input_version, task_id, input_id),
    )
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row, strict=True)) if row is not None else None
