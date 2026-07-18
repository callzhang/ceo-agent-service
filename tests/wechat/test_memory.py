import pytest

from app.store import AutoReplyStore
from app.wechat.memory import (
    ExtractedMemoryCandidate, WechatMemoryImporter, WechatMemoryWriter,
)


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


def test_run_persists_pending_candidates(store):
    class FakeCodex:
        def extract(self, *a):
            return [
                candidate("Derek approves the Q3 budget", category="decision"),
                candidate("验证码 654321", category="fact"),
            ]
    importer = WechatMemoryImporter(store, codex=FakeCodex())
    result = importer.run(account_id="acct-1", target_ids=["u1"], since="", until="", limit=100)
    assert result["candidates"] == 1
    pending = store.list_wechat_memory_candidates(status="pending")
    assert [p["statement"] for p in pending] == ["Derek approves the Q3 budget"]


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
