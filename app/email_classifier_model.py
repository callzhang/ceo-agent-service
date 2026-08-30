"""CPU-only TF-IDF + Logistic email classifier used by the readonly scan seam."""

from __future__ import annotations

import hashlib
import os
import pickle
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from app.email_classifier_contracts import EmailCategory
from app.jieba_loader import jieba_lcut


EMAIL_CATEGORIES = tuple(category.value for category in EmailCategory)


def _clean(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"https?://\S+", " URL ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\b", " EMAIL ", text)
    text = re.sub(
        r"\b(?:sub|ch|pi|sk|tok|token|sess|session|order|invoice)[_-]?[A-Za-z0-9_-]{6,}\b",
        " TOKEN ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<!\d)\d{4,}(?!\d)", " NUMBER ", text)
    return re.sub(r"\s+", " ", text).strip()


def _recipients(message: Mapping[str, object], field: str) -> str:
    values = message.get(field) or []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ""
    return " ".join(
        _clean(item.get("email") or item.get("name"))
        for item in values
        if isinstance(item, Mapping)
    )


def email_message_to_text(message: Mapping[str, object]) -> str:
    """Create redacted model features without retaining raw message content."""
    sender = message.get("from") or {}
    raw_sender = str(sender.get("email") or "") if isinstance(sender, Mapping) else ""
    sender_domain = raw_sender.rsplit("@", 1)[-1].lower() if "@" in raw_sender else ""
    exact_sender = ""
    if raw_sender:
        exact_sender = hashlib.sha256(raw_sender.strip().lower().encode("utf-8")).hexdigest()[:16]
    subject = _clean(message.get("subject"))
    body = _clean(message.get("markdownBody") or message.get("textBody"))
    if len(body) > 12000:
        body = body[:8000] + " " + body[-4000:]
    return " ".join(
        (
            f"__from_domain__{sender_domain}",
            f"__from_hash__{exact_sender}",
            f"__to__{_recipients(message, 'toRecipients')}",
            f"__cc__{_recipients(message, 'ccRecipients')}",
            f"__subject__{' '.join(jieba_lcut(subject))}",
            " ".join(jieba_lcut(body)),
        )
    )


@dataclass(frozen=True)
class EmailModelPrediction:
    label: str
    probability: float
    margin: float
    probabilities: Mapping[str, float]
    model_version: str


class CpuTfidfLogisticClassifier:
    """Sparse word-unigram Logistic model with a small, serializable footprint."""

    FORMAT_VERSION = 1
    FEATURE_VERSION = "jieba-tfidf-word-unigram-v1"

    def __init__(self, *, c: float = 0.25, model_version: str = "unversioned"):
        if c <= 0:
            raise ValueError("c must be positive")
        self.c = c
        self.model_version = model_version.strip() or "unversioned"
        self._vectorizer: TfidfVectorizer | None = None
        self._classifier: LogisticRegression | None = None

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "CpuTfidfLogisticClassifier":
        if len(texts) != len(labels) or not texts:
            raise ValueError("texts and labels must be non-empty and have equal length")
        unknown = sorted(set(labels) - set(EMAIL_CATEGORIES))
        if unknown:
            raise ValueError(f"unknown category: {unknown[0]}")
        if len(set(labels)) < 2:
            raise ValueError("at least two categories are required")
        vectorizer = TfidfVectorizer(
            token_pattern=r"\S+", ngram_range=(1, 1), sublinear_tf=True
        )
        classifier = LogisticRegression(
            C=self.c,
            class_weight="balanced",
            max_iter=2000,
            random_state=42,
        )
        classifier.fit(vectorizer.fit_transform(texts), labels)
        self._vectorizer = vectorizer
        self._classifier = classifier
        return self

    def fit_messages(
        self, messages: Sequence[Mapping[str, object]], labels: Sequence[str]
    ) -> "CpuTfidfLogisticClassifier":
        return self.fit([email_message_to_text(message) for message in messages], labels)

    def predict(self, text: str) -> EmailModelPrediction:
        if self._vectorizer is None or self._classifier is None:
            raise RuntimeError("classifier is not fitted")
        probabilities = self._classifier.predict_proba(
            self._vectorizer.transform([text])
        )[0]
        order = probabilities.argsort()
        top = int(order[-1])
        second = int(order[-2]) if len(order) > 1 else top
        values = {
            str(label): float(probability)
            for label, probability in zip(self._classifier.classes_, probabilities)
        }
        return EmailModelPrediction(
            label=str(self._classifier.classes_[top]),
            probability=float(probabilities[top]),
            margin=float(probabilities[top] - probabilities[second]),
            probabilities=values,
            model_version=self.model_version,
        )

    def predict_message(self, message: Mapping[str, object]) -> EmailModelPrediction:
        return self.predict(email_message_to_text(message))

    def class_labels(self) -> tuple[str, ...]:
        if self._classifier is None:
            raise RuntimeError("classifier is not fitted")
        return tuple(str(label) for label in self._classifier.classes_)

    def save(self, path: str | Path) -> None:
        if self._vectorizer is None or self._classifier is None:
            raise RuntimeError("classifier is not fitted")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                pickle.dump(
                    {
                        "format_version": self.FORMAT_VERSION,
                        "feature_version": self.FEATURE_VERSION,
                        "c": self.c,
                        "model_version": self.model_version,
                        "vectorizer": self._vectorizer,
                        "classifier": self._classifier,
                    },
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> "CpuTfidfLogisticClassifier":
        with Path(path).open("rb") as handle:
            payload: Any = pickle.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("invalid email classifier payload")
        if payload.get("format_version") != cls.FORMAT_VERSION:
            raise ValueError("unsupported email classifier format version")
        if payload.get("feature_version") != cls.FEATURE_VERSION:
            raise ValueError("unsupported email classifier feature version")
        result = cls(c=float(payload["c"]), model_version=str(payload["model_version"]))
        result._vectorizer = payload["vectorizer"]
        result._classifier = payload["classifier"]
        if not isinstance(result._vectorizer, TfidfVectorizer) or not isinstance(
            result._classifier, LogisticRegression
        ):
            raise ValueError("invalid email classifier model objects")
        return result
