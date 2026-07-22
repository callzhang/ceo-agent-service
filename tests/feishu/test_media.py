import asyncio
import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.feishu.maintenance import (
    purge_expired_feishu_events,
    purge_expired_feishu_media,
)
from app.feishu.media import (
    FeishuMediaRejected,
    FeishuMediaResolver,
    safe_media_name,
    verified_media_mime,
)
from app.feishu.models import (
    FeishuInboundMessage,
    FeishuInboundResourceCandidate,
)
from app.store import AutoReplyStore


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000"
    "b51c0c020000000b4944415478da6364f80f00010501012718e366"
    "0000000049454e44ae426082"
)


def _event(
    store: AutoReplyStore,
    number: int = 1,
    *,
    app_id: str = "cli_a",
    approved: bool = True,
    media_candidates=None,
):
    message = FeishuInboundMessage(
        event_id=f"evt_{app_id}_{number}",
        app_id=app_id,
        message_id=f"om_{app_id}_{number}",
        chat_id="oc_1",
        chat_type="group",
        sender_open_id="ou_1",
        message_type="image",
        mentioned_bot=True,
        body_text="[image]",
        normalized_summary="[image]",
        event_create_time=f"2026-07-22T10:00:{number:02d}+08:00",
    )
    return store.record_feishu_event(
        message,
        eligibility_status="eligible" if approved else "scope_pending",
        reject_reason="" if approved else "scope_pending",
        store_body=approved,
        enqueue_eligible=False,
        media_candidates=(
            list(media_candidates or [_candidate()]) if approved else None
        ),
    )


def _candidate(
    ordinal: int = 0,
    *,
    file_key: str = "file_key_secret",
    resource_type: str = "image",
    file_name: str = "photo.png",
):
    return FeishuInboundResourceCandidate(
        ordinal=ordinal,
        resource_type=resource_type,
        file_key=file_key,
        file_name=file_name,
    )


def _insert(store, event, candidates=None):
    return store.insert_feishu_media_assets(
        event.id,
        app_id=event.app_id,
        message_id=event.message_id,
        candidates=candidates or [_candidate()],
    )


class FakeMediaClient:
    def __init__(self, payload=PNG, *, app_id="cli_a"):
        self.app_id = app_id
        self.payload = payload
        self.calls = []

    async def download_inbound_resource(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def test_resolver_rejects_resource_limit_above_normalized_contract(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")

    with pytest.raises(ValueError, match="between 1 and 8"):
        FeishuMediaResolver(
            store=store,
            client=FakeMediaClient(),
            workspace=workspace,
            max_event_resources=9,
        )

    event = _event(store)
    with pytest.raises(ValueError, match="between 1 and 8"):
        store.insert_feishu_media_assets(
            event.id,
            app_id=event.app_id,
            message_id=event.message_id,
            candidates=[_candidate()],
            max_event_resources=9,
        )


def test_schema_and_approved_only_plaintext_key_retention(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    approved = _event(store)
    [asset] = _insert(store, approved)

    assert asset.status == "pending"
    assert asset.file_key == "file_key_secret"
    assert "file_key_secret" not in repr(asset)
    assert asset.file_key_sha256 == hashlib.sha256(
        b"file_key_secret"
    ).hexdigest()
    with store._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(feishu_media_assets)")
        }
        indexes = {
            row["name"]
            for row in db.execute("pragma index_list(feishu_media_assets)")
        }
    assert {
        "event_record_id",
        "app_id",
        "message_id",
        "ordinal",
        "resource_type",
        "file_key",
        "file_key_sha256",
        "lease_token",
        "relative_path",
        "sha256",
    } <= columns
    assert "idx_feishu_media_assets_claim" in indexes

    rejected = _event(store, 2, approved=False)
    with pytest.raises(PermissionError, match="approved media event"):
        _insert(store, rejected)
    assert store.list_feishu_media_assets(event_record_id=rejected.id) == []


def test_insert_is_idempotent_but_rejects_candidate_identity_drift(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store)

    first = _insert(store, event)
    duplicate = _insert(store, event)

    assert duplicate == first
    with pytest.raises(ValueError, match="replay does not match"):
        _insert(store, event, [_candidate(file_key="other_key")])


def test_concurrent_claim_has_one_lease_owner_and_is_app_bound(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store)
    _insert(store, event)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(lambda _: store.claim_feishu_media_asset("cli_a"), range(8))
        )

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0].status == "downloading"
    assert claimed[0].lease_token
    assert store.claim_feishu_media_asset("cli_other") is None


def test_ready_transition_binds_every_identity_and_clears_file_key(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store)
    _insert(store, event)
    claim = store.claim_feishu_media_asset("cli_a")
    assert claim is not None
    digest = hashlib.sha256(PNG).hexdigest()
    relative_path = (
        ".ceo-agent/feishu-media/"
        + hashlib.sha256(b"cli_a").hexdigest()
        + f"/{digest[:2]}/{digest}"
    )

    with pytest.raises(ValueError, match="lease or identity"):
        store.mark_feishu_media_ready(
            claim.id,
            event_record_id=claim.event_record_id,
            app_id=claim.app_id,
            message_id=claim.message_id,
            file_key="wrong_key",
            resource_type=claim.resource_type,
            lease_token=claim.lease_token,
            relative_path=relative_path,
            mime_type="image/png",
            size_bytes=len(PNG),
            sha256=digest,
        )

    ready, enqueue = store.mark_feishu_media_ready(
        claim.id,
        event_record_id=claim.event_record_id,
        app_id=claim.app_id,
        message_id=claim.message_id,
        file_key=claim.file_key,
        resource_type=claim.resource_type,
        lease_token=claim.lease_token,
        relative_path=relative_path,
        mime_type="image/png",
        size_bytes=len(PNG),
        sha256=digest,
    )
    assert ready.status == "ready"
    assert ready.file_key == ""
    assert ready.relative_path == relative_path
    assert enqueue is True
    assert store.feishu_media_event_ready_for_enqueue(
        event.id, app_id=event.app_id, message_id=event.message_id
    )


def test_resolver_stores_private_content_addressed_files_and_deduplicates(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    first_event = _event(
        store, 1, media_candidates=[_candidate(file_key="key_one")]
    )
    second_event = _event(
        store, 2, media_candidates=[_candidate(file_key="key_two")]
    )
    _insert(store, first_event, [_candidate(file_key="key_one")])
    _insert(store, second_event, [_candidate(file_key="key_two")])
    client = FakeMediaClient()
    resolver = FeishuMediaResolver(
        store=store, client=client, workspace=workspace
    )

    first, second = asyncio.run(resolver.resolve_pending(limit=2))

    assert first.asset.status == second.asset.status == "ready"
    assert first.asset.relative_path == second.asset.relative_path
    stored = workspace / first.asset.relative_path
    assert stored.read_bytes() == PNG
    assert os.stat(stored).st_mode & 0o777 == 0o600
    assert client.calls[0] == {
        "app_id": "cli_a",
        "message_id": first_event.message_id,
        "file_key": "key_one",
        "resource_type": "image",
        "max_bytes": 20 * 1024 * 1024 + 1,
    }


def test_existing_content_address_leaf_symlink_is_never_followed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    first_event = _event(
        store, 1, media_candidates=[_candidate(file_key="key_one")]
    )
    _insert(store, first_event, [_candidate(file_key="key_one")])
    resolver = FeishuMediaResolver(
        store=store, client=FakeMediaClient(), workspace=workspace
    )
    [first] = asyncio.run(resolver.resolve_pending(limit=1))
    retained = workspace / first.asset.relative_path
    outside = tmp_path / "outside.bin"
    outside.write_bytes(PNG)
    retained.unlink()
    retained.symlink_to(outside)

    second_event = _event(
        store, 2, media_candidates=[_candidate(file_key="key_two")]
    )
    _insert(store, second_event, [_candidate(file_key="key_two")])
    [second] = asyncio.run(resolver.resolve_pending(limit=1))

    assert second.asset.status == "rejected"
    assert second.asset.error_code == "unsafe_media_destination"
    assert retained.is_symlink()
    assert outside.read_bytes() == PNG


def test_per_event_limit_is_atomic_under_multiple_assets(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(
        store,
        media_candidates=[
            _candidate(0, file_key="first"),
            _candidate(1, file_key="second"),
        ],
    )
    _insert(
        store,
        event,
        [
            _candidate(0, file_key="first"),
            _candidate(1, file_key="second"),
        ],
    )
    resolver = FeishuMediaResolver(
        store=store,
        client=FakeMediaClient(),
        workspace=workspace,
        max_resource_bytes=len(PNG),
        max_event_bytes=len(PNG) * 2 - 1,
    )

    first, second = asyncio.run(resolver.resolve_pending(limit=2))

    assert first.asset.status == "ready"
    assert second.asset.status == "rejected"
    assert second.asset.error_code == "event_too_large"
    assert second.event_ready_for_enqueue is True
    assert sum(
        asset.size_bytes
        for asset in store.list_feishu_media_assets(event_record_id=event.id)
    ) == len(PNG)


def test_mime_magic_mismatch_is_rejected_without_file_or_secret_audit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store)
    _insert(store, event)
    client = FakeMediaClient((PNG, "image/jpeg"))
    resolver = FeishuMediaResolver(
        store=store, client=client, workspace=workspace
    )

    [result] = asyncio.run(resolver.resolve_pending())

    assert result.asset.status == "rejected"
    assert result.asset.error_code == "mime_mismatch"
    assert result.asset.file_key == ""
    assert not resolver.media_root.exists()
    audits = store.list_feishu_audit_events(entity_type="media_asset")
    serialized = "\n".join(
        f"{row.actor} {row.detail} {row.entity_id}" for row in audits
    )
    assert "file_key_secret" not in serialized
    assert str(workspace) not in serialized
    assert ".ceo-agent" not in serialized


def test_filename_path_and_symlink_attacks_fail_closed(tmp_path):
    with pytest.raises(FeishuMediaRejected, match="unsafe_file_name"):
        safe_media_name("../outside.png", resource_type="image")

    store = AutoReplyStore(tmp_path / "db.sqlite3")
    invalid_candidate = SimpleNamespace(
        ordinal=0,
        resource_type="image",
        file_key="hidden_key",
        file_name="../outside.png",
        duration_ms=0,
        role="content",
    )
    event = _event(store, media_candidates=[invalid_candidate])
    [invalid_name] = _insert(
        store,
        event,
        [invalid_candidate],
    )
    assert invalid_name.status == "rejected"
    assert invalid_name.error_code == "unsafe_file_name"
    assert invalid_name.file_key == ""

    second = _event(store, 2)
    _insert(store, second)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".ceo-agent").symlink_to(outside, target_is_directory=True)
    resolver = FeishuMediaResolver(
        store=store, client=FakeMediaClient(), workspace=workspace
    )
    [result] = asyncio.run(resolver.resolve_pending())
    assert result.asset.status == "rejected"
    assert result.asset.error_code == "unsafe_media_directory"
    assert list(outside.iterdir()) == []


def test_sticker_is_terminally_rejected_without_calling_download_api(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    sticker_candidate = _candidate(
        resource_type="sticker", file_name="sticker"
    )
    event = _event(store, media_candidates=[sticker_candidate])
    _insert(
        store,
        event,
        [sticker_candidate],
    )
    client = FakeMediaClient()
    resolver = FeishuMediaResolver(
        store=store, client=client, workspace=workspace
    )

    [result] = asyncio.run(resolver.resolve_pending())

    assert result.asset.status == "rejected"
    assert result.asset.error_code == "sticker_download_unsupported"
    assert client.calls == []


def test_event_retention_blocks_live_media_and_cascades_rejected_metadata(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    live_event = _event(store, 1)
    rejected_candidate = SimpleNamespace(
        ordinal=0,
        resource_type="image",
        file_key="rejected_key",
        file_name="../unsafe.png",
        duration_ms=0,
        role="content",
    )
    rejected_event = _event(
        store, 2, media_candidates=[rejected_candidate]
    )
    _insert(store, live_event)
    _insert(
        store,
        rejected_event,
        [rejected_candidate],
    )

    deleted = store.purge_feishu_events_before("2099-01-01T00:00:00+00:00")

    assert deleted == 1
    assert store.get_feishu_event(live_event.id) is not None
    assert store.get_feishu_event(rejected_event.id) is None
    assert store.list_feishu_media_assets(event_record_id=live_event.id)
    assert store.list_feishu_media_assets(event_record_id=rejected_event.id) == []


def test_stale_claim_recovery_is_cas_safe_and_audit_is_key_free(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store)
    _insert(store, event)
    claimed = store.claim_feishu_media_asset(
        "cli_a", now="2026-07-22T10:00:00+00:00"
    )
    assert claimed is not None

    assert (
        store.recover_stale_feishu_media_assets(
            app_id="cli_a",
            stale_after_seconds=300,
            now="2026-07-22T10:04:59+00:00",
        )
        == 0
    )
    assert (
        store.recover_stale_feishu_media_assets(
            app_id="cli_a",
            stale_after_seconds=300,
            now="2026-07-22T10:05:00+00:00",
        )
        == 1
    )
    reclaimed = store.claim_feishu_media_asset("cli_a")
    assert reclaimed is not None
    assert reclaimed.lease_token != claimed.lease_token
    audits = store.list_feishu_audit_events(entity_type="media_asset")
    assert all("file_key_secret" not in row.detail for row in audits)


def test_verified_mime_rejects_resource_kind_confusion():
    assert verified_media_mime(PNG, resource_type="image") == "image/png"
    with pytest.raises(FeishuMediaRejected, match="resource_type_mismatch"):
        verified_media_mime(PNG, resource_type="audio")


def _resolve_ready_asset(
    store, workspace, event, *, payload=PNG, file_key="file_key_secret"
):
    _insert(store, event, [_candidate(file_key=file_key)])
    resolver = FeishuMediaResolver(
        store=store,
        client=FakeMediaClient(payload, app_id=event.app_id),
        workspace=workspace,
    )
    [result] = asyncio.run(resolver.resolve_pending())
    assert result.asset.status == "ready"
    return result.asset


def _age_assets(store, *asset_ids, ready_at="2026-01-01 00:00:00"):
    placeholders = ",".join("?" for _ in asset_ids)
    with store._connect() as db:
        db.execute(
            f"update feishu_media_assets set ready_at=? where id in ({placeholders})",
            (ready_at, *asset_ids),
        )


def _maintenance_now():
    return datetime(2026, 7, 22, tzinfo=timezone.utc)


def test_media_purge_defers_shared_content_until_no_ready_reference(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    first = _resolve_ready_asset(
        store,
        workspace,
        _event(store, 1, media_candidates=[_candidate(file_key="one")]),
        file_key="one",
    )
    second = _resolve_ready_asset(
        store,
        workspace,
        _event(store, 2, media_candidates=[_candidate(file_key="two")]),
        file_key="two",
    )
    assert first.relative_path == second.relative_path
    _age_assets(store, first.id)

    first_run = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
        batch_limit=1,
        max_batches=1,
    )

    assert first_run.purged_assets == 1
    assert first_run.deleted_files == first_run.failures == 0
    assert (workspace / first.relative_path).is_file()
    persisted_first = store.get_feishu_media_asset(first.id)
    assert persisted_first.status == "purged"
    assert persisted_first.relative_path == ""
    assert store.get_feishu_media_asset(second.id).status == "ready"

    _age_assets(store, second.id)
    second_run = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
        batch_limit=1,
        max_batches=3,
    )
    assert second_run.purged_assets == 1
    assert second_run.deleted_files == 1
    assert not (workspace / first.relative_path).exists()
    assert all(
        asset.status == "purged" and asset.relative_path == ""
        for asset in (
            store.get_feishu_media_asset(first.id),
            store.get_feishu_media_asset(second.id),
        )
    )


def test_media_purge_rejects_symlink_and_path_drift_without_leaking_audit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(PNG)
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    symlink_asset = _resolve_ready_asset(
        store,
        workspace,
        _event(
            store,
            1,
            media_candidates=[_candidate(file_key="secret-key-789")],
        ),
        file_key="secret-key-789",
    )
    _age_assets(store, symlink_asset.id)
    stored_path = workspace / symlink_asset.relative_path
    stored_path.unlink()
    stored_path.symlink_to(outside)

    symlink_result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
    )

    assert symlink_result.failures == 1
    assert symlink_result.deleted_files == 0
    assert outside.read_bytes() == PNG
    assert stored_path.is_symlink()
    persisted = store.get_feishu_media_asset(symlink_asset.id)
    assert persisted.status == "purged"
    assert persisted.relative_path == symlink_asset.relative_path
    assert persisted.error_code == "symlink_rejected"

    drift_asset = _resolve_ready_asset(
        store,
        workspace,
        _event(store, 2, media_candidates=[_candidate(file_key="drift")]),
        payload=PNG + b"2",
        file_key="drift",
    )
    _age_assets(store, drift_asset.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update feishu_media_assets set relative_path='../outside.bin' where id=?",
            (drift_asset.id,),
        )
    drift_result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
    )
    assert drift_result.failures >= 1
    assert outside.read_bytes() == PNG
    drifted = store.get_feishu_media_asset(drift_asset.id)
    assert drifted.status == "purged"
    assert drifted.error_code == "path_validation_failed"
    audit_text = "\n".join(
        f"{event.event_type} {event.detail}"
        for event in store.list_feishu_audit_events(entity_type="media_asset")
    )
    assert str(workspace) not in audit_text
    assert "../outside.bin" not in audit_text
    assert "secret-key-789" not in audit_text


def test_missing_file_and_crash_after_mark_are_recoverable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    missing = _resolve_ready_asset(store, workspace, _event(store, 1))
    _age_assets(store, missing.id)
    (workspace / missing.relative_path).unlink()

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
    )

    assert result.purged_assets == 1
    assert result.deleted_files == result.failures == 0
    assert store.get_feishu_media_asset(missing.id).relative_path == ""

    crashed = _resolve_ready_asset(
        store, workspace, _event(store, 2), payload=PNG + b"crash"
    )
    _age_assets(store, crashed.id)
    [marked] = store.mark_feishu_media_purged_before(
        "2026-06-01T00:00:00+00:00", batch_limit=1
    )
    assert marked.id == crashed.id
    assert (workspace / crashed.relative_path).exists()

    recovered = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_maintenance_now(),
    )
    assert recovered.purged_assets == 0
    assert recovered.deleted_files == 1
    assert store.get_feishu_media_asset(crashed.id).relative_path == ""


def test_media_purge_preserves_pending_and_respects_batch_and_app_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    ready_a1 = _resolve_ready_asset(
        store,
        workspace,
        _event(store, 1, media_candidates=[_candidate(file_key="a1")]),
        payload=PNG + b"a1",
        file_key="a1",
    )
    ready_a2 = _resolve_ready_asset(
        store,
        workspace,
        _event(store, 2, media_candidates=[_candidate(file_key="a2")]),
        payload=PNG + b"a2",
        file_key="a2",
    )
    ready_b = _resolve_ready_asset(
        store,
        workspace,
        _event(
            store,
            3,
            app_id="cli_b",
            media_candidates=[_candidate(file_key="b")],
        ),
        payload=PNG + b"b",
        file_key="b",
    )
    _age_assets(store, ready_a1.id, ready_a2.id, ready_b.id)
    downloading_event = _event(
        store, 4, media_candidates=[_candidate(file_key="downloading")]
    )
    [downloading] = _insert(
        store, downloading_event, [_candidate(file_key="downloading")]
    )
    claimed = store.claim_feishu_media_asset("cli_a")
    assert claimed.id == downloading.id
    downloading = claimed
    pending_event = _event(
        store, 5, media_candidates=[_candidate(file_key="pending")]
    )
    [pending] = _insert(store, pending_event, [_candidate(file_key="pending")])

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        app_id="cli_a",
        workspace=workspace,
        now=_maintenance_now(),
        batch_limit=1,
        max_batches=1,
    )

    assert result.purged_assets == 1
    assert result.more_may_remain is True
    assert store.get_feishu_media_asset(downloading.id).status == "downloading"
    assert store.get_feishu_media_asset(pending.id).status == "pending"
    assert sum(
        asset.status == "purged"
        for asset in (
            store.get_feishu_media_asset(ready_a1.id),
            store.get_feishu_media_asset(ready_a2.id),
        )
    ) == 1
    assert store.get_feishu_media_asset(ready_b.id).status == "ready"
    assert (workspace / ready_b.relative_path).is_file()


def test_combined_maintenance_purges_media_before_event_retention(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event = _event(store, 1)
    asset = _resolve_ready_asset(store, workspace, event)
    _age_assets(store, asset.id)
    with store._connect() as db:
        db.execute(
            "update feishu_events set created_at='2026-01-01 00:00:00' where id=?",
            (event.id,),
        )

    result = purge_expired_feishu_events(
        store,
        retention_days=30,
        media_retention_days=30,
        media_workspace=workspace,
        now=_maintenance_now(),
    )

    assert result.purged_assets == 1
    assert result.deleted_files == 1
    assert result.media_failures == 0
    assert result.deleted_events == 1
    assert store.get_feishu_event(event.id) is None
    assert not (workspace / asset.relative_path).exists()
