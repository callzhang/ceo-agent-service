import json
import os
import sqlite3
import fcntl
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
    WorkbenchConfirmation,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)


_TURN_TRANSITIONS = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.STOPPED},
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

    def list_tasks(self, *, include_archived: bool = False) -> list[WorkbenchTask]:
        with self._connect() as db:
            query = "select * from workbench_tasks"
            if not include_archived:
                query += " where archived_at=''"
            query += " order by updated_at desc, id desc"
            return [self._task_from_row(row) for row in db.execute(query)]

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
        with self._connect() as db:
            self._require_task(db, task_id)
        attachment_id = str(uuid4())
        with self._attachment_lock(create_workbench=True):
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
                            id, task_id, filename, media_type, size_bytes, storage_path
                        ) values (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            attachment_id,
                            task_id,
                            filename,
                            media_type,
                            len(content),
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
            self._require_task(db, task_id)
            existing = db.execute(
                "select * from workbench_turns where client_request_id=?",
                (client_request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["task_id"] != task_id
                    or existing["user_text"] != user_text
                ):
                    raise ValueError("client_request_id conflicts with an existing turn")
                return self._turn_from_row(existing)
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
            try:
                db.execute(
                    """
                    insert into workbench_turns (
                        id, task_id, client_request_id, user_text, status
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        task_id,
                        client_request_id,
                        user_text,
                        TurnStatus.QUEUED.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("task already has an active turn") from exc
            return self._turn_from_row(self._require_turn(db, turn_id))

    def get_turn(self, turn_id: str) -> WorkbenchTurn | None:
        with self._connect() as db:
            row = db.execute(
                "select * from workbench_turns where id=?", (turn_id,)
            ).fetchone()
            return None if row is None else self._turn_from_row(row)

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
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> WorkbenchTurn | None:
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
            self._recover_expired_turns_in_transaction(db, now_text=now_text)
            row = db.execute(
                """
                select workbench_turns.*
                from workbench_turns
                join workbench_tasks on workbench_tasks.id=workbench_turns.task_id
                where workbench_turns.status='queued'
                  and workbench_tasks.archived_at=''
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
                    started_at=case when started_at='' then ? else started_at end,
                    updated_at=?
                where id=? and status='queued'
                """,
                (owner, lease_expires_at, now_text, now_text, row["id"]),
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
            if status is TurnStatus.RUNNING:
                if owner:
                    self._require_lease(db, turn_id, owner=owner, now_text=now_text)
                db.execute(
                    """
                    update workbench_turns
                    set stop_requested=1, updated_at=?
                    where id=? and status='running'
                    """,
                    (now_text, turn_id),
                )
                return self._turn_from_row(self._require_turn(db, turn_id))
            self._append_control_event(
                db,
                turn_id,
                event_type="turn_completed",
                payload={"status": TurnStatus.STOPPED.value},
            )
            if status is TurnStatus.WAITING_CONFIRMATION:
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
            db.execute(
                "update workbench_turns set stop_requested=1 where id=?", (turn_id,)
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

    def events_after(self, turn_id: str, after_id: int = 0) -> list[WorkbenchEvent]:
        if after_id < 0:
            raise ValueError("after_id must not be negative")
        with self._connect() as db:
            self._require_turn(db, turn_id)
            rows = db.execute(
                """
                select * from workbench_events
                where turn_id=? and id>?
                order by id
                """,
                (turn_id, after_id),
            ).fetchall()
            return [self._event_from_row(row) for row in rows]

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

    def create_confirmation(
        self,
        turn_id: str,
        *,
        action_kind: str,
        target: str,
        summary: str,
        risk: str,
        arguments_json: dict[str, Any] | str,
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
            confirmation_id = str(uuid4())
            db.execute(
                """
                insert into workbench_confirmations (
                    id, turn_id, action_kind, target, summary, risk, arguments_json, status
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (confirmation_id, turn_id, *values, arguments_text),
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

    def decide_confirmation(
        self,
        task_id: str,
        confirmation_id: str,
        *,
        decision: ConfirmationStatus | str,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        try:
            decision = ConfirmationStatus(decision)
        except ValueError as exc:
            raise ValueError("invalid confirmation decision") from exc
        if decision not in {ConfirmationStatus.CONFIRMED, ConfirmationStatus.CANCELLED}:
            raise ValueError("confirmation decision must be confirmed or cancelled")
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select confirmations.*, turns.task_id
                from workbench_confirmations as confirmations
                join workbench_turns as turns on turns.id=confirmations.turn_id
                where confirmations.id=?
                """,
                (confirmation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("workbench confirmation does not exist")
            if row["task_id"] != task_id:
                raise ValueError("confirmation does not belong to task")
            confirmation_status = ConfirmationStatus(row["status"])
            if confirmation_status is not ConfirmationStatus.PENDING:
                if confirmation_status is decision:
                    return self._confirmation_from_row(row, redact_arguments=True)
                raise ValueError("confirmation has already been decided")
            turn = self._require_turn(db, row["turn_id"])
            if TurnStatus(turn["status"]) is not TurnStatus.WAITING_CONFIRMATION:
                raise ValueError("confirmation turn is not waiting")
            db.execute(
                """
                update workbench_confirmations
                set status=?, decided_at=?
                where id=? and status='pending'
                """,
                (decision.value, now_text, confirmation_id),
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
            return self._confirmation_from_row(
                self._require_confirmation(db, confirmation_id), redact_arguments=True
            )

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

    def get_confirmation_for_executor(
        self,
        task_id: str,
        confirmation_id: str,
        *,
        owner: str,
        now: str | datetime | None = None,
    ) -> WorkbenchConfirmation:
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                """
                select confirmations.*, turns.task_id
                from workbench_confirmations as confirmations
                join workbench_turns as turns on turns.id=confirmations.turn_id
                where confirmations.id=?
                """,
                (confirmation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("workbench confirmation does not exist")
            if row["task_id"] != task_id:
                raise ValueError("confirmation does not belong to task")
            if ConfirmationStatus(row["status"]) is not ConfirmationStatus.CONFIRMED:
                raise ValueError("confirmation is not confirmed")
            self._require_executor_lease(
                db, row["turn_id"], owner=owner, now_text=now_text
            )
            return self._confirmation_from_row(row)

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
    def _confirmation_from_row(
        row: sqlite3.Row, *, redact_arguments: bool = False
    ) -> WorkbenchConfirmation:
        values = {key: row[key] for key in WorkbenchConfirmation.model_fields}
        if redact_arguments:
            values["arguments_json"] = ""
        return WorkbenchConfirmation.model_validate(values)
