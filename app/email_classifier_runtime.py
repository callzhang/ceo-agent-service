"""Runtime loading boundary for the local email classifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pickle import UnpicklingError

from app.email_classifier_model import CpuTfidfLogisticClassifier
from app.email_classifier_scan import EmailScanConfig, EmailScanResult, scan_readonly_batch
from app.email_store import EmailStore
from app.email_model_registry import EmailModelRegistry, ModelRegistryError
from app.jieba_loader import jieba_lcut


class EmailClassifierUnavailable(RuntimeError):
    """No valid active or previous model is available for classification."""


@dataclass(frozen=True)
class LoadedEmailClassifier:
    classifier: object
    path: Path
    used_previous: bool


@dataclass(frozen=True)
class ReadonlyScanWithModelResult:
    loaded: LoadedEmailClassifier
    scan: EmailScanResult


class RegistryPredictionClassifier:
    """Count consecutive runtime failures and atomically fall back at threshold."""

    def __init__(
        self,
        registry: EmailModelRegistry,
        classifier: CpuTfidfLogisticClassifier,
        model_id: str,
        *,
        failure_threshold: int = 3,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        self.registry = registry
        self.classifier = classifier
        self.model_id = model_id
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0

    def predict(self, text: str):
        return self._predict("predict", text)

    def predict_message(self, message: object):
        return self._predict("predict_message", message)

    def _predict(self, method: str, value: object):
        try:
            result = getattr(self.classifier, method)(value)
            self.consecutive_failures = 0
            return result
        except Exception:
            self.consecutive_failures += 1
            if self.consecutive_failures < self.failure_threshold:
                raise
            restored = self.registry.fallback_to_previous(
                reason="active_prediction_failed_repeatedly",
                failed_model_id=self.model_id,
            )
            self.classifier = self.registry.load_classifier(restored.model_id)
            self.model_id = restored.model_id
            self.consecutive_failures = 0
            return getattr(self.classifier, method)(value)


class EmailClassifierRuntime:
    """Own one loaded classifier and learning tick across mailbox scan cycles."""

    def __init__(self, registry: EmailModelRegistry, *, learning_service=None) -> None:
        self.registry = registry
        self.learning_service = learning_service
        self.loaded = _load_registry_classifier(registry)

    def tick(self, *, now: datetime | None = None):
        if self.learning_service is None:
            return None
        return self.learning_service.poll_retrain(now=now)

    def scan(
        self,
        source: object,
        store: EmailStore,
        config: EmailScanConfig,
        *,
        mailbox: str = "INBOX",
        limit: int = 50,
        now: datetime | None = None,
    ) -> ReadonlyScanWithModelResult:
        self.tick(now=now)
        result = scan_readonly_batch(
            source,
            self.loaded.classifier,
            store,
            config,
            mailbox=mailbox,
            limit=limit,
        )
        return ReadonlyScanWithModelResult(loaded=self.loaded, scan=result)


def _load_registry_classifier(registry) -> LoadedEmailClassifier:
    jieba_lcut("email classifier warmup")
    failed_model_id = registry.active_model_id_unverified()
    if failed_model_id is None:
        raise EmailClassifierUnavailable("no active email classifier manifest")
    try:
        manifest = registry.active_manifest()
        assert manifest is not None
        classifier = registry.load_classifier(manifest.model_id)
        return LoadedEmailClassifier(
            classifier=RegistryPredictionClassifier(
                registry, classifier, manifest.model_id
            ),
            path=registry.get_model(manifest.model_id).artifact_path,
            used_previous=False,
        )
    except ModelRegistryError:
        try:
            restored = registry.fallback_to_previous(
                reason="active_model_load_failed",
                failed_model_id=failed_model_id,
            )
            classifier = registry.load_classifier(restored.model_id)
        except ModelRegistryError as fallback_exc:
            raise EmailClassifierUnavailable(
                "no valid email classifier model after active load failure"
            ) from fallback_exc
        return LoadedEmailClassifier(
            classifier=RegistryPredictionClassifier(
                registry, classifier, restored.model_id
            ),
            path=registry.get_model(restored.model_id).artifact_path,
            used_previous=True,
        )


def load_active_classifier(
    active_path: str | Path | EmailModelRegistry,
    previous_path: str | Path | None = None,
) -> LoadedEmailClassifier:
    """Load active first, then previous, without modifying either file."""
    if isinstance(active_path, EmailModelRegistry):
        return _load_registry_classifier(active_path)
    if previous_path is None:
        raise ValueError("previous_path is required for path-based model loading")
    candidates = ((Path(active_path), False), (Path(previous_path), True))
    errors: list[str] = []
    for path, used_previous in candidates:
        try:
            classifier = CpuTfidfLogisticClassifier.load(path)
        except (OSError, KeyError, TypeError, ValueError, UnpicklingError) as exc:
            errors.append(f"{path}: {type(exc).__name__}")
            continue
        return LoadedEmailClassifier(
            classifier=classifier,
            path=path,
            used_previous=used_previous,
        )
    detail = "; ".join(errors) if errors else "no model paths configured"
    raise EmailClassifierUnavailable(f"no valid email classifier model: {detail}")


def scan_with_active_model(
    source: object,
    store: EmailStore,
    config: EmailScanConfig,
    *,
    runtime: EmailClassifierRuntime,
    mailbox: str = "INBOX",
    limit: int = 50,
    now: datetime | None = None,
) -> ReadonlyScanWithModelResult:
    """Load a local model and run one provider-readonly classification batch."""
    return runtime.scan(
        source,
        store,
        config,
        mailbox=mailbox,
        limit=limit,
        now=now,
    )
