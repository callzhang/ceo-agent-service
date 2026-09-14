"""Small supervised fastText adapter with the same prediction contract as TF-IDF."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Sequence

from app.email_classifier_model import EmailModelPrediction


class FastTextEmailClassifier:
    """Persist a fastText softmax model without changing the email runtime API."""

    FEATURE_VERSION = "fasttext-word-ngram-v1"

    def __init__(self, *, model_version: str = "unversioned") -> None:
        self.model_version = model_version
        self._model = None

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "FastTextEmailClassifier":
        if not texts or len(texts) != len(labels) or len(set(labels)) < 2:
            raise ValueError("fastText needs aligned examples from at least two categories")
        import fasttext

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt") as handle:
            for text, label in zip(texts, labels, strict=True):
                handle.write(f"__label__{label} {text.replace(chr(10), ' ')}\n")
            handle.flush()
            self._model = fasttext.train_supervised(
                input=handle.name, loss="softmax", dim=32, bucket=50_000,
                minCount=1, thread=1, lr=0.1, epoch=50, wordNgrams=2, verbose=0,
            )
        return self

    def predict(self, text: str) -> EmailModelPrediction:
        if self._model is None:
            raise RuntimeError("classifier is not fitted")
        labels, probabilities = self._model.predict(text, k=len(self.class_labels()))
        values = {
            label.removeprefix("__label__"): float(probability)
            for label, probability in zip(labels, probabilities, strict=True)
        }
        ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)
        label, probability = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else probability
        return EmailModelPrediction(label, probability, probability - second, values, self.model_version)

    def class_labels(self) -> tuple[str, ...]:
        if self._model is None:
            raise RuntimeError("classifier is not fitted")
        return tuple(label.removeprefix("__label__") for label in self._model.get_labels())

    def save(self, path: str | Path) -> None:
        if self._model is None:
            raise RuntimeError("classifier is not fitted")
        self._model.save_model(str(path))

    @classmethod
    def load(cls, path: str | Path) -> "FastTextEmailClassifier":
        import fasttext

        result = cls()
        result._model = fasttext.load_model(str(path))
        return result
