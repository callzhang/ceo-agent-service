"""Transient exact-version source binding and native body read for Business Signals.

This module does not persist original text or select a replacement Store model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from app import native_trajectory
from app.task_source_documents import (
    source_contains_quote,
    source_document_key,
    source_is_observed,
)

if TYPE_CHECKING:
    from app.business_console_source_reader import ConsoleLookup
    from app.task_semantic_models import BusinessTaskSignal
    from app.task_semantic_service import SourceSignal
    from app.store import AutoReplyStore
    import sqlite3


@dataclass(frozen=True)
class SourceVersionBinding:
    source_type: str
    source_ref: str
    source_time: str
    conversation_id: str
    author_user_id: str
    author_name: str
    author_kind: str
    identity_key: str
    body_sha256: str
    body_utf8_bytes: int
    native_provenance_json: str | None = None
    provider_locator_json: str | None = None

    @classmethod
    def from_signal(
        cls,
        signal: BusinessTaskSignal | SourceSignal,
        *,
        native_provenance: Mapping[str, object] | None = None,
        provider_locator: Mapping[str, object] | None = None,
    ) -> SourceVersionBinding:
        if native_provenance is not None and provider_locator is not None:
            raise ValueError("one exact source locator is permitted")
        body = signal.evidence_text
        encoded = body.encode("utf-8")
        return cls(
            source_type=signal.source_type,
            source_ref=signal.source_ref,
            source_time=signal.source_time,
            conversation_id=signal.conversation_id,
            author_user_id=signal.author_user_id,
            author_name=signal.author_name,
            author_kind=signal.author_kind,
            identity_key=source_document_key(
                source_type=signal.source_type,
                source_ref=signal.source_ref,
                source_time=signal.source_time,
                conversation_id=signal.conversation_id,
                author_user_id=signal.author_user_id,
                author_name=signal.author_name,
                author_kind=signal.author_kind,
                evidence_text=body,
            ),
            body_sha256=hashlib.sha256(encoded).hexdigest(),
            body_utf8_bytes=len(encoded),
            native_provenance_json=(
                json.dumps(native_provenance, ensure_ascii=False, sort_keys=True)
                if native_provenance is not None else None
            ),
            provider_locator_json=(
                json.dumps(provider_locator, ensure_ascii=False, sort_keys=True)
                if provider_locator is not None else None
            ),
        )

    def matches_body(self, body: str) -> bool:
        encoded = body.encode("utf-8")
        return (
            len(encoded) == self.body_utf8_bytes
            and hashlib.sha256(encoded).hexdigest() == self.body_sha256
            and source_document_key(
                source_type=self.source_type,
                source_ref=self.source_ref,
                source_time=self.source_time,
                conversation_id=self.conversation_id,
                author_user_id=self.author_user_id,
                author_name=self.author_name,
                author_kind=self.author_kind,
                evidence_text=body,
            ) == self.identity_key
        )


@dataclass(frozen=True)
class BusinessTaskSignalFact:
    """Current Signal metadata, independent of a recoverable original body."""

    id: int
    source: SourceVersionBinding
    conversation_title: str
    context_json: str
    dedupe_key: str
    created_at: str

    @property
    def source_type(self) -> str:
        return self.source.source_type

    @property
    def source_ref(self) -> str:
        return self.source.source_ref

    @property
    def source_time(self) -> str:
        return self.source.source_time

    @property
    def conversation_id(self) -> str:
        return self.source.conversation_id

    @property
    def author_user_id(self) -> str:
        return self.source.author_user_id

    @property
    def author_name(self) -> str:
        return self.source.author_name

    @property
    def author_kind(self):
        from app.task_semantic_models import BusinessActorKind

        return BusinessActorKind(self.source.author_kind)

    @classmethod
    def from_signal(
        cls, signal: BusinessTaskSignal, *, binding: SourceVersionBinding
    ) -> BusinessTaskSignalFact:
        expected = SourceVersionBinding.from_signal(signal)
        if (
            binding.source_type != expected.source_type
            or binding.source_ref != expected.source_ref
            or binding.source_time != expected.source_time
            or binding.conversation_id != expected.conversation_id
            or binding.author_user_id != expected.author_user_id
            or binding.author_name != expected.author_name
            or binding.author_kind != expected.author_kind
            or binding.identity_key != expected.identity_key
            or binding.body_sha256 != expected.body_sha256
            or binding.body_utf8_bytes != expected.body_utf8_bytes
        ):
            raise ValueError("source binding does not match Signal")
        return cls(
            id=signal.id,
            source=binding,
            conversation_title=signal.conversation_title,
            context_json=signal.context_json,
            dedupe_key=signal.dedupe_key,
            created_at=signal.created_at,
        )


class SourceUnavailableReason(StrEnum):
    MISSING_LOCATOR = "missing_locator"
    SOURCE_MISSING = "source_missing"
    SOURCE_INCOMPLETE = "source_incomplete"
    INVALID_LOCATOR = "invalid_locator"
    VERSION_MISMATCH = "version_mismatch"
    SOURCE_REF_MISMATCH = "source_ref_mismatch"
    QUOTE_MISSING = "quote_missing"
    REFERENCE_ONLY = "reference_only"
    READER_UNAVAILABLE = "reader_unavailable"
    BINDING_CONFLICT = "binding_conflict"


@dataclass(frozen=True)
class AvailableSource:
    binding: SourceVersionBinding
    body: str


@dataclass(frozen=True)
class UnavailableSource:
    binding: SourceVersionBinding
    reason: SourceUnavailableReason


type SourceReadResult = AvailableSource | UnavailableSource


class PreparedSources:
    """Historical source facts and once-per-version original reads for one request."""

    def __init__(self, reader: RequestSourceReader) -> None:
        self.reader = reader
        self.facts: dict[int, BusinessTaskSignalFact] = {}
        self.readings: dict[str, SourceReadResult] = {}
        self._conflicted_keys: set[str] = set()
        self.task_evidence_links: dict[int, tuple[frozenset[int], tuple[tuple[int, str], ...]]] = {}
        self.attention_cards: dict[int, tuple[object, tuple[int, ...]] | None] = {}
        self.project_evidence_links: dict[int, frozenset[int]] = {}

    @classmethod
    def load(
        cls, store: AutoReplyStore, signal_ids: Iterable[int],
        *, reader: RequestSourceReader | None = None,
    ) -> PreparedSources:
        prepared = cls(reader or store.business_source_reader())
        for signal_id in set(signal_ids):
            fact = store.get_business_task_signal(signal_id)
            if fact is not None:
                prepared.add(fact)
        return prepared

    def add(self, fact: BusinessTaskSignalFact) -> None:
        previous = self.readings.get(fact.source.identity_key)
        if previous is not None and previous.binding != fact.source:
            self._conflicted_keys.add(fact.source.identity_key)
        self.facts[fact.id] = fact
        self.readings.setdefault(fact.source.identity_key, self.reader.resolve(fact.source))

    def reading_for(self, fact: BusinessTaskSignalFact) -> SourceReadResult:
        reading = self.readings.get(fact.source.identity_key)
        if (fact.source.identity_key in self._conflicted_keys or reading is None
                or reading.binding != fact.source):
            return UnavailableSource(fact.source, SourceUnavailableReason.BINDING_CONFLICT)
        return reading

    def add_current(self, fact: BusinessTaskSignalFact, body: str) -> None:
        # Validate the transient shape, then compare only body identity checks;
        # exact native/provider coordinates are retained from the persisted fact.
        _source_input_from_fact(fact, body)
        if not fact.source.matches_body(body):
            raise ValueError("current source body does not match persisted Signal")
        previous = self.readings.get(fact.source.identity_key)
        if previous is not None and previous.binding != fact.source:
            self._conflicted_keys.add(fact.source.identity_key)
        self.facts[fact.id] = fact
        self.readings[fact.source.identity_key] = AvailableSource(fact.source, body)

    def require_fact(
        self, store: AutoReplyStore, signal_id: int, *, db: sqlite3.Connection,
    ) -> BusinessTaskSignalFact:
        expected = self.facts.get(signal_id)
        actual = store.get_business_task_signal_in_transaction(signal_id=signal_id, _db=db)
        if expected is None or actual is None or actual != expected:
            raise ValueError("prepared evidence Signal changed or is missing")
        return actual

    def require_body(
        self, store: AutoReplyStore, signal_id: int, *, db: sqlite3.Connection,
    ) -> tuple[BusinessTaskSignalFact, str]:
        actual = self.require_fact(store, signal_id, db=db)
        reading = self.reading_for(actual)
        if not isinstance(reading, AvailableSource) or reading.binding != actual.source:
            raise ValueError("original evidence source is unavailable")
        return actual, reading.body


def _source_input_from_fact(fact: BusinessTaskSignalFact, body: str):
    from app.task_semantic_models import BusinessTaskSignal

    return BusinessTaskSignal(
        id=fact.id,
        source_type=fact.source_type, source_ref=fact.source_ref,
        source_time=fact.source_time, conversation_id=fact.conversation_id,
        conversation_title=fact.conversation_title,
        author_user_id=fact.author_user_id, author_name=fact.author_name,
        author_kind=fact.author_kind, evidence_text=body,
        context_json=fact.context_json, dedupe_key=fact.dedupe_key,
        created_at=fact.created_at,
    )


def source_view(fact: BusinessTaskSignalFact, reading: SourceReadResult) -> dict[str, object]:
    """Public Signal fields with an honest original availability result."""
    if reading.binding != fact.source:
        reading = UnavailableSource(fact.source, SourceUnavailableReason.BINDING_CONFLICT)
    available = isinstance(reading, AvailableSource)
    return {
        "id": fact.id, "source_type": fact.source_type,
        "source_ref": fact.source_ref, "source_time": fact.source_time,
        "conversation_id": fact.conversation_id,
        "conversation_title": fact.conversation_title,
        "author_user_id": fact.author_user_id,
        "author_name": fact.author_name,
        "author_kind": fact.author_kind.value,
        "context_json": fact.context_json, "dedupe_key": fact.dedupe_key,
        "created_at": fact.created_at,
        "evidence_text": reading.body if available else None,
        "source_available": available,
        "source_unavailable_reason": None if available else reading.reason.value,
    }


class InvalidSerializedLocator(ValueError):
    """A stored locator is not a JSON string."""


def _parse_locator_json(value: object) -> object:
    if not isinstance(value, str):
        raise InvalidSerializedLocator("source locator is not serialized JSON")
    return json.loads(value)


class RequestSourceReader:
    """Resolve each exact binding once; callers own and discard this request object."""

    def __init__(self, *, provider_reader: Any = None, console_lookup: ConsoleLookup | None = None) -> None:
        self._readings: dict[str, tuple[SourceVersionBinding, SourceReadResult]] = {}
        self._minutes_page_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._provider_reader = provider_reader
        self._console_lookup = console_lookup

    def resolve(
        self,
        binding: SourceVersionBinding,
        *,
        source_ref: str | None = None,
        quote: str | None = None,
    ) -> SourceReadResult:
        entry = self._readings.get(binding.identity_key)
        if entry is not None and entry[0] != binding:
            return UnavailableSource(binding, SourceUnavailableReason.BINDING_CONFLICT)
        if entry is None:
            reading = self._read(binding)
            self._readings[binding.identity_key] = (binding, reading)
        else:
            reading = entry[1]
        if isinstance(reading, UnavailableSource):
            return reading
        if source_ref is not None and source_ref != binding.source_ref:
            return UnavailableSource(binding, SourceUnavailableReason.SOURCE_REF_MISMATCH)
        if quote is not None and not source_contains_quote(reading.body, quote):
            return UnavailableSource(binding, SourceUnavailableReason.QUOTE_MISSING)
        return reading

    def _read(self, binding: SourceVersionBinding) -> SourceReadResult:
        if not source_is_observed(binding.source_type):
            return UnavailableSource(binding, SourceUnavailableReason.REFERENCE_ONLY)
        if binding.native_provenance_json is None:
            if binding.provider_locator_json is None:
                if binding.source_type != "console" or self._console_lookup is None:
                    return UnavailableSource(binding, SourceUnavailableReason.MISSING_LOCATOR)
                from app.business_console_source_reader import (
                    ConsoleSourceIncomplete, ConsoleSourceMissing,
                    ConsoleSourceVersionChanged, read_console_source_body,
                )

                try:
                    body = read_console_source_body(binding, self._console_lookup)
                except ConsoleSourceMissing:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_MISSING)
                except ConsoleSourceIncomplete:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_INCOMPLETE)
                except ConsoleSourceVersionChanged:
                    return UnavailableSource(binding, SourceUnavailableReason.VERSION_MISMATCH)
            elif self._provider_reader is None:
                return UnavailableSource(binding, SourceUnavailableReason.READER_UNAVAILABLE)
            elif binding.source_type == "ai_minutes":
                from app.business_minutes_source_reader import (
                    MinutesInvalidLocator,
                    MinutesSourceIncomplete,
                    MinutesSourceUnavailable,
                    read_minutes_source_body,
                )

                try:
                    body = read_minutes_source_body(
                        _parse_locator_json(binding.provider_locator_json),
                        source_ref=binding.source_ref,
                        dws=self._provider_reader,
                        page_cache=self._minutes_page_cache,
                    )
                except MinutesSourceUnavailable:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_MISSING)
                except MinutesSourceIncomplete:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_INCOMPLETE)
                except (json.JSONDecodeError, InvalidSerializedLocator, MinutesInvalidLocator):
                    return UnavailableSource(binding, SourceUnavailableReason.INVALID_LOCATOR)
            else:
                from app.business_weekly_source_reader import (
                    WEEKLY_SOURCE_TYPES,
                    WeeklyInvalidLocator,
                    WeeklySourceIncomplete,
                    WeeklySourceUnavailable,
                    WeeklySourceVersionChanged,
                    read_weekly_source_body,
                )

                if binding.source_type not in WEEKLY_SOURCE_TYPES:
                    return UnavailableSource(binding, SourceUnavailableReason.READER_UNAVAILABLE)
                try:
                    body = read_weekly_source_body(
                        _parse_locator_json(binding.provider_locator_json),
                        source_type=binding.source_type,
                        source_ref=binding.source_ref,
                        dws=self._provider_reader,
                    )
                except WeeklySourceUnavailable:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_MISSING)
                except WeeklySourceIncomplete:
                    return UnavailableSource(binding, SourceUnavailableReason.SOURCE_INCOMPLETE)
                except WeeklySourceVersionChanged:
                    return UnavailableSource(binding, SourceUnavailableReason.VERSION_MISMATCH)
                except (json.JSONDecodeError, InvalidSerializedLocator, WeeklyInvalidLocator):
                    return UnavailableSource(binding, SourceUnavailableReason.INVALID_LOCATOR)
        else:
            try:
                body = native_trajectory.read_native_source_body(
                    _parse_locator_json(binding.native_provenance_json)
                )
            except native_trajectory.NativeSourceMissing:
                return UnavailableSource(binding, SourceUnavailableReason.SOURCE_MISSING)
            except native_trajectory.NativeSourceVersionChanged:
                return UnavailableSource(binding, SourceUnavailableReason.VERSION_MISMATCH)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return UnavailableSource(binding, SourceUnavailableReason.SOURCE_INCOMPLETE)
            except ValueError:
                return UnavailableSource(binding, SourceUnavailableReason.INVALID_LOCATOR)
        if not binding.matches_body(body):
            return UnavailableSource(binding, SourceUnavailableReason.VERSION_MISMATCH)
        return AvailableSource(binding, body)
