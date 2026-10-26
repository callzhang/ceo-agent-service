"""Write and read the native feedback_processing_* tables the way fixtures used the Task12 writers.

Items are Tasks (`tasks.kind='feedback_processing'`) with an owner row in feedback_processing_items;
their status lives on the Task. A round needs its item, so inserting a round creates the item when missing.
"""
from types import SimpleNamespace

_TABLES = {"feedback_processing_rounds", "feedback_processing_items", "feedback_processing_batches"}
_ITEM_TASK_COLUMNS = {"status", "error", "attempts", "available_at", "locked_at", "updated_at", "created_at"}


def task12_projection(kind, include_rowid=False):
    assert kind in _TABLES, kind
    if kind == "feedback_processing_items":
        columns = ("i.*, i.task_id as id, t.status as status, t.error as error, "
                   "t.attempts as attempts, t.available_at as available_at, "
                   "t.created_at as created_at, t.updated_at as updated_at")
        if include_rowid:
            columns = "i.task_id as rowid, " + columns
        return f"select {columns} from feedback_processing_items i join tasks t on t.id=i.task_id"
    return f"select rowid as rowid, * from {kind}" if include_rowid else f"select * from {kind}"


def _defaults(db, table):
    values = {}
    for _cid, name, kind, notnull, default, pk in db.execute(f"pragma table_info({table})"):
        if pk or not notnull or default is not None:
            continue
        if name.endswith("_json"):
            values[name] = "{}"
        elif kind.upper().startswith("INT"):
            values[name] = 0
        else:
            values[name] = ""
    return values


def _insert_row(db, table, row):
    cursor = db.execute(
        f"insert into {table}({','.join(row)}) values({','.join('?' for _ in row)})", tuple(row.values()),
    )
    return SimpleNamespace(lastrowid=cursor.lastrowid, rowcount=cursor.rowcount)


def _insert_item(db, values):
    from app import task_entity_storage

    values = dict(values)
    task_values = {name: values.pop(name) for name in list(values) if name in _ITEM_TASK_COLUMNS}
    created = db.execute(
        "insert into tasks(kind,status,error,parameters_json,created_at,updated_at) "
        "values('feedback_processing',?,?,?,current_timestamp,current_timestamp)",
        (task_values.get("status", "pending"), task_values.get("error", ""),
         task_entity_storage._parameters_blob(db, {})),
    )
    row = {**_defaults(db, "feedback_processing_items"), **values, "task_id": created.lastrowid}
    _insert_row(db, "feedback_processing_items", row)
    return SimpleNamespace(lastrowid=created.lastrowid, rowcount=1)


def insert_task12(db, table, values):
    if table == "feedback_processing_items":
        return _insert_item(db, values)
    if table == "feedback_processing_rounds" and not db.execute(
        "select 1 from feedback_processing_items where feedback_key=?", (values["feedback_key"],),
    ).fetchone():
        _insert_item(db, {"feedback_key": values["feedback_key"], "batch_id": values.get("batch_id", "")})
    return _insert_row(db, table, {**_defaults(db, table), **values})


def update_task12(db, table, row_id, values):
    if table == "feedback_processing_items":
        task_values = {name: value for name, value in values.items() if name in _ITEM_TASK_COLUMNS}
        item_values = {name: value for name, value in values.items() if name not in _ITEM_TASK_COLUMNS}
        cursor = None
        if task_values:
            cursor = db.execute(
                f"update tasks set {','.join(f'{name}=?' for name in task_values)} where id=?",
                (*task_values.values(), row_id),
            )
        if item_values:
            cursor = db.execute(
                f"update feedback_processing_items set {','.join(f'{name}=?' for name in item_values)} where task_id=?",
                (*item_values.values(), row_id),
            )
        return cursor
    return db.execute(
        f"update {table} set {','.join(f'{name}=?' for name in values)} where rowid=?",
        (*values.values(), row_id),
    )


def delete_task12(db, table, row_id):
    return db.execute(f"delete from {table} where rowid=?", (row_id,))
