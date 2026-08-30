"""Application service for confirming feedback and triggering local retraining."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_retrain import (
    AutoRetrainResult,
    RetrainPolicy,
    RetrainState,
    load_retrain_state,
    retrain_if_due,
    save_retrain_state,
)
from app.email_store import EmailStore


@dataclass(frozen=True)
class FeedbackLearningResult:
    confirmed: dict[str, object]
    retrain: AutoRetrainResult | None
    error: str | None


class EmailClassifierLearningService:
    """Persist user feedback first, then best-effort local model learning.

    A training failure is reported separately from the confirmed feedback. This
    keeps the user decision durable while leaving the old active model intact.
    This service never connects to an email provider.
    """

    def __init__(
        self,
        store: EmailStore,
        *,
        active_path: str | Path,
        previous_path: str | Path,
        retrain_state_path: str | Path,
        policy: RetrainPolicy = RetrainPolicy(),
    ) -> None:
        self.store = store
        self.active_path = Path(active_path)
        self.previous_path = Path(previous_path)
        self.retrain_state_path = Path(retrain_state_path)
        self.policy = policy

    def confirm_and_maybe_retrain(
        self,
        row_id: int,
        category: EmailCategory,
        *,
        now: datetime | None = None,
        model_version: str | None = None,
    ) -> FeedbackLearningResult | None:
        confirmed = self.store.confirm_classification(row_id, category)
        if confirmed is None:
            return None

        current = now or datetime.now(timezone.utc)
        try:
            state = load_retrain_state(self.retrain_state_path).record_feedback(current)
            retrain = retrain_if_due(
                self.store,
                state,
                self.active_path,
                self.previous_path,
                now=current,
                model_version=model_version or _model_version(current),
                policy=self.policy,
            )
            save_retrain_state(self.retrain_state_path, retrain.state)
            return FeedbackLearningResult(confirmed, retrain, None)
        except Exception as exc:  # feedback must survive a local training failure
            error = f"{type(exc).__name__}: {exc}"
            try:
                save_retrain_state(
                    self.retrain_state_path,
                    state if "state" in locals() else RetrainState(),
                )
            except Exception as state_exc:
                error += f"; state persistence failed: {type(state_exc).__name__}: {state_exc}"
            return FeedbackLearningResult(confirmed, None, error)

    def poll_retrain(
        self,
        *,
        now: datetime | None = None,
        model_version: str | None = None,
    ) -> AutoRetrainResult:
        """Poll the durable debounce state without blocking feedback persistence."""
        current = now or datetime.now(timezone.utc)
        state = load_retrain_state(self.retrain_state_path)
        result = retrain_if_due(
            self.store,
            state,
            self.active_path,
            self.previous_path,
            now=current,
            model_version=model_version or _model_version(current),
            policy=self.policy,
        )
        save_retrain_state(self.retrain_state_path, result.state)
        return result


def _model_version(now: datetime) -> str:
    return f"email-model-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
