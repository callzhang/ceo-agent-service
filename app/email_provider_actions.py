"""Side-effect boundary for deterministic email provider actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

from app.email_classifier_contracts import EmailAction
from app.email_store import StoredEmailAction, StoredEmailLocator


@dataclass(frozen=True)
class ProviderActionResult:
    status: Literal["done", "failed"]
    provider_operation: str
    provider_target: str
    provider_result_id: str
    error: str = ""


@dataclass(frozen=True)
class ProviderMessageState:
    revision: str
    labels: frozenset[str]
    is_read: bool
    archived: bool
    folder: str
    trashed: bool

    def satisfies(
        self,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> bool:
        if action_type is EmailAction.LABEL:
            return set(parameters["labels"]).issubset(self.labels)
        if action_type is EmailAction.MARK_READ:
            return self.is_read
        if action_type is EmailAction.ARCHIVE:
            return self.archived
        if action_type is EmailAction.MOVE:
            return self.folder == parameters["target_folder"]
        if action_type is EmailAction.TRASH:
            return self.trashed
        raise ValueError(f"unsupported deterministic email action: {action_type.value}")


def _provider_operation(action_type: EmailAction) -> str:
    operations = {
        EmailAction.LABEL: "STORE LABELS",
        EmailAction.MARK_READ: "STORE \\Seen",
        EmailAction.ARCHIVE: "MOVE ARCHIVE",
        EmailAction.MOVE: "MOVE",
        EmailAction.TRASH: "MOVE TRASH",
    }
    try:
        return operations[action_type]
    except KeyError as exc:
        raise ValueError(
            f"unsupported deterministic email action: {action_type.value}"
        ) from exc


class DeterministicEmailProvider(Protocol):
    def read_state(self, locator: StoredEmailLocator) -> ProviderMessageState: ...

    def apply(
        self,
        locator: StoredEmailLocator,
        action_type: EmailAction,
        parameters: Mapping[str, object],
    ) -> None: ...


class DeterministicEmailActionExecutor:
    def __init__(self, provider: DeterministicEmailProvider):
        self.provider = provider

    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        operation = _provider_operation(action.action_type)
        try:
            current = self.provider.read_state(action.locator)
        except Exception as exc:
            return ProviderActionResult(
                status="failed",
                provider_operation="READ",
                provider_target=action.locator.stable_message_identity,
                provider_result_id="",
                error=f"provider_read_failed:{type(exc).__name__}",
            )
        if current.satisfies(action.action_type, action.parameters):
            return ProviderActionResult(
                status="done",
                provider_operation="readback_noop",
                provider_target=action.locator.stable_message_identity,
                provider_result_id=current.revision,
            )
        try:
            self.provider.apply(action.locator, action.action_type, action.parameters)
        except Exception as exc:
            return ProviderActionResult(
                status="failed",
                provider_operation=operation,
                provider_target=action.locator.stable_message_identity,
                provider_result_id="",
                error=f"provider_apply_failed:{type(exc).__name__}",
            )
        try:
            verified = self.provider.read_state(action.locator)
        except Exception as exc:
            return ProviderActionResult(
                status="failed",
                provider_operation=operation,
                provider_target=action.locator.stable_message_identity,
                provider_result_id="",
                error=f"provider_readback_failed:{type(exc).__name__}",
            )
        if verified.satisfies(action.action_type, action.parameters):
            return ProviderActionResult(
                status="done",
                provider_operation=operation,
                provider_target=action.locator.stable_message_identity,
                provider_result_id=verified.revision,
            )
        return ProviderActionResult(
            status="failed",
            provider_operation=operation,
            provider_target=action.locator.stable_message_identity,
            provider_result_id=verified.revision,
            error="provider_readback_mismatch",
        )
