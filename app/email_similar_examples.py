"""The owner's own labels most like a message, for the Agent that classifies it.

A zero-shot Agent does not know how the owner files mail (on 888 confirmed
labels a local LLM alone got 65%; shown the eight most similar messages the
owner had labelled it got 89%, and 77% for a sender it had not seen). So the
classification prompt carries a few of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

EXAMPLE_COUNT = 8
SNIPPET_CHARS = 250
QUERY_CHARS = 3000


@dataclass(frozen=True)
class _Index:
    stamp: str
    examples: tuple[Mapping[str, str], ...]
    vectorizer: object
    matrix: object


_LOCK = Lock()
_INDEXES: dict[Path, _Index] = {}


def _document(sender: str, subject: str, text: str) -> str:
    return f"{sender}\n{subject}\n{text[:QUERY_CHARS]}"


def _index(store: object) -> _Index | None:
    examples, stamp = store.owner_labelled_examples()  # type: ignore[attr-defined]
    key = Path(getattr(store, "path"))
    with _LOCK:
        cached = _INDEXES.get(key)
        if cached is not None and cached.stamp == stamp:
            return cached
    if not examples:
        return None
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        min_df=1,
        sublinear_tf=True,
        max_features=200000,
    )
    matrix = vectorizer.fit_transform(
        [_document(item["sender"], item["subject"], item["text"]) for item in examples]
    )
    built = _Index(stamp, tuple(examples), vectorizer, matrix)
    with _LOCK:
        _INDEXES[key] = built
    return built


def similar_owner_examples(
    store: object,
    *,
    stable_message_identity: str,
    sender: str,
    subject: str,
    text: str,
    allowed_category_keys: Sequence[str],
    count: int = EXAMPLE_COUNT,
) -> list[dict[str, str]]:
    """The owner-labelled messages most like this one, most similar first.

    The message itself is never among them, and only categories that still
    exist are offered.
    """

    index = _index(store)
    if index is None:
        return []
    scores = (
        index.vectorizer.transform([_document(sender, subject, text)])  # type: ignore[attr-defined]
        @ index.matrix.T  # type: ignore[attr-defined]
    ).toarray()[0]
    allowed = set(allowed_category_keys)
    ranked = sorted(range(len(scores)), key=lambda position: -scores[position])
    chosen: list[dict[str, str]] = []
    for position in ranked:
        if scores[position] <= 0 or len(chosen) >= count:
            break
        example = index.examples[position]
        if example["identity"] == stable_message_identity:
            continue
        if example["category"] not in allowed:
            continue
        chosen.append(
            {
                "sender": example["sender"],
                "subject": example["subject"],
                "start": " ".join(example["text"][:SNIPPET_CHARS].split()),
                "category": example["category"],
            }
        )
    return chosen


def clear_cache() -> None:
    with _LOCK:
        _INDEXES.clear()
