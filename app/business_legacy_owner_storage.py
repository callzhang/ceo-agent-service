"""Current WorkTodo import-owner access."""

import sqlite3


def read_import_task(db, work_todo_id):
    row = db.execute("select import_task_id from main.business where record_type='work_todo' "
                     "and public_id=?", (work_todo_id,)).fetchone()
    return row[0] if row else None


def assign_import_task(db, work_todo_id, task_id):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("legacy owner assignment needs caller transaction")
    current = db.execute("select import_task_id from main.business where record_type='work_todo' "
                         "and public_id=?", (work_todo_id,)).fetchone()
    if current is None:
        raise ValueError("missing WorkTodo import owner")
    if type(task_id) is not int or db.execute(
            "select 1 from main.business where record_type='task' and public_id=?", (task_id,)
    ).fetchone() is None:
        raise ValueError("missing formal Task import target")
    if current[0] == task_id:
        return "skipped"
    if current[0] is not None:
        raise ValueError("conflicting WorkTodo import target")
    result = db.execute("update main.business set import_task_id=? where record_type='work_todo' "
                        "and public_id=? and import_task_id is null", (task_id, work_todo_id))
    if result.rowcount != 1:
        raise ValueError("conflicting WorkTodo import target")
    return "created"
