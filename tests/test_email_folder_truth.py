import pytest

from app.email_folder_truth import (
    EmailFolderTruth,
    EmailFolderTruthState,
    resolve_email_folder_truth,
)
from app.email_provider_folders import FolderRole, ProviderFolder


def _folder(folder_id: str, name: str, role: FolderRole) -> ProviderFolder:
    return ProviderFolder(folder_id, name, role)


def test_truth_uses_active_provider_folder_binding_as_category_truth() -> None:
    truth = resolve_email_folder_truth(
        account_id="primary",
        current_provider_folder_id="folder-work",
        provider_folders=(_folder("folder-work", "Work", FolderRole.CATEGORY),),
        bindings=(
            {
                "provider_folder_id": "folder-work",
                "account_id": "primary",
                "category_key": "work",
                "binding_status": "active",
            },
        ),
    )

    assert truth.state is EmailFolderTruthState.CATEGORIZED
    assert truth.category_key == "work"


def test_truth_maps_provider_special_use_roles_without_business_bindings() -> None:
    for role in (FolderRole.JUNK, FolderRole.TRASH):
        assert resolve_email_folder_truth(
            account_id="primary",
            current_provider_folder_id=role.value,
            provider_folders=(_folder(role.value, role.value, role),),
            bindings=(),
        ).state is EmailFolderTruthState.JUNK
    for role in (FolderRole.SENT, FolderRole.DRAFT):
        assert resolve_email_folder_truth(
            account_id="primary",
            current_provider_folder_id=role.value,
            provider_folders=(_folder(role.value, role.value, role),),
            bindings=(),
        ).state is EmailFolderTruthState.EXCLUDED


def test_truth_treats_inbox_and_unbound_provider_folders_as_unclassified() -> None:
    folders = (
        _folder("inbox", "Inbox", FolderRole.INBOX),
        _folder("archive", "Archive", FolderRole.UNBOUND),
    )

    for folder_id in ("inbox", "archive"):
        assert resolve_email_folder_truth(
            account_id="primary",
            current_provider_folder_id=folder_id,
            provider_folders=folders,
            bindings=(),
        ).state is EmailFolderTruthState.UNCLASSIFIED


def test_truth_treats_inbox_as_unclassified_before_stale_active_binding() -> None:
    truth = resolve_email_folder_truth(
        account_id="primary",
        current_provider_folder_id="INBOX",
        provider_folders=(_folder("INBOX", "INBOX", FolderRole.INBOX),),
        bindings=(
            {
                "account_id": "primary",
                "provider_folder_id": "INBOX",
                "category_key": "work",
                "binding_status": "active",
            },
        ),
    )

    assert truth.state is EmailFolderTruthState.UNCLASSIFIED
    assert truth.category_key is None


def test_truth_has_no_predicted_or_confirmed_fallback_when_provider_is_unavailable() -> None:
    truth = resolve_email_folder_truth(
        account_id="primary",
        current_provider_folder_id="folder-work",
        provider_folders=None,
        bindings=(
            {
                "provider_folder_id": "folder-work",
                "account_id": "primary",
                "category_key": "predicted-work",
                "binding_status": "active",
            },
        ),
    )

    assert truth.state is EmailFolderTruthState.UNAVAILABLE
    assert truth.category_key is None


def test_truth_is_unavailable_when_current_provider_folder_is_not_in_inventory() -> None:
    truth = resolve_email_folder_truth(
        account_id="primary",
        current_provider_folder_id="missing",
        provider_folders=(_folder("inbox", "Inbox", FolderRole.INBOX),),
        bindings=(),
    )

    assert truth.state is EmailFolderTruthState.UNAVAILABLE


def test_truth_filters_bindings_by_account_and_rejects_category_coercion() -> None:
    folders = (_folder("folder-work", "Work", FolderRole.CATEGORY),)
    truth = resolve_email_folder_truth(
        account_id="primary",
        current_provider_folder_id="folder-work",
        provider_folders=folders,
        bindings=(
            {
                "account_id": "secondary",
                "provider_folder_id": "folder-work",
                "category_key": "work",
                "binding_status": "active",
            },
        ),
    )
    assert truth.state is EmailFolderTruthState.UNCLASSIFIED

    with pytest.raises((TypeError, ValueError)):
        resolve_email_folder_truth(
            account_id="primary",
            current_provider_folder_id="folder-work",
            provider_folders=folders,
            bindings=(
                {
                    "account_id": "primary",
                    "provider_folder_id": "folder-work",
                    "category_key": 42,
                    "binding_status": "active",
                },
            ),
        )


def test_folder_truth_value_enforces_state_and_category_invariants() -> None:
    with pytest.raises(TypeError):
        EmailFolderTruth("categorized", "work")
    with pytest.raises((TypeError, ValueError)):
        EmailFolderTruth(EmailFolderTruthState.CATEGORIZED, "invalid key")
    with pytest.raises(ValueError):
        EmailFolderTruth(EmailFolderTruthState.UNCLASSIFIED, "work")
