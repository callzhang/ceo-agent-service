"""Gated, cached classification of read mail in unclassified source folders."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.email_classifier_contracts import validate_email_category_key
from app.email_embedding_cache import EmbeddingCacheKey
from app.email_embedding_classifier import EmbeddingModelPrediction
from app.email_provider_folders import FolderRole


MAX_HISTORICAL_BATCH_SIZE = 8
MAX_HISTORICAL_ENUMERATION_PAGE_SIZE = 50


@dataclass(frozen=True)
class HistoricalClassificationCandidate:
    stable_message_identity: str
    normalized_text: str
    provider_message: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.stable_message_identity.strip():
            raise ValueError("stable_message_identity must be nonblank")
        if not self.normalized_text:
            raise ValueError("normalized_text must be nonempty")
        if self.provider_message is not None and not isinstance(
            self.provider_message, Mapping
        ):
            raise TypeError("provider_message must be a mapping or None")


def enumerate_historical_candidates(
    source: object,
    *,
    mailbox: str,
    folder_role: FolderRole,
    configured_unclassified_source: bool,
    limit: int = MAX_HISTORICAL_BATCH_SIZE,
) -> tuple[HistoricalClassificationCandidate, ...]:
    """Enumerate a bounded read-only source-folder batch for a manual job."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
        raise ValueError("historical enumeration limit must be between 1 and 8")
    source_is_unclassified = folder_role is FolderRole.INBOX or (
        folder_role is FolderRole.UNBOUND and configured_unclassified_source
    )
    if not source_is_unclassified:
        return ()
    from app.email_classifier_model import email_message_to_text
    from app.email_classifier_scan import _provider_locator, _stable_message_identity
    from app.email_imap_readonly import ImapUidBatch

    batch = source.fetch_uid_batch(
        mailbox,
        cursor_uidvalidity=None,
        last_seen_uid=0,
        limit=limit,
        unread_only=False,
    )
    if not isinstance(batch, ImapUidBatch):
        raise TypeError("fetch_uid_batch must return ImapUidBatch")
    result = []
    for message in batch.messages:
        if message.get("providerUnread") is not False:
            continue
        locator = _provider_locator(message)
        result.append(
            HistoricalClassificationCandidate(
                stable_message_identity=_stable_message_identity(message, locator),
                normalized_text=email_message_to_text(message),
                provider_message=message,
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class HistoricalEnumerationPage:
    candidates: tuple[HistoricalClassificationCandidate, ...]
    uidvalidity: int
    last_seen_uid: int


def enumerate_historical_page(
    source: object,
    *,
    mailbox: str,
    folder_role: FolderRole,
    configured_unclassified_source: bool,
    cursor_uidvalidity: int | None,
    last_seen_uid: int,
    limit: int = MAX_HISTORICAL_ENUMERATION_PAGE_SIZE,
) -> HistoricalEnumerationPage:
    """Page forward independently of the at-most-eight execution batch."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("historical page limit must be between 1 and 50")
    source_is_unclassified = folder_role is FolderRole.INBOX or (
        folder_role is FolderRole.UNBOUND and configured_unclassified_source
    )
    if not source_is_unclassified:
        return HistoricalEnumerationPage((), cursor_uidvalidity or 0, last_seen_uid)
    from app.email_classifier_model import email_message_to_text
    from app.email_classifier_scan import _provider_locator, _stable_message_identity
    from app.email_imap_readonly import ImapUidBatch

    batch = source.fetch_uid_batch(
        mailbox,
        cursor_uidvalidity=cursor_uidvalidity,
        last_seen_uid=last_seen_uid,
        limit=limit,
        unread_only=False,
    )
    if not isinstance(batch, ImapUidBatch):
        raise TypeError("fetch_uid_batch must return ImapUidBatch")
    candidates = tuple(
        HistoricalClassificationCandidate(
            stable_message_identity=_stable_message_identity(
                message, _provider_locator(message)
            ),
            normalized_text=email_message_to_text(message),
            provider_message=message,
        )
        for message in batch.messages
    )
    page_last_uid = max(
        (int(item.provider_message.get("uid") or 0) for item in candidates),
        default=last_seen_uid,
    )
    return HistoricalEnumerationPage(candidates, batch.uidvalidity, page_last_uid)


@dataclass(frozen=True)
class HistoricalClassificationState:
    folder_role: FolderRole
    configured_unclassified_source: bool
    is_read: bool
    is_unclassified: bool
    provider_folder_name: str | None = None

    def __post_init__(self) -> None:
        if type(self.folder_role) is not FolderRole:
            raise TypeError("folder_role must be a FolderRole")
        for name in (
            "configured_unclassified_source",
            "is_read",
            "is_unclassified",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a strict bool")
        if self.provider_folder_name is not None and (
            type(self.provider_folder_name) is not str
            or not self.provider_folder_name.strip()
        ):
            raise ValueError("provider_folder_name must be nonblank text or None")

    @property
    def eligible(self) -> bool:
        source = self.folder_role is FolderRole.INBOX or (
            self.folder_role is FolderRole.UNBOUND
            and self.configured_unclassified_source
        )
        return self.is_read and self.is_unclassified and source


@dataclass(frozen=True)
class HistoricalActionResult:
    outcome: str
    is_read: bool

    def __post_init__(self) -> None:
        if not self.outcome.strip():
            raise ValueError("outcome must be nonblank")
        if type(self.is_read) is not bool:
            raise TypeError("is_read must be a strict bool")


@dataclass(frozen=True)
class HistoricalClassificationOutcome:
    stable_message_identity: str
    model_id: str
    predicted_category: str | None
    threshold: float | None
    probability: float | None
    important: bool | None
    action_outcome: str


@dataclass(frozen=True)
class HistoricalClassifier:
    """Run an explicitly invoked historical batch; never used by the scan loop."""

    model_id: str
    model: object
    cache: object
    historically_eligible: Mapping[str, bool]
    read_state: Callable[
        [HistoricalClassificationCandidate], HistoricalClassificationState
    ]
    execute: Callable[
        [HistoricalClassificationCandidate, EmbeddingModelPrediction],
        HistoricalActionResult,
    ]
    record: Callable[[HistoricalClassificationOutcome], object] | None = None

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must be nonblank")
        expected = tuple(getattr(self.model, "category_thresholds", ()))
        observed = tuple(self.historically_eligible)
        if set(expected) != set(observed):
            raise ValueError("historical eligibility must cover model categories")
        if any(type(value) is not bool for value in self.historically_eligible.values()):
            raise TypeError("historical eligibility values must be strict booleans")

    def run_batch(
        self, candidates: Sequence[HistoricalClassificationCandidate]
    ) -> tuple[HistoricalClassificationOutcome, ...]:
        values = tuple(candidates)
        if len(values) > MAX_HISTORICAL_BATCH_SIZE:
            raise ValueError("historical batches contain at most 8 messages")
        if any(type(item) is not HistoricalClassificationCandidate for item in values):
            raise TypeError("historical candidates are invalid")
        return tuple(self._run_one(item) for item in values)

    def _run_one(
        self, candidate: HistoricalClassificationCandidate
    ) -> HistoricalClassificationOutcome:
        planning_state = self.read_state(candidate)
        if not planning_state.eligible:
            return self._outcome(candidate, action_outcome="not_historical_candidate")
        key = EmbeddingCacheKey.for_text(
            normalized_text=candidate.normalized_text,
            input_schema_version=str(self.model.input_schema_version),
            embedding_model_id=str(self.model.embedding_model_id),
            embedding_revision=str(self.model.embedding_revision),
        )
        vector = self.cache.get(key)
        if vector is None:
            return self._outcome(candidate, action_outcome="exact_cache_miss")
        prediction = self.model.predict(vector)
        if type(prediction) is not EmbeddingModelPrediction:
            raise TypeError("historical model prediction is invalid")
        category = validate_email_category_key(prediction.category)
        threshold = float(self.model.category_thresholds[category])
        if not self.historically_eligible[category]:
            return self._prediction_outcome(
                candidate,
                prediction,
                threshold,
                "top1_not_historically_eligible",
            )
        if prediction.category_probability < threshold:
            return self._prediction_outcome(
                candidate,
                prediction,
                threshold,
                "below_category_threshold",
            )
        execution_state = self.read_state(candidate)
        if not execution_state.eligible:
            return self._prediction_outcome(
                candidate,
                prediction,
                threshold,
                "state_changed_before_execute",
            )
        action = self.execute(candidate, prediction)
        outcome = action.outcome if action.is_read else "read_state_not_preserved"
        return self._prediction_outcome(candidate, prediction, threshold, outcome)

    def _prediction_outcome(
        self,
        candidate: HistoricalClassificationCandidate,
        prediction: EmbeddingModelPrediction,
        threshold: float,
        action_outcome: str,
    ) -> HistoricalClassificationOutcome:
        return self._outcome(
            candidate,
            predicted_category=prediction.category,
            threshold=threshold,
            probability=prediction.category_probability,
            important=prediction.important,
            action_outcome=action_outcome,
        )

    def _outcome(
        self,
        candidate: HistoricalClassificationCandidate,
        *,
        predicted_category: str | None = None,
        threshold: float | None = None,
        probability: float | None = None,
        important: bool | None = None,
        action_outcome: str,
    ) -> HistoricalClassificationOutcome:
        result = HistoricalClassificationOutcome(
            stable_message_identity=candidate.stable_message_identity,
            model_id=self.model_id,
            predicted_category=predicted_category,
            threshold=threshold,
            probability=probability,
            important=important,
            action_outcome=action_outcome,
        )
        if self.record is not None:
            self.record(result)
        return result
