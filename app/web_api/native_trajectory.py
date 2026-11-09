"""Native trajectory entry points use persisted invocation identity, not guessed paths."""

from fastapi import HTTPException, Query

from app.native_trajectory_view import read_attempt_trajectory, trajectory_url
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
from app.web_api.common import snapshot_at


def attempt_link(item):
    return dict(
        id=item.id,
        runtime=item.runtime_kind,
        route=item.route_name,
        status=item.status,
        model=item.model,
        url=trajectory_url(item.id),
        session_id=item.session_id,
        created_at=item.created_at,
        finished_at=item.finished_at,
    )


def run_trajectory_links(store, run_id):
    if not run_id:
        return []
    # parent_id is the canonical Run owner for ordinary and professional runs.
    with store._connect() as db:
        rows = db.execute(
            "select * from runtime_attempts where parent_id=? order by id", (run_id,)
        ).fetchall()
    return [attempt_link(store._agent_runtime_attempt_from_row(row)) for row in rows]


def error_trajectory_links(store, error):
    """Only exact persisted trigger identities can associate a service error."""
    if not error.conversation_id or not error.message_id:
        return []
    with store._connect() as db:
        rows = db.execute(
            f"""select distinct attempts.id
            from ({REPLY_ATTEMPT_COMMON_SQL}) source
            join runs terminal on terminal.id=source.agent_run_id
            join runs on runs.task_id=terminal.task_id
              and runs.execution_generation=terminal.execution_generation
            join runtime_attempts attempts on attempts.parent_id=runs.id
              and attempts.workload_kind='agent_run'
            where source.conversation_id=? and source.trigger_message_id=?
            order by attempts.id""",
            (error.conversation_id, error.message_id),
        ).fetchall()
    return [attempt_link(store.get_agent_runtime_attempt(row["id"])) for row in rows]


def register_native_trajectory_routes(app, store_factory):
    @app.get("/api/console/runtime-attempts/{attempt_id}/trajectory")
    def native_trajectory(
        attempt_id: int, after: int | None = Query(default=None, ge=0)
    ):
        store = store_factory()
        item = store.get_agent_runtime_attempt(attempt_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Runtime attempt not found")
        try:
            page = read_attempt_trajectory(item, after=after)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        siblings = (
            run_trajectory_links(store, item.parent_id)
            if item.parent_id is not None
            else [
                attempt_link(value)
                for value in store.list_runtime_operation_attempts(
                    item.workload_kind, item.workload_key
                )
            ]
        )
        return dict(
            item={**page, "attempt": attempt_link(item), "runtime_attempts": siblings},
            meta={"snapshot_at": snapshot_at()},
        )
