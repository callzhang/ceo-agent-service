"""Closed-world Feishu capability metadata.

This module is deliberately declarative.  It records the parity phase,
credential boundary, risk, and current implementation state for every
persisted :class:`~app.universal_plan.PlannedActionKind`; it does not import or
pretend to provide adapters that have not been implemented yet.

The two ``dws_*`` action values are persisted compatibility identifiers.  They
remain unchanged and map to provider-neutral capability IDs here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.universal_plan import PlannedActionKind


class CapabilityProvider(StrEnum):
    FEISHU = "feishu"


class CapabilityPhase(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


class CredentialKind(StrEnum):
    TAT = "TAT"
    UAT = "UAT"
    LOC = "LOC"
    MEM = "MEM"


class CapabilityRisk(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class CapabilityStatus(StrEnum):
    PLANNED = "planned"
    IMPLEMENTED = "implemented"
    VERIFIED_NO_EQUIVALENT = "verified_no_equivalent"


class UnsupportedCapabilityError(LookupError):
    """Raised when a provider/action pair is outside the closed registry."""


_CAPABILITY_ID_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    provider: CapabilityProvider
    action_kind: PlannedActionKind
    capability_id: str
    phase: CapabilityPhase
    credential_kind: CredentialKind
    risk: CapabilityRisk
    status: CapabilityStatus

    def __post_init__(self) -> None:
        enum_fields = (
            ("provider", self.provider, CapabilityProvider),
            ("action_kind", self.action_kind, PlannedActionKind),
            ("phase", self.phase, CapabilityPhase),
            ("credential_kind", self.credential_kind, CredentialKind),
            ("risk", self.risk, CapabilityRisk),
            ("status", self.status, CapabilityStatus),
        )
        for field_name, value, expected_type in enum_fields:
            if not isinstance(value, expected_type):
                raise TypeError(f"{field_name} must be {expected_type.__name__}")
        if not _CAPABILITY_ID_PATTERN.fullmatch(self.capability_id):
            raise ValueError("capability_id must be a stable dotted identifier")

    def as_manifest_entry(self) -> dict[str, str]:
        return {
            "provider": self.provider.value,
            "action_kind": self.action_kind.value,
            "capability_id": self.capability_id,
            "phase": self.phase.value,
            "credential_kind": self.credential_kind.value,
            "risk": self.risk.value,
            "status": self.status.value,
        }


# Keep this tuple in PlannedActionKind declaration order.  ``implemented``
# means a real Feishu execution path with dedicated tests exists today.  In
# particular, HANDOFF_TO_HUMAN remains planned: the current consumer can finish
# such a task, but does not notify a human.
FEISHU_CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.SEND_REPLY,
        "message.reply",
        CapabilityPhase.P1,
        CredentialKind.TAT,
        CapabilityRisk.R2,
        CapabilityStatus.IMPLEMENTED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
        "message.ask_clarifying_question",
        CapabilityPhase.P1,
        CredentialKind.TAT,
        CapabilityRisk.R2,
        CapabilityStatus.IMPLEMENTED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.OA_APPROVAL,
        "approval.respond",
        CapabilityPhase.P4,
        CredentialKind.UAT,
        CapabilityRisk.R4,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.MAIL_REPLY,
        "mail.reply",
        CapabilityPhase.P4,
        CredentialKind.UAT,
        CapabilityRisk.R4,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.CALENDAR_RESPONSE,
        "calendar.respond",
        CapabilityPhase.P3,
        CredentialKind.UAT,
        CapabilityRisk.R3,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        "document.create_and_reply",
        CapabilityPhase.P3,
        CredentialKind.TAT,
        CapabilityRisk.R3,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.DWS_MESSAGE_REACTION,
        "message.react",
        CapabilityPhase.P1,
        CredentialKind.TAT,
        CapabilityRisk.R2,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.QUEUE_OKR_REVIEW,
        "okr.queue_review",
        CapabilityPhase.P5,
        CredentialKind.UAT,
        CapabilityRisk.R2,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.MEMORY_WRITE,
        "memory.write",
        CapabilityPhase.P3,
        CredentialKind.MEM,
        CapabilityRisk.R3,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.NO_REPLY,
        "control.no_reply",
        CapabilityPhase.P0,
        CredentialKind.LOC,
        CapabilityRisk.R0,
        CapabilityStatus.IMPLEMENTED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.HANDOFF_TO_HUMAN,
        "handoff.notify_human",
        CapabilityPhase.P1,
        CredentialKind.TAT,
        CapabilityRisk.R2,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.BLOCKED,
        "control.blocked",
        CapabilityPhase.P0,
        CredentialKind.LOC,
        CapabilityRisk.R0,
        CapabilityStatus.PLANNED,
    ),
    CapabilityDefinition(
        CapabilityProvider.FEISHU,
        PlannedActionKind.STOP_WITH_ERROR,
        "control.stop_with_error",
        CapabilityPhase.P0,
        CredentialKind.LOC,
        CapabilityRisk.R0,
        CapabilityStatus.PLANNED,
    ),
)


def _validate_closed_registry() -> dict[PlannedActionKind, CapabilityDefinition]:
    definitions_by_action: dict[PlannedActionKind, CapabilityDefinition] = {}
    capability_ids: set[str] = set()
    for definition in FEISHU_CAPABILITIES:
        if definition.action_kind in definitions_by_action:
            raise RuntimeError(
                f"duplicate Feishu action mapping: {definition.action_kind.value}"
            )
        if definition.capability_id in capability_ids:
            raise RuntimeError(
                f"duplicate Feishu capability ID: {definition.capability_id}"
            )
        definitions_by_action[definition.action_kind] = definition
        capability_ids.add(definition.capability_id)

    missing = set(PlannedActionKind) - set(definitions_by_action)
    extra = set(definitions_by_action) - set(PlannedActionKind)
    if missing or extra:
        raise RuntimeError(
            "Feishu capability registry does not match PlannedActionKind: "
            f"missing={sorted(item.value for item in missing)} "
            f"extra={sorted(item.value for item in extra)}"
        )
    return definitions_by_action


_FEISHU_CAPABILITY_BY_ACTION = _validate_closed_registry()


def _normalize_provider(provider: CapabilityProvider | str) -> CapabilityProvider:
    try:
        return CapabilityProvider(provider)
    except (TypeError, ValueError) as exc:
        raise UnsupportedCapabilityError(
            f"unsupported capability provider: {provider!r}"
        ) from exc


def _normalize_action(action: PlannedActionKind | str) -> PlannedActionKind:
    try:
        return PlannedActionKind(action)
    except (TypeError, ValueError) as exc:
        raise UnsupportedCapabilityError(
            f"unsupported planned action: {action!r}"
        ) from exc


def resolve_capability(
    provider: CapabilityProvider | str,
    action: PlannedActionKind | str,
) -> CapabilityDefinition:
    """Resolve a known provider/action pair or fail closed."""

    normalized_provider = _normalize_provider(provider)
    normalized_action = _normalize_action(action)
    if normalized_provider is not CapabilityProvider.FEISHU:
        # The enum is intentionally closed today; keep this guard if another
        # provider is added without a complete registry.
        raise UnsupportedCapabilityError(
            f"unsupported capability provider: {normalized_provider.value!r}"
        )
    try:
        return _FEISHU_CAPABILITY_BY_ACTION[normalized_action]
    except KeyError as exc:  # defensive against an incomplete future registry
        raise UnsupportedCapabilityError(
            f"unsupported planned action: {normalized_action.value!r}"
        ) from exc


def capability_manifest(
    provider: CapabilityProvider | str = CapabilityProvider.FEISHU,
) -> list[dict[str, str]]:
    """Return a fresh, JSON-serializable manifest in action declaration order."""

    normalized_provider = _normalize_provider(provider)
    if normalized_provider is not CapabilityProvider.FEISHU:
        raise UnsupportedCapabilityError(
            f"unsupported capability provider: {normalized_provider.value!r}"
        )
    return [definition.as_manifest_entry() for definition in FEISHU_CAPABILITIES]


__all__ = [
    "CapabilityDefinition",
    "CapabilityPhase",
    "CapabilityProvider",
    "CapabilityRisk",
    "CapabilityStatus",
    "CredentialKind",
    "FEISHU_CAPABILITIES",
    "UnsupportedCapabilityError",
    "capability_manifest",
    "resolve_capability",
]
