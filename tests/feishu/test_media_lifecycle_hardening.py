import asyncio
import hashlib
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.feishu.maintenance import purge_expired_feishu_media
from app.feishu.media import (
    FeishuMediaRejected,
    FeishuMediaResolver,
    feishu_media_content_lock,
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


class _MediaClient:
    def __init__(self, payload: bytes, app_id: str = "cli_a"):
        self.payload = payload
        self.app_id = app_id

    async def download_inbound_resource(self, **_kwargs):
        return self.payload


def _candidate(file_key: str = "opaque-key") -> FeishuInboundResourceCandidate:
    return FeishuInboundResourceCandidate(
        ordinal=0,
        resource_type="image",
        file_key=file_key,
        file_name="image.png",
    )


def _media_event(
    store: AutoReplyStore,
    number: int,
    *,
    app_id: str = "cli_a",
    file_key: str = "opaque-key",
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
    event = store.record_feishu_event(
        message,
        eligibility_status="eligible",
        store_body=True,
        enqueue_eligible=False,
        media_candidates=[_candidate(file_key)],
    )
    [asset] = store.list_feishu_media_assets(event_record_id=event.id)
    return event, asset


def _resolve(
    store: AutoReplyStore,
    workspace: Path,
    number: int,
    *,
    payload: bytes = PNG,
    app_id: str = "cli_a",
):
    event, _ = _media_event(
        store, number, app_id=app_id, file_key=f"key-{number}"
    )
    resolver = FeishuMediaResolver(
        store=store,
        client=_MediaClient(payload, app_id=app_id),
        workspace=workspace,
    )
    [resolution] = asyncio.run(resolver.resolve_pending(limit=1))
    assert resolution.asset.status == "ready"
    return event, resolution.asset


def _age_ready(store: AutoReplyStore, *ids: int) -> None:
    placeholders = ",".join("?" for _ in ids)
    with store._connect() as db:
        db.execute(
            f"update feishu_media_assets set ready_at=? where id in ({placeholders})",
            ("2026-01-01 00:00:00", *ids),
        )


def _now() -> datetime:
    return datetime(2026, 7, 22, tzinfo=timezone.utc)


def _lock_worker(workspace: str, child) -> None:
    child.send("started")
    digest = "a" * 64
    with feishu_media_content_lock(Path(workspace), "cli_a", digest):
        child.send("acquired")
    child.close()


def test_content_lock_is_interprocess_and_app_digest_scoped(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_lock_worker, args=(str(workspace), child))

    with feishu_media_content_lock(workspace, "cli_a", "a" * 64):
        process.start()
        child.close()
        assert parent.poll(2)
        assert parent.recv() == "started"
        assert not parent.poll(0.2)

    assert parent.poll(3)
    assert parent.recv() == "acquired"
    process.join(3)
    assert process.exitcode == 0
    lock_path = (
        workspace
        / ".ceo-agent"
        / "feishu-media"
        / ".locks"
        / hashlib.sha256(b"cli_a").hexdigest()
        / "aa"
    )
    assert lock_path.is_file()
    assert lock_path.stat().st_mode & 0o777 == 0o600

    for number in range(300):
        with feishu_media_content_lock(
            workspace, "cli_a", f"{number:064x}"
        ):
            pass
    assert len(list(lock_path.parent.iterdir())) <= 256


def test_publish_fsync_precedes_ready_commit_and_failed_cas_preserves_repair(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, first = _resolve(store, workspace, 1)
    content = workspace / first.relative_path
    content.unlink()

    _media_event(store, 2, file_key="repair-key")
    resolver = FeishuMediaResolver(
        store=store,
        client=_MediaClient(PNG),
        workspace=workspace,
    )
    fsynced = []
    from app.feishu import media as media_module

    original_fsync = media_module._fsync_directory

    def record_fsync(path):
        original_fsync(path)
        fsynced.append(Path(path))

    monkeypatch.setattr(media_module, "_fsync_directory", record_fsync)
    original_ready = store.mark_feishu_media_ready

    def fail_after_publish(*args, **kwargs):
        assert content.is_file()
        assert content.parent in fsynced
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(store, "mark_feishu_media_ready", fail_after_publish)
    [resolution] = asyncio.run(resolver.resolve_pending(limit=1))
    assert resolution.asset.status == "rejected"
    assert content.read_bytes() == PNG

    monkeypatch.setattr(store, "mark_feishu_media_ready", original_ready)


def test_image_validation_rejects_bombs_and_animation_but_keeps_static_png():
    assert verified_media_mime(PNG, resource_type="image") == "image/png"

    oversized_png = bytearray(PNG)
    oversized_png[16:20] = (20_000).to_bytes(4, "big")
    with pytest.raises(FeishuMediaRejected, match="image_dimensions_exceeded"):
        verified_media_mime(bytes(oversized_png), resource_type="image")

    animation_chunk = (
        (8).to_bytes(4, "big")
        + b"acTL"
        + (2).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + b"\x00\x00\x00\x00"
    )
    animated_png = PNG[:-12] + animation_chunk + PNG[-12:]
    with pytest.raises(FeishuMediaRejected, match="animated_image_unsupported"):
        verified_media_mime(animated_png, resource_type="image")

    with pytest.raises(FeishuMediaRejected, match="animated_image_unsupported"):
        verified_media_mime(b"GIF89a" + b"\x00" * 32, resource_type="image")

    vp8x = b"VP8X" + (10).to_bytes(4, "little") + b"\x02" + b"\x00" * 9
    animated_webp = b"RIFF" + (4 + len(vp8x)).to_bytes(4, "little") + b"WEBP" + vp8x
    with pytest.raises(FeishuMediaRejected, match="animated_image_unsupported"):
        verified_media_mime(animated_webp, resource_type="image")

    oversized_canvas = (
        b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (19_999).to_bytes(3, "little")
        + b"\x00\x00\x00"
    )
    oversized_webp = (
        b"RIFF"
        + (4 + len(oversized_canvas)).to_bytes(4, "little")
        + b"WEBP"
        + oversized_canvas
    )
    with pytest.raises(FeishuMediaRejected, match="image_dimensions_exceeded"):
        verified_media_mime(oversized_webp, resource_type="image")

    oversized_jpeg = (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + (1).to_bytes(2, "big")
        + (20_000).to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )
    with pytest.raises(FeishuMediaRejected, match="image_dimensions_exceeded"):
        verified_media_mime(oversized_jpeg, resource_type="image")


def test_retention_terminates_pending_but_grants_active_processing_a_grace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    pending_event, pending_asset = _resolve(store, workspace, 1)
    _age_ready(store, pending_asset.id)
    pending_task_id = store.attach_feishu_event_reply_task(
        pending_event.id
    ).reply_task_id
    assert pending_task_id

    pending = purge_expired_feishu_media(
        store, retention_days=30, workspace=workspace, now=_now()
    )
    assert pending.purged_assets == 1
    assert not (workspace / pending_asset.relative_path).exists()
    [pending_task] = store.list_reply_tasks(channel="feishu")
    assert pending_task.status == "failed"
    assert pending_task.error == "feishu_media_retention_expired"

    active_event, active_asset = _resolve(store, workspace, 2)
    _age_ready(store, active_asset.id)
    active_task_id = store.attach_feishu_event_reply_task(
        active_event.id
    ).reply_task_id
    assert active_task_id

    [task] = store.claim_reply_tasks(
        1, channel="feishu", feishu_app_id="cli_a"
    )
    processing = purge_expired_feishu_media(
        store, retention_days=30, workspace=workspace, now=_now()
    )
    assert processing.purged_assets == 0
    assert (workspace / active_asset.relative_path).is_file()
    assert store.complete_processing_reply_task(
        task.id, channel="feishu", lease_token=task.lease_token
    )

    completed = purge_expired_feishu_media(
        store, retention_days=30, workspace=workspace, now=_now()
    )
    assert completed.purged_assets == 1
    assert completed.deleted_files == 1
    assert not (workspace / active_asset.relative_path).exists()


def test_retention_terminates_stale_processing_after_bounded_grace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    event, asset = _resolve(store, workspace, 1)
    _age_ready(store, asset.id)
    store.attach_feishu_event_reply_task(event.id)
    [task] = store.claim_reply_tasks(
        1, channel="feishu", feishu_app_id="cli_a"
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at='2026-07-22T08:00:00+00:00' where id=?",
            (task.id,),
        )

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.purged_assets == 1
    assert result.deleted_files == 1
    [failed] = store.list_reply_tasks(channel="feishu")
    assert failed.status == "failed"
    assert failed.error == "feishu_media_retention_expired"


def test_shared_reference_finalizes_row_and_failed_rows_do_not_block_queue(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, first = _resolve(store, workspace, 1)
    _, second = _resolve(store, workspace, 2)
    assert first.relative_path == second.relative_path
    _age_ready(store, first.id)

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_now(),
        batch_limit=1,
        max_batches=1,
    )
    assert result.purged_assets == 1
    assert store.get_feishu_media_asset(first.id).relative_path == ""
    assert store.get_feishu_media_asset(second.id).relative_path
    assert (workspace / second.relative_path).is_file()

    _, third = _resolve(store, workspace, 3, payload=PNG + b"third")
    _, fourth = _resolve(store, workspace, 4, payload=PNG + b"fourth")
    _age_ready(store, third.id, fourth.id)
    marked = store.mark_feishu_media_purged_before(
        "2026-06-01T00:00:00+00:00", batch_limit=2
    )
    assert [row.id for row in marked] == [third.id, fourth.id]
    store.record_feishu_media_purge_failure(
        third.id, app_id="cli_a", error_code="content_hash_mismatch"
    )
    [next_asset] = store.list_feishu_media_pending_purge(limit=1)
    assert next_asset.id == fourth.id


def test_retention_expires_old_keys_but_not_an_active_download(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, pending = _media_event(store, 1, file_key="pending-secret")
    _, stale = _media_event(store, 2, file_key="stale-secret")
    _, active = _media_event(store, 3, file_key="active-secret")
    with store._connect() as db:
        db.execute(
            "update feishu_media_assets set created_at='2026-01-01 00:00:00'"
        )
        db.execute(
            """
            update feishu_media_assets
            set status='downloading', lease_token='stale-lease',
                locked_at='2026-07-22T11:00:00+00:00'
            where id=?
            """,
            (stale.id,),
        )
        db.execute(
            """
            update feishu_media_assets
            set status='downloading', lease_token='active-lease',
                locked_at='2026-07-22T11:59:00+00:00'
            where id=?
            """,
            (active.id,),
        )

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.expired_keys == 2
    for asset_id in (pending.id, stale.id):
        expired = store.get_feishu_media_asset(asset_id)
        assert expired.status == "rejected"
        assert expired.file_key == ""
        assert expired.error_code == "retention_expired"
    still_active = store.get_feishu_media_asset(active.id)
    assert still_active.status == "downloading"
    assert still_active.file_key == "active-secret"
    audit = "\n".join(
        row.detail
        for row in store.list_feishu_audit_events(entity_type="media_asset")
    )
    assert "pending-secret" not in audit
    assert "stale-secret" not in audit
    assert "active-secret" not in audit


def test_orphan_sweep_is_bounded_aged_referenced_and_no_follow(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    _, referenced = _resolve(store, workspace, 1)
    app_digest = hashlib.sha256(b"cli_a").hexdigest()
    old_timestamp = (_now() - timedelta(days=31)).timestamp()
    referenced_path = workspace / referenced.relative_path
    os.utime(referenced_path, (old_timestamp, old_timestamp))

    orphan_data = PNG + b"orphan"
    orphan_digest = hashlib.sha256(orphan_data).hexdigest()
    shard = (
        workspace
        / ".ceo-agent"
        / "feishu-media"
        / app_digest
        / orphan_digest[:2]
    )
    shard.mkdir(mode=0o700, parents=True, exist_ok=True)
    orphan = shard / orphan_digest
    orphan.write_bytes(orphan_data)
    temp = shard / f".{orphan_digest}.{'b' * 32}.tmp"
    temp.write_bytes(b"partial")
    os.utime(orphan, (old_timestamp, old_timestamp))
    os.utime(temp, (old_timestamp, old_timestamp))

    fresh_data = PNG + b"fresh"
    fresh_digest = hashlib.sha256(fresh_data).hexdigest()
    fresh_shard = shard.parent / fresh_digest[:2]
    fresh_shard.mkdir(mode=0o700, exist_ok=True)
    fresh = fresh_shard / fresh_digest
    fresh.write_bytes(fresh_data)

    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    symlink_digest = hashlib.sha256(b"outside").hexdigest()
    symlink_shard = shard.parent / symlink_digest[:2]
    symlink_shard.mkdir(mode=0o700, exist_ok=True)
    symlink = symlink_shard / symlink_digest
    symlink.symlink_to(outside)

    oversized_digest = "f" * 64
    oversized_shard = shard.parent / "ff"
    oversized_shard.mkdir(mode=0o700, exist_ok=True)
    oversized = oversized_shard / oversized_digest
    with oversized.open("wb") as handle:
        handle.truncate((20 * 1024 * 1024) + 1)
    os.utime(oversized, (old_timestamp, old_timestamp))

    result = purge_expired_feishu_media(
        store,
        retention_days=30,
        workspace=workspace,
        now=_now(),
        batch_limit=10,
    )

    assert result.deleted_orphans == 2
    assert result.failures >= 1
    assert not orphan.exists()
    assert not temp.exists()
    assert referenced_path.is_file()
    assert fresh.is_file()
    assert symlink.is_symlink()
    assert oversized.is_file()
    assert outside.read_bytes() == b"outside"
