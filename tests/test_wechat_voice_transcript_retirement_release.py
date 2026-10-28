import sqlite3
from contextlib import closing
from pathlib import Path

from app.current_schema_manifest import schema_is_current, schema_objects
from app.store_schema_release import prepare_database_schema_release
from app.task8_storage import register_task8_functions
from app.wechat_voice_transcript_retirement_release import (
    SOURCE_STORE_VERSION,
    TABLE_DDL,
    TARGET_STORE_VERSION,
    prepare_wechat_voice_transcript_retirement_release,
)
from tests.support.store28_4_source import create_store28_4_source

ROOT = Path(__file__).resolve().parents[1]


def _source(path, rows=()):
    with closing(sqlite3.connect(path)) as db:
        create_store28_4_source(db)
        db.executemany("insert into wechat_voice_transcripts"
                       "(account_id,conversation_id,message_id,audio_sha256,transcript,created_at) "
                       "values(?,?,?,?,?,?)", rows)
        db.commit()


def _state(path):
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        marker = db.execute("select payload_json from state where record_type='service_state'").fetchall()
        return schema_objects(db), marker


def test_release_drops_only_the_cache_table_and_the_inverse_restores_it_with_its_rows(tmp_path):
    path = tmp_path / "service.sqlite3"
    rows = (("acct", "u9", "m1", "a" * 64, "你好", "2026-10-28 10:00:00"),
            ("acct", "u9", "m2", "b" * 64, "再见", "2026-10-28 10:01:00"))
    _source(path, rows)
    before = _state(path)
    release = prepare_wechat_voice_transcript_retirement_release(path)
    release.publish()
    release.verify_loaded()
    with closing(sqlite3.connect(path)) as db:
        register_task8_functions(db)
        assert schema_is_current(db)
        assert db.execute("select 1 from sqlite_schema where name='wechat_voice_transcripts'").fetchone() is None
    objects, _ = _state(path)
    assert [o[1] for o in before[0] if o not in objects] == ["wechat_voice_transcripts"]
    release.rollback()
    assert _state(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.execute("select account_id,conversation_id,message_id,audio_sha256,transcript,created_at "
                                "from wechat_voice_transcripts order by message_id")) == rows


def test_changed_rows_after_preparation_are_refused_without_mutation(tmp_path):
    path = tmp_path / "service.sqlite3"
    _source(path)
    release = prepare_wechat_voice_transcript_retirement_release(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("insert into wechat_voice_transcripts(account_id,conversation_id,message_id,audio_sha256,transcript) "
                   "values('a','c','m','" + "c" * 64 + "','late')")
        db.commit()
    before = _state(path)
    try:
        release.publish()
    except ValueError as exc:
        assert "rows changed" in str(exc)
    else:
        raise AssertionError("a row written after preparation must stop the release")
    assert _state(path) == before


def test_dispatch_selects_this_release_for_store_284_and_nothing_for_the_current_store(tmp_path):
    path = tmp_path / "service.sqlite3"
    _source(path)
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == "WechatVoiceTranscriptRetirementRelease"
    assert (SOURCE_STORE_VERSION, TARGET_STORE_VERSION) == ("2026-10-28.4", "2026-10-28.5")
    release.publish()
    release.finalize()
    assert prepare_database_schema_release(ROOT, path) is None


def test_the_frozen_table_matches_what_store_284_declared():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(TABLE_DDL)
        sql = db.execute("select sql from sqlite_schema where name='wechat_voice_transcripts'").fetchone()[0]
    assert sql.lower() == TABLE_DDL
