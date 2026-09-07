"""Pure contracts and schema definitions for durable email categories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Protocol

from app.email_provider_folders import FolderRole


EMAIL_FOLDER_BINDING_STATUSES = frozenset(
    {"active", "missing", "ambiguous", "error"}
)
DESCRIPTION_VERSION = "email-category-descriptions-v1"
SEEDED_CONFIG_VERSION = "email-category-config-v1"
SEEDED_THRESHOLD = 0.95

INITIAL_CATEGORY_CONFIGS: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "work": {
            "display_name": "工作",
            "core": (
                "daily operations covering customers, projects, product, technology, "
                "sales, delivery, and ordinary internal approval."
            ),
            "include": (
                "daily operations",
                "customers",
                "projects",
                "product",
                "technology",
                "sales",
                "delivery",
                "ordinary internal approval",
            ),
            "exclude": (
                "human resources",
                "legal",
                "financing",
                "personal matters",
                "external charges",
                "standard shopping",
                "junk",
            ),
        },
        "human_resources": {
            "display_name": "人事",
            "core": "Employment and employee-lifecycle matters.",
            "include": (
                "recruiting",
                "candidates",
                "employment",
                "onboarding, transfer, and offboarding",
                "compensation",
                "performance",
                "employee relations",
            ),
            "exclude": ("legal disputes", "unrelated promotion"),
        },
        "legal": {
            "display_name": "法务",
            "core": "Non-financing legal rights, obligations, and risk.",
            "include": (
                "non-financing contracts",
                "rights and obligations",
                "lawyers",
                "disputes",
                "compliance",
                "intellectual property",
            ),
            "exclude": ("financing legal documents", "routine human resources"),
        },
        "financing": {
            "display_name": "融资",
            "core": "Fundraising transactions and investor relations.",
            "include": (
                "investors",
                "fundraising",
                "due diligence",
                "financing legal documents",
                "cap table",
                "closing",
            ),
            "exclude": ("unsolicited fundraising promotion", "ordinary legal"),
        },
        "personal": {
            "display_name": "个人",
            "core": "Derek's private life and identity matters.",
            "include": (
                "Derek",
                "family",
                "personal identity",
                "personal legal matters",
                "life matters",
            ),
            "exclude": ("company operations",),
        },
        "notification": {
            "display_name": "通知",
            "core": "Transactional security, calendar, system, and service notices.",
            "include": (
                "verification codes",
                "security state",
                "calendar status",
                "system status",
                "service status",
            ),
            "exclude": ("real business discussion",),
        },
        "external_billing": {
            "display_name": "外部账单",
            "core": "Charges and payment evidence issued to us by an outside party.",
            "include": (
                "an outside party charges us",
                "an outside party invoices us",
                "requests payment",
                "supplies payment proof",
            ),
            "exclude": (
                "our invoices to customers",
                "customer collections",
                "internal budgets",
                "project settlement",
            ),
        },
        "shopping": {
            "display_name": "购物",
            "core": "Standardized goods and consumer-service purchases.",
            "include": (
                "goods orders",
                "standardized consumer-service orders",
                "logistics",
                "refunds",
                "fulfillment",
            ),
            "exclude": ("external professional-service invoices",),
        },
        "junk": {
            "display_name": "垃圾邮件",
            "core": "Unwanted material with no retention value.",
            "include": (
                "unwanted messages",
                "no-retention-value messages",
                "suspicious promotion",
                "irrelevant promotion",
            ),
            "exclude": (
                "merely low-priority legitimate business",
                "Unsubscribe-link presence must not be classification evidence",
            ),
        },
    }
)

ColumnContract = tuple[str, bool, str | None]
CATEGORY_CONFIG_COLUMN_CONTRACTS: Mapping[str, ColumnContract] = {
    "category_key": ("text", False, None),
    "display_name": ("text", True, None),
    "core_description": ("text", True, None),
    "include_json": ("text", True, None),
    "exclude_json": ("text", True, None),
    "threshold": ("real", True, None),
    "actions_json": ("text", True, None),
    "action_parameters_json": ("text", True, None),
    "enabled": ("integer", True, None),
    "description_version": ("text", True, None),
    "config_version": ("text", True, None),
    "updated_at": ("text", True, None),
}
FOLDER_BINDING_COLUMN_CONTRACTS: Mapping[str, ColumnContract] = {
    "account_id": ("text", True, None),
    "category_key": ("text", True, None),
    "provider_folder_id": ("text", True, None),
    "provider_folder_name": ("text", True, None),
    "binding_status": ("text", True, None),
    "last_verified_at": ("text", True, None),
}
CATEGORY_CONFIG_CHECKS = (
    "trim(category_key) != ''",
    "trim(display_name) != ''",
    "trim(core_description) != ''",
    "json_valid(include_json)",
    "json_type(include_json) = 'array'",
    "json_array_length(include_json) > 0",
    "trim(json_extract(include_json, '$[0]')) != ''",
    "json_valid(exclude_json)",
    "json_type(exclude_json) = 'array'",
    "json_array_length(exclude_json) > 0",
    "trim(json_extract(exclude_json, '$[0]')) != ''",
    "threshold >= 0.0 and threshold <= 1.0",
    "json_valid(actions_json)",
    "json_valid(action_parameters_json)",
    "enabled in (0, 1)",
    "trim(description_version) != ''",
    "trim(config_version) != ''",
    "trim(updated_at) != ''",
)
FOLDER_BINDING_CHECKS = (
    "trim(account_id) != ''",
    "trim(category_key) != ''",
    "binding_status in ('active', 'missing', 'ambiguous', 'error')",
    "provider_folder_name != ''",
    "trim(last_verified_at) != ''",
)
STRUCTURED_CATEGORY_CONFIG_COLUMNS = frozenset(CATEGORY_CONFIG_COLUMN_CONTRACTS)

CATEGORY_CONFIG_TABLE_SQL = """
    create table email_category_configs (
        category_key text primary key check(trim(category_key) != ''),
        display_name text not null check(trim(display_name) != ''),
        core_description text not null check(trim(core_description) != ''),
        include_json text not null
            check(json_valid(include_json))
            check(json_type(include_json) = 'array')
            check(json_array_length(include_json) > 0)
            check(trim(json_extract(include_json, '$[0]')) != ''),
        exclude_json text not null
            check(json_valid(exclude_json))
            check(json_type(exclude_json) = 'array')
            check(json_array_length(exclude_json) > 0)
            check(trim(json_extract(exclude_json, '$[0]')) != ''),
        threshold real not null check(threshold >= 0.0 and threshold <= 1.0),
        actions_json text not null check(json_valid(actions_json)),
        action_parameters_json text not null
            check(json_valid(action_parameters_json)),
        enabled integer not null check(enabled in (0, 1)),
        description_version text not null check(trim(description_version) != ''),
        config_version text not null check(trim(config_version) != ''),
        updated_at text not null check(trim(updated_at) != '')
    )
"""
FOLDER_BINDING_TABLE_SQL = """
    create table email_category_folder_bindings (
        account_id text not null check(trim(account_id) != ''),
        category_key text not null check(trim(category_key) != ''),
        provider_folder_id text not null,
        provider_folder_name text not null check(provider_folder_name != ''),
        binding_status text not null check(binding_status in (
            'active', 'missing', 'ambiguous', 'error'
        )),
        last_verified_at text not null check(trim(last_verified_at) != ''),
        primary key(account_id, category_key),
        check(binding_status != 'active' or provider_folder_id != ''),
        foreign key(account_id) references email_accounts(account_id)
            on delete cascade,
        foreign key(category_key) references email_category_configs(category_key)
            on delete cascade
    )
"""
ACTIVE_BINDING_INDEX_SQL = """
    create unique index idx_email_category_folder_bindings_active_provider
    on email_category_folder_bindings(account_id, provider_folder_id)
    where binding_status = 'active'
"""


class CategoryConfigDataError(ValueError):
    """Persisted category configuration violates its pure data contract."""


@dataclass(frozen=True)
class VerifiedEmailFolderBinding:
    """One provider-folder observation produced by coordinator readback."""

    account_id: str
    provider_folder_id: str
    provider_folder_name: str
    binding_status: str
    last_verified_at: str
    provider_folder_role: FolderRole = FolderRole.UNBOUND

    def __post_init__(self) -> None:
        for field in (
            "account_id",
            "binding_status",
            "last_verified_at",
        ):
            value = getattr(self, field)
            if type(value) is not str or value != value.strip():
                raise ValueError(f"{field} must be canonical text")
        for field in ("provider_folder_id", "provider_folder_name"):
            if type(getattr(self, field)) is not str:
                raise ValueError(f"{field} must be text")
        if not self.account_id or not self.provider_folder_name or not self.last_verified_at:
            raise ValueError("verified folder binding fields must be non-empty")
        if self.binding_status not in EMAIL_FOLDER_BINDING_STATUSES:
            raise ValueError("binding_status is invalid")
        if self.binding_status == "active" and not self.provider_folder_id:
            raise ValueError("active folder binding requires provider_folder_id")
        if type(self.provider_folder_role) is not FolderRole:
            raise TypeError("provider_folder_role must be a FolderRole")


class EmailFolderBindingCoordinator(Protocol):
    """Provider boundary for creating folders and returning readback evidence."""

    def create_and_verify_bindings(
        self,
        *,
        category_key: str,
        provider_folder_name: str,
        enabled_accounts: Sequence[Mapping[str, object]],
    ) -> Sequence[VerifiedEmailFolderBinding]: ...


def validate_category_descriptions(
    *,
    display_name: str,
    core_description: str,
    include: Sequence[str],
    exclude: Sequence[str],
    description_version: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate canonical semantic fields and return immutable list values."""

    for field, value in (
        ("display_name", display_name),
        ("core_description", core_description),
        ("description_version", description_version),
    ):
        if type(value) is not str or not value.strip() or value != value.strip():
            raise ValueError(f"{field} must be nonblank canonical text")
    normalized: list[tuple[str, ...]] = []
    for field, values in (("include", include), ("exclude", exclude)):
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError(f"{field} must be a sequence of descriptions")
        items = tuple(values)
        if not items or any(
            type(item) is not str or not item.strip() or item != item.strip()
            for item in items
        ):
            raise ValueError(f"{field} must contain nonblank canonical descriptions")
        normalized.append(items)
    return normalized[0], normalized[1]


def _json_value(raw: object, *, field: str, expected_type: type[Any]) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CategoryConfigDataError(f"invalid {field} JSON") from exc
    if not isinstance(value, expected_type):
        raise CategoryConfigDataError(
            f"{field} must contain a JSON {expected_type.__name__}"
        )
    return value


def category_config_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one structured persistence row for store and API consumers."""

    include = _json_value(row["include_json"], field="include_json", expected_type=list)
    exclude = _json_value(row["exclude_json"], field="exclude_json", expected_type=list)
    validate_category_descriptions(
        display_name=row["display_name"],
        core_description=row["core_description"],
        include=include,
        exclude=exclude,
        description_version=row["description_version"],
    )
    return {
        "category_key": row["category_key"],
        "display_name": row["display_name"],
        "core_description": row["core_description"],
        "include": include,
        "exclude": exclude,
        "threshold": row["threshold"],
        "actions": _json_value(
            row["actions_json"], field="actions_json", expected_type=list
        ),
        "action_parameters": _json_value(
            row["action_parameters_json"],
            field="action_parameters_json",
            expected_type=dict,
        ),
        "enabled": bool(row["enabled"]),
        "description_version": row["description_version"],
        "config_version": row["config_version"],
        "updated_at": row["updated_at"],
    }


def legacy_config_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt a structured row to the worker's established read contract."""

    return {
        "category": row["category_key"],
        "description": row["core_description"],
        "threshold": row["threshold"],
        "actions": row["actions"],
        "action_parameters": row["action_parameters"],
        "enabled": row["enabled"],
        "config_version": row["config_version"],
        "updated_at": row["updated_at"],
    }
