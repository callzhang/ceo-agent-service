"""Atomic float32 cache for version-bound email and description embeddings."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class EmbeddingCacheKey:
    normalized_input_hash: str
    input_schema_version: str
    embedding_model_id: str
    embedding_revision: str

    def __post_init__(self) -> None:
        if len(self.normalized_input_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.normalized_input_hash
        ):
            raise ValueError("normalized_input_hash must be lowercase SHA-256")
        for field in (
            "input_schema_version",
            "embedding_model_id",
            "embedding_revision",
        ):
            value = getattr(self, field)
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"{field} must be canonical non-empty text")

    @classmethod
    def for_text(
        cls,
        *,
        normalized_text: str,
        input_schema_version: str,
        embedding_model_id: str,
        embedding_revision: str,
    ) -> "EmbeddingCacheKey":
        return cls(
            normalized_input_hash=sha256(normalized_text.encode("utf-8")).hexdigest(),
            input_schema_version=input_schema_version,
            embedding_model_id=embedding_model_id,
            embedding_revision=embedding_revision,
        )

    @classmethod
    def for_description(
        cls,
        *,
        text: str,
        description_version: str,
        input_schema_version: str,
        embedding_model_id: str,
        embedding_revision: str,
    ) -> "EmbeddingCacheKey":
        versioned = json.dumps(
            {"description_version": description_version, "text": text},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return cls.for_text(
            normalized_text=versioned,
            input_schema_version=input_schema_version,
            embedding_model_id=embedding_model_id,
            embedding_revision=embedding_revision,
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class EmbeddingCache:
    def __init__(self, model_registry_root: str | Path, *, dimension: int) -> None:
        if (
            isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or dimension < 1
        ):
            raise ValueError("dimension must be a positive integer")
        self.root = Path(model_registry_root) / "embedding-cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.dimension = dimension

    def path_for(self, key: EmbeddingCacheKey) -> Path:
        digest = key.digest
        return self.root / digest[:2] / f"{digest}.npz"

    def get(self, key: EmbeddingCacheKey) -> np.ndarray | None:
        path = self.path_for(key)
        try:
            with np.load(path, allow_pickle=False) as archive:
                vector = np.asarray(archive["vector"])
                metadata_bytes = np.asarray(
                    archive["metadata"], dtype=np.uint8
                ).tobytes()
            metadata = json.loads(metadata_bytes.decode("utf-8"))
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
        ):
            return None
        if metadata != {**asdict(key), "dimension": self.dimension}:
            return None
        if (
            vector.dtype != np.float32
            or vector.shape != (self.dimension,)
            or not np.isfinite(vector).all()
        ):
            return None
        result = vector.copy()
        result.setflags(write=False)
        return result

    def put(self, key: EmbeddingCacheKey, vector: np.ndarray) -> Path:
        value = np.asarray(vector, dtype=np.float32)
        if value.shape != (self.dimension,) or not np.isfinite(value).all():
            raise ValueError("vector has invalid dimension or values")
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata = json.dumps(
            {**asdict(key), "dimension": self.dimension},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                np.savez(
                    handle,
                    vector=value,
                    metadata=np.frombuffer(metadata, dtype=np.uint8),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
            return destination
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
