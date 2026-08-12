import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.store import AutoReplyStore, _utc_store_time
from app.workbench.models import (
    ConfirmationStatus,
    TurnStatus,
    WorkbenchAttachment,
    WorkbenchConfirmation,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)


_ACTIVE_TURN_STATUSES = {
    TurnStatus.QUEUED,
    TurnStatus.RUNNING,
    TurnStatus.WAITING_CONFIRMATION,
}
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
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a JSON object") from exc


class WorkbenchStore(AutoReplyStore):
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
        with self._connect() as db:
            self._require_task(db, task_id)
        attachment_id = str(uuid4())
        directory = self.path.parent / "workbench" / "attachments" / task_id
        directory.mkdir(parents=True, exist_ok=True)
        storage_path = directory / attachment_id
        storage_path.write_bytes(content)
        with self._connect() as db:
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
                "select * from workbench_attachments where id=?", (attachment_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("attachment insert did not create a row")
            return self._attachment_from_row(row)

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
            return db.execute(
                """
                update workbench_turns
                set status='queued', lease_owner='', lease_expires_at='', updated_at=?
                where status='running' and lease_expires_at<=?
                """,
                (now_text, now_text),
            ).rowcount

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
            db.execute(
                """
                update workbench_turns
                set status='queued', lease_owner='', lease_expires_at='', updated_at=?
                where status='running' and lease_expires_at<=?
                """,
                (now_text, now_text),
            )
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
            if TurnStatus.STOPPED not in _TURN_TRANSITIONS[status]:
                raise ValueError("invalid turn transition")
            if status is TurnStatus.RUNNING and owner:
                self._require_lease(db, turn_id, owner=owner, now_text=now_text)
            db.execute(
                """
                update workbench_turns
                set status='stopped', stop_requested=1, lease_owner='',
                    lease_expires_at='', completed_at=?, updated_at=?
                where id=?
                """,
                (now_text, now_text, turn_id),
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
            row = self._require_turn(db, turn_id)
            if TurnStatus(row["status"]) is TurnStatus.RUNNING:
                self._require_executor_lease(
                    db, turn_id, owner=owner, now_text=now_text
                )
            if TurnStatus(row["status"]) not in _ACTIVE_TURN_STATUSES:
                raise ValueError("cannot append an event to a terminal turn")
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
        self, task_id: str, provider_session_ref: str
    ) -> WorkbenchTask:
        with self._connect() as db:
            if db.execute(
                """
                update workbench_tasks
                set provider_session_ref=?, updated_at=current_timestamp
                where id=?
                """,
                (provider_session_ref.strip(), task_id),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, task_id))

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
            if ConfirmationStatus(row["status"]) is not ConfirmationStatus.PENDING:
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
        self, task_id: str, confirmation_id: str
    ) -> WorkbenchConfirmation:
        with self._connect() as db:
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
            return self._confirmation_from_row(row)

    def complete_turn(
        self,
        turn_id: str,
        *,
        status: TurnStatus | str,
        final_text: str = "",
        error_code: str = "",
        error_detail: str = "",
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
            if target_status not in _TURN_TRANSITIONS[current]:
                raise ValueError("invalid turn transition")
            if current is TurnStatus.RUNNING:
                self._require_executor_lease(
                    db, turn_id, owner=owner, now_text=now_text
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
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("stored event payload is malformed") from exc
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
