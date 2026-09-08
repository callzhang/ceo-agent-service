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
    SnapshotTrainingSignal,
    TrainingSubprocessController,
    evaluate_snapshot_retrain,
    load_retrain_state,
    retrain_state_reservation,
    save_retrain_state,
)
from app.email_model_registry import EmailModelRegistry, ModelRegistryError
from app.email_pipeline import apply_human_confirmation
from app.email_store import EmailStore


@dataclass(frozen=True)
class FeedbackLearningResult:
    confirmed: dict[str, object]
    retrain: AutoRetrainResult | None
    error: str | None
    feedback_request_id: str
    expected_current_action_plan_id: str | None
    resulting_action_plan_id: str
    feedback_applied: bool
    feedback_replayed: bool


@dataclass(frozen=True)
class SnapshotPublicationResult:
    snapshot: dict[str, object]
    retrain: AutoRetrainResult
    pending_trigger: bool = False


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
        description_optimizer: object | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.retrain_state_path = Path(retrain_state_path)
        self.policy = policy
        self.controller = controller or TrainingSubprocessController(
            registry, store_path=store.path
        )
        self.description_optimizer = description_optimizer

    def confirm_and_maybe_retrain(
        self,
        row_id: int,
        category: EmailCategory,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        now: datetime | None = None,
    ) -> FeedbackLearningResult | None:
        current = now or datetime.now(timezone.utc)
        application = apply_human_confirmation(
            self.store,
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            now=current,
        )
        if application is None:
            return None
        result_fields = {
            "confirmed": application.confirmed,
            "feedback_request_id": application.feedback_request_id,
            "expected_current_action_plan_id": (
                application.expected_current_action_plan_id
            ),
            "resulting_action_plan_id": application.resulting_action_plan_id,
            "feedback_applied": application.applied,
            "feedback_replayed": application.replayed,
        }
        if application.replayed:
            return FeedbackLearningResult(
                **result_fields,
                retrain=None,
                error=None,
            )
        try:
            with retrain_state_reservation(self.retrain_state_path):
                state = load_retrain_state(self.retrain_state_path).record_feedback(
                    current
                )
                save_retrain_state(self.retrain_state_path, state)
                retrain = self._request_if_ready(state, now=current, manual=False)
            return FeedbackLearningResult(
                **result_fields,
                retrain=retrain,
                error=None,
            )
        except Exception as exc:
            return FeedbackLearningResult(
                **result_fields,
                retrain=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def request_manual_training(
        self, *, now: datetime | None = None
    ) -> AutoRetrainResult:
        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            return self._request_if_ready(state, now=current, manual=True)

    def request_description_proposal_evaluation(
        self, proposal_id: str, *, now: datetime | None = None
    ) -> AutoRetrainResult:
        """Stage an immutable full-description overlay against its frozen split."""

        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            if state.active_run_id is not None:
                return self._poll_retrain(state, now=current)
            return self._start_description_proposal_evaluation(
                proposal_id, state=state, now=current
            )

    def publish_training_snapshot(
        self, snapshot: object, *, now: datetime | None = None
    ) -> SnapshotPublicationResult:
        """Freeze one snapshot, then immediately evaluate its training trigger."""

        stored = self.store.persist_training_snapshot(snapshot)
        retrain = self.observe_snapshot_and_maybe_retrain(now=now)
        return SnapshotPublicationResult(
            snapshot=stored,
            retrain=retrain,
            pending_trigger=retrain.decision.reason == "pending_active_training",
        )

    def observe_snapshot_and_maybe_retrain(
        self, *, now: datetime | None = None
    ) -> AutoRetrainResult:
        """Evaluate a newly frozen snapshot; this is the automatic trigger edge."""

        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            if state.active_run_id is not None:
                snapshot = self.store.latest_training_snapshot_state()
                if snapshot is None:
                    return AutoRetrainResult(
                        RetrainDecision(False, "training_not_ready", 0),
                        state,
                        None,
                        None,
                    )
                signal = _snapshot_signal(snapshot)
                updated = state.with_pending_snapshot(
                    signal, snapshot_id=str(snapshot["snapshot_id"])
                )
                save_retrain_state(self.retrain_state_path, updated)
                return AutoRetrainResult(
                    RetrainDecision(False, "pending_active_training", 0),
                    updated,
                    None,
                    None,
                )
            return self._request_if_ready(state, now=current, manual=False)

    def poll_retrain(self, *, now: datetime | None = None) -> AutoRetrainResult:
        current = now or datetime.now(timezone.utc)
        with retrain_state_reservation(self.retrain_state_path):
            state = load_retrain_state(self.retrain_state_path)
            return self._poll_retrain(state, now=current)

    def _poll_retrain(self, state: RetrainState, *, now: datetime) -> AutoRetrainResult:
        if state.active_run_id is None:
            recovered = self._recover_reserved_or_started_run(state, now=now)
            if recovered is not None:
                return recovered
            return self._resume_description_proposal_queue(state, now=now)
        run = self.controller.poll(state.active_run_id, now=now)
        decision = RetrainDecision(True, "training_run", 0)
        if run.status in {"queued", "launching", "running"}:
            return AutoRetrainResult(decision, state, None, run)
        if run.status == "succeeded":
            if run.description_proposal_id is not None:
                return self._complete_description_proposal_evaluation(
                    run, state=state, now=now
                )
            else:
                proposals = ()
                if self.description_optimizer is not None and run.model_id is not None:
                    proposals = self.description_optimizer.observe_candidate(
                        run.model_id
                    )
                    if not proposals:
                        from app.email_description_optimizer import (
                            DescriptionProposalRepository,
                        )

                        repository = DescriptionProposalRepository(self.registry.root)
                        proposals = (
                            *repository.list_by_status("proposed"),
                            *repository.list_by_status("evaluated"),
                        )
                signal = SnapshotTrainingSignal(
                    snapshot_sha=run.snapshot_sha,
                    description_version=run.description_version,
                    folder_label_watermark=run.folder_label_watermark,
                    important_label_watermark=run.important_label_watermark,
                    minimum_ready=True,
                )
                updated = state.mark_snapshot_trained(
                    signal, run_id=run.run_id, now=now
                )
                had_pending = updated.pending_snapshot_id is not None
                pending_result = self._consume_pending_snapshot(updated, now=now)
                if pending_result is not None:
                    return pending_result
                if had_pending:
                    updated = load_retrain_state(self.retrain_state_path)
                if proposals:
                    return self._start_description_proposal_evaluation(
                        proposals[0].proposal_id, state=updated, now=now
                    )
        else:
            updated = state.with_active_run(None)
            had_pending = updated.pending_snapshot_id is not None
            pending_result = self._consume_pending_snapshot(updated, now=now)
            if pending_result is not None:
                return pending_result
            if had_pending:
                updated = load_retrain_state(self.retrain_state_path)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(decision, updated, None, run)

    def _resume_description_proposal_queue(
        self, state: RetrainState, *, now: datetime
    ) -> AutoRetrainResult:
        from app.email_description_optimizer import DescriptionProposalRepository

        repository = DescriptionProposalRepository(self.registry.root)
        evaluated = repository.list_by_status("evaluated")
        if evaluated:
            return self._recover_evaluated_description_proposal(
                evaluated[0], repository=repository, state=state, now=now
            )
        proposed = repository.list_by_status("proposed")
        if not proposed:
            return AutoRetrainResult(RetrainDecision(False, None, 0), state, None, None)
        return self._start_description_proposal_evaluation(
            proposed[0].proposal_id, state=state, now=now
        )

    def _recover_evaluated_description_proposal(
        self,
        proposal: object,
        *,
        repository: object,
        state: RetrainState,
        now: datetime,
    ) -> AutoRetrainResult:
        model_id = getattr(proposal, "evaluated_model_id", None)
        passed = getattr(proposal, "evaluation_passed", None)
        evaluated_digest = getattr(proposal, "evaluated_description_set_digest", None)
        invalid = AutoRetrainResult(
            RetrainDecision(False, "description_proposal_evidence_invalid", 0),
            state,
            None,
            None,
        )
        if not isinstance(model_id, str) or type(passed) is not bool:
            return invalid
        try:
            current_evidence = self.registry.get_staged_evidence(model_id)
            if not isinstance(current_evidence, dict):
                return invalid
            readiness = current_evidence.get("whole_model_readiness")
            binding = current_evidence.get("description_proposal")
            if (
                not isinstance(readiness, dict)
                or readiness.get("ready") is not passed
                or not isinstance(binding, dict)
                or binding.get("description_set_digest") != evaluated_digest
            ):
                return invalid
            verified = repository.record_evaluation(
                proposal.proposal_id, model_id=model_id
            )
            if (
                verified.evaluated_model_id != model_id
                or verified.evaluation_passed is not passed
                or verified.evaluated_description_set_digest != evaluated_digest
            ):
                return invalid
        except (OSError, TypeError, ValueError, ModelRegistryError):
            return invalid
        matching_model_ids = {
            str(evidence.get("model_id"))
            for evidence in self.registry.list_staged_evidence()
            if _compatible_proposal_candidate_evidence(
                evidence, current=current_evidence
            )
        }
        if model_id not in matching_model_ids:
            return invalid
        if len(matching_model_ids) >= 2:
            repository.decide(proposal.proposal_id, accept=passed)
            return AutoRetrainResult(
                RetrainDecision(False, "description_proposal_recovered_decision", 0),
                state,
                None,
                None,
            )
        if len(matching_model_ids) == 1 and passed is False:
            return self._start_description_proposal_evaluation(
                proposal.proposal_id, state=state, now=now
            )
        return invalid

    def _consume_pending_snapshot(
        self, state: RetrainState, *, now: datetime
    ) -> AutoRetrainResult | None:
        signal = state.pending_signal()
        snapshot_id = state.pending_snapshot_id
        if signal is None or snapshot_id is None:
            return None
        updated = state.without_pending_snapshot()
        decision = evaluate_snapshot_retrain(updated, signal=signal, policy=self.policy)
        if not decision.due:
            save_retrain_state(self.retrain_state_path, updated)
            self._mark_pending_observation_handled(signal.snapshot_sha)
            return None
        run = self.controller.start(now=now, signal=signal, snapshot_id=snapshot_id)
        updated = updated.with_active_run(run.run_id)
        save_retrain_state(self.retrain_state_path, updated)
        self._mark_pending_observation_handled(signal.snapshot_sha)
        return AutoRetrainResult(decision, updated, None, run)

    def _mark_pending_observation_handled(self, snapshot_sha: str) -> None:
        from app.email_training_snapshot import mark_observation_trigger_handled

        mark_observation_trigger_handled(self.registry.root, snapshot_sha)

    def _start_description_proposal_evaluation(
        self, proposal_id: str, *, state: RetrainState, now: datetime
    ) -> AutoRetrainResult:
        from app.email_description_optimizer import (
            DescriptionProposalRepository,
            build_description_set_overlay,
        )
        from app.email_embedding_classifier import CategoryDescription

        recovered = self._recover_reserved_or_started_run(state, now=now)
        if recovered is not None:
            return recovered

        repository = DescriptionProposalRepository(self.registry.root)
        proposal = repository.get(proposal_id)
        active_descriptions = {
            row["category_key"]: CategoryDescription(
                core=row["core_description"],
                include=tuple(row["include"]),
                exclude=tuple(row["exclude"]),
                version=row["description_version"],
            )
            for row in self.store.list_category_configs()
            if row["enabled"]
        }
        overlay = build_description_set_overlay(proposal, active_descriptions)
        snapshot = self.store.get_training_snapshot(proposal.source_snapshot_id)
        if (
            snapshot is None
            or snapshot["snapshot_digest"] != proposal.source_snapshot_sha
        ):
            raise ValueError(
                "proposal frozen source snapshot is unavailable or corrupt"
            )
        latest = self.store.latest_training_snapshot_state()
        if latest is None:
            raise ValueError("training snapshot state is unavailable")
        repository.persist_overlay(overlay)
        signal = SnapshotTrainingSignal(
            snapshot_sha=proposal.source_snapshot_sha,
            description_version=overlay.description_set_version,
            folder_label_watermark=int(latest["folder_label_watermark"]),
            important_label_watermark=int(latest["important_label_watermark"]),
            minimum_ready=True,
            manual=True,
        )
        run = self.controller.start(
            now=now,
            signal=signal,
            snapshot_id=proposal.source_snapshot_id,
            description_overlay=overlay,
        )
        updated = state.with_active_run(run.run_id)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(
            RetrainDecision(True, "description_proposal_evaluation", 0),
            updated,
            None,
            run,
        )

    def _complete_description_proposal_evaluation(
        self,
        run: object,
        *,
        state: RetrainState,
        now: datetime,
    ) -> AutoRetrainResult:
        from app.email_description_optimizer import DescriptionProposalRepository

        if run.model_id is None or run.description_proposal_id is None:
            raise ValueError("successful proposal training run is incomplete")
        repository = DescriptionProposalRepository(self.registry.root)
        evaluated = repository.record_evaluation(
            run.description_proposal_id, model_id=run.model_id
        )
        updated = state.with_active_run(None)
        if evaluated.evaluation_passed:
            repository.decide(run.description_proposal_id, accept=True)
        else:
            current_evidence = self.registry.get_staged_evidence(run.model_id)
            matching_model_ids = {
                str(evidence.get("model_id"))
                for evidence in self.registry.list_staged_evidence()
                if _compatible_proposal_candidate_evidence(
                    evidence, current=current_evidence
                )
            }
            if len(matching_model_ids) < 2:
                had_pending = updated.pending_snapshot_id is not None
                pending_result = self._consume_pending_snapshot(updated, now=now)
                if pending_result is not None:
                    return pending_result
                if had_pending:
                    updated = load_retrain_state(self.retrain_state_path)
                return self._start_description_proposal_evaluation(
                    run.description_proposal_id, state=updated, now=now
                )
            repository.decide(run.description_proposal_id, accept=False)
        had_pending = updated.pending_snapshot_id is not None
        pending_result = self._consume_pending_snapshot(updated, now=now)
        if pending_result is not None:
            return pending_result
        if had_pending:
            updated = load_retrain_state(self.retrain_state_path)
        pending = repository.list_by_status("proposed")
        if pending:
            return self._start_description_proposal_evaluation(
                pending[0].proposal_id, state=updated, now=now
            )
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(
            RetrainDecision(True, "description_proposal_evaluation", 0),
            updated,
            None,
            run,
        )

    def _request_if_ready(
        self, state: RetrainState, *, now: datetime, manual: bool
    ) -> AutoRetrainResult:
        if state.active_run_id is not None:
            return self._poll_retrain(state, now=now)
        recovered = self._recover_reserved_or_started_run(state, now=now)
        if recovered is not None:
            return recovered
        snapshot = self.store.latest_training_snapshot_state()
        if snapshot is None:
            return AutoRetrainResult(
                RetrainDecision(False, "training_not_ready", 0), state, None, None
            )
        signal = _snapshot_signal(snapshot, manual=manual)
        decision = evaluate_snapshot_retrain(state, signal=signal, policy=self.policy)
        if not decision.due:
            return AutoRetrainResult(decision, state, None, None)
        run = self.controller.start(
            now=now, signal=signal, snapshot_id=str(snapshot["snapshot_id"])
        )
        updated = state.with_active_run(run.run_id)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(decision, updated, None, run)

    def _recover_reserved_or_started_run(
        self, state: RetrainState, *, now: datetime
    ) -> AutoRetrainResult | None:
        recover = getattr(self.controller, "recover_reserved_or_started_run", None)
        if not callable(recover):
            return None
        run = recover(now=now)
        if run is None:
            return None
        updated = state.with_active_run(run.run_id)
        pending = state.pending_signal()
        if (
            pending is not None
            and state.pending_snapshot_id == run.snapshot_id
            and pending.snapshot_sha == run.snapshot_sha
            and pending.description_version == run.description_version
        ):
            updated = updated.without_pending_snapshot()
            self._mark_pending_observation_handled(run.snapshot_sha)
        save_retrain_state(self.retrain_state_path, updated)
        return AutoRetrainResult(
            RetrainDecision(True, "training_run_recovered", 0),
            updated,
            None,
            run,
        )


def _snapshot_signal(
    snapshot: dict[str, object], *, manual: bool = False
) -> SnapshotTrainingSignal:
    return SnapshotTrainingSignal(
        snapshot_sha=str(snapshot["snapshot_sha"]),
        description_version=str(snapshot["description_version"]),
        folder_label_watermark=int(snapshot["folder_label_watermark"]),
        important_label_watermark=int(snapshot["important_label_watermark"]),
        minimum_ready=bool(snapshot["minimum_ready"]),
        manual=manual,
    )


def _compatible_proposal_candidate_evidence(
    evidence: object, *, current: object
) -> bool:
    if not isinstance(evidence, dict) or not isinstance(current, dict):
        return False
    binding = evidence.get("description_proposal")
    current_binding = current.get("description_proposal")
    hashes = evidence.get("hashes")
    current_hashes = current.get("hashes")
    return bool(
        isinstance(binding, dict)
        and binding == current_binding
        and evidence.get("compatibility") == current.get("compatibility")
        and isinstance(hashes, dict)
        and isinstance(current_hashes, dict)
        and hashes.get("snapshot_sha256") == current_hashes.get("snapshot_sha256")
        and hashes.get("description_sha256") == current_hashes.get("description_sha256")
        and type(evidence.get("unresolved_historical_systematic_error")) is bool
        and evidence.get("unresolved_historical_systematic_error") is False
    )
