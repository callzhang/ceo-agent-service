"""Paired core-run schema publication; inverse retains replacement business writes."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from app.logical_run_schema_release import TARGET_STORE_VERSION, PREVIOUS_STORE_VERSIONS
from app.run_storage import AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL, RETIRED_RUN_TABLES, RUN_TYPES, capture_run_schema, restore_run_schema
from app.state_publication_sources import publication_store_version

OLD_RUN_VERSION = '2026-10-11.1'
RUN_VERSION = '2026-10-11.2'
VERSION_KEY = 'store_schema_version'


def _migrate(root: Path, path: Path) -> None:
    from app.store_schema_release import _migrate_store
    _migrate_store(root, path)


def publication_run_sources(db: sqlite3.Connection) -> tuple[str, str]:
    """Read the stopped/quiet source layout before or after this explicit release.

    Runtime readers use only runs. Deploy must also read the still-running
    pre-migration source to reach the existing quiet condition.
    """
    version = publication_store_version(db, current_version=TARGET_STORE_VERSION,
                                        previous_versions=PREVIOUS_STORE_VERSIONS)
    tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    if 'runs' in tables:
        if version == TARGET_STORE_VERSION:
            from app.logical_run_current_schema import current_run11_schema_is_current
            from app.logical_run_storage import AGENT_RUN_SQL as LOGICAL_AGENT_RUN_SQL
            if not current_run11_schema_is_current(db):
                raise ValueError('unknown logical Run publication declaration')
            return LOGICAL_AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL
        return AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL
    if {'agent_runs', 'agent_runtime_attempts'} <= tables:
        return 'select * from agent_runs', 'select * from agent_runtime_attempts'
    raise ValueError('core run source schema is unavailable for publication')


def _foreign_keys(db: sqlite3.Connection) -> tuple[tuple, ...]:
    return tuple(sorted(tuple(row) for row in db.execute('pragma foreign_key_check')))


@dataclass
class RunSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple[str, str]
    source_schema: dict
    source_fk: tuple[tuple, ...]
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path.as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute('select value from service_state where key=?',(VERSION_KEY,)).fetchone()
            tables = {item[0] for item in db.execute("select name from sqlite_master where type='table'")}
            if row != (RUN_VERSION,) or 'runs' not in tables or tables.intersection(RETIRED_RUN_TABLES):
                raise ValueError('unified core run schema readback mismatch')
            columns = {item[1] for item in db.execute('pragma table_info(runs)')}
            if {'final_result_json','result_envelope_json','tool_events_json'} & columns:
                raise ValueError('retired trajectory cache column persisted in runs')
            if db.execute("select count(*) from runs where legacy_key is null or trim(legacy_key)=''").fetchone()[0]:
                raise ValueError('unified run public identity is missing')
            if db.execute("select count(*) from runs where record_type='agent_runs' and id<>cast(legacy_key as integer)").fetchone()[0]:
                raise ValueError('logical run identity changed')
            if _foreign_keys(db) != self.source_fk:
                raise ValueError('unified run foreign-key readback mismatch')
            if db.execute('pragma quick_check').fetchone()[0] != 'ok':
                raise ValueError('unified run database integrity mismatch')

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            current = db.execute('select value,updated_at from service_state where key=?',(VERSION_KEY,)).fetchone()
            if current == self.original_version:
                return
            if current is None or current[0] != RUN_VERSION:
                raise ValueError('core run inverse source version mismatch')
            db.execute('pragma foreign_keys=off')
            if db.execute('pragma foreign_keys').fetchone()[0] != 0:
                raise ValueError('core run inverse needs private FK-off transaction')
            db.execute('begin immediate')
            try:
                restore_run_schema(db, self.source_schema)
                db.execute('update service_state set value=?,updated_at=? where key=?',(*self.original_version,VERSION_KEY))
                for table,state in self.source_schema['tables'].items():
                    actual = [dict(zip(('type','name','sql'), row, strict=True)) for row in db.execute("select type,name,sql from sqlite_master where tbl_name=? and sql is not null order by type,name",(table,))]
                    if actual != state['objects']:
                        raise ValueError(f'core inverse declaration mismatch: {table}')
                    if [r[1] for r in db.execute(f'pragma table_info("{table}")')] != state['columns']:
                        raise ValueError(f'core inverse column mismatch: {table}')
                if _foreign_keys(db) != self.source_fk or db.execute('pragma quick_check').fetchone()[0] != 'ok':
                    raise ValueError('core inverse integrity/FK readback mismatch')
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self) -> None:
        self.verify_loaded()


def prepare_run_schema_release(root: Path, path: Path, original_version: tuple[str,str]) -> RunSchemaRelease:
    root,path = root.resolve(),path.resolve()
    if original_version[0] != OLD_RUN_VERSION:
        raise ValueError('core run schema release needs a verified inverse for this transition')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        source = capture_run_schema(db)
        if not set(RUN_TYPES) <= source['tables'].keys():
            raise ValueError('original core run schema is incomplete')
        return RunSchemaRelease(root,path,original_version,source,_foreign_keys(db))
