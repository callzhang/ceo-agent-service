import hashlib
import sqlite3
from pathlib import Path

import pytest

from app import task_artifacts
from app.agent_cli import build_role_server
from app.current_schema_manifest import create_current_email_migration_ledger, schema_is_current
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.task8_storage import register_task8_functions
from app.wechat import service
from app.wechat_voice_transcript_schema_release import (
    SOURCE_STORE_VERSION,
    TARGET_STORE_VERSION,
    prepare_wechat_voice_transcript_release,
    source_objects,
)
from tests.test_agent_cli_wechat_read import VoiceReader, _bound_task, _silk  # noqa: F401


@pytest.fixture(autouse=True)
def _task_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    monkeypatch.setattr("app.agent_cli.migrate_legacy_task_artifacts", lambda _task_id: None)


def _tool(role, db_path, task_id, name):
    return build_role_server(
        role, task_id=task_id, db_path=db_path, execution_generation="generation-one",
    )._tool_manager.get_tool(name).fn


def _digest(task_id, message_id):
    saved = (task_artifacts.TASK_ARTIFACT_ROOT / str(task_id) / "generation-one"
             / f"wechat-voice-{message_id}.wav")
    return hashlib.sha256(saved.read_bytes()).hexdigest()


def test_a_transcribed_voice_is_returned_as_text_and_its_audio_is_not_saved_again(
    tmp_path, monkeypatch,
):
    db_path, task_id = _bound_task(tmp_path)
    reader = VoiceReader(_silk(tmp_path))
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)
    save = _tool("consumer", db_path, task_id, "save_message_audio")

    first = save("m7")
    assert first["name"] == "wechat-voice-m7.wav" and "transcript" not in first
    result = _tool("consumer", db_path, task_id, "save_voice_transcript")("m7", " 测试测试 ")
    assert result == {"message_id": "m7", "saved": True}
    saved = task_artifacts.TASK_ARTIFACT_ROOT / str(task_id) / "generation-one" / first["name"]
    saved.unlink()

    again = save("m7")
    assert again["transcript"] == "测试测试" and "name" not in again
    assert not saved.exists()
    # Audit reads the same cache the Consumer wrote.
    assert _tool("audit", db_path, task_id, "save_message_audio")("m7")["transcript"] == "测试测试"


def test_changed_audio_is_not_served_a_stale_transcript(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path)
    store = AutoReplyStore(db_path)
    store.save_wechat_voice_transcript(
        account_id="acct-1", conversation_id="u9", message_id="m7",
        audio_sha256="0" * 64, transcript="old words",
    )
    monkeypatch.setattr(service, "build_reader", lambda **_kw: VoiceReader(_silk(tmp_path)))

    result = _tool("consumer", db_path, task_id, "save_message_audio")("m7")

    assert result["name"] == "wechat-voice-m7.wav" and "transcript" not in result


@pytest.mark.parametrize("transcript", ["", "   \n", None])
def test_an_empty_transcript_is_never_cached(tmp_path, monkeypatch, transcript):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: VoiceReader(_silk(tmp_path)))
    _tool("consumer", db_path, task_id, "save_message_audio")("m7")

    with pytest.raises(ValueError, match="must not be empty"):
        _tool("consumer", db_path, task_id, "save_voice_transcript")("m7", transcript)

    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from wechat_voice_transcripts").fetchone()[0] == 0


def test_unreadable_audio_is_never_cached_and_never_answered_from_the_cache(
    tmp_path, monkeypatch,
):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: VoiceReader(b"not a silk voice"))

    with pytest.raises(ValueError, match="could not read the voice message"):
        _tool("consumer", db_path, task_id, "save_message_audio")("m7")
    with pytest.raises(ValueError, match="save the voice with save_message_audio first"):
        _tool("consumer", db_path, task_id, "save_voice_transcript")("m7", "words")


def test_the_transcript_tool_is_the_consumers_and_bound_to_the_tasks_own_conversation(
    tmp_path, monkeypatch,
):
    db_path, task_id = _bound_task(tmp_path)
    audit = build_role_server(
        "audit", task_id=task_id, db_path=db_path, execution_generation="generation-one",
    )
    assert audit._tool_manager.get_tool("save_voice_transcript") is None
    with pytest.raises(TypeError):
        _tool("consumer", db_path, task_id, "save_voice_transcript")(
            "m7", "words", conversation_id="someone-else")

    (tmp_path / "email").mkdir()
    email_path, email_task = _bound_task(tmp_path / "email", channel="email")
    with pytest.raises(ValueError, match="not a WeChat conversation"):
        _tool("consumer", email_path, email_task, "save_voice_transcript")("m7", "words")


def test_the_cache_is_keyed_by_account_conversation_and_message(tmp_path):
    store = AutoReplyStore(tmp_path / "s.sqlite3")
    digest = "a" * 64
    store.save_wechat_voice_transcript(
        account_id="acct-1", conversation_id="u9", message_id="m7",
        audio_sha256=digest, transcript="one")
    key = dict(account_id="acct-1", conversation_id="u9", message_id="m7", audio_sha256=digest)
    assert store.wechat_voice_transcript(**key) == "one"
    for other in ({"account_id": "acct-2"}, {"conversation_id": "u8"}, {"message_id": "m8"},
                  {"audio_sha256": "b" * 64}):
        assert store.wechat_voice_transcript(**(key | other)) is None
    store.save_wechat_voice_transcript(
        account_id="acct-1", conversation_id="u9", message_id="m7",
        audio_sha256="b" * 64, transcript="two")
    assert store.wechat_voice_transcript(**key) is None
    assert store.wechat_voice_transcript(**(key | {"audio_sha256": "b" * 64})) == "two"


def _source_database(path):
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        db.execute("drop table wechat_voice_transcripts")
        if not db.execute("select 1 from sqlite_schema where name='email_schema_migrations'").fetchone():
            create_current_email_migration_ledger(db)
        db.execute("insert or ignore into email_schema_migrations(version,applied_at) values(54,'source-clock')")
        update_state_rows(
            db, "service_state", {"value": SOURCE_STORE_VERSION, "updated_at": "source-clock"},
            where_sql="key=?", parameters=("store_schema_version",))


def _marker(path):
    with sqlite3.connect(path) as db:
        return db.execute(
            f"select value,updated_at from ({state_projection('service_state')}) "
            "where key='store_schema_version'").fetchone()


def test_release_adds_only_the_table_and_the_inverse_restores_the_exact_source(tmp_path):
    path = tmp_path / "release.sqlite3"
    _source_database(path)
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        before = tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                  "where name not glob 'sqlite_*' order by type,name"))
        rows = db.execute("select count(*) from state").fetchone()[0]
    assert before == source_objects()
    source_marker = _marker(path)

    release = prepare_wechat_voice_transcript_release(path)
    release.publish()
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        assert schema_is_current(db, email_ledger=True)
        assert _marker(path)[0] == TARGET_STORE_VERSION
        assert db.execute("select count(*) from state").fetchone()[0] == rows
        assert db.execute("select count(*) from wechat_voice_transcripts").fetchone()[0] == 0
        db.execute("insert into wechat_voice_transcripts"
                   "(account_id,conversation_id,message_id,audio_sha256,transcript) "
                   "values('a','c','m',?,'text')", ("0" * 64,))
    release.finalize()

    release.rollback()
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        assert tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                "where name not glob 'sqlite_*' order by type,name")) == before
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
    assert _marker(path) == source_marker


def test_the_table_rejects_empty_text_and_malformed_digests(tmp_path):
    store = AutoReplyStore(tmp_path / "s.sqlite3")
    with sqlite3.connect(store.path) as db:
        for digest, text in (("short", "words"), ("a" * 64, "   ")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("insert into wechat_voice_transcripts"
                           "(account_id,conversation_id,message_id,audio_sha256,transcript) "
                           "values('a','c','m',?,?)", (digest, text))


def test_dispatch_selects_this_release_for_the_previous_store_and_nothing_for_current(tmp_path):
    from app.current_publication_sources import _version
    from app.store_schema_release import prepare_database_schema_release

    path = tmp_path / "dispatch.sqlite3"
    _source_database(path)
    with sqlite3.connect(path) as db:
        assert _version(db) == SOURCE_STORE_VERSION
    root = Path(__file__).resolve().parents[1]
    release = prepare_database_schema_release(root, path)
    assert type(release).__name__ == "WechatVoiceTranscriptRelease"
    release.publish()
    release.finalize()
    assert prepare_database_schema_release(root, path) is None
    with sqlite3.connect(path) as db:
        assert _version(db) == TARGET_STORE_VERSION
