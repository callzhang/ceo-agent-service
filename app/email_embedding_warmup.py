"""Fill the immutable cache required by a frozen embedding training run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey
from app.email_embedding_classifier import CategoryDescription


@dataclass(frozen=True)
class EmbeddingWarmupResult:
    cache_hits: int
    cache_writes: int


def warm_frozen_training_embeddings(
    *,
    cache: EmbeddingCache,
    embedding_client: object,
    snapshot: Mapping[str, object],
    descriptions: Mapping[str, CategoryDescription],
    embedding_model_id: str,
    embedding_revision: str,
    selected_message_identities: Sequence[str] | None = None,
) -> EmbeddingWarmupResult:
    """Cache the selected frozen inputs and their immutable descriptions once.

    The trainer only consumes cache entries, so this boundary keeps remote
    embedding I/O outside the fitting and artifact-writing steps.
    """

    input_schema_version = _required_text(
        snapshot.get("input_schema_version"), "snapshot input schema version"
    )
    observations = snapshot.get("observations")
    if not isinstance(observations, Sequence):
        raise ValueError("snapshot observations are invalid")
    selected = (
        None
        if selected_message_identities is None
        else frozenset(
            _required_text(value, "selected message identity")
            for value in selected_message_identities
        )
    )
    missing: dict[str, tuple[EmbeddingCacheKey, str]] = {}
    cache_hits = 0

    def collect(key: EmbeddingCacheKey, text: str) -> None:
        nonlocal cache_hits
        if cache.get(key) is not None:
            cache_hits += 1
            return
        missing.setdefault(key.digest, (key, text))

    for row in observations:
        if not isinstance(row, Mapping):
            raise ValueError("snapshot observation is invalid")
        identity = _required_text(
            row.get("stable_message_identity"), "snapshot message identity"
        )
        if selected is not None and identity not in selected:
            continue
        normalized_text = _required_text(
            row.get("normalized_model_input"), "snapshot normalized input"
        )
        collect(
            EmbeddingCacheKey.for_text(
                normalized_text=normalized_text,
                input_schema_version=input_schema_version,
                embedding_model_id=embedding_model_id,
                embedding_revision=embedding_revision,
            ),
            normalized_text,
        )
    for description in descriptions.values():
        if not isinstance(description, CategoryDescription):
            raise TypeError("description is invalid")
        for text in (description.core, *description.include, *description.exclude):
            text = _required_text(text, "category description text")
            collect(
                EmbeddingCacheKey.for_description(
                    text=text,
                    description_version=description.version,
                    input_schema_version=input_schema_version,
                    embedding_model_id=embedding_model_id,
                    embedding_revision=embedding_revision,
                ),
                text,
            )
    if not missing:
        return EmbeddingWarmupResult(cache_hits=cache_hits, cache_writes=0)
    embed = getattr(embedding_client, "embed", None)
    if not callable(embed):
        raise TypeError("embedding client must provide embed")
    entries = tuple(missing.values())
    result = embed([text for _key, text in entries])
    vectors = getattr(result, "vectors", None)
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.shape != (len(entries), cache.dimension):
        raise ValueError("embedding response does not match requested inputs")
    for (key, _text), vector in zip(entries, matrix, strict=True):
        cache.put(key, vector)
    return EmbeddingWarmupResult(cache_hits=cache_hits, cache_writes=len(entries))


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value
