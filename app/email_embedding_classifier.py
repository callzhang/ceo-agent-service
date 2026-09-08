"""Description-aware embedding classifier with independent category/important heads."""

from __future__ import annotations

import hmac
import io
import json
import math
import os
import struct
import tempfile
import time
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import numpy as np
from sklearn.neural_network import MLPClassifier

from app.email_classifier_contracts import validate_email_category_key
from app.email_embedding_client import EmbeddingResult, EmbeddingTiming


ARTIFACT_FRAME_MAGIC = b"CEOEMAILMLP\x00\x00\x00\x00\x00"
ARTIFACT_FRAME_VERSION = 1
_ARTIFACT_HEADER = struct.Struct(">16sHQ32s")
_ARTIFACT_SCHEMA = "email-description-mlp-npz-v1"
_MAX_ARTIFACT_PAYLOAD_BYTES = 64 * 1024 * 1024
_MAX_ARTIFACT_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_ZIP_LOCAL_MAGIC = b"PK\x03\x04"
_ZIP_EMPTY_COMMENT_EOCD_MAGIC = b"PK\x05\x06"


@dataclass(frozen=True)
class CategoryDescription:
    core: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    version: str

    def __post_init__(self) -> None:
        for field in ("core", "version"):
            value = getattr(self, field)
            if not value or value != value.strip():
                raise ValueError(f"{field} must be canonical non-empty text")
        for field in ("include", "exclude"):
            values = getattr(self, field)
            if not values or any(not item or item != item.strip() for item in values):
                raise ValueError(f"{field} must contain canonical non-empty text")


@dataclass(frozen=True)
class DescriptionVectors:
    core: np.ndarray
    include: np.ndarray
    exclude: np.ndarray


@dataclass(frozen=True)
class EmbeddingModelPrediction:
    category: str
    category_probability: float
    category_probabilities: Mapping[str, float]
    category_accepted: bool
    important: bool
    important_probability: float
    head_ms: float


@dataclass(frozen=True)
class TimedEmbeddingModelPrediction:
    prediction: EmbeddingModelPrediction
    timing: EmbeddingTiming


class DescriptionAwareEmailClassifier:
    FORMAT_VERSION = 1
    DEFAULT_ALPHA = 0.8
    DEFAULT_BETA = 0.5
    _WEIGHT_GRID = (0.0, 0.5, 0.8, 1.0, 1.5, 2.0)

    def __init__(
        self,
        *,
        enabled_categories: Sequence[str],
        descriptions: Mapping[str, CategoryDescription],
        description_vectors: Mapping[str, DescriptionVectors],
        dimension: int,
        input_schema_version: str,
        embedding_model_id: str,
        embedding_revision: str,
        category_thresholds: Mapping[str, float] | None = None,
        important_threshold: float = 0.5,
        alpha: float = DEFAULT_ALPHA,
        beta: float = DEFAULT_BETA,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        categories = tuple(
            validate_email_category_key(item) for item in enabled_categories
        )
        if len(categories) < 2 or len(set(categories)) != len(categories):
            raise ValueError("enabled_categories must contain unique category keys")
        if set(descriptions) != set(categories) or set(description_vectors) != set(
            categories
        ):
            raise ValueError("descriptions and vectors must match enabled categories")
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or dimension < 1
        ):
            raise ValueError("dimension must be a positive integer")
        self.enabled_categories = categories
        self.descriptions = MappingProxyType(dict(descriptions))
        self.dimension = dimension
        self.input_schema_version = _required_text(
            input_schema_version, "input_schema_version"
        )
        self.embedding_model_id = _required_text(
            embedding_model_id, "embedding_model_id"
        )
        self.embedding_revision = _required_text(
            embedding_revision, "embedding_revision"
        )
        self.category_thresholds = MappingProxyType(
            self._thresholds(category_thresholds or {key: 0.5 for key in categories})
        )
        self.important_threshold = _probability(
            important_threshold, "important_threshold"
        )
        self.alpha = _box_weight(alpha, "alpha")
        self.beta = _box_weight(beta, "beta")
        self._clock = clock
        self._description_vectors = MappingProxyType(
            {
                key: self._validated_vectors(description_vectors[key])
                for key in categories
            }
        )
        self._category_head: MLPClassifier | None = None
        self._important_head: MLPClassifier | None = None
        self.artifact_checksum = ""

    def fit(
        self,
        embeddings: np.ndarray,
        category_labels: Sequence[str],
        important_labels: Sequence[bool],
        *,
        important_embeddings: np.ndarray | None = None,
        tuning_folds: Sequence[tuple[np.ndarray, np.ndarray]] = (),
    ) -> "DescriptionAwareEmailClassifier":
        matrix = self._matrix(embeddings)
        important_matrix = (
            matrix
            if important_embeddings is None
            else self._matrix(important_embeddings)
        )
        labels = tuple(validate_email_category_key(item) for item in category_labels)
        important = tuple(important_labels)
        if len(matrix) != len(labels) or not len(matrix):
            raise ValueError("category training inputs must be non-empty and aligned")
        if len(important_matrix) != len(important) or not len(important_matrix):
            raise ValueError("training inputs must be non-empty and aligned")
        if set(labels) != set(self.enabled_categories):
            raise ValueError("training labels must cover enabled categories exactly")
        if (
            any(type(item) is not bool for item in important)
            or len(set(important)) != 2
        ):
            raise ValueError("important labels must be binary and cover both values")
        if tuning_folds:
            self.alpha, self.beta = self._fit_weights_in_folds(
                matrix, labels, tuning_folds
            )
        self._category_head = self._new_head().fit(matrix, labels)
        self._important_head = self._new_head().fit(important_matrix, important)
        return self

    def mlp_logits(self, embedding: np.ndarray) -> np.ndarray:
        head = self._require_category_head()
        probabilities = head.predict_proba(self._row(embedding))[0]
        by_class = {
            str(key): float(value) for key, value in zip(head.classes_, probabilities)
        }
        return np.asarray(
            [math.log(max(by_class[key], 1e-12)) for key in self.enabled_categories],
            dtype=np.float64,
        )

    def description_adjustment(self, embedding: np.ndarray) -> np.ndarray:
        row = _normalize(self._row(embedding)[0])
        values = []
        for key in self.enabled_categories:
            vectors = self._description_vectors[key]
            positive = np.concatenate(
                (vectors.core.reshape(1, -1), vectors.include), axis=0
            )
            positive_similarity = float(np.mean(_normalize_rows(positive) @ row))
            exclusion_similarity = float(
                np.mean(_normalize_rows(vectors.exclude) @ row)
            )
            values.append(
                self.alpha * positive_similarity - self.beta * exclusion_similarity
            )
        return np.asarray(values, dtype=np.float64)

    def category_logits(self, embedding: np.ndarray) -> np.ndarray:
        return self.mlp_logits(embedding) + self.description_adjustment(embedding)

    def predict(self, embedding: np.ndarray) -> EmbeddingModelPrediction:
        started = self._clock()
        logits = self.category_logits(embedding)
        shifted = logits - float(np.max(logits))
        probabilities = np.exp(shifted) / float(np.exp(shifted).sum())
        top = int(np.argmax(probabilities))
        category = self.enabled_categories[top]
        important_head = self._require_important_head()
        raw_important = important_head.predict_proba(self._row(embedding))[0]
        class_probabilities = {
            bool(key): float(value)
            for key, value in zip(important_head.classes_, raw_important)
        }
        important_probability = class_probabilities[True]
        return EmbeddingModelPrediction(
            category=category,
            category_probability=float(probabilities[top]),
            category_probabilities=MappingProxyType(
                {
                    key: float(value)
                    for key, value in zip(self.enabled_categories, probabilities)
                }
            ),
            category_accepted=float(probabilities[top])
            >= self.category_thresholds[category],
            important=important_probability >= self.important_threshold,
            important_probability=important_probability,
            head_ms=(self._clock() - started) * 1000.0,
        )

    def predict_result(
        self, embedding_result: EmbeddingResult, *, index: int = 0
    ) -> TimedEmbeddingModelPrediction:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index must be an integer")
        if index < 0 or index >= len(embedding_result.vectors):
            raise IndexError("embedding result index is out of range")
        prediction = self.predict(embedding_result.vectors[index])
        timing = embedding_result.timing
        return TimedEmbeddingModelPrediction(
            prediction=prediction,
            timing=EmbeddingTiming(
                queue_ms=timing.queue_ms,
                http_ms=timing.http_ms,
                embedding_ms=timing.embedding_ms,
                head_ms=prediction.head_ms,
                total_ms=timing.total_ms + prediction.head_ms,
            ),
        )

    def save(self, path: str | Path) -> None:
        if self._category_head is None or self._important_head is None:
            raise RuntimeError("classifier is not fitted")
        manifest, arrays = self._safe_artifact_content()
        manifest_bytes = json.dumps(
            manifest,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        payload_buffer = io.BytesIO()
        np.savez(
            payload_buffer,
            manifest=np.frombuffer(manifest_bytes, dtype=np.uint8),
            **arrays,
        )
        payload_bytes = payload_buffer.getvalue()
        if len(payload_bytes) > _MAX_ARTIFACT_PAYLOAD_BYTES:
            raise ValueError("embedding classifier artifact payload is too large")
        checksum = sha256(payload_bytes).hexdigest()
        framed_artifact = _ARTIFACT_HEADER.pack(
            ARTIFACT_FRAME_MAGIC,
            ARTIFACT_FRAME_VERSION,
            len(payload_bytes),
            bytes.fromhex(checksum),
        )
        framed_artifact += payload_bytes
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(framed_artifact)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, destination)
            temporary.unlink()
            temporary = None
            self.artifact_checksum = checksum
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> "DescriptionAwareEmailClassifier":
        payload_bytes, checksum = _read_verified_artifact_frame(path)
        manifest, arrays = _read_safe_npz_payload(payload_bytes)
        _validate_exact_keys(
            manifest,
            {
                "schema",
                "format_version",
                "enabled_categories",
                "category_thresholds",
                "important_threshold",
                "descriptions",
                "dimension",
                "input_schema_version",
                "embedding_model_id",
                "embedding_revision",
                "alpha",
                "beta",
                "category_head",
                "important_head",
            },
            "artifact manifest",
        )
        if manifest["schema"] != _ARTIFACT_SCHEMA:
            raise ValueError("unsupported embedding classifier artifact schema")
        if (
            type(manifest["format_version"]) is not int
            or manifest["format_version"] != cls.FORMAT_VERSION
        ):
            raise ValueError("unsupported embedding classifier artifact version")
        enabled_categories = _validated_manifest_categories(
            manifest["enabled_categories"]
        )
        dimension = _manifest_positive_int(manifest["dimension"], "dimension")
        descriptions, description_vectors, description_keys = _load_descriptions(
            manifest["descriptions"],
            arrays,
            enabled_categories=enabled_categories,
            dimension=dimension,
        )
        category_head, category_keys = _load_mlp_head(
            manifest["category_head"],
            arrays,
            prefix="category",
            dimension=dimension,
            expected_classes=enabled_categories,
        )
        important_head, important_keys = _load_mlp_head(
            manifest["important_head"],
            arrays,
            prefix="important",
            dimension=dimension,
            expected_classes=(False, True),
        )
        expected_array_keys = {
            "manifest",
            *description_keys,
            *category_keys,
            *important_keys,
        }
        if set(arrays) != expected_array_keys or len(arrays) != len(
            expected_array_keys
        ):
            raise ValueError("embedding classifier artifact contains unexpected arrays")
        result = cls(
            enabled_categories=enabled_categories,
            descriptions=descriptions,
            description_vectors=description_vectors,
            dimension=dimension,
            input_schema_version=_manifest_text(
                manifest["input_schema_version"], "input_schema_version"
            ),
            embedding_model_id=_manifest_text(
                manifest["embedding_model_id"], "embedding_model_id"
            ),
            embedding_revision=_manifest_text(
                manifest["embedding_revision"], "embedding_revision"
            ),
            category_thresholds=_validated_manifest_thresholds(
                manifest["category_thresholds"], enabled_categories
            ),
            important_threshold=_manifest_probability(
                manifest["important_threshold"], "important_threshold"
            ),
            alpha=_manifest_box_weight(manifest["alpha"], "alpha"),
            beta=_manifest_box_weight(manifest["beta"], "beta"),
        )
        result._category_head = category_head
        result._important_head = important_head
        result.artifact_checksum = checksum
        return result

    def _safe_artifact_content(
        self,
    ) -> tuple[dict[str, object], dict[str, np.ndarray]]:
        arrays: dict[str, np.ndarray] = {}
        description_entries: list[dict[str, object]] = []
        for index, category in enumerate(self.enabled_categories):
            description = self.descriptions[category]
            vectors = self._description_vectors[category]
            arrays[f"description_{index}_core"] = vectors.core
            arrays[f"description_{index}_include"] = vectors.include
            arrays[f"description_{index}_exclude"] = vectors.exclude
            description_entries.append(
                {
                    "category": category,
                    "core": description.core,
                    "include": list(description.include),
                    "exclude": list(description.exclude),
                    "version": description.version,
                }
            )
        category_head, category_arrays = _serialize_mlp_head(
            self._require_category_head(),
            prefix="category",
            expected_classes=self.enabled_categories,
            dimension=self.dimension,
        )
        important_head, important_arrays = _serialize_mlp_head(
            self._require_important_head(),
            prefix="important",
            expected_classes=(False, True),
            dimension=self.dimension,
        )
        arrays.update(category_arrays)
        arrays.update(important_arrays)
        return (
            {
                "schema": _ARTIFACT_SCHEMA,
                "format_version": self.FORMAT_VERSION,
                "enabled_categories": list(self.enabled_categories),
                "category_thresholds": dict(self.category_thresholds),
                "important_threshold": self.important_threshold,
                "descriptions": description_entries,
                "dimension": self.dimension,
                "input_schema_version": self.input_schema_version,
                "embedding_model_id": self.embedding_model_id,
                "embedding_revision": self.embedding_revision,
                "alpha": self.alpha,
                "beta": self.beta,
                "category_head": category_head,
                "important_head": important_head,
            },
            arrays,
        )

    def _fit_weights_in_folds(
        self,
        matrix: np.ndarray,
        labels: tuple[str, ...],
        folds: Sequence[tuple[np.ndarray, np.ndarray]],
    ) -> tuple[float, float]:
        validated_folds = _validated_tuning_folds(folds, sample_count=len(matrix))
        label_array = np.asarray(labels)
        best = (float("-inf"), self.DEFAULT_ALPHA, self.DEFAULT_BETA)
        for alpha in self._WEIGHT_GRID:
            for beta in self._WEIGHT_GRID:
                correct = 0
                count = 0
                for train, validation in validated_folds:
                    fold_head = self._new_head().fit(matrix[train], label_array[train])
                    for index in validation:
                        base = self._ordered_logits(fold_head, matrix[index])
                        adjustment = self._adjustment_with_weights(
                            matrix[index], alpha, beta
                        )
                        predicted = self.enabled_categories[
                            int(np.argmax(base + adjustment))
                        ]
                        correct += predicted == labels[int(index)]
                        count += 1
                score = correct / count
                candidate = (
                    score,
                    -abs(alpha - self.DEFAULT_ALPHA) - abs(beta - self.DEFAULT_BETA),
                )
                incumbent = (
                    best[0],
                    -abs(best[1] - self.DEFAULT_ALPHA)
                    - abs(best[2] - self.DEFAULT_BETA),
                )
                if candidate > incumbent:
                    best = (score, alpha, beta)
        return best[1], best[2]

    def _adjustment_with_weights(
        self, embedding: np.ndarray, alpha: float, beta: float
    ) -> np.ndarray:
        old_alpha, old_beta = self.alpha, self.beta
        self.alpha, self.beta = alpha, beta
        try:
            return self.description_adjustment(embedding)
        finally:
            self.alpha, self.beta = old_alpha, old_beta

    def _ordered_logits(self, head: MLPClassifier, embedding: np.ndarray) -> np.ndarray:
        probabilities = head.predict_proba(self._row(embedding))[0]
        by_class = {
            str(key): float(value) for key, value in zip(head.classes_, probabilities)
        }
        return np.asarray(
            [math.log(max(by_class[key], 1e-12)) for key in self.enabled_categories]
        )

    def _thresholds(self, values: Mapping[str, float]) -> dict[str, float]:
        if set(values) != set(self.enabled_categories):
            raise ValueError("category thresholds must match enabled categories")
        return {
            key: _probability(float(values[key]), f"threshold[{key}]")
            for key in self.enabled_categories
        }

    def _validated_vectors(self, value: DescriptionVectors) -> DescriptionVectors:
        if not isinstance(value, DescriptionVectors):
            raise TypeError("description vector value is invalid")
        core = self._vector(value.core)
        include = self._description_matrix(value.include, "include")
        exclude = self._description_matrix(value.exclude, "exclude")
        return DescriptionVectors(core=core, include=include, exclude=exclude)

    def _description_matrix(self, value: np.ndarray, field: str) -> np.ndarray:
        matrix = np.asarray(value, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.dimension or not len(matrix):
            raise ValueError(f"{field} vectors have invalid shape")
        if not np.isfinite(matrix).all():
            raise ValueError(f"{field} vectors must be finite")
        result = matrix.copy()
        result.setflags(write=False)
        return result

    def _matrix(self, value: np.ndarray) -> np.ndarray:
        matrix = np.asarray(value, dtype=np.float32)
        if (
            matrix.ndim != 2
            or matrix.shape[1] != self.dimension
            or not np.isfinite(matrix).all()
        ):
            raise ValueError("embedding matrix has invalid shape or values")
        return matrix

    def _vector(self, value: np.ndarray) -> np.ndarray:
        row = np.asarray(value, dtype=np.float32)
        if row.shape != (self.dimension,) or not np.isfinite(row).all():
            raise ValueError("description vector has invalid shape or values")
        result = row.copy()
        result.setflags(write=False)
        return result

    def _row(self, value: np.ndarray) -> np.ndarray:
        row = np.asarray(value, dtype=np.float32)
        if row.shape != (self.dimension,) or not np.isfinite(row).all():
            raise ValueError("embedding has invalid shape or values")
        return row.reshape(1, -1)

    @staticmethod
    def _new_head() -> MLPClassifier:
        return MLPClassifier(
            hidden_layer_sizes=(8,),
            solver="lbfgs",
            alpha=0.001,
            max_iter=1000,
            random_state=20260905,
        )

    def _require_category_head(self) -> MLPClassifier:
        if self._category_head is None:
            raise RuntimeError("classifier is not fitted")
        return self._category_head

    def _require_important_head(self) -> MLPClassifier:
        if self._important_head is None:
            raise RuntimeError("classifier is not fitted")
        return self._important_head


def _normalize(value: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(value))
    return value / norm if norm else np.zeros_like(value)


def _normalize_rows(value: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(value, axis=1, keepdims=True)
    return np.divide(value, norms, out=np.zeros_like(value), where=norms != 0)


def _required_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be canonical non-empty text")
    return value


def _probability(value: float, field: str) -> float:
    if not isinstance(value, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field} must be a finite float in [0, 1]")
    return value


def _box_weight(value: float, field: str) -> float:
    if not isinstance(value, float) or not math.isfinite(value) or not 0 <= value <= 2:
        raise ValueError(f"{field} must be a finite float in [0, 2]")
    return value


def _read_verified_artifact_frame(path: str | Path) -> tuple[bytes, str]:
    try:
        framed_artifact = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(
            "embedding classifier artifact is missing or unreadable"
        ) from exc
    if len(framed_artifact) < _ARTIFACT_HEADER.size:
        raise ValueError("invalid embedding classifier artifact framing")
    magic, frame_version, payload_length, expected_checksum = (
        _ARTIFACT_HEADER.unpack_from(framed_artifact)
    )
    if magic != ARTIFACT_FRAME_MAGIC or frame_version != ARTIFACT_FRAME_VERSION:
        raise ValueError("invalid embedding classifier artifact framing")
    if payload_length < 1 or payload_length > _MAX_ARTIFACT_PAYLOAD_BYTES:
        raise ValueError("embedding classifier artifact payload length is invalid")
    if len(framed_artifact) != _ARTIFACT_HEADER.size + payload_length:
        raise ValueError("embedding classifier artifact length mismatch")
    payload = framed_artifact[_ARTIFACT_HEADER.size :]
    actual_checksum = sha256(payload).digest()
    if not hmac.compare_digest(actual_checksum, expected_checksum):
        raise ValueError("artifact checksum mismatch")
    return payload, actual_checksum.hex()


def _read_safe_npz_payload(
    payload: bytes,
) -> tuple[dict[str, object], dict[str, np.ndarray]]:
    try:
        if (
            not payload.startswith(_ZIP_LOCAL_MAGIC)
            or len(payload) < 22
            or payload[-22:-18] != _ZIP_EMPTY_COMMENT_EOCD_MAGIC
            or payload[-2:] != b"\x00\x00"
        ):
            raise ValueError("embedding classifier NPZ framing is invalid")
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as zip_archive:
            members = zip_archive.infolist()
            member_names = [member.filename for member in members]
            if (
                not members
                or len(member_names) != len(set(member_names))
                or any(
                    member.is_dir()
                    or not member.filename.endswith(".npy")
                    or member.flag_bits & 0x1
                    or member.compress_type != zipfile.ZIP_STORED
                    for member in members
                )
                or sum(member.file_size for member in members)
                > _MAX_ARTIFACT_UNCOMPRESSED_BYTES
            ):
                raise ValueError("embedding classifier NPZ members are invalid")
        with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
            names = tuple(archive.files)
            if len(names) != len(set(names)) or "manifest" not in names:
                raise ValueError("embedding classifier archive entries are invalid")
            manifest_array = np.asarray(archive["manifest"])
            if (
                manifest_array.dtype != np.uint8
                or manifest_array.ndim != 1
                or not 1 <= manifest_array.size <= _MAX_MANIFEST_BYTES
            ):
                raise ValueError("embedding classifier manifest array is invalid")
            manifest = _strict_json_object(manifest_array.tobytes())
            arrays = {name: np.asarray(archive[name]).copy() for name in names}
    except (
        OSError,
        ValueError,
        KeyError,
        EOFError,
        UnicodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as exc:
        raise ValueError("invalid embedding classifier artifact archive") from exc
    return manifest, arrays


def _strict_json_object(encoded: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key is forbidden")
            result[key] = value
        return result

    value = json.loads(
        encoded.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError("artifact manifest must be an object")
    return value


def _serialize_mlp_head(
    head: MLPClassifier,
    *,
    prefix: str,
    expected_classes: Sequence[object],
    dimension: int,
) -> tuple[dict[str, object], dict[str, np.ndarray]]:
    classes = _validated_head_classes(head.classes_, expected_classes)
    hidden_units = 8
    output_width = 1 if len(classes) == 2 else len(classes)
    expected_coefficient_shapes = (
        (dimension, hidden_units),
        (hidden_units, output_width),
    )
    expected_intercept_shapes = ((hidden_units,), (output_width,))
    if (
        head.activation != "relu"
        or head.out_activation_ != ("logistic" if output_width == 1 else "softmax")
        or head.n_features_in_ != dimension
        or head.n_outputs_ != output_width
        or head.n_layers_ != 3
        or len(head.coefs_) != 2
        or len(head.intercepts_) != 2
    ):
        raise ValueError("MLP head state is incompatible with the safe artifact format")
    arrays: dict[str, np.ndarray] = {}
    for index, (coefficient, expected_shape) in enumerate(
        zip(head.coefs_, expected_coefficient_shapes)
    ):
        arrays[f"{prefix}_coef_{index}"] = _safe_float_array(
            coefficient,
            expected_dtype=np.float64,
            expected_shape=expected_shape,
            field=f"{prefix} coefficient {index}",
        )
    for index, (intercept, expected_shape) in enumerate(
        zip(head.intercepts_, expected_intercept_shapes)
    ):
        arrays[f"{prefix}_intercept_{index}"] = _safe_float_array(
            intercept,
            expected_dtype=np.float64,
            expected_shape=expected_shape,
            field=f"{prefix} intercept {index}",
        )
    return (
        {
            "class_type": "boolean"
            if all(type(item) is bool for item in expected_classes)
            else "category",
            "classes": list(classes),
            "activation": "relu",
            "out_activation": head.out_activation_,
            "n_features_in": dimension,
            "n_outputs": output_width,
            "n_layers": 3,
            "hidden_units": hidden_units,
            "n_iter": int(head.n_iter_),
            "t": int(head.t_),
            "loss": float(head.loss_),
        },
        arrays,
    )


def _load_mlp_head(
    value: object,
    arrays: Mapping[str, np.ndarray],
    *,
    prefix: str,
    dimension: int,
    expected_classes: Sequence[object],
) -> tuple[MLPClassifier, frozenset[str]]:
    _validate_exact_keys(
        value,
        {
            "class_type",
            "classes",
            "activation",
            "out_activation",
            "n_features_in",
            "n_outputs",
            "n_layers",
            "hidden_units",
            "n_iter",
            "t",
            "loss",
        },
        f"{prefix} MLP manifest",
    )
    assert isinstance(value, Mapping)
    expected_class_type = (
        "boolean"
        if all(type(item) is bool for item in expected_classes)
        else "category"
    )
    if value["class_type"] != expected_class_type:
        raise ValueError(f"{prefix} MLP class type is invalid")
    classes = _validated_head_classes(value["classes"], expected_classes)
    hidden_units = _manifest_positive_int(value["hidden_units"], "hidden_units")
    if hidden_units != 8:
        raise ValueError(f"{prefix} MLP hidden width is unsupported")
    output_width = 1 if len(classes) == 2 else len(classes)
    if (
        value["activation"] != "relu"
        or value["out_activation"] != ("logistic" if output_width == 1 else "softmax")
        or _manifest_positive_int(value["n_features_in"], "n_features_in") != dimension
        or _manifest_positive_int(value["n_outputs"], "n_outputs") != output_width
        or _manifest_positive_int(value["n_layers"], "n_layers") != 3
    ):
        raise ValueError(f"{prefix} MLP topology is invalid")
    coefficient_shapes = ((dimension, hidden_units), (hidden_units, output_width))
    intercept_shapes = ((hidden_units,), (output_width,))
    keys: set[str] = set()
    coefficients: list[np.ndarray] = []
    intercepts: list[np.ndarray] = []
    for index, shape in enumerate(coefficient_shapes):
        key = f"{prefix}_coef_{index}"
        keys.add(key)
        coefficients.append(
            _artifact_float_array(
                arrays, key, expected_dtype=np.float64, expected_shape=shape
            )
        )
    for index, shape in enumerate(intercept_shapes):
        key = f"{prefix}_intercept_{index}"
        keys.add(key)
        intercepts.append(
            _artifact_float_array(
                arrays, key, expected_dtype=np.float64, expected_shape=shape
            )
        )
    n_iter = _manifest_positive_int(value["n_iter"], "n_iter")
    t_value = _manifest_non_negative_int(value["t"], "t")
    loss = _manifest_finite_float(value["loss"], "loss")
    head = DescriptionAwareEmailClassifier._new_head()
    head.classes_ = np.asarray(
        classes, dtype=np.bool_ if expected_class_type == "boolean" else np.str_
    )
    head.n_features_in_ = dimension
    head.n_outputs_ = output_width
    head.n_layers_ = 3
    head.out_activation_ = str(value["out_activation"])
    head.coefs_ = coefficients
    head.intercepts_ = intercepts
    head.n_iter_ = n_iter
    head.t_ = t_value
    head.loss_ = loss
    return head, frozenset(keys)


def _validated_head_classes(
    value: object, expected_classes: Sequence[object]
) -> tuple[object, ...]:
    if isinstance(value, np.ndarray):
        if value.ndim != 1:
            raise ValueError("MLP classes must be one-dimensional")
        raw = value.tolist()
    elif isinstance(value, list | tuple):
        raw = list(value)
    else:
        raise ValueError("MLP classes must be a list")
    expects_boolean = all(type(item) is bool for item in expected_classes)
    if expects_boolean:
        if any(type(item) is not bool for item in raw) or raw != [False, True]:
            raise ValueError("important MLP classes are invalid")
    else:
        if any(type(item) is not str for item in raw):
            raise ValueError("category MLP classes are invalid")
        if len(raw) != len(set(raw)) or set(raw) != set(expected_classes):
            raise ValueError("category MLP classes do not match enabled categories")
    return tuple(raw)


def _load_descriptions(
    value: object,
    arrays: Mapping[str, np.ndarray],
    *,
    enabled_categories: tuple[str, ...],
    dimension: int,
) -> tuple[
    dict[str, CategoryDescription],
    dict[str, DescriptionVectors],
    frozenset[str],
]:
    if not isinstance(value, list) or len(value) != len(enabled_categories):
        raise ValueError("artifact descriptions must match enabled categories")
    descriptions: dict[str, CategoryDescription] = {}
    vectors: dict[str, DescriptionVectors] = {}
    keys: set[str] = set()
    for index, (category, entry) in enumerate(zip(enabled_categories, value)):
        _validate_exact_keys(
            entry,
            {"category", "core", "include", "exclude", "version"},
            f"description {index}",
        )
        assert isinstance(entry, Mapping)
        if entry["category"] != category:
            raise ValueError("artifact description order does not match categories")
        include = _manifest_text_list(entry["include"], f"description {index} include")
        exclude = _manifest_text_list(entry["exclude"], f"description {index} exclude")
        description = CategoryDescription(
            core=_manifest_text(entry["core"], f"description {index} core"),
            include=include,
            exclude=exclude,
            version=_manifest_text(entry["version"], f"description {index} version"),
        )
        core_key = f"description_{index}_core"
        include_key = f"description_{index}_include"
        exclude_key = f"description_{index}_exclude"
        keys.update((core_key, include_key, exclude_key))
        descriptions[category] = description
        vectors[category] = DescriptionVectors(
            core=_artifact_float_array(
                arrays,
                core_key,
                expected_dtype=np.float32,
                expected_shape=(dimension,),
            ),
            include=_artifact_float_array(
                arrays,
                include_key,
                expected_dtype=np.float32,
                expected_shape=(len(include), dimension),
            ),
            exclude=_artifact_float_array(
                arrays,
                exclude_key,
                expected_dtype=np.float32,
                expected_shape=(len(exclude), dimension),
            ),
        )
    return descriptions, vectors, frozenset(keys)


def _safe_float_array(
    value: object,
    *,
    expected_dtype: object,
    expected_shape: tuple[int, ...],
    field: str,
) -> np.ndarray:
    array = np.asarray(value)
    if (
        array.dtype != np.dtype(expected_dtype)
        or array.shape != expected_shape
        or not np.isfinite(array).all()
    ):
        raise ValueError(f"{field} array is invalid")
    return array.copy()


def _artifact_float_array(
    arrays: Mapping[str, np.ndarray],
    key: str,
    *,
    expected_dtype: object,
    expected_shape: tuple[int, ...],
) -> np.ndarray:
    if key not in arrays:
        raise ValueError(f"artifact array is missing: {key}")
    return _safe_float_array(
        arrays[key],
        expected_dtype=expected_dtype,
        expected_shape=expected_shape,
        field=key,
    )


def _validate_exact_keys(value: object, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"{field} must be an object")
    if set(value) != expected or len(value) != len(expected):
        raise ValueError(f"{field} fields are invalid")


def _validated_manifest_categories(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("enabled categories must be a list")
    categories = tuple(validate_email_category_key(item) for item in value)
    if len(categories) != len(set(categories)):
        raise ValueError("enabled categories must be unique")
    return categories


def _validated_manifest_thresholds(
    value: object, categories: tuple[str, ...]
) -> dict[str, float]:
    _validate_exact_keys(value, set(categories), "category thresholds")
    assert isinstance(value, Mapping)
    return {
        category: _manifest_probability(value[category], f"threshold[{category}]")
        for category in categories
    }


def _manifest_text_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    return tuple(_manifest_text(item, field) for item in value)


def _manifest_text(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be canonical non-empty text")
    return value


def _manifest_positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _manifest_non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _manifest_finite_float(value: object, field: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite float")
    return value


def _manifest_probability(value: object, field: str) -> float:
    result = _manifest_finite_float(value, field)
    return _probability(result, field)


def _manifest_box_weight(value: object, field: str) -> float:
    result = _manifest_finite_float(value, field)
    return _box_weight(result, field)


def _validated_tuning_folds(
    folds: Sequence[tuple[np.ndarray, np.ndarray]], *, sample_count: int
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    if isinstance(folds, (str, bytes)) or not isinstance(folds, Sequence) or not folds:
        raise ValueError("tuning folds must be a non-empty sequence")
    validated: list[tuple[np.ndarray, np.ndarray]] = []
    for position, fold in enumerate(folds):
        if not isinstance(fold, Sequence) or isinstance(fold, (str, bytes)):
            raise ValueError(
                f"tuning fold {position} must contain train and validation indices"
            )
        if len(fold) != 2:
            raise ValueError(
                f"tuning fold {position} must contain train and validation indices"
            )
        train_values = _validated_fold_index_set(
            fold[0],
            sample_count=sample_count,
            field=f"tuning fold {position} train",
        )
        validation_values = _validated_fold_index_set(
            fold[1],
            sample_count=sample_count,
            field=f"tuning fold {position} validation",
        )
        if set(train_values).intersection(validation_values):
            raise ValueError(f"tuning fold {position} train and validation overlap")
        validated.append(
            (
                np.asarray(train_values, dtype=np.int64),
                np.asarray(validation_values, dtype=np.int64),
            )
        )
    return tuple(validated)


def _validated_fold_index_set(
    values: object, *, sample_count: int, field: str
) -> tuple[int, ...]:
    if isinstance(values, np.ndarray):
        if values.ndim != 1:
            raise ValueError(f"{field} indices must be one-dimensional")
        items = values.tolist()
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        items = list(values)
    else:
        raise ValueError(f"{field} indices must be a sequence")
    if not items:
        raise ValueError(f"{field} indices must be non-empty")
    result: list[int] = []
    seen: set[int] = set()
    for item in items:
        if type(item) is not int:
            raise ValueError(f"{field} indices must be integers")
        if item < 0 or item >= sample_count:
            raise ValueError(f"{field} index is outside the valid range")
        if item in seen:
            raise ValueError(f"{field} indices contain a duplicate")
        seen.add(item)
        result.append(item)
    return tuple(result)
