"""Reconstruct a Console source from retained Signal and linked Task event facts."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Callable, Iterable

from app.business_source_reader import BusinessTaskSignalFact, SourceVersionBinding
from app.task_console_actions import ACTIONS, candidate_action_body, candidate_action_reference
from app.task_semantic_models import BusinessTaskEvent, BusinessTaskEventType


type ConsoleLookup = Callable[
    [str], Iterable[tuple[BusinessTaskSignalFact, BusinessTaskEvent]]
]


class ConsoleSourceMissing(Exception):
    """No linked original event remains available for this exact Console reference."""


class ConsoleSourceIncomplete(Exception):
    """Retained linked facts cannot reconstruct one complete original body."""


class ConsoleSourceVersionChanged(Exception):
    """The retained Signal binding disagrees with the requested original version."""


def _coordinates(source_ref: str) -> tuple[int, str, str]:
    prefix = "console:task:"
    if not isinstance(source_ref, str) or not source_ref.startswith(prefix):
        raise ConsoleSourceIncomplete("invalid Console source reference")
    task_text, separator, remainder = source_ref[len(prefix):].partition(":")
    action, separator2, updated_at = remainder.partition(":")
    if (
        not separator or not separator2 or not task_text.isdecimal()
        or action not in ACTIONS or not updated_at
    ):
        raise ConsoleSourceIncomplete("invalid Console source reference")
    try:
        task_id = int(task_text)
    except ValueError as exc:
        raise ConsoleSourceIncomplete("invalid Console task ID") from exc
    if task_id <= 0:
        raise ConsoleSourceIncomplete("invalid Console task ID")
    if candidate_action_reference(task_id, action, updated_at) != source_ref:
        raise ConsoleSourceIncomplete("noncanonical Console source reference")
    return task_id, action, updated_at


def read_console_source_body(
    binding: SourceVersionBinding, lookup: ConsoleLookup
) -> str:
    task_id, action, updated_at = _coordinates(binding.source_ref)
    eligible: list[str] = []
    for signal, event in lookup(binding.source_ref):
        if signal.source.source_type != "console" or signal.source.source_ref != binding.source_ref:
            continue
        if (
            event.event_type != BusinessTaskEventType.STATUS_CHANGED
            or event.signal_id != signal.id or event.task_id != task_id
        ):
            continue
        if replace(signal.source, native_provenance_json=None, provider_locator_json=None) != replace(
            binding, native_provenance_json=None, provider_locator_json=None
        ):
            raise ConsoleSourceVersionChanged("retained Signal binding changed")
        try:
            context = json.loads(signal.context_json)
            before = json.loads(event.before_json)
        except (TypeError, ValueError) as exc:
            raise ConsoleSourceIncomplete("malformed retained Console facts") from exc
        if not isinstance(context, dict) or context.get("action") != action:
            raise ConsoleSourceIncomplete("Console action missing or changed")
        if not isinstance(before, dict):
            raise ConsoleSourceIncomplete("missing original task snapshot")
        title = before.get("title")
        before_time = before.get("updated_at")
        if not isinstance(title, str) or not isinstance(before_time, str):
            raise ConsoleSourceIncomplete("missing original title or update time")
        if candidate_action_reference(task_id, action, before_time) != binding.source_ref:
            raise ConsoleSourceIncomplete("original task version changed")
        eligible.append(candidate_action_body(signal.source.author_name, action, title))
        if len(eligible) > 1:
            raise ConsoleSourceIncomplete("ambiguous Console source")
    if not eligible:
        raise ConsoleSourceMissing("linked Console event missing")
    return eligible[0]


def retained_console_sources(db, source_ref):
    """Read linked current facts in the caller's transaction, with either row factory."""
    from app.action_storage import SYNC_ACTION_PROJECTIONS
    from app.provenance_storage import signal_projection
    from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FACT_FIELDS
    cursor = db.execute(
        f"select event.* from ({SYNC_ACTION_PROJECTIONS['business_task_events']}) event "
        f"join ({signal_projection()}) signal on signal.id=event.signal_id "
        "where signal.source_type='console' and signal.source_ref=? order by event.id",
        (source_ref,),
    )
    names = tuple(item[0] for item in cursor.description)
    events = [dict(zip(names, row, strict=True)) for row in cursor]
    result = []
    for event in events:
        cursor = db.execute(signal_projection() + " and a.business_signal_public_id=?", (event["signal_id"],))
        values = dict(zip((item[0] for item in cursor.description), cursor.fetchone(), strict=True))
        binding = SourceVersionBinding(**{name: values[name] for name in SOURCE_FIELDS})
        fact = BusinessTaskSignalFact(source=binding, **{name: values[name] for name in ("id", *SIGNAL_FACT_FIELDS)})
        result.append((fact, BusinessTaskEvent.model_validate(event)))
    return tuple(result)
