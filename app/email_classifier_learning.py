"""Feedback-first orchestration for durable subprocess model training."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.email_classifier_contracts import EmailCategory
from app.email_classifier_retrain import (
    AutoRetrainResult,
    RetrainDecision,
    RetrainPolicy,
    RetrainState,
    TrainingSubprocessController,
    load_retrain_state,
    retrain_state_reservation,
    save_retrain_state,
)
from app.email_model_registry import EmailModelRegistry
from app.email_pipeline import apply_human_confirmation
from app.email_store import EmailStore


@dataclass(frozen=True)
class FeedbackLearningResult:
    confirmed: dict[str, object]
    retrain: AutoRetrainResult | None
    error: str | None


class EmailClassifierLearningService:
    """Persist feedback, then launch and poll the immutable registry lifecycle."""

    def __init__(
        self,
        store: EmailStore,
        *,
        registry: EmailModelRegistry,
        retrain_state_path: str | Path,
        policy: RetrainPolicy = RetrainPolicy(),
        controller: TrainingSubprocessController | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.retrain_state_path = Path(retrain_state_path)
        self.policy = policy
        self.controller = controller or TrainingSubprocessController(
            registry, store_path=store.path
        )

    def confirm_and_maybe_retrain(
        self,
        row_id: int,
        category: EmailCategory,
        *,
        now: datetime | None = None,
    ) -> FeedbackLearningResult | None:
        current = now or datetime.now(timezone.utc)
        confirmed = apply_human_confirmation(
            self.store,
            row_id,
            category,
            now=current,
        )
        if confirmed is None:
            return None
        try:
            with retrain_state_reservation(self.retrain_state_path):
                state = load_retrain_state(self.retrain_state_path).record_feedback(
                    current
                )
                save_retrain_state(self.retrain_state_path, state)
                retrain = self._request_if_ready(
                    state, now=current, manual=False
                )
            return FeedbackLearningResult(confirmed, retrain, None)
        except Exception as exc:
            return FeedbackLearningResult(
                confirmed, None, f"{type(exc).__name__}: {exc}"
            )

    def request_manual_training(
        self, *, now: datetime | None = None
    ) -> AutoRetrainResult:
        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            return self._request_if_ready(state, now=current, manual=True)

    def poll_retrain(self, *, now: datetime | None = None) -> AutoRetrainResult:
        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            return self._poll_retrain(state, now=current)

    def _poll_retrain(
        self, state: RetrainState, *, now: datetime
    ) -> AutoRetrainResult:
        if state.active_run_id is None:
            return self._request_if_ready(state, now=now, manual=False)
        run = self.controller.poll(state.active_run_id, now=now)
        decision = RetrainDecision(True, "training_run", len(self.store.list_unincluded_training_examples()))
        if run.status == "running" or run.status == "queued":
            return AutoRetrainResult(decision, state, None, run)
        if run.status == "succeeded":
            updated = state.mark_trained(
                len(self.store.list_training_examples()), now
            )
        else:
            updated = state.with_active_run(None)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(decision, updated, None, run)

    def _request_if_ready(
        self, state: RetrainState, *, now: datetime, manual: bool
    ) -> AutoRetrainResult:
        if state.active_run_id is not None:
            return self._poll_retrain(state, now=now)
        pending = len(self.store.list_unincluded_training_examples())
        enough = pending >= self.policy.minimum_new_examples
        idle = (
            state.last_feedback_at is not None
            and (
                now.astimezone(timezone.utc)
                - datetime.fromisoformat(state.last_feedback_at).astimezone(timezone.utc)
            ).total_seconds()
            >= self.policy.idle_seconds
        )
        overdue = (
            state.last_trained_at is not None
            and (
                now.astimezone(timezone.utc)
                - datetime.fromisoformat(state.last_trained_at).astimezone(timezone.utc)
            ).total_seconds()
            >= self.policy.max_interval_seconds
        )
        due = enough and (manual or idle or overdue)
        reason = "manual" if manual and enough else "idle_debounce" if idle and enough else "max_interval" if overdue and enough else None
        decision = RetrainDecision(due, reason, pending)
        if not due:
            return AutoRetrainResult(decision, state, None, None)
        run = self.controller.start(now=now)
        updated = state.with_active_run(run.run_id)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(decision, updated, None, run)
