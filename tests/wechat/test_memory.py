import json
import threading
import time

import pytest

from app.store import AutoReplyStore
from app.wechat.memory import (
    CodexMemoryExtractionRunner, CodexMemoryWriteBackend,
    ExtractedMemoryCandidate, WechatMemoryImporter, WechatMemoryWriter,
)
from app.wechat.models import WechatMessage


def candidate(statement, *, category, source_message_ids=("m1",)):
    return ExtractedMemoryCandidate(
        statement=statement, category=category, confidence=0.9, sensitivity="normal",
        source_message_ids=list(source_message_ids), source_conversation_ids=["c1"],
        source_time_start="2026-07-17T10:00:00+08:00",
        source_time_end="2026-07-17T10:00:00+08:00",
        evidence_excerpt=statement, cleanup_notes="test fixture",
    )


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


def test_import_requires_explicit_bound(store):
    importer = WechatMemoryImporter(store)
    with pytest.raises(ValueError, match="bounded scope"):
        importer.run(account_id="acct-1", target_ids=[], since="", until="", limit=0)


def test_credentials_never_become_candidates(store):
    importer = WechatMemoryImporter(store)
    rows = importer.clean_candidates([
        candidate("验证码是 123456", category="fact"),
        candidate("sk-proj-abcdefghijklmnopqrstuvwxyz", category="fact"),
        candidate("Derek prefers concise status updates", category="preference"),
    ])
    assert [r.statement for r in rows] == ["Derek prefers concise status updates"]


def test_clean_drops_empty_sources_and_unknown_category_and_dupes(store):
    importer = WechatMemoryImporter(store)
    rows = importer.clean_candidates([
        candidate("no sources", category="fact", source_message_ids=()),
        candidate("weird", category="not_a_category"),
        candidate("Derek likes async", category="preference"),
        candidate("Derek likes async", category="preference"),
    ])
    assert [r.statement for r in rows] == ["Derek likes async"]


def test_clean_rejects_non_normal_and_redacts_and_bounds_fields(store):
    importer = WechatMemoryImporter(store)
    rows = importer.clean_candidates([
        candidate("private diagnosis", category="fact").model_copy(
            update={"sensitivity": "sensitive"}
        ),
        candidate("  Derek   likes concise notes  ", category="preference").model_copy(
            update={
                "evidence_excerpt": "contact derek@example.com or 13800138000; concise",
                "cleanup_notes": " x " * 500,
            }
        ),
    ])
    assert len(rows) == 1
    assert rows[0].statement == "Derek likes concise notes"
    assert "derek@example.com" not in rows[0].evidence_excerpt
    assert "13800138000" not in rows[0].evidence_excerpt
    assert len(rows[0].cleanup_notes) <= 500


def test_clean_rejects_raw_transcript_sized_evidence_and_missing_source_time(store):
    importer = WechatMemoryImporter(store)
    assert importer.clean_candidates([
        candidate("useful", category="fact").model_copy(
            update={"evidence_excerpt": "x" * 301}
        ),
        candidate("missing time", category="fact").model_copy(
            update={"source_time_start": ""}
        ),
    ]) == []


def test_run_persists_pending_candidates(store):
    class FakeReader:
        def read_messages(self, account, **kwargs):
            return [WechatMessage(
                account_id=account.account_id, conversation_id=kwargs["conversation_id"],
                message_id="m1", sender_id="u1", sender_display_name="Alex",
                conversation_type=kwargs["conversation_type"], direction="inbound",
                sent_at="2026-07-17T10:00:00+08:00", kind="text", text="approved",
                source_version=account.app_version,
            )]

    class FakeCodex:
        def extract(self, messages):
            assert [m.message_id for m in messages] == ["m1"]
            return [
                candidate("Derek approves the Q3 budget", category="decision").model_copy(
                    update={"source_conversation_ids": ["u1"]}
                ),
                candidate("验证码 654321", category="fact").model_copy(
                    update={"source_conversation_ids": ["u1"]}
                ),
            ]
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="acct-1", display_name="Derek", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    importer = WechatMemoryImporter(store, reader=FakeReader(), codex=FakeCodex())
    result = importer.run(account=account, target_ids=["u1"],
                          since="2026-07-01", until="2026-07-31", limit=100)
    assert result["candidates"] == 1
    pending = store.list_wechat_memory_candidates(status="pending")
    assert [p["statement"] for p in pending] == ["Derek approves the Q3 budget"]


def test_run_id_is_stable_and_includes_targets(store):
    a = WechatMemoryImporter.import_run_id("a", ["u1"], "s", "u", 10)
    b = WechatMemoryImporter.import_run_id("a", ["u2"], "s", "u", 10)
    assert a == WechatMemoryImporter.import_run_id("a", ["u1"], "s", "u", 10)
    assert a != b


def test_import_does_not_change_scope_watermark(store):
    from app.wechat.models import WechatAccount, WechatReplyScope
    store.replace_wechat_reply_scopes("acct-1", [WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u1",
        display_name="A", trigger_mode="every_inbound_text")])
    before = store.get_wechat_reply_scope("acct-1", "direct", "u1").last_active_at
    account = WechatAccount(account_id="acct-1", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    reader = type("R", (), {"read_messages": lambda self, account, **kw: []})()
    WechatMemoryImporter(store, reader=reader, codex=type("C", (), {"extract": lambda s, m: []})()).run(
        account=account, target_ids=["u1"], since="2026-07-01", until="", limit=10)
    assert store.get_wechat_reply_scope("acct-1", "direct", "u1").last_active_at == before


def test_import_reads_each_target_with_correct_conversation_type_and_total_limit(store):
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="acct-1", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    calls = []
    class Reader:
        def read_messages(self, account, **kwargs):
            calls.append(kwargs)
            target = kwargs["conversation_id"]
            return [WechatMessage(
                account_id="acct-1", conversation_id=target, message_id=f"m-{target}",
                sender_id="s", sender_display_name="S",
                conversation_type=kwargs["conversation_type"], direction="inbound",
                sent_at="2026-07-17T10:00:00+08:00", kind="text", text="hello",
                source_version="4")]
    class Extractor:
        def extract(self, messages):
            return []
    result = WechatMemoryImporter(store, Reader(), Extractor()).run(
        account=account, target_ids=["u1", "g@chatroom"], since="2026-07-01",
        until="2026-07-31", limit=2)
    assert result["messages"] == 2
    assert [(c["conversation_id"], c["conversation_type"], c["limit"]) for c in calls] == [
        ("u1", "direct", 2), ("g@chatroom", "group", 1)]


def test_import_rejects_invalid_date_bounds_before_read(store):
    importer = WechatMemoryImporter(store, reader=object(), codex=object())
    with pytest.raises(ValueError, match="invalid since"):
        importer.run(account_id="acct", target_ids=["u"], since="yesterday", until="", limit=10)


def _seed_candidate(store, status):
    cid = store.add_wechat_memory_candidate(
        import_run_id="r1", account_id="acct-1",
        candidate=candidate("Derek prefers concise updates", category="preference"),
    )
    store.set_wechat_memory_candidate_status(cid, status)
    return cid


class FakeMemoryBackend:
    def __init__(self):
        self.calls = 0

    def write(self, statement, **kw):
        self.calls += 1
        return "memory-1"


def test_pending_candidate_cannot_be_written(store):
    writer = WechatMemoryWriter(store, FakeMemoryBackend())
    cid = _seed_candidate(store, status="pending")
    with pytest.raises(ValueError, match="approved"):
        writer.write(cid)


def test_approved_write_is_idempotent(store):
    backend = FakeMemoryBackend()
    writer = WechatMemoryWriter(store, backend)
    cid = _seed_candidate(store, status="approved")
    assert writer.write(cid) == "memory-1"
    assert writer.write(cid) == "memory-1"
    assert backend.calls == 1


def test_concurrent_approved_write_calls_backend_once(store):
    class SlowBackend(FakeMemoryBackend):
        def write(self, statement, **kw):
            self.calls += 1
            time.sleep(.05)
            return "memory-1"
    backend = SlowBackend()
    writer = WechatMemoryWriter(store, backend)
    cid = _seed_candidate(store, status="approved")
    results, errors = [], []
    def work():
        try:
            results.append(writer.write(cid))
        except RuntimeError as exc:
            errors.append(str(exc))
    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert backend.calls == 1
    assert results == ["memory-1"]
    assert errors == ["memory write already in progress"]


def test_unknown_write_is_not_auto_retryable(store):
    class Unknown:
        def write(self, *args, **kwargs):
            raise Exception("memory write outcome unknown")
    cid = _seed_candidate(store, status="approved")
    with pytest.raises(Exception, match="unknown"):
        WechatMemoryWriter(store, Unknown()).write(cid)
    assert store.get_wechat_memory_candidate(cid)["memory_write_status"] == "unknown"
    with pytest.raises(ValueError, match="unknown"):
        WechatMemoryWriter(store, Unknown()).write(cid)


def test_failed_write_is_explicit_and_can_be_manually_retried(store):
    class Flaky:
        def __init__(self): self.calls = 0
        def write(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("backend rejected")
            return "memory-2"
    backend = Flaky()
    cid = _seed_candidate(store, status="approved")
    with pytest.raises(RuntimeError, match="rejected"):
        WechatMemoryWriter(store, backend).write(cid)
    assert store.get_wechat_memory_candidate(cid)["memory_write_status"] == "failed"
    assert WechatMemoryWriter(store, backend).write(cid) == "memory-2"


def test_written_candidate_revoke_records_backend_limitation(store):
    cid = _seed_candidate(store, status="approved")
    assert WechatMemoryWriter(store, FakeMemoryBackend()).write(cid) == "memory-1"
    row = store.review_wechat_memory_candidate(cid, "revoke", reviewer="Derek")
    assert row["status"] == "revoked"
    assert row["memory_write_status"] == "revocation_unavailable"
    with pytest.raises(ValueError, match="approved"):
        WechatMemoryWriter(store, FakeMemoryBackend()).write(cid)


def test_codex_write_backend_requires_successful_memory_write_tool_event(tmp_path):
    success = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call", "call_id":"c1", "tool":"memory_write", "arguments":{"data":"ok","type":"text","created_at":"2026-07-17"}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result", "call_id":"c1", "output":{"status":"success","memory_id":"550e8400-e29b-41d4-a716-446655440000"}}}),
        json.dumps({"status":"attempted"}),
    ])
    backend = CodexMemoryWriteBackend(tmp_path, executor=lambda command, prompt: success)
    assert backend.write("final", source_time_start="2026-07-17", source_time_end="") == "550e8400-e29b-41d4-a716-446655440000"
    fake = CodexMemoryWriteBackend(tmp_path, executor=lambda command, prompt: json.dumps({"memory_id":"fake"}))
    with pytest.raises(Exception, match="unknown"):
        fake.write("final", source_time_start="2026-07-17", source_time_end="")

    extra_tool = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"tool_call", "call_id":"x", "tool_name":"exec_command", "arguments":{"cmd":"true"}}}),
        success,
    ])
    with pytest.raises(Exception, match="expected one tool call"):
        CodexMemoryWriteBackend(
            tmp_path, executor=lambda command, prompt: extra_tool
        ).write("final", source_time_start="2026-07-17", source_time_end="")


def test_codex_extraction_runner_parses_batch_envelope_and_forbids_write(tmp_path):
    captured = {}
    payload = {"candidates": [candidate("durable fact", category="fact").model_dump()]}
    def execute(command, prompt):
        captured.update(command=command, prompt=prompt)
        return json.dumps(payload)
    message = WechatMessage(
        account_id="a", conversation_id="c1", message_id="m1", sender_id="u",
        sender_display_name="U", conversation_type="direct", direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00", kind="text", text="hello",
        source_version="4")
    result = CodexMemoryExtractionRunner(tmp_path, executor=execute).extract([message])
    assert [item.statement for item in result] == ["durable fact"]
    assert "不会提供 memory_write" in captured["prompt"]
    assert "wechat_memory_candidates.schema.json" in " ".join(captured["command"])
    assert "mcp_servers.memory_connector.enabled=false" in captured["command"]
