import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.store import SQLITE_BUSY_TIMEOUT_SECONDS, AutoReplyStore, _utc_store_time
from app.workbench.models import (
    ConfirmationStatus,
    TurnStatus,
    WorkbenchAttachment,
    WorkbenchArtifact,
    WorkbenchConfirmation,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)


_TURN_TRANSITIONS = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.STOPPED, TurnStatus.FAILED},
    TurnStatus.RUNNING: {
        TurnStatus.WAITING_CONFIRMATION,
        TurnStatus.COMPLETED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
    TurnStatus.WAITING_CONFIRMATION: {
        TurnStatus.QUEUED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
    TurnStatus.COMPLETED: set(),
    TurnStatus.STOPPED: set(),
    TurnStatus.FAILED: set(),
}

WORKBENCH_RECOVERY_BATCH_LIMIT = 100


class WorkbenchConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


LEGACY_PENDING_PROPOSER_RECOVERY_SQL = """
select * from workbench_confirmations
where status='pending' and proposer_run_id='' and id>?
order by id limit ?
"""

EXPIRED_PENDING_PROPOSER_RECOVERY_SQL = """
select * from workbench_confirmations
where status='pending' and proposer_run_id<>'' and proposer_quiesced_at=''
  and proposer_lease_expires_at<=?
order by proposer_lease_expires_at, id limit ?
"""

LEGACY_CONFIRMED_OWNER_RECOVERY_SQL = """
select confirmations.*, turns.status as turn_status
from workbench_confirmations as confirmations
join workbench_turns as turns on turns.id=confirmations.turn_id
where confirmations.status='confirmed' and confirmations.result_json=''
  and confirmations.execution_owner=''
  and confirmations.id>?
order by confirmations.id limit ?
"""

LEGACY_CONFIRMED_LEASE_RECOVERY_SQL = """
select confirmations.*, turns.status as turn_status
from workbench_confirmations as confirmations
join workbench_turns as turns on turns.id=confirmations.turn_id
where confirmations.status='confirmed' and confirmations.result_json=''
  and confirmations.execution_owner<>''
  and confirmations.execution_lease_expires_at=''
  and confirmations.id>?
order by confirmations.id limit ?
"""

EXPIRED_CONFIRMED_EXECUTION_RECOVERY_SQL = """
select confirmations.*, turns.status as turn_status
from workbench_confirmations as confirmations
join workbench_turns as turns on turns.id=confirmations.turn_id
where confirmations.status='confirmed' and confirmations.result_json=''
  and confirmations.execution_owner<>''
  and confirmations.execution_lease_expires_at<>''
  and confirmations.execution_lease_expires_at<=?
order by confirmations.execution_lease_expires_at, confirmations.id limit ?
"""


def _json_object_text(value: dict[str, Any] | str, *, field: str) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a JSON object") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


class WorkbenchStore(AutoReplyStore):
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_seconds: int = SQLITE_BUSY_TIMEOUT_SECONDS,
    ):
        super().__init__(path, busy_timeout_seconds=busy_timeout_seconds)
        self._reconcile_attachments()

    def create_task(self, *, title: str, runtime_kind: str) -> WorkbenchTask:
        title = title.strip()
        runtime_kind = runtime_kind.strip()
        if not title:
            raise ValueError("title must be non-empty")
        if not runtime_kind:
            raise ValueError("runtime_kind must be non-empty")
        task_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                """
                insert into workbench_tasks (id, title, runtime_kind)
                values (?, ?, ?)
                """,
                (task_id, title, runtime_kind),
            )
            return self._task_from_row(self._require_task(db, task_id))

    def get_task(self, task_id: str) -> WorkbenchTask | None:
        with self._connect() as db:
            row = db.execute(
                "select * from workbench_tasks where id=?", (task_id,)
            ).fetchone()
            return None if row is None else self._task_from_row(row)

    def get_task_summary(self, task_id: str) -> tuple[WorkbenchTask, str] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select tasks.*,
                       coalesce((select status from workbench_turns
                                 where task_id=tasks.id
                                 order by task_sequence desc limit 1), 'idle')
                           as latest_state
                from workbench_tasks tasks where tasks.id=?
                """,
                (task_id,),
            ).fetchone()
            return None if row is None else (self._task_from_row(row), row["latest_state"])

    def list_tasks(self, *, include_archived: bool = False) -> list[WorkbenchTask]:
        with self._connect() as db:
            query = "select * from workbench_tasks"
            if not include_archived:
                query += " where archived_at=''"
            query += " order by updated_at desc, id desc"
            return [self._task_from_row(row) for row in db.execute(query)]

    def list_tasks_with_state(
        self,
        *,
        include_archived: bool = False,
        archived_only: bool = False,
        limit: int = 50,
        cursor: tuple[str, str] | None = None,
    ) -> list[tuple[WorkbenchTask, str]]:
        if limit < 1 or limit > 101:
            raise ValueError("task limit must be between 1 and 101")
        clauses: list[str] = []
        parameters: list[Any] = []
        if archived_only:
            clauses.append("tasks.archived_at<>''")
        elif not include_archived:
            clauses.append("tasks.archived_at=''")
        if cursor is not None:
            clauses.append("(tasks.updated_at < ? or (tasks.updated_at=? and tasks.id<?))")
            parameters.extend((cursor[0], cursor[0], cursor[1]))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select tasks.*,
                       coalesce((select status from workbench_turns
                                 where task_id=tasks.id
                                 order by task_sequence desc limit 1), 'idle')
                           as latest_state
                from workbench_tasks as tasks
                {where}
                order by tasks.updated_at desc, tasks.id desc limit ?
                """,
                tuple(parameters),
            ).fetchall()
            return [(self._task_from_row(row), row["latest_state"]) for row in rows]

    def rename_task(self, task_id: str, *, title: str) -> WorkbenchTask:
        title = title.strip()
        if not title:
            raise ValueError("title must be non-empty")
        with self._connect() as db:
            if db.execute(
                """
                update workbench_tasks
                set title=?, updated_at=current_timestamp
                where id=?
                """,
                (title, task_id),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, task_id))

    def archive_task(
        self, task_id: str, *, now: str | datetime | None = None
    ) -> WorkbenchTask:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            self._require_task(db, task_id)
            active = db.execute(
                """
                select 1 from workbench_turns
                where task_id=? and status in ('queued', 'running', 'waiting_confirmation')
                limit 1
                """,
                (task_id,),
            ).fetchone()
            if active is not None:
                raise WorkbenchConflictError(
                    "task_has_active_turn", "workbench task has an active turn"
                )
            if db.execute(
                """
                update workbench_tasks
                set archived_at=?, updated_at=?
                where id=?
                """,
                (now_text, now_text, task_id),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, task_id))

    def save_attachment(
        self,
        task_id: str,
        *,
        client_request_id: str = "",
        filename: str,
        media_type: str,
        content: bytes,
    ) -> WorkbenchAttachment:
        if not isinstance(content, bytes):
            raise ValueError("attachment content must be bytes")
        filename = filename.strip()
        media_type = media_type.strip()
        if not filename:
            raise ValueError("filename must be non-empty")
        if not media_type:
            raise ValueError("media_type must be non-empty")
        task_id = self._canonical_uuid(task_id, field="task_id")
        client_request_id = self._canonical_uuid(
            client_request_id or str(uuid4()), field="client_request_id"
        )
        content_sha256 = hashlib.sha256(content).hexdigest()
        with self._connect() as db:
            self._require_task(db, task_id)
        attachment_id = str(uuid4())
        with self._attachment_lock(create_workbench=True):
            with self._connect() as db:
                self._require_task(db, task_id)
                existing = db.execute(
                    """select * from workbench_attachments
                       where task_id=? and client_request_id=?""",
                    (task_id, client_request_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["filename"] == filename
                        and existing["media_type"] == media_type
                        and existing["size_bytes"] == len(content)
                        and existing["content_sha256"] == content_sha256
                    ):
                        return self._attachment_from_row(existing)
                    raise WorkbenchConflictError(
                        "attachment_request_conflict",
                        "client request ID conflicts with an existing attachment",
                    )
            directory = self._attachment_task_directory(task_id, create=True)
            if directory is None:
                raise RuntimeError("attachment directory was not created")
            storage_path = directory / attachment_id
            temp_path = directory / f".{attachment_id}.{uuid4().hex}.tmp"
            try:
                self._write_attachment_temp(temp_path, content)
                self._after_attachment_temp_created(temp_path)
                db = self._open_connection()
                try:
                    db.execute("begin immediate")
                    self._require_task(db, task_id)
                    db.execute(
                        """
                        insert into workbench_attachments (
                            id, task_id, client_request_id, filename, media_type,
                            size_bytes, content_sha256, storage_path
                        ) values (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            attachment_id,
                            task_id,
                            client_request_id,
                            filename,
                            media_type,
                            len(content),
                            content_sha256,
                            str(storage_path),
                        ),
                    )
                    row = db.execute(
                        "select * from workbench_attachments where id=?",
                        (attachment_id,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("attachment insert did not create a row")
                    attachment = self._attachment_from_row(row)
                    os.replace(temp_path, storage_path)
                    self._fsync_attachment_file(storage_path)
                    self._fsync_directory(directory)
                    self._commit_attachment_metadata(db)
                except Exception:
                    if db.in_transaction:
                        db.rollback()
                    raise
                finally:
                    db.close()
                return attachment
            except Exception:
                self._safe_attachment_unlink(temp_path, directory)
                self._safe_attachment_unlink(storage_path, directory)
                raise

    def list_attachments(self, task_id: str) -> list[WorkbenchAttachment]:
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                """
                select * from workbench_attachments
                where task_id=?
                order by created_at, id
                """,
                (task_id,),
            ).fetchall()
            return [self._attachment_from_row(row) for row in rows]

    def create_turn(
        self,
        task_id: str,
        *,
        user_text: str,
        client_request_id: str,
    ) -> WorkbenchTurn:
        user_text = user_text.strip()
        client_request_id = client_request_id.strip()
        if not user_text:
            raise ValueError("user_text must be non-empty")
        if not client_request_id:
            raise ValueError("client_request_id must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                "select * from workbench_turns where client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] != task_id
                    or existing["user_text"] != user_text
                ):
                    raise WorkbenchConflictError(
                        "client_request_conflict",
                        "client_request_id conflicts with an existing turn",
                    )
                return self._turn_from_row(existing)
            task = self._require_task(db, task_id)
            if task["archived_at"]:
                raise WorkbenchConflictError(
                    "task_archived", "workbench task is archived"
                )
            active = db.execute(
                """
                select 1 from workbench_turns
                where task_id=? and status in ('queued', 'running', 'waiting_confirmation')
                """,
                (task_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("task already has an active turn")
            turn_id = str(uuid4())
            task_sequence = int(
                db.execute(
                    """select coalesce(max(task_sequence),0)+1
                       from workbench_turns where task_id=?""",
                    (task_id,),
                ).fetchone()[0]
            )
            try:
                db.execute(
                    """
                    insert into workbench_turns (
                        id, task_id, client_request_id, task_sequence, user_text, status
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        task_id,
                        client_request_id,
                        task_sequence,
                        user_text,
                        TurnStatus.QUEUED.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("task already has an active turn") from exc
            self._append_control_event(
                db,
                turn_id,
                event_type="status_changed",
                payload={"status": TurnStatus.QUEUED.value},
            )
            return self._turn_from_row(self._require_turn(db, turn_id))

    def get_turn(self, turn_id: str) -> WorkbenchTurn | None:
        with self._connect() as db:
            row = db.execute(
                "select * from workbench_turns where id=?", (turn_id,)
            ).fetchone()
            return None if row is None else self._turn_from_row(row)

    def list_turns(self, task_id: str) -> list[WorkbenchTurn]:
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                """
                select * from workbench_turns
                where task_id=? order by task_sequence desc
                """,
                (task_id,),
            ).fetchall()
            return [self._turn_from_row(row) for row in rows]

    def resume_context_for_executor(
        self,
        turn_id: str,
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> str:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            row = self._require_executor_lease(
                db, turn_id, owner=owner, now_text=now_text
            )
            return str(row["resume_context"] or "")

    def execution_run_id_for_executor(self, turn_id: str, *, owner: str) -> str:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        with self._connect() as db:
            turn = self._require_turn(db, turn_id)
            if turn["lease_owner"] != owner:
                confirmation = db.execute(
                    """
                    select 1 from workbench_confirmations
                    where turn_id=? and proposer_owner=?
                      and proposer_run_id=? limit 1
                    """,
                    (turn_id, owner, turn["execution_run_id"]),
                ).fetchone()
                if confirmation is None:
                    raise ValueError("executor does not own runtime run")
            return str(turn["execution_run_id"] or "")

    def recover_expired_turns(
        self, *, now: str | datetime | None = None
    ) -> int:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            return self._recover_expired_turns_in_transaction(db, now_text=now_text)

    def claim_next_turn(
        self,
        *,
        owner: str,
        execution_run_id: str = "",
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> WorkbenchTurn | None:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        execution_run_id = execution_run_id.strip() or str(uuid4())
        now_value, now_text = _utc_store_time(now)
        lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            self._recover_expired_turns_in_transaction(db, now_text=now_text)
            row = db.execute(
                """
                select workbench_turns.*
                from workbench_turns
                join workbench_tasks on workbench_tasks.id=workbench_turns.task_id
                where workbench_turns.status='queued'
                  and workbench_tasks.archived_at=''
                  and not exists (
                      select 1 from workbench_confirmations
                      where workbench_confirmations.turn_id=workbench_turns.id
                        and workbench_confirmations.status='confirmed'
                        and workbench_confirmations.result_json=''
                  )
                order by workbench_turns.created_at, workbench_turns.id
                limit 1
                """
            ).fetchone()
            if row is None:
                return None
            if db.execute(
                """
                update workbench_turns
                set status='running', lease_owner=?, lease_expires_at=?,
                    execution_run_id=?, runtime_quiesced_run_id='',
                    started_at=case when started_at='' then ? else started_at end,
                    updated_at=?
                where id=? and status='queued'
                """,
                (
                    owner,
                    lease_expires_at,
                    execution_run_id,
                    now_text,
                    now_text,
                    row["id"],
                ),
            ).rowcount != 1:
                return None
            return self._turn_from_row(self._require_turn(db, row["id"]))

    def renew_turn_lease(
        self,
        turn_id: str,
        *,
        owner: str,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> WorkbenchTurn:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value, now_text = _utc_store_time(now)
        lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            self._require_lease(db, turn_id, owner=owner, now_text=now_text)
            if db.execute(
                """
                update workbench_turns
                set lease_expires_at=?, updated_at=?
                where id=? and status='running' and lease_owner=? and lease_expires_at>?
                """,
                (lease_expires_at, now_text, turn_id, owner, now_text),
            ).rowcount != 1:
                raise ValueError("turn lease is stale")
            return self._turn_from_row(self._require_turn(db, turn_id))

    def request_stop(
        self,
        turn_id: str,
        *,
        owner: str = "",
        now: str | datetime | None = None,
    ) -> WorkbenchTurn:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_turn(db, turn_id)
            status = TurnStatus(row["status"])
            if status is TurnStatus.STOPPED:
                return self._turn_from_row(row)
            if TurnStatus.STOPPED not in _TURN_TRANSITIONS[status]:
                raise ValueError("invalid turn transition")
            db.execute(
                "update workbench_turns set stop_requested=1, updated_at=? where id=?",
                (now_text, turn_id),
            )
            self._append_control_event(
                db,
                turn_id,
                event_type="turn_completed",
                payload={"status": TurnStatus.STOPPED.value},
            )
            self._resolve_pending_confirmations(
                db,
                turn_id,
                status=ConfirmationStatus.CANCELLED,
                now_text=now_text,
            )
            self._transition_turn(
                db,
                turn_id,
                current=status,
                target=TurnStatus.STOPPED,
                now_text=now_text,
                clear_lease=True,
            )
            return self._turn_from_row(self._require_turn(db, turn_id))

    def append_event(
        self,
        turn_id: str,
        *,
        sequence: int,
        event_type: str,
        payload: dict[str, Any] | str,
        owner: str = "",
        now: str | datetime | None = None,
    ) -> WorkbenchEvent:
        if sequence <= 0:
            raise ValueError("sequence must be positive")
        if event_type not in WorkbenchEvent.model_fields["event_type"].annotation.__args__:
            raise ValueError("invalid workbench event type")
        payload_json = _json_object_text(payload, field="payload")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            self._require_executor_lease(db, turn_id, owner=owner, now_text=now_text)
            expected_sequence = int(
                db.execute(
                    """
                    select coalesce(max(sequence), 0) + 1
                    from workbench_events where turn_id=?
                    """,
                    (turn_id,),
                ).fetchone()[0]
            )
            if sequence != expected_sequence:
                raise ValueError("event sequence must be next")
            try:
                cursor = db.execute(
                    """
                    insert into workbench_events (turn_id, sequence, event_type, payload_json)
                    values (?, ?, ?, ?)
                    """,
                    (turn_id, sequence, event_type, payload_json),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("event sequence already exists") from exc
            event = db.execute(
                "select * from workbench_events where id=?", (cursor.lastrowid,)
            ).fetchone()
            if event is None:
                raise RuntimeError("event insert did not create a row")
            return self._event_from_row(event)

    def events_after(
        self, turn_id: str, after_id: int = 0, *, limit: int | None = None
    ) -> list[WorkbenchEvent]:
        if after_id < 0:
            raise ValueError("after_id must not be negative")
        if limit is not None and (limit < 1 or limit > 1000):
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            self._require_turn(db, turn_id)
            query = """
                select * from workbench_events
                where turn_id=? and id>? order by id
            """
            parameters: tuple[Any, ...] = (turn_id, after_id)
            if limit is not None:
                query += " limit ?"
                parameters += (limit,)
            rows = db.execute(query, parameters).fetchall()
            return [self._event_from_row(row) for row in rows]

    def event_stream_snapshot(
        self, turn_id: str, after_id: int = 0, *, limit: int = 1000
    ) -> tuple[list[WorkbenchEvent], WorkbenchTurn | None]:
        if after_id < 0 or limit < 1 or limit > 1000:
            raise ValueError("invalid event stream cursor or limit")
        with self._connect() as db:
            db.execute("begin")
            rows = db.execute(
                """
                select * from workbench_events
                where turn_id=? and id>? order by id limit ?
                """,
                (turn_id, after_id, limit),
            ).fetchall()
            turn_row = db.execute(
                "select * from workbench_turns where id=?", (turn_id,)
            ).fetchone()
            return (
                [self._event_from_row(row) for row in rows],
                None if turn_row is None else self._turn_from_row(turn_row),
            )

    def append_artifact_event(
        self,
        turn_id: str,
        *,
        sequence: int,
        label: str,
        path: str,
        media_type: str,
        owner: str,
        now: str | datetime | None = None,
    ) -> tuple[WorkbenchArtifact, WorkbenchEvent]:
        label = label.strip()
        media_type = media_type.strip()
        if not label or not path or not media_type:
            raise ValueError("invalid artifact event")
        _, now_text = _utc_store_time(now)
        artifact_id = str(uuid4())
        payload = {
            "artifact_id": artifact_id,
            "label": label,
            "filename": Path(path).name,
            "media_type": media_type,
        }
        with self._connect() as db:
            db.execute("begin immediate")
            self._require_executor_lease(db, turn_id, owner=owner, now_text=now_text)
            expected = int(db.execute(
                "select coalesce(max(sequence),0)+1 from workbench_events where turn_id=?",
                (turn_id,),
            ).fetchone()[0])
            if sequence != expected:
                raise ValueError("event sequence must be next")
            db.execute(
                """insert into workbench_artifacts(id,turn_id,label,path,media_type)
                   values(?,?,?,?,?)""",
                (artifact_id, turn_id, label, path, media_type),
            )
            cursor = db.execute(
                """insert into workbench_events(turn_id,sequence,event_type,payload_json)
                   values(?,?,'artifact_created',?)""",
                (turn_id, sequence, json.dumps(payload, separators=(",", ":"))),
            )
            artifact_row = db.execute(
                "select * from workbench_artifacts where id=?", (artifact_id,)
            ).fetchone()
            event_row = db.execute(
                "select * from workbench_events where id=?", (cursor.lastrowid,)
            ).fetchone()
            return self._artifact_from_row(artifact_row), self._event_from_row(event_row)

    def list_artifacts(self, task_id: str) -> list[WorkbenchArtifact]:
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                """
                select artifacts.*
                from workbench_artifacts as artifacts
                join workbench_turns as turns on turns.id=artifacts.turn_id
                where turns.task_id=? order by artifacts.created_at, artifacts.id
                """,
                (task_id,),
            ).fetchall()
            return [self._artifact_from_row(row) for row in rows]

    def timeline_snapshot(
        self,
        task_id: str,
        *,
        turn_limit: int = 100,
        event_limit: int = 1000,
        before_sequence: int | None = None,
        event_before: int | None = None,
        artifact_after: tuple[str, str] | None = None,
        confirmation_after: tuple[str, str] | None = None,
        attachment_after: tuple[str, str] | None = None,
    ) -> tuple[
        WorkbenchTask,
        list[WorkbenchTurn],
        list[WorkbenchEvent],
        list[WorkbenchAttachment],
        list[WorkbenchArtifact],
        list[WorkbenchConfirmation],
        dict[str, Any],
    ]:
        if turn_limit < 1 or turn_limit > 100 or event_limit < 1 or event_limit > 1000:
            raise ValueError("invalid timeline limit")
        with self._connect() as db:
            db.execute("begin")
            task_row = self._require_task(db, task_id)
            state_row = db.execute(
                """select status from workbench_turns where task_id=?
                   order by task_sequence desc limit 1""",
                (task_id,),
            ).fetchone()
            task_state = state_row["status"] if state_row is not None else "idle"
            cursor_sql = ""
            turn_parameters: list[Any] = [task_id]
            if before_sequence is not None:
                cursor_sql = "and task_sequence<?"
                turn_parameters.append(before_sequence)
            turn_parameters.append(turn_limit + 1)
            turn_rows = db.execute(
                f"""select * from workbench_turns where task_id=? {cursor_sql}
                    order by task_sequence desc limit ?""",
                tuple(turn_parameters),
            ).fetchall()
            has_more = len(turn_rows) > turn_limit
            turn_rows = turn_rows[:turn_limit]
            turn_ids = [row["id"] for row in turn_rows]
            placeholders = ",".join("?" for _ in turn_ids)
            if turn_ids:
                event_cursor_sql = ""
                event_parameters: list[Any] = list(turn_ids)
                if event_before is not None:
                    event_cursor_sql = "and id<?"
                    event_parameters.append(event_before)
                event_parameters.append(event_limit + 1)
                event_rows = db.execute(
                    f"""select * from workbench_events where turn_id in ({placeholders})
                        {event_cursor_sql} order by id desc limit ?""",
                    tuple(event_parameters),
                ).fetchall()
                artifact_cursor_sql = ""
                artifact_parameters: list[Any] = list(turn_ids)
                if artifact_after is not None:
                    artifact_cursor_sql = (
                        "and (created_at>? or (created_at=? and id>?))"
                    )
                    artifact_parameters.extend(
                        (artifact_after[0], artifact_after[0], artifact_after[1])
                    )
                artifact_rows = db.execute(
                    f"""select * from workbench_artifacts where turn_id in ({placeholders})
                        {artifact_cursor_sql} order by created_at,id limit 101""",
                    tuple(artifact_parameters),
                ).fetchall()
                confirmation_cursor_sql = ""
                confirmation_parameters: list[Any] = list(turn_ids)
                if confirmation_after is not None:
                    confirmation_cursor_sql = (
                        "and (created_at>? or (created_at=? and id>?))"
                    )
                    confirmation_parameters.extend(
                        (
                            confirmation_after[0],
                            confirmation_after[0],
                            confirmation_after[1],
                        )
                    )
                confirmation_rows = db.execute(
                    f"""select * from workbench_confirmations
                        where turn_id in ({placeholders})
                        {confirmation_cursor_sql} order by created_at,id limit 101""",
                    tuple(confirmation_parameters),
                ).fetchall()
            else:
                event_rows, artifact_rows, confirmation_rows = [], [], []
            attachment_cursor_sql = ""
            attachment_parameters: list[Any] = [task_id]
            if attachment_after is not None:
                attachment_cursor_sql = (
                    "and (created_at>? or (created_at=? and id>?))"
                )
                attachment_parameters.extend(
                    (attachment_after[0], attachment_after[0], attachment_after[1])
                )
            attachment_rows = db.execute(
                f"""select * from workbench_attachments where task_id=?
                   {attachment_cursor_sql} order by created_at,id limit 101""",
                tuple(attachment_parameters),
            ).fetchall()
            events_has_more = len(event_rows) > event_limit
            artifacts_has_more = len(artifact_rows) > 100
            confirmations_has_more = len(confirmation_rows) > 100
            attachments_has_more = len(attachment_rows) > 100
            events_next_cursor = (
                event_rows[event_limit - 1]["id"] if events_has_more else None
            )
            event_rows = event_rows[:event_limit]
            artifact_rows = artifact_rows[:100]
            confirmation_rows = confirmation_rows[:100]
            attachment_rows = attachment_rows[:100]
            runtime_quiesced_runs = {
                row["id"]: row["runtime_quiesced_run_id"] for row in turn_rows
            }
            confirmation_quiescence = {
                row["id"]: bool(
                    row["proposer_quiesced_at"]
                    and row["proposer_run_id"]
                    and row["proposer_run_id"]
                    == runtime_quiesced_runs.get(row["turn_id"], "")
                )
                for row in confirmation_rows
            }
            next_cursor = (
                turn_rows[-1]["task_sequence"]
                if has_more and turn_rows
                else None
            )
            return (
                self._task_from_row(task_row),
                [self._turn_from_row(row) for row in turn_rows],
                [self._event_from_row(row) for row in reversed(event_rows)],
                [self._attachment_from_row(row) for row in attachment_rows],
                [self._artifact_from_row(row) for row in artifact_rows],
                [self._confirmation_from_row(row) for row in confirmation_rows],
                {
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "task_state": task_state,
                    "events_has_more": events_has_more,
                    "events_next_cursor": events_next_cursor,
                    "artifacts_has_more": artifacts_has_more,
                    "confirmations_has_more": confirmations_has_more,
                    "confirmation_quiescence": confirmation_quiescence,
                    "attachments_has_more": attachments_has_more,
                },
            )

    def get_artifact(self, artifact_id: str) -> WorkbenchArtifact | None:
        with self._connect() as db:
            row = db.execute(
                "select * from workbench_artifacts where id=?", (artifact_id,)
            ).fetchone()
            return None if row is None else self._artifact_from_row(row)

    def workbench_stats(self) -> dict[str, Any]:
        with self._connect() as db:
            task_row = db.execute(
                """
                select count(*) as total,
                       sum(case when archived_at='' then 1 else 0 end) as active,
                       sum(case when archived_at<>'' then 1 else 0 end) as archived
                from workbench_tasks
                """
            ).fetchone()
            turn_counts = {status.value: 0 for status in TurnStatus}
            turn_counts.update(
                {
                    row["status"]: int(row["count"])
                    for row in db.execute(
                        "select status, count(*) as count from workbench_turns group by status"
                    )
                }
            )
            confirmation_counts = {status.value: 0 for status in ConfirmationStatus}
            confirmation_counts.update(
                {
                    row["status"]: int(row["count"])
                    for row in db.execute(
                        """
                        select status, count(*) as count
                        from workbench_confirmations group by status
                        """
                    )
                }
            )
            event_counts = {
                event_type: 0
                for event_type in WorkbenchEvent.model_fields[
                    "event_type"
                ].annotation.__args__
            }
            event_counts.update(
                {
                    row["event_type"]: int(row["count"])
                    for row in db.execute(
                        """
                        select event_type, count(*) as count
                        from workbench_events group by event_type
                        """
                    )
                }
            )
            duration = db.execute(
                """
                select count(*) as completed_count,
                       coalesce(sum(julianday(completed_at) - julianday(started_at)), 0)
                           * 86400.0 as total_seconds
                from workbench_turns
                where started_at<>'' and completed_at<>''
                """
            ).fetchone()
            completed_count = int(duration["completed_count"] or 0)
            total_seconds = max(0.0, float(duration["total_seconds"] or 0.0))
            attachments = int(
                db.execute("select count(*) from workbench_attachments").fetchone()[0]
            )
            artifacts = int(
                db.execute("select count(*) from workbench_artifacts").fetchone()[0]
            )
        return {
            "tasks": {
                "total": int(task_row["total"] or 0),
                "active": int(task_row["active"] or 0),
                "archived": int(task_row["archived"] or 0),
            },
            "turns": turn_counts,
            "confirmations": confirmation_counts,
            "events": event_counts,
            "attachments": attachments,
            "artifacts": artifacts,
            "duration": {
                "completed_count": completed_count,
                "total_seconds": total_seconds,
                "average_seconds": (
                    total_seconds / completed_count if completed_count else 0.0
                ),
            },
        }

    def set_provider_session(
        self,
        turn_id: str,
        provider_session_ref: str,
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> WorkbenchTask:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            turn = self._require_executor_lease(
                db, turn_id, owner=owner, now_text=now_text
            )
            if db.execute(
                """
                update workbench_tasks
                set provider_session_ref=?, updated_at=?
                where id=?
                """,
                (provider_session_ref.strip(), now_text, turn["task_id"]),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, turn["task_id"]))

    def attachment_paths_for_executor(
        self,
        turn_id: str,
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> tuple[tuple[Path, str], ...]:
        """Return private attachment paths only to the current lease owner."""
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            turn = self._require_executor_lease(
                db, turn_id, owner=owner, now_text=now_text
            )
            rows = db.execute(
                """
                select storage_path, media_type from workbench_attachments
                where task_id=? order by created_at, id
                """,
                (turn["task_id"],),
            ).fetchall()
            return tuple((Path(row["storage_path"]), row["media_type"]) for row in rows)

    def create_confirmation(
        self,
        turn_id: str,
        *,
        action_kind: str,
        target: str,
        summary: str,
        risk: str,
        arguments_json: dict[str, Any] | str,
        confirmation_id: str = "",
        canonical_capability: str = "",
        canonical_operation: str = "",
        canonical_targets: tuple[str, ...] = (),
        canonical_operation_digest: str = "",
        canonical_arguments_digest: str = "",
        owner: str = "",
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        values = tuple(item.strip() for item in (action_kind, target, summary, risk))
        if not all(values):
            raise ValueError("confirmation fields must be non-empty")
        arguments_text = _json_object_text(arguments_json, field="arguments_json")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            turn = self._require_turn(db, turn_id)
            if TurnStatus(turn["status"]) is not TurnStatus.RUNNING:
                raise ValueError("confirmation requires a running turn")
            self._require_executor_lease(
                db, turn_id, owner=owner, now_text=now_text
            )
            confirmation_id = confirmation_id or str(uuid4())
            canonical_targets_text = json.dumps(
                canonical_targets, ensure_ascii=False, separators=(",", ":")
            )
            proposer_run_id = str(turn["execution_run_id"] or "")
            if not proposer_run_id:
                raise ValueError("confirmation proposer run is missing")
            db.execute(
                """
                insert into workbench_confirmations (
                    id, turn_id, action_kind, target, summary, risk, arguments_json,
                    canonical_capability, canonical_operation, canonical_targets_json,
                    canonical_operation_digest, canonical_arguments_digest, status,
                    proposer_run_id, proposer_owner, proposer_lease_expires_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    confirmation_id,
                    turn_id,
                    *values,
                    arguments_text,
                    canonical_capability,
                    canonical_operation,
                    canonical_targets_text,
                    canonical_operation_digest,
                    canonical_arguments_digest,
                    proposer_run_id,
                    owner,
                    turn["lease_expires_at"],
                ),
            )
            self._append_control_event(
                db,
                turn_id,
                event_type="confirmation_required",
                payload={
                    "action_kind": values[0],
                    "confirmation_id": confirmation_id,
                    "target": values[1],
                },
            )
            self._transition_turn(
                db,
                turn_id,
                current=TurnStatus.RUNNING,
                target=TurnStatus.WAITING_CONFIRMATION,
                now_text=now_text,
                clear_lease=True,
            )
            return self._confirmation_from_row(
                self._require_confirmation(db, confirmation_id), redact_arguments=True
            )

    def consume_confirmation_authorization(
        self, confirmation_id: str, *, owner: str, authorization: Any
    ) -> None:
        _, now_text = _utc_store_time()
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_confirmation(db, confirmation_id)
            expected = (
                row["canonical_capability"],
                row["canonical_operation"],
                row["canonical_operation_digest"],
                tuple(json.loads(row["canonical_targets_json"])),
                row["canonical_arguments_digest"],
            )
            actual = (
                authorization.capability,
                authorization.operation,
                authorization.operation_digest,
                authorization.target_identifiers,
                authorization.arguments_digest,
            )
            if (
                row["status"] != ConfirmationStatus.CONFIRMED.value
                or row["execution_owner"] != owner
                or row["execution_lease_expires_at"] <= now_text
                or row["authorization_consumed_at"]
                or expected != actual
            ):
                raise ValueError("reviewed write authorization cannot be consumed")
            if db.execute(
                """
                update workbench_confirmations set authorization_consumed_at=?
                where id=? and status='confirmed' and execution_owner=?
                  and authorization_consumed_at='' and execution_lease_expires_at>?
                """,
                (now_text, confirmation_id, owner, now_text),
            ).rowcount != 1:
                raise ValueError("reviewed write authorization cannot be consumed")

    def renew_confirmation_proposer(
        self,
        turn_id: str,
        *,
        owner: str,
        proposer_run_id: str,
        lease_seconds: int,
    ) -> None:
        now_value, now_text = _utc_store_time()
        expires = (now_value + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            if db.execute(
                """
                update workbench_confirmations
                set proposer_lease_expires_at=?
                where turn_id=? and status='pending' and proposer_owner=?
                  and proposer_run_id=? and proposer_quiesced_at=''
                  and proposer_lease_expires_at>?
                """,
                (expires, turn_id, owner, proposer_run_id, now_text),
            ).rowcount != 1:
                raise ValueError("confirmation proposer lease is stale")

    def mark_confirmation_proposer_quiesced(
        self,
        turn_id: str,
        *,
        owner: str,
        proposer_run_id: str,
        now: str | datetime | None = None,
    ) -> tuple[str, str] | None:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select * from workbench_confirmations
                where turn_id=? and status='pending' and proposer_owner=?
                  and proposer_run_id=? and proposer_quiesced_at=''
                  and proposer_lease_expires_at>?
                order by created_at desc, id desc limit 1
                """,
                (turn_id, owner, proposer_run_id, now_text),
            ).fetchone()
            if row is None:
                return None
            turn = self._require_turn(db, turn_id)
            if (
                TurnStatus(turn["status"]) is not TurnStatus.WAITING_CONFIRMATION
                or turn["execution_run_id"] != proposer_run_id
            ):
                raise ValueError("confirmation proposer run is stale")
            db.execute(
                "update workbench_confirmations set proposer_quiesced_at=? where id=?",
                (now_text, row["id"]),
            )
            db.execute(
                "update workbench_turns set runtime_quiesced_run_id=? where id=?",
                (proposer_run_id, turn_id),
            )
            return row["id"], str(row["decision_requested"] or "")

    def reconcile_unquiesced_proposers(
        self, *, now: str | datetime | None = None
    ) -> int:
        return len(self.reconcile_unquiesced_proposer_batch(now=now))

    def reconcile_unquiesced_proposer_batch(
        self, *, now: str | datetime | None = None
    ) -> tuple[str, ...]:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = list(
                db.execute(
                    LEGACY_PENDING_PROPOSER_RECOVERY_SQL,
                    ("", WORKBENCH_RECOVERY_BATCH_LIMIT),
                ).fetchall()
            )
            remaining = WORKBENCH_RECOVERY_BATCH_LIMIT - len(rows)
            if remaining:
                rows.extend(
                    db.execute(
                        EXPIRED_PENDING_PROPOSER_RECOVERY_SQL,
                        (now_text, remaining),
                    ).fetchall()
                )
            for row in rows:
                code = (
                    "legacy_proposer_state_unknown"
                    if not row["proposer_run_id"]
                    else "confirmation_proposer_not_quiesced"
                )
                db.execute(
                    """
                    update workbench_confirmations
                    set status='failed', result_json=?, decided_at=?,
                        execution_owner='', execution_lease_expires_at='',
                        execution_started_at='', authorization_consumed_at='',
                        proposer_owner='', proposer_lease_expires_at='',
                        proposer_quiesced_at='', decision_requested='',
                        decision_requested_at=''
                    where id=? and status='pending'
                    """,
                    (
                        _json_object_text(
                            {
                                "code": code,
                                "retryable": False,
                                "status": "failed",
                            },
                            field="result_json",
                        ),
                        now_text,
                        row["id"],
                    ),
                )
                turn = self._require_turn(db, row["turn_id"])
                status = TurnStatus(turn["status"])
                if status in {
                    TurnStatus.QUEUED,
                    TurnStatus.RUNNING,
                    TurnStatus.WAITING_CONFIRMATION,
                }:
                    self._append_control_event(
                        db,
                        row["turn_id"],
                        event_type="turn_failed",
                        payload={
                            "code": code,
                            "status": TurnStatus.FAILED.value,
                        },
                    )
                    self._transition_turn(
                        db,
                        row["turn_id"],
                        current=status,
                        target=TurnStatus.FAILED,
                        now_text=now_text,
                        error_code=code,
                        error_detail=(
                            "Legacy confirmation proposer state is unknown."
                            if code == "legacy_proposer_state_unknown"
                            else "Confirmation proposer did not quiesce safely."
                        ),
                        clear_lease=True,
                    )
            return tuple(row["id"] for row in rows)

    def list_confirmations(self, task_id: str) -> list[WorkbenchConfirmation]:
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                """
                select confirmations.*
                from workbench_confirmations as confirmations
                join workbench_turns as turns on turns.id=confirmations.turn_id
                where turns.task_id=?
                order by confirmations.created_at, confirmations.id
                """,
                (task_id,),
            ).fetchall()
            return [
                self._confirmation_from_row(row, redact_arguments=True)
                for row in rows
            ]

    def get_confirmation(self, confirmation_id: str) -> WorkbenchConfirmation | None:
        with self._connect() as db:
            row = db.execute(
                "select * from workbench_confirmations where id=?", (confirmation_id,)
            ).fetchone()
            return (
                None
                if row is None
                else self._confirmation_from_row(row, redact_arguments=True)
            )

    def confirmation_is_quiesced(self, confirmation_id: str) -> bool | None:
        with self._connect() as db:
            row = db.execute(
                """
                select confirmations.proposer_quiesced_at,
                       confirmations.proposer_run_id,
                       turns.runtime_quiesced_run_id
                from workbench_confirmations as confirmations
                join workbench_turns as turns on turns.id=confirmations.turn_id
                where confirmations.id=?
                """,
                (confirmation_id,),
            ).fetchone()
        if row is None:
            return None
        return bool(
            row["proposer_quiesced_at"]
            and row["proposer_run_id"]
            and row["proposer_run_id"] == row["runtime_quiesced_run_id"]
        )

    def confirmation_quiescence(self, confirmation_ids: list[str]) -> dict[str, bool]:
        if not confirmation_ids:
            return {}
        placeholders = ",".join("?" for _ in confirmation_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select confirmations.id, confirmations.proposer_quiesced_at,
                       confirmations.proposer_run_id, turns.runtime_quiesced_run_id
                from workbench_confirmations confirmations
                join workbench_turns turns on turns.id=confirmations.turn_id
                where confirmations.id in ({placeholders})
                """,
                tuple(confirmation_ids),
            ).fetchall()
        return {
            row["id"]: bool(
                row["proposer_quiesced_at"]
                and row["proposer_run_id"]
                and row["proposer_run_id"] == row["runtime_quiesced_run_id"]
            )
            for row in rows
        }

    def requested_quiesced_confirmation_ids(
        self, *, limit: int = 2
    ) -> tuple[tuple[str, str], ...]:
        if limit < 1 or limit > 2:
            raise ValueError("limit must be between 1 and 2")
        with self._connect() as db:
            rows = db.execute(
                """
                select id, decision_requested from workbench_confirmations
                where status='pending' and decision_requested<>''
                  and proposer_run_id<>'' and proposer_quiesced_at<>''
                order by decision_requested_at, id limit ?
                """,
                (limit,),
            ).fetchall()
        return tuple((row["id"], row["decision_requested"]) for row in rows)

    def claim_confirmation_execution(
        self,
        confirmation_id: str,
        *,
        owner: str,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation | None:
        """Claim one external action; exact argv is returned only to the winner."""
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value, now_text = _utc_store_time(now)
        lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_confirmation(db, confirmation_id)
            status = ConfirmationStatus(row["status"])
            if status is ConfirmationStatus.PENDING:
                requested = str(row["decision_requested"] or "")
                if requested and requested != "confirm":
                    raise ValueError("confirmation has conflicting decision intent")
                if not requested:
                    db.execute(
                        """
                        update workbench_confirmations
                        set decision_requested='confirm', decision_requested_at=?
                        where id=? and status='pending' and decision_requested=''
                        """,
                        (now_text, confirmation_id),
                    )
                    row = self._require_confirmation(db, confirmation_id)
                turn = self._require_turn(db, row["turn_id"])
                if TurnStatus(turn["status"]) is not TurnStatus.WAITING_CONFIRMATION:
                    raise ValueError("confirmation turn is not waiting")
                if (
                    not row["proposer_quiesced_at"]
                    or turn["runtime_quiesced_run_id"] != row["proposer_run_id"]
                ):
                    return None
                unresolved = db.execute(
                    """
                    select 1 from workbench_confirmations
                    where turn_id=? and id<>? and status='confirmed' and result_json=''
                    limit 1
                    """,
                    (row["turn_id"], confirmation_id),
                ).fetchone()
                if unresolved is not None:
                    return None
                if db.execute(
                    """
                    update workbench_confirmations
                    set status='confirmed', decided_at=?, execution_owner=?,
                        execution_lease_expires_at=?, execution_started_at=?
                    where id=? and status='pending'
                    """,
                    (
                        now_text,
                        owner,
                        lease_expires_at,
                        now_text,
                        confirmation_id,
                    ),
                ).rowcount != 1:
                    return None
                return self._confirmation_from_row(
                    self._require_confirmation(db, confirmation_id)
                )
            if status in {
                ConfirmationStatus.CONFIRMED,
                ConfirmationStatus.EXECUTED,
                ConfirmationStatus.FAILED,
            }:
                return None
            raise ValueError("confirmation has already been decided")

    def renew_confirmation_execution_lease(
        self,
        confirmation_id: str,
        *,
        owner: str,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value, now_text = _utc_store_time(now)
        lease_expires_at = (now_value + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_confirmation(db, confirmation_id)
            if (
                ConfirmationStatus(row["status"]) is not ConfirmationStatus.CONFIRMED
                or row["result_json"]
                or row["execution_owner"] != owner
                or row["execution_lease_expires_at"] <= now_text
            ):
                raise ValueError("confirmation execution lease is stale")
            db.execute(
                """
                update workbench_confirmations
                set execution_lease_expires_at=?
                where id=? and status='confirmed' and result_json=''
                  and execution_owner=? and execution_lease_expires_at>?
                """,
                (lease_expires_at, confirmation_id, owner, now_text),
            )
            return self._confirmation_from_row(
                self._require_confirmation(db, confirmation_id), redact_arguments=True
            )

    def finish_confirmation_execution(
        self,
        confirmation_id: str,
        *,
        owner: str,
        status: ConfirmationStatus | str,
        result_json: dict[str, Any] | str,
        resume_context: str,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        try:
            target = ConfirmationStatus(status)
        except ValueError as exc:
            raise ValueError("invalid confirmation result status") from exc
        if target not in {ConfirmationStatus.EXECUTED, ConfirmationStatus.FAILED}:
            raise ValueError("invalid confirmation result status")
        result_text = _json_object_text(result_json, field="result_json")
        resume_context = resume_context.strip()
        if not resume_context:
            raise ValueError("resume_context must be non-empty")
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_confirmation(db, confirmation_id)
            current = ConfirmationStatus(row["status"])
            if current is target:
                return self._confirmation_from_row(row, redact_arguments=True)
            if current is not ConfirmationStatus.CONFIRMED:
                raise ValueError("confirmation execution is not claimed")
            if (
                row["execution_owner"] != owner
                or not row["execution_lease_expires_at"]
                or row["execution_lease_expires_at"] <= now_text
            ):
                raise ValueError("confirmation execution lease is stale")
            if target is ConfirmationStatus.EXECUTED and not row[
                "authorization_consumed_at"
            ]:
                raise ValueError("confirmation authorization was not consumed")
            turn = self._require_turn(db, row["turn_id"])
            if db.execute(
                """
                update workbench_confirmations
                set status=?, result_json=?, execution_owner='',
                    execution_lease_expires_at=''
                where id=? and status='confirmed' and result_json=''
                  and execution_owner=? and execution_lease_expires_at>?
                """,
                (
                    target.value,
                    result_text,
                    confirmation_id,
                    owner,
                    now_text,
                ),
            ).rowcount != 1:
                raise ValueError("confirmation execution lease is stale")
            turn_status = TurnStatus(turn["status"])
            if turn_status in {
                TurnStatus.COMPLETED,
                TurnStatus.STOPPED,
                TurnStatus.FAILED,
            }:
                return self._confirmation_from_row(
                    self._require_confirmation(db, confirmation_id),
                    redact_arguments=True,
                )
            if turn_status is not TurnStatus.WAITING_CONFIRMATION:
                raise ValueError("confirmation turn is not waiting")
            self._append_control_event(
                db,
                row["turn_id"],
                event_type="status_changed",
                payload={
                    "confirmation_id": confirmation_id,
                    "status": TurnStatus.QUEUED.value,
                },
            )
            self._transition_turn(
                db,
                row["turn_id"],
                current=TurnStatus.WAITING_CONFIRMATION,
                target=TurnStatus.QUEUED,
                now_text=now_text,
            )
            db.execute(
                """
                update workbench_turns
                set resume_context=?, error_code=?, error_detail=?, updated_at=?
                where id=? and status='queued'
                """,
                (
                    resume_context,
                    "reviewed_write_failed" if target is ConfirmationStatus.FAILED else "",
                    (
                        "Reviewed external action failed; it will not be replayed."
                        if target is ConfirmationStatus.FAILED
                        else ""
                    ),
                    now_text,
                    row["turn_id"],
                ),
            )
            return self._confirmation_from_row(
                self._require_confirmation(db, confirmation_id), redact_arguments=True
            )

    def cancel_confirmation_execution(
        self,
        confirmation_id: str,
        *,
        resume_context: str,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        resume_context = resume_context.strip()
        if not resume_context:
            raise ValueError("resume_context must be non-empty")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_confirmation(db, confirmation_id)
            current = ConfirmationStatus(row["status"])
            if current is ConfirmationStatus.CANCELLED:
                return self._confirmation_from_row(row, redact_arguments=True)
            if current is not ConfirmationStatus.PENDING:
                raise ValueError("confirmation has already been decided")
            requested = str(row["decision_requested"] or "")
            if requested and requested != "cancel":
                raise ValueError("confirmation has conflicting decision intent")
            if not requested:
                db.execute(
                    """
                    update workbench_confirmations
                    set decision_requested='cancel', decision_requested_at=?
                    where id=? and status='pending' and decision_requested=''
                    """,
                    (now_text, confirmation_id),
                )
                row = self._require_confirmation(db, confirmation_id)
            turn = self._require_turn(db, row["turn_id"])
            if TurnStatus(turn["status"]) is not TurnStatus.WAITING_CONFIRMATION:
                raise ValueError("confirmation turn is not waiting")
            if (
                not row["proposer_quiesced_at"]
                or turn["runtime_quiesced_run_id"] != row["proposer_run_id"]
            ):
                return self._confirmation_from_row(row, redact_arguments=True)
            db.execute(
                """
                update workbench_confirmations
                set status='cancelled', decided_at=?
                where id=? and status='pending'
                """,
                (now_text, confirmation_id),
            )
            self._append_control_event(
                db,
                row["turn_id"],
                event_type="status_changed",
                payload={
                    "confirmation_id": confirmation_id,
                    "status": TurnStatus.QUEUED.value,
                },
            )
            self._transition_turn(
                db,
                row["turn_id"],
                current=TurnStatus.WAITING_CONFIRMATION,
                target=TurnStatus.QUEUED,
                now_text=now_text,
            )
            db.execute(
                "update workbench_turns set resume_context=? where id=?",
                (resume_context, row["turn_id"]),
            )
            return self._confirmation_from_row(
                self._require_confirmation(db, confirmation_id), redact_arguments=True
            )

    def reconcile_confirmed_without_result(
        self, *, now: str | datetime | None = None
    ) -> int:
        return len(self.reconcile_confirmed_without_result_batch(now=now))

    def reconcile_confirmed_without_result_batch(
        self, *, now: str | datetime | None = None
    ) -> tuple[str, ...]:
        """Fail crash-ambiguous writes without executing or replaying them."""
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            rows = list(
                db.execute(
                    LEGACY_CONFIRMED_OWNER_RECOVERY_SQL,
                    ("", WORKBENCH_RECOVERY_BATCH_LIMIT),
                ).fetchall()
            )
            remaining = WORKBENCH_RECOVERY_BATCH_LIMIT - len(rows)
            if remaining:
                rows.extend(
                    db.execute(
                        LEGACY_CONFIRMED_LEASE_RECOVERY_SQL,
                        ("", remaining),
                    ).fetchall()
                )
            remaining = WORKBENCH_RECOVERY_BATCH_LIMIT - len(rows)
            if remaining:
                rows.extend(
                    db.execute(
                        EXPIRED_CONFIRMED_EXECUTION_RECOVERY_SQL,
                        (now_text, remaining),
                    ).fetchall()
                )
            for row in rows:
                recovery_result = _json_object_text(
                    {
                        "code": "confirmation_execution_ambiguous",
                        "retryable": False,
                        "status": "failed",
                    },
                    field="result_json",
                )
                db.execute(
                    """
                    update workbench_confirmations
                    set status='failed', result_json=?, execution_owner='',
                        execution_lease_expires_at=''
                    where id=? and status='confirmed' and result_json=''
                    """,
                    (recovery_result, row["id"]),
                )
                current_turn = self._require_turn(db, row["turn_id"])
                current_status = TurnStatus(current_turn["status"])
                if current_status in {
                    TurnStatus.QUEUED,
                    TurnStatus.RUNNING,
                    TurnStatus.WAITING_CONFIRMATION,
                }:
                    self._resolve_pending_confirmations(
                        db,
                        row["turn_id"],
                        status=ConfirmationStatus.FAILED,
                        now_text=now_text,
                    )
                    self._append_control_event(
                        db,
                        row["turn_id"],
                        event_type="turn_failed",
                        payload={
                            "code": "confirmation_execution_ambiguous",
                            "confirmation_id": row["id"],
                            "status": TurnStatus.FAILED.value,
                        },
                    )
                    self._transition_turn(
                        db,
                        row["turn_id"],
                        current=current_status,
                        target=TurnStatus.FAILED,
                        now_text=now_text,
                        error_code="confirmation_execution_ambiguous",
                        error_detail="External action outcome is ambiguous after restart.",
                        clear_lease=True,
                    )
                else:
                    self._append_control_event(
                        db,
                        row["turn_id"],
                        event_type="status_changed",
                        payload={
                            "code": "confirmation_execution_ambiguous",
                            "confirmation_id": row["id"],
                            "confirmation_status": ConfirmationStatus.FAILED.value,
                        },
                    )
            return tuple(row["id"] for row in rows)

    def complete_turn(
        self,
        turn_id: str,
        *,
        status: TurnStatus | str,
        final_text: str = "",
        error_code: str = "",
        error_detail: str = "",
        event_payload: dict[str, Any] | str | None = None,
        owner: str = "",
        now: str | datetime | None = None,
    ) -> WorkbenchTurn:
        try:
            target_status = TurnStatus(status)
        except ValueError as exc:
            raise ValueError("invalid turn status") from exc
        if target_status is TurnStatus.RUNNING:
            raise ValueError("running turns must be claimed")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = self._require_turn(db, turn_id)
            current = TurnStatus(row["status"])
            if (
                current is TurnStatus.RUNNING
                and row["stop_requested"]
                and target_status in {TurnStatus.COMPLETED, TurnStatus.FAILED}
            ):
                target_status = TurnStatus.STOPPED
                final_text = ""
                error_code = ""
                error_detail = ""
                event_payload = {"status": TurnStatus.STOPPED.value}
            if (current, target_status) in {
                (TurnStatus.RUNNING, TurnStatus.WAITING_CONFIRMATION),
                (TurnStatus.WAITING_CONFIRMATION, TurnStatus.QUEUED),
            }:
                raise ValueError("reserved confirmation transition")
            if target_status not in _TURN_TRANSITIONS[current]:
                raise ValueError("invalid turn transition")
            if current is TurnStatus.RUNNING:
                self._require_executor_lease(
                    db, turn_id, owner=owner, now_text=now_text
                )
            terminal = target_status in {
                TurnStatus.COMPLETED,
                TurnStatus.STOPPED,
                TurnStatus.FAILED,
            }
            if event_payload is not None and not terminal:
                raise ValueError("event_payload requires a terminal turn status")
            if terminal:
                if current is TurnStatus.WAITING_CONFIRMATION:
                    self._resolve_pending_confirmations(
                        db,
                        turn_id,
                        status=(
                            ConfirmationStatus.CANCELLED
                            if target_status is TurnStatus.STOPPED
                            else ConfirmationStatus.FAILED
                        ),
                        now_text=now_text,
                    )
                self._append_control_event(
                    db,
                    turn_id,
                    event_type=(
                        "turn_failed"
                        if target_status is TurnStatus.FAILED
                        else "turn_completed"
                    ),
                    payload=(
                        {"status": target_status.value}
                        if event_payload is None
                        else event_payload
                    ),
                )
            self._transition_turn(
                db,
                turn_id,
                current=current,
                target=target_status,
                now_text=now_text,
                final_text=final_text,
                error_code=error_code,
                error_detail=error_detail,
                clear_lease=True,
            )
            return self._turn_from_row(self._require_turn(db, turn_id))

    def _recover_expired_turns_in_transaction(
        self, db: sqlite3.Connection, *, now_text: str
    ) -> int:
        rows = db.execute(
            """
            select * from workbench_turns
            where status='running' and lease_expires_at<=?
            order by id
            """,
            (now_text,),
        ).fetchall()
        for row in rows:
            if row["stop_requested"]:
                self._append_control_event(
                    db,
                    row["id"],
                    event_type="turn_completed",
                    payload={"status": TurnStatus.STOPPED.value},
                )
                self._transition_turn(
                    db,
                    row["id"],
                    current=TurnStatus.RUNNING,
                    target=TurnStatus.STOPPED,
                    now_text=now_text,
                    clear_lease=True,
                )
                continue
            db.execute(
                """
                update workbench_turns
                set status='queued', lease_owner='', lease_expires_at='', updated_at=?
                where id=? and status='running' and lease_expires_at<=?
                """,
                (now_text, row["id"], now_text),
            )
        return len(rows)

    @staticmethod
    def _resolve_pending_confirmations(
        db: sqlite3.Connection,
        turn_id: str,
        *,
        status: ConfirmationStatus,
        now_text: str,
    ) -> None:
        db.execute(
            """
            update workbench_confirmations
            set status=?, result_json='', decided_at=?
            where turn_id=? and status='pending'
            """,
            (status.value, now_text, turn_id),
        )

    @staticmethod
    def _canonical_uuid(value: str, *, field: str) -> str:
        try:
            return str(UUID(value))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a UUID") from exc

    @staticmethod
    def _commit_attachment_metadata(db: sqlite3.Connection) -> None:
        db.commit()

    @staticmethod
    def _managed_directory(path: Path, *, create: bool) -> bool:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            if not create:
                return False
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                pass
            metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"managed attachment component is a symlink: {path}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"managed attachment component is not a directory: {path}")
        return True

    @contextmanager
    def _attachment_lock(self, *, create_workbench: bool) -> Iterator[Path | None]:
        if not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("attachment locking requires O_NOFOLLOW")
        workbench = self.path.parent / "workbench"
        if not self._managed_directory(workbench, create=create_workbench):
            yield None
            return
        lock_path = workbench / ".attachments.lock"
        try:
            lock_metadata = os.lstat(lock_path)
        except FileNotFoundError:
            lock_metadata = None
        if lock_metadata is not None and not stat.S_ISREG(lock_metadata.st_mode):
            raise ValueError("attachment lock must be a regular file")
        try:
            descriptor = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            raise ValueError("attachment lock must not be a symlink") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("attachment lock must be a regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield workbench
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _attachment_task_directory(
        self, task_id: str, *, create: bool
    ) -> Path | None:
        workbench = self.path.parent / "workbench"
        if not self._managed_directory(workbench, create=create):
            return None
        root = workbench / "attachments"
        if not self._managed_directory(root, create=create):
            return None
        directory = root / task_id
        if not self._managed_directory(directory, create=create):
            return None
        return directory

    @staticmethod
    def _write_attachment_temp(path: Path, content: bytes) -> None:
        if not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("attachment writes require O_NOFOLLOW")
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _after_attachment_temp_created(_path: Path) -> None:
        return None

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_attachment_file(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _safe_attachment_unlink(self, path: Path, directory: Path) -> None:
        try:
            if not self._managed_directory(directory, create=False):
                return
            if path.parent != directory:
                return
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode):
                return
            os.unlink(path)
        except FileNotFoundError:
            return

    def _reconcile_attachments(self) -> None:
        """Keep only generated attachment files that have matching metadata rows."""
        with self._attachment_lock(create_workbench=False) as workbench:
            if workbench is None:
                return
            root = workbench / "attachments"
            if not self._managed_directory(root, create=False):
                return
            referenced_paths: set[Path] = set()
            with self._connect() as db:
                db.execute("begin immediate")
                rows = db.execute(
                    "select id, task_id, storage_path from workbench_attachments"
                ).fetchall()
                for row in rows:
                    try:
                        task_id = self._canonical_uuid(row["task_id"], field="task_id")
                        attachment_id = self._canonical_uuid(row["id"], field="id")
                    except ValueError:
                        db.execute(
                            "delete from workbench_attachments where id=?", (row["id"],)
                        )
                        continue
                    directory = root / task_id
                    if not self._managed_directory(directory, create=False):
                        db.execute(
                            "delete from workbench_attachments where id=?", (row["id"],)
                        )
                        continue
                    expected_path = directory / attachment_id
                    try:
                        metadata = os.lstat(expected_path)
                    except FileNotFoundError:
                        metadata = None
                    if (
                        str(expected_path) != str(row["storage_path"])
                        or metadata is None
                        or not stat.S_ISREG(metadata.st_mode)
                    ):
                        db.execute(
                            "delete from workbench_attachments where id=?", (row["id"],)
                        )
                        continue
                    referenced_paths.add(expected_path)
            for directory in root.iterdir():
                try:
                    metadata = os.lstat(directory)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError(
                        f"managed attachment component is a symlink: {directory}"
                    )
                if not stat.S_ISDIR(metadata.st_mode):
                    continue
                try:
                    task_id = self._canonical_uuid(directory.name, field="task_id")
                except ValueError:
                    continue
                if task_id != directory.name:
                    continue
                for path in directory.iterdir():
                    try:
                        metadata = os.lstat(path)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        continue
                    if path.name.startswith(".") and path.name.endswith(".tmp"):
                        os.unlink(path)
                        continue
                    try:
                        attachment_id = self._canonical_uuid(path.name, field="id")
                    except ValueError:
                        continue
                    if attachment_id == path.name and path not in referenced_paths:
                        os.unlink(path)

    def _append_control_event(
        self,
        db: sqlite3.Connection,
        turn_id: str,
        *,
        event_type: str,
        payload: dict[str, Any] | str,
    ) -> WorkbenchEvent:
        payload_json = _json_object_text(payload, field="payload")
        sequence = int(
            db.execute(
                """
                select coalesce(max(sequence), 0) + 1
                from workbench_events where turn_id=?
                """,
                (turn_id,),
            ).fetchone()[0]
        )
        cursor = db.execute(
            """
            insert into workbench_events (turn_id, sequence, event_type, payload_json)
            values (?, ?, ?, ?)
            """,
            (turn_id, sequence, event_type, payload_json),
        )
        row = db.execute(
            "select * from workbench_events where id=?", (cursor.lastrowid,)
        ).fetchone()
        if row is None:
            raise RuntimeError("control event insert did not create a row")
        return self._event_from_row(row)

    @staticmethod
    def _require_task(db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = db.execute(
            "select * from workbench_tasks where id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise ValueError("workbench task does not exist")
        return row

    @staticmethod
    def _require_turn(db: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
        row = db.execute(
            "select * from workbench_turns where id=?", (turn_id,)
        ).fetchone()
        if row is None:
            raise ValueError("workbench turn does not exist")
        return row

    @staticmethod
    def _require_confirmation(db: sqlite3.Connection, confirmation_id: str) -> sqlite3.Row:
        row = db.execute(
            "select * from workbench_confirmations where id=?", (confirmation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("workbench confirmation does not exist")
        return row

    @classmethod
    def _require_lease(
        cls,
        db: sqlite3.Connection,
        turn_id: str,
        *,
        owner: str,
        now_text: str,
    ) -> sqlite3.Row:
        row = cls._require_turn(db, turn_id)
        if TurnStatus(row["status"]) is not TurnStatus.RUNNING:
            raise ValueError("turn lease requires running status")
        if row["lease_owner"] != owner or row["lease_expires_at"] <= now_text:
            raise ValueError("turn lease is stale")
        return row

    @classmethod
    def _require_executor_lease(
        cls,
        db: sqlite3.Connection,
        turn_id: str,
        *,
        owner: str,
        now_text: str,
    ) -> sqlite3.Row:
        owner = owner.strip()
        if not owner:
            raise ValueError("owner must be non-empty")
        return cls._require_lease(db, turn_id, owner=owner, now_text=now_text)

    @staticmethod
    def _transition_turn(
        db: sqlite3.Connection,
        turn_id: str,
        *,
        current: TurnStatus,
        target: TurnStatus,
        now_text: str,
        final_text: str = "",
        error_code: str = "",
        error_detail: str = "",
        clear_lease: bool = False,
    ) -> None:
        if target not in _TURN_TRANSITIONS[current]:
            raise ValueError("invalid turn transition")
        terminal = target in {TurnStatus.COMPLETED, TurnStatus.STOPPED, TurnStatus.FAILED}
        cursor = db.execute(
            """
            update workbench_turns
            set status=?, final_text=?, error_code=?, error_detail=?,
                lease_owner=case when ? then '' else lease_owner end,
                lease_expires_at=case when ? then '' else lease_expires_at end,
                completed_at=case when ? then ? else completed_at end,
                updated_at=?
            where id=? and status=?
            """,
            (
                target.value,
                final_text,
                error_code,
                error_detail,
                clear_lease,
                clear_lease,
                terminal,
                now_text,
                now_text,
                turn_id,
                current.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("invalid turn transition")

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> WorkbenchTask:
        return WorkbenchTask.model_validate(
            {key: row[key] for key in WorkbenchTask.model_fields}
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> WorkbenchTurn:
        values = {key: row[key] for key in WorkbenchTurn.model_fields}
        values["stop_requested"] = bool(values["stop_requested"])
        return WorkbenchTurn.model_validate(values)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> WorkbenchEvent:
        try:
            payload = json.loads(
                row["payload_json"], parse_constant=_reject_json_constant
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("stored event payload is malformed") from exc
        if not isinstance(payload, dict):
            raise ValueError("stored event payload is malformed")
        return WorkbenchEvent.model_validate(
            {
                "id": row["id"],
                "turn_id": row["turn_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _attachment_from_row(row: sqlite3.Row) -> WorkbenchAttachment:
        return WorkbenchAttachment.model_validate(
            {key: row[key] for key in WorkbenchAttachment.model_fields}
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> WorkbenchArtifact:
        return WorkbenchArtifact.model_validate(
            {key: row[key] for key in WorkbenchArtifact.model_fields}
        )

    @staticmethod
    def _confirmation_from_row(
        row: sqlite3.Row, *, redact_arguments: bool = False
    ) -> WorkbenchConfirmation:
        values = {key: row[key] for key in WorkbenchConfirmation.model_fields}
        if redact_arguments:
            values["arguments_json"] = ""
        return WorkbenchConfirmation.model_validate(values)
