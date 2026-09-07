from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import pickle
import struct
import time

import numpy as np
import pytest

from app.email_embedding_classifier import (
    CategoryDescription,
    DescriptionAwareEmailClassifier,
    DescriptionVectors,
)
import app.email_embedding_classifier as embedding_classifier_module
from app.email_embedding_client import EmbeddingResult, EmbeddingTiming


CATEGORIES = ("work", "legal")
DESCRIPTIONS = {
    "work": CategoryDescription(
        core="ordinary company operations",
        include=("projects", "product"),
        exclude=("contracts and legal rights",),
        version="work-v1",
    ),
    "legal": CategoryDescription(
        core="legal rights and obligations",
        include=("contracts", "compliance"),
        exclude=("ordinary project delivery",),
        version="legal-v1",
    ),
}
VECTORS = {
    "work": DescriptionVectors(
        core=np.array([1.0, 0.0], dtype=np.float32),
        include=np.array([[1.0, 0.0], [0.8, 0.2]], dtype=np.float32),
        exclude=np.array([[0.0, 1.0]], dtype=np.float32),
    ),
    "legal": DescriptionVectors(
        core=np.array([0.0, 1.0], dtype=np.float32),
        include=np.array([[0.0, 1.0], [0.2, 0.8]], dtype=np.float32),
        exclude=np.array([[1.0, 0.0]], dtype=np.float32),
    ),
}

_ARTIFACT_MAGIC = b"CEOEMAILMLP\x00\x00\x00\x00\x00"
_ARTIFACT_HEADER = struct.Struct(">16sHQ32s")


def _write_marker(path: str) -> None:
    Path(path).write_text("executed", encoding="utf-8")


class _MaliciousPickle:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return _write_marker, (str(self.marker),)


def _training_data() -> tuple[np.ndarray, tuple[str, ...], tuple[bool, ...]]:
    embeddings = np.array(
        [
            [1.0, 0.0],
            [0.9, 0.1],
            [0.8, 0.2],
            [0.0, 1.0],
            [0.1, 0.9],
            [0.2, 0.8],
        ],
        dtype=np.float32,
    )
    return (
        embeddings,
        ("work", "work", "work", "legal", "legal", "legal"),
        (
            False,
            True,
            False,
            False,
            False,
            True,
        ),
    )


def test_description_similarity_changes_logits_in_opposite_directions() -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        alpha=1.0,
        beta=1.0,
    ).fit(embeddings, labels, important)

    work_positive = classifier.description_adjustment(np.array([1.0, 0.0]))
    legal_positive = classifier.description_adjustment(np.array([0.0, 1.0]))

    assert work_positive[0] > work_positive[1]
    assert legal_positive[1] > legal_positive[0]
    assert (
        classifier.category_logits(np.array([1.0, 0.0]))[0]
        > classifier.mlp_logits(np.array([1.0, 0.0]))[0]
    )


def test_shared_alpha_beta_are_fold_fitted_bounded_and_reload_exactly(tmp_path) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )
    assert classifier.alpha == 0.8
    assert classifier.beta == 0.5
    classifier.fit(
        embeddings,
        labels,
        important,
        tuning_folds=((np.array([0, 2, 3, 5]), np.array([1, 4])),),
    )
    assert 0.0 <= classifier.alpha <= 2.0
    assert 0.0 <= classifier.beta <= 2.0

    before = classifier.predict(np.array([0.85, 0.15], dtype=np.float32))
    path = tmp_path / "model.artifact"
    classifier.save(path)
    loaded = DescriptionAwareEmailClassifier.load(path)
    after = loaded.predict(np.array([0.85, 0.15], dtype=np.float32))

    assert loaded.alpha == classifier.alpha
    assert loaded.beta == classifier.beta
    assert loaded.enabled_categories == CATEGORIES
    assert loaded.descriptions == DESCRIPTIONS
    assert after.category == before.category
    assert after.category_probability == before.category_probability
    assert after.category_probabilities == before.category_probabilities
    assert after.category_accepted == before.category_accepted
    assert after.important == before.important
    assert after.important_probability == before.important_probability
    assert loaded.artifact_checksum


def test_category_and_important_heads_are_independent() -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        category_thresholds={"work": 0.8, "legal": 0.85},
        important_threshold=0.7,
    ).fit(embeddings, labels, important)

    prediction = classifier.predict(np.array([0.9, 0.1], dtype=np.float32))
    inverted = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        category_thresholds={"work": 0.8, "legal": 0.85},
        important_threshold=0.7,
    ).fit(embeddings, labels, tuple(not value for value in important))
    inverted_prediction = inverted.predict(np.array([0.9, 0.1], dtype=np.float32))

    assert prediction.category in CATEGORIES
    assert isinstance(prediction.important, bool)
    assert prediction.category_probabilities.keys() == set(CATEGORIES)
    assert (
        prediction.category_probabilities == inverted_prediction.category_probabilities
    )
    assert prediction.important_probability != inverted_prediction.important_probability


def test_artifact_rejects_tampering_and_contains_full_compatibility_metadata(
    tmp_path,
) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    path = tmp_path / "model.artifact"
    classifier.save(path)
    loaded = DescriptionAwareEmailClassifier.load(path)
    assert loaded.enabled_categories == CATEGORIES
    assert loaded.dimension == 2
    assert loaded.input_schema_version == "input-v3"
    assert loaded.embedding_model_id == "jina"
    assert loaded.embedding_revision == "r17"
    assert loaded.category_thresholds == {"work": 0.5, "legal": 0.5}
    assert loaded.important_threshold == 0.5
    assert loaded.descriptions == DESCRIPTIONS
    assert loaded.artifact_checksum == classifier.artifact_checksum
    path.write_bytes(path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 1]))

    with pytest.raises(ValueError, match="checksum|artifact"):
        DescriptionAwareEmailClassifier.load(path)


def test_artifact_is_immutable_once_published(tmp_path) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    path = tmp_path / "model.artifact"
    classifier.save(path)
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        classifier.save(path)

    assert path.read_bytes() == original


def test_malicious_outer_pickle_is_rejected_without_executing_reduction(
    tmp_path,
) -> None:
    marker = tmp_path / "outer-executed"
    artifact = tmp_path / "malicious.artifact"
    artifact.write_bytes(pickle.dumps(_MaliciousPickle(marker)))

    with pytest.raises(ValueError, match="artifact|framing"):
        DescriptionAwareEmailClassifier.load(artifact)

    assert not marker.exists()


def test_bad_checksum_rejects_malicious_payload_before_pickle_load(tmp_path) -> None:
    marker = tmp_path / "payload-executed"
    payload = pickle.dumps(_MaliciousPickle(marker))
    artifact = tmp_path / "bad-checksum.artifact"
    artifact.write_bytes(
        _ARTIFACT_HEADER.pack(
            _ARTIFACT_MAGIC,
            1,
            len(payload),
            b"\x00" * 32,
        )
        + payload
    )

    with pytest.raises(ValueError, match="checksum"):
        DescriptionAwareEmailClassifier.load(artifact)

    assert not marker.exists()


def test_valid_checksum_malicious_pickle_is_never_executed(tmp_path) -> None:
    marker = tmp_path / "valid-checksum-payload-executed"
    payload = pickle.dumps(_MaliciousPickle(marker))
    artifact = tmp_path / "malicious-payload.artifact"
    artifact.write_bytes(
        _ARTIFACT_HEADER.pack(
            _ARTIFACT_MAGIC,
            1,
            len(payload),
            sha256(payload).digest(),
        )
        + payload
    )

    with pytest.raises(ValueError, match="artifact|payload|archive"):
        DescriptionAwareEmailClassifier.load(artifact)

    assert not marker.exists()


def test_appended_artifact_bytes_are_rejected_before_deserialization(
    tmp_path,
) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    artifact = tmp_path / "appended.artifact"
    classifier.save(artifact)
    artifact.write_bytes(artifact.read_bytes() + b"trailing bytes")
    with pytest.raises(ValueError, match="length|trailing|artifact"):
        DescriptionAwareEmailClassifier.load(artifact)
    assert not hasattr(embedding_classifier_module, "pickle")


def test_payload_flip_is_rejected_by_checksum_before_deserialization(
    tmp_path,
) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    artifact = tmp_path / "modified.artifact"
    classifier.save(artifact)
    framed = bytearray(artifact.read_bytes())
    framed[-1] ^= 1
    artifact.write_bytes(framed)
    with pytest.raises(ValueError, match="checksum"):
        DescriptionAwareEmailClassifier.load(artifact)
    assert not hasattr(embedding_classifier_module, "pickle")


@pytest.mark.parametrize("mutation", ["missing", "short-header", "truncated-payload"])
def test_missing_or_truncated_artifact_is_rejected(tmp_path, mutation: str) -> None:
    artifact = tmp_path / "truncated.artifact"
    if mutation == "missing":
        with pytest.raises(ValueError, match="missing|artifact"):
            DescriptionAwareEmailClassifier.load(artifact)
        return

    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    classifier.save(artifact)
    framed = artifact.read_bytes()
    artifact.write_bytes(
        framed[: _ARTIFACT_HEADER.size - 1]
        if mutation == "short-header"
        else framed[:-1]
    )

    with pytest.raises(ValueError, match="length|truncated|artifact"):
        DescriptionAwareEmailClassifier.load(artifact)


@pytest.mark.parametrize(
    ("train_indices", "validation_indices", "message"),
    [
        ([True, 2, 3, 5], [0, 4], "integer"),
        ([0.0, 2, 3, 5], [1, 4], "integer"),
        ([-1, 0, 3, 4], [1, 2], "range"),
        ([0, 2, 3, 6], [1, 4], "range"),
        ([0, 0, 3, 5], [1, 4], "duplicate"),
        ([0, 2, 3, 5], [1, 1], "duplicate"),
        ([0, 2, 3, 5], [0, 4], "overlap"),
    ],
)
def test_tuning_fold_indices_reject_aliases_and_leakage_before_indexing(
    train_indices, validation_indices, message
) -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    )

    with pytest.raises(ValueError, match=message):
        classifier.fit(
            embeddings,
            labels,
            important,
            tuning_folds=((train_indices, validation_indices),),
        )


def test_cached_head_p95_is_below_one_hundred_ms() -> None:
    embeddings, labels, important = _training_data()
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
    ).fit(embeddings, labels, important)
    values = []
    for _ in range(30):
        started = time.perf_counter()
        classifier.predict(np.array([0.7, 0.3], dtype=np.float32))
        values.append((time.perf_counter() - started) * 1000.0)
    assert float(np.percentile(values, 95)) < 100.0


def test_combined_timing_records_all_required_stages_with_fake_clock() -> None:
    embeddings, labels, important = _training_data()
    values = iter((10.000, 10.002))
    classifier = DescriptionAwareEmailClassifier(
        enabled_categories=CATEGORIES,
        descriptions=DESCRIPTIONS,
        description_vectors=VECTORS,
        dimension=2,
        input_schema_version="input-v3",
        embedding_model_id="jina",
        embedding_revision="r17",
        clock=lambda: next(values),
    ).fit(embeddings, labels, important)
    embedding_result = EmbeddingResult(
        vectors=np.array([[0.7, 0.3]], dtype=np.float32),
        timing=EmbeddingTiming(
            queue_ms=1.0,
            http_ms=5.0,
            embedding_ms=6.0,
            head_ms=0.0,
            total_ms=7.0,
        ),
    )

    result = classifier.predict_result(embedding_result)

    assert result.timing.to_dict() == {
        "queue_ms": 1.0,
        "http_ms": 5.0,
        "embedding_ms": 6.0,
        "head_ms": pytest.approx(2.0),
        "total_ms": pytest.approx(9.0),
    }
