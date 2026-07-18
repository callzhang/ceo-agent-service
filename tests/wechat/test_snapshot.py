import pytest

from app.wechat.snapshot import readonly_snapshot


def test_snapshot_copies_db_wal_shm_and_cleans_up(tmp_path):
    source = tmp_path / "message_0.db"
    source.write_bytes(b"db")
    source.with_name(source.name + "-wal").write_bytes(b"wal")
    source.with_name(source.name + "-shm").write_bytes(b"shm")

    with readonly_snapshot(source, temp_root=tmp_path / "snapshots") as snapshot:
        assert sorted(path.name for path in snapshot.parent.iterdir()) == [
            "message_0.db", "message_0.db-shm", "message_0.db-wal"
        ]
        snapshot.write_bytes(b"snapshot-only")

    assert source.read_bytes() == b"db"


def test_snapshot_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        with readonly_snapshot(tmp_path / "absent.db", temp_root=tmp_path / "snapshots"):
            pass
