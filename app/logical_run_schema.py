"""Exact 19.9 logical history transfer and inverse from current owner facts."""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.configs_run_schema import _objects
from app.logical_run_definitions import SOURCE_TASKS, logical_run_objects, logical_action_objects
from app import action_storage as actions
from app.prepared_action_schema import prepared_action_objects
from app.runs11_schema_release import _target_objects as source_run_objects
from app.task_storage import validate_meeting_run_references

_ROWS = "_logical_run_rows"
_WATER = "_logical_run_water"


@lru_cache(maxsize=1)
def source_columns():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(source_run_objects()["runs"])
        return tuple(row[1] for row in db.execute("pragma table_info(runs)"))


def source_row_projection(*, owned, table="main.runs"):
    """Reconstruct raw source SQL cells; no body parsing or identity casts."""
    if not owned:
        return "select " + ",".join(source_columns()) + " from " + table
    standalone = "r.record_type='agent_runs' and r.source_type in (" + ",".join(map(repr, SOURCE_TASKS)) + ")"
    expressions = {}
    expressions["record_type"] = f"case when {standalone} then r.source_type else r.record_type end"
    expressions["source_type"] = f"case when {standalone} then null else r.source_type end"
    reparented = "r.record_type='agent_runs' and r.source_type in ('task_agent_runs','okr_review_runs')"
    expressions["task_id"] = f"case when {reparented} then null else r.task_id end"
    for source, column in (("task_agent_runs", "summary_input_id"), ("okr_review_runs", "request_id")):
        expressions[column] = (
            f"case when r.record_type='agent_runs' and r.source_type='{source}' "
            f"then (select cast(p.legacy_key as integer) from tasks p where p.id=r.task_id "
            f"and p.record_type='{SOURCE_TASKS[source]}') end"
        )
    moved = "r.record_type='agent_runs' and r.source_type in ('task_agent_runs','meeting_alignment_runs')"
    expressions["finished_at"] = f"case when {moved} then r.completed_at else r.finished_at end"
    expressions["completed_at"] = f"case when {moved} then null else r.completed_at end"
    return ("select " + ",".join(expressions.get(column, "r." + column) + " as " + column
                                for column in source_columns()) + " from " + table + " r")


def _require_source(db, *, owned):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("logical Run transition requires caller-owned FK-off transaction")
    for name in ("runs", _ROWS, _WATER):
        if db.execute("select 1 from temp.sqlite_schema where name=? collate nocase",
                      (name,)).fetchone():
            raise ValueError("logical Run staging collision")
    if _objects(db) != (logical_run_objects() if owned else source_run_objects()):
        raise ValueError("unsupported logical Run source declaration")
    action_objects = logical_action_objects if owned else prepared_action_objects
    if actions._objects(db, "actions") != action_objects(actions._reply_variant_for(db)):
        raise ValueError("unsupported logical Run Actions reference declaration")
    validate_meeting_run_references(
        db, runs_sql=source_row_projection(owned=owned),
    )
    for source, public in (("task_agent_runs", "summary_input_id"), ("okr_review_runs", "request_id")):
        task = SOURCE_TASKS[source]
        if owned:
            sql = ("select 1 from runs r left join tasks p on p.id=r.task_id "
                   f"where r.record_type='agent_runs' and r.source_type='{source}' "
                   f"and (p.id is null or p.record_type<>'{task}') limit 1")
        else:
            sql = (f"select 1 from runs r left join tasks p on p.record_type='{task}' "
                   "and p.record_type not in ('workbench_tasks','workbench_turns') "
                   f"and cast(p.legacy_key as integer)=r.{public} "
                   f"where r.record_type='{source}' and (p.id is null "
                   f"or typeof(r.{public})<>'integer' "
                   f"or r.{public} is not cast(p.legacy_key as integer)) limit 1")
        if db.execute(sql).fetchone():
            raise ValueError("logical Run parent cannot be inverted with exact SQL value/type")
    # Prepare every existing external SQL program against the prospective schema
    # before touching the source. This checks actual dependencies, not text hints.
    target = source_run_objects() if owned else logical_run_objects()
    objects = tuple(db.execute(
        "select type,name,sql from main.sqlite_schema where sql is not null "
        "and name not glob 'sqlite_*' and tbl_name<>'runs' "
        "order by case type when 'table' then 0 when 'index' then 1 when 'view' then 2 else 3 end,name"
    ))
    with closing(sqlite3.connect(":memory:")) as parser:
        parser.execute(target["runs"])
        for kind, name, sql in objects:
            parser.execute(sql)
        for kind, name, _sql in objects:
            if kind == "view":
                parser.execute(f'explain select * from "{name}" limit 0')
            elif kind == "table":
                columns = [row[1] for row in parser.execute(f'pragma table_xinfo("{name}")') if row[6] == 0]
                assignments = ",".join(f'"{column}"="{column}"' for column in columns)
                for sql in (f'insert into "{name}" default values',
                            f'update "{name}" set {assignments} where 0',
                            f'delete from "{name}" where 0'):
                    parser.execute("explain " + sql)


def _rebuild(db, *, owned):
    before = sorted(tuple(row) for row in db.execute("pragma foreign_key_check"))
    db.execute(f"create temp table {_ROWS} as " + source_row_projection(owned=not owned))
    db.execute(f"create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence")
    columns = source_columns()
    objects = logical_run_objects() if owned else source_run_objects()
    target_columns = tuple(column for column in columns if not owned or column not in ("summary_input_id", "request_id"))
    expressions = {column: column for column in target_columns}
    if owned:
        source_kinds = ",".join(map(repr, SOURCE_TASKS))
        expressions["record_type"] = f"case when record_type in ({source_kinds}) then 'agent_runs' else record_type end"
        expressions["source_type"] = f"case when record_type in ({source_kinds}) then record_type else source_type end"
        expressions["task_id"] = (
            "case " + " ".join(
                f"when record_type='{source}' then (select p.id from tasks p "
                f"where p.record_type='{SOURCE_TASKS[source]}' "
                "and p.record_type not in ('workbench_tasks','workbench_turns') "
                f"and cast(p.legacy_key as integer)={_ROWS}.{column})"
                for source, column in (("task_agent_runs", "summary_input_id"), ("okr_review_runs", "request_id"))
            ) + " else task_id end"
        )
        moved = "record_type in ('task_agent_runs','meeting_alignment_runs')"
        expressions["completed_at"] = f"case when {moved} then finished_at else completed_at end"
        expressions["finished_at"] = f"case when {moved} then null else finished_at end"
    db.execute("drop table main.runs")
    db.execute(objects["runs"])
    db.execute("insert into main.runs(" + ",".join(target_columns) + ") select "
               + ",".join(expressions.values()) + f" from temp.{_ROWS} order by id")
    for name, sql in objects.items():
        if name != "runs":
            db.execute(sql)
    source_actions = prepared_action_objects(actions._reply_variant_for(db))
    target_actions = logical_action_objects(actions._reply_variant_for(db))
    source_by_name = {obj["name"]: obj for obj in source_actions}
    target_by_name = {obj["name"]: obj for obj in target_actions}
    for name in source_by_name:
        if source_by_name[name] != target_by_name[name]:
            db.execute('drop trigger "' + name + '"')
            db.execute((target_by_name if owned else source_by_name)[name]["sql"])
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}")
    raw = ",".join(columns) + "," + ",".join("typeof(" + column + ")" for column in columns)
    current = "(" + source_row_projection(owned=owned) + ")"
    for left, right in ((current, "temp." + _ROWS), ("temp." + _ROWS, current)):
        if db.execute(f"select {raw} from {left} except select {raw} from {right} limit 1").fetchone():
            raise ValueError("logical Run transfer changed original SQL values/types")
    if sorted(tuple(row) for row in db.execute("pragma foreign_key_check")) != before:
        raise ValueError("logical Run transfer changed original FK findings")
    if tuple(db.execute("select rowid,name,seq from sqlite_sequence order by rowid")) != tuple(
        db.execute(f"select rowid,name,seq from temp.{_WATER} order by rowid")
    ):
        raise ValueError("logical Run transfer changed native sequence rows")
    db.execute(f"drop table temp.{_ROWS}")
    db.execute(f"drop table temp.{_WATER}")


def migrate_logical_runs(db):
    _require_source(db, owned=False)
    _rebuild(db, owned=True)
    return {}


def restore_logical_runs(db, metadata):
    if metadata != {}:
        raise ValueError("unsupported logical Run inverse metadata")
    _require_source(db, owned=True)
    _rebuild(db, owned=False)
