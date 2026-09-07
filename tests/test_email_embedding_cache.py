from __future__ import annotations

import numpy as np

from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey


def _key(**overrides: object) -> EmbeddingCacheKey:
    values = {
        "normalized_input_hash": "a" * 64,
        "input_schema_version": "email-input-v3",
        "embedding_model_id": "jinaai/jina-embeddings-v5-text-small",
        "embedding_revision": "gpu4-r17",
    }
    values.update(overrides)
    return EmbeddingCacheKey(**values)


def test_cache_identity_requires_all_four_version_fields(tmp_path) -> None:
    cache = EmbeddingCache(tmp_path, dimension=3)
    vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    cache.put(_key(), vector)

    assert np.array_equal(cache.get(_key()), vector)
    for field, value in (
        ("normalized_input_hash", "b" * 64),
        ("input_schema_version", "email-input-v4"),
        ("embedding_model_id", "different-model"),
        ("embedding_revision", "gpu4-r18"),
    ):
        assert cache.get(_key(**{field: value})) is None


def test_description_key_uses_description_version_hash(tmp_path) -> None:
    first = EmbeddingCacheKey.for_description(
        text="external bills issued to us",
        description_version="desc-v1",
        input_schema_version="description-input-v1",
        embedding_model_id="jina",
        embedding_revision="r1",
    )
    second = EmbeddingCacheKey.for_description(
        text="external bills issued to us",
        description_version="desc-v2",
        input_schema_version="description-input-v1",
        embedding_model_id="jina",
        embedding_revision="r1",
    )
    assert first.normalized_input_hash != second.normalized_input_hash


def test_corrupt_dimension_model_and_revision_are_cache_misses(tmp_path) -> None:
    cache = EmbeddingCache(tmp_path, dimension=3)
    key = _key()
    cache.put(key, np.array([1.0, 2.0, 3.0], dtype=np.float32))
    path = cache.path_for(key)
    path.write_bytes(b"not an npz")
    assert cache.get(key) is None

    cache.put(key, np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert EmbeddingCache(tmp_path, dimension=4).get(key) is None
    assert cache.get(_key(embedding_model_id="other")) is None
    assert cache.get(_key(embedding_revision="other")) is None


def test_cache_write_is_atomic_float32_under_registry_root(tmp_path) -> None:
    registry = tmp_path / "registry"
    cache = EmbeddingCache(registry, dimension=2)
    key = _key()
    cache.put(key, np.array([1.5, 2.5], dtype=np.float64))

    loaded = cache.get(key)
    assert loaded is not None and loaded.dtype == np.float32
    assert cache.path_for(key).is_relative_to(registry)
    assert not list(cache.path_for(key).parent.glob("*.tmp"))


def test_truncated_valid_npz_is_a_cache_miss(tmp_path) -> None:
    cache = EmbeddingCache(tmp_path, dimension=3)
    key = _key()
    cache.put(key, np.array([1.0, 2.0, 3.0], dtype=np.float32))
    path = cache.path_for(key)
    path.write_bytes(path.read_bytes()[:-12])

    assert cache.get(key) is None
