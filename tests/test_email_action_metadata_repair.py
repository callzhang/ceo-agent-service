"""Keep original Email metadata refusal outside the Store's repair permission."""

from contextlib import closing
from pathlib import Path
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from app.action_email_storage import SOURCE_TABLES
from app.action_storage import REPLY_VARIANTS, _current_objects
from app.current_database_schema import FINAL_ACTION_OPTIONS
from app.email_store import EmailStore
from app.store import AutoReplyStore, _INITIALIZED_STORE_PATHS
from app.store_schema_release import prepare_database_schema_release

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "0441cf15fafe800f9ca4d67f667e87062ca62109"


def _email_objects(variant):
    options = dict(
        include_reply=True,
        reply_variant=variant,
        physical_provenance=True,
        compact_payload=True,
    )
    old = {
        o["name"] for o in _current_objects(**options)
    }
    return {
        o["name"]: o
        for o in _current_objects(include_email=True, **options)
        if o["name"] not in old
    }


EMAIL_OBJECTS = _email_objects(REPLY_VARIANTS[0])


def _copy(source, target):
    with closing(sqlite3.connect(source)) as a, closing(sqlite3.connect(target)) as b:
        a.backup(b)


def _old(old_root, code, *args):
    result = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        cwd=old_root,
        env={**os.environ, "PYTHONPATH": str(old_root)},
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    root = tmp_path_factory.mktemp("metadata-source")
    old = root / "genuine-0441"
    old.mkdir()
    archive = subprocess.check_output(["git", "archive", SOURCE_REF, "app"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    source = root / "source.db"
    metadata = _old(
        old,
        """
import sys,json
from pathlib import Path
from app.store import AutoReplyStore,STORE_SCHEMA_VERSION
from app.email_store import EmailStore,EMAIL_SCHEMA_VERSION,_REQUIRED_INDEXES,_REQUIRED_TRIGGER_TABLES
p=Path(sys.argv[1]);AutoReplyStore(p);EmailStore(p)
families=set(sys.argv[2].split(','))
print(json.dumps({'versions':[STORE_SCHEMA_VERSION,EMAIL_SCHEMA_VERSION],
'indexes':{n:[t,list(c)] for n,(t,c) in _REQUIRED_INDEXES.items() if t in families},
 'triggers':{n:t for n,t in _REQUIRED_TRIGGER_TABLES.items() if t in families}}))
""",
        source,
        ",".join(SOURCE_TABLES),
    )
    assert metadata["versions"] == ["2026-10-13.5", 51]
    assert len(metadata["indexes"]) == len(metadata["triggers"]) == 5
    target = root / "target.db"
    _copy(source, target)
    release = prepare_database_schema_release(ROOT, target)
    release.publish()  # Actual child and both default cold constructors.
    return old, source, target, metadata


def _fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        facts = tuple(sorted(db.iterdump()))
        journal = db.execute("pragma journal_mode").fetchone()[0]
    return facts, journal, path.read_bytes()


def test_finite_delta_has_identical_complete_metadata_in_both_variants():
    assert _email_objects(REPLY_VARIANTS[0]) == _email_objects(REPLY_VARIANTS[1])
    assert len(EMAIL_OBJECTS) == 32
    assert sum(o["type"] == "index" for o in EMAIL_OBJECTS.values()) == 25
    assert sum(o["type"] == "trigger" for o in EMAIL_OBJECTS.values()) == 7
    assert (
        "actions" not in EMAIL_OBJECTS and "idx_actions_public_id" not in EMAIL_OBJECTS
    )


def test_genuine0441_required_independent_indexes_and_owned_triggers_refuse(
    pair, tmp_path
):
    old, source, _, metadata = pair
    for kind, group in (("index", "indexes"), ("trigger", "triggers")):
        for name in metadata[group]:
            for fast in (False, True):
                path = tmp_path / f"{name}-{fast}.db"
                _copy(source, path)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(f'drop {kind} "{name}"')
                    assert (
                        db.execute("pragma journal_mode=delete").fetchone()[0]
                        == "delete"
                    )
                before = _fingerprint(path)
                outcome = _old(
                    old,
                    """
import sys,json
from pathlib import Path
from app.email_store import EmailStore
try:EmailStore(Path(sys.argv[1]),validate_rows=sys.argv[2]=='True');r={'accepted':True}
except Exception as e:r={'error':type(e).__name__,'message':str(e)}
print(json.dumps(r))
""",
                    path,
                    not fast,
                )
                assert outcome["error"] == "EmailPersistenceCorruption", outcome
                assert name in outcome["message"]
                assert _fingerprint(path) == before


@pytest.mark.parametrize("name", sorted(EMAIL_OBJECTS))
def test_email_auxiliary_missing_never_becomes_store_repair(pair, tmp_path, name):
    _, _, target, _ = pair
    obj = EMAIL_OBJECTS[name]
    for cached in (False, True):
        for fast in (False, True):
            for email_first in (False, True):
                path = tmp_path / f"{cached}-{fast}-{email_first}.db"
                _copy(target, path)
                if cached:
                    AutoReplyStore(path)
                else:
                    _INITIALIZED_STORE_PATHS.discard(path.resolve())
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(f'drop {obj["type"]} "{name}"')
                    assert (
                        db.execute("pragma journal_mode=delete").fetchone()[0]
                        == "delete"
                    )
                before = _fingerprint(path)
                constructors = (
                    (EmailStore, AutoReplyStore)
                    if email_first
                    else (AutoReplyStore, EmailStore)
                )
                for constructor in constructors:
                    with pytest.raises(
                        sqlite3.IntegrityError,
                        match="^malformed current provider/config declaration$",
                    ) as error:
                        if constructor is EmailStore:
                            constructor(path, validate_rows=not fast)
                        else:
                            constructor(path)
                    assert type(error.value) is sqlite3.IntegrityError
                    assert _fingerprint(path) == before


@pytest.mark.parametrize(
    "damage", ["index_sql", "index_owner", "index_type", "trigger_sql"]
)
def test_email_auxiliary_type_owner_and_sql_are_compared_before_store(
    pair, tmp_path, damage
):
    _, _, target, _ = pair
    path = tmp_path / "malformed.db"
    _copy(target, path)
    index = "idx_actions_email_email_actions_1"
    trigger = "trg_actions_email_attempt_count_update"
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "trigger_sql":
            db.execute(f'drop trigger "{trigger}"')
            db.execute(
                f'create trigger "{trigger}" before update on actions begin select 1; end'
            )
        else:
            db.execute(f'drop index "{index}"')
            if damage == "index_owner":
                db.execute(f'create index "{index}" on emails(id)')
            elif damage == "index_type":
                db.execute(
                    f'create trigger "{index}" before update on actions begin select 1; end'
                )
            else:
                db.execute(
                    f"create index \"{index}\" on actions(email_updated_at) where record_type='email_actions'"
                )
        assert db.execute("pragma journal_mode=delete").fetchone()[0] == "delete"
    before = _fingerprint(path)
    for constructor in (EmailStore, AutoReplyStore):
        with pytest.raises(
            sqlite3.IntegrityError,
            match="^malformed current provider/config declaration$",
        ) as error:
            constructor(path)
        assert type(error.value) is sqlite3.IntegrityError
        assert _fingerprint(path) == before


def test_shared_store_public_index_still_uses_existing_repair(pair, tmp_path):
    from app.action_storage import action_schema_is_current

    _, _, target, _ = pair
    path = tmp_path / "shared-index.db"
    _copy(target, path)
    AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db, db:
        markers = tuple(db.execute("select * from email_schema_migrations"))
        db.execute("drop index idx_actions_public_id")
    AutoReplyStore(path)
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert action_schema_is_current(
            db, reply_variant="fresh_memory_fk", **FINAL_ACTION_OPTIONS
        )
        assert tuple(db.execute("select * from email_schema_migrations")) == markers
