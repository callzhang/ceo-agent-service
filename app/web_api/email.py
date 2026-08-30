"""Email classifier console APIs.

These endpoints expose local classifier state and user feedback only. They do
not call IMAP/SMTP or any provider API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import imaplib
import json
from pathlib import Path
import sqlite3
import smtplib
from typing import Any

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import config as app_config
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
)
from app.email_connector_config import EmailAccountPayload, resolve_secret
from app.email_pipeline import apply_human_confirmation
from app.email_store import EmailAccountConflict, EmailClassificationConflict, EmailStore
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


class EmailFeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str = Field(min_length=1)
    feedback_request_id: str = Field(min_length=1, max_length=200)
    expected_current_action_plan_id: str | None

    @field_validator("feedback_request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("feedback_request_id must not contain outer whitespace")
        return value

    @field_validator("expected_current_action_plan_id")
    @classmethod
    def validate_expected_action_plan_id(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError(
                "expected_current_action_plan_id must be null or non-empty"
            )
        return value


def register_email_routes(
    app: Any,
    email_store_factory: Any,
    *,
    email_learning_factory: Any | None = None,
    email_env_path: Path | None = None,
    imap_client_factory: Any | None = None,
    smtp_client_factory: Any | None = None,
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

    def secret_environment() -> dict[str, str]:
        return app_config.effective_env_values(email_env_path)

    def account_response(account: dict[str, Any]) -> dict[str, Any]:
        env = secret_environment()
        operational_fields = {
            key: value
            for key, value in account.items()
            if key not in {"imap_secret_reference", "smtp_secret_reference"}
        }
        return {
            **operational_fields,
            "imap_secret_configured": bool(
                resolve_secret(account["imap_secret_reference"], env)
            ),
            "smtp_secret_configured": bool(
                resolve_secret(account["smtp_secret_reference"], env)
            ),
        }

    def error_response(code: str, message: str, status_code: int) -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "code": code,
                "message": message,
                "details": {},
            },
            status_code=status_code,
        )

    def secret_write_error(*, compensated: bool) -> JSONResponse:
        if not compensated:
            return error_response(
                "email_account_consistency_failed",
                "Email account save could not be completed safely",
                500,
            )
        return error_response(
            "email_account_secret_write_failed",
            "Email account secrets could not be saved; retry is safe",
            503,
        )

    async def account_payload(request: Request) -> EmailAccountPayload | JSONResponse:
        if "application/json" not in request.headers.get("content-type", ""):
            return error_response(
                "json_content_type_required",
                "JSON Content-Type required",
                415,
            )
        try:
            raw = await request.body()
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValueError("JSON object required")
            return EmailAccountPayload.model_validate_json(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError, TypeError):
            return error_response(
                "invalid_email_account",
                "Email account configuration is invalid",
                400,
            )

    def save_secret_values(payload: EmailAccountPayload) -> None:
        updates: dict[str, str] = {}
        for reference, secret in (
            (payload.imap_secret_reference, payload.imap_secret),
            (payload.smtp_secret_reference, payload.smtp_secret),
        ):
            if secret is None:
                continue
            value = secret.get_secret_value()
            if value.strip():
                updates[reference] = value
        if updates:
            app_config.write_env_values(updates, path=email_env_path)

    def make_imap_client(account: dict[str, Any]):
        if imap_client_factory is not None:
            return imap_client_factory(account["imap_host"], account["imap_port"])
        factory = imaplib.IMAP4_SSL if account["imap_tls"] else imaplib.IMAP4
        return factory(account["imap_host"], account["imap_port"], timeout=10)

    def make_smtp_client(account: dict[str, Any]):
        if smtp_client_factory is not None:
            return smtp_client_factory(account["smtp_host"], account["smtp_port"])
        factory = smtplib.SMTP_SSL if account["smtp_tls"] else smtplib.SMTP
        return factory(account["smtp_host"], account["smtp_port"], timeout=10)

    def test_imap(account: dict[str, Any], secret: str | None) -> dict[str, Any]:
        if not secret:
            return {"ok": False, "code": "secret_not_configured"}
        client = None
        try:
            client = make_imap_client(account)
            login_status, _ = client.login(account["imap_username"], secret)
            if str(login_status).upper() != "OK":
                raise RuntimeError("IMAP login failed")
            for folder in account["scan_folders"]:
                select_status, _ = client.select(folder, readonly=True)
                if str(select_status).upper() != "OK":
                    raise RuntimeError("IMAP readonly select failed")
            return {"ok": True, "code": "connected"}
        except Exception:
            return {"ok": False, "code": "connection_failed"}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    try:
                        client.shutdown()
                    except Exception:
                        pass

    def test_smtp(account: dict[str, Any], secret: str | None) -> dict[str, Any]:
        if not secret:
            return {"ok": False, "code": "secret_not_configured"}
        client = None
        try:
            client = make_smtp_client(account)
            client.login(account["smtp_username"], secret)
            return {"ok": True, "code": "connected"}
        except Exception:
            return {"ok": False, "code": "connection_failed"}
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass

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

    @app.get("/api/console/email/accounts")
    def email_accounts():
        store = require_store()
        return {"items": [account_response(row) for row in store.list_accounts()], "meta": meta()}

    @app.post("/api/console/email/accounts")
    async def email_account_create(request: Request):
        store = require_store()
        payload = await account_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            row = store.create_account(
                payload.stored_values(),
                allow_shared_email=payload.allow_shared_email,
            )
        except EmailAccountConflict as exc:
            return error_response(
                exc.code,
                "Email account conflicts with existing configuration",
                409,
            )
        try:
            save_secret_values(payload)
        except (OSError, ValueError):
            try:
                compensated = store.delete_account_if_unchanged(
                    payload.account_id,
                    expected_updated_at=row["updated_at"],
                )
            except sqlite3.DatabaseError:
                compensated = False
            return secret_write_error(compensated=compensated)
        return JSONResponse(
            {
                "ok": True,
                "item": account_response(row),
                "restart_required": True,
                "message": "Email account configuration saved",
            },
            status_code=201,
        )

    @app.put("/api/console/email/accounts/{account_id}")
    async def email_account_update(account_id: str, request: Request):
        store = require_store()
        payload = await account_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        if payload.account_id != account_id:
            return error_response(
                "email_account_id_immutable",
                "Email account ID cannot be changed",
                400,
            )
        try:
            update_result = store.update_account(
                account_id,
                payload.stored_values(),
                allow_shared_email=payload.allow_shared_email,
            )
        except EmailAccountConflict as exc:
            return error_response(
                exc.code,
                "Email account conflicts with existing configuration",
                409,
            )
        if update_result is None:
            return error_response("not_found", "Email account not found", 404)
        row, previous = update_result
        try:
            save_secret_values(payload)
        except (OSError, ValueError):
            compensated = False
            try:
                compensated = store.restore_account_if_unchanged(
                    previous,
                    expected_updated_at=row["updated_at"],
                )
            except sqlite3.DatabaseError:
                compensated = False
            return secret_write_error(compensated=compensated)
        return {
            "ok": True,
            "item": account_response(row),
            "restart_required": True,
            "message": "Email account configuration saved",
        }

    @app.post("/api/console/email/accounts/{account_id}/test")
    def email_account_test(account_id: str):
        store = require_store()
        account = store.get_account(account_id)
        if account is None:
            return error_response("not_found", "Email account not found", 404)
        env = secret_environment()
        diagnostics = {
            "imap": test_imap(
                account,
                resolve_secret(account["imap_secret_reference"], env),
            ),
            "smtp": test_smtp(
                account,
                resolve_secret(account["smtp_secret_reference"], env),
            ),
        }
        return {
            "ok": all(item["ok"] for item in diagnostics.values()),
            "account_id": account_id,
            "diagnostics": diagnostics,
        }

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
            payload = EmailFeedbackPayload.model_validate(await request.json())
            category = EmailCategory(payload.category)
            feedback_request_id = payload.feedback_request_id
            expected_current_action_plan_id = payload.expected_current_action_plan_id
        except (ValueError, TypeError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail="email feedback is invalid") from exc
        learning_result = None
        application = None
        try:
            if email_learning_factory is not None:
                learning_result = email_learning_factory().confirm_and_maybe_retrain(
                    classification_id,
                    category,
                    feedback_request_id=feedback_request_id,
                    expected_current_action_plan_id=(
                        expected_current_action_plan_id
                    ),
                )
                row = None if learning_result is None else learning_result.confirmed
            else:
                application = apply_human_confirmation(
                    email_store,
                    classification_id,
                    category,
                    feedback_request_id=feedback_request_id,
                    expected_current_action_plan_id=(
                        expected_current_action_plan_id
                    ),
                    now=datetime.now(timezone.utc),
                )
                row = None if application is None else application.confirmed
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
            response["feedback"] = {
                "feedback_request_id": learning_result.feedback_request_id,
                "expected_current_action_plan_id": (
                    learning_result.expected_current_action_plan_id
                ),
                "resulting_action_plan_id": (
                    learning_result.resulting_action_plan_id
                ),
                "applied": learning_result.feedback_applied,
                "replayed": learning_result.feedback_replayed,
            }
        else:
            assert application is not None
            response["feedback"] = {
                "feedback_request_id": application.feedback_request_id,
                "expected_current_action_plan_id": (
                    application.expected_current_action_plan_id
                ),
                "resulting_action_plan_id": application.resulting_action_plan_id,
                "applied": application.applied,
                "replayed": application.replayed,
            }
        if learning_result is not None:
            retrain = learning_result.retrain
            response["learning"] = {
                "retrain_due": bool(retrain and retrain.decision.due),
                "retrain_reason": retrain.decision.reason if retrain else None,
                "training_run_id": (
                    retrain.training_run.run_id
                    if retrain and retrain.training_run
                    else None
                ),
                "training_status": (
                    retrain.training_run.status
                    if retrain and retrain.training_run
                    else None
                ),
                "promoted": bool(
                    retrain
                    and retrain.training_run
                    and retrain.training_run.status == "succeeded"
                ),
                "error": learning_result.error,
            }
        return response

    @app.post("/api/console/email/training")
    def email_manual_training():
        if email_learning_factory is None:
            return error_response(
                "email_learning_unavailable",
                "Email learning is unavailable",
                503,
            )
        result = email_learning_factory().request_manual_training()
        run = result.training_run
        return JSONResponse(
            {
                "ok": True,
                "learning": {
                    "retrain_due": result.decision.due,
                    "retrain_reason": result.decision.reason,
                    "pending_examples": result.decision.pending_examples,
                    "training_run_id": run.run_id if run else None,
                    "training_status": run.status if run else None,
                },
            },
            status_code=202 if run else 200,
        )

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
