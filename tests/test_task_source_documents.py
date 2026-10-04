import hashlib
import json

import pytest

from app.task_source_documents import source_document_key


SOURCE = {
    "source_type": "message",
    "source_ref": "m:1",
    "source_time": "2026-10-04T12:00:00+08:00",
    "conversation_id": "c1",
    "author_user_id": "u1",
    "author_name": "张三",
    "author_kind": "human",
    "evidence_text": "李四负责材料，王五负责商务。\n",
}


def test_source_document_key_is_exact_utf8_json_identity():
    encoded = json.dumps(list(SOURCE.values()), ensure_ascii=False, separators=(",", ":"))
    assert source_document_key(**SOURCE) == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert source_document_key(**SOURCE) == source_document_key(**dict(SOURCE))


@pytest.mark.parametrize(
    "change",
    [
        {"source_ref": "m:2"},
        {"source_type": "meeting"},
        {"source_type": "memory_provenance"},
        {"source_type": "session_provenance"},
        {"source_time": "2026-10-04T04:00:00Z"},
        {"conversation_id": "c2"},
        {"author_user_id": "u2"},
        {"author_name": "李四"},
        {"author_kind": "system"},
        {"evidence_text": "李四已提交材料，王五负责商务。\n"},
        {"evidence_text": SOURCE["evidence_text"].strip()},
        {"evidence_text": " " + SOURCE["evidence_text"]},
    ],
)
def test_source_document_key_separates_every_source_version_field(change):
    assert source_document_key(**SOURCE) != source_document_key(**(SOURCE | change))
