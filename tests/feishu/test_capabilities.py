from __future__ import annotations

import json

import pytest

from app.feishu.capabilities import (
    CapabilityDefinition,
    CapabilityPhase,
    CapabilityProvider,
    CapabilityRisk,
    CapabilityStatus,
    CredentialKind,
    FEISHU_CAPABILITIES,
    UnsupportedCapabilityError,
    capability_manifest,
    resolve_capability,
)
from app.universal_plan import PlannedActionKind


EXPECTED_CAPABILITIES = (
    (
        "send_reply",
        "message.reply",
        "P1",
        "TAT",
        "R2",
        "implemented",
    ),
    (
        "ask_clarifying_question",
        "message.ask_clarifying_question",
        "P1",
        "TAT",
        "R2",
        "implemented",
    ),
    ("oa_approval", "approval.respond", "P4", "UAT", "R4", "planned"),
    ("mail_reply", "mail.reply", "P4", "UAT", "R4", "planned"),
    (
        "calendar_response",
        "calendar.respond",
        "P3",
        "UAT",
        "R3",
        "planned",
    ),
    (
        "dws_markdown_document_reply",
        "document.create_and_reply",
        "P3",
        "TAT",
        "R3",
        "planned",
    ),
    (
        "dws_message_reaction",
        "message.react",
        "P1",
        "TAT",
        "R2",
        "planned",
    ),
    (
        "queue_okr_review",
        "okr.queue_review",
        "P5",
        "UAT",
        "R2",
        "planned",
    ),
    ("memory_write", "memory.write", "P3", "MEM", "R3", "planned"),
    ("no_reply", "control.no_reply", "P0", "LOC", "R0", "implemented"),
    (
        "handoff_to_human",
        "handoff.notify_human",
        "P1",
        "TAT",
        "R2",
        "planned",
    ),
    ("blocked", "control.blocked", "P0", "LOC", "R0", "planned"),
    (
        "stop_with_error",
        "control.stop_with_error",
        "P0",
        "LOC",
        "R0",
        "planned",
    ),
)


def test_registry_is_exactly_closed_over_planned_action_kind() -> None:
    assert len(FEISHU_CAPABILITIES) == len(PlannedActionKind) == 13
    assert [item.action_kind for item in FEISHU_CAPABILITIES] == list(
        PlannedActionKind
    )
    assert {item.action_kind for item in FEISHU_CAPABILITIES} == set(
        PlannedActionKind
    )


def test_registry_has_unique_action_and_capability_ids() -> None:
    assert len({item.action_kind for item in FEISHU_CAPABILITIES}) == 13
    assert len({item.capability_id for item in FEISHU_CAPABILITIES}) == 13


def test_registry_metadata_is_stable_and_does_not_overstate_p0() -> None:
    actual = tuple(
        (
            item.action_kind.value,
            item.capability_id,
            item.phase.value,
            item.credential_kind.value,
            item.risk.value,
            item.status.value,
        )
        for item in FEISHU_CAPABILITIES
    )
    assert actual == EXPECTED_CAPABILITIES
    assert resolve_capability("feishu", "handoff_to_human").status is (
        CapabilityStatus.PLANNED
    )
    assert resolve_capability("feishu", "blocked").status is CapabilityStatus.PLANNED
    assert resolve_capability("feishu", "stop_with_error").status is (
        CapabilityStatus.PLANNED
    )


def test_manifest_schema_is_exact_and_json_serializable() -> None:
    manifest = capability_manifest()
    assert len(manifest) == 13
    assert set(manifest[0]) == {
        "provider",
        "action_kind",
        "capability_id",
        "phase",
        "credential_kind",
        "risk",
        "status",
    }
    assert json.loads(json.dumps(manifest, sort_keys=True)) == manifest
    assert {row["provider"] for row in manifest} == {"feishu"}
    assert {row["phase"] for row in manifest} <= {item.value for item in CapabilityPhase}
    assert {row["credential_kind"] for row in manifest} <= {
        item.value for item in CredentialKind
    }
    assert {row["risk"] for row in manifest} <= {
        item.value for item in CapabilityRisk
    }
    assert {row["status"] for row in manifest} <= {
        item.value for item in CapabilityStatus
    }


def test_manifest_returns_fresh_entries_without_mutating_registry() -> None:
    first = capability_manifest()
    first[0]["status"] = "planned"
    assert capability_manifest()[0]["status"] == "implemented"


@pytest.mark.parametrize(
    "action",
    [PlannedActionKind.SEND_REPLY, "send_reply"],
)
def test_resolve_capability_accepts_typed_and_serialized_known_action(action) -> None:
    definition = resolve_capability(CapabilityProvider.FEISHU, action)
    assert definition is FEISHU_CAPABILITIES[0]


@pytest.mark.parametrize("provider", ["dingtalk", "", "FEISHU", object()])
def test_unknown_provider_fails_closed(provider) -> None:
    with pytest.raises(UnsupportedCapabilityError, match="unsupported capability provider"):
        resolve_capability(provider, PlannedActionKind.SEND_REPLY)
    with pytest.raises(UnsupportedCapabilityError, match="unsupported capability provider"):
        capability_manifest(provider)


@pytest.mark.parametrize("action", ["send_replay", "", object()])
def test_unknown_action_fails_closed(action) -> None:
    with pytest.raises(UnsupportedCapabilityError, match="unsupported planned action"):
        resolve_capability("feishu", action)


def test_capability_definition_rejects_invalid_schema_values() -> None:
    with pytest.raises(ValueError, match="stable dotted identifier"):
        CapabilityDefinition(
            CapabilityProvider.FEISHU,
            PlannedActionKind.SEND_REPLY,
            "not dotted",
            CapabilityPhase.P1,
            CredentialKind.TAT,
            CapabilityRisk.R2,
            CapabilityStatus.PLANNED,
        )
    with pytest.raises(TypeError, match="status must be CapabilityStatus"):
        CapabilityDefinition(
            CapabilityProvider.FEISHU,
            PlannedActionKind.SEND_REPLY,
            "message.reply",
            CapabilityPhase.P1,
            CredentialKind.TAT,
            CapabilityRisk.R2,
            "planned",  # type: ignore[arg-type]
        )


def test_persisted_dws_action_values_remain_unchanged() -> None:
    assert PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY.value == (
        "dws_markdown_document_reply"
    )
    assert PlannedActionKind.DWS_MESSAGE_REACTION.value == "dws_message_reaction"
