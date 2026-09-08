from __future__ import annotations

from dataclasses import replace

import numpy as np

from app.email_embedding_cache import EmbeddingCacheKey
from app.email_embedding_classifier import EmbeddingModelPrediction
from app.email_historical_classifier import (
    HistoricalActionResult,
    HistoricalClassificationCandidate,
    HistoricalClassificationState,
    HistoricalClassifier,
    enumerate_historical_candidates,
)
from app.email_imap_readonly import ImapUidBatch
from app.email_provider_folders import FolderRole


class _Cache:
    def __init__(self, values):
        self.values = values
        self.keys = []

    def get(self, key):
        self.keys.append(key)
        return self.values.get(key)


class _Model:
    input_schema_version = "input-v3"
    embedding_model_id = "jina"
    embedding_revision = "r17"
    category_thresholds = {"work": 0.8, "legal": 0.9}

    def __init__(self, prediction):
        self.prediction = prediction
        self.calls = []

    def predict(self, vector):
        self.calls.append(vector)
        return self.prediction


def _prediction(*, category="work", work=0.91, legal=0.09, important=False):
    return EmbeddingModelPrediction(
        category=category,
        category_probability={"work": work, "legal": legal}[category],
        category_probabilities={"work": work, "legal": legal},
        category_accepted=True,
        important=important,
        important_probability=0.9 if important else 0.1,
        head_ms=1.0,
    )


def _candidate(identity="mail-1"):
    return HistoricalClassificationCandidate(
        stable_message_identity=identity,
        normalized_text="current normalized message",
    )


def _state(*, role=FolderRole.INBOX, is_read=True, configured=False, unclassified=True):
    return HistoricalClassificationState(
        folder_role=role,
        configured_unclassified_source=configured,
        is_read=is_read,
        is_unclassified=unclassified,
    )


def _cache_for(candidate):
    key = EmbeddingCacheKey.for_text(
        normalized_text=candidate.normalized_text,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )
    return _Cache({key: np.array([1.0, 0.0], dtype=np.float32)})


def test_history_only_moves_read_unclassified_inbox_or_configured_source():
    candidates = tuple(_candidate(f"mail-{index}") for index in range(5))
    states = {
        "mail-0": _state(),
        "mail-1": _state(role=FolderRole.UNBOUND, configured=True),
        "mail-2": _state(is_read=False),
        "mail-3": _state(role=FolderRole.CATEGORY, unclassified=False),
        "mail-4": _state(role=FolderRole.UNBOUND, configured=False),
    }
    cache = _Cache(
        {
            EmbeddingCacheKey.for_text(
                normalized_text=item.normalized_text,
                input_schema_version="input-v3",
                embedding_model_id="jina",
                embedding_revision="r17",
            ): np.array([1.0, 0.0], dtype=np.float32)
            for item in candidates
        }
    )
    executed = []
    runner = HistoricalClassifier(
        model_id="email-embedding-mlp-ready",
        model=_Model(_prediction()),
        cache=cache,
        historically_eligible={"work": True, "legal": True},
        read_state=lambda item: states[item.stable_message_identity],
        execute=lambda item, prediction: executed.append(item.stable_message_identity)
        or HistoricalActionResult("done", is_read=True),
    )

    outcomes = runner.run_batch(candidates)

    assert executed == ["mail-0", "mail-1"]
    assert [item.action_outcome for item in outcomes] == [
        "done",
        "done",
        "not_historical_candidate",
        "not_historical_candidate",
        "not_historical_candidate",
    ]


def test_history_requires_eligible_top1_and_never_uses_eligible_second_place():
    candidate = _candidate()
    runner = HistoricalClassifier(
        model_id="email-embedding-mlp-ready",
        model=_Model(_prediction(category="work", work=0.55, legal=0.45)),
        cache=_cache_for(candidate),
        historically_eligible={"work": False, "legal": True},
        read_state=lambda _item: _state(),
        execute=lambda *_args: (_ for _ in ()).throw(AssertionError("must not move")),
    )

    outcome = runner.run_batch((candidate,))[0]

    assert outcome.predicted_category == "work"
    assert outcome.threshold == 0.8
    assert outcome.action_outcome == "top1_not_historically_eligible"


def test_history_requires_top1_threshold_even_when_prediction_claims_acceptance():
    candidate = _candidate()
    runner = HistoricalClassifier(
        model_id="email-embedding-mlp-ready",
        model=_Model(_prediction(category="work", work=0.79, legal=0.21)),
        cache=_cache_for(candidate),
        historically_eligible={"work": True, "legal": True},
        read_state=lambda _item: _state(),
        execute=lambda *_args: HistoricalActionResult("done", is_read=True),
    )

    assert runner.run_batch((candidate,))[0].action_outcome == "below_category_threshold"


def test_history_uses_exact_cached_vector_batches_of_at_most_eight():
    candidates = tuple(_candidate(f"mail-{index}") for index in range(9))
    cache = _Cache(
        {
            EmbeddingCacheKey.for_text(
                normalized_text=item.normalized_text,
                input_schema_version="input-v3",
                embedding_model_id="jina",
                embedding_revision="r17",
            ): np.array([1.0, 0.0], dtype=np.float32)
            for item in candidates
        }
    )
    runner = HistoricalClassifier(
        model_id="email-embedding-mlp-ready",
        model=_Model(_prediction()),
        cache=cache,
        historically_eligible={"work": True, "legal": True},
        read_state=lambda _item: _state(),
        execute=lambda *_args: HistoricalActionResult("done", is_read=True),
    )

    assert len(runner.run_batch(candidates[:8])) == 8
    with __import__("pytest").raises(ValueError, match="at most 8"):
        runner.run_batch(candidates)
    assert all(isinstance(key, EmbeddingCacheKey) for key in cache.keys)


def test_history_rereads_before_plan_and_execute_and_preserves_read_state():
    candidate = _candidate()
    states = iter((_state(), _state()))
    reads = []
    records = []

    def read_state(item):
        reads.append(item.stable_message_identity)
        return next(states)

    runner = HistoricalClassifier(
        model_id="email-embedding-mlp-ready",
        model=_Model(_prediction(important=True)),
        cache=_cache_for(candidate),
        historically_eligible={"work": True, "legal": True},
        read_state=read_state,
        execute=lambda _item, _prediction: HistoricalActionResult(
            "moved_and_flagged", is_read=True
        ),
        record=records.append,
    )

    outcome = runner.run_batch((candidate,))[0]

    assert reads == ["mail-1", "mail-1"]
    assert outcome.action_outcome == "moved_and_flagged"
    assert outcome.model_id == "email-embedding-mlp-ready"
    assert outcome.predicted_category == "work"
    assert outcome.threshold == 0.8
    assert records == [outcome]

    states = iter((_state(), _state()))
    runner = replace(runner, read_state=lambda _item: next(states))
    runner = replace(
        runner,
        execute=lambda *_args: HistoricalActionResult("done", is_read=False),
    )
    assert runner.run_batch((candidate,))[0].action_outcome == "read_state_not_preserved"


def test_manual_history_enumeration_is_read_source_only_and_bounded_to_eight():
    calls = []
    messages = [
        {
            "messageId": f"<history-{index}@example.com>",
            "accountId": "account-1",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": index + 1,
            "providerUnread": index == 0,
            "subject": f"Historical {index}",
            "textBody": "body",
        }
        for index in range(10)
    ]

    class Source:
        account_id = "account-1"

        def fetch_uid_batch(self, mailbox, **kwargs):
            calls.append((mailbox, kwargs))
            return ImapUidBatch(
                account_id=self.account_id,
                folder=mailbox,
                uidvalidity=42,
                previous_uidvalidity=None,
                messages=messages[: kwargs["limit"]],
            )

    candidates = enumerate_historical_candidates(
        Source(),
        mailbox="INBOX",
        folder_role=FolderRole.INBOX,
        configured_unclassified_source=False,
    )

    assert len(candidates) == 7
    assert all(item.provider_message["providerUnread"] is False for item in candidates)
    assert calls == [
        (
            "INBOX",
            {
                "cursor_uidvalidity": None,
                "last_seen_uid": 0,
                "limit": 8,
                "unread_only": False,
            },
        )
    ]
    assert (
        enumerate_historical_candidates(
            Source(),
            mailbox="Work",
            folder_role=FolderRole.CATEGORY,
            configured_unclassified_source=False,
        )
        == ()
    )
