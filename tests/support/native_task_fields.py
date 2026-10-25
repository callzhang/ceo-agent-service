"""Edit native Task rows the way fixtures used to edit legacy Task columns."""

_TASK_KINDS = {
    "reply_tasks": "reply",
    "work_summary_inputs": "work_summary",
    "meeting_alignment_jobs": "meeting_alignment",
    "okr_review_requests": "okr_review",
}
_TASK_COLUMNS = frozenset({
    "status", "error", "attempts", "available_at", "locked_at", "updated_at",
    "execution_generation", "claimed_input_version", "input_version",
})
_INPUT_TABLES = {"reply_tasks": "task_inputs", "work_summary_inputs": "work_summary_inputs"}


def set_native_task_fields(db, kind, task_id, values):
    """Set Task columns on `tasks` and the rest on the owner that stores them.

    `task_id` is the native `tasks.id`. Reply input fields go to `task_inputs`,
    work-summary input fields to `work_summary_inputs`.
    """
    task_values = {k: v for k, v in values.items() if k in _TASK_COLUMNS}
    other = {k: v for k, v in values.items() if k not in _TASK_COLUMNS}
    assert db.execute(
        "select 1 from tasks where id=? and kind=?", (task_id, _TASK_KINDS[kind]),
    ).fetchone(), f"no {kind} Task {task_id}"
    if task_values:
        db.execute(
            "update tasks set " + ",".join(f"{k}=?" for k in task_values) + " where id=?",
            (*task_values.values(), task_id),
        )
    if other:
        table = _INPUT_TABLES[kind]
        assert db.execute(
            f"update {table} set " + ",".join(f"{k}=?" for k in other)
            + " where task_id=?", (*other.values(), task_id),
        ).rowcount >= 1
