"""Every committed schema in app/schemas is pinned to the model it describes.

A schema file is data: it is live the moment it is saved. A Pydantic model is
code: it is live only after a restart. Wherever the two are compared at run
time, editing both in one commit takes the service down until someone restarts
it, which is how ~77 reply tasks were burned on 2026-09-10. So the comparison
belongs here, at commit time, and nowhere in the request path.

Pinning them also catches the quieter failure. For most of these files the
on-disk copy is what the agent is actually handed as its output contract, so a
file that has drifted from its model asks an agent for a shape the service will
not parse, or fails to ask for fields the service now expects.

Every file in app/schemas must appear in exactly one registry below, so a new
schema cannot be added without someone deciding which of the three it is.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "app" / "schemas"

# Snapshots that equal their model today. Keep them that way.
PINNED: dict[str, tuple[str, str]] = {
    "audit_agent_result.schema.json": ("app.agent_contracts", "AuditAgentResult"),
    "consumer_agent_result.schema.json": ("app.consumer_agent", "ConsumerAgentResult"),
    "meeting_alignment_decision.schema.json": (
        "app.meeting_alignment_models",
        "MeetingAlignmentDecision",
    ),
}

# Snapshots that do NOT equal their model. Each of these files is read at run
# time and handed to an agent, so regenerating one changes what that agent is
# asked to produce; that call belongs to the module's owner, not to this test.
# Listing them keeps the debt visible and stops any of it growing silently.
KNOWN_DRIFT: dict[str, tuple[str, str, str]] = {
    "agent_envelope.schema.json": (
        "app.agent_envelope",
        "AgentEnvelope",
        "on-disk copy is stricter than the model: it requires `title` on the "
        "markdown reply action and seven fields on the reaction action where "
        "the model requires one",
    ),
    "codex_decision.schema.json": (
        "app.dingtalk_models",
        "CodexDecision",
        "drifted both ways: the file still requires eight fields the model has "
        "made optional, and never mentions system_actions, failure_code or "
        "external_dependency_failed, which the model now carries",
    ),
    "project_memory_context.schema.json": (
        "app.task_models",
        "ProjectMemoryContext",
        "file requires memories/query/summary and names its item definition "
        "memory_context_item; the model makes every field optional and names "
        "it ProjectMemoryContextItem",
    ),
    "repository_upgrade_suggestion.schema.json": (
        "app.repository_upgrade_agent",
        "PreservationSuggestion",
        "same fields, types and constraints; differs only in the per-property "
        "and top-level `title` strings pydantic v2 emits",
    ),
    "wechat_memory_write_result.schema.json": (
        "app.codex_memory_write",
        "MemoryWriteTypedResult",
        "same fields, types and constraints; differs only in the per-property "
        "and top-level `title` strings pydantic v2 emits",
    ),
    "weekly_okr_report.schema.json": (
        "app.weekly_okr_report",
        "WeeklyOkrAnalysis",
        "file requires six top-level fields where the model requires two, and "
        "keeps a flat `dimensions` definition the model has split into "
        "CeoAttentionItem, DimensionScoreReview, KrScoreReview and "
        "ManagerReportAnalysis",
    ),
}

# Hand-authored envelopes with no single root model: each wraps a list of an
# inner model (ExtractedMemoryCandidate, DurableMemoryMatch) that carries no
# top-level schema of its own, so there is nothing to compare them against.
NO_SINGLE_MODEL: dict[str, str] = {
    "wechat_memory_candidates.schema.json": (
        "envelope around a list of app.wechat.memory_import."
        "ExtractedMemoryCandidate"
    ),
    "wechat_memory_dedupe.schema.json": (
        "envelope around a list of app.wechat.memory_import.DurableMemoryMatch"
    ),
}


def _model_schema(module_name: str, class_name: str) -> dict:
    return getattr(importlib.import_module(module_name), class_name).model_json_schema()


def _snapshot(file_name: str) -> dict:
    loaded = json.loads((SCHEMA_DIR / file_name).read_text(encoding="utf-8"))
    # A committed snapshot may declare its JSON Schema dialect; the model never
    # emits that key, and it says nothing about the shape being described.
    return {key: value for key, value in loaded.items() if key != "$schema"}


def test_every_schema_file_is_accounted_for() -> None:
    on_disk = {path.name for path in SCHEMA_DIR.glob("*.json")}
    registered = set(PINNED) | set(KNOWN_DRIFT) | set(NO_SINGLE_MODEL)
    assert on_disk == registered, (
        "every file in app/schemas must be registered in exactly one of "
        "PINNED, KNOWN_DRIFT or NO_SINGLE_MODEL; unregistered: "
        f"{sorted(on_disk - registered)}, registered but missing: "
        f"{sorted(registered - on_disk)}"
    )


def test_registries_do_not_overlap() -> None:
    assert not set(PINNED) & set(KNOWN_DRIFT)
    assert not set(PINNED) & set(NO_SINGLE_MODEL)
    assert not set(KNOWN_DRIFT) & set(NO_SINGLE_MODEL)


@pytest.mark.parametrize("file_name", sorted(PINNED))
def test_pinned_snapshot_equals_its_model(file_name: str) -> None:
    module_name, class_name = PINNED[file_name]
    assert _snapshot(file_name) == _model_schema(module_name, class_name), (
        f"app/schemas/{file_name} no longer matches "
        f"{module_name}.{class_name}. Regenerate the snapshot from the model in "
        "the same commit that changed the model, and restart the service once "
        "it lands."
    )


@pytest.mark.parametrize("file_name", sorted(KNOWN_DRIFT))
def test_known_drift_is_still_drift(file_name: str) -> None:
    """When someone repairs one of these, make them move it to PINNED.

    A debt list nobody maintains is worse than no list, because it reads as a
    statement about the code that has quietly stopped being true.
    """
    module_name, class_name, reason = KNOWN_DRIFT[file_name]
    assert _snapshot(file_name) != _model_schema(module_name, class_name), (
        f"app/schemas/{file_name} now matches {module_name}.{class_name} "
        f"({reason}). Move it from KNOWN_DRIFT to PINNED so it stays matched."
    )
