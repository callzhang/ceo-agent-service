"""Typed, side-effect-free contracts for email classification and action plans."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from collections.abc import Mapping as MappingABC, Sequence
from types import MappingProxyType
from typing import Annotated, Callable, Literal, Mapping

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationInfo,
    WrapValidator,
    field_serializer,
    field_validator,
    model_validator,
)


INITIAL_EMAIL_CATEGORY_KEYS = (
    "work",
    "human_resources",
    "legal",
    "financing",
    "personal",
    "notification",
    "external_billing",
    "shopping",
    "junk",
)
RESERVED_EMAIL_CATEGORY_KEYS = frozenset(
    {"important", "subscription", "other", "billing"}
)
LEGACY_EMAIL_CATEGORY_KEYS = frozenset({"important", "subscription", "billing"})
_LEGACY_CATEGORY_CONTEXT_KEY = "allow_legacy_email_category_keys"


def _reject_reserved_email_category_key(value: str) -> str:
    if value in RESERVED_EMAIL_CATEGORY_KEYS:
        raise ValueError(f"reserved email category key: {value}")
    return value


def _validate_email_category_key_with_context(
    value: object,
    handler: Callable[[object], str],
    info: ValidationInfo,
) -> str:
    if (
        info.context
        and info.context.get(_LEGACY_CATEGORY_CONTEXT_KEY) is True
        and type(value) is str
        and value in LEGACY_EMAIL_CATEGORY_KEYS
    ):
        return value
    return handler(value)


EmailCategoryKey = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{1,63}$"),
    AfterValidator(_reject_reserved_email_category_key),
    WrapValidator(_validate_email_category_key_with_context),
]
_EMAIL_CATEGORY_KEY_ADAPTER = TypeAdapter(EmailCategoryKey)


def validate_email_category_key(value: object) -> EmailCategoryKey:
    """Return one canonical category key without coercion or normalization."""

    return _EMAIL_CATEGORY_KEY_ADAPTER.validate_python(value)


def rehydrate_legacy_email_category_key(value: object) -> str:
    """Validate persisted category history, including the three retired keys."""

    return _EMAIL_CATEGORY_KEY_ADAPTER.validate_python(
        value,
        context={_LEGACY_CATEGORY_CONTEXT_KEY: True},
    )


class EmailCategory(StrEnum):
    IMPORTANT = "important"
    WORK = "work"
    HUMAN_RESOURCES = "human_resources"
    LEGAL = "legal"
    FINANCING = "financing"
    PERSONAL = "personal"
    NOTIFICATION = "notification"
    BILLING = "billing"
    EXTERNAL_BILLING = "external_billing"
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


_MESSAGE_ID_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_MESSAGE_ID_DOMAIN_ATOM = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_RFC_MESSAGE_ID = re.compile(
    rf"^(?P<local>{_MESSAGE_ID_ATOM}(?:\.{_MESSAGE_ID_ATOM})*)@"
    rf"(?P<domain>{_MESSAGE_ID_DOMAIN_ATOM}(?:\.{_MESSAGE_ID_DOMAIN_ATOM})*)$"
)


class EmailClassificationStatus(StrEnum):
    PENDING_FEEDBACK = "pending_feedback"
    PROCESSED = "processed"


def _freeze_json_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("authorization parameters must contain finite JSON values")
        return value
    if isinstance(value, MappingABC):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("authorization parameters require JSON string keys")
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_value(item) for item in value)
    raise ValueError("authorization parameters must contain only JSON-domain values")


def _serialize_json_value(value: object) -> object:
    if isinstance(value, MappingABC):
        return {str(key): _serialize_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_json_value(item) for item in value]
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
        if candidate.startswith("<") or candidate.endswith(">"):
            if not (
                candidate.startswith("<")
                and candidate.endswith(">")
                and candidate.count("<") == 1
                and candidate.count(">") == 1
            ):
                return None
            candidate = candidate[1:-1].strip()
        match = _RFC_MESSAGE_ID.fullmatch(candidate)
        if match is None:
            return None
        return f"<{match.group('local')}@{match.group('domain').lower()}>"

    @field_validator("thread_id", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("optional locator values must be strings or null")
        return value.strip() or None

    @property
    def stable_message_identity(self) -> str:
        if self.rfc_message_id is not None:
            return f"{self.account_id}:message-id:{self.rfc_message_id}"
        return f"{self.account_id}:imap:{self.folder}:{self.uidvalidity}:{self.uid}"


class EmailAttachmentMetadata(BaseModel):
    """Attachment metadata only; payload bytes and decoded content are excluded."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    inline: bool


AuthorizationSource = Literal["model_eligibility", "user_confirmation"]
AuthorizationSnapshotFormat = Literal[
    "authorization_snapshot_v2",
    "legacy_unavailable_v1",
]


class EmailActionAuthorization(BaseModel):
    """One immutable configured-action authorization decision."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_type: EmailAction
    parameters: Mapping[str, object] = Field(default_factory=dict)
    authorization_source: AuthorizationSource
    eligibility_evidence_reference: str = Field(min_length=1)
    authorized: bool
    ineligible_reason: str
    source_model_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)

    @field_validator("parameters", mode="before")
    @classmethod
    def validate_json_parameters(cls, value: object) -> object:
        if not isinstance(value, MappingABC):
            raise ValueError("authorization parameters must be a JSON object")
        return _freeze_json_value(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: object) -> object:
        return _serialize_json_value(value)

    @field_validator(
        "eligibility_evidence_reference",
        "source_model_id",
        "config_version",
    )
    @classmethod
    def strip_required_authorization_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("authorization binding values must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> "EmailActionAuthorization":
        reason = self.ineligible_reason.strip()
        if self.authorized and reason:
            raise ValueError("authorized action cannot have an ineligible reason")
        if not self.authorized and not reason:
            raise ValueError("unauthorized action requires an ineligible reason")
        if self.action_type is EmailAction.AUTO_REPLY and self.authorized:
            raise ValueError("auto_reply authorization is disabled")
        object.__setattr__(self, "ineligible_reason", reason)
        object.__setattr__(self, "parameters", _freeze_json_value(self.parameters))
        return self


def _action_plan_identity(
    *,
    action_plan_version: int,
    classification_id: int,
    account_id: str,
    category: EmailCategoryKey,
    classification_source: Literal["model", "user"],
    confidence: float,
    model_id: str,
    config_version: str,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    created_at: datetime,
    authorization_snapshot_format: AuthorizationSnapshotFormat = (
        "legacy_unavailable_v1"
    ),
    action_authorizations: Sequence[EmailActionAuthorization] = (),
) -> str:
    """Identify the complete immutable plan snapshot, including creation time."""

    snapshot_value: dict[str, object] = {
        "action_plan_version": action_plan_version,
        "classification_id": classification_id,
        "account_id": account_id,
        "category": category,
        "classification_source": classification_source,
        "confidence": confidence,
        "model_id": model_id,
        "config_version": config_version,
        "actions": [action.value for action in actions],
        "action_parameters": {
            action.value: dict(parameters)
            for action, parameters in action_parameters.items()
        },
        "created_at": created_at.isoformat(),
    }
    if authorization_snapshot_format == "authorization_snapshot_v2":
        snapshot_value.update(
            {
                "authorization_snapshot_format": authorization_snapshot_format,
                "action_authorizations": [
                    authorization.model_dump(mode="json")
                    for authorization in action_authorizations
                ],
            }
        )
    snapshot = json.dumps(
        snapshot_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"email-action-plan:{sha256(snapshot.encode('utf-8')).hexdigest()}"


class EmailActionPlan(BaseModel):
    """Immutable authorization for exactly the configured actions in this snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    action_plan_id: str = Field(min_length=1)
    action_plan_version: int = Field(gt=0)
    classification_id: int = Field(gt=0)
    account_id: str = Field(min_length=1)
    category: EmailCategoryKey
    classification_source: Literal["model", "user"]
    confidence: float = Field(ge=0.0, le=1.0)
    model_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    actions: tuple[EmailAction, ...] = ()
    action_parameters: Mapping[EmailAction, Mapping[str, object]] = Field(
        default_factory=dict
    )
    authorization_snapshot_format: AuthorizationSnapshotFormat = "legacy_unavailable_v1"
    action_authorizations: tuple[EmailActionAuthorization, ...] = ()
    created_at: datetime

    @field_validator("action_parameters", mode="before")
    @classmethod
    def validate_action_parameters_json(cls, value: object) -> object:
        if not isinstance(value, MappingABC):
            raise ValueError("action parameters must be a JSON object")
        return _freeze_json_value(value)

    @field_serializer("action_parameters")
    def serialize_action_parameters(self, value: object, info: object) -> object:
        assert isinstance(value, MappingABC)
        json_mode = getattr(info, "mode", "python") == "json"
        return {
            (str(action) if json_mode else action): _serialize_json_value(parameters)
            for action, parameters in value.items()
        }

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
            raise ValueError(
                "action parameters contain an action that is not configured"
            )

        terminal_actions = {
            EmailAction.ARCHIVE,
            EmailAction.MOVE,
            EmailAction.TRASH,
        }
        if len(set(self.actions) & terminal_actions) > 1:
            raise ValueError("archive, move, and trash are mutually exclusive")

        if self.authorization_snapshot_format == "legacy_unavailable_v1":
            if self.action_authorizations:
                raise ValueError(
                    "legacy ActionPlan cannot contain authorization evidence"
                )
        else:
            configured_actions = tuple(
                authorization.action_type
                for authorization in self.action_authorizations
            )
            if len(configured_actions) != len(set(configured_actions)):
                raise ValueError("configured action authorizations must be unique")
            authorized_actions = tuple(
                authorization.action_type
                for authorization in self.action_authorizations
                if authorization.authorized
            )
            if self.actions != authorized_actions:
                raise ValueError(
                    "ActionPlan actions must equal authorized snapshot actions"
                )
            for authorization in self.action_authorizations:
                if (
                    authorization.authorized
                    and authorization.authorization_source == "model_eligibility"
                    and authorization.source_model_id != self.model_id
                ):
                    raise ValueError(
                        "action authorization model must match ActionPlan model"
                    )
                if authorization.config_version != self.config_version:
                    raise ValueError(
                        "action authorization config must match ActionPlan config"
                    )
                if (
                    authorization.authorized
                    and authorization.parameters
                    != _freeze_json_value(
                        self.action_parameters.get(authorization.action_type, {})
                    )
                ):
                    raise ValueError(
                        "authorized action parameters must match ActionPlan parameters"
                    )

        parameter_schemas = {
            EmailAction.LABEL: {"labels"},
            EmailAction.MOVE: {"target_folder"},
            EmailAction.AUTO_REPLY: {"instruction"},
        }
        for action, parameters in self.action_parameters.items():
            allowed_keys = parameter_schemas.get(action)
            if allowed_keys is None:
                if parameters:
                    raise ValueError(f"{action.value} does not accept parameters")
                continue
            unexpected_keys = set(parameters) - allowed_keys
            if unexpected_keys:
                raise ValueError(
                    f"{action.value} has unsupported parameters: "
                    f"{', '.join(sorted(unexpected_keys))}"
                )

        if EmailAction.LABEL in self.actions:
            labels = self.action_parameters.get(EmailAction.LABEL, {}).get("labels")
            if (
                not isinstance(labels, list | tuple)
                or not labels
                or any(
                    not isinstance(label, str) or not label.strip() for label in labels
                )
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

        expected_identity = _action_plan_identity(
            action_plan_version=self.action_plan_version,
            classification_id=self.classification_id,
            account_id=self.account_id,
            category=self.category,
            classification_source=self.classification_source,
            confidence=self.confidence,
            model_id=self.model_id,
            config_version=self.config_version,
            actions=self.actions,
            action_parameters=self.action_parameters,
            created_at=self.created_at,
            authorization_snapshot_format=self.authorization_snapshot_format,
            action_authorizations=self.action_authorizations,
        )
        if self.action_plan_id != expected_identity:
            raise ValueError(
                "action plan identity does not match its immutable snapshot"
            )

        object.__setattr__(
            self,
            "action_parameters",
            _freeze_json_value(self.action_parameters),
        )
        return self

    @property
    def direct_actions(self) -> tuple[EmailAction, ...]:
        return tuple(action for action in self.actions if action in DIRECT_ACTIONS)

    @property
    def agent_actions(self) -> tuple[EmailAction, ...]:
        return tuple(action for action in self.actions if action in AGENT_ACTIONS)


def rehydrate_legacy_email_action_plan_json(value: str) -> EmailActionPlan:
    """Rehydrate immutable plans written before retired categories were reserved."""

    return EmailActionPlan.model_validate_json(
        value,
        context={_LEGACY_CATEGORY_CONTEXT_KEY: True},
    )


def build_versioned_email_action_plan(
    *,
    action_plan_version: int,
    classification_id: int,
    account_id: str,
    category: EmailCategoryKey,
    classification_source: Literal["model", "user"],
    confidence: float,
    model_id: str,
    config_version: str,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    created_at: datetime,
    action_authorizations: Sequence[EmailActionAuthorization | Mapping[str, object]]
    | None = None,
) -> EmailActionPlan:
    """Build an immutable plan whose identity covers its explicit history version."""

    category = validate_email_category_key(category)
    copied_parameters = {
        action: dict(parameters) for action, parameters in action_parameters.items()
    }
    typed_authorizations = (
        ()
        if action_authorizations is None
        else tuple(
            EmailActionAuthorization.model_validate(
                item.model_dump(mode="python")
                if isinstance(item, EmailActionAuthorization)
                else item
            )
            for item in action_authorizations
        )
    )
    authorization_snapshot_format: AuthorizationSnapshotFormat = (
        "legacy_unavailable_v1"
        if action_authorizations is None
        else "authorization_snapshot_v2"
    )
    return EmailActionPlan(
        action_plan_id=_action_plan_identity(
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            category=category,
            classification_source=classification_source,
            confidence=confidence,
            model_id=model_id,
            config_version=config_version,
            actions=actions,
            action_parameters=copied_parameters,
            created_at=created_at,
            authorization_snapshot_format=authorization_snapshot_format,
            action_authorizations=typed_authorizations,
        ),
        action_plan_version=action_plan_version,
        classification_id=classification_id,
        account_id=account_id,
        category=category,
        classification_source=classification_source,
        confidence=confidence,
        model_id=model_id,
        config_version=config_version,
        actions=actions,
        action_parameters=copied_parameters,
        authorization_snapshot_format=authorization_snapshot_format,
        action_authorizations=typed_authorizations,
        created_at=created_at,
    )


def build_email_action_plan(
    *,
    classification_id: int,
    account_id: str,
    category: EmailCategoryKey,
    classification_source: Literal["model", "user"],
    confidence: float,
    model_id: str,
    config_version: str,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    created_at: datetime,
    action_authorizations: Sequence[EmailActionAuthorization | Mapping[str, object]]
    | None = None,
) -> EmailActionPlan:
    """Build the initial immutable ActionPlan version."""

    return build_versioned_email_action_plan(
        action_plan_version=1,
        classification_id=classification_id,
        account_id=account_id,
        category=category,
        classification_source=classification_source,
        confidence=confidence,
        model_id=model_id,
        config_version=config_version,
        actions=actions,
        action_parameters=action_parameters,
        created_at=created_at,
        action_authorizations=action_authorizations,
    )


def build_user_confirmation_authorizations(
    *,
    category: EmailCategoryKey,
    actions: Sequence[EmailAction],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    model_id: str,
    config_version: str,
) -> tuple[EmailActionAuthorization, ...]:
    """Freeze user-confirmed configured actions without model eligibility claims."""

    category = validate_email_category_key(category)
    records: list[EmailActionAuthorization] = []
    for action in actions:
        parameters = dict(action_parameters.get(action, {}))
        evidence = json.dumps(
            {
                "authorization_source": "user_confirmation",
                "category": category,
                "action_type": action.value,
                "parameters": parameters,
                "model_id": model_id,
                "config_version": config_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        records.append(
            EmailActionAuthorization(
                action_type=action,
                parameters=parameters,
                authorization_source="user_confirmation",
                eligibility_evidence_reference=(
                    "email-user-confirmation:"
                    + sha256(evidence.encode("utf-8")).hexdigest()
                ),
                authorized=True,
                ineligible_reason="",
                source_model_id=model_id,
                config_version=config_version,
            )
        )
    return tuple(records)


class EmailClassification(BaseModel):
    """One model suggestion and, only when processed, its authorized action plan."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    classification_id: int = Field(gt=0)
    stable_message_identity: str = Field(min_length=1)
    provider_locator: EmailProviderLocator
    category: EmailCategoryKey
    confidence: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    probabilities: dict[EmailCategoryKey, float] = Field(min_length=1)
    model_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    status: EmailClassificationStatus
    classification_source: Literal["model", "user"]
    action_plan: EmailActionPlan | None

    @field_validator("stable_message_identity", "model_id", "config_version")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> "EmailClassification":
        account_identity_prefix = f"{self.provider_locator.account_id}:"
        if not self.stable_message_identity.startswith(account_identity_prefix):
            raise ValueError("stable message identity must be scoped to account_id")
        if (
            self.provider_locator.rfc_message_id is not None
            and self.stable_message_identity
            != self.provider_locator.stable_message_identity
        ):
            raise ValueError("RFC Message-ID must define the stable message identity")
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
            raise ValueError(
                "classification and action plan classification ids must match"
            )
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
