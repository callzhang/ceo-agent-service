"""Resolve classification truth from current provider folder state only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.email_classifier_contracts import validate_email_category_key
from app.email_provider_folders import FolderRole, ProviderFolder


class EmailFolderTruthState(StrEnum):
    CATEGORIZED = "categorized"
    JUNK = "junk"
    EXCLUDED = "excluded"
    UNCLASSIFIED = "unclassified"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EmailFolderTruth:
    state: EmailFolderTruthState
    category_key: str | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not EmailFolderTruthState:
            raise TypeError("state must be an EmailFolderTruthState")
        if self.state is EmailFolderTruthState.CATEGORIZED:
            validate_email_category_key(self.category_key)
        elif self.category_key is not None:
            raise ValueError("only categorized truth may carry category_key")


def resolve_email_folder_truth(
    *,
    account_id: str,
    current_provider_folder_id: str,
    provider_folders: Sequence[ProviderFolder] | None,
    bindings: Sequence[Mapping[str, object]],
) -> EmailFolderTruth:
    """Return folder-derived truth without predicted or confirmed fallback state."""

    if type(account_id) is not str or not account_id:
        raise ValueError("account_id must be nonempty text")
    if provider_folders is None:
        return EmailFolderTruth(EmailFolderTruthState.UNAVAILABLE)
    matches = tuple(
        folder
        for folder in provider_folders
        if folder.provider_folder_id == current_provider_folder_id
    )
    if len(matches) != 1:
        return EmailFolderTruth(EmailFolderTruthState.UNAVAILABLE)
    folder = matches[0]
    if folder.role in {FolderRole.JUNK, FolderRole.TRASH}:
        return EmailFolderTruth(EmailFolderTruthState.JUNK)
    if folder.role in {FolderRole.SENT, FolderRole.DRAFT}:
        return EmailFolderTruth(EmailFolderTruthState.EXCLUDED)
    if folder.role is FolderRole.INBOX:
        return EmailFolderTruth(EmailFolderTruthState.UNCLASSIFIED)
    active = tuple(
        validate_email_category_key(binding.get("category_key"))
        for binding in bindings
        if binding.get("account_id") == account_id
        and binding.get("provider_folder_id") == current_provider_folder_id
        and binding.get("binding_status") == "active"
    )
    if len(active) == 1:
        return EmailFolderTruth(EmailFolderTruthState.CATEGORIZED, active[0])
    return EmailFolderTruth(EmailFolderTruthState.UNCLASSIFIED)
