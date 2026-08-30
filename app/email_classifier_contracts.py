"""Typed, side-effect-free contracts for email classification and action plans."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class EmailCategory(StrEnum):
    IMPORTANT = "important"
    WORK = "work"
    PERSONAL = "personal"
    NOTIFICATION = "notification"
    BILLING = "billing"
    SHOPPING = "shopping"
    SUBSCRIPTION = "subscription"
    JUNK = "junk"


class EmailAction(StrEnum):
    LABEL = "label"
    MARK_READ = "mark_read"
    ARCHIVE = "archive"
    MOVE = "move"
    TRASH = "trash"
    AUTO_REPLY = "auto_reply"
    UNSUBSCRIBE = "unsubscribe"


DIRECT_ACTIONS = (
    EmailAction.LABEL,
    EmailAction.MARK_READ,
    EmailAction.ARCHIVE,
    EmailAction.MOVE,
    EmailAction.TRASH,
)
AGENT_ACTIONS = (EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE)


class EmailClassificationStatus(StrEnum):
    PENDING_FEEDBACK = "pending_feedback"
    PROCESSED = "processed"


class _FrozenDict(dict):
    """A JSON-serializable dictionary that rejects mutation after construction."""

    @staticmethod
    def _immutable(*args: object, **kwargs: object) -> None:
        raise TypeError("immutable mapping")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


class EmailProviderLocator(BaseModel):
    """Stable, account-scoped coordinates for one provider message."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    account_id: str = Field(min_length=1)
    folder: str = Field(min_length=1)
    uidvalidity: int = Field(gt=0)
    uid: int = Field(gt=0)
    rfc_message_id: str | None = None
    thread_id: str | None = None

    @field_validator("account_id", "folder")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-blank")
        return value

    @field_validator("rfc_message_id", mode="before")
    @classmethod
    def normalize_rfc_message_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("rfc_message_id must be a string or null")
        candidate = value.strip()
        if candidate.startswith("<") and candidate.endswith(">"):
            candidate = candidate[1:-1].strip()
        if candidate.count("@") != 1 or any(character.isspace() for character in candidate):
            return None
        local_part, domain = candidate.split("@", 1)
        if not local_part or not domain:
            return None
        return f"<{local_part}@{domain.lower()}>"

    @field_validator("thread_id", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("thread_id must be a string or null")
        return value.strip() or None

    @property
    def stable_message_identity(self) -> str:
        if self.rfc_message_id is not None:
            return f"{self.account_id}:message-id:{self.rfc_message_id}"
        return (
            f"{self.account_id}:imap:{self.folder}:"
            f"{self.uidvalidity}:{self.uid}"
        )


class EmailAttachmentMetadata(BaseModel):
    """Attachment metadata only; payload bytes and decoded content are excluded."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    inline: bool


class EmailActionPlan(BaseModel):
    """Immutable authorization for exactly the configured actions in this snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_plan_id: str = Field(min_length=1)
    action_plan_version: int = Field(gt=0)
    classification_id: int = Field(gt=0)
    account_id: str = Field(min_length=1)
    category: EmailCategory
    classification_source: Literal["model", "user"]
    confidence: float = Field(ge=0.0, le=1.0)
    model_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    actions: tuple[EmailAction, ...] = ()
    action_parameters: dict[EmailAction, dict[str, object]] = Field(
        default_factory=dict
    )
    created_at: datetime

    @field_validator("action_plan_id", "account_id", "model_id", "config_version")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_actions(self) -> "EmailActionPlan":
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("configured email actions must be unique")

        unexpected_parameters = set(self.action_parameters) - set(self.actions)
        if unexpected_parameters:
            raise ValueError("action parameters contain an action that is not configured")

        terminal_actions = {
            EmailAction.ARCHIVE,
            EmailAction.MOVE,
            EmailAction.TRASH,
        }
        if len(set(self.actions) & terminal_actions) > 1:
            raise ValueError("archive, move, and trash are mutually exclusive")

        if EmailAction.LABEL in self.actions:
            labels = self.action_parameters.get(EmailAction.LABEL, {}).get("labels")
            if (
                not isinstance(labels, list | tuple)
                or not labels
                or any(not isinstance(label, str) or not label.strip() for label in labels)
            ):
                raise ValueError("label action requires one or more non-blank labels")

        if EmailAction.MOVE in self.actions:
            target = self.action_parameters.get(EmailAction.MOVE, {}).get(
                "target_folder"
            )
            if not isinstance(target, str) or not target.strip():
                raise ValueError("move action requires a non-blank target_folder")

        if EmailAction.AUTO_REPLY in self.actions:
            instruction = self.action_parameters.get(EmailAction.AUTO_REPLY, {}).get(
                "instruction"
            )
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError("auto_reply action requires a non-blank instruction")

        object.__setattr__(self, "action_parameters", _freeze(self.action_parameters))
        return self

    @property
    def direct_actions(self) -> tuple[EmailAction, ...]:
        return tuple(action for action in self.actions if action in DIRECT_ACTIONS)

    @property
    def agent_actions(self) -> tuple[EmailAction, ...]:
        return tuple(action for action in self.actions if action in AGENT_ACTIONS)


class EmailClassification(BaseModel):
    """One model suggestion and, only when processed, its authorized action plan."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    classification_id: int = Field(gt=0)
    provider_locator: EmailProviderLocator
    category: EmailCategory
    confidence: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(min_length=1)
    model_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    status: EmailClassificationStatus
    classification_source: Literal["model", "user"]
    action_plan: EmailActionPlan | None

    @field_validator("model_id", "config_version")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> "EmailClassification":
        if (
            self.classification_source == "user"
            and self.status is not EmailClassificationStatus.PROCESSED
        ):
            raise ValueError("user classification must be processed")
        if self.status is EmailClassificationStatus.PENDING_FEEDBACK:
            if self.action_plan is not None:
                raise ValueError("pending feedback cannot have an action plan")
            return self
        if self.action_plan is None:
            raise ValueError("processed classification requires an action plan")

        plan = self.action_plan
        if self.classification_id != plan.classification_id:
            raise ValueError("classification and action plan classification ids must match")
        if self.provider_locator.account_id != plan.account_id:
            raise ValueError("classification and action plan accounts must match")
        if self.category != plan.category:
            raise ValueError("classification and action plan categories must match")
        if self.classification_source != plan.classification_source:
            raise ValueError("classification and action plan sources must match")
        if self.confidence != plan.confidence:
            raise ValueError("classification and action plan confidence must match")
        if self.model_id != plan.model_id:
            raise ValueError("classification and action plan model ids must match")
        if self.config_version != plan.config_version:
            raise ValueError("classification and action plan configs must match")
        return self
