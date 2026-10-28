"""Approved bounded preview policy; full inputs and source bindings stay distinct."""
from types import SimpleNamespace

import pytest

from app.email_canonical_storage import insert_email_classification, upsert_email_message
from app.email_store import EmailStore
from tests.support.email_native_fixtures import _classification, _message


def test_preview_is_displayed_plain_text_bounded_by_unicode_characters():
    from app.email_preview import email_preview

    text = "中" * 999 + "😀" + "正文后部关键词"
    assert email_preview(text) == text[:1000]
    assert len(email_preview(text)) == 1000
    assert email_preview("Content-Type: text/html\n\n<p>合同<strong>确认</strong></p>") == "合同确认"


@pytest.fixture
def preview_catalog(tmp_path):
    store = EmailStore(tmp_path / "preview.sqlite3")
    with store._connect() as db:
        db.execute("begin immediate")
        for index, text in enumerate(("前部关键词" + "中" * 1100 + "后部关键词", "另一个正文"), 1):
            identity = f"account:message-id:<preview{index}@example.test>"
            upsert_email_message(db, _message(identity, uid=index, normalized_text=text, preview="old"))
            insert_email_classification(db, _classification(identity, id=index, uid=index,
                sender="王五", subject=f"项目进展{index}", preview="old", status="pending_feedback"))
    return store


def test_population_keeps_one_preview_and_preserves_full_input(preview_catalog):
    from app.email_preview import populate_email_previews

    with preview_catalog._connect() as db:
        before = db.execute("select message_normalized_text,class_model_text,message_source_reference_json from emails order by id").fetchall()
        facts_before = [dict(row) for row in db.execute("select * from emails order by id")]
        populate_email_previews(db)
        rows = db.execute("select message_preview from emails order by id").fetchall()
        assert rows[0][0] == ("前部关键词" + "中" * 1100 + "后部关键词")[:1000]
        assert rows[1][0] == "另一个正文"
        facts_after = [dict(row) for row in db.execute("select * from emails order by id")]
        for old, new in zip(facts_before, facts_after, strict=True):
            assert new | {key: old[key] for key in ("message_preview",)} == old
        assert db.execute("select message_normalized_text,class_model_text,message_source_reference_json from emails order by id").fetchall() == before
        populate_email_previews(db)
        assert db.execute("select message_preview from emails order by id").fetchall() == rows


def test_default_search_never_reads_sources_but_full_search_finds_deep_keyword(preview_catalog):
    from app.email_preview import populate_email_previews
    from app.email_classification_reader import EmailClassificationReader

    with preview_catalog._connect() as db:
        populate_email_previews(db)
    calls = []
    def read_views(account, identities):
        calls.extend(identities)
        return {identity: {"message_text": "前部关键词" + "中" * 1100 + "后部关键词"} for identity in identities}
    reader = EmailClassificationReader(preview_catalog, message_reader=SimpleNamespace(read_views=read_views))
    rows, total = reader.list_classifications(status=None, limit=20, offset=0)
    assert total == 2 and len(rows) == 2
    assert calls == []
    assert all("message_text" not in row for row in rows)
    rows, total = reader.list_classifications(status=None, q="前部关键词", limit=20, offset=0)
    assert total == 1 and rows[0]["id"] == 1
    assert calls == []
    assert reader.list_classifications(status=None, q="后部关键词", limit=20, offset=0) == ([], 0)
    assert calls == []
    rows, total = reader.list_classifications(status=None, q="后部关键词", limit=20, offset=0, search_scope="full")
    assert total == 2 and len(rows) == 2
    assert len(calls) == 2
    detail = reader.get_classification(1)
    assert detail["message_text"] == "前部关键词" + "中" * 1100 + "后部关键词"
    assert len(detail["preview"]) == 1000
    assert detail["message_text"].endswith("后部关键词")


def test_default_search_keeps_unicode_casefold_and_literal_percent(preview_catalog):
    from app.email_classification_reader import EmailClassificationReader

    with preview_catalog._connect() as db:
        db.execute("update emails set message_preview='Straße 100% literal'")
    def unavailable(*args):
        raise AssertionError("preview listing must not read originals")
    reader = EmailClassificationReader(preview_catalog, message_reader=SimpleNamespace(read_views=unavailable))
    rows, total = reader.list_classifications(status=None, q="STRASSE", limit=1, offset=1)
    assert total == 2 and [row["id"] for row in rows] == [1]
    assert reader.list_classifications(status=None, q="100%", limit=20, offset=0)[1] == 2
    assert reader.list_classifications(status=None, q="100_", limit=20, offset=0) == ([], 0)


def test_scan_persists_one_bounded_preview_without_truncating_inputs(tmp_path):
    from app.email_classifier_contracts import EmailClassificationStatus
    from tests.test_email_store import _classification as scan_classification

    store = EmailStore(tmp_path / "scan-preview.sqlite3")
    classification = scan_classification(status=EmailClassificationStatus.PENDING_FEEDBACK)
    original = "中" * 999 + "😀" + "正文后部"
    features = "exact model input " + original
    store.persist_scan_result(classification, normalized_text=original,
        preview="caller preview must not become another body copy", model_text=features)
    with store._connect() as db:
        row = db.execute("select message_preview,message_normalized_text,class_model_text,message_source_reference_json from emails").fetchone()
    assert row[0] == original[:1000]
    assert row[1] == original
    assert row[2] == features
    assert row[3] is None


def test_message_only_row_owns_preview_without_inventing_classification(tmp_path):
    from app.email_preview import populate_email_previews

    store = EmailStore(tmp_path / "message-only-preview.sqlite3")
    body = "message-only" * 150
    with store._connect() as db:
        db.execute("begin immediate")
        upsert_email_message(db, _message("message-only", normalized_text=body))
        populate_email_previews(db)
        row = db.execute("select classification_id,message_preview,message_normalized_text from emails").fetchone()
    assert tuple(row) == (None, body[:1000], body)


def test_retired_source_preview_stays_bounded_without_recreating_original(preview_catalog):
    import json
    from app.email_preview import populate_email_previews

    reference = json.dumps({"kind": "provider", "sha256": "a" * 64})
    with preview_catalog._connect() as db:
        db.execute("update emails set message_normalized_text='',message_preview=?,message_source_reference_json=?",
                   (("已保存的真实预览" * 200)[:1000], reference))
        populate_email_previews(db)
        rows = db.execute("select message_preview,message_normalized_text,message_source_reference_json from emails").fetchall()
    assert all(tuple(row) == (("已保存的真实预览" * 200)[:1000], "", reference) for row in rows)
