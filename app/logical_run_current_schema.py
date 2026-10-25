"""Selected common logical Run declarations; the 19.9 factory stays frozen."""
from app.configs_run_schema import _objects
from app.configs_schema_current import configs_content_schema_is_current
from app.logical_run_definitions import logical_run_objects
from app.runs11_current_schema import RETIRED_RUN11_NAMES


def current_run11_objects():
    return logical_run_objects().copy()


def create_current_run11_schema(db):
    for sql in current_run11_objects().values():
        for prefix in ("CREATE TABLE ", "CREATE UNIQUE INDEX ", "CREATE INDEX ", "CREATE TRIGGER "):
            if sql.startswith(prefix):
                db.execute(prefix + "IF NOT EXISTS " + sql[len(prefix):])
                break
        else:
            raise ValueError("unsupported common Run declaration category")


def current_run11_schema_is_current(db, *, allow_missing_objects=False):
    if any(db.execute("select 1 from main.sqlite_schema where name=? collate nocase",
                      (name,)).fetchone() for name in RETIRED_RUN11_NAMES):
        return False
    actual, expected = _objects(db), current_run11_objects()
    if actual.get("runs") != expected["runs"] or not set(actual) <= set(expected):
        return False
    if allow_missing_objects:
        for name in set(expected) - set(actual):
            if name == "runs" or db.execute(
                "select 1 from main.sqlite_schema where name=? collate nocase", (name,)
            ).fetchone():
                return False
    return all(actual.get(name, sql) == sql if allow_missing_objects else actual.get(name) == sql
               for name, sql in expected.items())


def current_configs_schema_is_current(db, **options):
    return (configs_content_schema_is_current(db, **(options | {"logical_owner": True}))
            and current_run11_schema_is_current(db))
