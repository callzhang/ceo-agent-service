from app.deployment_drain import claim_connection
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.store import SQLITE_BUSY_TIMEOUT_SECONDS, AutoReplyStore, _utc_store_time
from app.task_entity_storage import create_task as create_native_task, update_task_cas
from app.task12_storage import task12_projection
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from app.run_storage import insert_run
from app.workbench.native_events import (
    AGENT_EVENTS,
    forget_live_turn,
    friday_refs,
    hydrate_events,
    hydrate_friday_turns,
    hydrate_turn,
    remember_live_payload,
    started_tool_event_id,
    stored_payload,
    terminal_cached_turn_ids,
)
from app.workbench.models import (
    TurnStatus,
    WorkbenchAttachment,
    WorkbenchArtifact,
    WorkbenchEvent,
    WorkbenchTask,
    WorkbenchTurn,
)


_TURN_TRANSITIONS = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.STOPPED, TurnStatus.FAILED},
    TurnStatus.RUNNING: {
        TurnStatus.COMPLETED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
    TurnStatus.COMPLETED: set(),
    TurnStatus.STOPPED: set(),
    TurnStatus.FAILED: set(),
    TurnStatus.WAITING_CONFIRMATION: set(),
}

_DEFAULT_WORKBENCH_TASK_TITLE = "新任务"
_DERIVED_TASK_TITLE_MAX_LENGTH = 32


class WorkbenchConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _begin_immediate_with_retry(
    db: sqlite3.Connection, *, attempts: int = MAX_AUTOMATIC_ATTEMPTS,
    delay_seconds: float = 0.25,
) -> None:
    """Serialize startup recovery with the worker's SQLite writer."""
    attempts = min(max(1, attempts), MAX_AUTOMATIC_ATTEMPTS)
    for attempt in range(attempts):
        try:
            db.execute("begin immediate")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_seconds * (attempt + 1))


WORKBENCH_TASK_SQL = """select cast(id as text) as id,title,runtime_kind,
    provider_session_ref,archived_at,created_at,updated_at from workbench_tasks"""
WORKBENCH_TURN_SQL = """select cast(t.id as text) as id,cast(w.workbench_task_id as text) as task_id,
    w.client_request_id,w.task_sequence,w.user_text,t.status,w.stop_requested,
    '' as final_text,w.error_code,w.error_detail,w.resume_context,w.execution_run_id,w.runtime_quiesced_run_id,
    t.lease_owner,t.lease_expires_at,w.started_at,w.completed_at,t.created_at,t.updated_at
    from tasks t join workbench_turns w on w.task_id=t.id where t.kind='workbench_turn'"""


# Attachments are owned by the native Workbench parent. Public DTO identities
# remain decimal strings while storage_path remains an independent file locator.
WORKBENCH_ATTACHMENT_SQL = """select cast(id as text) as id,
    cast(workbench_task_id as text) as task_id,client_request_id,filename,
    media_type,size_bytes,content_sha256,storage_path,created_at
    from workbench_attachments"""
WORKBENCH_ARTIFACT_SQL = """select cast(a.id as text) as id,
    cast(a.workbench_turn_id as text) as turn_id,a.label,a.path,a.media_type,a.created_at
    from workbench_artifacts a"""
WORKBENCH_EVENT_SQL = """select id,cast(task_id as text) as turn_id,sequence,event_type,
    payload_json,created_at from workbench_events"""
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


def _derive_task_title(user_text: str) -> str:
    title = " ".join(user_text.split())
    if len(title) <= _DERIVED_TASK_TITLE_MAX_LENGTH:
        return title
    return title[: _DERIVED_TASK_TITLE_MAX_LENGTH - 1].rstrip() + "…"


class WorkbenchStore(AutoReplyStore):
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_seconds: int = SQLITE_BUSY_TIMEOUT_SECONDS,
    ):
        super().__init__(path, busy_timeout_seconds=busy_timeout_seconds)
        self._reconcile_attachments()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        terminal_ids: list[str] = []
        with super()._connect() as db:
            yield db
            terminal_ids = terminal_cached_turn_ids(db, self.path)
        for turn_id in terminal_ids:
            forget_live_turn(self.path, turn_id)

    def create_task(self, *, title: str, runtime_kind: str) -> WorkbenchTask:
        title = title.strip()
        runtime_kind = runtime_kind.strip()
        if not title:
            raise ValueError("title must be non-empty")
        if not runtime_kind:
            raise ValueError("runtime_kind must be non-empty")
        with self._connect() as db:
            row = db.execute(
                "insert into workbench_tasks(title,runtime_kind) values(?,?) "
                "returning id,title,runtime_kind,provider_session_ref,archived_at,created_at,updated_at",
                (title, runtime_kind),
            ).fetchone()
            return self._task_from_row(row)

    def get_task(self, task_id: str) -> WorkbenchTask | None:
        native_id = self._native_task_id(task_id)
        with self._connect() as db:
            row = db.execute(
                f"select * from ({WORKBENCH_TASK_SQL}) as workbench_tasks where id=?",
                (str(native_id),),
            ).fetchone()
            return None if row is None else self._task_from_row(row)

    def get_task_summary(self, task_id: str) -> tuple[WorkbenchTask, str] | None:
        native_id = self._native_task_id(task_id)
        with self._connect() as db:
            row = db.execute(
                """
                select cast(tasks.id as text) as id,tasks.title,tasks.runtime_kind,
                       tasks.provider_session_ref,tasks.archived_at,tasks.created_at,tasks.updated_at,
                       coalesce((select turn_task.status from workbench_turns turns
                                 join tasks turn_task on turn_task.id=turns.task_id
                                 where turns.workbench_task_id=tasks.id
                                 order by turns.task_sequence desc limit 1), 'idle')
                           as latest_state
                from workbench_tasks tasks where tasks.id=?
                """,
                (native_id,),
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
            parameters.extend((cursor[0], cursor[0], self._native_task_id(cursor[1])))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select cast(tasks.id as text) as id,tasks.title,tasks.runtime_kind,
                       tasks.provider_session_ref,tasks.archived_at,tasks.created_at,tasks.updated_at,
                       coalesce((select turn_task.status from workbench_turns turns
                                 join tasks turn_task on turn_task.id=turns.task_id
                                 where turns.workbench_task_id=tasks.id
                                 order by turns.task_sequence desc limit 1), 'idle')
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
        native_id = self._native_task_id(task_id)
        with self._connect() as db:
            if db.execute(
                "update workbench_tasks set title=?,updated_at=current_timestamp where id=?",
                (title, native_id),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, str(native_id)))

    def archive_task(
        self, task_id: str, *, now: str | datetime | None = None
    ) -> WorkbenchTask:
        _, now_text = _utc_store_time(now)
        native_id = self._native_task_id(task_id)
        with self._connect() as db:
            _begin_immediate_with_retry(db)
            self._require_task(db, str(native_id))
            active = db.execute(
                """
                select 1 from workbench_turns turns join tasks turn_task on turn_task.id=turns.task_id
                where turns.workbench_task_id=? and turn_task.status in ('queued', 'running')
                limit 1
                """,
                (native_id,),
            ).fetchone()
            if active is not None:
                raise WorkbenchConflictError(
                    "task_has_active_turn", "workbench task has an active turn"
                )
            if db.execute(
                "update workbench_tasks set archived_at=?,updated_at=? where id=?",
                (now_text, now_text, native_id),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, str(native_id)))

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
        native_task_id = self._native_task_id(task_id)
        task_id = str(native_task_id)
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
                    f"""select * from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments
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
                    inserted = db.execute(
                        "insert into workbench_attachments(workbench_task_id,client_request_id,"
                        "filename,media_type,size_bytes,content_sha256,storage_path) "
                        "values(?,?,?,?,?,?,?) returning id",
                        (native_task_id, client_request_id, filename, media_type, len(content),
                         content_sha256, str(storage_path)),
                    ).fetchone()
                    native_attachment_id = int(inserted[0])
                    row = db.execute(
                        f"select * from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments where id=?",
                        (str(native_attachment_id),),
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
        native_id = self._native_task_id(task_id)
        with self._connect() as db:
            self._require_task(db, str(native_id))
            rows = db.execute(
                f"""
                select * from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments
                where task_id=?
                order by created_at, cast(id as integer)
                """,
                (str(native_id),),
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
                f"select * from ({WORKBENCH_TURN_SQL}) as workbench_turns where client_request_id=?",
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
            parent_id = self._native_task_id(task_id)
            active = db.execute(
                "select 1 from workbench_turns w join tasks t on t.id=w.task_id "
                "where w.workbench_task_id=? and t.status in ('queued','running') limit 1",
                (parent_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("task already has an active turn")
            task_sequence = int(db.execute(
                "select coalesce(max(task_sequence),0)+1 from workbench_turns where workbench_task_id=?",
                (parent_id,),
            ).fetchone()[0])
            now_text = db.execute("select current_timestamp").fetchone()[0]
            native = create_native_task(db, values={
                "kind": "workbench_turn", "status": TurnStatus.QUEUED.value,
                "created_at": now_text, "updated_at": now_text,
            })
            turn_id = str(native["id"])
            try:
                db.execute(
                    "insert into workbench_turns(task_id,workbench_task_id,client_request_id,"
                    "task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,"
                    "execution_run_id,runtime_quiesced_run_id,started_at,completed_at) "
                    "values(?,?,?,?,?,0,'','','','','','','')",
                    (native["id"], parent_id, client_request_id, task_sequence, user_text),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("task already has an active turn") from exc
            if task_sequence == 1 and task["title"] == _DEFAULT_WORKBENCH_TASK_TITLE:
                db.execute(
                    "update workbench_tasks set title=?,updated_at=current_timestamp where id=? and title=?",
                    (_derive_task_title(user_text), parent_id, _DEFAULT_WORKBENCH_TASK_TITLE),
                )
            self._append_control_event(
                db,
                turn_id,
                event_type="status_changed",
                payload={"status": TurnStatus.QUEUED.value},
            )
            return self._turn_from_row(self._require_turn(db, turn_id))

    def get_turn(self, turn_id: str) -> WorkbenchTurn | None:
        turn_id = str(self._native_task_id(turn_id))
        with self._connect() as db:
            row = db.execute(
                f"select * from ({WORKBENCH_TURN_SQL}) as workbench_turns where id=?", (turn_id,)
            ).fetchone()
            turn = None if row is None else hydrate_turn(db, self._turn_from_row(row))
            refs = friday_refs(db, [turn]) if turn is not None else {}
        if turn is not None:
            hydrate_friday_turns([turn], refs)
        return turn

    def list_turns(self, task_id: str) -> list[WorkbenchTurn]:
        task_id = str(self._native_task_id(task_id))
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                f"""
                select * from ({WORKBENCH_TURN_SQL}) as workbench_turns
                where task_id=? order by task_sequence desc
                """,
                (task_id,),
            ).fetchall()
            turns = [hydrate_turn(db, self._turn_from_row(row)) for row in rows]
            refs = friday_refs(db, turns)
        hydrate_friday_turns(turns, refs)
        return turns

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
        # Idle polling needs no writer lock; expired leases still require recovery.
        # Close the read before BEGIN IMMEDIATE and recheck everything under that lock.
        with self._connect() as db:
            ready = db.execute(
                f"""
                select 1 from ({WORKBENCH_TURN_SQL}) as workbench_turns
                where status='queued'
                   or (status='running' and lease_expires_at<=?)
                limit 1
                """,
                (now_text,),
            ).fetchone()
        if ready is None:
            return None
        with claim_connection(self) as db:
            if db is None:
                return None
            db.execute("begin immediate")
            self._recover_expired_turns_in_transaction(db, now_text=now_text)
            row = db.execute(
                f"""
                select workbench_turns.*
                from ({WORKBENCH_TURN_SQL}) as workbench_turns
                join workbench_tasks on workbench_tasks.id=cast(workbench_turns.task_id as integer)
                where workbench_turns.status='queued'
                  and workbench_tasks.archived_at=''
                order by workbench_turns.created_at, workbench_turns.id
                limit 1
                """
            ).fetchone()
            if row is None:
                return None
            turn_id = int(row["id"])
            updated = db.execute(
                "update tasks set status='running',lease_owner=?,lease_expires_at=?,updated_at=? "
                "where id=? and kind='workbench_turn' and status='queued' returning id",
                (owner, lease_expires_at, now_text, turn_id),
            ).fetchone()
            if updated is None:
                return None
            db.execute(
                "update workbench_turns set execution_run_id=?,runtime_quiesced_run_id='',"
                "started_at=case when started_at='' then ? else started_at end where task_id=?",
                (execution_run_id, now_text, turn_id),
            )
            return self._turn_from_row(self._require_turn(db, str(turn_id)))

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
                "update tasks set lease_expires_at=?,updated_at=? where id=? and kind='workbench_turn' "
                "and status='running' and lease_owner=? and lease_expires_at>?",
                (lease_expires_at, now_text, int(turn_id), owner, now_text),
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
            db.execute("update workbench_turns set stop_requested=1 where task_id=?", (int(turn_id),))
            db.execute(
                "update tasks set updated_at=? where id=? and kind='workbench_turn'",
                (now_text, int(turn_id)),
            )
            self._append_control_event(
                db,
                turn_id,
                event_type="turn_completed",
                payload={"status": TurnStatus.STOPPED.value},
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
        full_payload = json.loads(payload_json)
        _, now_text = _utc_store_time(now)
        with self._connect() as db:
            db.execute("begin immediate")
            self._require_executor_lease(db, turn_id, owner=owner, now_text=now_text)
            native_turn_id = int(turn_id)
            ordinal = 0
            if event_type == "tool_completed":
                ordinal = db.execute(
                    "select count(*)+1 from workbench_events where task_id=? and event_type='tool_completed'",
                    (native_turn_id,),
                ).fetchone()[0]
                call_id = full_payload.get("tool_call_id")
                if isinstance(call_id, str):
                    started_id = started_tool_event_id(self.path, turn_id, call_id)
                    if started_id is not None:
                        db.execute(
                            "update workbench_events set payload_json=? where id=? and task_id=? and event_type='tool_started'",
                            (json.dumps({"native_ordinal": ordinal}), started_id, native_turn_id),
                        )
            elif event_type == "text_delta":
                value = full_payload.get("native_ordinal")
                ordinal = value if isinstance(value, int) and value > 0 else 1
            elif event_type in {"thinking_summary", "file_changed"}:
                ordinal = db.execute(
                    "select count(*)+1 from workbench_events where task_id=? and event_type=?",
                    (native_turn_id, event_type),
                ).fetchone()[0]
            payload_json = _json_object_text(
                stored_payload(event_type, full_payload, ordinal=ordinal), field="payload"
            )
            expected_sequence = int(
                db.execute(
                    "select coalesce(max(sequence),0)+1 from workbench_events where task_id=?",
                    (native_turn_id,),
                ).fetchone()[0]
            )
            if sequence != expected_sequence:
                raise ValueError("event sequence must be next")
            try:
                cursor = db.execute(
                    "insert into workbench_events(task_id,sequence,event_type,payload_json,created_at) values(?,?,?,?,?)",
                    (native_turn_id, sequence, event_type, payload_json, now_text),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("event sequence already exists") from exc
            event = db.execute(
                f"select * from ({WORKBENCH_EVENT_SQL}) as workbench_events where id=?", (cursor.lastrowid,)
            ).fetchone()
            if event is None:
                raise RuntimeError("event insert did not create a row")
            result = self._event_from_row(event)
            if event_type in AGENT_EVENTS:
                remember_live_payload(self.path, turn_id, result.id, event_type, full_payload)
                result.payload = full_payload
        return result

    def events_after(
        self, turn_id: str, after_id: int = 0, *, limit: int | None = None
    ) -> list[WorkbenchEvent]:
        if after_id < 0:
            raise ValueError("after_id must not be negative")
        if limit is not None and (limit < 1 or limit > 1000):
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            self._require_turn(db, turn_id)
            query = f"""
                select * from ({WORKBENCH_EVENT_SQL}) as workbench_events
                where cast(turn_id as integer)=?
                  and id>? order by id
            """
            parameters: tuple[Any, ...] = (int(turn_id), after_id)
            if limit is not None:
                query += " limit ?"
                parameters += (limit,)
            rows = db.execute(query, parameters).fetchall()
            return hydrate_events(db, self.path, [self._event_from_row(row) for row in rows])

    def event_stream_snapshot(
        self, turn_id: str, after_id: int = 0, *, limit: int = 1000
    ) -> tuple[list[WorkbenchEvent], WorkbenchTurn | None]:
        if after_id < 0 or limit < 1 or limit > 1000:
            raise ValueError("invalid event stream cursor or limit")
        with self._connect() as db:
            db.execute("begin")
            rows = db.execute(
                f"""
                select * from ({WORKBENCH_EVENT_SQL}) as workbench_events
                where cast(turn_id as integer)=? and id>? order by id limit ?
                """,
                (int(turn_id), after_id, limit),
            ).fetchall()
            turn_row = db.execute(
                f"select * from ({WORKBENCH_TURN_SQL}) as workbench_turns where id=?", (turn_id,)
            ).fetchone()
            turn = None if turn_row is None else hydrate_turn(db, self._turn_from_row(turn_row))
            refs = friday_refs(db, [turn]) if turn is not None else {}
            snapshot = (
                hydrate_events(db, self.path, [self._event_from_row(row) for row in rows]),
                turn,
            )
        if turn is not None:
            hydrate_friday_turns([turn], refs)
        return snapshot

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
            native_turn_id = int(turn_id)
            expected = int(db.execute(
                "select coalesce(max(sequence),0)+1 from workbench_events where task_id=?",
                (native_turn_id,),
            ).fetchone()[0])
            if sequence != expected:
                raise ValueError("event sequence must be next")
            artifact = db.execute(
                "insert into workbench_artifacts(workbench_turn_id,label,path,media_type) "
                "values(?,?,?,?) returning id",
                (native_turn_id, label, path, media_type),
            ).fetchone()
            artifact_id = str(artifact["id"])
            payload["artifact_id"] = artifact_id
            cursor = db.execute(
                "insert into workbench_events(task_id,sequence,event_type,payload_json,created_at) values(?,?,?,?,?)",
                (native_turn_id, sequence, "artifact_created", json.dumps(payload, separators=(",", ":")), now_text),
            )
            artifact_row = db.execute(
                f"select * from ({WORKBENCH_ARTIFACT_SQL}) as workbench_artifacts where id=?", (artifact_id,)
            ).fetchone()
            event_row = db.execute(
                f"select * from ({WORKBENCH_EVENT_SQL}) as workbench_events where id=?", (cursor.lastrowid,)
            ).fetchone()
            return self._artifact_from_row(artifact_row), self._event_from_row(event_row)

    def list_artifacts(self, task_id: str) -> list[WorkbenchArtifact]:
        with self._connect() as db:
            self._require_task(db, task_id)
            rows = db.execute(
                f"""
                select artifacts.*
                from ({WORKBENCH_ARTIFACT_SQL}) as artifacts
                join workbench_turns turns on turns.task_id=cast(artifacts.turn_id as integer)
                where turns.workbench_task_id=? order by artifacts.created_at, artifacts.id
                """,
                (int(task_id),),
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
        attachment_after: tuple[str, str] | None = None,
    ) -> tuple[
        WorkbenchTask,
        list[WorkbenchTurn],
        list[WorkbenchEvent],
        list[WorkbenchAttachment],
        list[WorkbenchArtifact],
        dict[str, Any],
    ]:
        if turn_limit < 1 or turn_limit > 100 or event_limit < 1 or event_limit > 1000:
            raise ValueError("invalid timeline limit")
        with self._connect() as db:
            db.execute("begin")
            task_row = self._require_task(db, task_id)
            state_row = db.execute(
                "select t.status from workbench_turns w join tasks t on t.id=w.task_id "
                "where w.workbench_task_id=? order by w.task_sequence desc limit 1",
                (int(task_id),),
            ).fetchone()
            task_state = state_row["status"] if state_row is not None else "idle"
            cursor_sql = ""
            turn_parameters: list[Any] = [int(task_id)]
            if before_sequence is not None:
                cursor_sql = "where task_sequence<?"
                turn_parameters.append(before_sequence)
            turn_parameters.append(turn_limit + 1)
            turn_rows = db.execute(
                f"""select * from ({WORKBENCH_TURN_SQL}) as workbench_turns
                    where cast(workbench_turns.task_id as integer)=?
                    {cursor_sql} order by task_sequence desc limit ?""",
                tuple(turn_parameters),
            ).fetchall()
            has_more = len(turn_rows) > turn_limit
            turn_rows = turn_rows[:turn_limit]
            turn_ids = [row["id"] for row in turn_rows]
            placeholders = ",".join("?" for _ in turn_ids)
            if turn_ids:
                event_cursor_sql = ""
                event_parameters: list[Any] = [int(turn_id) for turn_id in turn_ids]
                if event_before is not None:
                    event_cursor_sql = "where id<?"
                    event_parameters.append(event_before)
                event_parameters.append(event_limit + 1)
                event_rows = db.execute(
                    f"""select * from ({WORKBENCH_EVENT_SQL}) as workbench_events
                        where cast(turn_id as integer) in ({placeholders})
                        {event_cursor_sql.replace('where', 'and') if event_cursor_sql else ''}
                        order by id desc limit ?""",
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
                    f"""select * from ({WORKBENCH_ARTIFACT_SQL}) as workbench_artifacts where cast(turn_id as integer) in ({placeholders})
                        {artifact_cursor_sql} order by created_at,id limit 101""",
                    tuple(artifact_parameters),
                ).fetchall()
            else:
                event_rows, artifact_rows = [], []
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
                f"""select * from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments where task_id=?
                   {attachment_cursor_sql} order by created_at,cast(id as integer) limit 101""",
                tuple(attachment_parameters),
            ).fetchall()
            events_has_more = len(event_rows) > event_limit
            artifacts_has_more = len(artifact_rows) > 100
            attachments_has_more = len(attachment_rows) > 100
            events_next_cursor = (
                event_rows[event_limit - 1]["id"] if events_has_more else None
            )
            event_rows = event_rows[:event_limit]
            artifact_rows = artifact_rows[:100]
            attachment_rows = attachment_rows[:100]
            next_cursor = (
                turn_rows[-1]["task_sequence"]
                if has_more and turn_rows
                else None
            )
            turns = [hydrate_turn(db, self._turn_from_row(row)) for row in turn_rows]
            refs = friday_refs(db, turns)
            snapshot = (
                self._task_from_row(task_row),
                turns,
                hydrate_events(db, self.path, [self._event_from_row(row) for row in reversed(event_rows)]),
                [self._attachment_from_row(row) for row in attachment_rows],
                [self._artifact_from_row(row) for row in artifact_rows],
                {
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "task_state": task_state,
                    "events_has_more": events_has_more,
                    "events_next_cursor": events_next_cursor,
                    "artifacts_has_more": artifacts_has_more,
                    "attachments_has_more": attachments_has_more,
                },
            )
        hydrate_friday_turns(turns, refs)
        return snapshot

    def get_artifact(self, artifact_id: str) -> WorkbenchArtifact | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({WORKBENCH_ARTIFACT_SQL}) as workbench_artifacts where id=?", (artifact_id,)
            ).fetchone()
            return None if row is None else self._artifact_from_row(row)

    def workbench_stats(self) -> dict[str, Any]:
        with self._connect() as db:
            task_row = db.execute(
                f"""
                select count(*) as total,
                       sum(case when archived_at='' then 1 else 0 end) as active,
                       sum(case when archived_at<>'' then 1 else 0 end) as archived
                from ({WORKBENCH_TASK_SQL}) as workbench_tasks
                """
            ).fetchone()
            turn_counts = {status.value: 0 for status in TurnStatus}
            turn_counts.update(
                {
                    row["status"]: int(row["count"])
                    for row in db.execute(
                        f"select status, count(*) as count from ({WORKBENCH_TURN_SQL}) as workbench_turns group by status"
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
                        f"""
                        select event_type, count(*) as count
                        from ({WORKBENCH_EVENT_SQL}) as workbench_events group by event_type
                        """
                    )
                }
            )
            duration = db.execute(
                f"""
                select count(*) as completed_count,
                       coalesce(sum(julianday(completed_at) - julianday(started_at)), 0)
                           * 86400.0 as total_seconds
                from ({WORKBENCH_TURN_SQL}) as workbench_turns
                where started_at<>'' and completed_at<>''
                """
            ).fetchone()
            completed_count = int(duration["completed_count"] or 0)
            total_seconds = max(0.0, float(duration["total_seconds"] or 0.0))
            attachments = int(
                db.execute(f"select count(*) from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments").fetchone()[0]
            )
            artifacts = int(
                db.execute(f"select count(*) from ({WORKBENCH_ARTIFACT_SQL}) as workbench_artifacts").fetchone()[0]
            )
        return {
            "tasks": {
                "total": int(task_row["total"] or 0),
                "active": int(task_row["active"] or 0),
                "archived": int(task_row["archived"] or 0),
            },
            "turns": turn_counts,
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
        native_turn_id = self._native_task_id(turn_id)
        with self._connect() as db:
            db.execute("begin immediate")
            owner = owner.strip()
            if not owner:
                raise ValueError("owner must be non-empty")
            turn = db.execute(
                "select turns.workbench_task_id from workbench_turns turns "
                "join tasks turn_task on turn_task.id=turns.task_id "
                "where turns.task_id=? and turn_task.status='running' "
                "and turn_task.lease_owner=? and turn_task.lease_expires_at>?",
                (native_turn_id, owner, now_text),
            ).fetchone()
            if turn is None:
                raise ValueError("turn lease is stale")
            if db.execute(
                "update workbench_tasks set provider_session_ref=?,updated_at=? where id=?",
                (provider_session_ref.strip(), now_text, turn["workbench_task_id"]),
            ).rowcount != 1:
                raise ValueError("workbench task does not exist")
            return self._task_from_row(self._require_task(db, str(turn["workbench_task_id"])))

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
                f"""
                select storage_path, media_type from ({WORKBENCH_ATTACHMENT_SQL}) as workbench_attachments
                where task_id=? order by created_at, id
                """,
                (turn["task_id"],),
            ).fetchall()
            return tuple((Path(row["storage_path"]), row["media_type"]) for row in rows)


















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
                final_text="",
                error_code=error_code,
                error_detail=error_detail,
                clear_lease=True,
            )
            result = self._turn_from_row(self._require_turn(db, turn_id))
            if target_status is TurnStatus.COMPLETED:
                result.final_text = final_text
        return result

    def _recover_expired_turns_in_transaction(
        self, db: sqlite3.Connection, *, now_text: str
    ) -> int:
        rows = db.execute(
            f"""
            select * from ({WORKBENCH_TURN_SQL}) as workbench_turns
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
                "update tasks set status='queued',lease_owner='',lease_expires_at='',updated_at=? "
                "where id=? and kind='workbench_turn' and status='running' and lease_expires_at<=?",
                (now_text, int(row["id"]), now_text),
            )
        return len(rows)


    @staticmethod
    def _native_task_id(value: str) -> int:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError("workbench task ID must be a canonical decimal integer")
        native_id = int(value)
        if native_id <= 0 or str(native_id) != value:
            raise ValueError("workbench task ID must be a canonical decimal integer")
        return native_id

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
        """Keep files addressed by their persisted locator and matching metadata."""
        with self._attachment_lock(create_workbench=False) as workbench:
            if workbench is None:
                return
            root = Path(os.path.abspath(workbench / "attachments"))
            if not self._managed_directory(root, create=False):
                return
            referenced_paths: set[Path] = set()
            with self._connect() as db:
                db.execute("begin immediate")
                rows = db.execute(
                    "select id,storage_path from workbench_attachments"
                ).fetchall()
                for row in rows:
                    expected_path = Path(row["storage_path"])
                    if not expected_path.is_absolute():
                        expected_path = self.path.parent / expected_path
                    expected_path = Path(os.path.abspath(expected_path))
                    try:
                        components = expected_path.relative_to(root).parts
                    except ValueError:
                        components = ()
                    if len(components) < 2:
                        db.execute(
                            "delete from workbench_attachments where id=?", (row["id"],)
                        )
                        continue
                    directory = root
                    for component in components[:-1]:
                        directory = directory / component
                        if not self._managed_directory(directory, create=False):
                            break
                    else:
                        try:
                            metadata = os.lstat(expected_path)
                        except FileNotFoundError:
                            metadata = None
                        if metadata is not None and stat.S_ISREG(metadata.st_mode):
                            referenced_paths.add(expected_path)
                            continue
                    db.execute(
                        "delete from workbench_attachments where id=?", (row["id"],)
                    )
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
                        self._canonical_uuid(path.name, field="id")
                    except ValueError:
                        continue
                    if path not in referenced_paths:
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
        native_turn_id = int(turn_id)
        sequence = int(db.execute(
            "select coalesce(max(sequence),0)+1 from workbench_events where task_id=?",
            (native_turn_id,),
        ).fetchone()[0])
        cursor = db.execute(
            "insert into workbench_events(task_id,sequence,event_type,payload_json,created_at) values(?,?,?,?,current_timestamp)",
            (native_turn_id, sequence, event_type, payload_json),
        )
        row = db.execute(
            f"select * from ({WORKBENCH_EVENT_SQL}) as workbench_events where id=?", (cursor.lastrowid,)
        ).fetchone()
        if row is None:
            raise RuntimeError("control event insert did not create a row")
        return self._event_from_row(row)

    @staticmethod
    def _require_task(db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        native_id = WorkbenchStore._native_task_id(task_id)
        row = db.execute(
            f"select * from ({WORKBENCH_TASK_SQL}) as workbench_tasks where id=?",
            (str(native_id),),
        ).fetchone()
        if row is None:
            raise ValueError("workbench task does not exist")
        return row

    @staticmethod
    def _require_turn(db: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
        native_id = WorkbenchStore._native_task_id(turn_id)
        row = db.execute(
            f"select * from ({WORKBENCH_TURN_SQL}) as workbench_turns where id=?", (str(native_id),)
        ).fetchone()
        if row is None:
            raise ValueError("workbench turn does not exist")
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
            "update tasks set status=?,lease_owner=case when ? then '' else lease_owner end,"
            "lease_expires_at=case when ? then '' else lease_expires_at end,updated_at=? "
            "where id=? and kind='workbench_turn' and status=?",
            (target.value, clear_lease, clear_lease, now_text, int(turn_id), current.value),
        )
        if cursor.rowcount != 1:
            raise ValueError("invalid turn transition")
        db.execute(
            "update workbench_turns set error_code=?,error_detail=?,"
            "completed_at=case when ? then ? else completed_at end where task_id=?",
            (error_code, error_detail, terminal, now_text, int(turn_id)),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> WorkbenchTask:
        values = {key: row[key] for key in WorkbenchTask.model_fields}
        values["id"] = str(values["id"])
        return WorkbenchTask.model_validate(
            values
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
        values = {key: row[key] for key in WorkbenchAttachment.model_fields}
        values["id"] = str(values["id"])
        values["task_id"] = str(values["task_id"])
        return WorkbenchAttachment.model_validate(
            values
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> WorkbenchArtifact:
        return WorkbenchArtifact.model_validate(
            {key: row[key] for key in WorkbenchArtifact.model_fields}
        )

