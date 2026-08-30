"""Email classifier console APIs.

These endpoints expose local classifier state and user feedback only. They do
not call IMAP/SMTP or any provider API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_store import EmailClassificationConflict, EmailStore
from app.email_store import EmailPersistenceCorruption


@dataclass(frozen=True)
class _EmailStoreAvailability:
    store: EmailStore | None
    diagnostic: str


class _EmailStoreUnavailable(RuntimeError):
    pass


def _initialization_diagnostic(exc: BaseException) -> str:
    if isinstance(exc, EmailPersistenceCorruption):
        return "email_persistence_corruption"
    if isinstance(exc, sqlite3.OperationalError):
        return "sqlite_operational_error"
    if isinstance(exc, sqlite3.IntegrityError):
        return "sqlite_integrity_error"
    if isinstance(exc, sqlite3.DatabaseError):
        return "sqlite_database_error"
    return "filesystem_error"


class EmailConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = ""
    threshold: float = Field(ge=0.0, le=1.0)
    actions: list[str] = Field(default_factory=list)
    action_parameters: dict[str, dict[str, object]] = Field(default_factory=dict)
    enabled: bool = True
    config_version: str = Field(min_length=1)


def register_email_routes(
    app: Any,
    email_store_factory: Any,
    *,
    email_learning_factory: Any | None = None,
) -> None:
    try:
        email_store = email_store_factory()
        availability = _EmailStoreAvailability(email_store, "")
    except (sqlite3.ProgrammingError, sqlite3.NotSupportedError):
        raise
    except (EmailPersistenceCorruption, sqlite3.DatabaseError, OSError) as exc:
        availability = _EmailStoreAvailability(
            None,
            _initialization_diagnostic(exc),
        )
    app.state.email_store_availability = availability

    def require_store() -> EmailStore:
        if availability.store is None:
            raise _EmailStoreUnavailable
        return availability.store

    @app.exception_handler(_EmailStoreUnavailable)
    async def email_store_unavailable(
        _request: Request,
        _exc: _EmailStoreUnavailable,
    ) -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "code": "email_store_unavailable",
                "message": "Email storage is unavailable",
                "details": {},
            },
            status_code=503,
        )

    def meta(*, page: int | None = None, page_size: int | None = None, total: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        if page is not None and page_size is not None and total is not None:
            result.update({
                "page": page,
                "page_size": page_size,
                "total": total,
                "next_cursor": str(page + 1) if page * page_size < total else "",
                "has_more": page * page_size < total,
            })
        return result

    @app.get("/api/console/email/classifications")
    def email_classifications(
        status: str = Query(default=EmailClassificationStatus.PROCESSED.value),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        email_store = require_store()
        try:
            classification_status = EmailClassificationStatus(status)
        except ValueError:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "invalid_email_status",
                    "message": "status must be processed or pending_feedback",
                    "details": {},
                },
                status_code=400,
            )
        rows, total = email_store.list_classifications(
            status=classification_status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return {"items": rows, "meta": meta(page=page, page_size=page_size, total=total)}

    @app.post("/api/console/email/classifications/{classification_id}/feedback")
    async def email_classification_feedback(classification_id: int, request: Request):
        email_store = require_store()
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(status_code=415, detail="JSON Content-Type required")
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            category = EmailCategory(payload.get("category"))
        except (ValueError, TypeError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail="category is invalid") from exc
        learning_result = None
        try:
            if email_learning_factory is not None:
                learning_result = email_learning_factory().confirm_and_maybe_retrain(
                    classification_id, category
                )
                row = None if learning_result is None else learning_result.confirmed
            else:
                row = email_store.confirm_classification(classification_id, category)
        except EmailClassificationConflict as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "email_classification_conflict",
                    "message": str(exc),
                    "details": {},
                },
                status_code=409,
            )
        if row is None:
            return JSONResponse(
                {
                    "ok": False,
                    "code": "not_found",
                    "message": "Email classification not found",
                    "details": {},
                },
                status_code=404,
            )
        response: dict[str, Any] = {
            "ok": True,
            "item": row,
            "message": "邮件分类反馈已保存",
        }
        if learning_result is not None:
            retrain = learning_result.retrain
            response["learning"] = {
                "retrain_due": bool(retrain and retrain.decision.due),
                "retrain_reason": retrain.decision.reason if retrain else None,
                "promoted": bool(retrain and retrain.training_result),
                "error": learning_result.error,
            }
        return response

    @app.get("/api/console/email/config")
    def email_config():
        email_store = require_store()
        return {"items": email_store.list_configs(), "meta": meta()}

    @app.put("/api/console/email/config/{category}")
    async def email_config_update(category: str, request: Request):
        email_store = require_store()
        try:
            email_category = EmailCategory(category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="category is invalid") from exc
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(status_code=415, detail="JSON Content-Type required")
        try:
            payload = EmailConfigPayload.model_validate(await request.json())
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid email category config") from exc
        try:
            actions = tuple(EmailAction(action) for action in payload.actions)
            action_parameters = {
                EmailAction(action): dict(parameters)
                for action, parameters in payload.action_parameters.items()
            }
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="actions or action_parameters contain an invalid value",
            ) from exc
        if len(actions) != len(set(actions)):
            raise HTTPException(status_code=400, detail="actions must be unique")
        try:
            row = email_store.upsert_config(
                category=email_category,
                description=payload.description,
                threshold=payload.threshold,
                actions=actions,
                action_parameters=action_parameters,
                enabled=payload.enabled,
                config_version=payload.config_version,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail="invalid email action parameters",
            ) from exc
        return {"ok": True, "item": row, "message": "邮件配置已保存"}
