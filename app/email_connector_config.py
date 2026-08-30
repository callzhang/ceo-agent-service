"""Validated, secret-safe configuration for external email accounts."""

from __future__ import annotations

from collections.abc import Mapping
from email.errors import HeaderParseError
from email.headerregistry import Address
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)


_ACCOUNT_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
_IMAP_SECRET_REFERENCE_PATTERN = r"^CEO_EMAIL_[A-Z0-9_]+_IMAP_SECRET$"
_SMTP_SECRET_REFERENCE_PATTERN = r"^CEO_EMAIL_[A-Z0-9_]+_SMTP_SECRET$"
_SECRET_REFERENCE_PATTERN = re.compile(r"^CEO_EMAIL_[A-Z0-9_]+_(?:IMAP|SMTP)_SECRET$")


class EmailAccountPayload(BaseModel):
    """Strict account save request; secret values are never serialized."""

    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str = Field(pattern=_ACCOUNT_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    email_address: str = Field(min_length=3, max_length=254)
    imap_host: str = Field(min_length=1, max_length=253)
    imap_port: int = Field(ge=1, le=65535)
    imap_tls: bool = True
    imap_username: str = Field(min_length=1, max_length=320)
    imap_secret_reference: str = Field(pattern=_IMAP_SECRET_REFERENCE_PATTERN)
    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_tls: bool = True
    smtp_username: str = Field(min_length=1, max_length=320)
    smtp_secret_reference: str = Field(pattern=_SMTP_SECRET_REFERENCE_PATTERN)
    enabled: bool = True
    scan_folders: tuple[str, ...] = ("INBOX",)
    scan_interval_seconds: int = Field(default=60, ge=15, le=3600)
    allow_shared_email: bool = False
    imap_secret: SecretStr | None = Field(default=None, exclude=True)
    smtp_secret: SecretStr | None = Field(default=None, exclude=True)

    @field_validator(
        "display_name",
        "imap_host",
        "imap_username",
        "smtp_host",
        "smtp_username",
    )
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("email_address")
    @classmethod
    def validate_email_address(cls, value: str) -> str:
        try:
            address = Address(addr_spec=value)
        except (HeaderParseError, TypeError, ValueError) as exc:
            raise ValueError("email address is invalid") from exc
        if not address.username or not address.domain:
            raise ValueError("email address is invalid")
        return value

    @field_validator("scan_folders")
    @classmethod
    def validate_scan_folders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one scan folder is required")
        if any(not folder.strip() for folder in value):
            raise ValueError("scan folders must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("scan folders must be unique")
        return value

    def stored_values(self) -> dict[str, object]:
        """Return exactly the non-secret fields owned by ``email_accounts``."""

        return self.model_dump(
            exclude={"allow_shared_email", "imap_secret", "smtp_secret"}
        )


def resolve_secret(reference: str, env: Mapping[str, str]) -> str | None:
    """Resolve a validated environment reference without exposing its value."""

    if (
        not isinstance(reference, str)
        or _SECRET_REFERENCE_PATTERN.fullmatch(reference) is None
    ):
        raise ValueError("email secret reference is invalid")
    return env.get(reference)
