# Explicit State upgrade-progress DAO plan

> Required workflow: subagent-driven-development; independent SPEC then QUALITY before accepting implementation.

**Goal:** Keep the existing updater progress record writable when the paired release removes `service_state`, without constructing application stores or guessing the schema from table existence.

**Scope:** A prerequisite only: replace the existing `ExistingSchemaUpgradeStateStore` class in `app/repository_updater.py` with the complete block below, and add `tests/test_state_upgrade_progress.py`. The generic `UpgradeStateStore` / `RepositoryUpdater` protocols remain unchanged. No entrypoint or publication selects current State in this unit. No schema/version/ledger changes, production action or outbound effect.

**Source evidence:** The existing class uses mode=rw, timeout30, three lock/busy attempts and delays0.25/0.5; getter returns str(row[0]) even for a legal BLOB. Preserve these details. The State helpers require a caller transaction; the existing immediate transaction supplies it. The two schema bindings select explicit release roles and never retry a different layout after SQL fails.

## Complete implementation

Replace only the existing class; existing Path/sqlite3/time imports and retry constants remain unchanged. Local imports avoid expanding module import dependencies for unrelated updater callers.

```python
class ExistingSchemaUpgradeStateStore:
    """Persist deploy progress using the release's explicitly selected schema.

    This DAO never constructs an application store or migrates the database.
    The paired publication binds the committed schema role; it rebinds source
    only after a successful inverse commit.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.bind_schema_source()

    def bind_schema_source(self) -> None:
        self._state_schema_current = False

    def bind_schema_current(self) -> None:
        self._state_schema_current = True

    def get_service_state(self, key: str) -> str | None:
        from app.state_definitions import state_projection

        projection = ("(" + state_projection("service_state") + ")"
                      if self._state_schema_current else "service_state")
        with sqlite3.connect(
            f"file:{self.database_path}?mode=rw", uri=True, timeout=30
        ) as db:
            row = db.execute(
                f"select value from {projection} where key=?", (key,)
            ).fetchone()
            return None if row is None else str(row[0])

    def set_service_state(self, key: str, value: str) -> None:
        from app.state_storage import state_json_field, upsert_state

        for attempt in range(UPGRADE_STATE_WRITE_RETRY_ATTEMPTS):
            try:
                with sqlite3.connect(
                    f"file:{self.database_path}?mode=rw", uri=True, timeout=30
                ) as db:
                    db.execute("begin immediate")
                    if self._state_schema_current:
                        upsert_state(
                            db, "service_state", {"key": key, "value": value},
                            {"value": state_json_field("value"),
                             "updated_at": "json_quote(current_timestamp)"},
                        )
                    else:
                        db.execute(
                            """insert into service_state (key, value, updated_at)
                               values (?, ?, current_timestamp)
                               on conflict(key) do update set
                                 value=excluded.value, updated_at=current_timestamp""",
                            (key, value),
                        )
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).casefold()
                is_lock_error = "locked" in message or "busy" in message
                if (
                    not is_lock_error
                    or attempt + 1 >= UPGRADE_STATE_WRITE_RETRY_ATTEMPTS
                ):
                    raise
                time.sleep(UPGRADE_STATE_WRITE_RETRY_DELAY_SECONDS * (attempt + 1))

```

## Complete regression module

```python
"""Explicit-schema upgrade progress without application constructors."""
from contextlib import closing
import sqlite3

import pytest

from app import repository_updater as updater
from app.state_definitions import state_projection
from app.state_schema_release import (
    SOURCE_TABLES, capture_state_source, migrate_state_schema, restore_state_schema,
)
from tests.configs_source_fixtures import capture


@pytest.fixture(params=("actual", "fresh"))
def source(tmp_path, request):
    path = tmp_path / "progress.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        for name in SOURCE_TABLES:
            spec = capture()[request.param]["tables"][name]
            db.execute(spec["sql"])
            for index in reversed(spec["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.commit()
    return path


def test_progress_explicit_rebind_and_current_fact_inverse(source):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    assert store.get_service_state("missing") is None
    store.set_service_state("upgrade", "before")
    with closing(sqlite3.connect(source)) as db:
        original_identity = db.execute(
            "select rowid from service_state where key='upgrade'"
        ).fetchone()[0]
        db.execute("begin")
        metadata = capture_state_source(db)
        migrate_state_schema(db, metadata)
        db.commit()
    # An explicit binding error must remain visible; there is no table fallback.
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.get_service_state("upgrade")
    store.bind_schema_current()
    assert store.get_service_state("upgrade") == "before"
    store.set_service_state("upgrade", "after-commit")
    store.set_service_state("new", "created-current")
    assert store.get_service_state("upgrade") == "after-commit"
    with closing(sqlite3.connect(source)) as db:
        projection = "(" + state_projection("service_state", include_rowid=True) + ")"
        assert db.execute(
            "select rowid from " + projection + " where key='upgrade'"
        ).fetchone()[0] == original_identity
        db.execute("begin")
        restore_state_schema(db, metadata)
        db.commit()
    store.bind_schema_source()
    assert store.get_service_state("upgrade") == "after-commit"
    assert store.get_service_state("new") == "created-current"
    store.set_service_state("upgrade", "rolled_back")
    assert store.get_service_state("upgrade") == "rolled_back"
    with closing(sqlite3.connect(source)) as db:
        assert not db.execute(
            "select 1 from sqlite_schema where name='state'"
        ).fetchone()
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("current", (False, True))
@pytest.mark.parametrize("problem", ("database is locked", "database is busy", "other failure"))
def test_progress_preserves_bounded_retry(source, current, problem, monkeypatch):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    if current:
        with closing(sqlite3.connect(source)) as db:
            db.execute("begin")
            metadata = capture_state_source(db)
            migrate_state_schema(db, metadata)
            db.commit()
        store.bind_schema_current()
    connect = updater.sqlite3.connect
    calls = []
    delays = []

    def injected_connect(*args, **kwargs):
        calls.append((args, kwargs))
        raise sqlite3.OperationalError(problem)

    monkeypatch.setattr(updater.sqlite3, "connect", injected_connect)
    monkeypatch.setattr(updater.time, "sleep", delays.append)
    with pytest.raises(sqlite3.OperationalError, match=problem):
        store.set_service_state("upgrade", "unwritten")
    retry = problem != "other failure"
    assert len(calls) == (3 if retry else 1)
    assert delays == ([0.25, 0.5] if retry else [])
    assert all(item[1] == {"uri": True, "timeout": 30} for item in calls)
    assert all(item[0][0].endswith("?mode=rw") for item in calls)
    monkeypatch.setattr(updater.sqlite3, "connect", connect)
    assert store.get_service_state("upgrade") is None


@pytest.mark.parametrize("current", (False, True))
def test_progress_getter_preserves_source_conversion_and_no_initializer(source, current):
    store = updater.ExistingSchemaUpgradeStateStore(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute(
            "insert into service_state(key,value,updated_at) values(?,?,?)",
            ("blob", sqlite3.Binary(b"native"), "before"),
        )
        db.commit()
        if current:
            db.execute("begin")
            metadata = capture_state_source(db)
            migrate_state_schema(db, metadata)
            db.commit()
    if current:
        store.bind_schema_current()
    with closing(sqlite3.connect(source)) as db:
        before = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
    assert store.get_service_state("blob") == str(b"native")
    assert store.get_service_state("absent") is None
    store.set_service_state("write", "no-constructor")
    with closing(sqlite3.connect(source)) as db:
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == before


def test_progress_open_missing_database_does_not_create(tmp_path):
    path = tmp_path / "does-not-exist.sqlite3"
    store = updater.ExistingSchemaUpgradeStateStore(path)
    for current in (False, True):
        if current:
            store.bind_schema_current()
        with pytest.raises(sqlite3.OperationalError):
            store.set_service_state("upgrade", "missing")
        assert not path.exists()

```

## Root preflight and remaining integration

The complete proposed class was executed in an owned temporary namespace, replacing only the test process's imported class. The test module was written to an owned /tmp TemporaryDirectory. Ruff passed; 19 synthetic cases passed in0.78s (pytest invocation0.833s), across exact frozen actual/fresh State source declarations. The directory was automatically removed and absence verified. Two earlier fixture defects were corrected before this result: capture_state_source requires an active FK-off transaction, and projections expose the source ROWID only with include_rowid=True. No runtime application/test files changed.

- [x] Write complete class and tests before application mutation.
- [x] Root extract/compile/Ruff/19-case preflight and owned temporary cleanup.
- [ ] Implement exact class and regression module, run existing updater publication tests.
- [ ] Fresh independent SPEC review, then fresh QUALITY review.
- [ ] As part of the later single atomic current-State cutover, bind initial source/current role before the first progress read/write in deploy.py, repository_updater.main and cli.py; pass the same real progress-store instance to prepare_database_schema_release and the paired release. Keep generic fake stores/protocol unchanged.
- [ ] Preserve the stable Email migration ledger as the release authority. Validate an explicit source/current version pair; never choose another storage path after a missing-table exception.
- [ ] At publish success bind current before loaded verification. A child can commit apply() and then fail its Store/Email constructor. On publish failure, rollback must bind the ledger-proven committed role BEFORE target/source validation. If inverse validation or execution fails, keep current binding so needs_manual persists. Bind source only AFTER successful inverse commit. Never unconditionally bind source in finally.
- [ ] Test actual SQL progress through precommit failure, postcommit child-constructor failure, successful inverse, refused/failed inverse retaining current progress, healthy finalize, and a later current-to-current deployment. Merely fake updater-state objects do not establish this boundary.
- [ ] Verify first cutover from updated development code via python -m app.deploy. The old production UI updater imports its old progress class before fast-forward; a new migration subprocess cannot replace that old parent class. Support for first cutover via the old UI path is not established.
- [ ] Integrate all current State callers/constructors/version readers/manifests and copied-data/genuine-predecessor cold proof before release selection. This prerequisite alone does not reduce the combined candidate's82 ordinary tables or establish67/final10+ledger.

The pending denied cleanup /private/tmp/state-upsert-impl.694bXO is unrelated and untouched.
