import copy
from dataclasses import replace

import pytest

from app.agent_turn_runner import _validate_runtime_reference_domain_result
from app.reviewed_sources import capture_candidate_sources, changed_candidate_sources
from tests.test_reviewed_orchestration import candidate, setup


def resource_context(tmp_path):
    _, _, context = setup(tmp_path)
    raw = {
        "openConversationId": "conversation",
        "openMessageId": context.trigger_message_id,
        "quotedMessage": {
            "openConversationId": "conversation",
            "openMessageId": "quoted",
            "content": "test results",
            "resources": [{
                "resourceId": "media",
                "resourceIdType": "mediaId",
                "resourceType": "image",
                "url": "https://example.com/image?signature=private-transport",
                "expireTimeMillis": 123,
                "downloadCommand": "provider transport instruction",
            }],
        },
    }
    return replace(context, trigger_raw_payload=raw)


class Reader:
    digest = "a" * 64

    def read_message_resource_digest(self, reference):
        return {**reference, "size_bytes": 8, "sha256": self.digest}


@pytest.mark.parametrize("raw", ["Provider-rendered text", '{"notice":"Provider text"}'])
def test_non_native_source_text_remains_unchanged_and_validated(tmp_path, raw):
    _, _, context = setup(tmp_path)
    context = replace(context, trigger_raw_payload=raw)
    prepared = capture_candidate_sources(candidate(), context, Reader())
    assert prepared.source_bindings[0].value["trigger_raw_payload"] == raw
    assert len(prepared.source_bindings) == 1
    _validate_runtime_reference_domain_result(prepared)


def test_serialized_signed_resource_is_not_treated_as_verified_native_data(tmp_path):
    import json

    context = resource_context(tmp_path)
    raw = json.dumps(context.trigger_raw_payload)
    prepared = capture_candidate_sources(candidate(), replace(context, trigger_raw_payload=raw), Reader())
    assert prepared.source_bindings[0].value["trigger_raw_payload"] == raw
    with pytest.raises(ValueError):
        _validate_runtime_reference_domain_result(prepared)


def test_verified_resource_identity_and_content_replace_only_transport(tmp_path):
    context = resource_context(tmp_path)
    original = copy.deepcopy(context.trigger_raw_payload)
    reader = Reader()
    prepared = capture_candidate_sources(candidate(), context, reader)
    _validate_runtime_reference_domain_result(prepared)
    assert context.trigger_raw_payload == original
    resource = prepared.source_bindings[0].value["trigger_raw_payload"]["quotedMessage"]["resources"][0]
    assert resource == {"resourceId": "media", "resourceIdType": "mediaId", "resourceType": "image"}
    [binding] = [b for b in prepared.source_bindings if b.provider == "dingtalk-message-resource"]
    assert binding.value["message_id"] == "quoted"
    assert binding.value["conversation_id"] == "conversation"
    assert binding.value["sha256"] == "a" * 64
    assert not changed_candidate_sources(prepared, context, reader)
    reader.digest = "b" * 64
    [change] = changed_candidate_sources(prepared, context, reader)
    assert change["provider"] == "dingtalk-message-resource"


def test_transport_rotation_is_not_a_business_source_change(tmp_path):
    context = resource_context(tmp_path)
    reader = Reader()
    prepared = capture_candidate_sources(candidate(), context, reader)
    raw = copy.deepcopy(context.trigger_raw_payload)
    raw["quotedMessage"]["resources"][0]["url"] = "https://example.com/new?signature=rotated"
    raw["quotedMessage"]["resources"][0]["expireTimeMillis"] = 456
    assert not changed_candidate_sources(prepared, replace(context, trigger_raw_payload=raw), reader)


@pytest.mark.parametrize("field", ["resourceId", "resourceType"])
def test_resource_fact_changes_still_invalidate_candidate(tmp_path, field):
    context = resource_context(tmp_path)
    reader = Reader()
    prepared = capture_candidate_sources(candidate(), context, reader)
    raw = copy.deepcopy(context.trigger_raw_payload)
    raw["quotedMessage"]["resources"][0][field] = "changed"
    changes = changed_candidate_sources(prepared, replace(context, trigger_raw_payload=raw), reader)
    assert any(change["provider"] == "task_context" for change in changes)


def test_resource_without_reader_cannot_be_bound(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        capture_candidate_sources(candidate(), resource_context(tmp_path), None)


def test_signed_url_outside_native_resource_remains_rejected(tmp_path):
    context = resource_context(tmp_path)
    context.trigger_raw_payload["unrelated"] = "https://example.com/?signature=private"
    prepared = capture_candidate_sources(candidate(), context, Reader())
    with pytest.raises(ValueError):
        _validate_runtime_reference_domain_result(prepared)


def test_unknown_resource_fields_are_not_silently_dropped(tmp_path):
    context = resource_context(tmp_path)
    context.trigger_raw_payload["quotedMessage"]["resources"][0]["unknown"] = "new fact"
    with pytest.raises(ValueError):
        capture_candidate_sources(candidate(), context, Reader())


@pytest.mark.parametrize("field,value", [("openConversationId", "other"), ("openMessageId", "")])
def test_resource_requires_original_message_identity(tmp_path, field, value):
    context = resource_context(tmp_path)
    context.trigger_raw_payload["quotedMessage"][field] = value
    with pytest.raises(ValueError):
        capture_candidate_sources(candidate(), context, Reader())


def test_reader_cannot_substitute_resource_identity(tmp_path):
    class WrongReader(Reader):
        def read_message_resource_digest(self, reference):
            return {**super().read_message_resource_digest(reference), "resource_id": "other"}

    with pytest.raises(ValueError, match="identity mismatch"):
        capture_candidate_sources(candidate(), resource_context(tmp_path), WrongReader())


def test_authored_signed_url_remains_rejected(tmp_path):
    context = resource_context(tmp_path)
    proposed = candidate().model_copy(update={"summary": "https://example.com/image?signature=private-transport"})
    prepared = capture_candidate_sources(proposed, context, Reader())
    with pytest.raises(ValueError, match="sensitive"):
        _validate_runtime_reference_domain_result(prepared)


@pytest.mark.parametrize("failure", [None, "message", "resource", "unverified", "size", "escape", "symlink"])
def test_native_download_identity_size_and_containment(tmp_path, failure):
    import hashlib
    from app.dws_client import DwsClient

    reference = {"conversation_id": "conversation", "message_id": "quoted",
                 "resource_id": "media", "resource_id_type": "mediaId", "resource_type": "image"}
    roots = []

    def run_json(command, *, cwd):
        roots.append(cwd)
        assert command[1:3] == ["chat", "+messages-resource-download"]
        assert command[command.index("--message-id") + 1] == "quoted"
        file = cwd / "resource.jpg"
        file.write_bytes(b"verified image")
        payload = {"localPath": str(file), "messageId": "quoted", "resourceId": "media",
                   "messageVerified": True, "sizeBytes": file.stat().st_size}
        if failure == "message":
            payload["messageId"] = "other"
        elif failure == "resource":
            payload["resourceId"] = "other"
        elif failure == "unverified":
            payload["messageVerified"] = False
        elif failure == "size":
            payload["sizeBytes"] += 1
        elif failure == "escape":
            payload["localPath"] = str(tmp_path / "outside.jpg")
        elif failure == "symlink":
            link = cwd / "link.jpg"
            link.symlink_to(file)
            payload["localPath"] = str(link)
        return payload

    client = DwsClient()
    client.run_json = run_json
    if failure:
        with pytest.raises(ValueError):
            client.read_message_resource_digest(reference)
    else:
        assert client.read_message_resource_digest(reference) == {
            **reference, "size_bytes": len(b"verified image"),
            "sha256": hashlib.sha256(b"verified image").hexdigest(),
        }
    assert roots and all(not root.exists() for root in roots)
