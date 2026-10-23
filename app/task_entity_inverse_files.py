"""Explicit, migration-only Workbench attachment inverse file handoff."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


def _digest(path: Path) -> tuple[str, int]:
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
            size += len(chunk)
    return value.hexdigest(), size


def _file(path: Path, root: Path) -> None:
    root_info = os.lstat(root)
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("unsafe Workbench attachment managed root")
    path = Path(os.path.abspath(path))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Workbench attachment locator is outside its managed root") from exc
    cursor = root
    for component in path.relative_to(root).parts[:-1]:
        cursor = cursor / component
        info = os.lstat(cursor)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe Workbench attachment directory")
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Workbench attachment locator is not a regular file")


def _ensure_directory(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        try:
            os.mkdir(cursor, 0o700)
        except FileExistsError:
            pass
        info = os.lstat(cursor)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe Workbench attachment destination directory")


def capture_workbench_attachment_files(db, managed_root: str | Path) -> dict:
    """Snapshot canonical owners and verified file facts before SQL inverse."""
    root = Path(os.path.abspath(managed_root))
    rows = db.execute(
        "select id,workbench_task_id,storage_path,content_sha256,size_bytes "
        "from main.workbench_attachments order by id"
    ).fetchall()
    attachments = []
    for row in rows:
        path = Path(row["storage_path"])
        if not path.is_absolute():
            path = root.parent.parent / path
        path = Path(os.path.abspath(path))
        _file(path, root)
        digest, size = _digest(path)
        if digest != row["content_sha256"] or size != row["size_bytes"]:
            raise ValueError("Workbench attachment bytes differ from native metadata")
        attachments.append({
            "canonical_id": int(row["id"]), "parent_canonical_id": int(row["workbench_task_id"]),
            "native_path": str(path), "content_sha256": digest, "size_bytes": size,
        })
    return {"version": 1, "attachments": attachments}


def stage_workbench_attachment_inverse_files(
    db, managed_root: str | Path, snapshot: dict, inverse: dict,
) -> dict:
    """Stage source-compatible locators and update source payloads in caller txn.

    The caller commits only after this returns and then calls finalize. On a
    failed caller transaction, call rollback_staged_workbench_attachment_files.
    """
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Workbench inverse file staging requires caller-owned FK-off transaction")
    root = Path(os.path.abspath(managed_root))
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise ValueError("invalid Workbench attachment file snapshot")
    identity_rows = inverse["inverse_identity"].get("workbench_attachment_identities", [])
    identities = {row["canonical_id"]: row for row in identity_rows}
    snapshot_by_canonical = {row["canonical_id"]: row for row in snapshot["attachments"]}
    owner_by_canonical = {
        row["canonical_id"]: row for row in inverse["inverse_identity"]["owners"]
        if row["source_kind"] == "workbench_attachments"
    }
    work = {}
    from uuid import UUID
    for canonical_id, item in snapshot_by_canonical.items():
        owner = owner_by_canonical.get(canonical_id)
        identity = identities.get(canonical_id)
        if owner is None and identity is None:
            from app.storage_payload import TASK_RECORD_TYPES
            source_row = db.execute(
                "select id,parent_id,legacy_key from main.tasks where id=? and _kind=?",
                (canonical_id, TASK_RECORD_TYPES.code("workbench_attachments")),
            ).fetchone()
            if source_row is not None:
                owner = {
                    "source_physical_id": int(source_row[0]),
                    "canonical_id": canonical_id,
                }
        if owner is None and identity is None:
            raise ValueError("Workbench attachment owner edge is missing from inverse")
        if identity is None:
            from app.task20_definitions import task20_column
            source_row = db.execute(
                "select parent_id," + task20_column("workbench_attachments", "id")
                + " from main.tasks where id=?",
                (owner["source_physical_id"],),
            ).fetchone()
            if source_row is None or source_row[1] is None:
                raise ValueError("restored Workbench attachment row is missing")
            parent = db.execute(
                "select " + task20_column("workbench_tasks", "id")
                + " from main.tasks where id=?", (source_row[0],)
            ).fetchone()
            source_id = source_row[1]
            if (parent is None or not isinstance(source_id, str)
                    or not isinstance(parent[0], str)):
                continue
            try:
                source_id = str(UUID(source_id))
                parent_id = str(UUID(parent[0]))
            except ValueError:
                continue
            source_path = root / parent_id / source_id
            if Path(item["native_path"]) == source_path:
                continue
            identity = {
                "source_physical_id": owner["source_physical_id"],
                "canonical_id": canonical_id,
                "source_public_id": source_id,
                "is_new_native": False,
            }
            identities[canonical_id] = identity
            identity_rows.append(identity)
        work[canonical_id] = identity
    from app.task_lifecycle_storage import source_assignments

    entries = []
    created = []
    try:
      for item in (snapshot_by_canonical[key] for key in work):
        identity = work[item["canonical_id"]]
        source_id = identity["source_public_id"]
        restored = db.execute(
            "select parent_id from main.tasks where id=?",
            (identity["source_physical_id"],),
        ).fetchone()
        if restored is None or restored[0] is None:
            raise ValueError("restored Workbench attachment row is missing")
        parent = db.execute(
            "select legacy_key from main.tasks where id=?",
            (restored[0],),
        ).fetchone()
        if parent is None or not isinstance(parent[0], str):
            raise ValueError("restored Workbench attachment parent is missing")
        from uuid import UUID
        source_parent_public_id = parent[0]
        if str(UUID(source_parent_public_id)) != source_parent_public_id:
            raise ValueError("restored Workbench attachment parent is not source-compatible")
        if str(UUID(source_id)) != source_id:
            raise ValueError("restored Workbench attachment identity is not source-compatible")
        parent_target = next((owner["canonical_id"] for owner in inverse["inverse_identity"]["owners"]
                              if owner["source_kind"] == "workbench_tasks"
                              and owner["source_physical_id"] == int(restored[0])), int(restored[0]))
        if parent_target != item["parent_canonical_id"]:
            raise ValueError("restored Workbench attachment parent differs from native snapshot")
        destination = root / parent[0] / source_id
        old_path = Path(item["native_path"])
        _file(old_path, root)
        if destination != old_path:
            _ensure_directory(destination.parent, root)
            if destination.exists() or destination.is_symlink():
                raise ValueError("Workbench inverse destination already exists")
            os.link(old_path, destination, follow_symlinks=False)
            created.append(destination)
            _fsync_file(destination)
            _fsync_dir(destination.parent)
        digest, size = _digest(destination)
        if digest != item["content_sha256"] or size != item["size_bytes"]:
            raise ValueError("staged Workbench attachment differs from captured bytes")
        assignments = source_assignments(
            "workbench_attachments", {"storage_path": str(destination)}, {},
            payload_source="tasks.payload_json", source_alias="tasks",
        )
        db.execute(
            "update main.tasks set " + assignments.sql +
            " where id=?",
            (*assignments.parameters, identity["source_physical_id"]),
        )
        entries.append({
            "source_physical_id": identity["source_physical_id"],
            "canonical_id": item["canonical_id"],
            "parent_canonical_id": item["parent_canonical_id"],
            "source_parent_physical_id": int(restored[0]),
            "source_parent_public_id": source_parent_public_id,
            "source_public_id": source_id,
            "source_path": str(destination), "native_path": str(old_path),
            "content_sha256": digest, "size_bytes": size,
            "staged": destination != old_path,
        })
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
            _fsync_dir(path.parent)
        raise
    handoff = dict(inverse["inverse_identity"])
    if identity_rows:
        handoff["workbench_attachment_identities"] = identity_rows
    handoff["workbench_attachment_files"] = [
        {key: row[key] for key in ("source_physical_id", "canonical_id", "parent_canonical_id",
                                    "source_parent_physical_id", "source_parent_public_id", "source_path",
                                    "native_path", "source_public_id", "content_sha256", "size_bytes")}
        for row in entries
    ]
    from app.task_entity_schema import _inverse_identity_digest
    inverse["inverse_identity"] = handoff
    inverse["source_context"] = {
        "version": 1, "handoff_required": True,
        "handoff_sha256": _inverse_identity_digest(handoff),
    }
    return {"version": 1, "attachments": entries}


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validated_inverse_plan(plan: dict, inverse_identity: dict, root: Path) -> list[dict]:
    from app.task_entity_schema import _validate_inverse_identity
    handoff = _validate_inverse_identity(inverse_identity)
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ValueError("invalid Workbench attachment file plan")
    entries = plan.get("attachments")
    expected = list(handoff["workbench_attachment_files"].values())
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError("Workbench attachment file plan differs from identity handoff")
    by_source = {row.get("source_physical_id"): row for row in entries}
    if len(by_source) != len(entries) or set(by_source) != {row["source_physical_id"] for row in expected}:
        raise ValueError("duplicate or missing Workbench attachment file plan entry")
    identities = handoff["workbench_attachment_identities"]
    validated = []
    for item in expected:
        actual = by_source[item["source_physical_id"]]
        if (not isinstance(actual.get("staged"), bool)
                or any(actual.get(key) != value for key, value in item.items())):
            raise ValueError("Workbench attachment plan entry differs from identity handoff")
        identity = identities[item["source_physical_id"]]
        if identity["source_public_id"] != actual.get("source_public_id"):
            raise ValueError("Workbench attachment plan identity mismatch")
        source = Path(item["source_path"])
        native = Path(item["native_path"])
        if (not source.is_absolute() or not native.is_absolute()
                or not _path_under(source, root) or not _path_under(native, root)
                or source != root / item["source_parent_public_id"] / identity["source_public_id"]
                or native.parent != root / str(item["parent_canonical_id"])
                or not native.name):
            raise ValueError("Workbench attachment plan locator differs from managed-root identity")
        validated.append({**item, "staged": actual["staged"]})
    return validated


def finalize_workbench_attachment_inverse_files(db, plan: dict, inverse_identity: dict,
                                                managed_root: str | Path) -> None:
    """Remove only old locators after committed replacement readback."""
    if db.in_transaction:
        raise ValueError("Workbench inverse file finalization requires committed database state")
    root = Path(os.path.abspath(managed_root))
    entries = _validated_inverse_plan(plan, inverse_identity, root)
    from app.task20_definitions import task20_projection
    rows = []
    for item in entries:
        row = db.execute("select task_id,storage_path from ("
                         + task20_projection("workbench_attachments")
                         + ") where id=?", (str(item["source_public_id"]),)).fetchone()
        destination = Path(item["source_path"])
        if (row is None or row[1] != str(destination)
                or int(row[0]) != item["source_parent_physical_id"]):
            raise ValueError("committed Workbench attachment replacement does not match plan")
        _file(destination, root)
        if _digest(destination) != (item["content_sha256"], item["size_bytes"]):
            raise ValueError("committed Workbench attachment replacement failed readback")
        original = Path(item["native_path"])
        if os.path.lexists(original):
            _file(original, root)
            if _digest(original) != (item["content_sha256"], item["size_bytes"]):
                raise ValueError("original Workbench attachment changed before finalization")
        elif not item["staged"]:
            raise ValueError("unchanged Workbench attachment locator disappeared")
        rows.append(item)
    for item in rows:
        source = Path(item["native_path"])
        if item["staged"] and os.path.lexists(source):
            source.unlink()
            _fsync_dir(source.parent)


def rollback_staged_workbench_attachment_files(plan: dict, inverse_identity: dict,
                                                managed_root: str | Path) -> None:
    """Remove only links created by the matching uncommitted plan."""
    root = Path(os.path.abspath(managed_root))
    entries = _validated_inverse_plan(plan, inverse_identity, root)
    staged = []
    for item in entries:
        destination = Path(item["source_path"])
        if item["staged"] and destination.exists():
            _file(destination, root)
            if _digest(destination) != (item["content_sha256"], item["size_bytes"]):
                raise ValueError("staged Workbench attachment changed before rollback")
            staged.append(destination)
    for destination in staged:
        destination.unlink()
        _fsync_dir(destination.parent)


def stage_workbench_attachment_forward_files(db, managed_root: str | Path, inverse_identity: dict) -> dict:
    """Stage original native locators before caller-owned forward conversion."""
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Workbench forward file staging requires caller-owned FK-off transaction")
    from app.task_entity_schema import _validate_inverse_identity
    handoff = _validate_inverse_identity(inverse_identity)
    root = Path(os.path.abspath(managed_root))
    from app.task20_definitions import task20_projection

    rows = db.execute(
        "select id,task_id,storage_path from (" + task20_projection("workbench_attachments") + ")"
    ).fetchall()
    by_public_id = {row[0]: row for row in rows}
    identities = {row["source_physical_id"]: row
                  for row in handoff["workbench_attachment_identities"].values()}
    if {row["source_public_id"] for row in identities.values()} - set(by_public_id):
        raise ValueError("Workbench forward handoff names a missing source attachment")
    from app.storage_payload import TASK_RECORD_TYPES
    from app.task20_definitions import task20_column
    prepared = []
    # Validate every portable file entry and its actual restored source rows
    # before creating directories or links for any entry.
    for item in handoff["workbench_attachment_files"].values():
        identity = identities.get(item["source_physical_id"])
        if (identity is None or identity["canonical_id"] != item["canonical_id"]
                or identity["source_public_id"] != item["source_public_id"]):
            raise ValueError("Workbench forward file identity differs from inverse metadata")
        row = by_public_id.get(item["source_public_id"])
        if row is None or row[2] != item["source_path"]:
            raise ValueError("Workbench forward source locator differs from inverse metadata")
        if int(row[1]) != item["source_parent_physical_id"]:
            raise ValueError("Workbench forward file parent is missing")
        source_parent_public = Path(item["source_path"]).parent.name
        if (source_parent_public != item["source_parent_public_id"]
                or Path(item["source_path"]).name != item["source_public_id"]):
            raise ValueError("Workbench forward source locator differs from UUID identity")
        source_row = db.execute(
            "select parent_id," + task20_column("workbench_attachments", "id")
            + " from main.tasks where id=? and _kind=?",
            (item["source_physical_id"], TASK_RECORD_TYPES.code("workbench_attachments")),
        ).fetchone()
        parent_row = db.execute(
            "select " + task20_column("workbench_tasks", "id")
            + " from main.tasks where id=? and _kind=?",
            (item["source_parent_physical_id"], TASK_RECORD_TYPES.code("workbench_tasks")),
        ).fetchone()
        if (source_row is None or int(source_row[0]) != item["source_parent_physical_id"]
                or source_row[1] != item["source_public_id"]
                or parent_row is None or parent_row[0] != item["source_parent_public_id"]):
            raise ValueError(
                "Workbench forward source rows differ from portable identity: "
                f"attachment={source_row!r}, parent={parent_row!r}, entry={item!r}"
            )
        source = Path(item["source_path"])
        destination = Path(item["native_path"])
        if destination.parent != root / str(item["parent_canonical_id"]):
            raise ValueError("Workbench forward native locator differs from identity handoff")
        _file(source, root)
        if _digest(source) != (item["content_sha256"], item["size_bytes"]):
            raise ValueError("Workbench forward source bytes differ from inverse metadata")
        prepared.append((item, source, destination))
    entries, created = [], []
    try:
        for item, source, destination in prepared:
            if not _path_under(destination, root) or not destination.name:
                raise ValueError("Workbench forward locator is outside its managed root")
            _ensure_directory(destination.parent, root)
            if destination != source:
                if destination.exists() or destination.is_symlink():
                    raise ValueError("Workbench forward destination already exists")
                os.link(source, destination, follow_symlinks=False)
                created.append(destination)
                _fsync_file(destination)
                _fsync_dir(destination.parent)
            entries.append({**item, "staged": destination != source})
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
            _fsync_dir(path.parent)
        raise
    return {"version": 1, "direction": "forward", "attachments": entries}


def finalize_workbench_attachment_forward_files(db, plan: dict, inverse_identity: dict,
                                                managed_root: str | Path) -> None:
    """After commit/readback, remove source locators listed in the validated handoff."""
    if db.in_transaction:
        raise ValueError("Workbench forward file finalization requires committed database state")
    from app.task_entity_schema import _validate_inverse_identity
    handoff = _validate_inverse_identity(inverse_identity)
    root = Path(os.path.abspath(managed_root))
    expected = {row["source_physical_id"]: row
                for row in handoff["workbench_attachment_files"].values()}
    entries = plan.get("attachments") if isinstance(plan, dict) else None
    if (not isinstance(plan, dict) or plan.get("version") != 1
            or plan.get("direction") != "forward"
            or not isinstance(entries, list) or len(entries) != len(expected)):
        raise ValueError("invalid Workbench forward file finalization plan")
    by_source = {row.get("source_physical_id"): row for row in entries}
    if set(by_source) != set(expected):
        raise ValueError("Workbench forward finalization differs from inverse metadata")
    source_cleanup = []
    for source_id, item in expected.items():
        staged = by_source[source_id]
        if (not isinstance(staged.get("staged"), bool)
                or any(staged.get(key) != value for key, value in item.items())):
            raise ValueError("Workbench forward finalization item differs from inverse metadata")
        row = db.execute(
            "select workbench_task_id,storage_path,content_sha256,size_bytes "
            "from workbench_attachments where id=?", (item["canonical_id"],),
        ).fetchone()
        if (row is None or int(row[0]) != item["parent_canonical_id"]
                or row[1] != item["native_path"] or row[2] != item["content_sha256"]
                or row[3] != item["size_bytes"]):
            raise ValueError("committed Workbench native attachment differs from handoff")
        destination = Path(item["native_path"])
        _file(destination, root)
        if _digest(destination) != (item["content_sha256"], item["size_bytes"]):
            raise ValueError("committed Workbench native file failed readback")
        source = Path(item["source_path"])
        if (destination.parent != root / str(item["parent_canonical_id"])
                or source != root / item["source_parent_public_id"] / item["source_public_id"]):
            raise ValueError("Workbench forward finalization locator differs from identity handoff")
        if os.path.lexists(source):
            _file(source, root)
            if _digest(source) != (item["content_sha256"], item["size_bytes"]):
                raise ValueError("Workbench forward source bytes changed before finalization")
        elif not staged["staged"]:
            raise ValueError("unchanged Workbench forward source locator disappeared")
        if staged["staged"] and os.path.lexists(source):
            source_cleanup.append(source)
    for source in source_cleanup:
        source.unlink()
        _fsync_dir(source.parent)
