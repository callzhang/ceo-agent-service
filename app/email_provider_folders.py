"""Provider-neutral folder inventory contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class FolderRole(StrEnum):
    INBOX = "inbox"
    CATEGORY = "category"
    JUNK = "junk"
    TRASH = "trash"
    SENT = "sent"
    DRAFT = "draft"
    UNBOUND = "unbound"


@dataclass(frozen=True)
class ProviderFolder:
    provider_folder_id: str
    display_name: str
    role: FolderRole

    def __post_init__(self) -> None:
        for field in ("provider_folder_id", "display_name"):
            value = getattr(self, field)
            if type(value) is not str or not value:
                raise ValueError(f"{field} must be nonempty text")
        if type(self.role) is not FolderRole:
            raise TypeError("role must be a FolderRole")


def folder_role_from_special_use_flags(flags: Iterable[str]) -> FolderRole:
    normalized = frozenset(flag.upper() for flag in flags)
    roles = (
        ("\\TRASH", FolderRole.TRASH),
        ("\\JUNK", FolderRole.JUNK),
        ("\\SENT", FolderRole.SENT),
        ("\\DRAFTS", FolderRole.DRAFT),
        ("\\INBOX", FolderRole.INBOX),
    )
    return next((role for flag, role in roles if flag in normalized), FolderRole.UNBOUND)
