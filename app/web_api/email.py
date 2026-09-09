"""Email classifier console APIs.

These endpoints expose local classifier state and user feedback. The explicit
account connectivity test is IMAP-only; SMTP remains disabled.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import imaplib
import json
import math
from pathlib import Path
import re
import sqlite3
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

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
from app.email_classifier_runtime import (
    ONLINE_ACTIVATION_FILENAME,
    EmailClassifierRuntimeMode,
    derive_runtime_mode,
    assess_online_promotion_gate,
    switch_online_model,
    online_control_history,
    measured_end_to_end_latency,
)
from app.email_model_registry import (
    EMBEDDING_MODEL_ID_PREFIX,
    MODEL_ID_PREFIX,
    ModelRegistryError,
    candidate_maturity_from_mapping,
    assess_staged_candidate_readiness,
)
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


_MODEL_REASON_CODES = frozenset(
    {
        "two_consecutive_candidates_required",
        "two_distinct_candidates_required",
        "candidate_compatibility_changed",
        "historical_systematic_error_unresolved",
        "maturity_gate_not_met",
        "two_consecutive_compatible_candidates_passed",
        "candidate_evidence_invalid",
        "independent_snapshot_required",
        "label_watermark_not_advanced",
        "label_watermark_regressed",
        "evaluation_evidence_not_advanced",
        "evaluation_evidence_regressed",
        "no_staged_candidate",
        "training_not_ready",
        "candidate_validation_pending",
        "macro_f1_and_latency_passed",
        "initial_validation_passed",
        "subscription_precision_below_0.95",
        "artifact_reload_failed",
        "eligible",
        "insufficient_validation_samples",
        "precision_below_threshold",
    }
)
_MODEL_INTEGRITY_CODES = frozenset(
    {
        "artifact_digest_mismatch",
        "metadata_invalid",
        "artifact_missing",
        "metadata_missing",
        "lifecycle_invalid",
        "manifest_invalid",
    }
)
_RUNTIME_FALLBACK_CODES = frozenset(
    {
        "model_rejected",
        "embedding_timeout",
        "OnlineEmbeddingBatchTimeout",
        "OnlineEmbeddingBatchError",
        "OnlineEmbeddingBatchClosed",
        "EmailClassifierUnavailable",
        "runtime_failure",
    }
)


def _controlled_model_reason(value: object) -> str:
    reason = str(value or "")
    if not reason:
        return ""
    return reason if reason in _MODEL_REASON_CODES else "unavailable"


def _controlled_integrity_error(value: object) -> str:
    code = str(value or "")
    return code if code in _MODEL_INTEGRITY_CODES else "registry_integrity_error"


_MODEL_EVIDENCE_ID = re.compile(
    r"email-embedding-mlp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}"
)
_SNAPSHOT_EVIDENCE_ID = re.compile(
    r"email-folder-snapshot-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{12}"
)
_DESCRIPTION_EVIDENCE_VERSION = re.compile(r"description-set-sha256:[0-9a-f]{64}")


def _safe_evidence_identifier(
    value: object, field: str, pattern: re.Pattern[str]
) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _safe_exact_evidence_value(value: object, field: str, expected: str) -> str:
    if value != expected:
        raise ValueError(f"{field} is invalid")
    return expected


def _safe_external_reference(value: object, field: str, placeholder: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"{field} is invalid")
    return placeholder


def _safe_evidence_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64 or any(
        character.isspace() for character in value
    ):
        raise ValueError(f"{field} is invalid")
    text = value
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} is invalid")
    return text


def _safe_evidence_count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
        raise ValueError(f"{field} is invalid")
    return value


def _safe_evidence_float(value: object, field: str, *, unit: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (unit and result > 1):
        raise ValueError(f"{field} is invalid")
    return result


def _safe_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _safe_evidence_status(value: object) -> str:
    if value not in {"candidate", "active", "previous", "rejected", "failed"}:
        raise ValueError("status is invalid")
    return str(value)


def _project_staged_model_evidence(
    evidence: Any,
) -> dict[str, object]:
    """Return only reviewed, non-sensitive model evidence fields."""

    if not isinstance(evidence, dict):
        raise ValueError("model evidence must be an object")
    for key in ("metrics", "head_latency_ms", "end_to_end_latency_ms", "evaluation", "training", "parameters"):
        if key in evidence and not isinstance(evidence[key], dict):
            raise ValueError(f"{key} evidence must be an object")
    maturity = candidate_maturity_from_mapping(evidence)
    hashes_value = evidence.get("hashes")
    if (
        not isinstance(hashes_value, dict)
        or not isinstance(hashes_value.get("artifact_sha256"), str)
        or len(hashes_value["artifact_sha256"]) != 64
    ):
        raise ValueError("model evidence artifact digest is invalid")
    compatibility = evidence.get("compatibility")
    historical = evidence.get("historical_eligibility")
    readiness = evidence.get("whole_model_readiness")
    # Earlier immutable evidence named classifier-head measurements latency_ms.
    latency = evidence.get("head_latency_ms", evidence.get("latency_ms"))
    split_counts = evidence.get("split_counts")
    hashes = evidence.get("hashes")
    if not isinstance(compatibility, dict) or not isinstance(historical, dict):
        raise ValueError("model evidence nested structure is invalid")
    safe_categories = [
        validate_email_category_key(category)
        for category in maturity.compatibility.enabled_categories
    ]
    projected_compatibility = {
        "enabled_categories": safe_categories,
        "description_version": _safe_evidence_identifier(
            maturity.compatibility.description_version,
            "description_version",
            _DESCRIPTION_EVIDENCE_VERSION,
        ),
        "input_schema_version": _safe_exact_evidence_value(
            maturity.compatibility.input_schema_version,
            "input_schema_version",
            "email-folder-model-input-v2",
        ),
        "embedding_model_reference": _safe_external_reference(
            maturity.compatibility.embedding_model_id,
            "embedding_model_id",
            "configured-external-model",
        ),
        "embedding_revision_reference": _safe_external_reference(
            maturity.compatibility.embedding_revision,
            "embedding_revision",
            "configured-external-revision",
        ),
        "head_format": _safe_exact_evidence_value(
            maturity.compatibility.head_format,
            "head_format",
            "description-mlp-v1",
        ),
        "parent_model_id": (
            None
            if maturity.compatibility.parent_model_id is None
            else _safe_evidence_identifier(
                maturity.compatibility.parent_model_id,
                "parent_model_id",
                _MODEL_EVIDENCE_ID,
            )
        ),
    }

    def eligibility(value: object, field: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError(f"{field} is invalid")
        eligible = value.get("eligible")
        if type(eligible) is not bool:
            raise ValueError(f"{field}.eligible is invalid")
        return {
            "precision": _safe_evidence_float(
                value.get("precision"), f"{field}.precision", unit=True
            ),
            "accepted_hits": _safe_evidence_count(
                value.get("accepted_hits"), f"{field}.accepted_hits"
            ),
            "independent_groups": _safe_evidence_count(
                value.get("independent_groups"), f"{field}.independent_groups"
            ),
            "eligible": eligible,
        }

    categories = historical.get("categories")
    if not isinstance(categories, dict) or set(categories) != set(
        maturity.compatibility.enabled_categories
    ):
        raise ValueError("historical categories are invalid")
    projected_historical = {
        "categories": {
            category: eligibility(categories[category], f"categories.{category}")
            for category in maturity.compatibility.enabled_categories
        },
        "important": eligibility(historical.get("important"), "important"),
    }
    if not isinstance(readiness, dict) or type(readiness.get("ready")) is not bool:
        raise ValueError("whole model readiness is invalid")
    passing_model_ids = readiness.get("passing_model_ids")
    if not isinstance(passing_model_ids, list) or len(passing_model_ids) > 2:
        raise ValueError("passing model ids are invalid")
    projected_readiness = {
        "ready": readiness["ready"],
        "passing_model_ids": [
            _safe_evidence_identifier(item, "passing_model_id", _MODEL_EVIDENCE_ID)
            for item in passing_model_ids
        ],
        "reason": _controlled_model_reason(readiness.get("reason")),
    }
    if not isinstance(latency, dict):
        raise ValueError("latency evidence is invalid")
    projected_latency = {
        key: _safe_evidence_float(latency.get(key), f"latency.{key}")
        for key in ("p50", "p95", "p99")
    }
    projected_split_counts = None
    if isinstance(split_counts, dict):
        important = split_counts.get("important")
        projected_split_counts = {
            key: _safe_evidence_count(split_counts.get(key), f"split_counts.{key}")
            for key in ("train", "validation", "test", "test_evaluations")
        }
        if isinstance(important, dict):
            projected_split_counts["important"] = {
                key: _safe_evidence_count(
                    important.get(key), f"split_counts.important.{key}"
                )
                for key in ("train", "validation", "test")
            }
        else:
            raise ValueError("important split counts are invalid")
    else:
        raise ValueError("split counts are invalid")
    def measured(value, *, unit=False, count=False):
        if value is None:
            return None
        return (_safe_evidence_count(value, "metric") if count
                else _safe_evidence_float(value, "metric", unit=unit))

    training = evidence.get("training")
    projected_training = None
    if training is not None:
        times = {key: (_safe_evidence_timestamp(training[key], key) if training.get(key) is not None else None)
                 for key in ("started_at", "completed_at")}
        if (all(times.values()) and datetime.fromisoformat(times["completed_at"])
                < datetime.fromisoformat(times["started_at"])):
            raise ValueError("training completion precedes start")
        projected_training = {
            **times, "duration_ms": measured(training.get("duration_ms")),
            **{key: measured(training.get(key), count=True) for key in (
                "sample_count", "category_sample_count", "account_count", "group_count",
            )},
        }
    parameters = evidence.get("parameters")
    projected_parameters = None
    if parameters is not None:
        layers = parameters.get("hidden_layer_sizes")
        if layers is not None and (not isinstance(layers, list) or not layers
                or any(type(size) is not int or not 0 < size <= 1_000_000_000 for size in layers)):
            raise ValueError("hidden layer sizes are invalid")
        thresholds = parameters.get("category_thresholds")
        if thresholds is not None and not isinstance(thresholds, dict):
            raise ValueError("category thresholds are invalid")
        projected_parameters = {
            **{key: measured(parameters.get(key)) for key in ("alpha", "beta", "regularization_alpha")},
            **{key: measured(parameters.get(key), count=True) for key in ("max_iter", "random_seed")},
            "important_threshold": measured(parameters.get("important_threshold"), unit=True),
            "category_thresholds": ({key: measured(thresholds.get(key), unit=True) for key in safe_categories}
                                    if thresholds is not None else None),
            "hidden_layer_sizes": layers,
            "solver": (_safe_exact_evidence_value(parameters["solver"], "solver", "lbfgs")
                       if parameters.get("solver") is not None else None),
            "head_format": (_safe_exact_evidence_value(parameters["head_format"], "head_format", "description-mlp-v1")
                            if parameters.get("head_format") is not None else None),
        }
    raw_metrics = evidence.get("metrics", {})
    metric_categories = raw_metrics.get("categories", {})
    projected_metrics = {
        "accuracy": measured(raw_metrics.get("accuracy"), unit=True),
        "macro_f1": measured(raw_metrics.get("macro_f1"), unit=True),
        "categories": {
            category: {
                **{key: measured(metric_categories[category].get(key), unit=True)
                   for key in ("precision", "recall", "f1", "accepted_precision", "threshold")},
                **{key: measured(metric_categories[category].get(key), count=True)
                   for key in ("support", "accepted_hits", "independent_groups")},
            } for category in safe_categories
        },
        "important": {
            key: measured(raw_metrics.get("important", {}).get(key), unit=True)
            for key in ("precision", "recall", "f1", "accepted_precision")
        },
    }
    evaluation = evidence.get("evaluation")
    projected_evaluation = None
    if isinstance(evaluation, dict) and evaluation.get("protocol") == "email-folder-heldout-v1":
        test_digest = _safe_digest(evaluation.get("test_digest"), "test_digest")
        comparison = json.dumps([evaluation["protocol"], test_digest, sorted(safe_categories)])
        projected_evaluation = {"protocol": evaluation["protocol"], "test_digest": test_digest,
                                "comparability_key": sha256(comparison.encode()).hexdigest()}
    projected_end_to_end = measured_end_to_end_latency(evidence.get("end_to_end_latency_ms"))
    return {
        "training": projected_training,
        "parameters": projected_parameters,
        "metrics": projected_metrics,
        "evaluation": projected_evaluation,
        "head_timing_percentiles_ms": projected_latency,
        "end_to_end_latency_ms": projected_end_to_end,
        "model_id": _safe_evidence_identifier(
            evidence["model_id"], "model_id", _MODEL_EVIDENCE_ID
        ),
        "status": _safe_evidence_status(evidence.get("status")),
        "trained_at": _safe_evidence_timestamp(evidence.get("trained_at"), "trained_at"),
        "training_snapshot_id": _safe_evidence_identifier(
            maturity.source_snapshot_id,
            "source_snapshot_id",
            _SNAPSHOT_EVIDENCE_ID,
        ),
        "training_snapshot_digest": _safe_digest(
            maturity.source_snapshot_digest, "source_snapshot_digest"
        ),
        "training_snapshot_observed_at": _safe_evidence_timestamp(
            maturity.source_snapshot_observed_at, "source_snapshot_observed_at"
        ),
        "folder_label_watermark": _safe_evidence_count(
            maturity.folder_label_watermark, "folder_label_watermark"
        ),
        "important_label_watermark": _safe_evidence_count(
            maturity.important_label_watermark, "important_label_watermark"
        ),
        "compatibility": projected_compatibility,
        "split_counts": projected_split_counts,
        "historical_eligibility": projected_historical,
        "promotion_evidence": projected_readiness,
        "timing_percentiles_ms": projected_latency,
        "artifact_sha256": (
            _safe_digest(hashes.get("artifact_sha256"), "artifact_sha256")
            if isinstance(hashes, dict)
            else None
        ),
        "failure_reason": _controlled_model_reason(evidence.get("failure_reason")),
    }


def _safe_staged_evidence(
    registry: Any, registry_issues: list[dict[str, str]]
) -> list[dict[str, object]]:
    try:
        inventory = registry.list_staged_evidence_inventory()
    except (OSError, TypeError, ValueError, ModelRegistryError):
        registry_issues.append(
            {
                "model_id": "staged-evidence",
                "integrity_status": "corrupt",
                "integrity_error": "staged_evidence_invalid",
            }
        )
        return []
    valid: list[dict[str, object]] = []
    for entry in inventory:
        row = entry["evidence"]
        try:
            if row is None:
                raise ValueError("unreadable staged evidence")
            _project_staged_model_evidence(row)
        except (KeyError, OverflowError, TypeError, ValueError):
            registry_issues.append(
                {
                    "model_id": (entry["model_id"] if _MODEL_EVIDENCE_ID.fullmatch(str(entry["model_id"])) else "staged-evidence"),
                    "integrity_status": "corrupt",
                    "integrity_error": "staged_evidence_invalid",
                }
            )
            continue
        valid.append(dict(row))
    return sorted(valid, key=lambda row: (str(row.get("trained_at", "")), str(row["model_id"])))


def _project_legacy_model_inventory(entry: object) -> dict[str, object] | None:
    metadata_value = getattr(entry, "metadata", None)
    if metadata_value is None:
        return None
    metadata = metadata_value.to_dict()
    projected = {
        key: metadata.get(key)
        for key in (
            "model_id",
            "parent_model_id",
            "model_family",
            "tokenizer_version",
            "feature_version",
            "training_dataset_version",
            "trained_at",
            "training_started_at",
            "training_finished_at",
            "sample_count",
            "new_sample_count",
            "category_counts",
            "account_counts",
            "validation_method",
            "accuracy",
            "macro_f1",
            "prediction_latency_p50_ms",
            "prediction_latency_p95_ms",
            "artifact_sha256",
        )
    }
    per_category_metrics = metadata.get("per_category_metrics")
    projected["per_category_metrics"] = {
        str(category): {
            key: values.get(key)
            for key in (
                "precision",
                "recall",
                "f1",
                "validation_sample_count",
                "validation_positive_support",
                "automatic_candidate_count",
                "evaluated_threshold",
                "configured_threshold",
                "minimum_precision",
                "minimum_validation_samples",
                "auto_action_eligible",
            )
        }
        | {
            "eligibility_reason": _controlled_model_reason(
                values.get("eligibility_reason")
            )
        }
        for category, values in (
            per_category_metrics.items()
            if isinstance(per_category_metrics, dict)
            else ()
        )
        if isinstance(values, dict)
    }
    lifecycle = tuple(getattr(entry, "lifecycle", ()))

    def latest_reason(status: str) -> str:
        return next(
            (
                _controlled_model_reason(event.reason)
                for event in reversed(lifecycle)
                if event.status == status
            ),
            "",
        )

    projected.update(
        {
            "model_version": metadata.get("model_id"),
            "status": getattr(entry, "status", None) or metadata.get("status"),
            "status_reason": _controlled_model_reason(
                getattr(entry, "status_reason", "")
            ),
            "candidate_reason": latest_reason("candidate")
            or _controlled_model_reason(metadata.get("promotion_reason")),
            "promotion_reason": latest_reason("active"),
            "rejection_reason": latest_reason("rejected"),
            "failure_reason": latest_reason("failed")
            or _controlled_model_reason(metadata.get("failure_reason")),
            "superseded_reason": (
                "superseded" if latest_reason("previous") else ""
            ),
            "integrity_status": getattr(entry, "integrity_status", "corrupt"),
            "integrity_error": (
                _controlled_integrity_error(getattr(entry, "integrity_error", ""))
                if getattr(entry, "integrity_error", "")
                else ""
            ),
            "lifecycle": [
                {
                    "event_id": event.event_id,
                    "model_id": event.model_id,
                    "status": event.status,
                    "occurred_at": event.occurred_at,
                }
                for event in lifecycle
            ],
        }
    )
    return projected


def _active_runtime_mode(registry: Any):
    return derive_runtime_mode(registry)


def _active_embedding_model_id(
    registry: Any, mode: EmailClassifierRuntimeMode
) -> str | None:
    if mode is not EmailClassifierRuntimeMode.MODEL_PRIMARY:
        return None
    try:
        payload = json.loads(
            (Path(registry.root) / ONLINE_ACTIVATION_FILENAME).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        try:
            value = registry.active_model_id_unverified()
        except (AttributeError, OSError, TypeError, ValueError, ModelRegistryError):
            return None
        return str(value) if value else None
    if not isinstance(payload, dict) or not payload.get("model_id"):
        return None
    return str(payload["model_id"])


def _project_runtime_observability(
    value: object,
) -> tuple[dict[str, object], dict[str, int]]:
    if not isinstance(value, dict):
        return {"state": "unavailable"}, {}
    summary = value.get("timing")
    fallback_counts = value.get("fallback_counts")
    if not isinstance(summary, dict) or not isinstance(fallback_counts, dict):
        return {"state": "unavailable"}, {}
    slo_status = str(summary.get("slo_status") or "")
    projected_summary: dict[str, object] = {
        "slo_status": (
            slo_status
            if slo_status in {"compliant", "non_compliant", "not_enough_data"}
            else "unavailable"
        )
    }
    for segment_name in (
        "all",
        "warm_success",
        "warm_success_cache",
        "warm_success_remote",
    ):
        segment = summary.get(segment_name)
        if not isinstance(segment, dict):
            continue
        stages = segment.get("stages")
        projected_stages: dict[str, object] = {}
        if isinstance(stages, dict):
            for stage_name in ("queue", "http", "embedding", "head", "total"):
                percentiles = stages.get(stage_name)
                if isinstance(percentiles, dict):
                    projected_stages[stage_name] = {
                        key: percentiles.get(key) for key in ("p50", "p95", "p99")
                    }
        projected_summary[segment_name] = {
            "sample_count": segment.get("sample_count"),
            "stages": projected_stages,
        }
    projected_fallbacks: dict[str, int] = {}
    for key, value in fallback_counts.items():
        code = str(key)
        safe_code = code if code in _RUNTIME_FALLBACK_CODES else "runtime_failure"
        projected_fallbacks[safe_code] = projected_fallbacks.get(safe_code, 0) + int(
            value
        )
    return projected_summary, projected_fallbacks


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
    description_version: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    config_version: str = Field(default_factory=lambda: str(uuid4()), min_length=1)


class EmailCategoryUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    core_description: str = Field(min_length=1)
    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(min_length=1)
    threshold: float = Field(ge=0.0, le=1.0)
    enabled: bool = True
    description_version: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    config_version: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    expected_current_version: str = Field(min_length=1)

    @field_validator("expected_current_version")
    @classmethod
    def require_current_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expected_current_version must not be blank")
        return value


class EmailPromotionConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    macro_f1_min: float = Field(gt=0, le=1)
    category_precision_min: float = Field(gt=0, le=1)
    category_validation_samples_min: int = Field(gt=0)
    p95_latency_max_ms: float = Field(gt=0)
    expected_current_version: str = Field(min_length=1)


class EmailRuntimeModePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["agent_primary", "model_primary"]
    model_id: str | None = None
    request_id: str = Field(min_length=1, max_length=200)
    expected_mode: Literal["agent_primary", "model_primary"]
    expected_model_id: str | None = None


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

    if folder_binding_coordinator is None and availability.store is not None:
        from app.email_worker import build_provider_folder_binding_coordinator

        folder_binding_coordinator = build_provider_folder_binding_coordinator(
            secret_environment
        )

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
                "imap_move_mode",
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
        items = []
        for row in rows:
            provider_state = email_store.get_provider_classification_state(row["id"])
            items.append({**row, "id": str(row["id"]),
                          "important": provider_state.get("important"),
                          "provider_classification": provider_state})
        return {
            "items": items,
            "meta": meta(page=page, page_size=page_size, total=total),
        }

    @app.get("/api/console/email/classifications/{classification_id}")
    def email_classification_detail(classification_id: int):
        email_store = require_store()
        item = email_store.get_classification(classification_id)
        if item is None:
            return error_response("not_found", "Email classification not found", 404)
        provider_state_reader = getattr(
            email_store, "get_provider_classification_state", None
        )
        provider_state = (
            provider_state_reader(classification_id)
            if callable(provider_state_reader)
            else {"state": "unavailable", "reason": "provider_truth_not_supported"}
        )
        return {
            "ok": True,
            "item": {**item, "id": str(item["id"])},
            "observability": email_store.list_email_classification_observability(
                classification_id
            ),
            "provider_classification": provider_state,
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

    def training_controls(service, email_store, staged_evidence, registry_issues):
        from app.email_description_optimizer import description_set_digest
        from app.email_embedding_classifier import CategoryDescription

        try:
            transitions = online_control_history(service.registry)
        except (OSError, ValueError, TypeError):
            transitions = []
            registry_issues.append({"model_id": "online-control", "integrity_status": "corrupt",
                                    "integrity_error": "online_control_invalid"})
        config = email_store.current_model_promotion_config()
        descriptions = {
            row["category_key"]: CategoryDescription(
                core=row["core_description"], include=tuple(row["include"]),
                exclude=tuple(row["exclude"]), version=row["description_version"],
            ) for row in email_store.list_category_configs() if row["enabled"]
        }
        latest = staged_evidence[-1] if staged_evidence else None
        verified = False
        if latest:
            artifact = Path(service.registry.embedding_artifacts) / f"{latest['model_id']}.artifact"
            try:
                verified = artifact.is_file() and sha256(artifact.read_bytes()).hexdigest() == latest["hashes"]["artifact_sha256"]
            except OSError:
                registry_issues.append({"model_id": latest["model_id"], "integrity_status": "corrupt",
                                        "integrity_error": "artifact_unreadable"})
        readiness = assess_staged_candidate_readiness(staged_evidence)
        gate = assess_online_promotion_gate(
            evidence=latest, config=config, enabled_category_keys=tuple(descriptions),
            description_version=("description-set-sha256:" + description_set_digest(descriptions)
                                 if descriptions else "description-set-unavailable"),
            readiness=readiness, registry_issues=registry_issues, artifact_verified=verified,
        )
        mode = _active_runtime_mode(service.registry)
        active_id = _active_embedding_model_id(service.registry, mode)
        eligible = gate["promotion_eligible"]
        runtime = {"mode": mode.value, "active_model_id": active_id,
                   "candidate_model_id": latest["model_id"] if latest else None,
                   "candidate_ready": bool(eligible and mode is EmailClassifierRuntimeMode.AGENT_PRIMARY),
                   "toggle_enabled": bool(eligible or mode is EmailClassifierRuntimeMode.MODEL_PRIMARY)}
        return {"runtime": runtime, "promotion_gate": gate, "mode_transitions": list(reversed(transitions))}

    def registry_inventory(registry, issues):
        """Use the same complete registry assessment for display and activation."""
        try:
            manifest = registry.active_manifest()
            active_id = manifest.model_id if manifest is not None else None
        except (OSError, ValueError, ModelRegistryError):
            active_id = None
            issues.append({"model_id": "active-manifest", "integrity_status": "corrupt",
                           "integrity_error": "active_manifest_invalid"})
        models = []
        for entry in registry.list_model_inventory():
            if entry.integrity_status != "verified":
                issues.append({
                    "model_id": "model-inventory-evidence",
                    "integrity_status": entry.integrity_status,
                    "integrity_error": _controlled_integrity_error(entry.integrity_error),
                })
            projected = _project_legacy_model_inventory(entry)
            if projected is not None:
                models.append(projected)
        return active_id, models

    @app.put("/api/console/email/promotion-config")
    async def update_promotion_config(request: Request):
        email_store = require_store()
        try:
            payload = EmailPromotionConfigPayload.model_validate(await request.json())
        except (ValueError, TypeError):
            return error_response("invalid_promotion_config", "晋升门槛格式不正确", 400)
        try:
            config = email_store.create_model_promotion_config(**payload.model_dump())
        except ValueError:
            return error_response("promotion_config_conflict", "门槛版本已更新，请刷新后重试", 409)
        return {"ok": True, "config": config}

    @app.put("/api/console/email/runtime-mode")
    async def update_runtime_mode(request: Request):
        email_store = require_store()
        if email_learning_factory is None:
            return error_response("email_learning_unavailable", "模型训练服务不可用", 503)
        try:
            payload = EmailRuntimeModePayload.model_validate(await request.json())
        except (ValueError, TypeError):
            return error_response("invalid_runtime_mode", "模式切换请求不正确", 400)
        service = email_learning_factory()

        @contextmanager
        def configuration_guard():
            with email_store._connect() as db:
                db.execute("begin immediate")
                yield

        def validate_promotion():
            issues = []
            registry_inventory(service.registry, issues)
            rows = _safe_staged_evidence(service.registry, issues)
            controls = training_controls(service, email_store, rows, issues)
            gate = controls["promotion_gate"]
            if not gate["promotion_eligible"] or gate["candidate_model_id"] != payload.model_id:
                raise ValueError("candidate is not eligible")
            return gate["config"]["config_version"]

        try:
            switch_online_model(service.registry, **payload.model_dump(), actor="console-user",
                                validate_promotion=validate_promotion,
                                configuration_guard=configuration_guard)
            issues = []
            registry_inventory(service.registry, issues)
            rows = _safe_staged_evidence(service.registry, issues)
            controls = training_controls(service, email_store, rows, issues)
        except (OSError, ValueError, ModelRegistryError):
            return error_response("runtime_mode_conflict", "模型未达标或状态已变化，请刷新后重试", 409)
        return {"ok": True, **controls}

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
        active_model_id, models = registry_inventory(service.registry, registry_issues)
        staged_evidence = _safe_staged_evidence(service.registry, registry_issues)
        latest_evidence = staged_evidence[-1] if staged_evidence else None
        latest_projection = (
            _project_staged_model_evidence(latest_evidence)
            if latest_evidence is not None
            else None
        )
        active_mode = _active_runtime_mode(service.registry)
        modern_active_model_id = _active_embedding_model_id(
            service.registry, active_mode
        )
        if modern_active_model_id is not None:
            active_model_id = modern_active_model_id
        persisted_runtime = (
            email_store.classifier_runtime_observability(
                model_id=modern_active_model_id
            )
            if modern_active_model_id is not None
            and hasattr(email_store, "classifier_runtime_observability")
            else None
        )
        runtime_timing, fallback_counts = _project_runtime_observability(
            persisted_runtime
        )
        pending_examples = len(email_store.list_unincluded_training_examples())
        return {
            "ok": True,
            "learning": {
                **training_controls(service, email_store, staged_evidence, registry_issues),
                "active_model_id": active_model_id,
                "active_mode": active_mode.value,
                "pending_examples": pending_examples,
                "last_trained_feedback_count": state.last_trained_feedback_count,
                "last_trained_at": state.last_trained_at,
                "last_feedback_at": state.last_feedback_at,
                "active_run_id": state.active_run_id,
                "models": models,
                "staged_models": [
                    _project_staged_model_evidence(row) for row in staged_evidence
                ],
                "training_snapshot": (
                    email_store.latest_training_snapshot_state()
                    if hasattr(email_store, "latest_training_snapshot_state")
                    else None
                ),
                "historical_eligibility": (
                    latest_projection.get("historical_eligibility")
                    if latest_projection is not None
                    else None
                ),
                "promotion_evidence": (
                    latest_projection.get("promotion_evidence")
                    if latest_projection is not None
                    else {
                        "ready": False,
                        "passing_model_ids": [],
                        "reason": "no_staged_candidate",
                    }
                ),
                "runtime_timing": runtime_timing,
                "fallback_counts": fallback_counts,
                "registry_issues": registry_issues,
                "category_thresholds": {
                    row["category"]: row["threshold"]
                    for row in email_store.list_configs()
                },
            },
            "meta": {"snapshot_at": datetime.now(timezone.utc).isoformat()},
        }

    @app.get("/api/console/email/folder-bindings")
    def email_folder_bindings():
        email_store = require_store()
        bindings = email_store.list_account_folder_bindings()
        by_category: dict[str, list[dict[str, object]]] = {}
        for binding in bindings:
            by_category.setdefault(str(binding["category_key"]), []).append(
                {
                    key: binding[key]
                    for key in (
                        "account_id",
                        "provider_folder_id",
                        "provider_folder_name",
                        "binding_status",
                        "last_verified_at",
                    )
                }
            )
        items = []
        for config in email_store.list_category_configs():
            category_key = str(config["category_key"])
            items.append(
                {
                    "category_key": category_key,
                    "display_name": config["display_name"],
                    "enabled": config["enabled"],
                    "description_version": config["description_version"],
                    "accounts": by_category.get(category_key, []),
                }
            )
        return {"ok": True, "items": items, "meta": meta()}

    @app.get("/api/console/email/model-versions/{model_id}")
    def email_model_version(model_id: str):
        if email_learning_factory is None:
            return error_response(
                "email_learning_unavailable",
                "Email learning is unavailable",
                503,
            )
        service = email_learning_factory()
        if (
            not model_id.startswith((MODEL_ID_PREFIX, EMBEDDING_MODEL_ID_PREFIX))
            or Path(model_id).name != model_id
            or "/" in model_id
            or "\\" in model_id
        ):
            return error_response(
                "invalid_email_model_id", "Email model ID is invalid", 400
            )
        staged_root = Path(
            getattr(
                service.registry,
                "staged_evidence",
                Path(service.registry.root) / "staged-evidence",
            )
        )
        if not (staged_root / f"{model_id}.json").is_file():
            return error_response("not_found", "Email model version not found", 404)
        try:
            evidence = service.registry.get_staged_evidence(model_id)
            if evidence.get("model_id") != model_id:
                raise ValueError("model identity mismatch")
            projected = _project_staged_model_evidence(evidence)
        except (KeyError, OSError, OverflowError, TypeError, ValueError, ModelRegistryError):
            return error_response(
                "email_model_integrity_error",
                "Email model evidence failed integrity validation",
                409,
            )
        return {
            "ok": True,
            "model": projected,
            "meta": meta(),
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

    @app.get("/api/console/email/config/{category_key}/history")
    def email_config_history(category_key: str):
        email_store = require_store()
        try:
            category_key = validate_email_category_key(category_key)
        except ValueError:
            return error_response("invalid_email_category", "Email category is invalid", 400)
        if email_store.get_category_config(category_key) is None:
            return error_response("email_category_not_found", "Email category was not found", 404)
        fields = (
            "category_key", "display_name", "core_description", "include", "exclude",
            "threshold", "enabled", "description_version", "config_version", "updated_at",
        )
        return {"ok": True, "items": [
            {**{key: revision[key] for key in ("revision_id", "category_key", "config_version", "created_at")},
             "description_version": revision["config"]["description_version"],
             "config": {key: revision["config"][key] for key in fields}}
            for revision in email_store.list_category_description_revisions(category_key)
        ]}

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
        if payload.enabled:
            enabled_account_ids = {
                str(account["account_id"]) for account in enabled_accounts
            }
            active_account_ids = {
                binding.account_id
                for binding in bindings
                if binding.binding_status == "active"
            }
            if active_account_ids != enabled_account_ids:
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
            existing = email_store.get_category_config(category_key)
            if existing is None:
                return error_response(
                    "email_category_not_found",
                    "Email category was not found",
                    404,
                )
            if payload.expected_current_version != existing["config_version"]:
                return error_response("email_category_version_conflict", "类别配置已更新，请刷新后重试", 409)
            enabled_accounts = [
                account for account in email_store.list_accounts() if account["enabled"]
            ]
            existing_bindings = email_store.list_account_folder_bindings(category_key)
            provider_folder_name = next(
                (
                    binding["provider_folder_name"]
                    for binding in existing_bindings
                    if binding["provider_folder_name"]
                ),
                existing["display_name"],
            )
            assert folder_binding_coordinator is not None
            coordinator_bindings = folder_binding_coordinator.create_and_verify_bindings(
                category_key=category_key,
                provider_folder_name=provider_folder_name,
                enabled_accounts=enabled_accounts,
            )
            if isinstance(coordinator_bindings, (str, bytes)) or not isinstance(
                coordinator_bindings, Sequence
            ):
                raise TypeError("coordinator returned a non-sequence result")
            if any(
                type(binding) is not VerifiedEmailFolderBinding
                for binding in coordinator_bindings
            ):
                raise TypeError("coordinator returned an unverified binding")
            row = email_store.refresh_category_with_bindings(
                category_key,
                core_description=payload.core_description,
                include=payload.include,
                exclude=payload.exclude,
                threshold=payload.threshold,
                enabled=payload.enabled,
                description_version=payload.description_version,
                config_version=payload.config_version,
                bindings=tuple(coordinator_bindings),
                expected_current_version=payload.expected_current_version,
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
        return {
            "ok": True,
            "item": category_response(email_store, row),
            "message": "邮件配置已保存",
        }
