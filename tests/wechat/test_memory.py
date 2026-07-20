import json
import threading
import time

import pytest
from pydantic import ValidationError

from app.store import AutoReplyStore
from app.wechat.memory import (
    CodexMemoryExtractionRunner, CodexMemoryWriteBackend,
    ExtractedMemoryCandidate, WechatMemoryImporter, WechatMemoryWriter,
)
from app.wechat.memory_import import CodexMemoryRecallMatcher, DurableMemoryMatch
from app.wechat.models import WechatMessage


def candidate(statement, *, category, source_message_ids=("m1",)):
    return ExtractedMemoryCandidate(
        statement=statement, category=category, confidence=0.9, sensitivity="normal",
        source_message_ids=list(source_message_ids), source_conversation_ids=["c1"],
        source_time_start="2026-07-17T10:00:00+08:00",
        source_time_end="2026-07-17T10:00:00+08:00",
        evidence_excerpt=statement, cleanup_notes="test fixture",
    )


class NoDurableMatch:
    def match(self, candidates):
        return {item.statement: "none" for item in candidates}


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
                "cleanup_notes": " x " * 200,
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


def test_candidate_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ExtractedMemoryCandidate(**candidate("x", category="fact").model_dump(), raw_chat="no")


def test_clean_discards_secret_or_transcript_cleanup_notes(store):
    importer = WechatMemoryImporter(store)
    rows = importer.clean_candidates([
        candidate("useful", category="fact").model_copy(
            update={"cleanup_notes": "token sk-proj-abcdefghijklmnopqrstuvwxyz"}
        ),
        candidate("also useful", category="fact").model_copy(
            update={"cleanup_notes": "x" * 501}
        ),
    ])
    assert [row.cleanup_notes for row in rows] == [
        "deterministic_cleanup:v1", "deterministic_cleanup:v1"]


def test_cleanup_note_is_deterministic_and_never_keeps_model_pii_or_chat(store):
    row = WechatMemoryImporter(store).clean_candidates([
        candidate("useful", category="fact").model_copy(update={
            "cleanup_notes": "Alex said call 13800138000 or alex@example.com: 原聊天短句"
        })
    ])[0]
    assert row.cleanup_notes == "deterministic_cleanup:v1"


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
    importer = WechatMemoryImporter(
        store, reader=FakeReader(), codex=FakeCodex(), matcher=NoDurableMatch())
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
    WechatMemoryImporter(store, reader=reader, codex=type("C", (), {"extract": lambda s, m: []})(), matcher=NoDurableMatch()).run(
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
    result = WechatMemoryImporter(store, Reader(), Extractor(), NoDurableMatch()).run(
        account=account, target_ids=["u1", "g@chatroom"], since="2026-07-01",
        until="2026-07-31", limit=2)
    assert result["messages"] == 2
    assert [(c["conversation_id"], c["conversation_type"], c["limit"]) for c in calls] == [
        ("u1", "direct", 2), ("g@chatroom", "group", 2)]


def test_import_global_newest_selection_is_target_order_independent(store):
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="acct", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    rows = {
        "old": [WechatMessage(account_id="acct", conversation_id="old", message_id=f"o{i}",
            sender_id="s", sender_display_name="S", conversation_type="direct",
            direction="inbound", sent_at=f"2026-07-0{i}T10:00:00+08:00", kind="text",
            text="old", source_version="4") for i in range(1, 4)],
        "new": [WechatMessage(account_id="acct", conversation_id="new", message_id="n1",
            sender_id="s", sender_display_name="S", conversation_type="direct",
            direction="inbound", sent_at="2026-07-20T10:00:00+08:00", kind="text",
            text="new", source_version="4")],
    }
    class Reader:
        def read_messages(self, account, **kwargs): return rows[kwargs["conversation_id"]]
    seen = []
    class Extractor:
        def extract(self, messages):
            seen.append([m.message_id for m in messages])
            return []
    WechatMemoryImporter(store, Reader(), Extractor(), NoDurableMatch()).run(
        account=account, target_ids=["old", "new"], since="2026-07-01", until="", limit=2)
    assert set(seen[0]) == {"o3", "n1"}
    WechatMemoryImporter(store, Reader(), Extractor(), NoDurableMatch()).run(
        account=account, target_ids=["new", "old"], since="2026-07-01", until="", limit=2)
    assert seen[1] == seen[0]


def test_import_filters_non_text_before_extraction(store):
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="acct", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    messages = [WechatMessage(account_id="acct", conversation_id="u", message_id=kind,
        sender_id="s", sender_display_name="S", conversation_type="direct", direction="inbound",
        sent_at="2026-07-20T10:00:00+08:00", kind=kind, text="payload", source_version="4")
        for kind in ("text", "image", "file", "system", "unknown")]
    class Reader:
        def read_messages(self, account, **kwargs): return messages
    seen = []
    class Extractor:
        def extract(self, batch):
            seen.extend(m.kind for m in batch)
            return []
    WechatMemoryImporter(store, Reader(), Extractor(), NoDurableMatch()).run(
        account=account, target_ids=["u"], since="2026-07-01", until="", limit=10)
    assert seen == ["text"]


def test_import_rejects_invalid_date_bounds_before_read(store):
    importer = WechatMemoryImporter(store, reader=object(), codex=object())
    with pytest.raises(ValueError, match="invalid since"):
        importer.run(account_id="acct", target_ids=["u"], since="yesterday", until="", limit=10)


def test_durable_exact_match_skips_pending_candidate(store):
    class Exact:
        def match(self, candidates):
            return {item.statement: DurableMemoryMatch(
                statement=item.statement, relation="exact", memory_id="mem-1",
                evidence="fact") for item in candidates}
    # Reuse the real bounded import fixture via simple source/extractor.
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="a", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    message = WechatMessage(account_id="a", conversation_id="c1", message_id="m1",
        sender_id="s", sender_display_name="S", conversation_type="direct", direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00", kind="text", text="fact", source_version="4")
    reader = type("R", (), {"read_messages": lambda self, account, **kw: [message]})()
    extractor = type("E", (), {"extract": lambda self, batch: [candidate("fact", category="fact")]})()
    result = WechatMemoryImporter(store, reader, extractor, Exact()).run(
        account=account, target_ids=["c1"], since="2026-07-01", until="", limit=10)
    assert result["durable_duplicates"] == 1
    assert store.list_wechat_memory_candidates() == []


def test_import_fails_closed_without_durable_matcher(store):
    importer = WechatMemoryImporter(store, reader=object(), codex=object())
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="a", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    with pytest.raises(RuntimeError, match="durable Memory matcher"):
        importer.run(account=account, target_ids=["c1"], since="2026-07-01", until="", limit=10)


def test_codex_recall_matcher_accepts_only_audited_memory_recall(tmp_path):
    query = 'wechat-memory-dedupe:v1:["fact"]'
    final = {"matches":[{"statement":"fact", "relation":"exact",
        "memory_id":"mem-1", "evidence":"durable fact", "merged_statement":""}]}
    success = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call","call_id":"r1","tool":"memory_recall","arguments":{"query":query}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result","call_id":"r1","output":{"memories":[{"uuid":"mem-1","text":"durable fact"}]}}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(final)}}),
    ])
    captured = {}
    def execute(command, prompt):
        captured["command"] = command
        return success
    matcher = CodexMemoryRecallMatcher(tmp_path, executor=execute)
    assert matcher.match([candidate("fact", category="fact")])["fact"].relation == "exact"
    assert 'mcp_servers.memory_connector.enabled_tools=["memory_recall"]' in captured["command"]
    assert 'mcp_servers.memory_connector.disabled_tools=["memory_write"]' in captured["command"]
    malicious = success.replace('"tool": "memory_recall"', '"tool": "memory_write"')
    with pytest.raises(RuntimeError, match="only memory_recall"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: malicious).match(
            [candidate("fact", category="fact")])

    unrelated_events = [json.loads(line) for line in success.splitlines()]
    unrelated_events[0]["item"]["arguments"]["query"] = "unrelated"
    unrelated = "\n".join(json.dumps(event) for event in unrelated_events)
    with pytest.raises(RuntimeError, match="query does not match"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: unrelated).match(
            [candidate("fact", category="fact")])
    missing_memories = success.replace('"memories":', '"items":')
    with pytest.raises(RuntimeError, match="memories list"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: missing_memories).match(
            [candidate("fact", category="fact")])
    fabricated = success.replace("durable fact", "unrelated evidence", 1)
    with pytest.raises(RuntimeError, match="same recalled memory"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: fabricated).match(
            [candidate("fact", category="fact")])
    fabricated_id = success.replace("mem-1", "other-id", 1)
    with pytest.raises(RuntimeError, match="same recalled memory"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: fabricated_id).match(
            [candidate("fact", category="fact")])


def test_codex_recall_matcher_accepts_real_empty_memories_as_none(tmp_path):
    query = 'wechat-memory-dedupe:v1:["fact"]'
    final = {"matches":[{"statement":"fact", "relation":"none",
        "memory_id":"", "evidence":"", "merged_statement":""}]}
    raw = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call",
            "call_id":"r1","tool":"memory_recall","arguments":{"query":query}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result",
            "call_id":"r1","output":{"structured_content":{"result":json.dumps({"memories":[]})}}}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(final)}}),
    ])
    result = CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: raw).match(
        [candidate("fact", category="fact")])
    assert result["fact"].relation == "none"


def test_codex_recall_support_must_come_from_same_memory_object(tmp_path):
    query = 'wechat-memory-dedupe:v1:["fact"]'
    final = {"matches":[{"statement":"fact", "relation":"exact",
        "memory_id":"mem-a", "evidence":"evidence from B", "merged_statement":""}]}
    output = {"memories":[{"uuid":"mem-a","text":"evidence from A"},
                           {"uuid":"mem-b","summary":"evidence from B"}]}
    raw = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call",
            "call_id":"r1","tool":"memory_recall","arguments":{"query":query}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result",
            "call_id":"r1","output":output}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(final)}}),
    ])
    with pytest.raises(RuntimeError, match="same recalled memory"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: raw).match(
            [candidate("fact", category="fact")])


def test_codex_recall_explicit_is_error_fails(tmp_path):
    query = 'wechat-memory-dedupe:v1:["fact"]'
    final = {"matches":[{"statement":"fact", "relation":"none",
        "memory_id":"", "evidence":"", "merged_statement":""}]}
    raw = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call",
            "call_id":"r1","tool":"memory_recall","arguments":{"query":query}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result",
            "call_id":"r1","isError":True,"output":{"memories":[]}}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(final)}}),
    ])
    with pytest.raises(RuntimeError, match="tool error"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: raw).match(
            [candidate("fact", category="fact")])


@pytest.mark.parametrize("bad_evidence", [" ", "short"])
def test_codex_recall_rejects_blank_or_too_short_evidence(tmp_path, bad_evidence):
    query = 'wechat-memory-dedupe:v1:["fact"]'
    final = {"matches":[{"statement":"fact", "relation":"exact",
        "memory_id":"mem-1", "evidence":bad_evidence, "merged_statement":""}]}
    raw = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call",
            "call_id":"r1","tool":"memory_recall","arguments":{"query":query}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result",
            "call_id":"r1","output":{"memories":[{
                "uuid":"mem-1", "text":f"context {bad_evidence} context"}]}}}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(final)}}),
    ])
    with pytest.raises(RuntimeError, match="evidence is too short"):
        CodexMemoryRecallMatcher(tmp_path, executor=lambda c, p: raw).match(
            [candidate("fact", category="fact")])


def test_compatible_durable_match_persists_safe_merged_statement(store):
    class Compatible:
        def match(self, candidates):
            return {item.statement: DurableMemoryMatch(
                statement=item.statement, relation="compatible", memory_id="mem-1",
                evidence="support", merged_statement="Derek prefers concise weekly updates")
                for item in candidates}
    from app.wechat.models import WechatAccount
    account = WechatAccount(account_id="a", display_name="D", self_user_id="self",
                            account_dir="/a", db_dir="/a/db", app_version="4")
    message = WechatMessage(account_id="a", conversation_id="c1", message_id="m1",
        sender_id="s", sender_display_name="S", conversation_type="direct", direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00", kind="text", text="fact", source_version="4")
    reader = type("R", (), {"read_messages": lambda self, account, **kw: [message]})()
    extractor = type("E", (), {"extract": lambda self, batch: [candidate(
        "Derek prefers concise updates", category="preference")]})()
    WechatMemoryImporter(store, reader, extractor, Compatible()).run(
        account=account, target_ids=["c1"], since="2026-07-01", until="", limit=10)
    row = store.list_wechat_memory_candidates()[0]
    assert row["statement"] == "Derek prefers concise weekly updates"
    assert row["cleanup_notes"].endswith("dedupe_relation:compatible")


def _seed_candidate(store, status):
    cid = store.add_wechat_memory_candidate(
        import_run_id="r1", account_id="acct-1",
        candidate=candidate("Derek prefers concise updates", category="preference"),
    )
    if status == "approved":
        store.review_wechat_memory_candidate(
            cid, "approve", reviewer="Derek", final_statement="Derek prefers concise updates")
    elif status == "rejected":
        store.review_wechat_memory_candidate(cid, "reject", reviewer="Derek")
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


def test_review_actions_refuse_candidate_while_write_claimed(store):
    cid = _seed_candidate(store, status="approved")
    assert store.claim_wechat_memory_candidate_write(cid)["outcome"] == "claimed"
    for action in ("approve", "reject", "revoke"):
        with pytest.raises(ValueError, match="writing"):
            store.review_wechat_memory_candidate(
                cid, action, reviewer="Derek", final_statement="safe")
    with pytest.raises(ValueError, match="stale"):
        store.resolve_wechat_memory_candidate_write_unknown(
            cid, reviewer="Derek", confirm=True)
    with store._connect() as db:
        db.execute("update wechat_memory_candidates set updated_at='2000-01-01' where id=?", (cid,))
    store.resolve_wechat_memory_candidate_write_unknown(
        cid, reviewer="Derek", confirm=True)
    assert store.get_wechat_memory_candidate(cid)["memory_write_status"] == "unknown"


def test_resolve_writing_rejects_reduced_stale_threshold(store):
    cid = _seed_candidate(store, status="approved")
    store.claim_wechat_memory_candidate_write(cid)
    with pytest.raises(ValueError, match="less than 900"):
        store.resolve_wechat_memory_candidate_write_unknown(
            cid, reviewer="Derek", confirm=True, stale_after_seconds=0)


def test_finish_write_race_never_creates_revoked_written(store):
    cid = _seed_candidate(store, status="approved")
    assert store.claim_wechat_memory_candidate_write(cid)["outcome"] == "claimed"
    with store._connect() as db:
        db.execute("update wechat_memory_candidates set status='revoked' where id=?", (cid,))
    store.finish_wechat_memory_candidate_write(cid, status="written", memory_id="episode-1")
    row = store.get_wechat_memory_candidate(cid)
    assert row["status"] == "revoked"
    assert row["memory_write_status"] == "revocation_unavailable"


def test_cross_run_duplicate_merges_sources_without_new_candidate(store):
    first = store.add_wechat_memory_candidate(
        import_run_id="r1", account_id="acct", candidate=candidate(
            "Derek likes async", category="preference"))
    duplicate = candidate("  derek   likes ASYNC ", category="preference").model_copy(
        update={"source_message_ids":["m2"], "source_conversation_ids":["c2"],
                "source_time_start":"2026-07-16", "source_time_end":"2026-07-18"})
    assert store.add_wechat_memory_candidate(
        import_run_id="r2", account_id="acct", candidate=duplicate) is None
    rows = store.list_wechat_memory_candidates()
    assert [row["id"] for row in rows] == [first]
    assert set(json.loads(rows[0]["source_message_ids_json"])) == {"m1", "m2"}


@pytest.mark.parametrize("terminal", ["rejected", "revoked"])
def test_rejected_or_revoked_local_candidate_does_not_suppress_new_run(store, terminal):
    first = store.add_wechat_memory_candidate(
        import_run_id="r1", account_id="acct",
        candidate=candidate("Derek likes async", category="preference"))
    if terminal == "rejected":
        store.review_wechat_memory_candidate(first, "reject", reviewer="Derek")
    else:
        store.review_wechat_memory_candidate(
            first, "approve", reviewer="Derek", final_statement="Derek likes async")
        store.review_wechat_memory_candidate(first, "revoke", reviewer="Derek")
    second = store.add_wechat_memory_candidate(
        import_run_id="r2", account_id="acct",
        candidate=candidate(" derek likes ASYNC ", category="preference"))
    assert second is not None and second != first


def test_codex_write_backend_requires_successful_memory_write_tool_event(tmp_path):
    output = {"structured_content":{"result":json.dumps({"ok":True,"episode_uuid":"episode-1","processing_status":"completed"})}}
    success = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call", "call_id":"c1", "tool":"memory_write", "arguments":{"data":"final","type":"text","created_at":"2026-07-17"}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result", "call_id":"c1", "output":output}}),
        json.dumps({"status":"attempted"}),
    ])
    backend = CodexMemoryWriteBackend(tmp_path, executor=lambda command, prompt: success)
    assert backend.write("final", source_time_start="2026-07-17", source_time_end="") == "episode-1"
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

    malicious = success.replace('"data": "final"', '"data": "evil", "user_id": "u"')
    with pytest.raises(Exception, match="arguments"):
        CodexMemoryWriteBackend(tmp_path, executor=lambda c, p: malicious).write(
            "final", source_time_start="2026-07-17", source_time_end="")

    vague = "\n".join([
        json.dumps({"type":"item.completed","item":{"type":"mcp_tool_call", "call_id":"c1", "tool":"memory_write", "arguments":{"data":"final","type":"text","created_at":"2026-07-17"}}}),
        json.dumps({"type":"item.completed","item":{"type":"tool_result", "call_id":"c1", "output":"550e8400-e29b-41d4-a716-446655440000"}}),
    ])
    with pytest.raises(Exception, match="unknown"):
        CodexMemoryWriteBackend(tmp_path, executor=lambda c, p: vague).write(
            "final", source_time_start="2026-07-17", source_time_end="")

    failed_output = {"structured_content":{"result":json.dumps({
        "ok":False, "episode_uuid":"episode-failed", "processing_status":"failed",
        "last_error":"backend rejected"})}}
    failed = success.replace(json.dumps(output), json.dumps(failed_output))
    with pytest.raises(RuntimeError, match="backend rejected"):
        CodexMemoryWriteBackend(tmp_path, executor=lambda c, p: failed).write(
            "final", source_time_start="2026-07-17", source_time_end="")

    preview = success.replace('"tool": "memory_write"', '"tool": "memory_write_preview"')
    with pytest.raises(Exception, match="expected one tool call"):
        CodexMemoryWriteBackend(tmp_path, executor=lambda c, p: preview).write(
            "final", source_time_start="2026-07-17", source_time_end="")


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


def test_codex_extraction_parses_live_item_completed_agent_message(tmp_path):
    payload = {"candidates": [candidate("durable fact", category="fact").model_dump()]}
    raw = json.dumps({"type":"item.completed", "item":{
        "type":"agent_message", "text":json.dumps(payload)}})
    result = CodexMemoryExtractionRunner(
        tmp_path, executor=lambda command, prompt: raw).extract([])
    assert result[0].statement == "durable fact"
