"""Email classifier console APIs.

These endpoints expose local classifier state and user feedback. The explicit
account connectivity test is IMAP-only; SMTP remains disabled.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import imaplib
import json
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import config as app_config
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassificationStatus,
    build_email_action_plan,
    validate_email_category_key,
)
from app.email_category_config import (
    EmailFolderBindingCoordinator,
    VerifiedEmailFolderBinding,
    validate_category_descriptions,
)
from app.email_connector_config import EmailAccountPayload, resolve_secret
from app.email_classifier_retrain import load_retrain_state
from app.email_model_registry import ModelRegistryError
from app.email_pipeline import apply_human_confirmation
from app.email_store import (
    EmailAccountConflict,
    EmailClassificationConflict,
    EmailFolderBindingConflict,
    EmailStore,
)
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


class EmailCategoryCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider_folder_name: str | None = None
    core_description: str = Field(min_length=1)
    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(min_length=1)
    threshold: float = Field(ge=0.0, le=1.0)
    actions: list[str] = Field(default_factory=list)
    action_parameters: dict[str, dict[str, object]] = Field(default_factory=dict)
    enabled: bool = True
    description_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)


class EmailCategoryUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    core_description: str = Field(min_length=1)
    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(min_length=1)
    threshold: float = Field(ge=0.0, le=1.0)
    enabled: bool = True
    description_version: str = Field(min_length=1)
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
    folder_binding_coordinator: EmailFolderBindingCoordinator | None = None,
) -> None:
    del smtp_client_factory  # Legacy injection point; SMTP is intentionally inert.
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
            key: account[key]
            for key in (
                "account_id",
                "display_name",
                "email_address",
                "imap_host",
                "imap_port",
                "imap_tls",
                "imap_username",
                "enabled",
                "scan_folders",
                "scan_interval_seconds",
                "created_at",
                "updated_at",
            )
        }
        return {
            **operational_fields,
            "imap_secret_configured": bool(
                resolve_secret(account["imap_secret_reference"], env)
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

    def default_imap_secret_reference(account_id: object) -> str:
        if not isinstance(account_id, str):
            return ""
        return f"CEO_EMAIL_{account_id.upper().replace('-', '_')}_IMAP_SECRET"

    async def account_payload(
        request: Request,
        *,
        existing_secret_reference: str = "",
    ) -> EmailAccountPayload | JSONResponse:
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
            if "imap_secret_reference" not in decoded:
                decoded["imap_secret_reference"] = (
                    existing_secret_reference
                    or default_imap_secret_reference(decoded.get("account_id"))
                )
            return EmailAccountPayload.model_validate_json(json.dumps(decoded))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
            ValueError,
            TypeError,
        ):
            return error_response(
                "invalid_email_account",
                "Email account configuration is invalid",
                400,
            )

    def save_secret_values(payload: EmailAccountPayload) -> None:
        updates: dict[str, str] = {}
        if payload.imap_secret is not None:
            value = payload.imap_secret.get_secret_value()
            if value.strip():
                updates[payload.imap_secret_reference] = value
        if updates:
            app_config.write_env_values(updates, path=email_env_path)

    def make_imap_client(account: dict[str, Any]):
        if imap_client_factory is not None:
            return imap_client_factory(account["imap_host"], account["imap_port"])
        factory = imaplib.IMAP4_SSL if account["imap_tls"] else imaplib.IMAP4
        return factory(account["imap_host"], account["imap_port"], timeout=10)

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
        return {
            "items": [account_response(row) for row in store.list_accounts()],
            "meta": meta(),
        }

    @app.post("/api/console/email/accounts")
    async def email_account_create(request: Request):
        store = require_store()
        payload = await account_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            create_result = store.create_account_with_category_enablement_snapshot(
                payload.stored_values(),
                allow_shared_email=payload.allow_shared_email,
            )
            row, category_snapshot = create_result
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
                    category_enablement_snapshot=category_snapshot,
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
        existing = store.get_account(account_id)
        payload = await account_payload(
            request,
            existing_secret_reference=(
                existing["imap_secret_reference"] if existing is not None else ""
            ),
        )
        if isinstance(payload, JSONResponse):
            return payload
        if payload.account_id != account_id:
            return error_response(
                "email_account_id_immutable",
                "Email account ID cannot be changed",
                400,
            )
        try:
            update_result = store.update_account_with_category_enablement_snapshot(
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
        row, previous, category_snapshot = update_result
        try:
            save_secret_values(payload)
        except (OSError, ValueError):
            compensated = False
            try:
                compensated = store.restore_account_if_unchanged(
                    previous,
                    expected_updated_at=row["updated_at"],
                    category_enablement_snapshot=category_snapshot,
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
            "smtp": {"enabled": False, "tested": False, "code": "disabled"},
        }
        return {
            "ok": diagnostics["imap"]["ok"],
            "account_id": account_id,
            "diagnostics": diagnostics,
        }

    def meta(
        *,
        page: int | None = None,
        page_size: int | None = None,
        total: int | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
        if page is not None and page_size is not None and total is not None:
            result.update(
                {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "next_cursor": str(page + 1) if page * page_size < total else "",
                    "has_more": page * page_size < total,
                }
            )
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
        return {
            "items": [{**row, "id": str(row["id"])} for row in rows],
            "meta": meta(page=page, page_size=page_size, total=total),
        }

    @app.get("/api/console/email/classifications/{classification_id}")
    def email_classification_detail(classification_id: int):
        email_store = require_store()
        item = email_store.get_classification(classification_id)
        if item is None:
            return error_response("not_found", "Email classification not found", 404)
        return {
            "ok": True,
            "item": {**item, "id": str(item["id"])},
            "observability": email_store.list_email_classification_observability(
                classification_id
            ),
            "meta": meta(),
        }

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
            raise HTTPException(
                status_code=400, detail="email feedback is invalid"
            ) from exc
        learning_result = None
        application = None
        try:
            if email_learning_factory is not None:
                learning_result = email_learning_factory().confirm_and_maybe_retrain(
                    classification_id,
                    category,
                    feedback_request_id=feedback_request_id,
                    expected_current_action_plan_id=(expected_current_action_plan_id),
                )
                row = None if learning_result is None else learning_result.confirmed
            else:
                application = apply_human_confirmation(
                    email_store,
                    classification_id,
                    category,
                    feedback_request_id=feedback_request_id,
                    expected_current_action_plan_id=(expected_current_action_plan_id),
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
            "item": {**row, "id": str(row["id"])},
            "message": "邮件分类反馈已保存",
        }
        if learning_result is not None:
            response["feedback"] = {
                "feedback_request_id": learning_result.feedback_request_id,
                "expected_current_action_plan_id": (
                    learning_result.expected_current_action_plan_id
                ),
                "resulting_action_plan_id": (learning_result.resulting_action_plan_id),
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

    @app.get("/api/console/email/learning")
    def email_learning():
        """Expose immutable model evidence and current retraining state."""

        if email_learning_factory is None:
            return error_response(
                "email_learning_unavailable",
                "Email learning is unavailable",
                503,
            )
        email_store = require_store()
        service = email_learning_factory()
        state = load_retrain_state(service.retrain_state_path)
        registry_issues: list[dict[str, str]] = []
        try:
            active_manifest = service.registry.active_manifest()
            active_model_id = (
                active_manifest.model_id if active_manifest is not None else None
            )
        except (OSError, ValueError, ModelRegistryError):
            active_model_id = None
            registry_issues.append(
                {
                    "model_id": "active-manifest",
                    "integrity_status": "corrupt",
                    "integrity_error": "active_manifest_invalid",
                }
            )
        models: list[dict[str, Any]] = []
        for entry in service.registry.list_model_inventory():
            if entry.integrity_status != "verified":
                registry_issues.append(
                    {
                        "model_id": entry.model_id,
                        "integrity_status": entry.integrity_status,
                        "integrity_error": entry.integrity_error,
                    }
                )
            if entry.metadata is None:
                continue
            metadata = entry.metadata.to_dict()
            lifecycle = [event.__dict__ for event in entry.lifecycle]

            def latest_reason(status: str) -> str:
                return next(
                    (
                        event["reason"]
                        for event in reversed(lifecycle)
                        if event["status"] == status
                    ),
                    "",
                )

            models.append(
                {
                    **metadata,
                    "model_version": metadata["model_id"],
                    "status": entry.status or metadata["status"],
                    "status_reason": entry.status_reason,
                    "candidate_reason": latest_reason("candidate")
                    or metadata["promotion_reason"],
                    "promotion_reason": latest_reason("active"),
                    "rejection_reason": latest_reason("rejected"),
                    "failure_reason": latest_reason("failed")
                    or metadata["failure_reason"],
                    "superseded_reason": latest_reason("previous"),
                    "integrity_status": entry.integrity_status,
                    "integrity_error": entry.integrity_error,
                    "lifecycle": lifecycle,
                }
            )
        pending_examples = len(email_store.list_unincluded_training_examples())
        return {
            "ok": True,
            "learning": {
                "active_model_id": active_model_id,
                "pending_examples": pending_examples,
                "last_trained_feedback_count": state.last_trained_feedback_count,
                "last_trained_at": state.last_trained_at,
                "last_feedback_at": state.last_feedback_at,
                "active_run_id": state.active_run_id,
                "models": models,
                "registry_issues": registry_issues,
                "category_thresholds": {
                    row["category"]: row["threshold"]
                    for row in email_store.list_configs()
                },
            },
            "meta": {"snapshot_at": datetime.now(timezone.utc).isoformat()},
        }

    def category_response(email_store: EmailStore, row: dict[str, Any]):
        return {
            **row,
            "bindings": email_store.list_account_folder_bindings(
                row["category_key"]
            ),
        }

    def category_actions(
        *,
        category_key: str,
        threshold: float,
        actions: list[str],
        action_parameters: dict[str, dict[str, object]],
        config_version: str,
    ) -> tuple[
        tuple[EmailAction, ...],
        dict[EmailAction, dict[str, object]],
    ]:
        parsed_actions = tuple(EmailAction(action) for action in actions)
        parsed_parameters = {
            EmailAction(action): dict(parameters)
            for action, parameters in action_parameters.items()
        }
        if len(parsed_actions) != len(set(parsed_actions)):
            raise ValueError("actions must be unique")
        if EmailAction.AUTO_REPLY in parsed_actions:
            raise ValueError("auto_reply is disabled")
        if EmailAction.UNSUBSCRIBE in parsed_actions:
            raise ValueError("unsubscribe is not a current category action")
        build_email_action_plan(
            classification_id=1,
            account_id="configuration-validation",
            category=category_key,
            classification_source="model",
            confidence=threshold,
            model_id="configuration-validation",
            config_version=config_version,
            actions=parsed_actions,
            action_parameters=parsed_parameters,
            created_at=datetime.now(timezone.utc),
        )
        return parsed_actions, parsed_parameters

    @app.get("/api/console/email/config")
    def email_config():
        email_store = require_store()
        return {
            "items": [
                category_response(email_store, row)
                for row in email_store.list_category_configs()
            ],
            "meta": meta(),
        }

    @app.post("/api/console/email/config")
    async def email_config_create(request: Request):
        email_store = require_store()
        if "application/json" not in request.headers.get("content-type", ""):
            return error_response(
                "invalid_email_category",
                "Email category configuration must be JSON",
                400,
            )
        try:
            payload = EmailCategoryCreatePayload.model_validate(await request.json())
            category_key = validate_email_category_key(payload.category_key)
            if category_key == "junk":
                raise ValueError("junk is a system category")
            provider_folder_name = (
                payload.provider_folder_name or payload.display_name
            )
            validate_category_descriptions(
                display_name=payload.display_name,
                core_description=payload.core_description,
                include=payload.include,
                exclude=payload.exclude,
                description_version=payload.description_version,
            )
            if (
                not provider_folder_name.strip()
                or provider_folder_name != provider_folder_name.strip()
            ):
                raise ValueError("provider_folder_name must be canonical")
            actions, action_parameters = category_actions(
                category_key=category_key,
                threshold=payload.threshold,
                actions=payload.actions,
                action_parameters=payload.action_parameters,
                config_version=payload.config_version,
            )
        except (ValidationError, ValueError, TypeError):
            return error_response(
                "invalid_email_category",
                "Email category configuration is invalid",
                400,
            )
        if email_store.get_category_config(category_key) is not None:
            return error_response(
                "email_folder_binding_conflict",
                "Email category already exists",
                409,
            )
        if folder_binding_coordinator is None:
            return error_response(
                "email_folder_binding_unavailable",
                "Email folder binding is not available",
                503,
            )
        enabled_accounts = [
            account for account in email_store.list_accounts() if account["enabled"]
        ]
        try:
            coordinator_bindings = (
                folder_binding_coordinator.create_and_verify_bindings(
                    category_key=category_key,
                    provider_folder_name=provider_folder_name,
                    enabled_accounts=enabled_accounts,
                )
            )
            if isinstance(coordinator_bindings, (str, bytes)) or not isinstance(
                coordinator_bindings,
                Sequence,
            ):
                raise TypeError("coordinator returned a non-sequence result")
            if any(
                type(binding) is not VerifiedEmailFolderBinding
                for binding in coordinator_bindings
            ):
                raise TypeError("coordinator returned an unverified binding")
            bindings = tuple(coordinator_bindings)
        except EmailFolderBindingConflict:
            return error_response(
                "email_folder_binding_conflict",
                "Email category or folder binding conflicts with stored state",
                409,
            )
        except Exception:
            return error_response(
                "email_folder_binding_unavailable",
                "Email folder binding could not be verified",
                503,
            )
        try:
            row = email_store.create_category_with_bindings(
                category_key=category_key,
                display_name=payload.display_name,
                core_description=payload.core_description,
                include=payload.include,
                exclude=payload.exclude,
                threshold=payload.threshold,
                actions=actions,
                action_parameters=action_parameters,
                enabled=payload.enabled,
                description_version=payload.description_version,
                config_version=payload.config_version,
                bindings=bindings,
            )
        except EmailFolderBindingConflict:
            return error_response(
                "email_folder_binding_conflict",
                "Email category or folder binding conflicts with stored state",
                409,
            )
        except (ValidationError, ValueError, TypeError):
            return error_response(
                "invalid_email_category",
                "Email category configuration is invalid",
                400,
            )
        return JSONResponse(
            {
                "ok": True,
                "item": category_response(email_store, row),
                "message": "邮件分类已创建",
            },
            status_code=201,
        )

    @app.put("/api/console/email/config/{category_key}")
    async def email_config_update(category_key: str, request: Request):
        email_store = require_store()
        if "application/json" not in request.headers.get("content-type", ""):
            return error_response(
                "invalid_email_category",
                "Email category configuration must be JSON",
                400,
            )
        try:
            category_key = validate_email_category_key(category_key)
            payload = EmailCategoryUpdatePayload.model_validate(await request.json())
            validate_category_descriptions(
                display_name="unchanged",
                core_description=payload.core_description,
                include=payload.include,
                exclude=payload.exclude,
                description_version=payload.description_version,
            )
            row = email_store.update_category_descriptions(
                category_key,
                core_description=payload.core_description,
                include=payload.include,
                exclude=payload.exclude,
                threshold=payload.threshold,
                enabled=payload.enabled,
                description_version=payload.description_version,
                config_version=payload.config_version,
            )
        except (ValidationError, ValueError, TypeError):
            return error_response(
                "invalid_email_category",
                "Email category configuration is invalid",
                400,
            )
        if row is None:
            return error_response(
                "email_category_not_found",
                "Email category was not found",
                404,
            )
        return {
            "ok": True,
            "item": category_response(email_store, row),
            "message": "邮件配置已保存",
        }
