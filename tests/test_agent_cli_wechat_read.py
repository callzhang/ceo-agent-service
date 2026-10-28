"""A WeChat task reads only its own conversation, through the standard agent tools."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import math
import sqlite3
import struct
import wave

import pilk
import pytest

from app import task_artifacts
from app.agent_cli import build_role_server
from app.store import AutoReplyStore, REPLY_TASK_SQL
from app.wechat import service
from app.wechat.models import WechatMessage, WechatVoiceUnavailable
from app.wechat.reader_ipc import ReaderIpcError


def _message(index, *, direction="inbound", text=None):
    return WechatMessage(
        account_id="acct-1", conversation_id="u9", message_id=f"m{index}",
        sender_id="u9" if direction == "inbound" else "self-1",
        sender_display_name="Alex" if direction == "inbound" else "derek",
        conversation_type="direct", direction=direction,
        sent_at=f"2026-10-24T10:0{index}:00+08:00", kind="text",
        text=text or f"message {index}", source_version="test",
    )


class FakeReader:
    def __init__(self, messages):
        self.messages, self.calls = messages, []

    def read_messages(self, account, **kwargs):
        self.calls.append((account, kwargs))
        return self.messages


def _bound_task(tmp_path, *, channel="wechat", single_chat=True, ready=True):
    db_path = tmp_path / "service.sqlite3"
    store = AutoReplyStore(db_path)
    if ready:
        store.upsert_wechat_read_state(
            account_id="acct-1", account_dir="/a", db_dir="/a/db_storage",
            app_version="4.1.10", self_user_id="self-1", capability_status="ready",
        )
    assert store.enqueue_reply_task(
        channel=channel, conversation_id="u9", conversation_title="Alex",
        single_chat=single_chat, trigger_message_id="m1",
        trigger_create_time="2026-10-24T10:00:00+08:00", trigger_sender="Alex",
        trigger_text="hi", execution_generation="generation-one",
    )
    with sqlite3.connect(db_path) as db:
        task_id = db.execute(f"select id from ({REPLY_TASK_SQL})").fetchone()[0]
    assert store.claim_reply_task(task_id) is not None
    return db_path, task_id


def _tool(role, db_path, task_id, generation="generation-one"):
    return build_role_server(
        role, task_id=task_id, db_path=db_path, execution_generation=generation,
    )._tool_manager.get_tool("read_wechat_messages").fn


@pytest.mark.parametrize("role", ["consumer", "audit"])
@pytest.mark.parametrize(("single_chat", "kind"), [(True, "direct"), (False, "group")])
def test_wechat_task_reads_its_own_conversation(
    tmp_path, monkeypatch, role, single_chat, kind,
):
    db_path, task_id = _bound_task(tmp_path, single_chat=single_chat)
    reader = FakeReader([_message(1), _message(2, direction="outbound")])
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)

    result = _tool(role, db_path, task_id)(limit=5)

    assert result["conversation_id"] == "u9"
    assert result["conversation_type"] == kind
    assert [m["text"] for m in result["messages"]] == ["message 1", "message 2"]
    assert result["messages"][1]["direction"] == "outbound"
    account, kwargs = reader.calls[0]
    assert account.account_id == "acct-1"
    assert kwargs == {"conversation_id": "u9", "conversation_type": kind, "limit": 5}


def test_wechat_read_uses_the_claimed_input_after_current_input_refresh(
    tmp_path, monkeypatch,
):
    db_path, task_id = _bound_task(tmp_path)
    store = AutoReplyStore(db_path)
    with store._immediate_write_transaction() as db:
        from app.task_entity_storage import (
            append_task_input,
            bind_current_input,
            get_task_with_inputs,
        )

        task = get_task_with_inputs(db, task_id)
        claimed = task["current_input"]
        refreshed = {
            key: value for key, value in claimed.items()
            if key not in {"id", "task_id"}
        }
        refreshed.update(
            conversation_id="u10",
            trigger_message_id="m2",
            input_revision_key="refresh:2:changed-conversation",
        )
        new_input, inserted = append_task_input(db, task_id=task_id, values=refreshed)
        assert inserted
        assert bind_current_input(
            db,
            task_id=task_id,
            input_id=int(new_input["id"]),
            expected_input_version=int(task["input_version"]),
            input_version=int(task["input_version"]) + 1,
        ) is not None

    reader = FakeReader([_message(1)])
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)
    result = _tool("consumer", db_path, task_id)(limit=5)

    assert result["conversation_id"] == "u9"
    assert reader.calls[0][1]["conversation_id"] == "u9"


def test_wechat_read_cannot_name_another_conversation(tmp_path):
    db_path, task_id = _bound_task(tmp_path)

    with pytest.raises(TypeError):
        _tool("consumer", db_path, task_id)(conversation_id="someone-else")


def test_wechat_read_refuses_a_non_wechat_task(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path, channel="dingtalk")
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="not a WeChat conversation"):
        _tool("consumer", db_path, task_id)()


def test_wechat_read_refuses_a_changed_generation(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="generation"):
        _tool("consumer", db_path, task_id, generation="generation-two")()


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_wechat_read_validates_the_limit(tmp_path, monkeypatch, limit):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="limit"):
        _tool("consumer", db_path, task_id)(limit=limit)


def test_wechat_read_needs_a_ready_account(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path, ready=False)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="no ready WeChat account"):
        _tool("consumer", db_path, task_id)()


def _voice_tool(role, db_path, task_id, generation="generation-one"):
    return build_role_server(
        role, task_id=task_id, db_path=db_path, execution_generation=generation,
    )._tool_manager.get_tool("save_wechat_voice").fn


def _silk(tmp_path):
    pcm, silk = tmp_path / "t.pcm", tmp_path / "t.silk"
    pcm.write_bytes(b"".join(
        struct.pack("<h", int(8000 * math.sin(n / 20))) for n in range(24000)
    ))
    pilk.encode(str(pcm), str(silk), pcm_rate=24000, tencent=True)
    return silk.read_bytes()


class VoiceReader:
    def __init__(self, result):
        self.result, self.calls = result, []

    def read_voice(self, account, **kwargs):
        self.calls.append((account, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def _task_artifacts(tmp_path, monkeypatch):
    from app import task_artifacts

    monkeypatch.setattr(task_artifacts, "TASK_ARTIFACT_ROOT", tmp_path / "task-artifacts")
    monkeypatch.setattr("app.agent_cli.migrate_legacy_task_artifacts", lambda _task_id: None)


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_wechat_voice_is_saved_as_a_wav_task_file(tmp_path, monkeypatch, role):
    db_path, task_id = _bound_task(tmp_path)
    reader = VoiceReader(_silk(tmp_path))
    monkeypatch.setattr(service, "build_reader", lambda **_kw: reader)

    result = _voice_tool(role, db_path, task_id)("m7")

    assert result["name"] == "wechat-voice-m7.wav"
    assert result["message_id"] == "m7"
    assert result["duration_seconds"] == pytest.approx(1, abs=0.1)
    assert "audio_base64" not in result
    saved = task_artifacts.TASK_ARTIFACT_ROOT / str(task_id) / "generation-one" / result["name"]
    with wave.open(str(saved)) as audio:
        assert audio.getframerate() == 24000
    account, kwargs = reader.calls[0]
    assert account.account_id == "acct-1"
    assert kwargs == {"conversation_id": "u9", "message_id": "m7"}


@pytest.mark.parametrize("failure", [
    ReaderIpcError("the voice audio is not stored on this Mac", code="voice_unavailable"),
    WechatVoiceUnavailable("the voice audio is too large to read"),
    b"bytes that are not a silk voice",
])
def test_unreadable_voice_is_an_explicit_error_never_an_empty_result(
    tmp_path, monkeypatch, failure,
):
    db_path, task_id = _bound_task(tmp_path)
    monkeypatch.setattr(service, "build_reader", lambda **_kw: VoiceReader(failure))

    with pytest.raises(ValueError, match="could not read the voice message"):
        _voice_tool("consumer", db_path, task_id)("m7")


def test_reader_outage_is_not_reported_as_an_unreadable_voice(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path)
    outage = ReaderIpcError("WeChat reader unavailable", code="unavailable")
    monkeypatch.setattr(service, "build_reader", lambda **_kw: VoiceReader(outage))

    with pytest.raises(ReaderIpcError, match="unavailable"):
        _voice_tool("consumer", db_path, task_id)("m7")


def test_wechat_voice_cannot_name_another_conversation(tmp_path):
    db_path, task_id = _bound_task(tmp_path)

    with pytest.raises(TypeError):
        _voice_tool("consumer", db_path, task_id)("m7", conversation_id="someone-else")


def test_wechat_voice_refuses_a_non_wechat_task(tmp_path, monkeypatch):
    db_path, task_id = _bound_task(tmp_path, channel="dingtalk")
    monkeypatch.setattr(service, "build_reader", lambda **_kw: pytest.fail("must not read"))

    with pytest.raises(ValueError, match="not a WeChat conversation"):
        _voice_tool("consumer", db_path, task_id)("m7")


def _upload_tool(role, db_path, task_id):
    return build_role_server(
        role, task_id=task_id, db_path=db_path, execution_generation="generation-one",
    )._tool_manager.get_tool("upload_audio_file").fn


class _UploadServer:
    """A stand-in for a transcription service's one-time upload URL."""

    def __init__(self, status=202, answer=b'{"job_id": "job-1", "status": "running"}'):
        outer = self
        outer.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.received.append((self.headers["Content-Type"], self.rfile.read(length)))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *_args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/asr/upload?ticket=once"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def _task_file(task_id, name, data):
    root = task_artifacts.TASK_ARTIFACT_ROOT / str(task_id) / "generation-one"
    root.mkdir(parents=True)
    (root / name).write_bytes(data)


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_audio_file_is_posted_as_multipart_to_the_one_time_url(tmp_path, role):
    db_path, task_id = _bound_task(tmp_path)
    _task_file(task_id, "clip.wav", b"RIFF-audio-bytes")
    server = _UploadServer()
    try:
        result = _upload_tool(role, db_path, task_id)("clip.wav", server.url)
    finally:
        server.close()

    assert result == {"job_id": "job-1", "status": "running"}
    content_type, body = server.received[0]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="clip.wav"' in body
    assert b"RIFF-audio-bytes" in body


def test_upload_failure_names_the_status_and_the_service_answer(tmp_path):
    db_path, task_id = _bound_task(tmp_path)
    _task_file(task_id, "clip.wav", b"x")
    server = _UploadServer(status=410, answer=b'{"error": "ticket used"}')
    try:
        with pytest.raises(ValueError, match="HTTP 410.*ticket used"):
            _upload_tool("consumer", db_path, task_id)("clip.wav", server.url)
    finally:
        server.close()


def test_upload_needs_an_existing_task_file_and_a_web_url(tmp_path):
    db_path, task_id = _bound_task(tmp_path)
    _task_file(task_id, "clip.wav", b"x")
    tool = _upload_tool("consumer", db_path, task_id)

    with pytest.raises(ValueError, match="task file is unavailable"):
        tool("missing.wav", "https://example.test/u")
    with pytest.raises(ValueError, match="upload url is invalid"):
        tool("clip.wav", "file:///etc/passwd")
    with pytest.raises(ValueError, match="task file name is invalid"):
        tool("../clip.wav", "https://example.test/u")
