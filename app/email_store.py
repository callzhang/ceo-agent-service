"""Account-aware persistence for email classification and planned actions.

This module owns durable email business state only. It never connects to a
provider and never creates Agent, Audit, reply-task, or run records.
"""

from __future__ import annotations

from app.deployment_drain import claim_connection
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS

from app.store import (
    NATIVE_AGENT_RUN_SQL,
    NATIVE_REPLY_TASK_CURRENT_SQL,
    AutoReplyStore,
)

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message
from hashlib import sha256
import json
import logging
import math
import re
import sqlite3
import sys
from pathlib import Path
from types import MappingProxyType
from time import monotonic
from typing import Any, ClassVar
from uuid import uuid4

from app.email_classifier_contracts import (
    ACTION_DEPENDENCY_PARAMETER,
    DIRECT_ACTIONS,
    MODEL_OTHERS_CATEGORY_KEY,
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailCategoryKey,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
    build_email_action_plan,
    build_user_confirmation_authorizations,
    build_versioned_email_action_plan,
    effective_direct_action_dependencies,
    rehydrate_legacy_email_action_plan_json,
    rehydrate_legacy_email_category_key,
    validate_email_category_key,
)
from app.email_category_config import (
    CATEGORY_CONFIG_COLUMN_CONTRACTS,
    DESCRIPTION_VERSION,
    INITIAL_CATEGORY_CONFIGS,
    SEEDED_CONFIG_VERSION,
    SEEDED_THRESHOLD,
    CategoryConfigDataError,
    VerifiedEmailFolderBinding,
    category_config_row,
    legacy_config_row,
    validate_category_descriptions,
)
from app.action_email_history_storage import (
    email_history_rows, insert_email_history, update_email_operation,
)
from app.email_canonical_storage import (
    email_classification_projection as _canonical_classifications,
    email_message_projection as _canonical_messages,
    insert_email_classification, upsert_email_message,
    update_email_classification,
    update_email_paired_locator,
)

from app.current_schema_manifest import (
    create_current_email_migration_ledger,
    require_current_pair,
    schema_is_current,
)

from app.task8_storage import register_task8_functions
from app.state_definitions import state_projection
from app.state_storage import insert_state, state_json_field, update_state_rows, upsert_state
from app.provider_state_definitions import FRESH_OBSERVATION_FIELDS
from app.provider_state_storage import (
    mark_authoritative_missing, mark_inactive_accounts, mark_unavailable_folder,
    update_important_signals, upsert_folder_generations, upsert_observations,
)
from app.configs_definitions import config_projection
from app.configs_storage import insert_config, update_config, delete_config
from app.email_html_text import html_to_text, visible_email_text
from app.email_provider_folders import FolderRole
from app.leak_check import assert_no_credentials
from app.sqlite_schema_lock import schema_initialize_lock
from app.action_storage import (
    ActionWriteResult, insert_action, insert_email_action, update_action,
    delete_action, delete_email_action,
)


from app import shared_action_lifecycle
from app.current_email_fields import EMAIL_FIELDS
from app.shared_action_definitions import email_projection as email_action_projection

REPLY_TASK_SQL = NATIVE_REPLY_TASK_CURRENT_SQL
REPLY_TASK_CURRENT_SQL = NATIVE_REPLY_TASK_CURRENT_SQL
AGENT_RUN_SQL = NATIVE_AGENT_RUN_SQL

_EMAIL_CLASSIFICATION_SQL = """select tasks.id, tasks.id as task_id,
    inputs.stable_message_identity, tasks.status,
    tasks.lease_owner as owner, tasks.lease_expires_at,
    inputs.input_json, coalesce(results.result_json,'null') as result_json,
    tasks.attempts
    from tasks join email_classification_inputs inputs on inputs.task_id=tasks.id
    left join task_results results on results.task_id=tasks.id
      and results.result_kind='email_classification'
    where tasks.kind='email_classification'"""


# Finite Email29 source-shaped reads used by the current DAO and validators.
EMAIL_ACTION_PROJECTIONS = {kind: email_action_projection(kind) for kind in EMAIL_FIELDS}

EMAIL_PLAN_SQL = EMAIL_ACTION_PROJECTIONS["email_action_plans"]
# Direct effects have one current common Action owner; the historical
# source-shaped projection remains available from the release module only.
EMAIL_RETRY_SQL = EMAIL_ACTION_PROJECTIONS["email_direct_retry_requests"]
EMAIL_FEEDBACK_SQL = EMAIL_ACTION_PROJECTIONS["email_feedback_requests"]
EMAIL_REPLY_RECEIPT_SQL = EMAIL_ACTION_PROJECTIONS["email_reply_receipts"]
EMAIL_REPLY_CLAIM_SQL = EMAIL_ACTION_PROJECTIONS["email_reply_dispatch_claims"]
EMAIL_REPLY_CLAIM_LOCATOR_SQL = email_action_projection(
    "email_reply_dispatch_claims", include_rowid=True
)
EMAIL_REPLY_RECEIPT_LOCATOR_SQL = email_action_projection(
    "email_reply_receipts", include_rowid=True
)
EMAIL_UNSUBSCRIBE_RECEIPT_SQL = EMAIL_ACTION_PROJECTIONS["email_unsubscribe_receipts"]
EMAIL_UNSUBSCRIBE_CLAIM_SQL = EMAIL_ACTION_PROJECTIONS["email_unsubscribe_claims"]
EMAIL_UNSUBSCRIBE_EFFECT_SQL = EMAIL_ACTION_PROJECTIONS["email_unsubscribe_effects"]
EMAIL_UNSUBSCRIBE_CONTINUATION_SQL = EMAIL_ACTION_PROJECTIONS[
    "email_unsubscribe_continuations"
]
EMAIL_UNSUBSCRIBE_STEP_SQL = EMAIL_ACTION_PROJECTIONS["email_unsubscribe_steps"]
EMAIL_UNSUBSCRIBE_CANCELLATION_SQL = EMAIL_ACTION_PROJECTIONS[
    "email_unsubscribe_cancellations"
]

# Actual direct effects are owned by common Actions. These caller-local views
# retain the EmailStore row contract while taking status and attempt history
# exclusively from that owner and its immutable receipts. Complex unsubscribe
# workflows continue to use the specialized email projections above.
DIRECT_EMAIL_ACTION_SQL = """
select a.id as owner_id, a.id as action_id,
       a.action_action_plan_id as action_plan_id,
       a.action_classification_id as classification_id,
       a.action_account_id as account_id, a.action_operation as action_type,
       a.action_parameters_json as parameters_json, a.action_status as status,
       p.config_version,
       coalesce((select max(r.attempt_number) from action_receipts r
                 where r.action_id=a.id and r.attempt_number is not null),0) as attempt_count,
       (select r.started_at from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1) as last_attempt_started_at,
       coalesce(a.action_next_attempt_at,'') as next_attempt_at,
       case when a.action_status='processing' then a.action_started_at
            when a.action_status='pending' then null
            else coalesce((select r.started_at from action_receipts r
                           where r.action_id=a.id and r.attempt_number is not null
                           order by r.attempt_number desc limit 1),'') end as started_at,
       a.action_created_at as created_at, a.action_updated_at as updated_at,
       case when a.action_status='processing' then '' else coalesce((select r.finished_at from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1),'') end as finished_at,
       case when a.action_status='processing' then '' else coalesce((select r.operation from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1),'') end as provider_operation,
       case when a.action_status='processing' then '' else coalesce((select r.target from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1),'') end as provider_target,
       case when a.action_status='processing' then '' else coalesce((select r.provider_result_id from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1),'') end as provider_result_id,
       case when a.action_status='processing' then '' else coalesce((select r.error_text from action_receipts r where r.action_id=a.id
          and r.attempt_number is not null order by r.id desc limit 1),'') end as error
from actions a
join (""" + EMAIL_PLAN_SQL + """) p
  on p.action_plan_id=a.action_action_plan_id
where a.record_type='action' and a.action_action_plan_id is not null
  and a.action_operation in ('archive','flag_important','mark_read','move','trash','label')
"""

DIRECT_EMAIL_ATTEMPT_SQL = """
select r.id, a.id as action_id, r.attempt_number,
       r.status, coalesce(r.operation,'') as provider_operation,
       coalesce(r.target,'') as provider_target,
       coalesce(r.provider_result_id,'') as provider_result_id,
       coalesce(r.error_text,'') as error,
       r.started_at, r.finished_at
from action_receipts r join actions a on a.id=r.action_id
where a.record_type='action' and a.action_operation in
 ('archive','flag_important','mark_read','move','trash','label')
  and r.attempt_number is not null
"""
EMAIL_DIRECT_ACTION_SQL = DIRECT_EMAIL_ACTION_SQL
EMAIL_UNSUBSCRIBE_CLAIM_LOCATOR_SQL = email_action_projection(
    "email_unsubscribe_claims", include_rowid=True
)
EMAIL_UNSUBSCRIBE_CONTINUATION_LOCATOR_SQL = email_action_projection(
    "email_unsubscribe_continuations", include_rowid=True
)
EMAIL_UNSUBSCRIBE_RECEIPT_LOCATOR_SQL = email_action_projection(
    "email_unsubscribe_receipts", include_rowid=True
)
# Direct Email effects are current shared Actions; the source-shaped Email
# locator remains available only through the explicit release module.
EMAIL_DIRECT_ACTION_LOCATOR_SQL = DIRECT_EMAIL_ACTION_SQL

EMAIL_SCHEMA_VERSION = 54
EMAIL_CLASSIFICATION_SQL = _canonical_classifications()
EMAIL_MESSAGE_SQL = _canonical_messages()
MAX_CLASSIFIER_RUNTIME_SAMPLES = 2048
_RUNTIME_LATENCY_FALLBACK_SAMPLES = 200
HISTORICAL_DEFER_RETRY_SECONDS = 60
DIRECT_ACTION_MAX_ATTEMPTS = MAX_AUTOMATIC_ATTEMPTS
DIRECT_ACTION_EXHAUSTED_TRANSIENT_RETRY_SECONDS = 300
# Cross-restart bound for one accepted unsubscribe effect lineage.  This is a
# durable data limit, independent of any Agent process turn budget.
MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS = 32
DIRECT_ACTION_RETRY_BASE_SECONDS = 2
_CLASSIFICATION_STATUSES = frozenset(
    status.value for status in EmailClassificationStatus
)
_CLASSIFICATION_SOURCES = frozenset({"model", "user", "agent"})
_CURRENT_ACTION_STATUSES = frozenset(
    {"pending", "processing", "done", "skipped", "failed"}
)
_TERMINAL_ATTEMPT_STATUSES = frozenset({"done", "skipped", "failed"})
_DIRECT_ACTION_VALUES = frozenset(action.value for action in DIRECT_ACTIONS)
_EMAIL_REPLY_CLAIM_STATUSES = frozenset(
    {"dispatching", "retryable", "uncertain", "done"}
)
_EMAIL_UNSUBSCRIBE_CLAIM_STATUSES = frozenset(
    {"dispatching", "awaiting_audit", "uncertain", "done"}
)
# The direct unsubscribe path holds no lease, so a claim it retires is
# stamped with a name no live owner fence can ever match.
_DIRECT_UNSUBSCRIBE_OWNER_ID = "direct-unsubscribe"
_EMAIL_UNSUBSCRIBE_OUTCOMES = frozenset(
    {
        "done",
        "already_unsubscribed",
        "skipped_no_reliable_entry",
        "skipped_login_required",
        "skipped_captcha",
        "skipped_payment",
        "failed_browser",
        "failed_provider_auth",
    }
)
_OPAQUE_PROVIDER_ID = re.compile(r"[A-Za-z0-9._:@<>\[\]{}/+=,!#$%&'*?^-]+")
_UNSUBSCRIBE_OPAQUE_ID = re.compile(r"[A-Za-z0-9:_-]+")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_RUNTIME_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_MAX_PROVIDER_IDENTIFIER_BYTES = 256
_MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES = 16 * 1024


def _display_message_body(message: Message) -> str:
    payload = message.get_payload()
    if not isinstance(payload, str):
        return ""
    if message.get_content_type() == "text/html":
        return html_to_text(payload)
    return visible_email_text(payload)


_AUDITED_UNSUBSCRIBE_LINEAGE_SQL = f"""
    select effects.action_identity as effect_action_identity,
           effects.effect_digest as effect_digest,
           effects.audit_agent_run_id,
           audit_runs.id as audit_run_id,
           audit_runs.reply_task_id,
           audit_runs.execution_generation as audit_execution_generation,
           audit_runs.role as audit_role,
           tasks.id as task_id,
           tasks.channel as task_channel,
           tasks.conversation_id as task_conversation_id,
           tasks.trigger_message_id,
           tasks.trigger_message_json,
           tasks.input_compacted,
           tasks.input_provenance_json,
           tasks.execution_generation as task_execution_generation,
           tasks.status as task_status
    from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) as effects
    join ({AGENT_RUN_SQL}) as audit_runs
      on audit_runs.id=effects.audit_agent_run_id
    join ({REPLY_TASK_CURRENT_SQL}) as tasks
      on tasks.id=audit_runs.reply_task_id
    where effects.action_identity=? and effects.effect_digest=?
"""
_DIRECT_ACTION_PRIORITY = {
    action.value: priority for priority, action in enumerate(DIRECT_ACTIONS)
}
_UNREDACTED_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_UNREDACTED_SECRET_TOKEN = re.compile(
    r"\b(?:sub|ch|pi|sk|tok|token|sess|session|order|invoice|qrp)"
    r"[_.-]?[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{6,})*\b",
    flags=re.IGNORECASE,
)


class EmailClassificationConflict(RuntimeError):
    """The classification was already resolved by another confirmation."""


@dataclass(frozen=True)
class EmailFeedbackApplication:
    """Durable result of one explicit human-feedback intent."""

    feedback_request_id: str
    expected_current_action_plan_id: str | None
    resulting_action_plan_id: str
    confirmed: dict[str, Any]
    applied: bool

    @property
    def replayed(self) -> bool:
        return not self.applied






class EmailTrainingSnapshotConflict(RuntimeError):
    """A frozen snapshot identity is already bound to different content."""


class EmailClassificationIdentityCollision(RuntimeError):
    """A classification ID is already bound to another stable identity."""


class EmailCursorConflict(RuntimeError):
    """The persisted scan cursor no longer matches the scanner's observation."""


class EmailActionPlanConflict(RuntimeError):
    """An immutable ActionPlan ID or version conflicts with stored history."""


class EmailActionAttemptConflict(RuntimeError):
    """A direct-action attempt conflicts with append-only history."""


def _stored_action_started_at(db: sqlite3.Connection, action_id: int) -> str | None:
    """The Action owner's own started_at, which the direct-action view hides for pending rows."""

    from app.action_entity_storage import get_action

    action = get_action(db, int(action_id))
    return None if action is None else action["started_at"]


class EmailReplyReceiptConflict(RuntimeError):
    """A stable automatic-reply identity conflicts with durable provider evidence."""


class EmailReplyDispatchConflict(RuntimeError):
    """An automatic reply dispatch claim conflicts with authorization history."""


class EmailUnsubscribeReceiptConflict(RuntimeError):
    """An unsubscribe action is bound to different durable terminal evidence."""


class EmailUnsubscribeClaimConflict(RuntimeError):
    """An unsubscribe browser-write claim conflicts with authorization history."""


@dataclass(frozen=True)
class StoredEmailLocator:
    """Provider coordinates plus the durable message identity used for receipts."""

    account_id: str
    folder: str
    uidvalidity: int
    uid: int
    rfc_message_id: str | None
    thread_id: str | None
    stable_message_identity: str


@dataclass(frozen=True)
class StoredEmailAction:
    """One claimed deterministic action built from immutable persisted state."""

    action_id: str
    action_plan_id: str
    classification_id: int
    account_id: str
    action_type: EmailAction
    parameters: Mapping[str, object]
    config_version: str
    locator: StoredEmailLocator
    attempt_number: int
    claim_started_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameters",
            _freeze_action_parameters(self.parameters),
        )


@dataclass(frozen=True)
class FailedDirectAction:
    """A terminal-failed direct action as it stands, not claimed."""

    action: StoredEmailAction
    provider_operation: str
    error: str
    parameters_json: str = ""


class EmailAccountConflict(RuntimeError):
    """An account ID or unshared email address conflicts with stored config."""

    def __init__(self, code: str):
        super().__init__("email account configuration conflicts with stored state")
        self.code = code


class EmailFolderBindingConflict(RuntimeError):
    """A category or active provider folder conflicts with durable configuration."""


def _validate_verified_folder_binding_role(
    category_key: str,
    binding: VerifiedEmailFolderBinding,
) -> None:
    if binding.binding_status != "active":
        return
    if category_key == "junk":
        if binding.provider_folder_role is not FolderRole.TRASH:
            raise ValueError("junk accepts only a verified system Trash binding")
        return
    if binding.provider_folder_role not in {FolderRole.UNBOUND, FolderRole.CATEGORY}:
        raise ValueError("business category requires an ordinary provider folder")


@dataclass(frozen=True)
class EmailCategoryEnablementState:
    """One category state changed by an account mutation."""

    category_key: str
    enabled: bool
    updated_at: str
    mutation_updated_at: str


@dataclass(frozen=True)
class EmailCategoryEnablementSnapshot:
    """Optimistic restore token for one exact account mutation."""

    states: tuple[EmailCategoryEnablementState, ...]


class EmailPersistenceCorruption(RuntimeError):
    """Durable email state violates its JSON or enum contract."""


def _validate_model_text(model_text: str) -> None:
    lowered = model_text.lower()
    if (
        _UNREDACTED_EMAIL.search(model_text)
        or _UNREDACTED_SECRET_TOKEN.search(model_text)
        or "http://" in lowered
        or "https://" in lowered
    ):
        raise ValueError("model_text must be redacted")


def _validated_agent_result_json(
    classification: EmailClassification, agent_result: object | None
) -> str:
    if classification.classification_source != "agent":
        if agent_result is not None:
            raise ValueError("non-Agent classification cannot carry an Agent result")
        return "null"
    if agent_result is None:
        raise ValueError("Agent classification requires its exact typed result")
    from app.email_classifier_agent import DurableAgentClassificationResult

    result = (
        agent_result
        if isinstance(agent_result, DurableAgentClassificationResult)
        else DurableAgentClassificationResult.model_validate(agent_result)
    )
    expected_status = (
        EmailClassificationStatus.PROCESSED
        if result.certainty == "certain"
        else EmailClassificationStatus.PENDING_FEEDBACK
    )
    if classification.status is not expected_status:
        raise ValueError("Agent certainty and canonical status diverge")
    if classification.category != result.category:
        raise ValueError("Agent and canonical categories diverge")
    if classification.confidence != result.confidence:
        raise ValueError("Agent and canonical confidence diverge")
    return result.model_dump_json()


def _validated_agent_correction_result_json(
    action_plan: EmailActionPlan,
    agent_result: object | None,
) -> str:
    """Require the durable Agent decision that authorizes an Agent correction."""

    if action_plan.classification_source != "agent":
        if agent_result is not None:
            raise ValueError("non-Agent correction cannot carry an Agent result")
        return "null"
    if agent_result is None:
        raise ValueError("Agent correction requires its exact typed result")
    from app.email_classifier_agent import DurableAgentClassificationResult

    result = (
        agent_result
        if isinstance(agent_result, DurableAgentClassificationResult)
        else DurableAgentClassificationResult.model_validate(agent_result)
    )
    if (
        result.certainty != "certain"
        or result.category != action_plan.category
        or result.confidence != action_plan.confidence
    ):
        raise ValueError("Agent correction and ActionPlan diverge")
    return result.model_dump_json()


def _normalized_message_id(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("message ID must be text")
    locator = EmailProviderLocator.model_validate(
        {
            "account_id": "message-id-normalizer",
            "folder": "message-id-normalizer",
            "uidvalidity": 1,
            "uid": 1,
            "rfc_message_id": value,
        }
    )
    return locator.rfc_message_id or ""


def _normalized_message_ids(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes | bytearray):
        raise ValueError("references must be a sequence of message IDs")
    normalized = tuple(
        message_id for value in values if (message_id := _normalized_message_id(value))
    )
    return tuple(dict.fromkeys(normalized))


def _json_dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _config_source_rowid(db: sqlite3.Connection, kind: str, field: str, value: object) -> int | None:
    """Resolve a public Email key to the exact source rowid in a current config projection."""
    row = db.execute(
        f"select rowid from ({config_projection(kind, include_rowid=True)}) where {field}=?",
        (value,),
    ).fetchone()
    return int(row["rowid"]) if row is not None else None


def _folder_binding_rowid(db: sqlite3.Connection, account_id: str, category_key: str) -> int | None:
    row = db.execute(
        f"select rowid from ({config_projection('email_category_folder_bindings', include_rowid=True)}) "
        "where account_id=? and category_key=?",
        (account_id, category_key),
    ).fetchone()
    return int(row["rowid"]) if row is not None else None


def _upsert_current_folder_binding(db: sqlite3.Connection, category_key: str,
                                   binding: VerifiedEmailFolderBinding) -> None:
    values = {
        "account_id": binding.account_id, "category_key": category_key,
        "provider_folder_id": binding.provider_folder_id,
        "provider_folder_name": binding.provider_folder_name,
        "binding_status": binding.binding_status,
        "last_verified_at": binding.last_verified_at,
    }
    identity = _folder_binding_rowid(db, binding.account_id, category_key)
    if identity is None:
        insert_config(db, "email_category_folder_bindings", values)
    else:
        update_config(db, "email_category_folder_bindings", identity, values)


def _historical_candidate_projection(
    message: Mapping[str, object],
    *,
    stable_message_identity: str,
    account_id: str,
    folder: str,
) -> dict[str, object]:
    """Keep only provider coordinates needed for an exact later reread."""

    if str(message.get("accountId") or "") != account_id:
        raise ValueError("historical candidate account does not match queue")
    if str(message.get("folder") or "") != folder:
        raise ValueError("historical candidate folder does not match queue")
    message_identity = str(message.get("stableMessageIdentity") or "")
    if message_identity and message_identity != stable_message_identity:
        raise ValueError("historical candidate identity does not match queue")
    projection: dict[str, object] = {
        "accountId": account_id,
        "folder": folder,
        "uidValidity": int(message.get("uidValidity") or 0),
        "uid": int(message.get("uid") or 0),
        "messageId": str(message.get("messageId") or "") or None,
        "threadId": str(message.get("threadId") or "") or None,
        "stableMessageIdentity": stable_message_identity,
    }
    EmailProviderLocator.model_validate(
        {
            "account_id": projection["accountId"],
            "folder": projection["folder"],
            "uidvalidity": projection["uidValidity"],
            "uid": projection["uid"],
            "rfc_message_id": projection["messageId"],
            "thread_id": projection["threadId"],
        }
    )
    return projection


def _historical_candidate_list_item(row: sqlite3.Row) -> dict[str, Any]:
    from app.email_historical_candidate_schema import reconstruct_projection

    return {
        **dict(row),
        "normalized_input_hash": row["normalized_text"],
        "provider_message": reconstruct_projection(row),
    }






def _freeze_action_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_action_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_action_value(item) for item in value)
    return value


def _freeze_action_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    return MappingProxyType(
        {str(key): _freeze_action_value(value) for key, value in parameters.items()}
    )


def _required_utc_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _next_attempt_at(
    finished_at: str,
    *,
    attempt_number: int,
    retryable: bool,
) -> str:
    if not retryable or attempt_number >= DIRECT_ACTION_MAX_ATTEMPTS:
        return ""
    parsed = datetime.fromisoformat(finished_at)
    delay = DIRECT_ACTION_RETRY_BASE_SECONDS * (2 ** (attempt_number - 1))
    return (parsed + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _retry_is_due(next_attempt_at: object, claimed_at: str) -> bool:
    if not next_attempt_at:
        return False
    try:
        return (
            _required_utc_timestamp(str(next_attempt_at), field="next_attempt_at")
            <= claimed_at
        )
    except ValueError as exc:
        raise EmailPersistenceCorruption(
            "invalid direct action retry timestamp"
        ) from exc


def _explicit_direct_retry_sql(alias: str, attempt_offset: int = 0) -> str:
    return (
        f"exists(select 1 from ({EMAIL_RETRY_SQL}) retry_request "
        f"where retry_request.action_id={alias}.action_id "
        f"and retry_request.attempt_number={alias}.attempt_count+{attempt_offset})"
    )


def _direct_retry_configuration_sql(alias: str) -> str:
    return (
        f"(NOT {_explicit_direct_retry_sql(alias, 1)} OR EXISTS("
        f"select 1 from ({EMAIL_RETRY_SQL}) r "
        f"join ({config_projection('email_accounts')}) cfg "
        f"on cfg.account_id={alias}.account_id where r.action_id={alias}.action_id "
        f"and r.attempt_number={alias}.attempt_count+1 "
        "and r.account_configuration_snapshot=json_array(cfg.account_id,cfg.email_address,"
        "cfg.imap_host,cfg.imap_port,cfg.imap_username,cfg.imap_secret_reference,"
        "cfg.imap_move_mode,cfg.imap_tls,cfg.enabled)))"
    )


def _direct_action_failed_retryable(
    row: sqlite3.Row, *, claimed_at: str,
    explicit_retry_attempt: bool = False, explicit_retry_scheduled: bool = False,
) -> bool:
    if row["status"] != "failed":
        return False
    attempt_count = int(row["attempt_count"])
    if explicit_retry_scheduled:
        return _retry_is_due(row["next_attempt_at"], claimed_at)
    if explicit_retry_attempt:
        return False
    if attempt_count < DIRECT_ACTION_MAX_ATTEMPTS:
        return _retry_is_due(row["next_attempt_at"], claimed_at)
    if not _exhausted_direct_action_is_service_transient(row):
        return False
    finished_at = _required_utc_timestamp(str(row["finished_at"]), field="finished_at")
    retry_at = datetime.fromisoformat(finished_at) + timedelta(
        seconds=DIRECT_ACTION_EXHAUSTED_TRANSIENT_RETRY_SECONDS
    )
    return retry_at.isoformat(timespec="seconds") <= claimed_at


def _failed_action_retriable_sql(alias: str) -> str:
    """The boolean SQL twin of `_direct_action_retry_outlook`'s verdict.

    Both read the same four columns of a failed `email_actions` row and must
    agree: this one lets the list and the progress counts filter and split by
    it, the Python one explains a single row in the console.
    """

    return (
        f"(({alias}.next_attempt_at != '' AND ("
        f"{_explicit_direct_retry_sql(alias, 1)} OR ("
        f"{alias}.attempt_count < {DIRECT_ACTION_MAX_ATTEMPTS} "
        f"AND NOT {_explicit_direct_retry_sql(alias)})))"
        f" OR ({alias}.attempt_count >= {DIRECT_ACTION_MAX_ATTEMPTS}"
        f" AND NOT {_explicit_direct_retry_sql(alias)}"
        f" AND (("
        f"{alias}.provider_operation='provider_factory'"
        f" AND {alias}.error LIKE 'provider_factory_failed:%')"
        f" OR ({alias}.provider_operation='startup_recovery'"
        f" AND {alias}.error='stale_processing_recovered')"
        f" OR ({alias}.action_type='trash' AND {alias}.provider_operation='READ'"
        f" AND {alias}.error='provider_read_failed:ImapMessageUnavailable')"
        f"))) AND {_direct_retry_configuration_sql(alias)}"
    )


def _direct_action_retry_outlook(
    row: sqlite3.Row, *, explicit_retry_attempt: bool = False,
    explicit_retry_scheduled: bool = False,
    explicit_retry_configuration_matches: bool = True,
) -> dict[str, object]:
    """Whether a failed direct action will be tried again, and why or why not.

    The same rules `_direct_action_failed_retryable` applies when it claims the
    action, minus the clock: a retry that is only not yet due is still coming.
    """

    attempts = int(row["attempt_count"])
    if explicit_retry_scheduled and not explicit_retry_configuration_matches:
        return {"retriable": False, "retry_note": "账户配置已变化，原恢复授权不能执行"}
    if explicit_retry_scheduled and row["next_attempt_at"]:
        return {"retriable": True, "retry_note": "已安排一次明确恢复，不重置失败次数"}
    if explicit_retry_attempt:
        return {"retriable": False, "retry_note": "本次明确恢复已结束，不自动续试"}
    if attempts < DIRECT_ACTION_MAX_ATTEMPTS:
        if row["next_attempt_at"]:
            return {
                "retriable": True,
                "retry_note": f"将自动重试（已试 {attempts}/{DIRECT_ACTION_MAX_ATTEMPTS} 次）",
            }
        return {"retriable": False, "retry_note": "这个错误不可重试"}
    if _exhausted_direct_action_is_service_transient(row):
        return {
            "retriable": True,
            "retry_note": f"已试 {attempts} 次，稍后仍会自动再试",
        }
    return {
        "retriable": False,
        "retry_note": f"已试 {attempts} 次仍失败，不再自动重试",
    }


def _exhausted_direct_action_is_service_transient(row: sqlite3.Row) -> bool:
    provider_operation = str(row["provider_operation"])
    error = str(row["error"])
    if provider_operation == "provider_factory" and error.startswith(
        "provider_factory_failed:"
    ):
        return True
    if (
        provider_operation == "startup_recovery"
        and error == "stale_processing_recovered"
    ):
        # A restart interrupted the claim before the provider answered, so this
        # attempt learned nothing about the action and must not end it.
        return True
    provider_read_transient = error == "provider_read_failed:ImapMessageUnavailable"
    return (
        str(row["action_type"]) in {
            EmailAction.MOVE.value,
            EmailAction.TRASH.value,
        }
        and provider_operation == "READ"
        and provider_read_transient
    )


def _json_load(raw: str, *, field: str, expected_type: type[Any]) -> Any:
    try:
        value = json.loads(raw)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise EmailPersistenceCorruption(f"invalid {field} JSON") from exc
    if not isinstance(value, expected_type):
        raise EmailPersistenceCorruption(
            f"{field} must contain a JSON {expected_type.__name__}"
        )
    return value




def _schema_metadata_text(value: object, *, field: str, kind: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise EmailPersistenceCorruption(f"invalid schema {kind} metadata: {field}")
    return value


def _schema_identifier(value: object, *, field: str) -> str:
    return _schema_metadata_text(value, field=field, kind="identifier").casefold()














def _require_positive_int(value: int, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _validate_feedback_request_id(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("feedback_request_id must be a non-empty stable identifier")
    if len(value) > 200:
        raise ValueError("feedback_request_id must be at most 200 characters")
    return value


def _validate_expected_action_plan_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            "expected_current_action_plan_id must be null or a non-empty identifier"
        )
    return value


def _validate_opaque_provider_identifier(
    value: str,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if not value:
        if allow_empty:
            return value
        raise ValueError(f"{field} must be non-empty")
    if (
        value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or _OPAQUE_PROVIDER_ID.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a bounded opaque identifier")
    return value


def _validate_provider_location(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise ValueError(f"{field} must be a bounded single-line provider location")
    return value


def _validate_unsubscribe_opaque(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
        or _UNSUBSCRIBE_OPAQUE_ID.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a bounded opaque unsubscribe reference")
    try:
        assert_no_credentials(value)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a bounded opaque unsubscribe reference"
        ) from exc
    return value


def is_valid_unsubscribe_opaque_reference(value: object) -> bool:
    """Return whether a durable unsubscribe reference is bounded and opaque."""

    try:
        _validate_unsubscribe_opaque(value, field="unsubscribe_reference")
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _validate_durable_unsubscribe_result_text(
    result_text: object,
    observation_digest: object,
    result_text_truncated: object,
    result_text_digest: object,
) -> None:
    """Require the stored display text to already be canonical and redacted."""

    if (
        not isinstance(result_text, str)
        or not isinstance(observation_digest, str)
        or not isinstance(result_text_digest, str)
        or not isinstance(result_text_truncated, int)
        or result_text_truncated not in {0, 1}
    ):
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result integrity metadata is invalid"
        )
    encoded_length = len(result_text.encode("utf-8"))
    if encoded_length > _MAX_UNSUBSCRIBE_RESULT_TEXT_BYTES:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result text exceeds its durable bound"
        )
    from app.email_unsubscribe import normalize_unsubscribe_result_text

    canonical_text, bounded_digest = normalize_unsubscribe_result_text(result_text)
    if canonical_text != result_text:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt result text is not canonical and redacted"
        )
    if not result_text:
        if observation_digest or result_text_digest or result_text_truncated:
            raise EmailPersistenceCorruption(
                "unsubscribe receipt empty result has non-empty integrity metadata"
            )
        return
    if (
        _SHA256_HEX.fullmatch(result_text_digest) is None
        or result_text_digest != bounded_digest
    ):
        raise EmailPersistenceCorruption(
            "unsubscribe receipt bounded result digest does not match"
        )
    if _SHA256_HEX.fullmatch(observation_digest) is None:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt observation digest is invalid"
        )
    if not result_text_truncated and observation_digest != result_text_digest:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt untruncated observation digest does not match"
        )
    if result_text_truncated and observation_digest == result_text_digest:
        raise EmailPersistenceCorruption(
            "unsubscribe receipt truncated observation digest is not distinct"
        )


def _audited_unsubscribe_run_chain(
    db: sqlite3.Connection,
    *,
    task_id: int,
    execution_generation: str,
    final_audit_run_id: int,
) -> tuple[list[int], list[int]] | None:
    rows = db.execute(
        f"""
        select id, role, proposal_revision, turn_attempt,
               parent_agent_run_id, operation_id
        from ({AGENT_RUN_SQL}) as agent_runs
        where reply_task_id=? and execution_generation=?
        """,
        (task_id, execution_generation),
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    current = by_id.get(final_audit_run_id)
    if current is None or current["role"] != "audit":
        return None
    chain: list[sqlite3.Row] = []
    seen: set[int] = set()
    while current is not None:
        run_id = int(current["id"])
        if run_id in seen:
            return None
        seen.add(run_id)
        chain.append(current)
        role = str(current["role"])
        revision = int(current["proposal_revision"])
        parent_id = current["parent_agent_run_id"]
        operation_id = str(current["operation_id"])
        if role == "audit":
            if not operation_id or not isinstance(parent_id, int):
                return None
            parent = by_id.get(parent_id)
            if (
                parent is None
                or parent["role"] != "consumer"
                or int(parent["proposal_revision"]) != revision
            ):
                return None
            current = parent
            continue
        if role != "consumer" or operation_id:
            return None
        if revision == 0:
            if parent_id is not None:
                return None
            current = None
            continue
        if not isinstance(parent_id, int):
            return None
        parent = by_id.get(parent_id)
        if (
            parent is None
            or parent["role"] != "audit"
            or int(parent["proposal_revision"]) != revision - 1
        ):
            return None
        current = parent

    chain.reverse()
    return (
        [int(row["id"]) for row in chain if row["role"] == "consumer"],
        [int(row["id"]) for row in chain if row["role"] == "audit"],
    )


def _audited_unsubscribe_lineage(
    db: sqlite3.Connection,
    *,
    receipt: sqlite3.Row,
    classification: sqlite3.Row,
) -> dict[str, object] | None:
    lineage = db.execute(
        _AUDITED_UNSUBSCRIBE_LINEAGE_SQL,
        (receipt["action_identity"], receipt["effect_digest"]),
    ).fetchone()
    if lineage is None or lineage["audit_agent_run_id"] is None:
        return None
    current_plan = db.execute(
        f"""
        select action_plan_id, action_plan_version, classification_id,
               account_id, actions_json
        from ({EMAIL_PLAN_SQL})
        where action_plan_id=?
        """,
        (classification["current_action_plan_id"],),
    ).fetchone()
    if current_plan is None:
        return None
    try:
        payload = json.loads(
            AutoReplyStore._compact_reply_input_json(lineage["input_provenance_json"])
            if lineage["input_compacted"] else lineage["trigger_message_json"]
        )
        plan_actions = _json_load(
            current_plan["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        from app.email_task_adapter import email_conversation_id

        expected_conversation_id = email_conversation_id(
            str(receipt["account_id"]),
            str(receipt["thread_identity"]),
        )
        expected_action_identity = email_action_identity(
            account_id=str(receipt["account_id"]),
            stable_message_identity=str(receipt["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(receipt["action_plan_version"]),
        )
    except (TypeError, ValueError, RecursionError, KeyError):
        return None
    if not isinstance(payload, dict):
        return None
    expected_payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": EmailAction.UNSUBSCRIBE.value,
        "action_identity": receipt["action_identity"],
        "action_plan_id": receipt["action_plan_id"],
        "action_plan_version": receipt["action_plan_version"],
        "classification_id": receipt["classification_id"],
        "account_id": receipt["account_id"],
        "stable_message_identity": receipt["stable_message_identity"],
        "thread_identity": receipt["thread_identity"],
    }
    if any(payload.get(key) != value for key, value in expected_payload.items()):
        return None
    integer_payload_fields = ("action_plan_version", "classification_id")
    if any(
        not isinstance(payload.get(field), int) or isinstance(payload.get(field), bool)
        for field in integer_payload_fields
    ):
        return None
    task_id = int(lineage["task_id"])
    audit_run_id = int(lineage["audit_agent_run_id"])
    # The generation the Audit run that earned this receipt ran in, which is
    # what the chain has to be rebuilt against. The task's own generation moves
    # every time anything reruns it, and a receipt does not become someone
    # else's because the task was rerun.
    lineage_generation = str(lineage["audit_execution_generation"])
    if (
        receipt["action_identity"] != expected_action_identity
        or lineage["effect_action_identity"] != receipt["action_identity"]
        or lineage["effect_digest"] != receipt["effect_digest"]
        or lineage["audit_run_id"] != audit_run_id
        or lineage["audit_role"] != "audit"
        or lineage["reply_task_id"] != task_id
        or not lineage_generation
        or lineage["task_channel"] != "email"
        or lineage["task_conversation_id"] != expected_conversation_id
        or lineage["trigger_message_id"] != receipt["action_identity"]
        or classification["id"] != receipt["classification_id"]
        or classification["account_id"] != receipt["account_id"]
        or classification["stable_message_identity"]
        != receipt["stable_message_identity"]
        or classification["message_thread_identity"] != receipt["thread_identity"]
        or classification["current_action_plan_id"] != receipt["action_plan_id"]
        or current_plan["action_plan_id"] != receipt["action_plan_id"]
        or current_plan["action_plan_version"] != receipt["action_plan_version"]
        or current_plan["classification_id"] != receipt["classification_id"]
        or current_plan["account_id"] != receipt["account_id"]
        or EmailAction.UNSUBSCRIBE.value not in plan_actions
    ):
        return None
    run_chain = _audited_unsubscribe_run_chain(
        db,
        task_id=task_id,
        execution_generation=lineage_generation,
        final_audit_run_id=audit_run_id,
    )
    if run_chain is None:
        return None
    consumer_run_ids, audit_run_ids = run_chain
    attempt_ids = _email_unsubscribe_attempt_ids(
        db,
        conversation_id=expected_conversation_id,
        action_identity=str(receipt["action_identity"]),
    )
    return {
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "task_id": task_id,
        "task_status": str(lineage["task_status"]),
        "consumer_run_ids": consumer_run_ids,
        "audit_run_ids": audit_run_ids,
        "attempt_ids": attempt_ids,
    }


def _email_unsubscribe_attempt_ids(
    db: sqlite3.Connection,
    *,
    conversation_id: str,
    action_identity: str,
) -> list[int]:
    from app.action_storage import REPLY_ATTEMPT_COMMON_SQL
    if db.execute(
        "select 1 from sqlite_master where type='table' and name='actions'"
    ).fetchone() is None:
        return []
    return [
        int(row["id"])
        for row in db.execute(
            f"""
            select id
            from ({REPLY_ATTEMPT_COMMON_SQL})
            where channel='email'
              and conversation_id=?
              and trigger_message_id=?
            order by id
            """,
            (conversation_id, action_identity),
        ).fetchall()
    ]


def _current_unsubscribe_task_lineage(
    db: sqlite3.Connection,
    *,
    task: sqlite3.Row,
    classification: sqlite3.Row,
) -> dict[str, object] | None:
    current_plan = db.execute(
        f"""
        select action_plan_id, action_plan_version, classification_id,
               account_id, actions_json
        from ({EMAIL_PLAN_SQL})
        where action_plan_id=?
        """,
        (classification["current_action_plan_id"],),
    ).fetchone()
    if current_plan is None:
        return None
    try:
        payload = json.loads(
            AutoReplyStore._compact_reply_input_json(task["input_provenance_json"])
            if task["input_compacted"] else str(task["trigger_message_json"])
        )
        actions = _json_load(
            current_plan["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        thread_identity = str(classification["message_thread_identity"] or "")
        from app.email_task_adapter import email_conversation_id

        expected_action_identity = email_action_identity(
            account_id=str(classification["account_id"]),
            stable_message_identity=str(classification["stable_message_identity"]),
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(current_plan["action_plan_version"]),
        )
        expected_conversation_id = email_conversation_id(
            str(classification["account_id"]),
            thread_identity,
        )
    except (TypeError, ValueError, RecursionError, KeyError):
        return None
    if not isinstance(payload, dict) or EmailAction.UNSUBSCRIBE.value not in actions:
        return None
    expected_payload = {
        "schema": "email_agent_action.v1",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_type": EmailAction.UNSUBSCRIBE.value,
        "action_identity": expected_action_identity,
        "action_plan_id": current_plan["action_plan_id"],
        "action_plan_version": current_plan["action_plan_version"],
        "classification_id": current_plan["classification_id"],
        "account_id": current_plan["account_id"],
        "stable_message_identity": classification["stable_message_identity"],
        "thread_identity": thread_identity,
    }
    if (
        any(payload.get(field) != value for field, value in expected_payload.items())
        or task["trigger_message_id"] != expected_action_identity
        or task["conversation_id"] != expected_conversation_id
        or not str(task["execution_generation"] or "").strip()
    ):
        return None
    runs = db.execute(
        f"""
        select id, role
        from ({AGENT_RUN_SQL}) as agent_runs
        where reply_task_id=? and execution_generation=?
        order by id
        """,
        (task["id"], task["execution_generation"]),
    ).fetchall()
    continuation_row = db.execute(
        f"""
        select continuation.controls_json, effects.operations_json
        from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL}) as continuation
        join ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) as effects
          on effects.action_identity=continuation.action_identity
         and effects.effect_digest=continuation.effect_digest
        where continuation.action_identity=?
        """,
        (expected_action_identity,),
    ).fetchone()
    continuation: dict[str, object] | None = None
    if continuation_row is not None:
        controls = _json_load(
            continuation_row["controls_json"],
            field="controls_json",
            expected_type=list,
        )
        operations = _json_load(
            continuation_row["operations_json"],
            field="operations_json",
            expected_type=list,
        )
        control_kinds = sorted(
            {
                str(control["kind"])
                for control in controls
                if isinstance(control, dict) and "kind" in control
            }
        )
        continuation = {
            "state": "awaiting_audit",
            "control_kinds": control_kinds,
            "operation_count": len(operations),
            "requires_human": bool(
                {"captcha_handoff", "credential_handoff"} & set(control_kinds)
            ),
        }
    result = {
        "kind": "unsubscribe",
        "operation": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "task_id": int(task["id"]),
        "task_status": str(task["status"]),
        "consumer_run_ids": [
            int(row["id"]) for row in runs if row["role"] == "consumer"
        ],
        "audit_run_ids": [int(row["id"]) for row in runs if row["role"] == "audit"],
        "status": str(task["status"]),
        # A task that failed before any page produced a receipt has no
        # outcome to explain it (unsubscribeStateLabel only maps outcomes):
        # its own error is the only record of why, so it rides along here.
        "error": str(task["error"] or ""),
        "_sort_created_at": str(task["created_at"] or ""),
    }
    if continuation is not None:
        result["continuation"] = continuation
    return result


def _validate_unsubscribe_operations(
    operations: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise TypeError("unsubscribe operations must be a sequence")
    approved_kinds = {
        "post_one_click",
        "open_entry",
        "follow_redirect",
        "submit_form",
        "click_confirmation",
        "confirm_email",
        "reconcile_handoff",
    }
    validated: list[dict[str, str]] = []
    for operation in operations:
        if not isinstance(operation, Mapping) or set(operation) != {
            "operation_reference",
            "kind",
            "target_reference",
        }:
            raise ValueError("unsubscribe operation has invalid fields")
        kind = operation["kind"]
        if kind not in approved_kinds:
            raise ValueError("unsubscribe operation kind is invalid")
        validated.append(
            {
                "operation_reference": _validate_unsubscribe_opaque(
                    operation["operation_reference"], field="operation_reference"
                ),
                "kind": str(kind),
                "target_reference": _validate_unsubscribe_opaque(
                    operation["target_reference"], field="target_reference"
                ),
            }
        )
    if not validated or len({item["operation_reference"] for item in validated}) != len(
        validated
    ):
        raise ValueError("unsubscribe operations must be non-empty and unique")
    if len(validated) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
        raise ValueError("durable continuation operation limit exceeded")
    return validated


def _validate_terminal_expected_effect(
    value: Mapping[str, object],
) -> dict[str, object]:
    required = {
        "action_identity",
        "action_plan_id",
        "action_plan_version",
        "classification_id",
        "account_id",
        "stable_message_identity",
        "thread_identity",
        "entry_reference",
        "operations",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("expected terminal effect fields are invalid")
    binding = _validate_unsubscribe_binding(
        action_identity=value["action_identity"],
        effect_digest="0" * 64,
        action_plan_id=value["action_plan_id"],
        action_plan_version=value["action_plan_version"],
        classification_id=value["classification_id"],
        account_id=value["account_id"],
        stable_message_identity=value["stable_message_identity"],
        thread_identity=value["thread_identity"],
        entry_reference=value["entry_reference"],
    )
    operations = _validate_unsubscribe_operations(value["operations"])
    return {
        **{key: binding[key] for key in binding if key != "effect_digest"},
        "operations": operations,
    }


def _validate_unsubscribe_controls(
    controls: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(controls, Sequence) or isinstance(controls, (str, bytes)):
        raise TypeError("unsubscribe controls must be a sequence")
    validated: list[dict[str, str]] = []
    for control in controls:
        if not isinstance(control, Mapping) or set(control) != {
            "reference",
            "kind",
            "intent",
        }:
            raise ValueError("unsubscribe control has invalid fields")
        if control["kind"] not in {
            "form",
            "link",
            "button",
            "confirmation_email",
            "email_otp",
            "captcha_handoff",
            "credential_handoff",
        }:
            raise ValueError("unsubscribe control kind is invalid")
        if control["intent"] not in {"continue", "unsubscribe", "confirm"}:
            raise ValueError("unsubscribe control intent is invalid")
        validated.append(
            {
                "reference": _validate_unsubscribe_opaque(
                    control["reference"], field="control_reference"
                ),
                "kind": str(control["kind"]),
                "intent": str(control["intent"]),
            }
        )
    if not validated or len({item["reference"] for item in validated}) != len(
        validated
    ):
        raise ValueError("unsubscribe controls must be non-empty and unique")
    return validated


_MAX_UNSUBSCRIBE_ENTRY_URL_BYTES = 2048


def _validate_unsubscribe_entry_url(value: object, *, entry_reference: str) -> str:
    """Return the unsubscribe URL a receipt was earned against, or ''.

    This is the one durable unsubscribe field that is not a redacted
    reference: Derek asked for the address itself so a receipt can be
    reproduced by hand, which means the stored value carries whatever query
    and token the provider put in the link, and opening it performs a real
    unsubscribe. It is therefore not run through ``assert_no_credentials``,
    which rejects exactly those tokens.

    What is enforced is binding, not a second opinion about which URLs are
    acceptable: the value's sha256 must equal the digest in
    ``entry_reference``. Only ``unsubscribe_entry_reference`` mints those
    digests, and it already refuses anything but a private HTTPS, loopback
    HTTP or mailto entry, so restating that rule here could only disagree with
    it - and did, rejecting the loopback entries that rule allows.
    """

    if value is None or value == "":
        return ""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_UNSUBSCRIBE_ENTRY_URL_BYTES
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("entry_url must be a bounded single-line URL")
    expected = entry_reference.removeprefix("unsubscribe-entry:")
    if sha256(value.encode("utf-8")).hexdigest() != expected:
        raise ValueError("entry_url does not match the receipt entry reference")
    return value


def _validate_unsubscribe_binding(
    *,
    action_identity: object,
    effect_digest: object,
    action_plan_id: object,
    action_plan_version: object,
    classification_id: object,
    account_id: object,
    stable_message_identity: object,
    thread_identity: object,
    entry_reference: object,
) -> dict[str, object]:
    text = {
        "action_identity": action_identity,
        "effect_digest": effect_digest,
        "action_plan_id": action_plan_id,
        "account_id": account_id,
        "stable_message_identity": stable_message_identity,
        "thread_identity": thread_identity,
    }
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\r" in value
        or "\n" in value
        for value in text.values()
    ):
        raise ValueError("unsubscribe binding fields must be non-empty text")
    if not re.fullmatch(r"[0-9a-f]{64}", str(effect_digest)):
        raise ValueError("effect_digest must be canonical sha256 hex")
    _require_positive_int(action_plan_version, field="action_plan_version")
    _require_positive_int(classification_id, field="classification_id")
    entry_reference = _validate_unsubscribe_opaque(
        entry_reference,
        field="entry_reference",
    )
    return {
        **text,
        "action_plan_version": action_plan_version,
        "classification_id": classification_id,
        "entry_reference": entry_reference,
    }


def _validate_email_reply_owner(owner: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(owner, Mapping):
        raise TypeError("email reply owner must be a mapping")
    owner_id = _validate_opaque_provider_identifier(
        owner.get("owner_id"),
        field="owner_id",
    )
    lease_token = _validate_opaque_provider_identifier(
        owner.get("lease_token"),
        field="lease_token",
    )
    generation = owner.get("generation")
    _require_positive_int(generation, field="owner_generation")
    return {
        "owner_id": owner_id,
        "generation": generation,
        "lease_token": lease_token,
    }


def _validate_email_unsubscribe_owner(owner: Mapping[str, object]) -> dict[str, object]:
    try:
        return _validate_email_reply_owner(owner)
    except (TypeError, ValueError) as exc:
        raise type(exc)(str(exc).replace("email reply", "email unsubscribe")) from exc


def email_action_identity(
    *,
    account_id: str,
    stable_message_identity: str,
    action_type: EmailAction,
    action_plan_version: int,
) -> str:
    """Identify one immutable email action across scans and restarts."""

    account_id = account_id.strip()
    stable_message_identity = stable_message_identity.strip()
    action_type = EmailAction(action_type)
    if not account_id or not stable_message_identity:
        raise ValueError("email action identity fields must be non-empty")
    if action_plan_version <= 0:
        raise ValueError("action_plan_version must be positive")
    canonical = json.dumps(
        {
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "action_type": action_type.value,
            "action_plan_version": action_plan_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"email-action:{sha256(canonical.encode('utf-8')).hexdigest()}"


def email_unsubscribe_effect_digest(
    *,
    action_identity: str,
    action_plan_id: str,
    action_plan_version: int,
    classification_id: int,
    account_id: str,
    stable_message_identity: str,
    thread_identity: str,
    entry_reference: str,
    operations: Sequence[Mapping[str, object]],
    previous_effect_digest: str = "",
) -> str:
    """Hash the complete immutable, Audit-accepted unsubscribe effect."""

    validated_operations = _validate_unsubscribe_operations(operations)
    binding = _validate_unsubscribe_binding(
        action_identity=action_identity,
        effect_digest="0" * 64,
        action_plan_id=action_plan_id,
        action_plan_version=action_plan_version,
        classification_id=classification_id,
        account_id=account_id,
        stable_message_identity=stable_message_identity,
        thread_identity=thread_identity,
        entry_reference=entry_reference,
    )
    binding.pop("effect_digest")
    if (
        previous_effect_digest
        and re.fullmatch(r"[0-9a-f]{64}", previous_effect_digest) is None
    ):
        raise ValueError("previous_effect_digest must be canonical sha256 hex")
    digest_payload: dict[str, object] = {
        **binding,
        "operations": validated_operations,
    }
    if previous_effect_digest:
        digest_payload["previous_effect_digest"] = previous_effect_digest
    canonical = _json_dump(digest_payload)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _direct_action_id(action_plan_id: str, action: EmailAction) -> str:
    digest = sha256(f"{action_plan_id}:{action.value}".encode("utf-8")).hexdigest()
    return f"email-action:{digest}"


def email_imap_configuration_snapshot(account: Mapping[str, object]) -> str:
    fields = ("account_id", "email_address", "imap_host", "imap_port",
              "imap_username", "imap_secret_reference", "imap_move_mode")
    values = [account[key] for key in fields]
    values.extend((int(bool(account["imap_tls"])), int(bool(account["enabled"]))))
    return _json_dump(values)




class EmailStore:
    """Persist messages, classifier results, immutable plans, and direct actions."""

    def __init__(
        self, path: Path, *, validate_rows: bool = True,
        training_run_id: str | None = None,
    ):
        self.path = Path(path)
        self.training_run_id = training_run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        require_current_pair(self.path)
        # The path flock is non-reentrant. Store must finish before Email locks;
        # Store creates only the neutral classification parent, never EmailStore.
        AutoReplyStore(self.path)
        with schema_initialize_lock(self.path):
            require_current_pair(self.path)
            durable_rows_validated = self._initialize(validate_rows=False)
        if validate_rows and not durable_rows_validated:
            with self._connect() as db:
                self._validate_durable_state(db)

    def _open_connection(self, *, foreign_keys: bool = True) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("pragma busy_timeout = 30000")
        db.execute("pragma foreign_keys = " + ("on" if foreign_keys else "off"))
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        return db

    @contextmanager
    def _connect(self, *, foreign_keys: bool = True) -> Iterator[sqlite3.Connection]:
        db = self._open_connection(foreign_keys=foreign_keys)
        started_at = monotonic()
        body_finished_at = started_at
        try:
            with db:
                try:
                    yield db
                finally:
                    body_finished_at = monotonic()
        finally:
            close_started_at = monotonic()
            db.close()
            closed_at = monotonic()
            elapsed = closed_at - started_at
            if elapsed >= 1.0:
                caller = sys._getframe(2)
                logging.getLogger(__name__).warning(
                    "slow email sqlite context elapsed_seconds=%.3f "
                    "body_seconds=%.3f finish_seconds=%.3f close_seconds=%.3f "
                    "caller=%s:%s:%s",
                    elapsed,
                    body_finished_at - started_at,
                    close_started_at - body_finished_at,
                    closed_at - close_started_at,
                    Path(caller.f_code.co_filename).name,
                    caller.f_lineno,
                    caller.f_code.co_name,
                )
                del caller


    def _initialize(self, *, validate_rows: bool = True) -> bool:
        with self._connect(foreign_keys=False) as db:
            db.execute("begin")
            latest_version = self._read_schema_version(db)
            if latest_version == EMAIL_SCHEMA_VERSION:
                if validate_rows:
                    self._validate_durable_state(db)
                else:
                    self._validate_schema_shape(db)
                    self._validate_account_configuration(db)
                return validate_rows
            if latest_version is not None:
                raise EmailPersistenceCorruption("unsupported Email source; paired schema release required")
            db.rollback()
            db.execute("begin immediate")
            create_current_email_migration_ledger(db)
            self._seed_current_fresh_state(db)
            db.execute("insert into email_schema_migrations(version,applied_at) values(?,?)", (EMAIL_SCHEMA_VERSION, self._now()))
            self._validate_durable_state(db)
            return True







    @staticmethod
    def _read_schema_version(db: sqlite3.Connection) -> int | None:
        migration_tables = {
            _schema_identifier(row[0], field="sqlite_master table name")
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        if "email_schema_migrations" not in migration_tables:
            return None
        column_names = {
            _schema_identifier(row["name"], field="pragma table_info column name")
            for row in db.execute("pragma table_info(email_schema_migrations)")
        }
        missing_columns = {"version", "applied_at"} - column_names
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise EmailPersistenceCorruption(
                "email_schema_migrations is missing required columns: " + missing
            )
        row = db.execute(
            "select coalesce(max(version), 0) from email_schema_migrations"
        ).fetchone()
        try:
            return int(row[0])
        except (IndexError, TypeError, ValueError, OverflowError) as exc:
            raise EmailPersistenceCorruption("invalid email schema version") from exc






































    def _seed_current_fresh_state(self, db: sqlite3.Connection) -> None:
        """Prepare only a new database; caller owns tables and transaction.

        Only the fresh Email constructor invokes this inside its schema transaction.
        Current reopen and verified-source migration must not invoke it.
        """
        if db.execute("select 1 from configs where record_type='email_category_configs'").fetchone() is None:
            now = self._now()
            has_enabled_account = db.execute(
                f"select 1 from ({config_projection('email_accounts')}) where enabled=1 limit 1"
            ).fetchone() is not None
            for category_key, definition in sorted(INITIAL_CATEGORY_CONFIGS.items()):
                insert_config(db, "email_category_configs", {
                    "category_key": category_key,
                    "display_name": str(definition["display_name"]),
                    "core_description": str(definition["core"]),
                    "include_json": _json_dump(list(definition["include"])),
                    "exclude_json": _json_dump(list(definition["exclude"])),
                    "threshold": SEEDED_THRESHOLD,
                    "actions_json": "[]",
                    "action_parameters_json": "{}",
                    "enabled": int(not has_enabled_account),
                    "description_version": DESCRIPTION_VERSION,
                    "config_version": SEEDED_CONFIG_VERSION,
                    "updated_at": now,
                })
        if db.execute("select 1 from configs where record_type='email_model_promotion_configs'").fetchone() is None:
            insert_config(db, "email_model_promotion_configs", {
                "config_version": str(uuid4()),
                "micro_f1_min": 0.95,
                "category_precision_min": 0.95,
                "category_validation_samples_min": 20,
                "p95_latency_max_ms": 500.0,
                "created_at": self._now(),
            })
        for row in db.execute(
            f"select * from ({config_projection('email_category_configs')}) order by category_key"
        ).fetchall():
            category_key = row["category_key"]
            if db.execute(
                f"select 1 from ({config_projection('email_category_description_revisions')}) "
                "where category_key=?", (category_key,)
            ).fetchone() is None:
                previous = db.execute(
                    f"select max(created_at) from ({config_projection('email_category_description_revisions')})"
                ).fetchone()[0]
                insert_config(db, "email_category_description_revisions", {
                    "revision_id": str(uuid4()),
                    "category_key": category_key,
                    "config_version": row["config_version"],
                    "config_json": _json_dump(self._category_config_row(row)),
                    "created_at": self._next_account_timestamp(previous),
                })





    def _next_model_control_timestamp(self, db: sqlite3.Connection, table: str) -> str:
        """Allocate insertion order while the caller holds BEGIN IMMEDIATE."""
        previous = db.execute(f"select max(created_at) from {table}").fetchone()[0]
        return self._next_account_timestamp(previous)

    def current_model_promotion_config(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({config_projection('email_model_promotion_configs')}) order by created_at desc limit 1"
            ).fetchone()
        if row is None:
            raise EmailPersistenceCorruption("missing model promotion config")
        return dict(row)

    def list_model_promotion_configs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    f"select * from ({config_projection('email_model_promotion_configs')}) order by created_at desc"
                )
            ]














    @staticmethod
    def _validate_model_promotion_values(
        *,
        micro_f1_min: float,
        category_precision_min: float,
        category_validation_samples_min: int,
        p95_latency_max_ms: float,
    ) -> None:
        for field, value, minimum, maximum in (
            ("micro_f1_min", micro_f1_min, 0, 1),
            ("category_precision_min", category_precision_min, 0, 1),
            ("p95_latency_max_ms", p95_latency_max_ms, 0, 1.7976931348623157e308),
        ):
            if (
                type(value) not in (int, float)
                or not minimum <= value <= maximum
                or not math.isfinite(value)
            ):
                raise ValueError(f"{field} must be a finite number in range")
        if p95_latency_max_ms <= 0:
            raise ValueError("p95_latency_max_ms must be positive")
        if (
            type(category_validation_samples_min) is not int
            or not 0 < category_validation_samples_min < 2**63
        ):
            raise ValueError(
                "category_validation_samples_min must be a positive SQLite integer"
            )

    def create_model_promotion_config(
        self,
        *,
        expected_current_version: str,
        micro_f1_min: float,
        category_precision_min: float,
        category_validation_samples_min: int,
        p95_latency_max_ms: float,
    ) -> dict[str, Any]:
        values = dict(
            micro_f1_min=micro_f1_min,
            category_precision_min=category_precision_min,
            category_validation_samples_min=category_validation_samples_min,
            p95_latency_max_ms=p95_latency_max_ms,
        )
        self._validate_model_promotion_values(**values)
        with self._connect() as db:
            db.execute("begin immediate")
            current = db.execute(
                f"select config_version from ({config_projection('email_model_promotion_configs')}) order by created_at desc limit 1"
            ).fetchone()
            if current is None or current["config_version"] != expected_current_version:
                raise ValueError("model promotion config changed")
            version = str(uuid4())
            insert_config(db, "email_model_promotion_configs", {
                "config_version": version, **values,
                "created_at": self._next_model_control_timestamp(
                    db, f"({config_projection('email_model_promotion_configs')})"
                ),
            })
            return dict(
                db.execute(
                    f"select * from ({config_projection('email_model_promotion_configs')}) where config_version=?",
                    (version,),
                ).fetchone()
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _account_now() -> datetime:
        return datetime.now(timezone.utc)

    def _next_account_timestamp(self, existing: str | None = None) -> str:
        candidate = self._account_now().astimezone(timezone.utc)
        if existing:
            previous = datetime.fromisoformat(existing)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if candidate <= previous:
                candidate = previous + timedelta(microseconds=1)
        return candidate.isoformat(timespec="microseconds")


    @classmethod
    def _validate_schema_shape(cls, db: sqlite3.Connection) -> None:
        if not schema_is_current(db, email_ledger=True):
            raise EmailPersistenceCorruption("required current Store/Email declaration is missing or malformed")


    def _validate_durable_state(self, db: sqlite3.Connection) -> None:
        self._validate_schema_shape(db)
        for row in db.execute(
            f"select r.*, a.attempt_count from ({EMAIL_RETRY_SQL}) r "
            f"join ({EMAIL_DIRECT_ACTION_SQL}) a on a.action_id=r.action_id"
        ):
            if row["attempt_number"] > row["attempt_count"] + 1:
                raise EmailPersistenceCorruption("retry request exceeds the next action attempt")
            _required_utc_timestamp(row["requested_at"], field="requested_at")
        try:
            self._validate_durable_rows(db)
        except IndexError as exc:
            raise EmailPersistenceCorruption(
                "durable email row is missing a required field"
            ) from exc

    def _validate_account_configuration(self, db: sqlite3.Connection) -> None:
        for row in db.execute(config_projection("email_accounts")):
            self._account_row(row)
            folders = _json_load(
                row["scan_folders_json"],
                field="scan_folders_json",
                expected_type=list,
            )
            if not folders or any(
                not isinstance(folder, str) or not folder.strip() for folder in folders
            ):
                raise EmailPersistenceCorruption(
                    f"scan_folders_json for account {row['account_id']} must contain "
                    "one or more folder names"
                )

    def _validate_durable_rows(self, db: sqlite3.Connection) -> None:
        configs = db.execute(config_projection("email_model_promotion_configs")).fetchall()
        if not configs:
            raise EmailPersistenceCorruption("missing model promotion config")
        try:
            for row in configs:
                self._validate_model_promotion_values(
                    **{
                        key: row[key]
                        for key in (
                            "micro_f1_min",
                            "category_precision_min",
                            "category_validation_samples_min",
                            "p95_latency_max_ms",
                        )
                    }
                )
            for row in db.execute(config_projection("email_category_description_revisions")):
                config = _json_load(
                    row["config_json"], field="config_json", expected_type=dict
                )
                self._validate_category_revision_snapshot(config)
                if (
                    config.get("category_key") != row["category_key"]
                    or config.get("config_version") != row["config_version"]
                ):
                    raise ValueError("category description revision identity mismatch")
        except ValueError as exc:
            raise EmailPersistenceCorruption(str(exc)) from exc
        for row in db.execute(
            f"""
            select stable_message_identity, status, owner, lease_expires_at,
                   json_type(input_json) as input_json_type,
                   json_type(input_json, '$.input_compacted') as input_compacted_type,
                   json_extract(input_json, '$.stable_message_identity')
                       as input_stable_message_identity,
                   json_type(input_json, '$.stable_message_identity')
                       as input_stable_message_identity_type,
                   json_type(input_json, '$.unsubscribe_candidates')
                       as unsubscribe_candidates_type,
                   json_extract(input_json, '$.unsubscribe_candidates')
                       as unsubscribe_candidates_json,
                   json_type(result_json) as result_json_type,
                   json_type(result_json, '$.unsubscribe_url')
                       as result_unsubscribe_url_type
            from ({_EMAIL_CLASSIFICATION_SQL})
            """
        ):
            if row["input_json_type"] != "object":
                raise EmailPersistenceCorruption(
                    "classifier task input is not a JSON object"
                )
            compact_done = row["status"] == "done" and row["input_compacted_type"] == "true"
            if compact_done and row["input_stable_message_identity_type"] is not None:
                raise EmailPersistenceCorruption(
                    "compacted classifier task input repeats stable identity"
                )
            if not compact_done and (
                row["input_stable_message_identity"]
                != row["stable_message_identity"]
            ):
                raise EmailPersistenceCorruption(
                    "classifier task stable identity diverges from input"
                )
            candidates_json = row["unsubscribe_candidates_json"]
            candidates = (
                json.loads(candidates_json)
                if row["unsubscribe_candidates_type"] == "array"
                and isinstance(candidates_json, str)
                else None
            )
            if not isinstance(candidates, list) or any(
                not isinstance(candidate, dict)
                or set(candidate) != {"index", "source", "digest", "reference"}
                or candidate["index"] != index
                or not isinstance(candidate["digest"], str)
                or not _SHA256_HEX.fullmatch(candidate["digest"])
                or candidate["reference"] != "unsubscribe-entry:" + candidate["digest"]
                for index, candidate in enumerate(candidates)
            ):
                raise EmailPersistenceCorruption(
                    "classifier task unsubscribe candidates are not redacted"
                )
            if (
                row["result_json_type"] == "object"
                and row["result_unsubscribe_url_type"] is not None
            ):
                raise EmailPersistenceCorruption(
                    "classifier task result contains private unsubscribe URL"
                )
            if row["status"] == "running" and (
                not row["owner"] or not row["lease_expires_at"]
            ):
                raise EmailPersistenceCorruption(
                    "running classifier task has no durable lease"
                )
            if row["status"] != "running" and (row["owner"] or row["lease_expires_at"]):
                raise EmailPersistenceCorruption(
                    "non-running classifier task retains a lease"
                )
        self._validate_account_configuration(db)

        messages: dict[str, sqlite3.Row] = {}
        for row in db.execute(
            f"""
            select stable_message_identity, account_id, folder, uidvalidity, uid,
                   rfc_message_id, thread_identity, recipients_json,
                   references_json, in_reply_to, attachment_metadata_json
            from ({EMAIL_MESSAGE_SQL})
            """
        ):
            recipients = _json_load(
                row["recipients_json"],
                field="recipients_json",
                expected_type=list,
            )
            if any(not isinstance(value, str) for value in recipients):
                raise EmailPersistenceCorruption("recipients_json must contain strings")
            references = _json_load(
                row["references_json"],
                field="references_json",
                expected_type=list,
            )
            if any(
                not isinstance(value, str)
                or not value
                or _normalized_message_id(value) != value
                for value in references
            ):
                raise EmailPersistenceCorruption(
                    "references_json must contain normalized message IDs"
                )
            if row["in_reply_to"] and (
                _normalized_message_id(row["in_reply_to"]) != row["in_reply_to"]
            ):
                raise EmailPersistenceCorruption(
                    "in_reply_to must contain a normalized message ID"
                )
            attachments = _json_load(
                row["attachment_metadata_json"],
                field="attachment_metadata_json",
                expected_type=list,
            )
            try:
                tuple(
                    EmailAttachmentMetadata.model_validate(item) for item in attachments
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid attachment_metadata_json for message {row['id']}"
                ) from exc
            messages[row["stable_message_identity"]] = row

        plans: dict[str, EmailActionPlan] = {}
        plans_by_classification: dict[int, list[EmailActionPlan]] = {}
        for row in db.execute(f"select * from ({EMAIL_PLAN_SQL})"):
            plan = self._stored_action_plan(row)
            plans[plan.action_plan_id] = plan
            plans_by_classification.setdefault(plan.classification_id, []).append(plan)
        for classification_id, stored_plans in plans_by_classification.items():
            ordered = sorted(plan.action_plan_version for plan in stored_plans)
            if ordered != list(range(1, ordered[-1] + 1)):
                raise EmailPersistenceCorruption(
                    f"non-contiguous ActionPlan versions for classification "
                    f"{classification_id}"
                )

        classifications = {
            row["id"]: row
            for row in db.execute(
                f"""
                select id, account_id, folder, uidvalidity, uid, rfc_message_id,
                       thread_id, stable_message_identity, category,
                       predicted_category, confirmed_category, confidence,
                       probabilities_json, model_id, config_version, status,
                       classification_source, agent_result_json,
                       current_action_plan_id
                from ({EMAIL_CLASSIFICATION_SQL})
                """
            )
        }
        classifications_by_identity: dict[str, list[sqlite3.Row]] = {}
        for row in classifications.values():
            classifications_by_identity.setdefault(
                row["stable_message_identity"], []
            ).append(row)
        for row in classifications.values():
            try:
                if row["category"] is not None:
                    rehydrate_legacy_email_category_key(row["category"])
                if row["predicted_category"] is not None:
                    rehydrate_legacy_email_category_key(row["predicted_category"])
                if row["confirmed_category"]:
                    rehydrate_legacy_email_category_key(row["confirmed_category"])
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid classification category for {row['id']}"
                ) from exc
            if row["status"] not in _CLASSIFICATION_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid classification status for {row['id']}"
                )
            if row["classification_source"] not in _CLASSIFICATION_SOURCES:
                raise EmailPersistenceCorruption(
                    f"invalid classification source for {row['id']}"
                )
            probabilities = _json_load(
                row["probabilities_json"],
                field="probabilities_json",
                expected_type=dict,
            )
            try:
                tuple(
                    rehydrate_legacy_email_category_key(category)
                    for category in probabilities
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid classification probabilities for {row['id']}"
                ) from exc
            if row["agent_result_json"] != "null":
                from app.email_classifier_agent import DurableAgentClassificationResult

                try:
                    agent_result = DurableAgentClassificationResult.model_validate_json(
                        row["agent_result_json"]
                    )
                except ValueError as exc:
                    raise EmailPersistenceCorruption(
                        f"invalid Agent result for classification {row['id']}"
                    ) from exc
                if row["classification_source"] == "model":
                    raise EmailPersistenceCorruption(
                        f"model classification {row['id']} carries Agent result"
                    )
                if row["classification_source"] == "agent":
                    expected_status = (
                        EmailClassificationStatus.PROCESSED.value
                        if agent_result.certainty == "certain"
                        else EmailClassificationStatus.PENDING_FEEDBACK.value
                    )
                    if (
                        row["category"] != agent_result.category
                        or row["confidence"] != agent_result.confidence
                        or row["status"] != expected_status
                    ):
                        raise EmailPersistenceCorruption(
                            f"Agent result diverges for classification {row['id']}"
                        )
            elif row["classification_source"] == "agent":
                raise EmailPersistenceCorruption(
                    f"Agent classification {row['id']} has no Agent result"
                )
            message = messages.get(row["stable_message_identity"])
            if (
                message is None
                or message["account_id"] != row["account_id"]
                or message["stable_message_identity"] != row["stable_message_identity"]
            ):
                raise EmailPersistenceCorruption(
                    f"message identity mismatch for classification {row['id']}"
                )
            try:
                message_provider_locator = EmailProviderLocator.model_validate(
                    {
                        "account_id": message["account_id"],
                        "folder": message["folder"],
                        "uidvalidity": message["uidvalidity"],
                        "uid": message["uid"],
                        "rfc_message_id": message["rfc_message_id"] or None,
                        "thread_id": message["thread_identity"] or None,
                    }
                )
                classification_provider_locator = EmailProviderLocator.model_validate(
                    {
                        "account_id": row["account_id"],
                        "folder": row["folder"],
                        "uidvalidity": row["uidvalidity"],
                        "uid": row["uid"],
                        "rfc_message_id": row["rfc_message_id"] or None,
                        "thread_id": row["thread_id"] or None,
                    }
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    f"invalid provider locator metadata for classification {row['id']}"
                ) from exc
            message_locator = (
                message_provider_locator.folder,
                message_provider_locator.uidvalidity,
                message_provider_locator.uid,
                message_provider_locator.rfc_message_id or "",
                message_provider_locator.thread_id or "",
            )
            classification_locator = (
                classification_provider_locator.folder,
                classification_provider_locator.uidvalidity,
                classification_provider_locator.uid,
                classification_provider_locator.rfc_message_id or "",
                classification_provider_locator.thread_id or "",
            )
            if message_locator != classification_locator:
                raise EmailPersistenceCorruption(
                    f"message locator mismatch for classification {row['id']}"
                )
            classification_plans = plans_by_classification.get(row["id"], [])
            if row["status"] == EmailClassificationStatus.PENDING_FEEDBACK.value:
                if row["classification_source"] not in {"model", "agent"}:
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} must have model "
                        "or agent source"
                    )
                if row["classification_source"] == "agent" and (
                    row["category"] is not None
                    or row["predicted_category"] is not None
                    or probabilities
                ):
                    raise EmailPersistenceCorruption(
                        f"uncertain Agent classification {row['id']} has a category"
                    )
                if (
                    row["current_action_plan_id"] is not None
                    or classification_plans
                ):
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} carries an ActionPlan"
                    )
                if row["confirmed_category"] is not None:
                    raise EmailPersistenceCorruption(
                        f"pending feedback classification {row['id']} is confirmed"
                    )
                continue
            if row["category"] is None:
                raise EmailPersistenceCorruption(
                    f"processed classification {row['id']} has no category"
                )
            if (
                row["classification_source"] == "user"
                and row["confirmed_category"] != row["category"]
            ):
                raise EmailPersistenceCorruption(
                    f"user-confirmed classification {row['id']} has inconsistent category"
                )
            if (
                row["classification_source"] in {"model", "agent"}
                and row["confirmed_category"] is not None
                and row["confirmed_category"] != row["category"]
            ):
                raise EmailPersistenceCorruption(
                    f"model-processed classification {row['id']} has inconsistent category"
                )
            if row["current_action_plan_id"] is None:
                raise EmailPersistenceCorruption(
                    f"processed classification {row['id']} has no current ActionPlan"
                )
            current_plan = plans.get(row["current_action_plan_id"])
            if current_plan is None or current_plan.classification_id != row["id"]:
                raise EmailPersistenceCorruption(
                    f"missing current ActionPlan for classification {row['id']}"
                )
            highest_version = max(
                plan.action_plan_version for plan in classification_plans
            )
            if current_plan.action_plan_version != highest_version:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan is not highest ActionPlan version for "
                    f"classification {row['id']}"
                )
            expected_fields = (
                row["account_id"],
                row["category"],
                row["classification_source"],
                row["confidence"],
                row["model_id"],
                row["config_version"],
            )
            plan_fields = (
                current_plan.account_id,
                current_plan.category,
                current_plan.classification_source,
                current_plan.confidence,
                current_plan.model_id,
                current_plan.config_version,
            )
            if plan_fields != expected_fields:
                raise EmailPersistenceCorruption(
                    f"current ActionPlan classification fields mismatch for {row['id']}"
                )

        for stable_identity in messages:
            related = classifications_by_identity.get(stable_identity, [])
            # A message row is the mailbox cache and may be written before the
            # classifier creates its durable decision.  Startup validation must
            # not turn that recoverable scan intermediate state into a worker-
            # wide outage; duplicate ownership is still corruption.
            if len(related) > 1:
                raise EmailPersistenceCorruption(
                    f"email message {stable_identity} has multiple classifications"
                )

        for plan in plans.values():
            classification = classifications.get(plan.classification_id)
            if (
                classification is None
                or classification["account_id"] != plan.account_id
            ):
                raise EmailPersistenceCorruption(
                    f"ActionPlan {plan.action_plan_id} has no matching classification"
                )

        for row in db.execute(f"select * from ({EMAIL_FEEDBACK_SQL})"):
            try:
                request_id = _validate_feedback_request_id(row["feedback_request_id"])
                expected_plan_id = _validate_expected_action_plan_id(
                    row["expected_current_action_plan_id"]
                )
                category = rehydrate_legacy_email_category_key(row["category"])
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "invalid durable email feedback request"
                ) from exc
            classification = classifications.get(row["classification_id"])
            resulting_plan = plans.get(row["resulting_action_plan_id"])
            expected_plan = (
                plans.get(expected_plan_id) if expected_plan_id is not None else None
            )
            valid_result = (
                classification is not None
                and resulting_plan is not None
                and resulting_plan.classification_id == row["classification_id"]
                and resulting_plan.category == category
                and resulting_plan.classification_source == "user"
            )
            if not valid_result:
                raise EmailPersistenceCorruption(
                    f"feedback request {request_id} has an inconsistent result"
                )
            if expected_plan_id is None:
                valid_version = resulting_plan.action_plan_version == 1
            else:
                valid_version = (
                    expected_plan is not None
                    and expected_plan.classification_id == row["classification_id"]
                    and resulting_plan.action_plan_version
                    == expected_plan.action_plan_version + 1
                )
            if not valid_version:
                raise EmailPersistenceCorruption(
                    f"feedback request {request_id} has an inconsistent plan version"
                )

        for row in db.execute(f"select * from ({EMAIL_REPLY_RECEIPT_SQL})"):
            receipt = self._email_reply_receipt_row(row)
            provider_receipt = receipt["provider_receipt"]
            if set(provider_receipt) != {
                "sent_folder",
                "sent_uidvalidity",
                "sent_uid",
                "smtp_acceptance_id",
            }:
                raise EmailPersistenceCorruption(
                    "invalid durable email reply provider receipt"
                )
            try:
                assert_no_credentials(provider_receipt)
                _validate_opaque_provider_identifier(
                    receipt["outgoing_message_id"],
                    field="outgoing_message_id",
                )
                _validate_opaque_provider_identifier(
                    receipt["provider_target"],
                    field="provider_target",
                )
                _validate_opaque_provider_identifier(
                    receipt["provider_result_id"],
                    field="provider_result_id",
                )
                _validate_provider_location(
                    provider_receipt["sent_folder"],
                    field="sent_folder",
                )
                _validate_opaque_provider_identifier(
                    provider_receipt["smtp_acceptance_id"],
                    field="smtp_acceptance_id",
                    allow_empty=True,
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "email reply provider receipt is not a safe opaque receipt"
                ) from exc
            plan = plans.get(receipt["action_plan_id"])
            classification = classifications.get(receipt["classification_id"])
            if (
                plan is None
                or classification is None
                or plan.classification_id != receipt["classification_id"]
                or plan.account_id != receipt["account_id"]
                or classification["stable_message_identity"]
                != receipt["stable_message_identity"]
                or EmailAction.AUTO_REPLY not in plan.agent_actions
                or not isinstance(provider_receipt["sent_folder"], str)
                or not provider_receipt["sent_folder"]
                or not isinstance(provider_receipt["sent_uidvalidity"], int)
                or provider_receipt["sent_uidvalidity"] <= 0
                or not isinstance(provider_receipt["sent_uid"], int)
                or provider_receipt["sent_uid"] <= 0
                or not isinstance(provider_receipt["smtp_acceptance_id"], str)
            ):
                raise EmailPersistenceCorruption(
                    "email reply receipt does not match its immutable ActionPlan"
                )

        for row in db.execute(f"select * from ({EMAIL_REPLY_CLAIM_SQL})"):
            claim = self._email_reply_dispatch_claim_row(row)
            plan = plans.get(claim["action_plan_id"])
            classification = classifications.get(claim["classification_id"])
            try:
                _validate_email_reply_owner(
                    {
                        "owner_id": claim["owner_id"],
                        "generation": claim["owner_generation"],
                        "lease_token": claim["lease_token"],
                    }
                )
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "email reply dispatch claim has an invalid owner fence"
                ) from exc
            account_snapshot = claim["account_snapshot"]
            if (
                claim["status"] not in _EMAIL_REPLY_CLAIM_STATUSES
                or plan is None
                or classification is None
                or plan.classification_id != claim["classification_id"]
                or plan.account_id != claim["account_id"]
                or classification["stable_message_identity"]
                != claim["stable_message_identity"]
                or EmailAction.AUTO_REPLY not in plan.agent_actions
                or account_snapshot.get("account_id") != claim["account_id"]
                or account_snapshot.get("email_address") != claim["sender"]
                or account_snapshot.get("updated_at") != claim["account_updated_at"]
            ):
                raise EmailPersistenceCorruption(
                    "email reply dispatch claim does not match its ActionPlan"
                )
            if (
                claim["status"] == "done"
                and db.execute(
                    f"select 1 from ({EMAIL_REPLY_RECEIPT_SQL}) where action_identity=?",
                    (claim["action_identity"],),
                ).fetchone()
                is None
            ):
                raise EmailPersistenceCorruption(
                    "done email reply dispatch claim has no durable receipt"
                )
            if claim["status"] == "dispatching":
                live_account = db.execute(
                    f"select * from ({config_projection('email_accounts')}) where account_id=?",
                    (claim["account_id"],),
                ).fetchone()
                live_message = messages.get(claim["stable_message_identity"])
                if (
                    classification["current_action_plan_id"] != claim["action_plan_id"]
                    or live_account is None
                    or not live_account["enabled"]
                    or live_account["email_address"] != claim["sender"]
                    or live_account["updated_at"] != claim["account_updated_at"]
                    or live_message is None
                    or live_message["thread_identity"] != claim["thread_identity"]
                ):
                    raise EmailPersistenceCorruption(
                        "dispatching email reply claim lost its live authorization"
                    )

        unsubscribe_effects: dict[tuple[str, str], dict[str, Any]] = {}
        for row in db.execute(f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL})"):
            effect_claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (row["action_identity"],),
            ).fetchone()
            if effect_claim is None:
                raise EmailPersistenceCorruption(
                    "unsubscribe claim does not match its immutable ActionPlan"
                )
            try:
                operations = _validate_unsubscribe_operations(
                    _json_load(
                        row["operations_json"],
                        field="operations_json",
                        expected_type=list,
                    )
                )
                expected = email_unsubscribe_effect_digest(
                    action_identity=row["action_identity"],
                    action_plan_id=effect_claim["action_plan_id"],
                    action_plan_version=effect_claim["action_plan_version"],
                    classification_id=effect_claim["classification_id"],
                    account_id=effect_claim["account_id"],
                    stable_message_identity=effect_claim["stable_message_identity"],
                    thread_identity=effect_claim["thread_identity"],
                    entry_reference=effect_claim["entry_reference"],
                    operations=operations,
                    previous_effect_digest=row["previous_effect_digest"],
                )
            except (IndexError, TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix contains invalid durable state"
                ) from exc
            if expected != row["effect_digest"]:
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix digest is invalid"
                )
            unsubscribe_effects[(row["action_identity"], row["effect_digest"])] = {
                "operations": operations,
                "previous_effect_digest": row["previous_effect_digest"],
            }

        for (action_identity, _), effect in unsubscribe_effects.items():
            previous_digest = effect["previous_effect_digest"]
            if not previous_digest:
                continue
            previous = unsubscribe_effects.get((action_identity, previous_digest))
            if (
                previous is None
                or effect["operations"][:-1] != previous["operations"]
                or len(effect["operations"]) != len(previous["operations"]) + 1
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe effect prefix chain is not append-only"
                )

        unsubscribe_claims: dict[str, dict[str, Any]] = {}
        for row in db.execute(f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL})"):
            claim = self._email_unsubscribe_claim_row(row)
            unsubscribe_claims[claim["action_identity"]] = claim
            plan = plans.get(claim["action_plan_id"])
            classification = classifications.get(claim["classification_id"])
            message = messages.get(claim["stable_message_identity"])
            try:
                _validate_email_unsubscribe_owner(
                    {
                        "owner_id": claim["owner_id"],
                        "generation": claim["owner_generation"],
                        "lease_token": claim["lease_token"],
                    }
                )
                _validate_unsubscribe_opaque(
                    claim["entry_reference"], field="entry_reference"
                )
                operations = _validate_unsubscribe_operations(claim["operations"])
                current_effect = unsubscribe_effects.get(
                    (claim["action_identity"], claim["effect_digest"])
                )
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe claim contains non-redacted durable state"
                ) from exc
            if (
                claim["status"] not in _EMAIL_UNSUBSCRIBE_CLAIM_STATUSES
                or current_effect is None
                or current_effect["operations"] != operations
                or plan is None
                or classification is None
                or message is None
                or plan.classification_id != claim["classification_id"]
                or plan.action_plan_version != claim["action_plan_version"]
                or plan.account_id != claim["account_id"]
                or classification["stable_message_identity"]
                != claim["stable_message_identity"]
                or message["account_id"] != claim["account_id"]
                or message["thread_identity"] != claim["thread_identity"]
                or EmailAction.UNSUBSCRIBE not in plan.agent_actions
                or claim["action_identity"]
                != email_action_identity(
                    account_id=claim["account_id"],
                    stable_message_identity=claim["stable_message_identity"],
                    action_type=EmailAction.UNSUBSCRIBE,
                    action_plan_version=claim["action_plan_version"],
                )
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe claim does not match its immutable ActionPlan"
                )
            receipt_exists = db.execute(
                f"select 1 from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (claim["action_identity"],),
            ).fetchone()
            if claim["status"] == "done" and receipt_exists is None:
                raise EmailPersistenceCorruption(
                    "done unsubscribe claim has no durable terminal receipt"
                )
            if claim["status"] == "dispatching":
                live_account = db.execute(
                    f"select * from ({config_projection('email_accounts')}) where account_id=?",
                    (claim["account_id"],),
                ).fetchone()
                if (
                    classification["status"] != "processed"
                    or classification["current_action_plan_id"]
                    != claim["action_plan_id"]
                    or live_account is None
                    or not live_account["enabled"]
                    or live_account["updated_at"] != claim["account_updated_at"]
                ):
                    raise EmailPersistenceCorruption(
                        "dispatching unsubscribe claim lost live authorization"
                    )

            continuation = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL}) where action_identity=?",
                (claim["action_identity"],),
            ).fetchone()
            if (claim["status"] == "awaiting_audit") != (continuation is not None):
                raise EmailPersistenceCorruption(
                    "unsubscribe awaiting-audit claim has inconsistent continuation"
                )
            if continuation is not None:
                try:
                    _validate_unsubscribe_opaque(
                        continuation["observation_reference"],
                        field="observation_reference",
                    )
                    _validate_unsubscribe_controls(
                        _json_load(
                            continuation["controls_json"],
                            field="controls_json",
                            expected_type=list,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise EmailPersistenceCorruption(
                        "unsubscribe continuation contains unsafe durable state"
                    ) from exc
                if continuation["effect_digest"] != claim["effect_digest"]:
                    raise EmailPersistenceCorruption(
                        "unsubscribe continuation effect binding is invalid"
                    )

        next_sequences: dict[str, int] = {}
        for row in db.execute(
            f"select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) order by action_identity, sequence"
        ):
            claim = unsubscribe_claims.get(row["action_identity"])
            expected_sequence = next_sequences.get(row["action_identity"], 1)
            try:
                for field in ("operation", "state", "reference"):
                    _validate_unsubscribe_opaque(row[field], field=field)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe journal contains non-redacted state"
                ) from exc
            if (
                claim is None
                or (row["action_identity"], row["effect_digest"])
                not in unsubscribe_effects
                or row["sequence"] != expected_sequence
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe journal is not contiguous or effect-bound"
                )
            effect = unsubscribe_effects[(row["action_identity"], row["effect_digest"])]
            if row["state"] == "action_required" and (
                row["sequence"] > len(effect["operations"])
                or row["operation"] != effect["operations"][row["sequence"] - 1]["kind"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe journal does not match its audited operation prefix"
                )
            next_sequences[row["action_identity"]] = expected_sequence + 1

        for action_identity, claim in unsubscribe_claims.items():
            if claim["status"] == "awaiting_audit" and (
                next_sequences.get(action_identity, 1) - 1 != len(claim["operations"])
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe continuation prefix is not fully journaled"
                )

        for row in db.execute(f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})"):
            receipt = self._email_unsubscribe_receipt_row(row)
            claim = unsubscribe_claims.get(receipt["action_identity"])
            try:
                _validate_unsubscribe_opaque(
                    receipt["entry_reference"], field="entry_reference"
                )
                _validate_unsubscribe_opaque(receipt["receipt_id"], field="receipt_id")
                _validate_unsubscribe_opaque(receipt["evidence"], field="evidence")
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe receipt contains non-redacted state"
                ) from exc
            if (
                claim is None
                or claim["status"] != "done"
                or receipt["outcome"] not in _EMAIL_UNSUBSCRIBE_OUTCOMES
                or any(
                    receipt[key] != claim[key]
                    for key in (
                        "effect_digest",
                        "action_plan_id",
                        "action_plan_version",
                        "classification_id",
                        "account_id",
                        "stable_message_identity",
                        "thread_identity",
                        "entry_reference",
                    )
                )
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe receipt does not match its exact accepted effect"
                )

        for row in db.execute(config_projection("email_category_configs")):
            try:
                validate_email_category_key(row["category_key"])
                validate_category_descriptions(
                    display_name=row["display_name"],
                    core_description=row["core_description"],
                    include=_json_load(
                        row["include_json"],
                        field="include_json",
                        expected_type=list,
                    ),
                    exclude=_json_load(
                        row["exclude_json"],
                        field="exclude_json",
                        expected_type=list,
                    ),
                    description_version=row["description_version"],
                )
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid configured email category"
                ) from exc
            actions = _json_load(
                row["actions_json"], field="actions_json", expected_type=list
            )
            parameters = _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            )
            try:
                tuple(EmailAction(action) for action in actions)
                tuple(EmailAction(action) for action in parameters)
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid configured email action"
                ) from exc

        for row in db.execute(config_projection("email_category_folder_bindings")):
            try:
                VerifiedEmailFolderBinding(
                    account_id=row["account_id"],
                    provider_folder_id=row["provider_folder_id"],
                    provider_folder_name=row["provider_folder_name"],
                    binding_status=row["binding_status"],
                    last_verified_at=row["last_verified_at"],
                )
                validate_email_category_key(row["category_key"])
            except ValueError as exc:
                raise EmailPersistenceCorruption(
                    "invalid email category folder binding"
                ) from exc

        self._validate_category_binding_completeness(db)

        action_rows = list(db.execute(f"select * from ({DIRECT_EMAIL_ACTION_SQL})"))
        actions_by_plan: dict[str, list[sqlite3.Row]] = {}
        actions_by_id: dict[str, sqlite3.Row] = {}
        for row in action_rows:
            if row["action_type"] not in _DIRECT_ACTION_VALUES:
                raise EmailPersistenceCorruption(
                    f"non-direct email action row {row['action_id']}"
                )
            if row["status"] not in _CURRENT_ACTION_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid current action status for {row['action_id']}"
                )
            parameters = _json_load(
                row["parameters_json"],
                field="parameters_json",
                expected_type=dict,
            )
            plan = plans.get(row["action_plan_id"])
            action = EmailAction(row["action_type"])
            expected_parameters = (
                dict(plan.action_parameters.get(action, {}))
                if plan is not None
                else None
            )
            immutable_fields_match = plan is not None and (
                action in plan.direct_actions
                and row["action_plan_id"] == plan.action_plan_id
                and row["action_type"] == action.value
                and row["classification_id"] == plan.classification_id
                and row["account_id"] == plan.account_id
                and row["config_version"] == plan.config_version
                and _json_dump(parameters) == _json_dump(expected_parameters)
            )
            if not immutable_fields_match:
                raise EmailPersistenceCorruption(
                    f"immutable direct action {row['action_id']} does not match its "
                    "ActionPlan"
                )
            actions_by_plan.setdefault(row["action_plan_id"], []).append(row)
            actions_by_id[row["action_id"]] = row
        for plan in plans.values():
            expected_actions = {action.value for action in plan.direct_actions}
            actual_actions = {
                row["action_type"]
                for row in actions_by_plan.get(plan.action_plan_id, [])
            }
            if actual_actions != expected_actions:
                raise EmailPersistenceCorruption(
                    f"direct action row set mismatch for ActionPlan {plan.action_plan_id}"
                )

        attempts_by_action: dict[str, list[sqlite3.Row]] = {}
        for row in db.execute(
            f"select * from ({DIRECT_EMAIL_ATTEMPT_SQL}) order by action_id, attempt_number"
        ):
            if row["status"] not in _TERMINAL_ATTEMPT_STATUSES:
                raise EmailPersistenceCorruption(
                    f"invalid action attempt status for {row['id']}"
                )
            if row["action_id"] not in actions_by_id:
                raise EmailPersistenceCorruption(
                    f"attempt {row['id']} has no current direct action"
                )
            attempts_by_action.setdefault(row["action_id"], []).append(row)
        execution_fields = (
            "provider_operation",
            "provider_target",
            "provider_result_id",
            "error",
            "started_at",
            "finished_at",
        )
        for action_id, row in actions_by_id.items():
            attempts = attempts_by_action.get(action_id, [])
            numbers = [attempt["attempt_number"] for attempt in attempts]
            if numbers != list(range(1, len(attempts) + 1)):
                raise EmailPersistenceCorruption(
                    f"non-contiguous attempt ledger for action {action_id}"
                )
            if row["status"] == "pending":
                if (
                    attempts
                    or row["attempt_count"] != 0
                    or any(row[field] for field in execution_fields)
                ):
                    raise EmailPersistenceCorruption(
                        f"pending action {action_id} has attempt state"
                    )
                continue
            if row["attempt_count"] != len(attempts):
                raise EmailPersistenceCorruption(
                    f"attempt count mismatch for action {action_id}"
                )
            if row["status"] == "processing":
                if (
                    not row["started_at"]
                    or row["finished_at"]
                    or row["provider_result_id"]
                    or row["error"]
                ):
                    raise EmailPersistenceCorruption(
                        f"processing action {action_id} has impossible terminal state"
                    )
                continue
            if not attempts:
                raise EmailPersistenceCorruption(
                    f"terminal action {action_id} has no terminal attempt"
                )
            latest = attempts[-1]
            if row["status"] != latest["status"] or any(
                row[field] != latest[field] for field in execution_fields
            ):
                raise EmailPersistenceCorruption(
                    f"latest attempt mismatch for action {action_id}"
                )

    @staticmethod
    def _current_action_plan_rows(
        db: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> dict[str, sqlite3.Row]:
        plan_ids = list({row["current_action_plan_id"] for row in rows
                         if row["current_action_plan_id"] is not None})
        if not plan_ids:
            return {}
        return {
            row["action_plan_id"]: row
            for row in db.execute(
                f"select * from ({EMAIL_PLAN_SQL}) "
                "where action_plan_id in (select value from json_each(?))",
                (_json_dump(plan_ids),),
            )
        }

    @classmethod
    def _classification_row(
        cls, row: sqlite3.Row, plan_rows: Mapping[str, sqlite3.Row]
    ) -> dict[str, Any]:
        probabilities = _json_load(
            row["probabilities_json"],
            field="probabilities_json",
            expected_type=dict,
        )
        if row["status"] not in _CLASSIFICATION_STATUSES:
            raise EmailPersistenceCorruption("invalid classification status")
        if row["classification_source"] not in _CLASSIFICATION_SOURCES:
            raise EmailPersistenceCorruption("invalid classification source")
        action_plan: dict[str, Any] | None = None
        if row["current_action_plan_id"] is not None:
            plan_row = plan_rows.get(row["current_action_plan_id"])
            if plan_row is None or plan_row["classification_id"] != row["id"]:
                raise EmailPersistenceCorruption(
                    f"missing current ActionPlan for classification {row['id']}"
                )
            action_plan = cls._stored_action_plan(plan_row).model_dump(mode="json")
        if row["agent_result_json"] != "null":
            agent_result = _json_load(
                row["agent_result_json"],
                field="agent_result_json",
                expected_type=dict,
            )
        else:
            agent_result = None
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "folder": row["folder"],
            "uidvalidity": row["uidvalidity"],
            "uid": row["uid"],
            "rfc_message_id": row["rfc_message_id"],
            "thread_id": row["thread_id"],
            "stable_message_identity": row["stable_message_identity"],
            "sender": row["sender"],
            "subject": row["subject"],
            "preview": row["preview"],
            "received_at": row["received_at"],
            "category": row["category"],
            "predicted_category": row["predicted_category"],
            "confirmed_category": row["confirmed_category"],
            "confidence": row["confidence"],
            "margin": row["margin"],
            "probabilities": probabilities,
            "model_id": row["model_id"],
            "config_version": row["config_version"],
            "status": row["status"],
            "classification_source": row["classification_source"],
            "agent_result": agent_result,
            "action_plan": action_plan,
            "current_action_plan_id": row["current_action_plan_id"],
            "confirmed_at": row["confirmed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _classification_evidence_row(
        cls, row: sqlite3.Row, plan_rows: Mapping[str, sqlite3.Row]
    ) -> dict[str, Any]:
        item = cls._classification_row(row, plan_rows)
        raw_metadata = row["message_attachment_metadata_json"]
        if raw_metadata is None:
            item["attachment_metadata"] = []
            return item
        attachments = _json_load(
            raw_metadata,
            field="attachment_metadata_json",
            expected_type=list,
        )
        try:
            item["attachment_metadata"] = [
                EmailAttachmentMetadata.model_validate(attachment).model_dump(
                    mode="json"
                )
                for attachment in attachments
            ]
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid attachment_metadata_json for classification {row['id']}"
            ) from exc
        return item

    @staticmethod
    def _stored_action_plan(row: sqlite3.Row) -> EmailActionPlan:
        payload: dict[str, object] = {
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "category": row["category"],
            "classification_source": row["classification_source"],
            "confidence": row["confidence"],
            "model_id": row["model_id"],
            "config_version": row["config_version"],
            "actions": _json_load(
                row["actions_json"],
                field="actions_json",
                expected_type=list,
            ),
            "action_parameters": _json_load(
                row["action_parameters_json"],
                field="action_parameters_json",
                expected_type=dict,
            ),
            "created_at": row["created_at"],
        }
        if row["authorization_snapshot_json"] is not None:
            payload.update(
                {
                    "authorization_snapshot_format": "authorization_snapshot_v2",
                    "action_authorizations": _json_load(
                        row["authorization_snapshot_json"],
                        field="authorization_snapshot_json",
                        expected_type=list,
                    ),
                }
            )
        try:
            return rehydrate_legacy_email_action_plan_json(_json_dump(payload))
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid immutable ActionPlan {row['action_plan_id']}"
            ) from exc

    def _feedback_application(
        self,
        db: sqlite3.Connection,
        request: sqlite3.Row,
        *,
        applied: bool,
    ) -> EmailFeedbackApplication:
        classification = db.execute(
            f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
            (request["classification_id"],),
        ).fetchone()
        plan_row = db.execute(
            f"select * from ({EMAIL_PLAN_SQL}) where action_plan_id=?",
            (request["resulting_action_plan_id"],),
        ).fetchone()
        if classification is None or plan_row is None:
            raise EmailPersistenceCorruption(
                f"feedback request {request['feedback_request_id']} has no result"
            )
        action_plan = self._stored_action_plan(plan_row)
        confirmed = self._classification_row(
            classification, self._current_action_plan_rows(db, [classification])
        )
        confirmed.update(
            {
                "category": action_plan.category,
                "confirmed_category": action_plan.category,
                "confidence": action_plan.confidence,
                "model_id": action_plan.model_id,
                "config_version": action_plan.config_version,
                "status": EmailClassificationStatus.PROCESSED.value,
                "classification_source": "user",
                "action_plan": action_plan.model_dump(mode="json"),
                "current_action_plan_id": action_plan.action_plan_id,
                "confirmed_at": request["applied_at"],
                "updated_at": request["applied_at"],
            }
        )
        return EmailFeedbackApplication(
            feedback_request_id=request["feedback_request_id"],
            expected_current_action_plan_id=request["expected_current_action_plan_id"],
            resulting_action_plan_id=request["resulting_action_plan_id"],
            confirmed=confirmed,
            applied=applied,
        )

    @staticmethod
    def _check_classification_identity(
        db: sqlite3.Connection,
        classification: EmailClassification,
    ) -> sqlite3.Row | None:
        by_id = db.execute(
            f"select id, stable_message_identity from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
            (classification.classification_id,),
        ).fetchone()
        if by_id is not None and (
            by_id["stable_message_identity"] != classification.stable_message_identity
        ):
            raise EmailClassificationIdentityCollision(
                f"classification ID {classification.classification_id} is already bound "
                f"to {by_id['stable_message_identity']}"
            )
        by_identity = db.execute(
            f"select * from ({EMAIL_CLASSIFICATION_SQL}) where stable_message_identity=?",
            (classification.stable_message_identity,),
        ).fetchone()
        if by_identity is not None and (
            by_identity["id"] != classification.classification_id
        ):
            raise EmailClassificationIdentityCollision(
                f"stable identity {classification.stable_message_identity} is already bound "
                f"to classification ID {by_identity['id']}"
            )
        return by_identity

    @staticmethod
    def _upsert_message(
        db: sqlite3.Connection,
        classification: EmailClassification,
        *,
        sender: str,
        recipients: Sequence[str],
        subject: str,
        normalized_text: str,
        attachment_metadata: Sequence[EmailAttachmentMetadata],
        in_reply_to: str,
        references: Sequence[str],
        received_at: str,
        now: str,
    ) -> None:
        if any(not isinstance(recipient, str) for recipient in recipients):
            raise ValueError("recipients must contain strings")
        if any(
            not isinstance(item, EmailAttachmentMetadata)
            for item in attachment_metadata
        ):
            raise ValueError("attachment_metadata must contain metadata records")
        normalized_in_reply_to = _normalized_message_id(in_reply_to)
        normalized_references = _normalized_message_ids(references)
        locator = classification.provider_locator
        upsert_email_message(db, {
            "account_id": locator.account_id,
            "stable_message_identity": classification.stable_message_identity,
            "folder": locator.folder,
            "uidvalidity": locator.uidvalidity,
            "uid": locator.uid,
            "rfc_message_id": locator.rfc_message_id or "",
            "in_reply_to": normalized_in_reply_to,
            "references_json": _json_dump(list(normalized_references)),
            "thread_identity": locator.thread_id or "",
            "sender": sender,
            "recipients_json": _json_dump(list(recipients)),
            "subject": subject,
            "normalized_text": normalized_text,
            "preview": "",
            "attachment_metadata_json": _json_dump(
                [item.model_dump(mode="json") for item in attachment_metadata]
            ),
            "received_at": received_at,
            "created_at": now,
            "updated_at": now,
        })

    def _persist_action_plan(
        self,
        db: sqlite3.Connection,
        plan: EmailActionPlan,
        *,
        now: str,
    ) -> None:
        encoded_actions = _json_dump([action.value for action in plan.actions])
        encoded_parameters = _json_dump(
            {
                action.value: dict(parameters)
                for action, parameters in plan.action_parameters.items()
            }
        )
        encoded_authorizations = (
            None
            if plan.authorization_snapshot_format == "legacy_unavailable_v1"
            else _json_dump(
                [
                    authorization.model_dump(mode="json")
                    for authorization in plan.action_authorizations
                ]
            )
        )
        shared_action_lifecycle.persist_email_plan(
            db,
            {
                "action_plan_id": plan.action_plan_id,
                "action_plan_version": plan.action_plan_version,
                "classification_id": plan.classification_id,
                "account_id": plan.account_id,
                "category": plan.category,
                "classification_source": plan.classification_source,
                "confidence": plan.confidence,
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "actions_json": encoded_actions,
                "action_parameters_json": encoded_parameters,
                "authorization_snapshot_json": encoded_authorizations,
                "created_at": plan.created_at.isoformat(),
            },
        )
        self._ensure_direct_action_rows(db, plan, now=now)

    @staticmethod
    def _ensure_direct_action_rows(
        db: sqlite3.Connection,
        plan: EmailActionPlan,
        *,
        now: str,
    ) -> None:
        for action in plan.direct_actions:
            parameters = dict(plan.action_parameters.get(action, {}))
            shared_action_lifecycle.create_direct_email_action(
                db,
                {
                    "action_plan_id": plan.action_plan_id,
                    "classification_id": plan.classification_id,
                    "account_id": plan.account_id,
                    "action_type": action.value,
                    "parameters_json": _json_dump(parameters),
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )

    @staticmethod
    def _advance_cursor(
        db: sqlite3.Connection,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_seen_uid: int,
        last_success_at: str,
        last_error: str,
        expected_uidvalidity: int | None,
    ) -> None:
        _require_positive_int(uidvalidity, field="cursor_uidvalidity")
        if expected_uidvalidity is not None:
            _require_positive_int(
                expected_uidvalidity,
                field="expected_cursor_uidvalidity",
            )
        if (
            isinstance(last_seen_uid, bool)
            or not isinstance(last_seen_uid, int)
            or last_seen_uid < 0
        ):
            raise ValueError("cursor_last_seen_uid must be a non-negative integer")
        current = db.execute(
            '\n            select uidvalidity, last_seen_uid from (' + state_projection('email_scan_cursors') + ')\n            where account_id=? and folder=?\n            ',
            (account_id, folder),
        ).fetchone()
        if current is None:
            if expected_uidvalidity is not None:
                raise EmailCursorConflict(
                    f"cursor generation expected {expected_uidvalidity}, but no cursor exists"
                )
            insert_state(db, "email_scan_cursors", {
                    "account_id": account_id, "folder": folder,
                    "uidvalidity": uidvalidity, "last_seen_uid": last_seen_uid,
                    "last_success_at": last_success_at, "last_error": last_error,
                })
            return
        current_uidvalidity = int(current["uidvalidity"])
        if (
            expected_uidvalidity is not None
            and current_uidvalidity != expected_uidvalidity
        ):
            raise EmailCursorConflict(
                f"cursor generation conflict: expected {expected_uidvalidity}, "
                f"found {current_uidvalidity}"
            )
        if current_uidvalidity == uidvalidity:
            update_state_rows(
                db, "email_scan_cursors",
                {"last_success_at": last_success_at, "last_error": last_error},
                update_json={"last_seen_uid":
                    "case when state.last_seen_uid>=? then " +
                    state_json_field("last_seen_uid", "state") + " else json_quote(?) end"},
                update_parameters=(last_seen_uid, last_seen_uid),
                where_sql="account_id=? and folder=? and uidvalidity=?",
                parameters=(account_id, folder, uidvalidity),
            )
            return
        if expected_uidvalidity is None:
            raise EmailCursorConflict(
                "cursor generation change requires expected_cursor_uidvalidity; "
                f"current generation is {current_uidvalidity}"
            )
        updated = update_state_rows(
            db, "email_scan_cursors",
            {"uidvalidity": uidvalidity, "last_seen_uid": last_seen_uid,
             "last_success_at": last_success_at, "last_error": last_error},
            where_sql="account_id=? and folder=? and uidvalidity=?",
            parameters=(account_id, folder, expected_uidvalidity),
        )
        if updated != 1:
            raise EmailCursorConflict(
                f"cursor generation changed while replacing {expected_uidvalidity}"
            )

    def persist_scan_result(
        self,
        classification: EmailClassification,
        *,
        agent_result: object | None = None,
        sender: str = "",
        recipients: Sequence[str] = (),
        subject: str = "",
        normalized_text: str = "",
        preview: str = "",
        attachment_metadata: Sequence[EmailAttachmentMetadata] = (),
        in_reply_to: str = "",
        references: Sequence[str] = (),
        received_at: str = "",
        model_text: str = "",
        cursor_uidvalidity: int | None = None,
        cursor_last_seen_uid: int | None = None,
        cursor_last_success_at: str = "",
        cursor_last_error: str = "",
        expected_cursor_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Atomically persist scan state and advance its folder cursor last."""

        from app.email_preview import email_preview

        preview = email_preview(normalized_text)
        _validate_model_text(model_text)
        agent_result_json = _validated_agent_result_json(classification, agent_result)
        now = self._now()
        locator = classification.provider_locator
        if (cursor_uidvalidity is None) != (cursor_last_seen_uid is None):
            raise ValueError(
                "cursor UIDVALIDITY and last UID must be supplied together"
            )
        if expected_cursor_uidvalidity is not None and cursor_uidvalidity is None:
            raise ValueError(
                "expected cursor generation requires cursor UIDVALIDITY and last UID"
            )
        if cursor_uidvalidity is not None and cursor_uidvalidity != locator.uidvalidity:
            raise ValueError("cursor UIDVALIDITY must match the message locator")
        if cursor_last_seen_uid is not None and cursor_last_seen_uid < locator.uid:
            raise ValueError("cursor cannot advance behind the persisted message UID")
        with self._connect() as db:
            db.execute("begin immediate")
            existing = self._check_classification_identity(db, classification)
            self._upsert_message(
                db,
                classification,
                sender=sender,
                recipients=recipients,
                subject=subject,
                normalized_text=normalized_text,
                attachment_metadata=attachment_metadata,
                in_reply_to=in_reply_to,
                references=references,
                received_at=received_at,
                now=now,
            )
            if existing is None:
                confirmed_category = (
                    classification.category
                    if classification.status is EmailClassificationStatus.PROCESSED
                    else None
                )
                insert_email_classification(db, {
                    "id": classification.classification_id,
                    "account_id": locator.account_id,
                    "folder": locator.folder,
                    "uidvalidity": locator.uidvalidity,
                    "uid": locator.uid,
                    "rfc_message_id": locator.rfc_message_id,
                    "thread_id": locator.thread_id,
                    "stable_message_identity": classification.stable_message_identity,
                    "sender": sender,
                    "subject": subject,
                    "preview": preview,
                    "model_text": model_text,
                    "received_at": received_at,
                    "category": classification.category,
                    "predicted_category": classification.category,
                    "confirmed_category": confirmed_category,
                    "confidence": classification.confidence,
                    "margin": classification.margin,
                    "probabilities_json": _json_dump(classification.probabilities),
                    "model_id": classification.model_id,
                    "config_version": classification.config_version,
                    "status": classification.status.value,
                    "classification_source": classification.classification_source,
                    "agent_result_json": agent_result_json,
                    "current_action_plan_id": None,
                    "updated_at": now,
                })
                if classification.action_plan is not None:
                    self._persist_action_plan(db, classification.action_plan, now=now)
                    update_email_classification(db, classification.classification_id, {
                        "current_action_plan_id": classification.action_plan.action_plan_id,
                    })
            else:
                if (
                    existing["classification_source"] == "agent"
                    and existing["agent_result_json"] != agent_result_json
                ):
                    raise EmailClassificationConflict(
                        "canonical Agent result diverges from retry"
                    )
                update_email_classification(db, classification.classification_id, {
                    "folder": locator.folder,
                    "uidvalidity": locator.uidvalidity,
                    "uid": locator.uid,
                    "thread_id": locator.thread_id,
                    "updated_at": now,
                })
            if cursor_uidvalidity is not None and cursor_last_seen_uid is not None:
                self._advance_cursor(
                    db,
                    account_id=locator.account_id,
                    folder=locator.folder,
                    uidvalidity=cursor_uidvalidity,
                    last_seen_uid=cursor_last_seen_uid,
                    last_success_at=cursor_last_success_at,
                    last_error=cursor_last_error,
                    expected_uidvalidity=expected_cursor_uidvalidity,
                )
            row = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (classification.classification_id,),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [row] if row else [])
        assert row is not None
        return self._classification_row(row, plan_rows)

    def persist_empty_scan_cursor(
        self,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_success_at: str,
        expected_cursor_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Commit a successful empty readonly scan without inventing a message."""

        account_id = account_id.strip()
        folder = folder.strip()
        if not account_id or not folder:
            raise ValueError("account_id and folder must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            self._advance_cursor(
                db,
                account_id=account_id,
                folder=folder,
                uidvalidity=uidvalidity,
                last_seen_uid=0,
                last_success_at=last_success_at,
                last_error="",
                expected_uidvalidity=expected_cursor_uidvalidity,
            )
            row = db.execute(
                '\n                select * from (' + state_projection('email_scan_cursors') + ')\n                where account_id=? and folder=?\n                ',
                (account_id, folder),
            ).fetchone()
        assert row is not None
        return dict(row)

    def record_historical_classification_outcome(
        self, outcome: object
    ) -> dict[str, Any]:
        """Append one redacted model decision/action outcome for a manual history run."""

        stable_identity = str(
            getattr(outcome, "stable_message_identity", "") or ""
        ).strip()
        model_id = str(getattr(outcome, "model_id", "") or "").strip()
        action_outcome = str(getattr(outcome, "action_outcome", "") or "").strip()
        if not stable_identity or not model_id or not action_outcome:
            raise ValueError("historical outcome identity is incomplete")
        predicted_value = getattr(outcome, "predicted_category", None)
        predicted_category = (
            None
            if predicted_value is None
            else validate_email_category_key(predicted_value)
        )
        threshold = _optional_probability(
            getattr(outcome, "threshold", None), field="threshold"
        )
        probability = _optional_probability(
            getattr(outcome, "probability", None), field="probability"
        )
        important_value = getattr(outcome, "important", None)
        if important_value is not None and type(important_value) is not bool:
            raise TypeError("important must be a strict bool or None")
        identity_payload = _json_dump(
            {
                "stable_message_identity": stable_identity,
                "model_id": model_id,
                "predicted_category": predicted_category,
                "threshold": threshold,
                "probability": probability,
                "important": important_value,
                "action_outcome": action_outcome,
            }
        )
        event_id = "email-historical:" + sha256(
            identity_payload.encode("utf-8")
        ).hexdigest()
        created_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            insert_email_history(db, "email_historical_classification_history", {
                "event_id": event_id,
                "stable_message_identity": stable_identity,
                "model_id": model_id,
                "predicted_category": predicted_category,
                "threshold": threshold,
                "probability": probability,
                "important": None if important_value is None else int(important_value),
                "action_outcome": action_outcome,
                "created_at": created_at,
            }, ignore_public_duplicate=True)
            rows = email_history_rows(db, "email_historical_classification_history",
                                      where="event_id=?", parameters=(event_id,))
            row = rows[0] if rows else None
        assert row is not None
        return _historical_outcome_row(row)

    def historical_traversal_cursor(
        self, *, account_id: str, folder: str, model_id: str
    ) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                'select * from (' + state_projection('email_historical_traversals') + ') where account_id=? and folder=? and model_id=?',
                (account_id, folder, model_id),
            ).fetchone()
        return {
            "uidvalidity": None if row is None else row["uidvalidity"],
            "last_seen_uid": 0 if row is None else int(row["last_seen_uid"]),
        }

    def enqueue_historical_page(
        self,
        *,
        account_id: str,
        folder: str,
        model_id: str,
        uidvalidity: int,
        last_seen_uid: int,
        candidates: Sequence[object],
    ) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            for candidate in candidates:
                message = getattr(candidate, "provider_message", None)
                if not isinstance(message, Mapping):
                    raise ValueError("historical queued candidate message is missing")
                projection = _historical_candidate_projection(
                    message,
                    stable_message_identity=str(candidate.stable_message_identity),
                    account_id=account_id,
                    folder=folder,
                )
                normalized_input_hash = sha256(
                    str(candidate.normalized_text).encode("utf-8")
                ).hexdigest()
                candidate_uidvalidity = int(message.get('uidValidity') or uidvalidity)
                candidate_uid = int(message.get('uid') or 0)
                from app.email_historical_candidate_schema import compact_projection
                stored_projection = compact_projection(
                    projection, account_id=account_id, folder=folder,
                    stable_message_identity=str(candidate.stable_message_identity),
                    uidvalidity=candidate_uidvalidity, uid=candidate_uid,
                )
                db.execute(
                    """insert into email_historical_candidates (
                        account_id,folder,model_id,stable_message_identity,normalized_text,
                        provider_message_json,uidvalidity,uid,state,reason,updated_at
                    ) values (?,?,?,?,?,?,?,?, 'pending','',?)
                    on conflict(account_id,folder,model_id,stable_message_identity)
                    do update set normalized_text=excluded.normalized_text,
                        provider_message_json=excluded.provider_message_json,
                        uidvalidity=excluded.uidvalidity, uid=excluded.uid,
                        updated_at=excluded.updated_at""",
                    (account_id, folder, model_id, candidate.stable_message_identity,
                     normalized_input_hash, _json_dump(stored_projection),
                     candidate_uidvalidity, candidate_uid, now),
                )
            upsert_state(
                db, "email_historical_traversals",
                {"account_id": account_id, "folder": folder, "model_id": model_id,
                 "uidvalidity": uidvalidity, "last_seen_uid": last_seen_uid,
                 "updated_at": now},
                {
                    "uidvalidity": state_json_field("uidvalidity"),
                    "last_seen_uid":
                        "case when state.uidvalidity=excluded.uidvalidity and "
                        "state.last_seen_uid>=excluded.last_seen_uid then " +
                        state_json_field("last_seen_uid", "state") +
                        " else " + state_json_field("last_seen_uid") + " end",
                    "updated_at": state_json_field("updated_at"),
                },
            )

    def list_historical_candidates(
        self, *, account_id: str, folder: str, model_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("historical candidate limit must be positive")
        now = self._now()
        deferred_quota = min(2, limit)
        with self._connect() as db:
            deferred_rows = db.execute(
                """select * from email_historical_candidates
                where account_id=? and folder=? and model_id=? and state='deferred'
                  and next_retry_at != '' and next_retry_at <= ?
                order by case when attempted_at='' then updated_at else attempted_at end,
                         uid, stable_message_identity limit ?""",
                (account_id, folder, model_id, now, limit),
            ).fetchall()
            reserved = list(deferred_rows[:deferred_quota])
            pending_rows = db.execute(
                """select * from email_historical_candidates
                where account_id=? and folder=? and model_id=? and state='pending'
                order by uid, stable_message_identity limit ?""",
                (account_id, folder, model_id, limit - len(reserved)),
            ).fetchall()
            rows = reserved + list(pending_rows)
            if len(rows) < limit:
                rows.extend(deferred_rows[len(reserved):limit-len(rows)+len(reserved)])
        return [
            _historical_candidate_list_item(row)
            for row in rows
        ]

    def set_historical_candidate_state(
        self,
        *,
        account_id: str,
        folder: str,
        model_id: str,
        stable_message_identity: str,
        state: str,
        reason: str,
    ) -> None:
        if state not in {"pending", "deferred", "terminal"}:
            raise ValueError("historical candidate state is invalid")
        now = self._now()
        next_retry_at = ""
        attempted_at = ""
        if state == "deferred":
            attempted_at = now
            next_retry_at = (
                datetime.fromisoformat(now)
                + timedelta(seconds=HISTORICAL_DEFER_RETRY_SECONDS)
            ).isoformat(timespec="seconds")
        with self._connect() as db:
            updated = db.execute(
                """update email_historical_candidates set state=?,reason=?,attempted_at=?,
                    next_retry_at=?,updated_at=? where account_id=? and folder=? and model_id=?
                    and stable_message_identity=?""",
                (state, reason, attempted_at, next_retry_at, now,
                 account_id, folder, model_id, stable_message_identity),
            ).rowcount
        if updated != 1:
            raise ValueError("historical queued candidate is missing")

    def list_historical_classification_history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = email_history_rows(db, "email_historical_classification_history",
                                      order="created_at,event_id")
        return [_historical_outcome_row(row) for row in rows]

    def begin_historical_operation(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
        model_id: str,
        classification_id: int,
        action_plan_id: str,
        action_ids: Sequence[str],
        predicted_category: str,
        threshold: float,
        probability: float,
        important: bool,
    ) -> dict[str, Any]:
        identity = f"{model_id}\0{stable_message_identity}"
        operation_id = "email-historical-operation:" + sha256(
            identity.encode("utf-8")
        ).hexdigest()
        now = self._now()
        immutable = (
            account_id,
            stable_message_identity,
            model_id,
            classification_id,
            action_plan_id,
            _json_dump(list(action_ids)),
            predicted_category,
            float(threshold),
            float(probability),
            int(important),
        )
        with self._connect() as db:
            db.execute("begin immediate")
            insert_email_history(db, "email_historical_operations", dict(zip(
                ("operation_id", "account_id", "stable_message_identity", "model_id",
                 "classification_id", "action_plan_id", "action_ids_json",
                 "predicted_category", "threshold", "probability", "important",
                 "status", "action_outcome", "created_at", "updated_at"),
                (operation_id, *immutable, "processing", "", now, now), strict=True,
            )), ignore_public_duplicate=True)
            rows = email_history_rows(db, "email_historical_operations",
                                      where="operation_id=?", parameters=(operation_id,))
            row = rows[0] if rows else None
        assert row is not None
        observed = (
            row["account_id"], row["stable_message_identity"], row["model_id"],
            int(row["classification_id"]), row["action_plan_id"], row["action_ids_json"],
            row["predicted_category"], float(row["threshold"]),
            float(row["probability"]), int(row["important"]),
        )
        if observed != immutable:
            raise EmailPersistenceCorruption("historical operation identity conflict")
        return self._historical_operation_row(row)

    def list_processing_historical_operations(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = email_history_rows(db, "email_historical_operations",
                                      where="status='processing'",
                                      order="created_at,operation_id")
        return [self._historical_operation_row(row) for row in rows]

    def complete_historical_operation(self, outcome: object) -> None:
        stable_identity = str(getattr(outcome, "stable_message_identity"))
        model_id = str(getattr(outcome, "model_id"))
        with self._connect() as db:
            update_email_operation(
                db, model_id=model_id, stable_message_identity=stable_identity,
                outcome=str(getattr(outcome, "action_outcome")), updated_at=self._now(),
            )

    def touch_historical_operation(
        self, *, model_id: str, stable_message_identity: str
    ) -> None:
        with self._connect() as db:
            update_email_operation(
                db, model_id=model_id,
                stable_message_identity=stable_message_identity,
                updated_at=self._now(),
            )

    @staticmethod
    def _historical_operation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "action_ids": tuple(
                _json_load(row["action_ids_json"], field="action_ids_json", expected_type=list)
            ),
            "important": bool(row["important"]),
        }

    def upsert_classification(
        self,
        classification: EmailClassification,
        *,
        sender: str = "",
        subject: str = "",
        preview: str = "",
        model_text: str = "",
        received_at: str = "",
    ) -> dict[str, Any]:
        """Compatibility entrypoint for prototype callers without cursor state."""

        return self.persist_scan_result(
            classification,
            sender=sender,
            subject=subject,
            normalized_text=model_text,
            preview=preview,
            model_text=model_text,
            received_at=received_at,
        )

    def get_scan_cursor(self, account_id: str, folder: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                'select * from (' + state_projection('email_scan_cursors') + ') where account_id=? and folder=?',
                (account_id, folder),
            ).fetchone()
        return None if row is None else dict(row)

    def record_scan_cursor(
        self,
        *,
        account_id: str,
        folder: str,
        uidvalidity: int,
        last_seen_uid: int,
        expected_uidvalidity: int | None = None,
    ) -> dict[str, Any]:
        """Advance an observation-only scan after durable task enqueue."""

        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            self._advance_cursor(
                db,
                account_id=account_id,
                folder=folder,
                uidvalidity=uidvalidity,
                last_seen_uid=last_seen_uid,
                last_success_at=now,
                last_error="",
                expected_uidvalidity=expected_uidvalidity,
            )
            row = db.execute(
                'select * from (' + state_projection('email_scan_cursors') + ') where account_id=? and folder=?',
                (account_id, folder),
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                f"select * from ({config_projection('email_accounts')}) order by account_id"
            ).fetchall()
        return [self._account_row(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({config_projection('email_accounts')}) where account_id=?",
                (account_id,),
            ).fetchone()
        return None if row is None else self._account_row(row)

    def get_email_reply_delivery_authorization(
        self,
        classification_id: int,
        action_plan_id: str,
    ) -> dict[str, Any] | None:
        """Read one immutable auto-reply authorization and current-plan relation."""

        with self._connect() as db:
            row = db.execute(
                f"""
                select
                    classifications.id as classification_id,
                    classifications.account_id as account_id,
                    classifications.stable_message_identity
                        as stable_message_identity,
                    classifications.current_action_plan_id
                        as current_action_plan_id,
                    messages.thread_identity as thread_identity,
                    plans.action_plan_id as action_plan_id,
                    plans.action_plan_version as action_plan_version,
                    plans.actions_json as actions_json
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                join ({EMAIL_PLAN_SQL}) as plans
                  on plans.classification_id=classifications.id
                 and plans.action_plan_id=?
                where classifications.id=?
                  and classifications.status='processed'
                """,
                (action_plan_id, classification_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "current_action_plan_id": row["current_action_plan_id"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "actions": _json_load(
                row["actions_json"],
                field="actions_json",
                expected_type=list,
            ),
        }

    def get_email_reply_receipt(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({EMAIL_REPLY_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_reply_receipt_row(row)

    def claim_email_reply_dispatch(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        outgoing_message_id: str,
        sender: str,
        thread_identity: str,
        expected_account_updated_at: str,
        owner: Mapping[str, object],
    ) -> dict[str, Any] | None:
        """Atomically bind the complete current dispatch authorization to an owner."""

        claimed_at = self._now()
        owner = _validate_email_reply_owner(owner)
        immutable = {
            "action_identity": action_identity,
            "effect_digest": effect_digest,
            "action_plan_id": action_plan_id,
            "classification_id": classification_id,
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "outgoing_message_id": outgoing_message_id,
            "sender": sender,
            "thread_identity": thread_identity,
        }
        if (
            any(
                not isinstance(value, str) or not value or value != value.strip()
                for key, value in immutable.items()
                if key != "classification_id"
            )
            or classification_id <= 0
        ):
            raise ValueError("email reply dispatch identity is invalid")
        if (
            not expected_account_updated_at
            or expected_account_updated_at != expected_account_updated_at.strip()
        ):
            raise ValueError("expected_account_updated_at must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            authorization = db.execute(
                f"""
                select plans.actions_json, plans.action_plan_version,
                       messages.thread_identity
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_PLAN_SQL}) as plans
                  on plans.action_plan_id=classifications.current_action_plan_id
                 and plans.classification_id=classifications.id
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                  and classifications.status='processed'
                  and classifications.current_action_plan_id=?
                  and classifications.account_id=?
                  and classifications.stable_message_identity=?
                """,
                (
                    classification_id,
                    action_plan_id,
                    account_id,
                    stable_message_identity,
                ),
            ).fetchone()
            account = db.execute(
                f"select * from ({config_projection('email_accounts')}) where account_id=?",
                (account_id,),
            ).fetchone()
            if (
                authorization is None
                or action_identity
                != email_action_identity(
                    account_id=account_id,
                    stable_message_identity=stable_message_identity,
                    action_type=EmailAction.AUTO_REPLY,
                    action_plan_version=int(authorization["action_plan_version"]),
                )
                or authorization["thread_identity"] != thread_identity
                or EmailAction.AUTO_REPLY.value
                not in _json_load(
                    authorization["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                or account is None
                or not account["enabled"]
                or account["email_address"] != sender
                or account["updated_at"] != expected_account_updated_at
            ):
                return None
            account_snapshot = self._account_row(account)
            account_snapshot_json = _json_dump(account_snapshot)
            receipt = db.execute(
                f"select 1 from ({EMAIL_REPLY_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt is not None:
                return {**immutable, "status": "done", "acquired": False}
            existing = db.execute(
                f"select * from ({EMAIL_REPLY_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if existing is not None:
                claim = self._email_reply_dispatch_claim_row(existing)
                if any(claim[key] != value for key, value in immutable.items()):
                    raise EmailReplyDispatchConflict(
                        "email reply action identity is bound to another dispatch"
                    )
                if claim["status"] != "retryable":
                    return {**claim, "acquired": False}
                mutation_values = {
                    "owner_id": owner["owner_id"],
                    "owner_generation": owner["generation"],
                    "lease_token": owner["lease_token"],
                    "account_updated_at": expected_account_updated_at,
                    "account_snapshot_json": account_snapshot_json,
                    "status": "dispatching",
                    "claimed_at": claimed_at,
                    "updated_at": claimed_at,
                }
                updated = shared_action_lifecycle.transition(
                    db,
                    "email_reply",
                    "state",
                    values=mutation_values,
                    where_sql="action_identity=? and status='retryable' and owner_id=? and owner_generation=? and lease_token=?",
                    parameters=(
                        action_identity,
                        claim["owner_id"],
                        claim["owner_generation"],
                        claim["lease_token"],
                    ),
                ).rowcount
                if updated != 1:
                    raise EmailReplyDispatchConflict(
                        "email reply retryable claim changed concurrently"
                    )
                row = db.execute(
                    f"select * from ({EMAIL_REPLY_CLAIM_SQL}) where action_identity=?",
                    (action_identity,),
                ).fetchone()
                assert row is not None
                return {**self._email_reply_dispatch_claim_row(row), "acquired": True}
            shared_action_lifecycle.transition(
                db,
                "email_reply",
                "claim",
                values={
                    "action_identity": action_identity,
                    "effect_digest": effect_digest,
                    "action_plan_id": action_plan_id,
                    "classification_id": classification_id,
                    "account_id": account_id,
                    "stable_message_identity": stable_message_identity,
                    "outgoing_message_id": outgoing_message_id,
                    "owner_id": owner["owner_id"],
                    "owner_generation": owner["generation"],
                    "lease_token": owner["lease_token"],
                    "sender": sender,
                    "thread_identity": thread_identity,
                    "account_updated_at": expected_account_updated_at,
                    "account_snapshot_json": account_snapshot_json,
                    "status": "dispatching",
                    "claimed_at": claimed_at,
                    "updated_at": claimed_at,
                },
            )
            row = db.execute(
                f"select * from ({EMAIL_REPLY_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return {**self._email_reply_dispatch_claim_row(row), "acquired": True}

    def get_email_reply_dispatch_claim(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({EMAIL_REPLY_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_reply_dispatch_claim_row(row)

    def mark_email_reply_dispatch_uncertain(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        """CAS one owned possible dispatch into reconciliation-only state."""

        owner = _validate_email_reply_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            mutation_values = {"status": "uncertain", "updated_at": self._now()}
            updated = shared_action_lifecycle.transition(
                db,
                "email_reply",
                "state",
                values=mutation_values,
                where_sql="action_identity=? and status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailReplyDispatchConflict(
                    "email reply dispatch owner fence changed"
                )

    def release_email_reply_dispatch_retryable(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        """Release only an explicitly non-dispatched owned claim for retry."""

        owner = _validate_email_reply_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"""
                select claims.*, classifications.current_action_plan_id,
                       classifications.status as classification_status,
                       plans.actions_json, accounts.enabled,
                       accounts.email_address, accounts.updated_at as live_account_updated_at,
                       messages.thread_identity as live_thread_identity
                from ({EMAIL_REPLY_CLAIM_SQL}) as claims
                join ({EMAIL_CLASSIFICATION_SQL}) as classifications
                  on classifications.id=claims.classification_id
                join ({EMAIL_PLAN_SQL}) as plans
                  on plans.action_plan_id=claims.action_plan_id
                join ({config_projection('email_accounts')}) as accounts
                  on accounts.account_id=claims.account_id
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=claims.account_id
                 and messages.stable_message_identity=claims.stable_message_identity
                where claims.action_identity=? and claims.status='dispatching'
                  and claims.owner_id=? and claims.owner_generation=?
                  and claims.lease_token=?
                """,
                (
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            if (
                claim is None
                or claim["classification_status"] != "processed"
                or claim["current_action_plan_id"] != claim["action_plan_id"]
                or EmailAction.AUTO_REPLY.value
                not in _json_load(
                    claim["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                or not claim["enabled"]
                or claim["email_address"] != claim["sender"]
                or claim["live_account_updated_at"] != claim["account_updated_at"]
                or claim["live_thread_identity"] != claim["thread_identity"]
            ):
                raise EmailReplyDispatchConflict(
                    "email reply retry authorization changed"
                )
            mutation_values = {"status": "retryable", "updated_at": self._now()}
            updated = shared_action_lifecycle.transition(
                db,
                "email_reply",
                "state",
                values=mutation_values,
                where_sql="action_identity=? and status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailReplyDispatchConflict(
                    "email reply dispatch owner fence changed"
                )

    def recover_terminated_email_reply_dispatch_claims(
        self,
        *,
        owner: Mapping[str, object],
        termination_verifier: Callable[[Mapping[str, object]], bool],
        recovered_at: str,
    ) -> int:
        """Recover an owner only after the caller proves that generation terminated."""

        owner = _validate_email_reply_owner(owner)
        if not termination_verifier(owner):
            raise EmailReplyDispatchConflict("owner_termination_not_proven")
        recovered_at = _required_utc_timestamp(recovered_at, field="recovered_at")
        with self._connect() as db:
            db.execute("begin immediate")
            mutation_values = {"status": "uncertain", "updated_at": recovered_at}
            updated = shared_action_lifecycle.transition(
                db,
                "email_reply",
                "state",
                values=mutation_values,
                where_sql="status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(owner["owner_id"], owner["generation"], owner["lease_token"]),
            ).rowcount
            return updated

    @staticmethod
    def _email_reply_dispatch_claim_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "outgoing_message_id": row["outgoing_message_id"],
            "owner_id": row["owner_id"],
            "owner_generation": row["owner_generation"],
            "lease_token": row["lease_token"],
            "sender": row["sender"],
            "thread_identity": row["thread_identity"],
            "account_updated_at": row["account_updated_at"],
            "account_snapshot": _json_load(
                row["account_snapshot_json"],
                field="account_snapshot_json",
                expected_type=dict,
            ),
            "status": row["status"],
            "claimed_at": row["claimed_at"],
            "updated_at": row["updated_at"],
        }

    def persist_email_reply_receipt(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        outgoing_message_id: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        sent_folder: str,
        sent_uidvalidity: int,
        sent_uid: int,
        smtp_acceptance_id: str,
        claim_owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist/replay Sent evidence and complete its matching claim."""

        text_fields = {
            "action_identity": action_identity,
            "effect_digest": effect_digest,
            "action_plan_id": action_plan_id,
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "outgoing_message_id": outgoing_message_id,
            "provider_operation": provider_operation,
            "provider_target": provider_target,
            "provider_result_id": provider_result_id,
        }
        if any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in text_fields.values()
        ):
            raise ValueError("email reply receipt fields must be non-empty")
        if provider_operation not in {
            "sent_readback",
            "sent_equivalent_readback",
        }:
            raise ValueError("provider_operation is not an approved receipt operation")
        _validate_opaque_provider_identifier(
            outgoing_message_id,
            field="outgoing_message_id",
        )
        _validate_opaque_provider_identifier(provider_target, field="provider_target")
        _validate_opaque_provider_identifier(
            provider_result_id,
            field="provider_result_id",
        )
        _validate_provider_location(sent_folder, field="sent_folder")
        if classification_id <= 0 or sent_uidvalidity <= 0 or sent_uid <= 0:
            raise ValueError("email reply receipt numeric fields must be positive")
        _validate_opaque_provider_identifier(
            smtp_acceptance_id,
            field="smtp_acceptance_id",
            allow_empty=True,
        )
        requested_provider_receipt = {
            "sent_folder": sent_folder,
            "sent_uidvalidity": sent_uidvalidity,
            "sent_uid": sent_uid,
            "smtp_acceptance_id": smtp_acceptance_id,
        }
        assert_no_credentials(
            {
                "provider_operation": provider_operation,
                "provider_target": provider_target,
                "provider_result_id": provider_result_id,
                "provider_receipt": requested_provider_receipt,
            }
        )
        validated_claim_owner = (
            None if claim_owner is None else _validate_email_reply_owner(claim_owner)
        )
        created_at = self._now()
        receipt_base = {
            **text_fields,
            "classification_id": classification_id,
            "display_excerpt": "Automatic email reply verified in Sent.",
        }
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"select * from ({EMAIL_REPLY_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if row is None:
                shared_action_lifecycle.transition(
                    db,
                    "email_reply",
                    "outcome",
                    values={
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "action_plan_id": action_plan_id,
                        "classification_id": classification_id,
                        "account_id": account_id,
                        "stable_message_identity": stable_message_identity,
                        "outgoing_message_id": outgoing_message_id,
                        "provider_operation": provider_operation,
                        "provider_target": provider_target,
                        "provider_result_id": provider_result_id,
                        "provider_receipt_json": _json_dump(requested_provider_receipt),
                        "display_excerpt": receipt_base["display_excerpt"],
                        "created_at": created_at,
                    },
                )
            else:
                persisted = self._email_reply_receipt_row(row)
                comparable = {
                    key: value
                    for key, value in persisted.items()
                    if key not in {"created_at", "provider_receipt"}
                }
                if comparable != receipt_base:
                    raise EmailReplyReceiptConflict(
                        "email reply identity is bound to different provider evidence"
                    )
                persisted_receipt = persisted["provider_receipt"]
                persisted_without_smtp = {
                    key: value
                    for key, value in persisted_receipt.items()
                    if key != "smtp_acceptance_id"
                }
                requested_without_smtp = {
                    key: value
                    for key, value in requested_provider_receipt.items()
                    if key != "smtp_acceptance_id"
                }
                current_smtp_id = persisted_receipt["smtp_acceptance_id"]
                requested_smtp_id = requested_provider_receipt["smtp_acceptance_id"]
                if persisted_without_smtp != requested_without_smtp or (
                    current_smtp_id
                    and requested_smtp_id
                    and current_smtp_id != requested_smtp_id
                ):
                    raise EmailReplyReceiptConflict(
                        "email reply identity is bound to different provider evidence"
                    )
                if not current_smtp_id and requested_smtp_id:
                    enriched_receipt = {
                        **persisted_receipt,
                        "smtp_acceptance_id": requested_smtp_id,
                    }
                    mutation_values = {
                        "provider_receipt_json": _json_dump(enriched_receipt)
                    }
                    updated = shared_action_lifecycle.transition(
                        db,
                        "email_reply",
                        "amend_outcome",
                        values=mutation_values,
                        where_sql="action_identity=? and provider_receipt_json=?",
                        parameters=(action_identity, _json_dump(persisted_receipt)),
                    ).rowcount
                    if updated != 1:
                        raise EmailReplyReceiptConflict(
                            "email reply receipt changed concurrently"
                        )
            claim = db.execute(
                f"select * from ({EMAIL_REPLY_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if claim is not None:
                claim_row = self._email_reply_dispatch_claim_row(claim)
                claim_expected = {
                    "effect_digest": effect_digest,
                    "action_plan_id": action_plan_id,
                    "classification_id": classification_id,
                    "account_id": account_id,
                    "stable_message_identity": stable_message_identity,
                    "outgoing_message_id": outgoing_message_id,
                }
                if any(
                    claim_row[key] != value for key, value in claim_expected.items()
                ):
                    raise EmailReplyDispatchConflict(
                        "email reply receipt does not match its dispatch claim"
                    )
                if claim_row["status"] != "done":
                    if validated_claim_owner is None:
                        raise EmailReplyDispatchConflict(
                            "email reply claim completion requires its owner fence"
                        )
                    mutation_values = {"status": "done", "updated_at": self._now()}
                    updated = shared_action_lifecycle.transition(
                        db,
                        "email_reply",
                        "state",
                        values=mutation_values,
                        where_sql="action_identity=? and status in ('dispatching', 'retryable', 'uncertain') and owner_id=? and owner_generation=? and lease_token=?",
                        parameters=(
                            action_identity,
                            validated_claim_owner["owner_id"],
                            validated_claim_owner["generation"],
                            validated_claim_owner["lease_token"],
                        ),
                    ).rowcount
                    if updated != 1:
                        raise EmailReplyDispatchConflict(
                            "email reply dispatch owner fence changed"
                        )
            row = db.execute(
                f"select * from ({EMAIL_REPLY_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            persisted = self._email_reply_receipt_row(row)
        return persisted

    @staticmethod
    def _email_reply_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "outgoing_message_id": row["outgoing_message_id"],
            "provider_operation": row["provider_operation"],
            "provider_target": row["provider_target"],
            "provider_result_id": row["provider_result_id"],
            "provider_receipt": _json_load(
                row["provider_receipt_json"],
                field="provider_receipt_json",
                expected_type=dict,
            ),
            "display_excerpt": row["display_excerpt"],
            "created_at": row["created_at"],
        }

    def claim_email_unsubscribe_write(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        owner: Mapping[str, object],
        previous_effect_digest: str = "",
        task_id: int | None = None,
        task_execution_generation: str | None = None,
        task_lifecycle_version: str | None = None,
        task_action_type: str | None = None,
        audit_agent_run_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Atomically verify current authorization and fence one browser write."""

        if (task_id is None) != (task_execution_generation is None):
            raise ValueError(
                "task_id and task_execution_generation must be supplied together"
            )
        if task_id is not None:
            if not isinstance(task_id, int) or task_id <= 0:
                raise ValueError("task_id must be positive")
            if (
                not isinstance(task_execution_generation, str)
                or not task_execution_generation.strip()
            ):
                raise ValueError("task_execution_generation must be non-empty")
            if (
                not isinstance(task_lifecycle_version, str)
                or not task_lifecycle_version.strip()
            ):
                raise ValueError("task_lifecycle_version must be non-empty")
            if task_action_type != EmailAction.UNSUBSCRIBE.value:
                raise ValueError("task_action_type must be unsubscribe")
            if task_lifecycle_version == "email_unsubscribe_audited_v2":
                if (
                    not isinstance(audit_agent_run_id, int)
                    or isinstance(audit_agent_run_id, bool)
                    or audit_agent_run_id <= 0
                ):
                    raise ValueError("audit_agent_run_id must be positive")
            elif audit_agent_run_id is not None:
                raise ValueError("audit_agent_run_id requires the audited-v2 lifecycle")
        elif audit_agent_run_id is not None:
            raise ValueError("audit_agent_run_id requires a task binding")

        binding = _validate_unsubscribe_binding(
            action_identity=action_identity,
            effect_digest=effect_digest,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            stable_message_identity=stable_message_identity,
            thread_identity=thread_identity,
            entry_reference=entry_reference,
        )
        validated_operations = _validate_unsubscribe_operations(operations)
        expected_digest = email_unsubscribe_effect_digest(
            **{key: binding[key] for key in binding if key != "effect_digest"},
            operations=validated_operations,
            previous_effect_digest=previous_effect_digest,
        )
        if effect_digest != expected_digest:
            raise EmailUnsubscribeClaimConflict(
                "unsubscribe effect digest does not match accepted operations"
            )
        owner = _validate_email_unsubscribe_owner(owner)
        operations_json = _json_dump(validated_operations)
        claimed_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            if (
                db.execute(
                    f"select 1 from ({EMAIL_UNSUBSCRIBE_CANCELLATION_SQL}) where action_identity=?",
                    (action_identity,),
                ).fetchone()
                is not None
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe cancelled by principal"
                )
            task_join = ""
            task_predicates = ""
            task_args: tuple[object, ...] = ()
            if task_id is not None:
                task_join = f"join ({REPLY_TASK_CURRENT_SQL}) as tasks on tasks.id=?"
                task_predicates = """
                  and tasks.status='processing'
                  and tasks.channel='email'
                  and tasks.execution_generation=?
                  and tasks.trigger_message_id=?
                  and json_extract(tasks.trigger_message_json, '$.schema')=?
                  and json_extract(tasks.trigger_message_json, '$.action_type')=?
                  and json_extract(tasks.trigger_message_json, '$.lifecycle_version')=?
                """
                if audit_agent_run_id is not None:
                    task_join += f"""
                      join ({AGENT_RUN_SQL}) as audit_runs on audit_runs.id=?
                      join ({AGENT_RUN_SQL}) as consumer_runs
                        on consumer_runs.id=audit_runs.parent_agent_run_id
                    """
                    task_predicates += f"""
                  and audit_runs.reply_task_id=tasks.id
                  and audit_runs.execution_generation=tasks.execution_generation
                  and audit_runs.role='audit'
                  and audit_runs.status='running'
                  and trim(audit_runs.operation_id)<>''
                  and consumer_runs.reply_task_id=tasks.id
                  and consumer_runs.execution_generation=tasks.execution_generation
                  and consumer_runs.role='consumer'
                  and consumer_runs.status='completed'
                  and consumer_runs.proposal_revision=audit_runs.proposal_revision
                  and consumer_runs.id=(
                      select current_consumer.id
                      from ({AGENT_RUN_SQL}) as current_consumer
                      where current_consumer.reply_task_id=tasks.id
                        and current_consumer.execution_generation=
                            tasks.execution_generation
                        and current_consumer.role='consumer'
                        and current_consumer.status='completed'
                        and current_consumer.proposal_revision=
                            audit_runs.proposal_revision
                      order by current_consumer.turn_attempt desc,
                               current_consumer.id desc
                      limit 1
                  )
                  and audit_runs.id=(
                      select current_audit.id
                      from ({AGENT_RUN_SQL}) as current_audit
                      where current_audit.reply_task_id=tasks.id
                        and current_audit.execution_generation=
                            tasks.execution_generation
                        and current_audit.role='audit'
                        and current_audit.status='running'
                        and current_audit.proposal_revision=
                            audit_runs.proposal_revision
                      order by current_audit.turn_attempt desc,
                               current_audit.id desc
                      limit 1
                  )
                    """
                task_args = (
                    task_execution_generation,
                    action_identity,
                    "email_agent_action.v1",
                    EmailAction.UNSUBSCRIBE.value,
                    task_lifecycle_version,
                )
            live = db.execute(
                f"""
                select classifications.status as classification_status,
                       classifications.current_action_plan_id,
                       classifications.account_id as classification_account_id,
                       classifications.stable_message_identity
                           as classification_message_identity,
                       plans.action_plan_version, plans.account_id as plan_account_id,
                       plans.actions_json,
                       messages.account_id as message_account_id,
                       messages.thread_identity,
                       accounts.enabled, accounts.updated_at as account_updated_at
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_PLAN_SQL}) as plans
                  on plans.action_plan_id=?
                 and plans.classification_id=classifications.id
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.stable_message_identity=?
                join ({config_projection('email_accounts')}) as accounts on accounts.account_id=?
                {task_join}
                where classifications.id=?
                {task_predicates}
                """,
                tuple(
                    [action_plan_id, stable_message_identity, account_id]
                    + (
                        [task_id]
                        + (
                            [audit_agent_run_id]
                            if audit_agent_run_id is not None
                            else []
                        )
                        if task_id is not None
                        else []
                    )
                    + [classification_id]
                    + list(task_args)
                ),
            ).fetchone()
            expected_identity = email_action_identity(
                account_id=account_id,
                stable_message_identity=stable_message_identity,
                action_type=EmailAction.UNSUBSCRIBE,
                action_plan_version=action_plan_version,
            )
            authorized = (
                live is not None
                and action_identity == expected_identity
                and live["classification_status"] == "processed"
                and live["current_action_plan_id"] == action_plan_id
                and live["classification_account_id"] == account_id
                and live["classification_message_identity"] == stable_message_identity
                and live["action_plan_version"] == action_plan_version
                and live["plan_account_id"] == account_id
                and EmailAction.UNSUBSCRIBE.value
                in _json_load(
                    live["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
                and live["message_account_id"] == account_id
                and live["thread_identity"] == thread_identity
                and bool(live["enabled"])
            )
            if not authorized:
                return None
            receipt = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt is not None:
                persisted = self._email_unsubscribe_receipt_row(receipt)
                if persisted["effect_digest"] != effect_digest:
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe receipt belongs to a different accepted effect"
                    )
                return {"acquired": False, "status": "done"}
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            expected_claim = {
                **binding,
                "operations": validated_operations,
            }
            if audit_agent_run_id is not None:
                expected_claim["audit_agent_run_id"] = audit_agent_run_id
            if claim is not None:
                persisted = self._email_unsubscribe_claim_row(claim)
                if persisted["status"] == "awaiting_audit":
                    continuation = db.execute(
                        f"select * from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL}) where action_identity=?",
                        (action_identity,),
                    ).fetchone()
                    prior_effect = db.execute(
                        f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=? and effect_digest=?",
                        (action_identity, persisted["effect_digest"]),
                    ).fetchone()
                    if continuation is None or prior_effect is None:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation durable state is missing"
                        )
                    prior_operations = _json_load(
                        prior_effect["operations_json"],
                        field="operations_json",
                        expected_type=list,
                    )
                    controls = _json_load(
                        continuation["controls_json"],
                        field="controls_json",
                        expected_type=list,
                    )
                    if previous_effect_digest != persisted["effect_digest"]:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation prefix digest changed"
                        )
                    if (
                        validated_operations[:-1] != prior_operations
                        or len(validated_operations) != len(prior_operations) + 1
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation prefix is not append-only"
                        )
                    next_operation = validated_operations[-1]
                    control = next(
                        (
                            item
                            for item in controls
                            if item.get("reference")
                            == next_operation["target_reference"]
                        ),
                        None,
                    )
                    allowed_kinds = {
                        "form": {"submit_form"},
                        "link": {"click_confirmation"},
                        "button": {"click_confirmation"},
                        "confirmation_email": {"confirm_email"},
                        "email_otp": {"submit_form"},
                        "captcha_handoff": {
                            "click_confirmation",
                            "reconcile_handoff",
                        },
                        "credential_handoff": {"reconcile_handoff"},
                    }
                    if control is None:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation control is unknown"
                        )
                    if next_operation["kind"] not in allowed_kinds.get(
                        control.get("kind"), set()
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation control kind is invalid"
                        )
                    captcha_attempted = any(
                        item["kind"] == "click_confirmation"
                        and item["target_reference"] == control.get("reference")
                        for item in validated_operations[:-1]
                    )
                    if control.get("kind") == "captcha_handoff" and (
                        (
                            next_operation["kind"] == "click_confirmation"
                            and captcha_attempted
                        )
                        or (
                            next_operation["kind"] == "reconcile_handoff"
                            and not captcha_attempted
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe CAPTCHA continuation stage is invalid"
                        )
                    if any(
                        persisted[key] != binding[key]
                        for key in (
                            "action_identity",
                            "action_plan_id",
                            "action_plan_version",
                            "classification_id",
                            "account_id",
                            "stable_message_identity",
                            "thread_identity",
                            "entry_reference",
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation belongs to a different effect"
                        )
                    if (
                        persisted["owner_id"] == owner["owner_id"]
                        and persisted["owner_generation"] == owner["generation"]
                        and persisted["lease_token"] == owner["lease_token"]
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe continuation requires a fresh owner fence"
                        )
                    mutation_values = {
                        "effect_digest": effect_digest,
                        "operations_json": operations_json,
                        "owner_id": owner["owner_id"],
                        "owner_generation": owner["generation"],
                        "lease_token": owner["lease_token"],
                        "account_updated_at": live["account_updated_at"],
                        "audit_agent_run_id": audit_agent_run_id,
                        "status": "dispatching",
                        "phase": "prepared",
                        "claimed_at": claimed_at,
                        "updated_at": claimed_at,
                    }
                    shared_action_lifecycle.transition(
                        db,
                        "email_unsubscribe",
                        "state",
                        values=mutation_values,
                        where_sql="action_identity=? and status='awaiting_audit'",
                        parameters=(action_identity,),
                    )
                    insert_action(
                        db,
                        "email_unsubscribe_effects",
                        {
                            "action_identity": action_identity,
                            "effect_digest": effect_digest,
                            "previous_effect_digest": previous_effect_digest,
                            "operations_json": operations_json,
                            "audit_agent_run_id": audit_agent_run_id,
                            "created_at": claimed_at,
                        },
                    )
                    mutation_rows = db.execute(
                        f"""select rowid from ({EMAIL_UNSUBSCRIBE_CONTINUATION_LOCATOR_SQL}) where action_identity=?""",
                        (action_identity,),
                    ).fetchall()
                    for source_row in mutation_rows:
                        delete_email_action(
                            db, "email_unsubscribe_continuations", source_row["rowid"]
                        )
                    row = db.execute(
                        f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                        (action_identity,),
                    ).fetchone()
                    assert row is not None
                    return {
                        **self._email_unsubscribe_claim_row(row),
                        "acquired": True,
                        "executed_prefix_length": len(prior_operations),
                    }
                if (
                    audit_agent_run_id is None
                    and persisted["audit_agent_run_id"] is not None
                    and (
                        persisted["owner_id"] != owner["owner_id"]
                        or persisted["owner_generation"] != owner["generation"]
                        or persisted["lease_token"] != owner["lease_token"]
                    )
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "audited unsubscribe claim requires its exact owner fence"
                    )
                if any(
                    persisted[key] != value for key, value in expected_claim.items()
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe identity is bound to another accepted effect"
                    )
                return {**persisted, "acquired": False}
            else:
                if previous_effect_digest:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe initial effect cannot skip its prefix"
                    )
                if len(validated_operations) != 1 or validated_operations[0][
                    "kind"
                ] not in {"open_entry", "post_one_click"}:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe initial effect must contain one entry operation"
                    )
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "claim",
                    values={
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "action_plan_id": action_plan_id,
                        "action_plan_version": action_plan_version,
                        "classification_id": classification_id,
                        "account_id": account_id,
                        "stable_message_identity": stable_message_identity,
                        "thread_identity": thread_identity,
                        "entry_reference": entry_reference,
                        "operations_json": operations_json,
                        "owner_id": owner["owner_id"],
                        "owner_generation": owner["generation"],
                        "lease_token": owner["lease_token"],
                        "account_updated_at": live["account_updated_at"],
                        "status": "dispatching",
                        "phase": "prepared",
                        "audit_agent_run_id": audit_agent_run_id,
                        "claimed_at": claimed_at,
                        "updated_at": claimed_at,
                    },
                )
                insert_action(
                    db,
                    "email_unsubscribe_effects",
                    {
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "previous_effect_digest": "",
                        "operations_json": operations_json,
                        "audit_agent_run_id": audit_agent_run_id,
                        "created_at": claimed_at,
                    },
                )
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return {
                **self._email_unsubscribe_claim_row(row),
                "acquired": True,
                "executed_prefix_length": 0,
            }

    def get_email_unsubscribe_claim(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_unsubscribe_claim_row(row)

    def get_email_unsubscribe_state_snapshot(
        self,
        action_identity: str,
    ) -> dict[str, Any]:
        """Read the claim, continuation, and complete bounded lineage once.

        The explicit deferred transaction pins one SQLite read view.  The
        continuation driver can therefore validate up to the durable maximum
        without opening one connection per historical effect.
        This method is read-only and deliberately performs no recovery writes.
        """

        with self._connect() as db:
            db.execute("begin")
            claim_row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            continuation_row = db.execute(
                f"""
                select continuation.*, effect.previous_effect_digest,
                       effect.operations_json
                from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL}) as continuation
                join ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) as effect
                  on effect.action_identity=continuation.action_identity
                 and effect.effect_digest=continuation.effect_digest
                where continuation.action_identity=?
                """,
                (action_identity,),
            ).fetchone()
            receipt_row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            effect_rows = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) "
                "where action_identity=? limit ?",
                (
                    action_identity,
                    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS + 1,
                ),
            ).fetchall()
            if len(effect_rows) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
                raise EmailPersistenceCorruption(
                    "unsubscribe snapshot exceeds durable continuation operation limit"
                )
            claim = (
                None
                if claim_row is None
                else self._email_unsubscribe_claim_row(claim_row)
            )
            continuation = (
                None
                if continuation_row is None
                else self._email_unsubscribe_continuation_row(continuation_row)
            )
            effects = tuple(
                self._email_unsubscribe_effect_row(row) for row in effect_rows
            )
            receipt = (
                None
                if receipt_row is None
                else self._email_unsubscribe_receipt_row(receipt_row)
            )
            db.rollback()
            return {
                "claim": claim,
                "continuation": continuation,
                "effects": effects,
                "receipt": receipt,
            }

    def get_email_unsubscribe_terminal_snapshot(
        self,
        action_identity: str,
        effect_digest: str | None = None,
        *,
        task_id: int | None = None,
        task_execution_generation: str | None = None,
        expected_audit_agent_run_id: int | None = None,
        expected_owner: Mapping[str, object] | None = None,
        expected_effect: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Atomically validate and project one durable terminal readback."""

        action_identity = _validate_unsubscribe_opaque(
            action_identity,
            field="action_identity",
        )
        if effect_digest is not None and (
            not isinstance(effect_digest, str)
            or _SHA256_HEX.fullmatch(effect_digest) is None
        ):
            raise ValueError("effect_digest must be canonical sha256 hex")
        if (task_id is None) != (task_execution_generation is None):
            raise ValueError(
                "task_id and task_execution_generation must be supplied together"
            )
        if task_id is not None and (
            not isinstance(task_id, int)
            or isinstance(task_id, bool)
            or task_id <= 0
            or not isinstance(task_execution_generation, str)
            or not task_execution_generation.strip()
        ):
            raise ValueError("terminal snapshot task binding is invalid")
        if expected_audit_agent_run_id is not None and (
            not isinstance(expected_audit_agent_run_id, int)
            or isinstance(expected_audit_agent_run_id, bool)
            or expected_audit_agent_run_id <= 0
        ):
            raise ValueError("expected_audit_agent_run_id must be positive")
        owner = (
            None
            if expected_owner is None
            else _validate_email_unsubscribe_owner(expected_owner)
        )
        if (expected_audit_agent_run_id is None) != (owner is None):
            raise ValueError("expected Audit run and owner must be supplied together")
        validated_expected_effect = (
            None
            if expected_effect is None
            else _validate_terminal_expected_effect(expected_effect)
        )
        if (
            validated_expected_effect is not None
            and validated_expected_effect["action_identity"] != action_identity
        ):
            raise ValueError("expected terminal effect identity changed")

        with self._connect() as db:
            db.execute("begin")
            claim_row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            claim = (
                None
                if claim_row is None
                else self._email_unsubscribe_claim_row(claim_row)
            )
            receipt_row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt_row is None:
                if claim is not None and claim["status"] == "done":
                    raise EmailPersistenceCorruption(
                        "done unsubscribe claim has no durable terminal receipt"
                    )
                db.rollback()
                return None
            if claim is None:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal receipt has no durable claim"
                )
            durable_effect_digest = str(claim["effect_digest"])
            if effect_digest is None:
                effect_digest = durable_effect_digest
            elif effect_digest != durable_effect_digest:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect digest changed"
                )
            receipt = self._email_unsubscribe_receipt_row(receipt_row)
            effect_rows = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) "
                "where action_identity=? limit ?",
                (
                    action_identity,
                    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS + 1,
                ),
            ).fetchall()
            if len(effect_rows) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage exceeds its durable limit"
                )
            effects = {
                str(row["effect_digest"]): self._email_unsubscribe_effect_row(row)
                for row in effect_rows
            }
            steps = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) "
                "where action_identity=? order by sequence",
                (action_identity,),
            ).fetchall()
            self._validate_email_unsubscribe_terminal_snapshot(
                db,
                claim=claim,
                receipt=receipt,
                effects=effects,
                steps=steps,
                effect_digest=effect_digest,
                task_id=task_id,
                task_execution_generation=task_execution_generation,
                expected_audit_agent_run_id=expected_audit_agent_run_id,
                expected_owner=owner,
                expected_effect=validated_expected_effect,
            )
            projected_steps = [
                {
                    key: row[key]
                    for key in ("sequence", "operation", "state", "reference")
                }
                for row in steps
            ]
            safe_receipt = {
                key: receipt[key]
                for key in (
                    "outcome",
                    "receipt_id",
                    "evidence",
                    "result_text",
                    "observation_digest",
                    "started_at",
                    "completed_at",
                )
            }
            db.rollback()
            return {
                "receipt": safe_receipt,
                "steps": projected_steps,
            }

    def _validate_email_unsubscribe_terminal_snapshot(
        self,
        db: sqlite3.Connection,
        *,
        claim: Mapping[str, object],
        receipt: Mapping[str, object],
        effects: Mapping[str, Mapping[str, object]],
        steps: Sequence[sqlite3.Row],
        effect_digest: str,
        task_id: int | None,
        task_execution_generation: str | None,
        expected_audit_agent_run_id: int | None,
        expected_owner: Mapping[str, object] | None,
        expected_effect: Mapping[str, object] | None,
    ) -> None:
        if (
            claim.get("status") != "done"
            or claim.get("phase") != "terminal"
            or claim.get("action_identity") != receipt.get("action_identity")
            or claim.get("effect_digest") != effect_digest
            or receipt.get("effect_digest") != effect_digest
            or receipt.get("outcome") not in _EMAIL_UNSUBSCRIBE_OUTCOMES
            or any(
                receipt.get(key) != claim.get(key)
                for key in (
                    "action_plan_id",
                    "action_plan_version",
                    "classification_id",
                    "account_id",
                    "stable_message_identity",
                    "thread_identity",
                    "entry_reference",
                )
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal receipt identity is invalid"
            )
        try:
            _validate_email_unsubscribe_owner(
                {
                    "owner_id": claim["owner_id"],
                    "generation": claim["owner_generation"],
                    "lease_token": claim["lease_token"],
                }
            )
            _validate_unsubscribe_opaque(
                receipt["receipt_id"],
                field="receipt_id",
            )
            _validate_unsubscribe_opaque(receipt["evidence"], field="evidence")
        except (KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal snapshot contains unsafe durable state"
            ) from exc
        if expected_owner is not None and any(
            claim.get(claim_key) != expected_owner.get(owner_key)
            for claim_key, owner_key in (
                ("owner_id", "owner_id"),
                ("owner_generation", "generation"),
                ("lease_token", "lease_token"),
            )
        ):
            raise EmailPersistenceCorruption("unsubscribe terminal owner fence changed")

        validated_effects: dict[str, dict[str, object]] = {}
        for digest, effect in effects.items():
            try:
                operations = _validate_unsubscribe_operations(effect["operations"])
                expected_digest = email_unsubscribe_effect_digest(
                    action_identity=str(claim["action_identity"]),
                    action_plan_id=str(claim["action_plan_id"]),
                    action_plan_version=int(claim["action_plan_version"]),
                    classification_id=int(claim["classification_id"]),
                    account_id=str(claim["account_id"]),
                    stable_message_identity=str(claim["stable_message_identity"]),
                    thread_identity=str(claim["thread_identity"]),
                    entry_reference=str(claim["entry_reference"]),
                    operations=operations,
                    previous_effect_digest=str(effect["previous_effect_digest"]),
                )
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect contains invalid durable state"
                ) from exc
            if digest != expected_digest or effect.get("effect_digest") != digest:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect digest is invalid"
                )
            validated_effects[digest] = {
                **effect,
                "operations": operations,
            }
        head = validated_effects.get(effect_digest)
        if head is None or head["operations"] != claim.get("operations"):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal head effect does not match its claim"
            )
        if expected_effect is not None and (
            any(
                claim.get(field) != expected_effect.get(field)
                for field in (
                    "action_identity",
                    "action_plan_id",
                    "action_plan_version",
                    "classification_id",
                    "account_id",
                    "stable_message_identity",
                    "thread_identity",
                    "entry_reference",
                )
            )
            or head["operations"] != expected_effect.get("operations")
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect does not match accepted action"
            )
        chain: list[dict[str, object]] = []
        current = head
        seen: set[str] = set()
        while True:
            digest = str(current["effect_digest"])
            if digest in seen:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage contains a cycle"
                )
            seen.add(digest)
            chain.append(current)
            previous_digest = str(current["previous_effect_digest"])
            if not previous_digest:
                break
            previous = validated_effects.get(previous_digest)
            if (
                previous is None
                or current["operations"][:-1] != previous["operations"]
                or len(current["operations"]) != len(previous["operations"]) + 1
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect lineage is not append-only"
                )
            current = previous
        chain.reverse()
        if len(chain) != len(validated_effects):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect lineage contains an orphan branch"
            )
        chain_by_length = {
            len(effect["operations"]): str(effect["effect_digest"]) for effect in chain
        }
        if len(steps) != len(head["operations"]):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal journal is not contiguous or effect-bound"
            )
        for sequence, step in enumerate(steps, start=1):
            try:
                for field in ("operation", "state", "reference"):
                    _validate_unsubscribe_opaque(step[field], field=field)
            except (TypeError, ValueError) as exc:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal journal contains unsafe durable state"
                ) from exc
            if (
                step["sequence"] != sequence
                or chain_by_length.get(sequence) != step["effect_digest"]
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal journal is not contiguous or effect-bound"
                )

        original_audit_run_id = claim.get("audit_agent_run_id")
        if head.get("audit_agent_run_id") != original_audit_run_id:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal Audit binding is inconsistent"
            )
        if original_audit_run_id is None:
            if task_id is not None or expected_audit_agent_run_id is not None:
                raise EmailPersistenceCorruption(
                    "audited unsubscribe terminal is missing its original Audit run"
                )
            return
        self._validate_email_unsubscribe_effect_audit_lineage(
            db,
            chain=chain,
            claim=claim,
            task_id=task_id,
            task_execution_generation=task_execution_generation,
            expected_audit_agent_run_id=expected_audit_agent_run_id,
        )
        classification = db.execute(
            f"""
            select classifications.id, classifications.account_id,
                   classifications.stable_message_identity,
                   classifications.current_action_plan_id,
                   messages.thread_identity as message_thread_identity
            from ({EMAIL_CLASSIFICATION_SQL}) as classifications
            join ({EMAIL_MESSAGE_SQL}) as messages
              on messages.account_id=classifications.account_id
             and messages.stable_message_identity=
                 classifications.stable_message_identity
            where classifications.id=?
            """,
            (claim["classification_id"],),
        ).fetchone()
        lineage = (
            None
            if classification is None
            else _audited_unsubscribe_lineage(
                db,
                receipt=db.execute(
                    f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                    (claim["action_identity"],),
                ).fetchone(),
                classification=classification,
            )
        )
        if (
            lineage is None
            or (task_id is not None and lineage["task_id"] != task_id)
            or (
                task_execution_generation is not None
                and db.execute(
                    f"select execution_generation from ({REPLY_TASK_SQL}) where id=?",
                    (lineage["task_id"],),
                ).fetchone()[0]
                != task_execution_generation
            )
            or (
                expected_audit_agent_run_id is not None
                and original_audit_run_id != expected_audit_agent_run_id
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal historical Audit lineage is invalid"
            )

    def _validate_email_unsubscribe_effect_audit_lineage(
        self,
        db: sqlite3.Connection,
        *,
        chain: Sequence[Mapping[str, object]],
        claim: Mapping[str, object],
        task_id: int | None,
        task_execution_generation: str | None,
        expected_audit_agent_run_id: int | None,
    ) -> None:
        """Bind every accepted effect prefix to its exact Consumer/Audit round."""

        effect_audit_run_ids: list[int] = []
        previous_revision: int | None = None
        lineage_task_id: int | None = None
        lineage_generation: str | None = None
        # The task's generation as it is right now, read from the same joined
        # row. The caller must be the task's current generation; the receipt
        # need not have been earned in it.
        lineage_live_task_generation: str | None = None
        for depth, effect in enumerate(chain, start=1):
            audit_agent_run_id = effect.get("audit_agent_run_id")
            if (
                not isinstance(audit_agent_run_id, int)
                or isinstance(audit_agent_run_id, bool)
                or audit_agent_run_id <= 0
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect is missing its Audit run"
                )
            row = db.execute(
                f"""
                select audit.id as audit_id, audit.reply_task_id,
                       audit.execution_generation, audit.role as audit_role,
                       audit.status as audit_status,
                       audit.proposal_revision as audit_revision,
                       audit.turn_attempt as audit_turn_attempt,
                       audit.parent_agent_run_id as audit_parent_id,
                       audit.operation_id as audit_operation_id,
                       consumer.id as consumer_id,
                       consumer.reply_task_id as consumer_task_id,
                       consumer.execution_generation as consumer_generation,
                       consumer.role as consumer_role,
                       consumer.status as consumer_status,
                       consumer.proposal_revision as consumer_revision,
                       consumer.parent_agent_run_id as consumer_parent_id,
                       consumer.operation_id as consumer_operation_id,
                       tasks.channel as task_channel,
                       tasks.trigger_message_id,
                       tasks.trigger_message_json,
                       tasks.input_compacted, tasks.input_provenance_json,
                       tasks.execution_generation as task_generation
                from ({AGENT_RUN_SQL}) as audit
                join ({AGENT_RUN_SQL}) as consumer
                  on consumer.id=audit.parent_agent_run_id
                join ({REPLY_TASK_CURRENT_SQL}) as tasks on tasks.id=audit.reply_task_id
                where audit.id=?
                """,
                (audit_agent_run_id,),
            ).fetchone()
            is_current_head = (
                depth == len(chain)
                and expected_audit_agent_run_id == audit_agent_run_id
            )
            allowed_statuses = {"completed"}
            if depth == len(chain):
                allowed_statuses.add("failed")
            if is_current_head:
                allowed_statuses.add("running")
            task_payload = None
            payload_entries = None
            if row is not None:
                try:
                    task_payload = json.loads(
                        AutoReplyStore._compact_reply_input_json(row["input_provenance_json"])
                        if row["input_compacted"] else str(row["trigger_message_json"])
                    )
                    payload_entries = task_payload.get("unsubscribe_entries")
                except (AttributeError, TypeError, ValueError, RecursionError, KeyError):
                    task_payload = None
                    payload_entries = None
            expected_payload = {
                "schema": "email_agent_action.v1",
                "lifecycle_version": "email_unsubscribe_audited_v2",
                "action_type": EmailAction.UNSUBSCRIBE.value,
                "action_identity": claim["action_identity"],
                "action_plan_id": claim["action_plan_id"],
                "action_plan_version": claim["action_plan_version"],
                "classification_id": claim["classification_id"],
                "account_id": claim["account_id"],
                "stable_message_identity": claim["stable_message_identity"],
                "thread_identity": claim["thread_identity"],
            }
            if (
                row is None
                or row["audit_id"] != audit_agent_run_id
                or row["audit_role"] != "audit"
                or row["audit_status"] not in allowed_statuses
                or not isinstance(row["audit_turn_attempt"], int)
                or row["audit_turn_attempt"] < 0
                or not str(row["audit_operation_id"]).strip()
                or row["consumer_id"] != row["audit_parent_id"]
                or row["consumer_role"] != "consumer"
                or row["consumer_status"] != "completed"
                or row["consumer_revision"] != row["audit_revision"]
                or str(row["consumer_operation_id"])
                or row["consumer_task_id"] != row["reply_task_id"]
                or row["consumer_generation"] != row["execution_generation"]
                or row["task_channel"] != "email"
                or row["trigger_message_id"] != claim["action_identity"]
                # Deliberately NOT compared to row["task_generation"]: that is
                # the task's generation right now, and a rerun rotates it while
                # the Audit run and its effect rows keep the generation they
                # were written in. Requiring them equal made a finished receipt
                # unreadable the moment anything reran the task, which is the
                # one situation the receipt exists to answer. Consistency
                # inside the lineage is still enforced, by consumer_generation
                # against execution_generation above and by the chain rebuild
                # below; that the caller is the task's current generation is
                # checked separately, against the live value.
                or not isinstance(task_payload, dict)
                or any(
                    task_payload.get(field) != expected
                    for field, expected in expected_payload.items()
                )
                or not isinstance(payload_entries, list)
                or not any(
                    isinstance(entry, dict)
                    and entry.get("reference") == claim["entry_reference"]
                    for entry in payload_entries
                )
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect Audit lineage is invalid"
                )
            if depth == len(chain):
                owner_id = claim.get("owner_id")
                expected_owner_suffix = f":{audit_agent_run_id}"
                if (
                    not isinstance(owner_id, str)
                    or not owner_id.endswith(expected_owner_suffix)
                    or len(owner_id) <= len(expected_owner_suffix)
                    or claim.get("owner_generation")
                    != max(1, int(row["audit_turn_attempt"]) + 1)
                ):
                    raise EmailPersistenceCorruption(
                        "unsubscribe terminal owner is not bound to its Audit run"
                    )
            current_task_id = int(row["reply_task_id"])
            current_generation = str(row["execution_generation"])
            live_task_generation = str(row["task_generation"])
            if lineage_task_id is None:
                lineage_task_id = current_task_id
                lineage_generation = current_generation
                lineage_live_task_generation = live_task_generation
            elif (
                current_task_id != lineage_task_id
                or current_generation != lineage_generation
            ):
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect Audit lineage changed task"
                )
            current_revision = int(row["audit_revision"])
            if previous_revision is not None and current_revision <= previous_revision:
                raise EmailPersistenceCorruption(
                    "unsubscribe terminal effect Audit revisions are not increasing"
                )
            previous_revision = current_revision
            effect_audit_run_ids.append(audit_agent_run_id)

        if (
            lineage_task_id is None
            or (task_id is not None and lineage_task_id != task_id)
            or (
                task_execution_generation is not None
                and lineage_live_task_generation != task_execution_generation
            )
        ):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect Audit task binding is invalid"
            )
        audited_chain = _audited_unsubscribe_run_chain(
            db,
            task_id=lineage_task_id,
            execution_generation=str(lineage_generation),
            final_audit_run_id=effect_audit_run_ids[-1],
        )
        if audited_chain is None:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect Audit lineage is disconnected"
            )
        _consumer_run_ids, audit_run_ids = audited_chain
        positions = {run_id: index for index, run_id in enumerate(audit_run_ids)}
        try:
            effect_positions = [positions[run_id] for run_id in effect_audit_run_ids]
        except KeyError as exc:
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect Audit lineage is incomplete"
            ) from exc
        if effect_positions != sorted(effect_positions):
            raise EmailPersistenceCorruption(
                "unsubscribe terminal effect Audit lineage is out of order"
            )

    def get_email_unsubscribe_effect(
        self,
        action_identity: str,
        effect_digest: str,
    ) -> dict[str, Any] | None:
        """Return one redacted durable effect without exposing provider URLs."""

        with self._connect() as db:
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) "
                "where action_identity=? and effect_digest=?",
                (action_identity, effect_digest),
            ).fetchone()
        if row is None:
            return None
        return self._email_unsubscribe_effect_row(row)

    @staticmethod
    def _email_unsubscribe_effect_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "previous_effect_digest": row["previous_effect_digest"],
            "operations": _json_load(
                row["operations_json"],
                field="operations_json",
                expected_type=list,
            ),
            "audit_agent_run_id": row["audit_agent_run_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _email_unsubscribe_claim_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "entry_reference": row["entry_reference"],
            "operations": _json_load(
                row["operations_json"],
                field="operations_json",
                expected_type=list,
            ),
            "owner_id": row["owner_id"],
            "owner_generation": row["owner_generation"],
            "lease_token": row["lease_token"],
            "account_updated_at": row["account_updated_at"],
            "status": row["status"],
            "phase": row["phase"],
            "audit_agent_run_id": row["audit_agent_run_id"],
            "claimed_at": row["claimed_at"],
            "updated_at": row["updated_at"],
        }

    def persist_email_unsubscribe_continuation(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        controls: Sequence[Mapping[str, object]],
        observation_reference: str,
        final_step: Mapping[str, object],
        owner: Mapping[str, object],
        previous_effect_digest: str = "",
    ) -> dict[str, Any]:
        del action_plan_id, action_plan_version, classification_id, account_id
        del stable_message_identity, thread_identity, entry_reference
        validated_operations = _validate_unsubscribe_operations(operations)
        validated_controls = _validate_unsubscribe_controls(controls)
        observation_reference = _validate_unsubscribe_opaque(
            observation_reference, field="observation_reference"
        )
        owner = _validate_email_unsubscribe_owner(owner)
        if set(final_step) != {"sequence", "operation", "state", "reference"}:
            raise ValueError("unsubscribe continuation step fields are invalid")
        sequence = final_step["sequence"]
        _require_positive_int(sequence, field="sequence")
        step = {
            "operation": _validate_unsubscribe_opaque(
                final_step["operation"], field="operation"
            ),
            "state": _validate_unsubscribe_opaque(final_step["state"], field="state"),
            "reference": _validate_unsubscribe_opaque(
                final_step["reference"], field="reference"
            ),
        }
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"""
                select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL})
                where action_identity=? and effect_digest=? and status='dispatching'
                  and owner_id=? and owner_generation=? and lease_token=?
                """,
                (
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            effect_row = db.execute(
                f"""
                select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL})
                where action_identity=? and effect_digest=?
                """,
                (action_identity, effect_digest),
            ).fetchone()
            if claim is None or effect_row is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation owner fence changed"
                )
            if (
                _json_load(
                    effect_row["operations_json"],
                    field="operations_json",
                    expected_type=list,
                )
                != validated_operations
                or effect_row["previous_effect_digest"] != previous_effect_digest
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation effect binding changed"
                )
            previous = db.execute(
                f"select coalesce(max(sequence), 0) from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()[0]
            if sequence != previous + 1 or sequence != len(validated_operations):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation journal is not contiguous"
                )
            insert_action(
                db,
                "email_unsubscribe_steps",
                {
                    "action_identity": action_identity,
                    "effect_digest": effect_digest,
                    "sequence": sequence,
                    "operation": step["operation"],
                    "state": step["state"],
                    "reference": step["reference"],
                    "created_at": now,
                },
            )
            insert_email_action(
                db,
                "email_unsubscribe_continuations",
                {
                    "action_identity": action_identity,
                    "effect_digest": effect_digest,
                    "observation_reference": observation_reference,
                    "controls_json": _json_dump(validated_controls),
                    "created_at": now,
                    "updated_at": now,
                },
                update_continuation_conflict=True,
            )
            mutation_values = {"status": "awaiting_audit", "updated_at": now}
            updated = shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "state",
                values=mutation_values,
                where_sql="action_identity=? and effect_digest=? and status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe continuation owner fence changed"
                )
        continuation = self.get_email_unsubscribe_continuation(action_identity)
        assert continuation is not None
        return continuation

    def get_email_unsubscribe_continuation(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"""
                select continuation.*, effect.previous_effect_digest,
                       effect.operations_json
                from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL}) as continuation
                join ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) as effect
                  on effect.action_identity=continuation.action_identity
                 and effect.effect_digest=continuation.effect_digest
                where continuation.action_identity=?
                """,
                (action_identity,),
            ).fetchone()
        if row is None:
            return None
        return self._email_unsubscribe_continuation_row(row)

    @staticmethod
    def _email_unsubscribe_continuation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "previous_effect_digest": row["previous_effect_digest"],
            "operations": _json_load(
                row["operations_json"], field="operations_json", expected_type=list
            ),
            "controls": _json_load(
                row["controls_json"], field="controls_json", expected_type=list
            ),
            "observation_reference": row["observation_reference"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def append_email_unsubscribe_step(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        sequence: int,
        operation: str,
        state: str,
        reference: str,
        owner: Mapping[str, object],
    ) -> dict[str, Any]:
        owner = _validate_email_unsubscribe_owner(owner)
        _require_positive_int(sequence, field="sequence")
        operation = _validate_unsubscribe_opaque(operation, field="operation")
        state = _validate_unsubscribe_opaque(state, field="state")
        reference = _validate_unsubscribe_opaque(reference, field="reference")
        created_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"""
                select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL})
                where action_identity=? and effect_digest=?
                  and status='dispatching' and owner_id=?
                  and owner_generation=? and lease_token=?
                """,
                (
                    action_identity,
                    effect_digest,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).fetchone()
            if claim is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe journal owner fence changed"
                )
            existing = db.execute(
                f"""
                select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                where action_identity=? and sequence=?
                """,
                (action_identity, sequence),
            ).fetchone()
            if existing is not None:
                persisted = self._email_unsubscribe_step_row(existing)
                if {
                    key: persisted[key] for key in ("operation", "state", "reference")
                } != {
                    "operation": operation,
                    "state": state,
                    "reference": reference,
                } or existing["effect_digest"] != effect_digest:
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe journal sequence is bound differently"
                    )
                return persisted
            previous = db.execute(
                f"""
                select coalesce(max(sequence), 0) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                where action_identity=?
                """,
                (action_identity,),
            ).fetchone()[0]
            if sequence != previous + 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe journal sequence is not contiguous"
                )
            cursor = insert_action(
                db,
                "email_unsubscribe_steps",
                {
                    "action_identity": action_identity,
                    "effect_digest": effect_digest,
                    "sequence": sequence,
                    "operation": operation,
                    "state": state,
                    "reference": reference,
                    "created_at": created_at,
                },
            )
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where id=?",
                (cursor.lastrowid,),
            ).fetchone()
            assert row is not None
            return self._email_unsubscribe_step_row(row)

    def list_email_unsubscribe_steps(
        self,
        action_identity: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                where action_identity=? order by sequence
                """,
                (action_identity,),
            ).fetchall()
        return [self._email_unsubscribe_step_row(row) for row in rows]

    @staticmethod
    def _email_unsubscribe_step_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sequence": row["sequence"],
            "operation": row["operation"],
            "state": row["state"],
            "reference": row["reference"],
            "created_at": row["created_at"],
        }

    def list_orphaned_email_unsubscribe_claim_owners(
        self,
    ) -> list[dict[str, object]]:
        """Owners of dispatching claims whose Audit run is no longer alive.

        A browser claim is taken by one Audit run and released by that same
        run's owner fence. When the run dies before releasing it, nothing can:
        a later run fails the fence, and the identity conflicts forever, so the
        task can never be retried. Eighteen claims sat in this state on
        2026-09-11, every one owned by an Audit run that had already failed.

        Liveness is read from the run itself rather than from a lease clock,
        because the claim's owner id names the run and the run's terminal
        status is the fact that matters.
        """
        with self._connect() as db:
            rows = db.execute(
                f"""
                select claims.action_identity, claims.effect_digest,
                       claims.owner_id, claims.owner_generation,
                       claims.lease_token, runs.status as run_status
                from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) as claims
                join ({AGENT_RUN_SQL}) as runs
                  on runs.id=claims.audit_agent_run_id
                where claims.status in ('dispatching', 'uncertain')
                  and runs.status in ('failed', 'completed')
                order by claims.action_identity
                """
            ).fetchall()
        return [
            {
                "action_identity": row["action_identity"],
                "effect_digest": row["effect_digest"],
                "owner": {
                    "owner_id": row["owner_id"],
                    "generation": row["owner_generation"],
                    "lease_token": row["lease_token"],
                },
            }
            for row in rows
        ]

    def release_orphaned_email_unsubscribe_claim(self, action_identity: str) -> bool:
        """Delete a claim stranded by a dead Audit run that wrote nothing.

        release_email_unsubscribe_failed_operation does this on the normal
        path, keyed on the live owner fence. A run that dies never reaches it,
        and no later run can present that fence, so the claim blocks its action
        for good. This is the same release keyed on the orphan condition
        instead: the owning run has reached a terminal status, so no one is
        going to come back for it.

        The safety test is unchanged and is the one that matters -- no steps,
        no receipts, no continuation. With none of those the browser
        demonstrably persisted nothing, so there is no completed action to
        lose. A claim that did leave durable state is left alone for a person
        to judge; returning False says so.
        """
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"""
                select claims.effect_digest, runs.status as run_status
                from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) as claims
                join ({AGENT_RUN_SQL}) as runs
                  on runs.id=claims.audit_agent_run_id
                where claims.action_identity=?
                  and claims.status in ('dispatching', 'uncertain')
                """,
                (action_identity,),
            ).fetchone()
            if claim is None or claim["run_status"] not in {"failed", "completed"}:
                return False
            related = db.execute(
                f"""
                select
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                     where action_identity=?) as steps,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                     where action_identity=?) as receipts,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL})
                     where action_identity=?) as continuations
                """,
                (action_identity, action_identity, action_identity),
            ).fetchone()
            if related is None or any(int(related[name]) for name in related.keys()):
                return False
            mutation_rows = db.execute(
                f"""select action_identity,effect_digest from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=? and effect_digest=?""",
                (action_identity, claim["effect_digest"]),
            ).fetchall()
            for source_row in mutation_rows:
                delete_action(
                    db,
                    "email_unsubscribe_effects",
                    (source_row["action_identity"], source_row["effect_digest"]),
                )
            return (
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "retire",
                    where_sql="action_identity=?",
                    parameters=(action_identity,),
                ).rowcount
                == 1
            )

    def repair_email_unsubscribe_effect_journal(
        self,
        action_identity: str,
    ) -> dict[str, Any]:
        """Make an effect's operations describe the journal actually bound to it.

        A durable step is validated against ``operations[sequence - 1]`` of the
        effect it carries, where ``sequence`` counts every step the action has
        ever recorded. The audited lifecycle satisfied that by making each
        continuation effect carry the whole accepted prefix. The direct
        lifecycle briefly wrote effects that declared only the entry
        operation, so a run that clicked one control left a step indexing past
        its own effect -- and because the integrity check scans every step in
        the table, that one row made EmailStore refuse to open at all.

        This rebuilds the effect from the journal it owns: one entry per step
        of the action, in sequence order, so every index resolves. The steps,
        the claim and the receipt are repointed to the recomputed digest in
        the same transaction, and the superseded effect row is removed only
        after nothing references it. Nothing about what happened is changed --
        the operations written are the ones the journal already records.

        Returns what it changed; an action whose effects already cover their
        journal is left alone and reported as ``{"repaired": False}``.
        """

        action_identity = _validate_unsubscribe_opaque(
            action_identity, field="action_identity"
        )
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            steps = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=? "
                "order by sequence",
                (action_identity,),
            ).fetchall()
            if claim is None or not steps:
                return {"repaired": False, "reason": "no claim or no journal"}
            effects = {
                row["effect_digest"]: row
                for row in db.execute(
                    f"select * from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=?",
                    (action_identity,),
                )
            }
            undercovered = sorted(
                {
                    step["effect_digest"]
                    for step in steps
                    if step["effect_digest"] in effects
                    and step["sequence"]
                    > len(
                        _json_load(
                            effects[step["effect_digest"]]["operations_json"],
                            field="operations_json",
                            expected_type=list,
                        )
                    )
                }
            )
            if not undercovered:
                return {"repaired": False, "reason": "every effect covers its journal"}
            operations = [
                {
                    "operation_reference": "unsubscribe-op:"
                    + sha256(
                        f"journaled:{step['sequence']}\n{step['reference']}".encode()
                    ).hexdigest(),
                    "kind": step["operation"],
                    "target_reference": step["reference"],
                }
                for step in steps
            ]
            validated = _validate_unsubscribe_operations(operations)
            repaired: list[dict[str, Any]] = []
            for stale_digest in undercovered:
                stale = effects[stale_digest]
                digest = email_unsubscribe_effect_digest(
                    action_identity=action_identity,
                    action_plan_id=claim["action_plan_id"],
                    action_plan_version=claim["action_plan_version"],
                    classification_id=claim["classification_id"],
                    account_id=claim["account_id"],
                    stable_message_identity=claim["stable_message_identity"],
                    thread_identity=claim["thread_identity"],
                    entry_reference=claim["entry_reference"],
                    operations=validated,
                    previous_effect_digest=stale["previous_effect_digest"],
                )
                if digest == stale_digest:
                    continue
                insert_action(
                    db,
                    "email_unsubscribe_effects",
                    {
                        "action_identity": action_identity,
                        "effect_digest": digest,
                        "previous_effect_digest": stale["previous_effect_digest"],
                        "operations_json": _json_dump(validated),
                        "created_at": stale["created_at"],
                    },
                    ignore=True,
                )
                mutation_values = {"effect_digest": digest}
                mutation_rows = db.execute(
                    f"""select id from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=? and effect_digest=?""",
                    (action_identity, stale_digest),
                ).fetchall()
                moved_steps = sum(
                    update_action(
                        db, "email_unsubscribe_steps", source_row["id"], mutation_values
                    ).rowcount
                    for source_row in mutation_rows
                )
                mutation_timestamp = db.execute("select current_timestamp").fetchone()[
                    0
                ]
                mutation_values = {
                    "effect_digest": digest,
                    "operations_json": _json_dump(validated),
                    "updated_at": mutation_timestamp,
                }
                moved_claim = shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "state",
                    values=mutation_values,
                    where_sql="action_identity=? and effect_digest=?",
                    parameters=(action_identity, stale_digest),
                ).rowcount
                mutation_values = {"effect_digest": digest}
                moved_receipt = shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "amend_outcome",
                    values=mutation_values,
                    where_sql="action_identity=? and effect_digest=?",
                    parameters=(action_identity, stale_digest),
                ).rowcount
                still_referenced = db.execute(
                    f"""
                    select
                      (select count(*) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                        where action_identity=? and effect_digest=?) as steps,
                      (select count(*) from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL})
                        where action_identity=? and effect_digest=?) as claims,
                      (select count(*) from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                        where action_identity=? and effect_digest=?) as receipts,
                      (select count(*) from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL})
                        where action_identity=? and effect_digest=?) as continuations,
                      (select count(*) from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL})
                        where action_identity=? and previous_effect_digest=?) as children
                    """,
                    (action_identity, stale_digest) * 5,
                ).fetchone()
                if not any(
                    int(still_referenced[key]) for key in still_referenced.keys()
                ):
                    mutation_rows = db.execute(
                        f"""select action_identity,effect_digest from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=? and effect_digest=?""",
                        (action_identity, stale_digest),
                    ).fetchall()
                    for source_row in mutation_rows:
                        delete_action(
                            db,
                            "email_unsubscribe_effects",
                            (
                                source_row["action_identity"],
                                source_row["effect_digest"],
                            ),
                        )
                repaired.append(
                    {
                        "stale_effect_digest": stale_digest,
                        "effect_digest": digest,
                        "steps": moved_steps,
                        "claims": moved_claim,
                        "receipts": moved_receipt,
                    }
                )
            return {
                "repaired": bool(repaired),
                "operations": [item["kind"] for item in validated],
                "effects": repaired,
            }

    def retire_email_unsubscribe_claim_for_direct_run(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        operations: Sequence[Mapping[str, object]],
        previous_effect_digest: str = "",
    ) -> bool:
        """Point a claim left by the audited lifecycle at the direct run.

        The direct unsubscribe path holds no lease: it opens the authorized
        entry, operates the page, and persists one receipt. A claim written
        by the old Consumer-proposes / Audit-accepts lifecycle still carries
        that lifecycle's effect digest, and persist_email_unsubscribe_terminal
        refuses a receipt whose digest does not match the claim -- which is
        how five live tasks ended as EmailUnsubscribeClaimConflict.

        Nothing is deleted. The claim is moved onto this run's digest and
        marked terminal, and the effect row is added beside the one already
        there, so every operation the old lifecycle recorded stays readable.
        Returns False when there is no claim to retire; the receipt write
        then creates its own.
        """

        binding = _validate_unsubscribe_binding(
            action_identity=action_identity,
            effect_digest=effect_digest,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            stable_message_identity=stable_message_identity,
            thread_identity=thread_identity,
            entry_reference=entry_reference,
        )
        validated_operations = _validate_unsubscribe_operations(operations)
        expected_digest = email_unsubscribe_effect_digest(
            **{key: binding[key] for key in binding if key != "effect_digest"},
            operations=validated_operations,
            previous_effect_digest=previous_effect_digest,
        )
        if effect_digest != expected_digest:
            raise EmailUnsubscribeClaimConflict(
                "unsubscribe claim digest does not match its operations"
            )
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if claim is None:
                return False
            mutation_values = {
                "effect_digest": effect_digest,
                "operations_json": _json_dump(validated_operations),
                "status": "done",
                "phase": "terminal",
                "owner_id": _DIRECT_UNSUBSCRIBE_OWNER_ID,
                "owner_generation": 1,
                "lease_token": f"direct-unsubscribe:{effect_digest[:24]}",
                "updated_at": now,
            }
            shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "state",
                values=mutation_values,
                where_sql="action_identity=?",
                parameters=(action_identity,),
            )
            insert_action(
                db,
                "email_unsubscribe_effects",
                {
                    "action_identity": action_identity,
                    "effect_digest": effect_digest,
                    "previous_effect_digest": previous_effect_digest,
                    "operations_json": _json_dump(validated_operations),
                    "created_at": now,
                },
                ignore=True,
            )
            return True

    def recover_terminated_email_unsubscribe_claims(
        self,
        *,
        owner: Mapping[str, object],
        termination_verifier: Callable[[Mapping[str, object]], bool],
        recovered_at: str,
    ) -> int:
        owner = _validate_email_unsubscribe_owner(owner)
        if not termination_verifier(owner):
            raise EmailUnsubscribeClaimConflict("owner_termination_not_proven")
        recovered_at = _required_utc_timestamp(recovered_at, field="recovered_at")
        with self._connect() as db:
            db.execute("begin immediate")
            mutation_values = {"status": "uncertain", "updated_at": recovered_at}
            return shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "state",
                values=mutation_values,
                where_sql="status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(owner["owner_id"], owner["generation"], owner["lease_token"]),
            ).rowcount

    def mark_email_unsubscribe_uncertain(
        self,
        action_identity: str,
        *,
        owner: Mapping[str, object],
    ) -> None:
        owner = _validate_email_unsubscribe_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            mutation_values = {
                "status": "uncertain",
                "phase": "effect_uncertain",
                "updated_at": self._now(),
            }
            updated = shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "state",
                values=mutation_values,
                where_sql="action_identity=? and status='dispatching' and owner_id=? and owner_generation=? and lease_token=?",
                parameters=(
                    action_identity,
                    owner["owner_id"],
                    owner["generation"],
                    owner["lease_token"],
                ),
            ).rowcount
            if updated != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe claim owner fence changed"
                )

    def release_email_unsubscribe_failed_operation(
        self,
        action_identity: str,
        *,
        effect_digest: str,
        owner: Mapping[str, object],
    ) -> None:
        """Release a failed claim when no browser result was persisted."""

        owner = _validate_email_unsubscribe_owner(owner)
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if claim is None or any(
                claim[column] != expected
                for column, expected in (
                    ("effect_digest", effect_digest),
                    ("owner_id", owner["owner_id"]),
                    ("owner_generation", owner["generation"]),
                    ("lease_token", owner["lease_token"]),
                    ("status", "dispatching"),
                    ("phase", "navigating"),
                )
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe failed claim owner fence changed"
                )
            related = db.execute(
                f"""
                select
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                     where action_identity=?) as steps,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                     where action_identity=?) as receipts,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL})
                     where action_identity=?) as continuations
                """,
                (action_identity, action_identity, action_identity),
            ).fetchone()
            if related is None or any(int(related[name]) for name in related.keys()):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe failed operation has durable browser state"
                )
            mutation_rows = db.execute(
                f"""select action_identity,effect_digest from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=? and effect_digest=?""",
                (action_identity, effect_digest),
            ).fetchall()
            deleted_effect = sum(
                delete_action(
                    db,
                    "email_unsubscribe_effects",
                    (source_row["action_identity"], source_row["effect_digest"]),
                ).rowcount
                for source_row in mutation_rows
            )
            deleted_claim = shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "retire",
                where_sql="action_identity=?",
                parameters=(action_identity,),
            ).rowcount
            if deleted_effect != 1 or deleted_claim != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe failed operation changed concurrently"
                )

    def release_failed_email_unsubscribe_for_explicit_retry(
        self,
        action_identity: str,
        *,
        audit_agent_run_id: int,
    ) -> None:
        """Release a legacy uncertain claim after an explicit reviewed retry."""

        if audit_agent_run_id <= 0:
            raise ValueError("audit_agent_run_id must be positive")
        with self._connect() as db:
            db.execute("begin immediate")
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            audit = db.execute(
                f"""
                select runs.role, runs.status, runs.structured_error_json,
                       tasks.channel, tasks.trigger_message_id
                from ({AGENT_RUN_SQL}) as runs
                join ({REPLY_TASK_SQL}) as tasks on tasks.id=runs.reply_task_id
                where runs.id=?
                """,
                (audit_agent_run_id,),
            ).fetchone()
            try:
                structured_error = (
                    None
                    if audit is None
                    else json.loads(str(audit["structured_error_json"]))
                )
            except (KeyError, TypeError, ValueError, RecursionError) as exc:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe retry Audit evidence is invalid"
                ) from exc
            if (
                claim is None
                or claim["status"] != "uncertain"
                or claim["phase"] != "effect_uncertain"
                or claim["audit_agent_run_id"] != audit_agent_run_id
                or audit is None
                or audit["role"] != "audit"
                or audit["status"] != "failed"
                or audit["channel"] != "email"
                or audit["trigger_message_id"] != action_identity
                or not isinstance(structured_error, dict)
                or structured_error.get("code") != "email_unsubscribe_browser_failed"
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe retry is not bound to the failed Audit claim"
                )
            related = db.execute(
                f"""
                select
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                     where action_identity=?) as steps,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                     where action_identity=?) as receipts,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_CONTINUATION_SQL})
                     where action_identity=?) as continuations,
                    (select count(*) from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL})
                     where action_identity=?) as effects
                """,
                (action_identity,) * 4,
            ).fetchone()
            if (
                related is None
                or int(related["steps"]) != 0
                or int(related["receipts"]) != 0
                or int(related["continuations"]) != 0
                or int(related["effects"]) != 1
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe explicit retry has durable browser state"
                )
            mutation_rows = db.execute(
                f"""select action_identity,effect_digest from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=?""",
                (action_identity,),
            ).fetchall()
            deleted_effect = sum(
                delete_action(
                    db,
                    "email_unsubscribe_effects",
                    (source_row["action_identity"], source_row["effect_digest"]),
                ).rowcount
                for source_row in mutation_rows
            )
            deleted_claim = shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "retire",
                where_sql="action_identity=?",
                parameters=(action_identity,),
            ).rowcount
            if deleted_effect != 1 or deleted_claim != 1:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe explicit retry changed concurrently"
                )

    def advance_email_unsubscribe_phase(
        self,
        action_identity: str,
        phase: str,
        *,
        owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Advance the durable unsubscribe phase without permitting rollback."""

        phases = {
            "prepared": 0,
            "navigating": 1,
            "effect_uncertain": 2,
            "terminal": 3,
        }
        if phase not in phases:
            raise ValueError("unsubscribe phase is invalid")
        validated_owner = (
            None if owner is None else _validate_email_unsubscribe_owner(owner)
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            if row is None:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase claim is missing"
                )
            current = row["phase"]
            if phases[current] > phases[phase]:
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase cannot move backward"
                )
            if validated_owner is not None and any(
                row[key] != validated_owner[owner_key]
                for key, owner_key in (
                    ("owner_id", "owner_id"),
                    ("owner_generation", "generation"),
                    ("lease_token", "lease_token"),
                )
            ):
                raise EmailUnsubscribeClaimConflict(
                    "unsubscribe phase owner fence changed"
                )
            if current != phase:
                mutation_values = {"phase": phase, "updated_at": self._now()}
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "state",
                    values=mutation_values,
                    where_sql="action_identity=?",
                    parameters=(action_identity,),
                )
            updated = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert updated is not None
            return self._email_unsubscribe_claim_row(updated)

    def persist_email_unsubscribe_terminal(
        self,
        *,
        action_identity: str,
        effect_digest: str,
        action_plan_id: str,
        action_plan_version: int,
        classification_id: int,
        account_id: str,
        stable_message_identity: str,
        thread_identity: str,
        entry_reference: str,
        entry_url: str = "",
        operations: Sequence[Mapping[str, object]],
        previous_effect_digest: str = "",
        outcome: str,
        receipt_id: str,
        evidence: str,
        result_text: str = "",
        observation_digest: str = "",
        result_text_digest: str | None = None,
        result_text_truncated: bool | None = None,
        started_at: str = "",
        completed_at: str = "",
        final_step: Mapping[str, object] | None = None,
        journal_steps: Sequence[Mapping[str, object]] = (),
        claim_owner: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        binding = _validate_unsubscribe_binding(
            action_identity=action_identity,
            effect_digest=effect_digest,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
            classification_id=classification_id,
            account_id=account_id,
            stable_message_identity=stable_message_identity,
            thread_identity=thread_identity,
            entry_reference=entry_reference,
        )
        validated_operations = _validate_unsubscribe_operations(operations)
        expected_digest = email_unsubscribe_effect_digest(
            **{key: binding[key] for key in binding if key != "effect_digest"},
            operations=validated_operations,
            previous_effect_digest=previous_effect_digest,
        )
        if effect_digest != expected_digest:
            raise EmailUnsubscribeReceiptConflict(
                "unsubscribe receipt digest does not match accepted operations"
            )
        if outcome not in _EMAIL_UNSUBSCRIBE_OUTCOMES:
            raise ValueError("unsubscribe terminal outcome is invalid")
        receipt_id = _validate_unsubscribe_opaque(receipt_id, field="receipt_id")
        evidence = _validate_unsubscribe_opaque(evidence, field="evidence")
        entry_url = _validate_unsubscribe_entry_url(
            entry_url, entry_reference=binding["entry_reference"]
        )
        if not isinstance(result_text, str):
            raise ValueError("unsubscribe result_text must be text")
        from app.email_unsubscribe import normalize_unsubscribe_result_text

        explicit_integrity_metadata = (
            result_text_digest is not None or result_text_truncated is not None
        )
        normalized_result_text, expected_observation_digest = (
            normalize_unsubscribe_result_text(result_text)
        )
        if explicit_integrity_metadata:
            if (
                result_text_digest is None
                or result_text_truncated is None
                or type(result_text_truncated) is not bool
                or normalized_result_text != result_text
            ):
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe result integrity metadata is inconsistent"
                )
            try:
                _validate_durable_unsubscribe_result_text(
                    normalized_result_text,
                    observation_digest,
                    int(result_text_truncated),
                    result_text_digest,
                )
            except EmailPersistenceCorruption as exc:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe result integrity metadata is inconsistent"
                ) from exc
        else:
            if observation_digest and observation_digest != expected_observation_digest:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe observation digest does not match result text"
                )
            if normalized_result_text:
                observation_digest = expected_observation_digest
                result_text_digest = sha256(
                    normalized_result_text.encode("utf-8")
                ).hexdigest()
                result_text_truncated = observation_digest != result_text_digest
            else:
                observation_digest = ""
                result_text_digest = ""
                result_text_truncated = False
        created_at = self._now()
        started_at = started_at or created_at
        completed_at = completed_at or created_at
        owner = (
            None
            if claim_owner is None
            else _validate_email_unsubscribe_owner(claim_owner)
        )
        # A one-call unsubscribe reaches the terminal page through every step
        # it took, and none of those steps had a lease to write itself under.
        # They are written here, with the terminal one last.
        validated_steps: list[dict[str, object]] = []
        for step in (
            *journal_steps,
            *((final_step,) if final_step is not None else ()),
        ):
            if set(step) != {"sequence", "operation", "state", "reference"}:
                raise ValueError("unsubscribe terminal step fields are invalid")
            _require_positive_int(step["sequence"], field="sequence")
            validated_steps.append(
                {
                    "sequence": step["sequence"],
                    "operation": _validate_unsubscribe_opaque(
                        step["operation"], field="operation"
                    ),
                    "state": _validate_unsubscribe_opaque(step["state"], field="state"),
                    "reference": _validate_unsubscribe_opaque(
                        step["reference"], field="reference"
                    ),
                }
            )
        with self._connect() as db:
            db.execute("begin immediate")
            plan = db.execute(
                f"""
                select plans.*, classifications.stable_message_identity
                  as classification_message_identity,
                       messages.account_id as message_account_id,
                       messages.thread_identity as message_thread_identity
                from ({EMAIL_PLAN_SQL}) as plans
                join ({EMAIL_CLASSIFICATION_SQL}) as classifications
                  on classifications.id=plans.classification_id
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where plans.action_plan_id=? and plans.classification_id=?
                """,
                (action_plan_id, classification_id),
            ).fetchone()
            expected_action_identity = email_action_identity(
                account_id=account_id,
                stable_message_identity=stable_message_identity,
                action_type=EmailAction.UNSUBSCRIBE,
                action_plan_version=action_plan_version,
            )
            if action_identity != expected_action_identity:
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe terminal action identity is invalid"
                )
            if (
                plan is None
                or plan["action_plan_version"] != action_plan_version
                or plan["account_id"] != account_id
                or plan["classification_message_identity"] != stable_message_identity
                or plan["message_account_id"] != account_id
                or plan["message_thread_identity"] != thread_identity
                or EmailAction.UNSUBSCRIBE.value
                not in _json_load(
                    plan["actions_json"],
                    field="actions_json",
                    expected_type=list,
                )
            ):
                raise EmailUnsubscribeReceiptConflict(
                    "unsubscribe terminal evidence does not match immutable plan"
                )
            claim = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            expected_claim = {**binding, "operations": validated_operations}
            if claim is None:
                if previous_effect_digest:
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe continuation terminal is missing its prefix claim"
                    )
                account = db.execute(
                    f"select updated_at from ({config_projection('email_accounts')}) where account_id=?",
                    (account_id,),
                ).fetchone()
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "claim",
                    values={
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "action_plan_id": action_plan_id,
                        "action_plan_version": action_plan_version,
                        "classification_id": classification_id,
                        "account_id": account_id,
                        "stable_message_identity": stable_message_identity,
                        "thread_identity": thread_identity,
                        "entry_reference": entry_reference,
                        "operations_json": _json_dump(validated_operations),
                        "owner_id": "reconciliation",
                        "owner_generation": 1,
                        "lease_token": f"receipt-{effect_digest[:24]}",
                        "account_updated_at": account["updated_at"]
                        if account is not None
                        else "historical",
                        "status": "done",
                        "phase": "terminal",
                        "claimed_at": created_at,
                        "updated_at": created_at,
                    },
                )
                insert_action(
                    db,
                    "email_unsubscribe_effects",
                    {
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "previous_effect_digest": previous_effect_digest,
                        "operations_json": _json_dump(validated_operations),
                        "created_at": created_at,
                    },
                )
            else:
                persisted_claim = self._email_unsubscribe_claim_row(claim)
                if any(
                    persisted_claim[key] != value
                    for key, value in expected_claim.items()
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe receipt does not match its claim"
                    )
                if persisted_claim["status"] == "dispatching":
                    if owner is None or any(
                        persisted_claim[key] != owner[owner_key]
                        for key, owner_key in (
                            ("owner_id", "owner_id"),
                            ("owner_generation", "generation"),
                            ("lease_token", "lease_token"),
                        )
                    ):
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe claim completion requires its owner fence"
                        )
            existing_receipt = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            # entry_url is deliberately absent from this comparison: it is the
            # pre-image of entry_reference, which is compared, so it adds no
            # independent evidence and a receipt written before the column
            # existed must not read as a conflict.
            requested_receipt = {
                **binding,
                "outcome": outcome,
                "receipt_id": receipt_id,
                "evidence": evidence,
                "result_text": normalized_result_text,
                "observation_digest": observation_digest,
                "result_text_truncated": result_text_truncated,
                "result_text_digest": result_text_digest,
                "started_at": started_at,
                "completed_at": completed_at,
            }
            if existing_receipt is None:
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "outcome",
                    values={
                        "action_identity": action_identity,
                        "effect_digest": effect_digest,
                        "action_plan_id": action_plan_id,
                        "action_plan_version": action_plan_version,
                        "classification_id": classification_id,
                        "account_id": account_id,
                        "stable_message_identity": stable_message_identity,
                        "thread_identity": thread_identity,
                        "entry_reference": entry_reference,
                        "entry_url": entry_url,
                        "outcome": outcome,
                        "receipt_id": receipt_id,
                        "evidence": evidence,
                        "result_text": normalized_result_text,
                        "observation_digest": observation_digest,
                        "result_text_truncated": int(result_text_truncated),
                        "result_text_digest": result_text_digest,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "created_at": created_at,
                    },
                )
            else:
                persisted_receipt = self._email_unsubscribe_receipt_row(
                    existing_receipt
                )
                retrying_unreliable_entry = (
                    persisted_receipt["outcome"]
                    in {"skipped_no_reliable_entry", "skipped_login_required"}
                    and outcome != persisted_receipt["outcome"]
                )
                if retrying_unreliable_entry:
                    # An explicit retry may promote a page that the old
                    # detector misclassified. Attempt rows remain immutable
                    # history; this row is the current projection.
                    mutation_values = {
                        "effect_digest": effect_digest,
                        "action_plan_id": action_plan_id,
                        "action_plan_version": action_plan_version,
                        "classification_id": classification_id,
                        "account_id": account_id,
                        "stable_message_identity": stable_message_identity,
                        "thread_identity": thread_identity,
                        "entry_reference": entry_reference,
                        "entry_url": entry_url,
                        "outcome": outcome,
                        "receipt_id": receipt_id,
                        "evidence": evidence,
                        "result_text": normalized_result_text,
                        "observation_digest": observation_digest,
                        "result_text_truncated": int(result_text_truncated),
                        "result_text_digest": result_text_digest,
                        "started_at": started_at,
                        "completed_at": completed_at,
                    }
                    shared_action_lifecycle.transition(
                        db,
                        "email_unsubscribe",
                        "amend_outcome",
                        values=mutation_values,
                        where_sql="action_identity=?",
                        parameters=(action_identity,),
                    )
                elif any(
                    persisted_receipt[key] != value
                    for key, value in requested_receipt.items()
                ):
                    raise EmailUnsubscribeReceiptConflict(
                        "unsubscribe identity is bound to different terminal evidence"
                    )
            for validated_step in validated_steps:
                previous = db.execute(
                    f"""
                    select coalesce(max(sequence), 0) from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                    where action_identity=?
                    """,
                    (action_identity,),
                ).fetchone()[0]
                existing_step = db.execute(
                    f"""
                    select * from ({EMAIL_UNSUBSCRIBE_STEP_SQL})
                    where action_identity=? and sequence=?
                    """,
                    (action_identity, validated_step["sequence"]),
                ).fetchone()
                if existing_step is None:
                    if validated_step["sequence"] != previous + 1:
                        raise EmailUnsubscribeClaimConflict(
                            "unsubscribe terminal journal is not contiguous"
                        )
                    insert_action(
                        db,
                        "email_unsubscribe_steps",
                        {
                            "action_identity": action_identity,
                            "effect_digest": effect_digest,
                            "sequence": validated_step["sequence"],
                            "operation": validated_step["operation"],
                            "state": validated_step["state"],
                            "reference": validated_step["reference"],
                            "created_at": created_at,
                        },
                    )
                elif existing_step["effect_digest"] != effect_digest or any(
                    existing_step[key] != validated_step[key]
                    for key in ("operation", "state", "reference")
                ):
                    raise EmailUnsubscribeClaimConflict(
                        "unsubscribe terminal journal is bound differently"
                    )
            mutation_values = {
                "status": "done",
                "phase": "terminal",
                "updated_at": created_at,
            }
            shared_action_lifecycle.transition(
                db,
                "email_unsubscribe",
                "state",
                values=mutation_values,
                where_sql="action_identity=? and status in ('dispatching', 'awaiting_audit', 'uncertain')",
                parameters=(action_identity,),
            )
            mutation_rows = db.execute(
                f"""select rowid from ({EMAIL_UNSUBSCRIBE_CONTINUATION_LOCATOR_SQL}) where action_identity=?""",
                (action_identity,),
            ).fetchall()
            for source_row in mutation_rows:
                delete_email_action(
                    db, "email_unsubscribe_continuations", source_row["rowid"]
                )
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
            assert row is not None
            return self._email_unsubscribe_receipt_row(row)

    def get_email_unsubscribe_receipt(
        self,
        action_identity: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select * from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else self._email_unsubscribe_receipt_row(row)

    @staticmethod
    def _email_unsubscribe_receipt_row(row: sqlite3.Row) -> dict[str, Any]:
        _validate_durable_unsubscribe_result_text(
            row["result_text"],
            row["observation_digest"],
            row["result_text_truncated"],
            row["result_text_digest"],
        )
        return {
            "action_identity": row["action_identity"],
            "effect_digest": row["effect_digest"],
            "action_plan_id": row["action_plan_id"],
            "action_plan_version": row["action_plan_version"],
            "classification_id": row["classification_id"],
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "thread_identity": row["thread_identity"],
            "entry_reference": row["entry_reference"],
            "entry_url": row["entry_url"],
            "outcome": row["outcome"],
            "receipt_id": row["receipt_id"],
            "evidence": row["evidence"],
            "result_text": row["result_text"],
            "observation_digest": row["observation_digest"],
            "result_text_truncated": bool(row["result_text_truncated"]),
            "result_text_digest": row["result_text_digest"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
        }

    def create_account(
        self,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> dict[str, Any]:
        row, _snapshot = self.create_account_with_category_enablement_snapshot(
            values,
            allow_shared_email=allow_shared_email,
        )
        return row

    def create_account_with_category_enablement_snapshot(
        self,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> tuple[dict[str, Any], EmailCategoryEnablementSnapshot]:
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            if (
                db.execute(
                    f"select 1 from ({config_projection('email_accounts')}) where account_id=?",
                    (values["account_id"],),
                ).fetchone()
                is not None
            ):
                raise EmailAccountConflict("account_id_conflict")
            self._assert_email_address_available(
                db,
                str(values["email_address"]),
                allow_shared_email=allow_shared_email,
            )
            now = self._next_account_timestamp()
            self._insert_account(db, values, created_at=now, updated_at=now)
            category_snapshot = self._disable_categories_missing_active_bindings(
                db,
                updated_at=now,
            )
            self._validate_category_binding_completeness(db)
            row = db.execute(
                f"select * from ({config_projection('email_accounts')}) where account_id=?",
                (values["account_id"],),
            ).fetchone()
        assert row is not None
        return self._account_row(row), category_snapshot

    def update_account(
        self,
        account_id: str,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        result = self.update_account_with_category_enablement_snapshot(
            account_id,
            values,
            allow_shared_email=allow_shared_email,
        )
        if result is None:
            return None
        row, previous, _snapshot = result
        return row, previous

    def update_account_with_category_enablement_snapshot(
        self,
        account_id: str,
        values: Mapping[str, object],
        *,
        allow_shared_email: bool = False,
    ) -> (
        tuple[
            dict[str, Any],
            dict[str, Any],
            EmailCategoryEnablementSnapshot,
        ]
        | None
    ):
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                f"select * from ({config_projection('email_accounts')}) where account_id=?",
                (account_id,),
            ).fetchone()
            if existing is None:
                return None
            previous = self._account_row(existing)
            updated_at = self._next_account_timestamp(existing["updated_at"])
            self._assert_email_address_available(
                db,
                str(values["email_address"]),
                allow_shared_email=allow_shared_email,
                excluding_account_id=account_id,
            )
            update_config(db, "email_accounts", _config_source_rowid(db, "email_accounts", "account_id", account_id), {
                "display_name": values["display_name"],
                "email_address": values["email_address"],
                "imap_host": values["imap_host"],
                "imap_port": values["imap_port"],
                "imap_tls": int(bool(values["imap_tls"])),
                "imap_username": values["imap_username"],
                "imap_secret_reference": values["imap_secret_reference"],
                # A save that does not mention the move mode keeps it.
                "imap_move_mode": values.get("imap_move_mode", existing["imap_move_mode"]),
                "smtp_host": values["smtp_host"],
                "smtp_port": values["smtp_port"],
                "smtp_tls": int(bool(values["smtp_tls"])),
                "smtp_username": values["smtp_username"],
                "smtp_secret_reference": values["smtp_secret_reference"],
                "enabled": int(bool(values["enabled"])),
                "scan_folders_json": _json_dump(list(values["scan_folders"])),
                "scan_interval_seconds": values["scan_interval_seconds"],
                "agent_lookback_days": int(values.get("agent_lookback_days", existing["agent_lookback_days"])),
                "model_lookback_days": int(values.get("model_lookback_days", existing["model_lookback_days"])),
                "scan_read_state": str(values.get("scan_read_state", existing["scan_read_state"])),
                "updated_at": updated_at,
            })
            category_snapshot = self._disable_categories_missing_active_bindings(
                db,
                updated_at=updated_at,
            )
            self._validate_category_binding_completeness(db)
            row = db.execute(
                f"select * from ({config_projection('email_accounts')}) where account_id=?",
                (account_id,),
            ).fetchone()
        assert row is not None
        return self._account_row(row), previous, category_snapshot

    def delete_account_if_unchanged(
        self,
        account_id: str,
        *,
        expected_updated_at: str,
        category_enablement_snapshot: EmailCategoryEnablementSnapshot | None = None,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
            if category_enablement_snapshot is not None and not (
                self._category_enablement_snapshot_is_current(
                    db,
                    category_enablement_snapshot,
                )
            ):
                return False
            current = db.execute(
                f"select updated_at from ({config_projection('email_accounts')}) where account_id=?",
                (account_id,),
            ).fetchone()
            deleted = (
                delete_config(db, "email_accounts", _config_source_rowid(db, "email_accounts", "account_id", account_id))
                if current is not None and current["updated_at"] == expected_updated_at
                else 0
            )
            if deleted == 1 and category_enablement_snapshot is not None:
                self._restore_category_enablement_snapshot(
                    db,
                    category_enablement_snapshot,
                )
                self._validate_category_binding_completeness(db)
        return deleted == 1

    def restore_account_if_unchanged(
        self,
        snapshot: Mapping[str, object],
        *,
        expected_updated_at: str,
        category_enablement_snapshot: EmailCategoryEnablementSnapshot | None = None,
    ) -> bool:
        with self._connect() as db:
            db.execute("begin immediate")
            if category_enablement_snapshot is not None and not (
                self._category_enablement_snapshot_is_current(
                    db,
                    category_enablement_snapshot,
                )
            ):
                return False
            current = db.execute(
                f"select updated_at from ({config_projection('email_accounts')}) where account_id=?",
                (snapshot["account_id"],),
            ).fetchone()
            restored = 0
            if current is not None and current["updated_at"] == expected_updated_at:
                restored = update_config(db, "email_accounts", _config_source_rowid(db, "email_accounts", "account_id", snapshot["account_id"]), {
                    "display_name": snapshot["display_name"],
                    "email_address": snapshot["email_address"],
                    "imap_host": snapshot["imap_host"],
                    "imap_port": snapshot["imap_port"],
                    "imap_tls": int(bool(snapshot["imap_tls"])),
                    "imap_username": snapshot["imap_username"],
                    "imap_secret_reference": snapshot["imap_secret_reference"],
                    "imap_move_mode": snapshot.get("imap_move_mode", "move"),
                    "smtp_host": snapshot["smtp_host"],
                    "smtp_port": snapshot["smtp_port"],
                    "smtp_tls": int(bool(snapshot["smtp_tls"])),
                    "smtp_username": snapshot["smtp_username"],
                    "smtp_secret_reference": snapshot["smtp_secret_reference"],
                    "enabled": int(bool(snapshot["enabled"])),
                    "scan_folders_json": _json_dump(list(snapshot["scan_folders"])),
                    "scan_interval_seconds": snapshot["scan_interval_seconds"],
                    "agent_lookback_days": int(snapshot.get("agent_lookback_days", 30)),
                    "model_lookback_days": int(snapshot.get("model_lookback_days", 365)),
                    "scan_read_state": str(snapshot.get("scan_read_state", "unread")),
                    "created_at": snapshot["created_at"],
                    "updated_at": snapshot["updated_at"],
                })
            if restored == 1:
                if category_enablement_snapshot is None:
                    self._disable_categories_missing_active_bindings(
                        db,
                        updated_at=self._now(),
                    )
                else:
                    self._restore_category_enablement_snapshot(
                        db,
                        category_enablement_snapshot,
                    )
                self._validate_category_binding_completeness(db)
        return restored == 1

    @staticmethod
    def _categories_missing_active_bindings(
        db: sqlite3.Connection,
        account_id: str,
    ) -> tuple[str, ...]:
        """Return the categories this mailbox has no usable folder for.

        A category the owner switched off on purpose, and never bound here, is
        not a problem to report; one that is enabled, or whose readback came
        back unusable, is exactly what the console has to explain.
        """

        rows = db.execute(
            f"""
            select configs.category_key
            from ({config_projection('email_category_configs')}) as configs
            left join ({config_projection('email_category_folder_bindings')}) as bindings
              on bindings.category_key=configs.category_key
             and bindings.account_id=?
            where coalesce(bindings.binding_status, '') != 'active'
              and (
                configs.enabled=1
                or bindings.account_id is not null
                or exists (
                    select 1 from ({config_projection('email_category_folder_bindings')}) as elsewhere
                    where elsewhere.category_key=configs.category_key
                      and elsewhere.binding_status='active'
                )
              )
            order by configs.category_key
            """,
            (account_id,),
        ).fetchall()
        return tuple(str(row["category_key"]) for row in rows)

    def categories_missing_active_bindings(self, account_id: str) -> tuple[str, ...]:
        """Return why an account cannot be enabled yet, for the console to show."""

        with self._connect() as db:
            return self._categories_missing_active_bindings(db, account_id)

    def restore_category_enablement_with_complete_bindings(
        self,
        snapshot: EmailCategoryEnablementSnapshot,
    ) -> tuple[str, ...]:
        """Switch back on the categories one account write switched off.

        Adding a mailbox disables every category it has no verified folder for,
        which is what keeps mail from being filed or trashed into a folder
        nobody proved exists. Once that mailbox's folders are verified the
        reason is gone, so each category whose bindings now cover every enabled
        account is restored. Returns the ones still left off.
        """

        if type(snapshot) is not EmailCategoryEnablementSnapshot:
            raise TypeError("snapshot must be an EmailCategoryEnablementSnapshot")
        now = self._now()
        still_disabled: list[str] = []
        with self._connect() as db:
            db.execute("begin immediate")
            for state in snapshot.states:
                if not self._bindings_cover_enabled_accounts(db, state.category_key):
                    still_disabled.append(state.category_key)
                    continue
                row = db.execute(
                    f"select enabled, updated_at from ({config_projection('email_category_configs')}) "
                    "where category_key=?", (state.category_key,),
                ).fetchone()
                if row is not None and row["enabled"] == 0 and row["updated_at"] == state.mutation_updated_at:
                    update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", state.category_key), {
                        "enabled": int(state.enabled), "updated_at": now,
                    })
            self._validate_category_binding_completeness(db)
        return tuple(sorted(still_disabled))

    @staticmethod
    def _disable_categories_missing_active_bindings(
        db: sqlite3.Connection,
        *,
        updated_at: str,
    ) -> EmailCategoryEnablementSnapshot:
        rows = db.execute(
            f"""
            select configs.category_key, configs.enabled, configs.updated_at
            from ({config_projection('email_category_configs')}) as configs
            where enabled=1 and exists (
                select 1 from ({config_projection('email_accounts')}) as accounts
                where accounts.enabled=1
                  and not exists (
                      select 1 from ({config_projection('email_category_folder_bindings')}) as bindings
                      where bindings.account_id=accounts.account_id
                        and bindings.category_key=
                            configs.category_key
                        and bindings.binding_status='active'
                  )
            )
            order by category_key
            """
        ).fetchall()
        for row in rows:
            update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", row["category_key"]), {
                "enabled": 0, "updated_at": updated_at,
            })
        return EmailCategoryEnablementSnapshot(
            states=tuple(
                EmailCategoryEnablementState(
                    category_key=row["category_key"],
                    enabled=bool(row["enabled"]),
                    updated_at=row["updated_at"],
                    mutation_updated_at=updated_at,
                )
                for row in rows
            )
        )

    @staticmethod
    def _category_enablement_snapshot_is_current(
        db: sqlite3.Connection,
        snapshot: EmailCategoryEnablementSnapshot,
    ) -> bool:
        if type(snapshot) is not EmailCategoryEnablementSnapshot:
            return False
        for state in snapshot.states:
            if type(state) is not EmailCategoryEnablementState:
                return False
            row = db.execute(
                f"select enabled, updated_at from ({config_projection('email_category_configs')}) "
                "where category_key=?",
                (state.category_key,),
            ).fetchone()
            if (
                row is None
                or bool(row["enabled"]) is not False
                or row["updated_at"] != state.mutation_updated_at
            ):
                return False
        return True

    @staticmethod
    def _restore_category_enablement_snapshot(
        db: sqlite3.Connection,
        snapshot: EmailCategoryEnablementSnapshot,
    ) -> None:
        for state in snapshot.states:
            row = db.execute(
                f"select enabled, updated_at from ({config_projection('email_category_configs')}) "
                "where category_key=?", (state.category_key,),
            ).fetchone()
            updated = (
                update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", state.category_key), {
                    "enabled": int(state.enabled), "updated_at": state.updated_at,
                })
                if row is not None and row["enabled"] == 0 and row["updated_at"] == state.mutation_updated_at
                else 0
            )
            if updated != 1:
                raise EmailPersistenceCorruption(
                    "category enablement changed during account compensation"
                )

    @staticmethod
    def _validate_category_binding_completeness(db: sqlite3.Connection) -> None:
        incomplete = db.execute(
            f"""
            select configs.category_key, accounts.account_id
            from ({config_projection('email_category_configs')}) as configs
            cross join ({config_projection('email_accounts')}) as accounts
            left join ({config_projection('email_category_folder_bindings')}) as bindings
              on bindings.category_key=configs.category_key
             and bindings.account_id=accounts.account_id
             and bindings.binding_status='active'
            where configs.enabled=1 and accounts.enabled=1
              and bindings.account_id is null
            limit 1
            """
        ).fetchone()
        if incomplete is not None:
            raise EmailPersistenceCorruption(
                "enabled email category is missing an active account folder binding"
            )

    @staticmethod
    def _assert_email_address_available(
        db: sqlite3.Connection,
        email_address: str,
        *,
        allow_shared_email: bool,
        excluding_account_id: str = "",
    ) -> None:
        if allow_shared_email:
            return
        row = db.execute(
            f"""
            select 1 from ({config_projection('email_accounts')})
            where lower(email_address)=lower(?) and account_id != ?
            """,
            (email_address, excluding_account_id),
        ).fetchone()
        if row is not None:
            raise EmailAccountConflict("email_address_conflict")

    @staticmethod
    def _insert_account(
        db: sqlite3.Connection,
        values: Mapping[str, object],
        *,
        created_at: str,
        updated_at: str,
    ) -> None:
        insert_config(db, "email_accounts", {
            "account_id": values["account_id"],
            "display_name": values["display_name"],
            "email_address": values["email_address"],
            "imap_host": values["imap_host"],
            "imap_port": values["imap_port"],
            "imap_tls": int(bool(values["imap_tls"])),
            "imap_username": values["imap_username"],
            "imap_secret_reference": values["imap_secret_reference"],
            "imap_move_mode": values.get("imap_move_mode", "move"),
            "smtp_host": values["smtp_host"],
            "smtp_port": values["smtp_port"],
            "smtp_tls": int(bool(values["smtp_tls"])),
            "smtp_username": values["smtp_username"],
            "smtp_secret_reference": values["smtp_secret_reference"],
            "enabled": int(bool(values["enabled"])),
            "scan_folders_json": _json_dump(list(values["scan_folders"])),
            "scan_interval_seconds": values["scan_interval_seconds"],
            "created_at": created_at,
            "updated_at": updated_at,
            "agent_lookback_days": int(values.get("agent_lookback_days", 30)),
            "model_lookback_days": int(values.get("model_lookback_days", 365)),
            "scan_read_state": str(values.get("scan_read_state", "unread")),
        })

    @staticmethod
    def _account_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "display_name": row["display_name"],
            "email_address": row["email_address"],
            "imap_host": row["imap_host"],
            "imap_port": row["imap_port"],
            "imap_tls": bool(row["imap_tls"]),
            "imap_username": row["imap_username"],
            "imap_secret_reference": row["imap_secret_reference"],
            "imap_move_mode": row["imap_move_mode"],
            "smtp_host": row["smtp_host"],
            "smtp_port": row["smtp_port"],
            "smtp_tls": bool(row["smtp_tls"]),
            "smtp_username": row["smtp_username"],
            "smtp_secret_reference": row["smtp_secret_reference"],
            "enabled": bool(row["enabled"]),
            "scan_folders": _json_load(
                row["scan_folders_json"],
                field="scan_folders_json",
                expected_type=list,
            ),
            "scan_interval_seconds": row["scan_interval_seconds"],
            "agent_lookback_days": row["agent_lookback_days"],
            "model_lookback_days": row["model_lookback_days"],
            "scan_read_state": row["scan_read_state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def classification_source_candidates(
        self, *, status: EmailClassificationStatus | None,
        limit: int | None = None, offset: int = 0, category: str = "",
        action_status: str = "", source: str = "", unsubscribe: bool = False,
        classification_id: int | None = None, preview_query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Existing business eligibility/order without reading either input body."""
        from app.current_email_definitions import CLASS_FIELDS

        if offset < 0 or (limit is not None and limit <= 0):
            raise ValueError("classification pagination is invalid")
        with self._connect() as db:
            if classification_id is not None:
                where, params = "classifications.id=?", (classification_id,)
            elif unsubscribe:
                where, params = self._unsubscribe_classification_membership(db), ()
            else:
                statuses = ((EmailClassificationStatus.PROCESSED, EmailClassificationStatus.PENDING_FEEDBACK)
                            if status is None else (status,))
                filters, filter_params = self._classification_filters(
                    category=category, action_status=action_status, source=source,
                )
                where = "classifications.status in (" + ",".join("?" for _ in statuses) + ")" + filters
                params = (*(item.value for item in statuses), *filter_params)
            if preview_query:
                db.create_function("email_casefold", 1, str.casefold, deterministic=True)
                where = "(" + where + ") and (" + " or ".join(
                    "instr(email_casefold(coalesce(classifications." + field + ",'')),?)>0"
                    for field in ("sender", "subject", "preview")
                ) + ")"
                params = (*params, preview_query, preview_query, preview_query)
            total = db.execute(
                f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) as classifications where " + where,
                params,
            ).fetchone()[0]
            fields = ",".join('classifications."' + name + '"' for name in CLASS_FIELDS if name != "model_text")
            pagination = " limit ? offset ?" if limit is not None else ""
            rows = db.execute(
                f"select {fields}, messages.attachment_metadata_json as message_attachment_metadata_json "
                f"from ({EMAIL_CLASSIFICATION_SQL}) as classifications "
                f"left join ({EMAIL_MESSAGE_SQL}) as messages "
                "on messages.account_id=classifications.account_id "
                "and messages.stable_message_identity=classifications.stable_message_identity "
                "where " + where + " order by classifications.updated_at desc,classifications.id desc" + pagination,
                (*params, limit, offset) if limit is not None else params,
            ).fetchall()
            plans = self._current_action_plan_rows(db, rows)
        return [self._classification_evidence_row(row, plans) for row in rows], int(total)


    # What "action status" means to the owner, mapped onto the stored states.
    _ACTION_STATUS_FILTERS: ClassVar[Mapping[str, tuple[str, ...]]] = {
        "failed": ("failed",),
        "pending": ("pending", "processing"),
        "done": ("done",),
        "skipped": ("skipped",),
    }

    @classmethod
    def _classification_filters(
        cls, *, category: str, action_status: str, source: str
    ) -> tuple[str, tuple[str, ...]]:
        """SQL for the list's category, mailbox-action and decider filters."""

        clauses: list[str] = []
        params: list[str] = []
        wanted_categories = [item.strip() for item in category.split(",") if item.strip()]
        if wanted_categories:
            # The category the owner sees: their own confirmation if any.
            clauses.append(
                " and coalesce(nullif(classifications.confirmed_category, ''),"
                " classifications.category) in ("
                + ",".join("?" for _ in wanted_categories)
                + ")"
            )
            params.extend(wanted_categories)
        if source.strip():
            clauses.append(" and classifications.classification_source = ?")
            params.append(source.strip())
        if action_status.strip():
            wanted = action_status.strip()
            if wanted == "none":
                clauses.append(
                    f" and not exists (select 1 from ({EMAIL_DIRECT_ACTION_SQL}) as filter_action"
                    " where filter_action.classification_id=classifications.id"
                    " and filter_action.action_plan_id=classifications.current_action_plan_id)"
                )
            elif wanted in ("failed_retriable", "failed_not_retriable"):
                # Same failed rows the plain "failed" filter lists, split by
                # whether the worker will try this action again.
                retriable_sql = _failed_action_retriable_sql("filter_action")
                if wanted == "failed_not_retriable":
                    retriable_sql = f"not {retriable_sql}"
                clauses.append(
                    f" and exists (select 1 from ({EMAIL_DIRECT_ACTION_SQL}) as filter_action"
                    " where filter_action.classification_id=classifications.id"
                    " and filter_action.action_plan_id=classifications.current_action_plan_id"
                    " and filter_action.status='failed'"
                    f" and {retriable_sql})"
                )
            else:
                statuses = cls._ACTION_STATUS_FILTERS.get(wanted)
                if statuses is None:
                    raise ValueError("action_status is invalid")
                clauses.append(
                    f" and exists (select 1 from ({EMAIL_DIRECT_ACTION_SQL}) as filter_action"
                    " where filter_action.classification_id=classifications.id"
                    " and filter_action.action_plan_id=classifications.current_action_plan_id"
                    " and filter_action.status in ("
                    + ",".join("?" for _ in statuses)
                    + "))"
                )
                params.extend(statuses)
        return "".join(clauses), tuple(params)

    def list_mailbox_action_states(
        self, classification_ids: Sequence[int]
    ) -> dict[int, list[dict[str, str]]]:
        """The mailbox actions of each classification's current plan.

        This is what the list shows beside a message, so a failed move can be
        seen where the message is, with the reason it failed.
        """

        ids = [int(item) for item in classification_ids]
        if not ids:
            return {}
        states: dict[int, list[dict[str, str]]] = {item: [] for item in ids}
        with self._connect() as db:
            rows = db.execute(
                f"""
                select actions.classification_id, actions.action_plan_id,
                       actions.created_at, actions.action_type,
                       actions.status, actions.error, actions.attempt_count,
                       actions.next_attempt_at, actions.provider_operation,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=actions.action_id and r.attempt_number=actions.attempt_count) as explicit_retry_attempt,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=actions.action_id and r.attempt_number=actions.attempt_count+1) as explicit_retry_scheduled,
                       {_direct_retry_configuration_sql('actions')} as explicit_retry_configuration_matches
                from ({EMAIL_DIRECT_ACTION_SQL}) as actions
                join ({EMAIL_CLASSIFICATION_SQL}) as classifications
                  on classifications.id=actions.classification_id
                 and classifications.current_action_plan_id=actions.action_plan_id
                where actions.classification_id in ("""
                + ",".join("?" for _ in ids)
                + """)
                """,
                ids,
            ).fetchall()
        rows = sorted(
            rows,
            key=lambda row: (
                row["created_at"],
                _direct_action_id(
                    str(row["action_plan_id"]), EmailAction(row["action_type"])
                ),
            ),
        )
        for row in rows:
            state = {
                "type": str(row["action_type"]),
                "status": str(row["status"]),
                "error": str(row["error"] or ""),
            }
            if state["status"] == "failed":
                state.update(_direct_action_retry_outlook(
                    row, explicit_retry_attempt=bool(row["explicit_retry_attempt"]),
                    explicit_retry_scheduled=bool(row["explicit_retry_scheduled"]),
                    explicit_retry_configuration_matches=bool(row["explicit_retry_configuration_matches"]),
                ))
            states[int(row["classification_id"])].append(state)
        return states


    @staticmethod
    def _unsubscribe_classification_membership(db: sqlite3.Connection) -> str:
        task_tables = {
            str(row["name"])
            for row in db.execute(
                "select name from sqlite_master where type='table' "
                "and name in ('tasks', 'runs')"
            ).fetchall()
        }
        task_clause = ""
        if task_tables == {"tasks", "runs"}:
            task_clause = f"""
                or classifications.id in (
                    select json_extract(
                               tasks.trigger_message_json,
                               '$.classification_id'
                           )
                    from ({REPLY_TASK_CURRENT_SQL}) as tasks
                    where tasks.channel='email'
                      and json_valid(tasks.trigger_message_json)
                      and json_extract(
                            tasks.trigger_message_json,
                            '$.action_type'
                          )='unsubscribe'
                )
            """
        where = f"""
            classifications.id in (
                select claims.classification_id
                from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) as claims
            )
            or classifications.id in (
                select receipts.classification_id
                from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) as receipts
            )
        """ + task_clause
        return "(" + where + ")"


    @staticmethod
    def _email_context_message_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "stable_message_identity": row["stable_message_identity"],
            "folder": row["folder"],
            "uidvalidity": row["uidvalidity"],
            "uid": row["uid"],
            "rfc_message_id": row["rfc_message_id"],
            "in_reply_to": row["in_reply_to"],
            "references": _json_load(
                row["references_json"], field="references_json", expected_type=list
            ),
            "thread_identity": row["thread_identity"],
            "sender": row["sender"],
            "recipients": _json_load(
                row["recipients_json"], field="recipients_json", expected_type=list
            ),
            "subject": row["subject"],
            "attachment_metadata": _json_load(
                row["attachment_metadata_json"],
                field="attachment_metadata_json",
                expected_type=list,
            ),
            "received_at": row["received_at"],
        }

    def list_email_context_thread(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
    ) -> list[dict[str, Any]]:
        """Return the complete relation component using only message metadata."""

        account_id = account_id.strip()
        stable_message_identity = stable_message_identity.strip()
        if not account_id or not stable_message_identity:
            raise ValueError("email context identity must be non-empty")
        from app.email_canonical_definitions import canonical_column

        fields = (
            "id", "account_id", "stable_message_identity", "folder", "uidvalidity",
            "uid", "rfc_message_id", "in_reply_to", "references_json", "thread_identity",
            "sender", "recipients_json", "subject", "attachment_metadata_json", "received_at",
        )
        selected = ",".join(
            f'{canonical_column("email_messages", field)} as "{field}"'
            for field in fields
        )
        with self._connect() as db:
            rows = db.execute(
                f"select {selected} from main.emails where account_id=? "
                "order by message_received_at, message_id",
                (account_id,),
            ).fetchall()
        trigger = next(
            (
                row
                for row in rows
                if row["stable_message_identity"] == stable_message_identity
            ),
            None,
        )
        if trigger is None:
            return []
        thread_identity = str(trigger["thread_identity"] or "")
        known_ids = {
            value
            for value in (
                trigger["rfc_message_id"],
                trigger["in_reply_to"],
                *_json_load(
                    trigger["references_json"],
                    field="references_json",
                    expected_type=list,
                ),
            )
            if value
        }
        related = {stable_message_identity}
        changed = True
        while changed:
            changed = False
            for row in rows:
                row_identity = str(row["stable_message_identity"])
                row_links = set(
                    _json_load(
                        row["references_json"],
                        field="references_json",
                        expected_type=list,
                    )
                )
                row_links.update(
                    value
                    for value in (row["rfc_message_id"], row["in_reply_to"])
                    if value
                )
                if not (
                    row_identity in related
                    or (thread_identity and row["thread_identity"] == thread_identity)
                    or known_ids & row_links
                ):
                    continue
                if row_identity not in related or not row_links <= known_ids:
                    related.add(row_identity)
                    known_ids.update(row_links)
                    changed = True
        return [
            self._email_context_message_row(row)
            for row in rows
            if row["stable_message_identity"] in related
        ]

    def list_email_context_receipts(
        self,
        *,
        account_id: str,
        stable_message_identity: str,
    ) -> list[dict[str, object]]:
        """Project safe prior reply and unsubscribe readbacks."""

        with self._connect() as db:
            reply_rows = db.execute(
                f"""
                select provider_result_id, provider_operation, display_excerpt, created_at
                from ({EMAIL_REPLY_RECEIPT_SQL})
                where account_id=? and stable_message_identity=?
                """,
                (account_id, stable_message_identity),
            ).fetchall()
            unsubscribe_rows = db.execute(
                f"""
                select receipt_id, outcome, created_at
                from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                where account_id=? and stable_message_identity=?
                """,
                (account_id, stable_message_identity),
            ).fetchall()
        receipts = [
            {
                "receipt_id": row["provider_result_id"],
                "operation": row["provider_operation"],
                "summary": row["display_excerpt"],
                "completed": True,
                "created_at": row["created_at"],
            }
            for row in reply_rows
        ]
        receipts.extend(
            {
                "receipt_id": row["receipt_id"],
                "operation": "unsubscribe_readback",
                "summary": f"Automatic unsubscribe outcome: {row['outcome']}.",
                "completed": True,
                "created_at": row["created_at"],
            }
            for row in unsubscribe_rows
        )
        receipts.sort(key=lambda item: str(item["created_at"]))
        return [
            {key: value for key, value in item.items() if key != "created_at"}
            for item in receipts
        ]

    def get_classification(self, classification_id: int) -> dict[str, Any] | None:
        """Return runtime identity, business state and recipient metadata only."""
        from app.current_email_definitions import CLASS_FIELDS
        from app.email_canonical_definitions import canonical_column

        fields = ",".join(
            f'{canonical_column("email_classifications", field, metadata_owner=True)} as "{field}"'
            for field in CLASS_FIELDS if field != "model_text"
        )
        with self._connect() as db:
            row = db.execute(
                f"select {fields}, message_recipients_json, message_attachment_metadata_json "
                "from main.emails where classification_id=?",
                (classification_id,),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [row] if row else [])
        if row is None:
            return None
        return {
            **self._classification_evidence_row(row, plan_rows),
            "recipients": _json_load(
                row["message_recipients_json"] or "[]",
                field="recipients_json", expected_type=list,
            ),
        }

    def count_unincluded_training_examples(self) -> int:
        """Count the existing eligible owner-feedback set without loading inputs."""
        with self._connect() as db:
            return int(db.execute(
                "select count(*) from main.emails "
                "where classification_id is not null "
                "and class_classification_source='user' and class_status='processed' "
                "and class_confirmed_category is not null and class_confirmed_category<>'' "
                "and (trim(class_model_text)<>'' or json_type(message_source_reference_json,'$.model_input')='object') "
                "and class_included_in_model_id is null"
            ).fetchone()[0])

    def _owner_labelled_rows(self):
        with self._connect() as db:
            return db.execute(
                """
                select classification_id, account_id, stable_message_identity,
                       message_sender as sender, message_subject as subject,
                       class_model_text as model_text,
                       class_confirmed_category as confirmed_category,
                       class_updated_at as updated_at, message_source_reference_json
                from emails
                where classification_id is not null and class_classification_source='user'
                  and coalesce(class_confirmed_category, '') != ''
                  and not (
                    class_model_text=''
                    and coalesce(json_extract(message_source_reference_json,'$.model_input.kind'),'')='provider'
                    and exists (
                      select 1 from provider_state observation
                      where observation.record_type='observation'
                        and observation.account_id=emails.account_id
                        and observation.stable_message_identity=emails.stable_message_identity
                        and observation.state='unavailable'
                    )
                  )
                order by classification_id
                """
            ).fetchall()

    @staticmethod
    def _owner_labelled_rows_stamp(rows) -> str:
        """Same original bytes have the same identity before and after retirement."""
        digest = sha256()
        for row in rows:
            text = str(row["model_text"])
            reference = json.loads(row["message_source_reference_json"] or '{}')
            model_reference = reference.get('model_input')
            input_hash = (sha256(text.encode('utf-8')).hexdigest()
                          if text or model_reference is None
                          else model_reference['sha256'])
            fields = [row[key] for key in (
                'classification_id', 'account_id', 'stable_message_identity',
                'sender', 'subject', 'confirmed_category', 'updated_at')]
            digest.update(json.dumps([*fields, input_hash], ensure_ascii=False).encode('utf-8'))
            digest.update(b'\n')
        return digest.hexdigest()

    def owner_labelled_stamp(self) -> str:
        """Digest the existing owner facts; second-resolution clocks can coincide."""
        return self._owner_labelled_rows_stamp(self._owner_labelled_rows())

    def owner_labelled_examples(self, *, source_reader=None) -> tuple[list[dict[str, str]], str]:
        """Messages the owner has labelled, and the stamp they were read at.

        The Agent is shown the ones most like the message it is classifying, so
        it files mail the way the owner does.
        """
        rows = self._owner_labelled_rows()
        stamp = self._owner_labelled_rows_stamp(rows)
        from app.email_model_input_reader import read_owner_model_inputs

        inputs = read_owner_model_inputs(self, rows, source_reader=source_reader)
        if self.owner_labelled_stamp() != stamp:
            raise ValueError("Email owner labels changed during input read")
        return (
            [
                {
                    "identity": str(row["stable_message_identity"]),
                    "sender": str(row["sender"]),
                    "subject": str(row["subject"]),
                    "text": inputs[(row["account_id"], row["stable_message_identity"])],
                    "category": str(row["confirmed_category"]),
                }
                for row in rows
                if (row["account_id"], row["stable_message_identity"]) in inputs
            ],
            stamp,
        )

    def get_classification_by_stable_identity(
        self, stable_message_identity: str
    ) -> dict[str, Any] | None:
        from app.current_email_definitions import CLASS_FIELDS
        from app.email_canonical_definitions import canonical_column

        fields = ",".join(
            f'{canonical_column("email_classifications", field, metadata_owner=True)} as "{field}"'
            for field in CLASS_FIELDS if field != "model_text"
        )
        with self._connect() as db:
            row = db.execute(
                f"select {fields} from main.emails "
                "where stable_message_identity=? and classification_id is not null",
                (stable_message_identity,),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [row] if row else [])
        return None if row is None else self._classification_row(row, plan_rows)

    def has_stable_classification(self, stable_message_identity: str) -> bool:
        return (
            self.get_classification_by_stable_identity(stable_message_identity)
            is not None
        )

    def has_stable_classification_record(self, stable_message_identity: str) -> bool:
        """Check durable row presence without claiming its plan is valid."""
        with self._connect() as db:
            return db.execute(
                f"select 1 from ({EMAIL_CLASSIFICATION_SQL}) where stable_message_identity=?",
                (stable_message_identity,),
            ).fetchone() is not None

    def classified_stable_message_identities(
        self, identities: Sequence[str]
    ) -> frozenset[str]:
        """Return known messages with bounded SQLite work for a folder batch."""

        normalized = tuple(
            sorted(
                {
                    identity.strip()
                    for identity in identities
                    if isinstance(identity, str) and identity.strip()
                }
            )
        )
        if not normalized:
            return frozenset()
        matches: set[str] = set()
        # SQLite's bound-variable limit is installation-dependent.  Keep this
        # lookup bounded even for the provider's full Junk and Trash folders.
        chunk_size = 500
        with self._connect() as db:
            for start in range(0, len(normalized), chunk_size):
                chunk = normalized[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"select stable_message_identity from ({EMAIL_CLASSIFICATION_SQL}) "
                    f"where stable_message_identity in ({placeholders})",
                    chunk,
                ).fetchall()
                matches.update(str(row["stable_message_identity"]) for row in rows)
        return frozenset(matches)

    def backfill_email_unsubscribe_entry_url(
        self, action_identity: str, entry_url: str
    ) -> bool:
        """Fill in the address of a receipt written before receipts kept one.

        The address must hash to the receipt's own entry reference, so it can
        only be the link that receipt was earned against. A receipt that
        already has an address is left alone. Returns whether one was written.
        """

        with self._connect() as db:
            receipt = db.execute(
                f"select entry_reference, entry_url from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) "
                "where action_identity=?",
                (action_identity,),
            ).fetchone()
            if receipt is None or receipt["entry_url"]:
                return False
            validated = _validate_unsubscribe_entry_url(
                entry_url, entry_reference=receipt["entry_reference"]
            )
            if not validated:
                return False
            db.execute("begin immediate")
            mutation_values = {"entry_url": validated}
            cursor = ActionWriteResult(
                None,
                shared_action_lifecycle.transition(
                    db,
                    "email_unsubscribe",
                    "amend_outcome",
                    values=mutation_values,
                    where_sql="action_identity=? and entry_url=''",
                    parameters=(action_identity,),
                ).rowcount,
            )
            return cursor.rowcount == 1

    def get_email_unsubscribe_entry_url(self, classification_id: int) -> str | None:
        """Return one terminal unsubscribe entry URL after full lineage validation."""

        _require_positive_int(classification_id, field="classification_id")
        with self._connect() as db:
            generic_tables = {
                str(row["name"])
                for row in db.execute(
                    """
                    select name from sqlite_master
                    where type='table' and name in ('tasks', 'runs')
                    """
                ).fetchall()
            }
            if generic_tables != {"tasks", "runs"}:
                return None
            classification = db.execute(
                f"""
                select classifications.id, classifications.account_id,
                       classifications.stable_message_identity,
                       classifications.current_action_plan_id,
                       messages.thread_identity as message_thread_identity
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                """,
                (classification_id,),
            ).fetchone()
            if classification is None:
                return None
            receipts = db.execute(
                f"""
                select action_identity, effect_digest, action_plan_id,
                       action_plan_version, classification_id, account_id,
                       stable_message_identity, thread_identity, entry_reference,
                       entry_url
                from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                where classification_id=? and trim(entry_url) != ''
                order by created_at desc, action_identity desc
                """,
                (classification_id,),
            ).fetchall()
            for receipt in receipts:
                # The address is bound to its receipt by its own hash, and the
                # receipt to this classification by the query. The audited
                # lifecycle's Agent-run lineage is not required: a direct
                # unsubscribe has no Agent run, so demanding one hid the
                # address of every receipt the live service writes.
                try:
                    return _validate_unsubscribe_entry_url(
                        receipt["entry_url"],
                        entry_reference=receipt["entry_reference"],
                    )
                except ValueError:
                    continue
        return None

    def stable_classification_uids(
        self, *, account_id: str, folder: str, uidvalidity: int
    ) -> frozenset[int]:
        with self._connect() as db:
            rows = db.execute(
                f"""
                select uid from ({EMAIL_CLASSIFICATION_SQL})
                where account_id=? and folder=? and uidvalidity=?
                """,
                (account_id, folder, uidvalidity),
            ).fetchall()
        return frozenset(int(row["uid"]) for row in rows)

    def classified_provider_uids(
        self, *, account_id: str, folder: str, uidvalidity: int
    ) -> dict[int, dict[str, str]]:
        """Return known classification metadata keyed by provider UID."""

        with self._connect() as db:
            rows = db.execute(
                f"""
                select uid, stable_message_identity, subject, sender
                from ({EMAIL_CLASSIFICATION_SQL})
                where account_id=? and folder=? and uidvalidity=?
                """,
                (account_id, folder, uidvalidity),
            ).fetchall()
        return {
            int(row["uid"]): {
                "stable_message_identity": str(row["stable_message_identity"]),
                "subject": str(row["subject"] or ""),
                "sender": str(row["sender"] or ""),
            }
            for row in rows
        }

    def classified_provider_targets(
        self, *, account_id: str, uidvalidity: int | None = None
    ) -> tuple[dict[str, str | int], ...]:
        """Return bounded locator metadata for cross-folder provider lookup."""

        with self._connect() as db:
            if uidvalidity is None:
                rows = db.execute(
                    f"""
                    select uid, folder, stable_message_identity, subject, sender
                    from ({EMAIL_CLASSIFICATION_SQL})
                    where account_id=?
                    order by updated_at desc, id desc
                    limit 50
                    """,
                    (account_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    f"""
                    select uid, folder, stable_message_identity, subject, sender
                    from ({EMAIL_CLASSIFICATION_SQL})
                    where account_id=? and uidvalidity=?
                    order by updated_at desc, id desc
                    limit 50
                    """,
                    (account_id, uidvalidity),
                ).fetchall()
        return tuple(
            {
                "uid": int(row["uid"]),
                "folder": str(row["folder"]),
                "stable_message_identity": str(row["stable_message_identity"]),
                "subject": str(row["subject"] or ""),
                "sender": str(row["sender"] or ""),
            }
            for row in rows
        )

    @contextmanager
    def _connect_or_reuse(
        self, db: sqlite3.Connection | None
    ) -> Iterator[sqlite3.Connection]:
        if db is not None:
            yield db
            return
        with self._connect() as opened:
            yield opened

    def list_unsubscribe_states(
        self, classification_ids: Sequence[int]
    ) -> dict[int, dict[str, Any] | None]:
        """Newest unsubscribe outcome per email, read on one connection.

        Each state is the last unsubscribe event of the same projection the
        detail page shows, so a list row and its detail cannot disagree. Events
        come back oldest first.
        """

        states: dict[int, dict[str, Any] | None] = {}
        with self._connect() as db:
            for classification_id in classification_ids:
                events = self.list_email_classification_observability(
                    classification_id, db=db
                )
                latest = next(
                    (
                        event
                        for event in reversed(events)
                        if event.get("kind") == "unsubscribe"
                    ),
                    None,
                )
                states[classification_id] = (
                    None
                    if latest is None
                    else {
                        "status": latest.get("status"),
                        "outcome": latest.get("outcome"),
                        "error": latest.get("error", ""),
                    }
                )
        return states

    def list_email_classification_observability(
        self, classification_id: int, *, db: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        """Project safe action and external-write evidence for one email.

        The projection deliberately excludes provider targets, raw parameters,
        task payloads, and browser-private coordinates. Unsubscribe result text
        is already normalized and redacted at persistence time.
        """

        _require_positive_int(classification_id, field="classification_id")
        with self._connect_or_reuse(db) as db:
            classification = db.execute(
                f"""
                select classifications.id, classifications.account_id,
                       classifications.stable_message_identity,
                       classifications.current_action_plan_id,
                       messages.thread_identity as message_thread_identity
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.account_id=classifications.account_id
                 and messages.stable_message_identity=
                     classifications.stable_message_identity
                where classifications.id=?
                """,
                (classification_id,),
            ).fetchone()
            if classification is None:
                return []
            action_rows = db.execute(
                f"""
                select action_id, action_type, status, attempt_count,
                       provider_operation, provider_result_id, error,
                       started_at, finished_at, created_at
                from ({EMAIL_DIRECT_ACTION_SQL})
                where classification_id=?
                order by created_at, action_id
                """,
                (classification_id,),
            ).fetchall()
            attempts = db.execute(
                f"""
                select attempts.action_id, attempts.attempt_number,
                       attempts.status, attempts.provider_operation,
                       attempts.provider_result_id, attempts.error,
                       attempts.started_at, attempts.finished_at
                from ({DIRECT_EMAIL_ATTEMPT_SQL}) as attempts
                join ({EMAIL_DIRECT_ACTION_SQL}) as actions
                  on actions.action_id=attempts.action_id
                where actions.classification_id=?
                order by attempts.action_id, attempts.attempt_number
                """,
                (classification_id,),
            ).fetchall()
            reply_rows = db.execute(
                f"""
                select action_identity, provider_operation, provider_result_id,
                       display_excerpt, created_at
                from ({EMAIL_REPLY_RECEIPT_SQL})
                where classification_id=?
                order by created_at, action_identity
                """,
                (classification_id,),
            ).fetchall()
            unsubscribe_rows = db.execute(
                f"""
                select action_identity, effect_digest, action_plan_id,
                       action_plan_version, classification_id, account_id,
                       stable_message_identity, thread_identity, receipt_id,
                       evidence, result_text, observation_digest,
                       result_text_truncated, result_text_digest, outcome,
                       created_at, completed_at
                from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                where classification_id=?
                order by created_at, action_identity
                """,
                (classification_id,),
            ).fetchall()
            generic_tables = {
                str(row["name"])
                for row in db.execute(
                    """
                    select name from sqlite_master
                    where type='table' and name in ('tasks', 'runs')
                    """
                ).fetchall()
            }
            task_rows: Sequence[sqlite3.Row] = ()
            if generic_tables == {"tasks", "runs"}:
                task_rows = db.execute(
                    f"""
                    select id, conversation_id, trigger_message_id,
                           trigger_message_json, input_compacted, input_provenance_json,
                           execution_generation, status, error, created_at
                    from (
                        select reply_tasks.*,
                               case when input_compacted=1 then
                                   case when json_valid(input_provenance_json) then
                                       json_extract(input_provenance_json, '$.source') end
                                   else trigger_message_json end as lineage_source_json
                        from ({REPLY_TASK_CURRENT_SQL}) as reply_tasks
                        where channel='email'
                    )
                    where json_valid(lineage_source_json)
                      and json_extract(lineage_source_json, '$.classification_id')=?
                      and json_extract(lineage_source_json, '$.action_type')='unsubscribe'
                    order by created_at, id
                    """,
                    (classification_id,),
                ).fetchall()
            lineage_by_action: dict[str, dict[str, object]] = {}
            if unsubscribe_rows and generic_tables == {"tasks", "runs"}:
                for row in unsubscribe_rows:
                    _validate_durable_unsubscribe_result_text(
                        row["result_text"],
                        row["observation_digest"],
                        row["result_text_truncated"],
                        row["result_text_digest"],
                    )
                    lineage = _audited_unsubscribe_lineage(
                        db,
                        receipt=row,
                        classification=classification,
                    )
                    if lineage is not None:
                        lineage_by_action[str(row["action_identity"])] = lineage
            else:
                for row in unsubscribe_rows:
                    _validate_durable_unsubscribe_result_text(
                        row["result_text"],
                        row["observation_digest"],
                        row["result_text_truncated"],
                        row["result_text_digest"],
                    )
            unsubscribe_steps = db.execute(
                f"""
                select steps.action_identity, steps.sequence, steps.operation,
                       steps.state, steps.reference
                from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) as steps
                join ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) as receipts
                  on receipts.action_identity=steps.action_identity
                where receipts.classification_id=?
                order by steps.action_identity, steps.sequence
                """,
                (classification_id,),
            ).fetchall()
            terminal_unsubscribe_actions = {
                str(row["action_identity"]) for row in unsubscribe_rows
            }
            inflight_unsubscribe_events = []
            for task in task_rows:
                if str(task["trigger_message_id"]) in terminal_unsubscribe_actions:
                    continue
                lineage = _current_unsubscribe_task_lineage(
                    db,
                    task=task,
                    classification=classification,
                )
                if lineage is not None:
                    inflight_unsubscribe_events.append(lineage)
            terminal_attempt_ids_by_action: dict[str, list[int]] = {}
            for action_identity in terminal_unsubscribe_actions:
                if action_identity in lineage_by_action:
                    continue
                task = next(
                    (
                        candidate
                        for candidate in task_rows
                        if str(candidate["trigger_message_id"]) == action_identity
                    ),
                    None,
                )
                if task is None or _current_unsubscribe_task_lineage(
                    db, task=task, classification=classification
                ) is None:
                    continue
                attempt_ids = _email_unsubscribe_attempt_ids(
                    db,
                    conversation_id=str(task["conversation_id"]),
                    action_identity=action_identity,
                )
                if attempt_ids:
                    terminal_attempt_ids_by_action[action_identity] = attempt_ids

        attempts_by_action: dict[str, list[dict[str, Any]]] = {}
        for row in attempts:
            attempts_by_action.setdefault(row["action_id"], []).append(
                {
                    "attempt_number": row["attempt_number"],
                    "status": row["status"],
                    "provider_operation": row["provider_operation"],
                    "provider_result_id": row["provider_result_id"],
                    "error": row["error"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
            )
        events: list[dict[str, Any]] = []
        events.extend(inflight_unsubscribe_events)
        for row in action_rows:
            events.append(
                {
                    "kind": "provider_action",
                    "operation": row["action_type"],
                    "action_id": row["action_id"],
                    "status": row["status"],
                    "attempt_count": row["attempt_count"],
                    "provider_operation": row["provider_operation"],
                    "provider_result_id": row["provider_result_id"],
                    "error": row["error"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "created_at": row["created_at"],
                    "attempts": attempts_by_action.get(row["action_id"], []),
                }
            )
        for row in reply_rows:
            events.append(
                {
                    "kind": "auto_reply",
                    "operation": "auto_reply",
                    "action_identity": row["action_identity"],
                    "status": "done",
                    "provider_operation": row["provider_operation"],
                    "receipt_id": row["provider_result_id"],
                    "summary": row["display_excerpt"],
                    "created_at": row["created_at"],
                }
            )
        steps_by_action: dict[str, list[dict[str, Any]]] = {}
        for row in unsubscribe_steps:
            steps_by_action.setdefault(row["action_identity"], []).append(
                {
                    "sequence": row["sequence"],
                    "operation": row["operation"],
                    "state": row["state"],
                    "reference": row["reference"],
                }
            )
        for row in unsubscribe_rows:
            action_identity = str(row["action_identity"])
            lineage = lineage_by_action.get(action_identity)
            attempt_ids = (
                list(lineage.get("attempt_ids", []))
                if lineage is not None
                else []
            )
            if not attempt_ids:
                attempt_ids = terminal_attempt_ids_by_action.get(action_identity, [])
            event = {
                "kind": "unsubscribe",
                "operation": "unsubscribe",
                "consumer_run_ids": (
                    lineage["consumer_run_ids"] if lineage is not None else []
                ),
                "audit_run_ids": (
                    lineage["audit_run_ids"] if lineage is not None else []
                ),
                "status": "done",
                "outcome": row["outcome"],
                "receipt_id": row["receipt_id"],
                "result_text": row["result_text"],
                "evidence": row["evidence"],
                "observation_digest": row["observation_digest"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
                "steps": steps_by_action.get(action_identity, []),
                "_sort_created_at": row["created_at"],
            }
            if attempt_ids:
                event["attempt_ids"] = attempt_ids
            if lineage is not None:
                event.update(lineage)
            events.append(event)
        events.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("_sort_created_at") or ""),
                str(item["kind"]),
            )
        )
        for event in events:
            event.pop("_sort_created_at", None)
        return events


    @staticmethod
    def _selected_training_rows(db: sqlite3.Connection, *, include_input: bool):
        input_fields = (
            ", messages.recipients_json, messages.subject, messages.normalized_text,"
            " messages.attachment_metadata_json, messages.rfc_message_id,"
            " messages.in_reply_to, messages.references_json" if include_input else ""
        )
        return db.execute(f"""
                select classifications.id, classifications.account_id,
                       classifications.stable_message_identity,
                       classifications.thread_id, classifications.classification_source,
                       classifications.category, classifications.confirmed_category,
                       classifications.confirmed_at, classifications.updated_at,
                       messages.sender, messages.updated_at as message_updated_at{input_fields}
                from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                join ({EMAIL_MESSAGE_SQL}) as messages
                  on messages.stable_message_identity=classifications.stable_message_identity
                where classifications.status='processed'
                  and classifications.classification_source in ('agent', 'user')
                  and (
                    (classifications.classification_source='agent'
                     and classifications.category is not null
                     and classifications.category != '')
                    or (classifications.classification_source='user'
                        and classifications.confirmed_category is not null
                        and classifications.confirmed_category != '')
                  )
                order by classifications.id asc
                """).fetchall()

    @staticmethod
    def _selected_training_label(row: sqlite3.Row) -> dict[str, Any]:
        from app.email_important import important_training_label

        return {
            "classification_id": int(row["id"]),
            "account_id": str(row["account_id"]),
            "stable_message_identity": str(row["stable_message_identity"]),
            "provider_thread_id": str(row["thread_id"]) if row["thread_id"] else None,
            "source": (
                "agent_auto_label"
                if row["classification_source"] == "agent"
                else "user_feedback"
            ),
            "category_key": str(
                row["category"]
                if row["classification_source"] == "agent"
                else row["confirmed_category"]
            ),
            "important": important_training_label(
                category=str(
                    row["category"]
                    if row["classification_source"] == "agent"
                    else row["confirmed_category"]
                ),
                sender=str(row["sender"] or ""),
            ),
            "label_recorded_at": str(
                row["confirmed_at"] or row["updated_at"]
            ),
        }

    def list_selected_training_labels(self) -> list[dict[str, Any]]:
        """Existing selected source labels, without reading either input text."""
        with self._connect() as db:
            rows = self._selected_training_rows(db, include_input=False)
        return [self._selected_training_label(row) for row in rows]

    def list_selected_training_source_metadata(self) -> list[dict[str, Any]]:
        """Current selected labels and existing update clocks, without input text."""
        with self._connect() as db:
            rows = self._selected_training_rows(db, include_input=False)
        return [
            {**self._selected_training_label(row),
             "classification_updated_at": row["updated_at"],
             "message_updated_at": row["message_updated_at"]}
            for row in rows
        ]

    def latest_training_snapshot_reference(
        self, snapshot_version: str,
    ) -> dict[str, object] | None:
        """Read an external frozen baseline's persisted identity, never its input."""
        with self._connect() as db:
            row = db.execute(
                f"select snapshot_id,snapshot_version,snapshot_digest,description_version,"
                "input_schema_version,seed,observed_at "
                f"from ({config_projection('email_training_snapshots')}) "
                "where frozen=1 and snapshot_version=? "
                "and json_extract(manifest_json, '$.sample_count') is not null "
                "order by observed_at desc,snapshot_id desc limit 1",
                (snapshot_version,),
            ).fetchone()
        return None if row is None else dict(row)

    def list_selected_training_records(self) -> list[dict[str, Any]]:
        """Return the service-produced labels that can be frozen for training.

        The caller decides which sources and categories participate.  This
        method deliberately does not require a provider-folder snapshot: user
        feedback and Agent labels are valid, independently selectable sources.
        """

        with self._connect() as db:
            rows = self._selected_training_rows(db, include_input=True)
        from app.email_training_snapshot import canonical_model_input

        def stored_address(value: object) -> dict[str, str]:
            text = str(value or "").strip()
            local, separator, domain = text.rpartition("@")
            return (
                {"name": "", "email": text}
                if separator and local and domain
                else {"name": text, "email": ""}
            )

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                recipients = json.loads(str(row["recipients_json"]))
                attachments = json.loads(str(row["attachment_metadata_json"]))
                references = json.loads(str(row["references_json"]))
            except json.JSONDecodeError as exc:
                raise EmailPersistenceCorruption(
                    "stored email training input JSON is corrupt"
                ) from exc
            if not all(isinstance(item, str) for item in recipients):
                raise EmailPersistenceCorruption("stored email recipients are corrupt")
            if not isinstance(attachments, list) or not isinstance(references, list):
                raise EmailPersistenceCorruption("stored email training metadata is corrupt")
            model_input = canonical_model_input(
                {
                    "sender": stored_address(row["sender"]),
                    "to_recipients": [stored_address(item) for item in recipients],
                    "cc_recipients": [],
                    "subject": row["subject"],
                    "body": row["normalized_text"],
                    "headers": {
                        "message-id": row["rfc_message_id"],
                        "in-reply-to": row["in_reply_to"],
                        "references": " ".join(str(item) for item in references),
                    },
                    "attachments": attachments,
                }
            )
            result.append({**self._selected_training_label(row), "normalized_model_input": model_input})
        return result

    def persist_training_snapshot(self, snapshot: object) -> dict[str, object]:
        """Append metadata after publishing one complete external training file."""

        from app.email_training_data import summarize, write_snapshot
        from app.email_training_snapshot import (
            FolderTrainingSnapshot,
            validate_folder_training_snapshot,
        )

        if type(snapshot) is not FolderTrainingSnapshot:
            raise TypeError("snapshot must be a FolderTrainingSnapshot")
        validate_folder_training_snapshot(snapshot)
        manifest_json = json.dumps(
            summarize(snapshot),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        created_at = self._now()
        with self._connect() as db:
            existing = db.execute(
                f"select snapshot_digest from ({config_projection('email_training_snapshots')}) "
                "where snapshot_id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["snapshot_digest"] != snapshot.snapshot_digest:
                    raise EmailTrainingSnapshotConflict(
                        "training snapshot id already exists with different content"
                    )
                loaded = self._get_training_snapshot(db, snapshot.snapshot_id)
                assert loaded is not None
                return loaded
            # A watermark counts the label changes this snapshot adds to the
            # ones before it, so it must be measured against the last snapshot
            # of its own kind. Measured against a different kind, correcting a
            # label back to what that snapshot already said read as a smaller
            # number, and promotion refused the run for "regressing".
            previous_snapshot = db.execute(
                f"""
                select snapshot_id, folder_label_watermark,
                       important_label_watermark
                from ({config_projection('email_training_snapshots')})
                where frozen=1 and snapshot_version=?
                  and json_extract(manifest_json, '$.sample_count') is not null
                order by observed_at desc, snapshot_id desc
                limit 1
                """,
                (snapshot.snapshot_version,),
            ).fetchone() or db.execute(
                f"""
                select snapshot_id, folder_label_watermark,
                       important_label_watermark
                from ({config_projection('email_training_snapshots')})
                where frozen=1
                  and snapshot_version='email-folder-training-snapshot-v1'
                  and json_extract(manifest_json, '$.sample_count') is not null
                order by observed_at desc, snapshot_id desc
                limit 1
                """
            ).fetchone()
            previous_rows: dict[tuple[str, str], object] = {}
            folder_label_watermark = 0
            important_label_watermark = 0
            if previous_snapshot is not None:
                folder_label_watermark = int(
                    previous_snapshot["folder_label_watermark"]
                )
                important_label_watermark = int(
                    previous_snapshot["important_label_watermark"]
                )
                previous_data = self._get_training_snapshot(
                    db, str(previous_snapshot["snapshot_id"])
                )
                if previous_data is not None:
                    previous_rows = {
                        (str(row["account_id"]), str(row["stable_message_identity"])): row
                        for row in previous_data["observations"]
                    }
            for row in snapshot.observations:
                previous = previous_rows.get(
                    (row.account_id, row.stable_message_identity)
                )
                if row.category_key is not None and (
                    previous is None or previous["category_key"] != row.category_key
                ):
                    folder_label_watermark += 1
                if previous is None or bool(previous["important"]) != row.important:
                    important_label_watermark += 1
            write_snapshot(self.path, snapshot)
            try:
                db.execute("begin immediate")
                insert_config(db, "email_training_snapshots", {
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_version": snapshot.snapshot_version,
                    "description_version": snapshot.description_version,
                    "input_schema_version": snapshot.input_schema_version,
                    "seed": snapshot.seed, "observed_at": snapshot.observed_at,
                    "snapshot_digest": snapshot.snapshot_digest,
                    "manifest_json": manifest_json, "frozen": 1,
                    "created_at": created_at,
                    "folder_label_watermark": folder_label_watermark,
                    "important_label_watermark": important_label_watermark,
                })
            except sqlite3.IntegrityError as exc:
                raise EmailTrainingSnapshotConflict(
                    "training snapshot already exists or violates frozen constraints"
                ) from exc
            stored = self._get_training_snapshot(db, snapshot.snapshot_id)
            assert stored is not None
            return stored

    def get_training_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty text")
        with self._connect() as db:
            return self._get_training_snapshot(db, snapshot_id)

    def pin_training_snapshot(self, run_id: str, snapshot_id: str) -> None:
        """Bind the complete current payload before launching its training child."""
        from app.email_training_data import write_pin

        with self._connect() as db:
            latest = db.execute(
                f"select snapshot_id, snapshot_digest from ({config_projection('email_training_snapshots')}) "
                "where frozen=1 and json_extract(manifest_json, '$.sample_count') is not null "
                "and snapshot_version=(select snapshot_version from "
                f"({config_projection('email_training_snapshots')}) where snapshot_id=?) "
                "order by observed_at desc, snapshot_id desc limit 1",
                (snapshot_id,),
            ).fetchone()
            if latest is None or latest["snapshot_id"] != snapshot_id:
                raise EmailTrainingSnapshotConflict("training snapshot is unavailable to pin")
            write_pin(self.path, run_id, snapshot_id, str(latest["snapshot_digest"]))

    def prune_training_snapshot_data(self, *, training_active: bool) -> list[Path]:
        """Keep each type's latest publication baseline after readers finish."""
        from app.email_training_data import prune_snapshots

        if training_active:
            return []
        with self._connect() as db:
            latest = db.execute(
                "select snapshot_digest from ("
                "select snapshot_digest, row_number() over ("
                "partition by snapshot_version order by observed_at desc, snapshot_id desc"
                f") as ordinal from ({config_projection('email_training_snapshots')}) where frozen=1 "
                "and json_extract(manifest_json, '$.sample_count') is not null"
                ") where ordinal=1"
            ).fetchall()
        return prune_snapshots(
            self.path,
            {str(row["snapshot_digest"]) for row in latest},
        )

    def list_provider_folder_correction_conflicts(
        self, snapshot_id: str
    ) -> list[dict[str, object]]:
        """Return current folder truth that conflicts with the original prediction."""

        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be non-empty text")
        frozen = self.get_training_snapshot(snapshot_id)
        if frozen is None:
            return []
        with self._connect() as db:
            predictions = {
                (str(row["account_id"]), str(row["stable_message_identity"])): row["predicted_category"]
                for row in db.execute(
                    "select account_id, stable_message_identity, predicted_category "
                    f"from ({EMAIL_CLASSIFICATION_SQL}) where predicted_category is not null"
                )
            }
        result = []
        for row in frozen["observations"]:
            predicted = predictions.get((row["account_id"], row["stable_message_identity"]))
            if row["category_key"] is not None and predicted is not None and predicted != row["category_key"]:
                result.append({
                    "sample_id": row["stable_message_identity"],
                    "group_key": row["group_key"],
                    "predicted_category": predicted,
                    "confirmed_category": row["category_key"],
                    "redacted_text": row["normalized_model_input"],
                })
        return sorted(result, key=lambda row: row["sample_id"])

    def record_classifier_runtime_sample(
        self,
        *,
        model_id: str,
        outcome: str,
        fallback_code: str = "",
        cache_hit: bool,
        runtime_warm: bool,
        queue_ms: float,
        http_ms: float,
        embedding_ms: float,
        head_ms: float,
        total_ms: float,
    ) -> None:
        """Persist one bounded, content-free runtime timing observation."""

        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be non-empty")
        if outcome not in {"success", "rejected", "failure"}:
            raise ValueError("runtime outcome is invalid")
        if fallback_code and _RUNTIME_CODE.fullmatch(fallback_code) is None:
            raise ValueError("fallback_code must be a controlled code")
        if type(cache_hit) is not bool or type(runtime_warm) is not bool:
            raise TypeError("runtime flags must be boolean")
        values = (queue_ms, http_ms, embedding_ms, head_ms, total_ms)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in values
        ):
            raise ValueError("runtime timings must be finite and non-negative")
        with self._connect() as db:
            db.execute(
                "insert into email_classifier_runtime_samples "
                "(model_id,outcome,fallback_code,cache_hit,runtime_warm,queue_ms,"
                "http_ms,embedding_ms,head_ms,total_ms,created_at) "
                "values(?,?,?,?,?,?,?,?,?,?,?)",
                (model_id.strip(), outcome, fallback_code, int(cache_hit),
                 int(runtime_warm), float(queue_ms), float(http_ms),
                 float(embedding_ms), float(head_ms), float(total_ms), self._now()),
            )
            db.execute(
                """
                delete from email_classifier_runtime_samples
                where id not in (
                    select id from email_classifier_runtime_samples
                    order by id desc limit ?
                  )
                """,
                (MAX_CLASSIFIER_RUNTIME_SAMPLES,),
            )


    def model_scan_progress(self) -> dict[str, object] | None:
        """Sum how far the model's sweep has got across every account and folder.

        Each scanned batch leaves the backlog it saw and the most that was
        waiting in this sweep; the bar is how much of that peak is worked off.
        """

        try:
            with self._connect() as db:
                rows = db.execute(
                    'select value from (' + state_projection('service_state') + ") where key like 'email_model_scan_progress:%'"
                ).fetchall()
        except sqlite3.Error:
            return None
        remaining = peak = 0
        latest = ""
        for row in rows:
            try:
                state = json.loads(str(row["value"]))
            except (TypeError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            row_remaining, row_peak = state.get("remaining"), state.get("peak")
            if type(row_remaining) is not int or type(row_peak) is not int:
                continue
            remaining += max(0, row_remaining)
            peak += max(row_remaining, row_peak, 0)
            latest = max(latest, str(state.get("at") or ""))
        if not peak and not latest:
            return None
        return {
            "remaining": remaining,
            "total": peak,
            "done": max(0, peak - remaining),
            "updated_at": latest,
        }

    def classifier_runtime_rate(self, *, window_seconds: int = 600) -> dict[str, object]:
        """What the live model has done lately: how many, and how long each took.

        Every runtime sample is one message the model was asked about, whatever
        it answered, so the count over a window is how many it judged. The time
        is the model's own (queue, embedding call and output head), never the
        mailbox action that follows it.
        """

        if type(window_seconds) is not int or window_seconds <= 0:
            raise ValueError("window_seconds must be a positive integer")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        ).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "select total_ms, created_at as recorded_at "
                "from email_classifier_runtime_samples where created_at >= ?",
                (cutoff,),
            ).fetchall()
        times = sorted(float(row["total_ms"]) for row in rows)
        latest_at = max((row["recorded_at"] for row in rows), default=None)
        recent_only = False
        if not rows:
            # Nothing came in during the window. The model's speed has not
            # changed for that, so report the last batch it did judge and say so.
            with self._connect() as db:
                last = db.execute(
                    "select total_ms, created_at as recorded_at "
                    "from email_classifier_runtime_samples order by id desc limit ?",
                    (_RUNTIME_LATENCY_FALLBACK_SAMPLES,),
                ).fetchall()
            times = sorted(float(row["total_ms"]) for row in last)
            latest_at = max((row["recorded_at"] for row in last), default=None)
            recent_only = bool(last)

        def percentile(fraction: float) -> float:
            return round(times[min(len(times) - 1, int(len(times) * fraction))], 1)

        return {
            "window_seconds": window_seconds,
            "evaluated": len(rows),
            "per_minute": round(len(rows) / (window_seconds / 60), 1),
            "latest_at": latest_at,
            "latency_ms": (
                {
                    "mean": round(sum(times) / len(times), 1),
                    "p50": percentile(0.5),
                    "p95": percentile(0.95),
                }
                if times
                else None
            ),
            # True when the figures come from the last batch judged, not from
            # the window itself.
            "latency_from_last_batch": recent_only,
        }

    def classifier_runtime_observability(
        self, *, model_id: str
    ) -> dict[str, object]:
        """Read bounded cross-process timing percentiles and fallback counters."""

        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be non-empty")
        with self._connect() as db:
            rows = db.execute(
                "select outcome, fallback_code, cache_hit, runtime_warm, "
                "queue_ms, http_ms, embedding_ms, head_ms, total_ms from ("
                + "select * from email_classifier_runtime_samples"
                + ") where model_id=? order by id desc limit ?",
                (model_id.strip(), MAX_CLASSIFIER_RUNTIME_SAMPLES),
            ).fetchall()

        def percentile(values: list[float], quantile: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            position = (len(ordered) - 1) * quantile
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return ordered[lower]
            weight = position - lower
            return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

        def segment(selected: list[sqlite3.Row]) -> dict[str, object]:
            return {
                "sample_count": len(selected),
                "stages": {
                    stage: {
                        key: percentile(
                            [float(row[f"{stage}_ms"]) for row in selected], quantile
                        )
                        for key, quantile in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
                    }
                    for stage in ("queue", "http", "embedding", "head", "total")
                },
            }

        all_rows = list(rows)
        warm_success = [
            row
            for row in rows
            if row["runtime_warm"] == 1 and row["outcome"] == "success"
        ]
        timing = {
            "all": segment(all_rows),
            "warm_success": segment(warm_success),
            "warm_success_cache": segment(
                [row for row in warm_success if row["cache_hit"] == 1]
            ),
            "warm_success_remote": segment(
                [row for row in warm_success if row["cache_hit"] == 0]
            ),
            "slo_status": (
                "not_enough_data"
                if not warm_success
                else "compliant"
                if percentile(
                    [float(row["total_ms"]) for row in warm_success], 0.95
                )
                < 500.0
                else "non_compliant"
            ),
        }
        fallback_counts: dict[str, int] = {}
        for row in rows:
            code = str(row["fallback_code"] or "")
            if code:
                fallback_counts[code] = fallback_counts.get(code, 0) + 1
        return {"timing": timing, "fallback_counts": fallback_counts}

    def latest_training_snapshot_state(self) -> dict[str, object] | None:
        """Return the latest frozen snapshot and cumulative label-change watermarks."""

        with self._connect() as db:
            latest = db.execute(
                f"""
                select snapshot_id, snapshot_version, snapshot_digest,
                       description_version, input_schema_version, observed_at,
                       folder_label_watermark, important_label_watermark,
                       manifest_json
                from ({config_projection('email_training_snapshots')})
                where frozen=1
                  and snapshot_version='email-folder-training-snapshot-v1'
                  and json_extract(manifest_json, '$.sample_count') is not null
                order by observed_at desc, snapshot_id desc
                limit 1
                """
            ).fetchone()
            if latest is None:
                return None
            summary = json.loads(str(latest["manifest_json"]))
            description_rows = db.execute(
                f"""
                select category_key, core_description, include_json, exclude_json,
                       description_version
                from ({config_projection('email_category_configs')}) where enabled=1
                order by category_key
                """
            ).fetchall()
            enabled_categories = {row["category_key"] for row in description_rows}
            from app.email_description_optimizer import description_set_digest
            from app.email_embedding_classifier import CategoryDescription

            descriptions = {
                row["category_key"]: CategoryDescription(
                    core=row["core_description"],
                    include=tuple(
                        _json_load(
                            row["include_json"],
                            field="include_json",
                            expected_type=list,
                        )
                    ),
                    exclude=tuple(
                        _json_load(
                            row["exclude_json"],
                            field="exclude_json",
                            expected_type=list,
                        )
                    ),
                    version=row["description_version"],
                )
                for row in description_rows
            }
            current_description_version = (
                "description-set-sha256:" + description_set_digest(descriptions)
                if descriptions
                else "description-set-unavailable"
            )
            split_summary = summary.get("splits", {})

            def split_categories(name: str) -> set[str]:
                row = split_summary.get(name)
                if row is None:
                    return set()
                return set(row["categories"])

            def split_has_both_important(name: str) -> bool:
                row = split_summary.get(name)
                return bool(
                    row is not None
                    and int(row["count"]) > 0
                    and row["important"] == [False, True]
                )

            train_categories = split_categories("train")
            validation_categories = split_categories("validation")
            test_categories = split_categories("test")
            minimum_ready = bool(
                train_categories
                and validation_categories
                and test_categories
                and enabled_categories
                and enabled_categories <= train_categories
                and enabled_categories <= validation_categories
                and enabled_categories <= test_categories
                and all(split_has_both_important(name) for name in ("train", "validation", "test"))
            )
            latest_category_counts = summary.get("category_sample_counts", {})
            latest_category_group_counts = summary.get("category_group_counts", {})
            return {
                "snapshot_id": latest["snapshot_id"],
                "snapshot_version": latest["snapshot_version"],
                "snapshot_sha": latest["snapshot_digest"],
                "description_version": current_description_version,
                "input_schema_version": latest["input_schema_version"],
                "folder_label_watermark": int(latest["folder_label_watermark"]),
                "important_label_watermark": int(
                    latest["important_label_watermark"]
                ),
                "minimum_ready": minimum_ready,
                "sample_count": int(summary.get("sample_count", 0)),
                "group_count": int(summary.get("group_count", 0)),
                "category_sample_counts": latest_category_counts,
                "category_group_counts": latest_category_group_counts,
            }

    def _provider_action_throughput(
        self, *, now: datetime, window_minutes: int = 30
    ) -> dict[str, object]:
        """How fast mailbox actions are really being carried out, end to end.

        This is the time a mailbox action takes from the claim to the verified
        result, including the connection and the read-back, not the model's own
        time. It is measured over the actions finished lately.
        """

        cutoff = (
            (now - timedelta(minutes=window_minutes))
            .astimezone(timezone.utc)
            .isoformat(timespec="seconds")
        )
        with self._connect() as db:
            rows = db.execute(
                f"""
                select started_at, finished_at from ({EMAIL_DIRECT_ACTION_SQL})
                where status in ('done', 'skipped') and finished_at>=?
                """,
                (cutoff,),
            ).fetchall()
        seconds: list[float] = []
        for row in rows:
            try:
                started = datetime.fromisoformat(str(row["started_at"]))
                finished = datetime.fromisoformat(str(row["finished_at"]))
            except ValueError:
                continue
            seconds.append(max(0.0, (finished - started).total_seconds()))
        seconds.sort()
        return {
            "window_minutes": window_minutes,
            "finished": len(rows),
            "per_minute": round(len(rows) / window_minutes, 1),
            "median_seconds": (
                round(seconds[len(seconds) // 2], 1) if seconds else None
            ),
        }

    def email_processing_progress(self, *, now: datetime) -> dict[str, Any]:
        """Exact counts of where mail processing stands, in one read.

        There is no separate history job to measure: the scan advances one
        cursor per folder and everything it finds flows through the same
        queues. So progress is what those queues hold: provider actions over
        the last 24 hours (done against everything planned), the Agent
        classification queue, unsubscribe tasks, and mail waiting for the
        owner, plus where each folder's scan last got to.
        """

        since = (now - timedelta(days=1)).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
        with self._connect() as db:
            actions = {
                str(row["status"]): int(row["n"])
                for row in db.execute(
                    f"select status, count(*) as n from ({EMAIL_DIRECT_ACTION_SQL}) "
                    "where created_at>=? group by status",
                    (since,),
                )
            }
            provider_action_backlog = {
                str(row["status"]): int(row["n"])
                for row in db.execute(
                    f"""
                    select action.status, count(*) as n
                    from ({DIRECT_EMAIL_ACTION_SQL}) as action
                    join ({EMAIL_CLASSIFICATION_SQL}) as classifications
                      on classifications.id=action.classification_id
                     and classifications.current_action_plan_id=action.action_plan_id
                    where action.status in ('pending','processing')
                    group by action.status
                    """
                )
            }
            # A failed action stays until someone deals with it, so it is
            # counted the way the list's "邮箱动作失败" filter counts it (mail
            # whose current plan has one), not only within the 24 hours.
            actions["failed"] = int(
                db.execute(
                    f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) as classifications "
                    f"where exists (select 1 from ({EMAIL_DIRECT_ACTION_SQL}) as failed_action "
                    "where failed_action.classification_id=classifications.id "
                    "and failed_action.action_plan_id="
                    "classifications.current_action_plan_id "
                    "and failed_action.status='failed')"
                ).fetchone()[0]
            )
            # Split the same failed mail by whether the worker will try it
            # again, so the bar can point at what still needs a person.
            actions["failed_retriable"] = int(
                db.execute(
                    f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) as classifications "
                    f"where exists (select 1 from ({EMAIL_DIRECT_ACTION_SQL}) as failed_action "
                    "where failed_action.classification_id=classifications.id "
                    "and failed_action.action_plan_id="
                    "classifications.current_action_plan_id "
                    "and failed_action.status='failed' and "
                    + _failed_action_retriable_sql("failed_action")
                    + ")"
                ).fetchone()[0]
            )
            actions["failed_not_retriable"] = actions["failed"] - actions["failed_retriable"]
            classification_queue = {
                str(row["status"]): int(row["n"])
                for row in db.execute(
                    f"select status, count(*) as n from ({_EMAIL_CLASSIFICATION_SQL}) "
                    "where status in ('pending','processing') group by status"
                )
            }
            has_tasks = db.execute(
                "select count(*) from sqlite_master where type='table' and name='tasks'"
            ).fetchone()[0]
            unsubscribe_queue = (
                {
                    str(row["status"]): int(row["n"])
                    for row in db.execute(
                        f"select status, count(*) as n from ({REPLY_TASK_SQL}) "
                        "where channel='email' and status in ('pending','processing') "
                        "group by status"
                    )
                }
                if has_tasks
                else {}
            )
            waiting = int(
                db.execute(
                    f"select count(*) from ({EMAIL_CLASSIFICATION_SQL}) "
                    "where status='pending_feedback'"
                ).fetchone()[0]
            )
            scans = [
                {
                    "account": row["display_name"] or row["account_id"],
                    "folder": row["folder"],
                    "last_seen_uid": row["last_seen_uid"],
                    "last_success_at": row["last_success_at"],
                    "last_error": row["last_error"],
                }
                for row in db.execute(
                    f"""
                    select cursors.account_id, cursors.folder,
                           cursors.last_seen_uid, cursors.last_success_at,
                           cursors.last_error, accounts.display_name
                    from ({state_projection('email_scan_cursors')}) as cursors
                    left join ({config_projection('email_accounts')}) as accounts
                      on accounts.account_id=cursors.account_id
                    order by cursors.account_id, cursors.folder
                    """
                )
            ]
        return {
            "window_hours": 24,
            "throughput": self._provider_action_throughput(now=now),
            "provider_actions": {
                key: actions.get(key, 0)
                for key in (
                    "done", "pending", "processing", "failed", "skipped",
                    "failed_retriable", "failed_not_retriable",
                )
            },
            "provider_action_backlog": {
                key: provider_action_backlog.get(key, 0)
                for key in ("pending", "processing")
            },
            "classification_queue": {
                key: classification_queue.get(key, 0)
                for key in ("pending", "processing")
            },
            "unsubscribe_queue": {
                key: unsubscribe_queue.get(key, 0) for key in ("pending", "processing")
            },
            "waiting_for_owner": waiting,
            "scans": scans,
        }

    def backfill_email_message_recipients(
        self,
        account_id: str,
        stable_message_identity: str,
        recipients: Sequence[str],
    ) -> bool:
        """Save the recipients of a message stored before its path saved any.

        Only an empty list is filled in; recipients already saved are never
        replaced. Returns whether the message was updated.
        """

        values = [str(value).strip() for value in recipients if str(value).strip()]
        if not values:
            return False
        with self._connect() as db:
            cursor = db.execute(
                "update emails set message_recipients_json=? "
                "where account_id=? and stable_message_identity=? "
                "and message_recipients_json='[]'",
                (_json_dump(values), account_id, stable_message_identity),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _completed_email_input_sql():
        """Existing completion predicates shared by the two original inputs."""
        return f"""class_status='processed'
            and not exists(select 1 from ({_EMAIL_CLASSIFICATION_SQL}) t
                where t.stable_message_identity=emails.stable_message_identity and t.status<>'done')
            and not exists(select 1 from ({REPLY_TASK_CURRENT_SQL}) t where t.channel='email'
                and json_extract(t.trigger_message_json,'$.classification_id')=emails.classification_id
                and t.status not in ('done','skipped'))
            and not exists(select 1 from actions a where
                (a.record_type='action' and a.action_classification_id=emails.classification_id
                  and a.action_operation in ('archive','flag_important','mark_read','move','trash','label')
                  and a.action_status not in ('done','skipped'))
                or (a.record_type in ('email_reply_dispatch_claims','email_unsubscribe_claims')
                  and a.email_classification_id=emails.classification_id
                  and a.email_status<>'done'))"""

    def compact_email_message_body(self, classification_id: int, *, source_reader) -> bool:
        """Retire one completed original with an exact source and no unfinished dependency."""
        from app.email_body_source import locate_original
        from app.email_preview import email_preview

        eligible = self._completed_email_input_sql() + " and json_type(message_source_reference_json,'$.kind') is null " \
            "and typeof(message_normalized_text)='text' and message_normalized_text<>''"
        with self._connect() as db:
            row = db.execute("select id,account_id,stable_message_identity,message_normalized_text,"
                "message_updated_at,class_updated_at,class_current_action_plan_id,message_source_reference_json from emails "
                "where classification_id=? and " + eligible, (classification_id,)).fetchone()
            if row is None:
                return False
            original = dict(row)
            attempts = db.execute(f"select a.runtime_kind,a.session_id,a.transcript_start,a.transcript_end "
                f"from runtime_attempts a join ({_EMAIL_CLASSIFICATION_SQL}) t "
                "on a.workload_kind='email_classification' and a.workload_key=t.task_id "
                "where t.stable_message_identity=? and a.status not in ('starting','running') order by a.id desc",
                (original["stable_message_identity"],)).fetchall()
        reference = locate_original(original["message_normalized_text"], attempts=attempts,
            read_message=lambda: source_reader.read(original["account_id"], original["stable_message_identity"]))
        if reference is None:
            return False
        reference = json.loads(original['message_source_reference_json'] or '{}') | reference
        with self._connect() as db:
            cursor = db.execute("update emails set message_normalized_text='',message_source_reference_json=?,message_preview=? "
                "where id=? and message_normalized_text=? and message_source_reference_json is ? and message_updated_at is ? "
                "and class_updated_at is ? and class_current_action_plan_id is ? and " + eligible,
                (_json_dump(reference), email_preview(original["message_normalized_text"]), original["id"], original["message_normalized_text"], original['message_source_reference_json'],
                 original["message_updated_at"], original["class_updated_at"], original["class_current_action_plan_id"]))
            return cursor.rowcount == 1

    def compact_email_model_input(self, classification_id: int, *, source_reader) -> bool:
        """Replace a completed exact model input with its native/provider proof."""
        from app.email_body_source import locate_original
        from app.email_classifier_model import CpuTfidfLogisticClassifier

        eligible = self._completed_email_input_sql() + " and typeof(class_model_text)='text' and class_model_text<>'' " \
            "and json_type(message_source_reference_json,'$.model_input') is null"
        with self._connect() as db:
            row = db.execute("select id,account_id,stable_message_identity,class_model_text,"
                "message_source_reference_json,message_updated_at,class_updated_at,"
                "class_current_action_plan_id,class_included_in_model_id from emails "
                "where classification_id=? and " + eligible, (classification_id,)).fetchone()
            if row is None:
                return False
            original = dict(row)
            attempts = db.execute(f"select a.runtime_kind,a.session_id,a.transcript_start,a.transcript_end "
                f"from runtime_attempts a join ({_EMAIL_CLASSIFICATION_SQL}) t "
                "on a.workload_kind='email_classification' and a.workload_key=t.task_id "
                "where t.stable_message_identity=? and a.status not in ('starting','running') order by a.id desc",
                (original["stable_message_identity"],)).fetchall()
        model_reference = locate_original(original['class_model_text'], attempts=attempts,
            read_message=lambda: source_reader.read(original['account_id'], original['stable_message_identity']),
            representations=(CpuTfidfLogisticClassifier.FEATURE_VERSION,))
        if model_reference is None:
            return False
        reference = json.loads(original['message_source_reference_json'] or '{}')
        reference['model_input'] = model_reference
        with self._connect() as db:
            cursor = db.execute("update emails set class_model_text='',message_source_reference_json=? "
                "where id=? and class_model_text=? and message_source_reference_json is ? "
                "and message_updated_at is ? and class_updated_at is ? "
                "and class_current_action_plan_id is ? and class_included_in_model_id is ? and " + eligible,
                (_json_dump(reference), original['id'], original['class_model_text'],
                 original['message_source_reference_json'], original['message_updated_at'],
                 original['class_updated_at'], original['class_current_action_plan_id'],
                 original['class_included_in_model_id']))
            if cursor.rowcount != 1:
                return False
            # The existing raw-field trigger invalidates inclusion on model_text
            # changes. These are identical proven bytes, so preserve its prior
            # inclusion in the same transaction, invisible to other readers.
            db.execute("update emails set class_included_in_model_id=? where id=?",
                (original['class_included_in_model_id'], original['id']))
            return True

    def compact_email_inputs(self, classification_id: int, *, source_reader) -> bool:
        """Retire both completed inputs using one request-local source read."""
        from types import SimpleNamespace

        messages = {}
        def read(account, identity):
            key = (account, identity)
            if key not in messages:
                messages[key] = source_reader.read(account, identity)
            return messages[key]
        reader = SimpleNamespace(read=read)
        body = self.compact_email_message_body(classification_id, source_reader=reader)
        model = self.compact_email_model_input(classification_id, source_reader=reader)
        return body or model

    def get_email_source_location(
        self, account_id: str, stable_message_identity: str,
    ) -> dict[str, object] | None:
        key = (account_id, stable_message_identity)
        return self.get_email_source_locations([key]).get(key)

    def get_email_source_locations(
        self, identities: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, object]]:
        """Only source coordinates/current folder facts; never original content."""
        if not identities:
            return {}
        with self._connect() as db:
            rows = db.execute(
                """with requested as (
                    select json_extract(value, '$[0]') as account_id,
                           json_extract(value, '$[1]') as stable_message_identity
                    from json_each(?)
                )
                select messages.account_id, messages.stable_message_identity,
                       messages.message_folder as folder, messages.uidvalidity, messages.uid,
                       messages.message_rfc_message_id as rfc_message_id,
                       messages.message_thread_identity as thread_identity,
                       messages.message_updated_at as updated_at, messages.message_source_reference_json,
                       observations.state, observations.provider_folder_name, observations.observed_at
                from requested
                join emails as messages
                  on messages.account_id=requested.account_id
                 and messages.stable_message_identity=requested.stable_message_identity
                left join provider_state as observations
                  on observations.record_type='observation'
                 and observations.account_id=messages.account_id
                 and observations.stable_message_identity=messages.stable_message_identity""",
                (_json_dump(list(identities)),),
            ).fetchall()
        return {
            (row["account_id"], row["stable_message_identity"]): dict(row)
            for row in rows
        }

    def get_stored_email_locator(
        self, classification_id: int
    ) -> StoredEmailLocator | None:
        """Where the mailbox currently holds this classified message."""

        _require_positive_int(classification_id, field="classification_id")
        with self._connect() as db:
            row = db.execute(
                f"""
                select account_id, folder, uidvalidity, uid, rfc_message_id,
                       thread_id, stable_message_identity
                from ({EMAIL_CLASSIFICATION_SQL}) where id=?
                """,
                (classification_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredEmailLocator(
            account_id=row["account_id"],
            folder=row["folder"],
            uidvalidity=row["uidvalidity"],
            uid=row["uid"],
            rfc_message_id=row["rfc_message_id"] or None,
            thread_id=row["thread_id"] or None,
            stable_message_identity=row["stable_message_identity"],
        )

    def record_provider_important_signals(
        self,
        account_id: str,
        stable_message_identity: str,
        signal_names: Sequence[str],
    ) -> bool:
        """Carry a just-verified Star/Flag change into the observed state.

        Only a message the scan already observed is updated; the next scan
        generation is what publishes the rest. Returns whether a row changed.
        """

        names = tuple(sorted(str(name) for name in signal_names))
        trusted = {"\\flagged", "$important"}
        with self._connect() as db:
            db.execute("begin immediate")
            return update_important_signals(
                db, account_id, stable_message_identity,
                int(any(name.casefold() in trusted for name in names)),
                _json_dump(list(names)),
            )

    def get_provider_classification_state(
        self, classification_id: int
    ) -> dict[str, object]:
        """Project current provider-folder truth without provider network I/O."""

        _require_positive_int(classification_id, field="classification_id")
        with self._connect() as db:
            classification = db.execute(
                "select account_id, stable_message_identity "
                f"from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (classification_id,),
            ).fetchone()
            if classification is None:
                return {"state": "unavailable", "reason": "classification_missing"}
            row = db.execute(
                """
                select observations.state, observations.category_key,
                       observations.important, observations.important_signals_json,
                       observations.provider_folder_id,
                       observations.provider_folder_name,
                       coalesce(generations.observed_at, observations.observed_at)
                           as observed_at
                from provider_state as observations
                left join provider_state as generations
                  on generations.record_type='folder_generation'
                 and generations.account_id=observations.account_id
                 and generations.provider_folder_id=observations.provider_folder_id
                where observations.record_type='observation'
                  and observations.account_id=?
                  and observations.stable_message_identity=?
                """,
                (
                    classification["account_id"],
                    classification["stable_message_identity"],
                ),
            ).fetchone()
        return self._provider_state_from_observation(row)

    def get_provider_classification_states(
        self, classification_ids: Sequence[int]
    ) -> dict[int, dict[str, object]]:
        """Project provider-folder truth for a page of emails in one connection.

        Opening a connection per row made a 50-row list spend more time
        connecting than reading.
        """

        for classification_id in classification_ids:
            _require_positive_int(classification_id, field="classification_id")
        states: dict[int, dict[str, object]] = {}
        with self._connect() as db:
            for classification_id in classification_ids:
                row = db.execute(
                    f"""
                    select observations.state, observations.category_key,
                           observations.important,
                           observations.important_signals_json,
                           observations.provider_folder_id,
                           observations.provider_folder_name,
                           coalesce(generations.observed_at, observations.observed_at)
                               as observed_at
                    from ({EMAIL_CLASSIFICATION_SQL}) as classifications
                    left join provider_state as observations
                      on observations.record_type='observation'
                     and observations.account_id=classifications.account_id
                     and observations.stable_message_identity=
                         classifications.stable_message_identity
                    left join provider_state as generations
                      on generations.record_type='folder_generation'
                     and generations.account_id=observations.account_id
                     and generations.provider_folder_id=observations.provider_folder_id
                    where classifications.id=?
                    """,
                    (classification_id,),
                ).fetchone()
                states[classification_id] = (
                    {"state": "unavailable", "reason": "classification_missing"}
                    if row is None
                    else self._provider_state_from_observation(
                        row if row["state"] is not None else None
                    )
                )
        return states

    @staticmethod
    def _provider_state_from_observation(
        row: sqlite3.Row | None,
    ) -> dict[str, object]:
        if row is None:
            return {"state": "unavailable", "reason": "provider_truth_not_observed"}
        if row["state"] != "available":
            return {
                "state": str(row["state"]),
                "reason": "provider_truth_" + str(row["state"]),
                "observed_at": row["observed_at"],
            }
        category_key = row["category_key"]
        state = (
            "junk"
            if category_key == "junk"
            else "categorized"
            if category_key is not None
            else "unclassified"
        )
        raw_signals: list[str] | None = None
        starred: bool | None = None
        important_flag: bool | None = None
        if row["important_signals_json"] is not None:
            try:
                decoded_signals = json.loads(row["important_signals_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise EmailPersistenceCorruption(
                    "provider important signals are not valid JSON"
                ) from exc
            if not isinstance(decoded_signals, list) or not all(
                isinstance(value, str) and value.strip() for value in decoded_signals
            ):
                raise EmailPersistenceCorruption(
                    "provider important signals have invalid shape"
                )
            raw_signals = decoded_signals
            normalized_signals = {value.casefold() for value in raw_signals}
            star_names = {r"\flagged", "starred"}
            important_flag_names = {
                "$important",
                r"\important",
                "important",
                "importance=high",
            }
            recognized_names = star_names | important_flag_names
            if not raw_signals or normalized_signals & recognized_names:
                starred = bool(star_names & normalized_signals)
                important_flag = bool(important_flag_names & normalized_signals)
        return {
            "state": state,
            "category_key": category_key,
            "important": bool(row["important"]),
            "important_signals": raw_signals,
            "starred": starred,
            "important_flag": important_flag,
            "provider_folder_id": row["provider_folder_id"],
            "provider_folder_name": row["provider_folder_name"],
            "observed_at": row["observed_at"],
        }

    def record_current_provider_observations(
        self,
        observations: Sequence[Mapping[str, object]],
        *,
        unavailable_folders: Sequence[str],
        authoritative_folders: Sequence[str] | None = None,
        active_account_ids: Sequence[str] | None = None,
        observed_at: str,
    ) -> None:
        """Publish one scan generation and reconcile its authoritative membership.

        ``observed_at`` is the durable generation marker. Only folders explicitly
        listed as authoritative may tombstone identities absent from this
        generation; unavailable or still-partial folders retain an unknown row.
        """

        if not isinstance(observed_at, str) or not observed_at.strip():
            raise ValueError("observed_at must be non-empty text")
        rows: list[tuple[object, ...]] = []
        for observation in observations:
            account_id = str(observation.get("account_id") or "").strip()
            identity = str(
                observation.get("stable_message_identity") or ""
            ).strip()
            folder_id = str(observation.get("provider_folder_id") or "").strip()
            folder_name = str(
                observation.get("provider_folder_name") or ""
            ).strip()
            if not account_id or not identity or not folder_id or not folder_name:
                raise ValueError("provider observation identity is incomplete")
            role_value = observation.get("folder_role")
            role = str(getattr(role_value, "value", role_value))
            binding_status = str(
                observation.get("folder_binding_status") or "unbound"
            )
            state = "excluded" if role in {"sent", "draft"} else "available"
            category_key = observation.get("bound_category_key")
            if role in {"spam", "trash"}:
                category_key = "junk"
            elif binding_status != "active":
                category_key = None
            signals = observation.get("important_signals")
            important_value = getattr(signals, "provider_important", None)
            raw_signal_names = getattr(signals, "raw_signal_names", ())
            if isinstance(signals, Mapping):
                important_value = signals.get("provider_important")
                raw_signal_names = signals.get("raw_signal_names", ())
            if type(important_value) is not bool:
                raise ValueError("provider important state is invalid")
            if not isinstance(raw_signal_names, (list, tuple)) or not all(
                isinstance(value, str) and value.strip() for value in raw_signal_names
            ):
                raise ValueError("provider important signals are invalid")
            rows.append(
                (
                    account_id,
                    identity,
                    state,
                    folder_id,
                    folder_name,
                    category_key,
                    int(important_value),
                    _json_dump(list(raw_signal_names)),
                    observed_at,
                )
            )
        unavailable = tuple(str(value) for value in unavailable_folders)
        authoritative = tuple(str(value) for value in (authoritative_folders or ()))
        active_accounts = (
            None
            if active_account_ids is None
            else tuple(str(value) for value in active_account_ids)
        )
        parsed_authoritative: list[tuple[str, str]] = []
        parsed_unavailable: list[tuple[str, str]] = []
        for values, target, label in (
            (authoritative, parsed_authoritative, "authoritative"),
            (unavailable, parsed_unavailable, "unavailable"),
        ):
            for value in values:
                account_id, separator, folder_id = value.partition(":")
                if not separator or not account_id or not folder_id:
                    raise ValueError(f"{label} folder identity is invalid")
                target.append((account_id, folder_id))
        current_membership = {
            (str(row[0]), str(row[3]), str(row[1])) for row in rows
        }
        folder_generations = {
            (str(row[0]), str(row[3])) for row in rows
        } | set(parsed_authoritative) | set(parsed_unavailable)
        with self._connect() as db:
            # Stage scan membership in SQLite's connection-local temp database
            # before acquiring the shared database writer lock. This also keeps
            # reconciliation independent of SQLite's host-parameter limit.
            db.execute(
                "create temp table current_provider_membership ("
                "account_id text not null, provider_folder_id text not null, "
                "stable_message_identity text not null, "
                "primary key(account_id, provider_folder_id, stable_message_identity))"
            )
            db.executemany(
                "insert or ignore into current_provider_membership values (?, ?, ?)",
                current_membership,
            )
            if active_accounts is not None:
                db.execute(
                    "create temp table current_provider_accounts ("
                    "account_id text primary key)"
                )
                db.executemany(
                    "insert or ignore into current_provider_accounts values (?)",
                    ((account_id,) for account_id in active_accounts),
                )
            db.commit()
            db.execute("begin immediate")
            upsert_observations(
                db, [dict(zip(FRESH_OBSERVATION_FIELDS, row, strict=True)) for row in rows]
            )
            for account_id, folder_id in parsed_authoritative:
                mark_authoritative_missing(db, account_id, folder_id, observed_at)
            if active_accounts is not None:
                mark_inactive_accounts(db, observed_at)
            for account_id, folder_id in parsed_unavailable:
                mark_unavailable_folder(db, account_id, folder_id, observed_at)
            upsert_folder_generations(
                db, [
                    {"account_id": account_id, "provider_folder_id": folder_id,
                     "observed_at": observed_at}
                    for account_id, folder_id in folder_generations
                ],
            )

    @staticmethod
    def _training_snapshot_from_dict(value: Mapping[str, object]):
        from app.email_training_snapshot import FolderTrainingSnapshot, TrainingSnapshotObservation

        return FolderTrainingSnapshot(
            snapshot_id=value["snapshot_id"],
            snapshot_version=value["snapshot_version"],
            description_version=value["description_version"],
            input_schema_version=value["input_schema_version"],
            seed=value["seed"],
            observed_at=value["observed_at"],
            observations=tuple(
                TrainingSnapshotObservation(**row) for row in value["observations"]
            ),
            snapshot_digest=value["snapshot_digest"],
            _manifest_json=json.dumps(value["manifest"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def _get_training_snapshot(
        self, db: sqlite3.Connection, snapshot_id: str
    ) -> dict[str, object] | None:
        from app.email_training_data import TrainingSnapshotUnavailable, pinned_digest, read_snapshot

        snapshot = db.execute(
            f"select * from ({config_projection('email_training_snapshots')}) where snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if snapshot is None:
            return None
        if snapshot["frozen"] != 1:
            raise EmailPersistenceCorruption("training snapshot is not frozen")
        latest = db.execute(
            f"select snapshot_id from ({config_projection('email_training_snapshots')}) where frozen=1 "
            "and snapshot_version=? "
            "and json_extract(manifest_json, '$.sample_count') is not null "
            "order by observed_at desc, snapshot_id desc limit 1",
            (snapshot["snapshot_version"],),
        ).fetchone()
        if latest is None or latest["snapshot_id"] != snapshot_id:
            pinned = (
                pinned_digest(self.path, self.training_run_id, snapshot_id)
                if self.training_run_id is not None else None
            )
            if pinned != snapshot["snapshot_digest"]:
                raise TrainingSnapshotUnavailable("historical training snapshot data is unavailable")
        restored = read_snapshot(self.path, str(snapshot["snapshot_digest"]))
        if restored.snapshot_id != snapshot_id:
            raise EmailPersistenceCorruption("external training snapshot identity mismatch")
        result = restored.to_dict()
        persisted_identity = {
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_version": snapshot["snapshot_version"],
            "description_version": snapshot["description_version"],
            "input_schema_version": snapshot["input_schema_version"],
            "seed": snapshot["seed"],
            "observed_at": snapshot["observed_at"],
            "snapshot_digest": snapshot["snapshot_digest"],
        }
        if any(
            result[field] != value for field, value in persisted_identity.items()
        ):
            raise EmailPersistenceCorruption(
                "external training snapshot does not match persisted identity"
            )
        result["folder_label_watermark"] = int(snapshot["folder_label_watermark"])
        result["important_label_watermark"] = int(snapshot["important_label_watermark"])
        return result







    def confirm_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
    ) -> dict[str, Any] | None:
        application = self._apply_human_classification(
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            allow_processed_correction=False,
            created_at=None,
        )
        return None if application is None else application.confirmed

    def apply_human_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        created_at: datetime | None = None,
    ) -> EmailFeedbackApplication | None:
        """Confirm or correct a classification under one durable write lease."""

        return self._apply_human_classification(
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            allow_processed_correction=True,
            created_at=created_at,
        )

    def correct_training_label(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        created_at: datetime | None = None,
    ) -> EmailFeedbackApplication | None:
        """Correct what a message is labelled as, without touching the mailbox.

        A console reclassification derives the category's move or trash from
        its folder binding, which is right when the owner is sorting mail. A
        training-data review is not: the mail may have been filed long ago, and
        fixing an inconsistent label must not move or delete it now. This
        records the same owner confirmation with an ActionPlan that has no
        actions.
        """

        return self._apply_human_classification(
            row_id,
            category,
            feedback_request_id=feedback_request_id,
            expected_current_action_plan_id=expected_current_action_plan_id,
            allow_processed_correction=True,
            created_at=created_at,
            label_only=True,
        )

    def _apply_human_classification(
        self,
        row_id: int,
        category: EmailCategoryKey,
        *,
        feedback_request_id: str,
        expected_current_action_plan_id: str | None,
        allow_processed_correction: bool,
        created_at: datetime | None,
        label_only: bool = False,
    ) -> EmailFeedbackApplication | None:
        feedback_request_id = _validate_feedback_request_id(feedback_request_id)
        expected_current_action_plan_id = _validate_expected_action_plan_id(
            expected_current_action_plan_id
        )
        category = validate_email_category_key(category)
        with self._connect() as db:
            db.execute("begin immediate")
            request = db.execute(
                f"select * from ({EMAIL_FEEDBACK_SQL}) where feedback_request_id=?",
                (feedback_request_id,),
            ).fetchone()
            if request is not None:
                same_intent = (
                    request["classification_id"] == row_id
                    and request["category"] == category
                    and request["expected_current_action_plan_id"]
                    == expected_current_action_plan_id
                )
                if not same_intent:
                    raise EmailClassificationConflict(
                        "feedback_request_id is already bound to another intent"
                    )
                return self._feedback_application(db, request, applied=False)
            row = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?", (row_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] == EmailClassificationStatus.PENDING_FEEDBACK.value:
                if expected_current_action_plan_id is not None:
                    raise EmailClassificationConflict(
                        "pending confirmation requires a null expected ActionPlan"
                    )
                action_plan_version = 1
            elif (
                row["status"] == EmailClassificationStatus.PROCESSED.value
                and allow_processed_correction
            ):
                if not expected_current_action_plan_id:
                    raise EmailClassificationConflict(
                        "processed correction requires the current ActionPlan"
                    )
                if row["current_action_plan_id"] != expected_current_action_plan_id:
                    raise EmailClassificationConflict(
                        "email classification current ActionPlan changed"
                    )
                current_plan = db.execute(
                    f"""
                    select action_plan_version
                    from ({EMAIL_PLAN_SQL})
                    where action_plan_id=? and classification_id=?
                    """,
                    (expected_current_action_plan_id, row_id),
                ).fetchone()
                if current_plan is None:
                    raise EmailActionPlanConflict(
                        "processed classification current ActionPlan is missing"
                    )
                action_plan_version = int(current_plan["action_plan_version"]) + 1
            else:
                raise EmailClassificationConflict(
                    "email classification is no longer pending feedback"
                )
            self._assert_no_email_reply_dispatch_in_flight(db, row_id)
            actions, action_parameters, config_version = self._category_action_snapshot(
                db,
                account_id=row["account_id"],
                category=category,
                fallback_config_version=row["config_version"],
            )
            if label_only:
                actions, action_parameters = (), {}
            plan_created_at = created_at or datetime.now(timezone.utc)
            action_plan = build_versioned_email_action_plan(
                action_plan_version=action_plan_version,
                classification_id=row["id"],
                account_id=row["account_id"],
                category=category,
                classification_source="user",
                confidence=row["confidence"],
                model_id=row["model_id"],
                config_version=config_version,
                actions=actions,
                action_parameters=action_parameters,
                created_at=plan_created_at,
                action_authorizations=build_user_confirmation_authorizations(
                    category=category,
                    actions=actions,
                    action_parameters=action_parameters,
                    model_id=row["model_id"],
                    config_version=config_version,
                ),
            )
            applied_at = self._now()
            self._persist_action_plan(db, action_plan, now=applied_at)
            updated_count = db.execute(
                """
                update emails
                set class_category=?, class_confirmed_category=?, class_status=?,
                    class_classification_source='user', class_config_version=?,
                    class_current_action_plan_id=?,
                    class_agent_result_json='null',
                    class_confirmed_at=?, class_updated_at=?
                where classification_id=?
                  and class_status=?
                  and class_current_action_plan_id is ?
                """,
                (
                    category,
                    category,
                    EmailClassificationStatus.PROCESSED.value,
                    config_version,
                    action_plan.action_plan_id,
                    applied_at,
                    applied_at,
                    row_id,
                    row["status"],
                    expected_current_action_plan_id,
                ),
            ).rowcount
            if updated_count != 1:
                raise EmailClassificationConflict(
                    "email classification was changed concurrently"
                )
            insert_email_action(
                db,
                "email_feedback_requests",
                {
                    "feedback_request_id": feedback_request_id,
                    "classification_id": row_id,
                    "category": category,
                    "expected_current_action_plan_id": expected_current_action_plan_id,
                    "resulting_action_plan_id": action_plan.action_plan_id,
                    "applied_at": applied_at,
                },
            )
            request = db.execute(
                f"select * from ({EMAIL_FEEDBACK_SQL}) where feedback_request_id=?",
                (feedback_request_id,),
            ).fetchone()
            assert request is not None
            return self._feedback_application(db, request, applied=True)

    @staticmethod
    def _category_action_snapshot(
        db: sqlite3.Connection,
        *,
        account_id: str,
        category: EmailCategoryKey,
        fallback_config_version: str,
    ) -> tuple[
        tuple[EmailAction, ...],
        dict[EmailAction, dict[str, object]],
        str,
    ]:
        selected_config = db.execute(
            f"""
            select actions_json, action_parameters_json, enabled, config_version
            from ({config_projection('email_category_configs')}) where category_key=?
            """,
            (category,),
        ).fetchone()
        if selected_config is None:
            return (), {}, fallback_config_version
        stored_actions = _json_load(
            selected_config["actions_json"],
            field="actions_json",
            expected_type=list,
        )
        stored_parameters = _json_load(
            selected_config["action_parameters_json"],
            field="action_parameters_json",
            expected_type=dict,
        )
        if not selected_config["enabled"]:
            return (), {}, selected_config["config_version"]
        if not stored_actions:
            if category == EmailCategory.JUNK.value:
                return (
                    (EmailAction.TRASH,),
                    {EmailAction.TRASH: {}},
                    selected_config["config_version"],
                )
            binding = db.execute(
                f"""
                select provider_folder_name
                from ({config_projection('email_category_folder_bindings')})
                where account_id=? and category_key=? and binding_status='active'
                """,
                (account_id, category),
            ).fetchone()
            if binding is not None:
                return (
                    (EmailAction.MOVE,),
                    {
                        EmailAction.MOVE: {
                            "target_folder": binding["provider_folder_name"]
                        }
                    },
                    selected_config["config_version"],
                )
        return (
            tuple(EmailAction(value) for value in stored_actions),
            {
                EmailAction(action): dict(parameters)
                for action, parameters in stored_parameters.items()
            },
            selected_config["config_version"],
        )

    def reapply_classification_to_copy(
        self,
        stable_message_identity: str,
        locator: EmailProviderLocator,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Point a settled classification at another copy of the same message.

        Mail is recognised by its Message-ID, so a second copy of a message
        already filed was treated as handled and left sitting unread in the
        inbox forever. The decision still holds - it is the same message - so
        this moves the classification onto the copy that is still there and
        gives it the category's own action. Returns None when there is nothing
        to do: no record, a copy that is already the one on file, or work still
        in flight on the previous copy.
        """

        plan_created_at = now or datetime.now(timezone.utc)
        if plan_created_at.tzinfo is None or plan_created_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        applied_at = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where stable_message_identity=?",
                (stable_message_identity,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["account_id"] != locator.account_id
                or row["status"] != EmailClassificationStatus.PROCESSED.value
            ):
                return None
            if (
                row["folder"] == locator.folder
                and int(row["uidvalidity"]) == int(locator.uidvalidity)
                and int(row["uid"]) == int(locator.uid)
            ):
                return None
            unfinished = db.execute(
                f"""
                select 1 from ({EMAIL_DIRECT_ACTION_SQL})
                where classification_id=? and status in ('pending', 'processing')
                """,
                (row["id"],),
            ).fetchone()
            if unfinished is not None:
                return None
            category = str(row["confirmed_category"] or row["category"] or "")
            if not category:
                return None
            actions, action_parameters, config_version = self._category_action_snapshot(
                db,
                account_id=str(row["account_id"]),
                category=category,
                fallback_config_version=str(row["config_version"]),
            )
            if not actions:
                return None
            current_plan = db.execute(
                f"""
                select action_plan_version from ({EMAIL_PLAN_SQL})
                where action_plan_id=? and classification_id=?
                """,
                (row["current_action_plan_id"], row["id"]),
            ).fetchone()
            action_plan = build_versioned_email_action_plan(
                action_plan_version=(
                    1 if current_plan is None else int(current_plan["action_plan_version"]) + 1
                ),
                classification_id=int(row["id"]),
                account_id=str(row["account_id"]),
                category=category,
                classification_source=str(row["classification_source"]),
                confidence=float(row["confidence"]),
                model_id=str(row["model_id"]),
                config_version=config_version,
                actions=actions,
                action_parameters=action_parameters,
                created_at=plan_created_at,
            )
            self._assert_no_email_reply_dispatch_in_flight(db, int(row["id"]))
            update_email_paired_locator(
                db, classification_id=int(row["id"]),
                account_id=str(row["account_id"]),
                stable_message_identity=stable_message_identity,
                class_folder=locator.folder, message_folder=locator.folder,
                uidvalidity=locator.uidvalidity, uid=locator.uid,
                class_thread_id=locator.thread_id,
                class_updated_at=applied_at,
            )
            self._persist_action_plan(db, action_plan, now=applied_at)
            update_email_classification(db, int(row["id"]), {
                "current_action_plan_id": action_plan.action_plan_id,
                "config_version": config_version,
                "updated_at": applied_at,
            })
            updated = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (int(row["id"]),),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [updated] if updated else [])
        return self._classification_row(updated, plan_rows)

    def append_action_plan_version(
        self,
        classification_id: int,
        action_plan: EmailActionPlan,
        *,
        confirmed_category: EmailCategoryKey,
        agent_result: object | None = None,
    ) -> dict[str, Any]:
        """Append a correction plan and atomically make it current."""

        confirmed_category_value = validate_email_category_key(confirmed_category)
        agent_result_json = _validated_agent_correction_result_json(
            action_plan,
            agent_result,
        )
        if action_plan.classification_id != classification_id:
            raise EmailActionPlanConflict("ActionPlan classification does not match")
        if action_plan.category != confirmed_category_value:
            raise EmailActionPlanConflict(
                "ActionPlan category does not match correction"
            )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (classification_id,),
            ).fetchone()
            if row is None:
                raise EmailActionPlanConflict(
                    f"unknown classification {classification_id}"
                )
            if row["account_id"] != action_plan.account_id:
                raise EmailActionPlanConflict("ActionPlan account does not match")
            self._assert_no_email_reply_dispatch_in_flight(db, classification_id)
            existing = db.execute(
                f"select action_plan_id from ({EMAIL_PLAN_SQL}) where action_plan_id=?",
                (action_plan.action_plan_id,),
            ).fetchone()
            if existing is not None and (
                row["current_action_plan_id"] != action_plan.action_plan_id
            ):
                raise EmailActionPlanConflict(
                    "historical ActionPlan cannot replace the current version"
                )
            now = self._now()
            self._persist_action_plan(db, action_plan, now=now)
            if row["current_action_plan_id"] != action_plan.action_plan_id:
                update_email_classification(db, classification_id, {
                    "category": action_plan.category,
                    "confirmed_category": confirmed_category_value,
                    "status": "processed",
                    "classification_source": action_plan.classification_source,
                    "confidence": action_plan.confidence,
                    "model_id": action_plan.model_id,
                    "config_version": action_plan.config_version,
                    "current_action_plan_id": action_plan.action_plan_id,
                        "agent_result_json": agent_result_json,
                    "confirmed_at": now,
                    "updated_at": now,
                })
            updated = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (classification_id,),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [updated] if updated else [])
        assert updated is not None
        return self._classification_row(updated, plan_rows)

    @staticmethod
    def _assert_no_email_reply_dispatch_in_flight(
        db: sqlite3.Connection,
        classification_id: int,
    ) -> None:
        if (
            db.execute(
                f"""
            select 1 from ({EMAIL_REPLY_CLAIM_SQL})
            where classification_id=? and status='dispatching'
            """,
                (classification_id,),
            ).fetchone()
            is not None
        ):
            raise EmailReplyDispatchConflict("email_reply_dispatch_in_flight")


    def append_action_attempt(
        self,
        *,
        action_id: str,
        attempt_number: int,
        status: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        error: str,
        started_at: str,
        finished_at: str,
    ) -> dict[str, Any]:
        _require_positive_int(attempt_number, field="attempt_number")
        if status not in _TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("attempt status must be done, skipped or failed")
        with self._connect() as db:
            db.execute("begin immediate")
            owner = db.execute("select * from (" + DIRECT_EMAIL_ACTION_SQL + ") where action_id=?", (action_id,)).fetchone()
            if owner is None:
                raise EmailActionAttemptConflict(f"unknown direct email action {action_id}")
            action_started_at = db.execute(
                "select action_started_at from actions where record_type='action' and id=?",
                (action_id,),
            ).fetchone()
            if action_started_at is None:
                raise EmailActionAttemptConflict(f"unknown direct email action {action_id}")
            receipt_id = shared_action_lifecycle.record_direct_email_action_outcome(
                db, action_id=action_id, expected_status=owner['status'],
                expected_started_at=action_started_at[0], expected_attempt_count=owner['attempt_count'],
                attempt_number=attempt_number, status=status,
                provider_operation=provider_operation, provider_target=provider_target,
                provider_result_id=provider_result_id, error=error,
                started_at=started_at, finished_at=finished_at,
                next_attempt_at=None, updated_at=self._now())
            if receipt_id is None:
                raise EmailActionAttemptConflict(f"direct action changed for {action_id}")
            row = db.execute(f"select * from ({DIRECT_EMAIL_ATTEMPT_SQL}) where id=?", (receipt_id,)).fetchone()
        assert row is not None
        return dict(row)

    def claim_next_direct_action(
        self,
        *,
        claimed_at: str,
        account_ids: Sequence[str] | None = None,
    ) -> StoredEmailAction | None:
        """Claim one retryable action from the current immutable ActionPlan."""

        claimed_at = _required_utc_timestamp(claimed_at, field="claimed_at")
        if account_ids is not None:
            account_ids = tuple(
                account_id.strip()
                for account_id in account_ids
                if isinstance(account_id, str) and account_id.strip()
            )
            if not account_ids:
                return None
        # A read probe is not a reservation; recheck all dependencies under the
        # writer lock before claiming. Idle polls must not join the writer queue.
        started_at = monotonic()
        preflight_seconds = 0.0
        lock_wait_seconds = 0.0
        locked_recheck_seconds = 0.0
        claim_mutation_seconds = 0.0
        outcome = "error"
        try:
            with self._connect() as db:
                phase_started_at = monotonic()
                try:
                    candidate = self._next_direct_action_row(db, claimed_at, account_ids)
                finally:
                    preflight_seconds += monotonic() - phase_started_at
                if candidate is None:
                    outcome = "no_action"
                    return None
            with claim_connection(self) as db:
                if db is None:
                    outcome = "no_action"
                    return None
                phase_started_at = monotonic()
                try:
                    db.execute("begin immediate")
                finally:
                    lock_wait_seconds += monotonic() - phase_started_at
                phase_started_at = monotonic()
                try:
                    row = self._next_direct_action_row(db, claimed_at, account_ids)
                finally:
                    locked_recheck_seconds += monotonic() - phase_started_at
                if row is None:
                    outcome = "raced"
                    return None
                phase_started_at = monotonic()
                try:
                    attempt_number = int(row["attempt_count"]) + 1
                    updated = shared_action_lifecycle.claim_direct_email_action(
                        db, action_id=row["action_id"], expected_status=row["status"],
                        expected_started_at=_stored_action_started_at(db, row["action_id"]),
                        attempt_count=row["attempt_count"], claimed_at=claimed_at)
                    if updated is None:
                        raise EmailActionAttemptConflict(
                            f"direct action claim changed for {row['action_id']}"
                        )
                    action = self._claimed_direct_action(
                        row,
                        attempt_number=attempt_number,
                        claimed_at=claimed_at,
                    )
                finally:
                    claim_mutation_seconds += monotonic() - phase_started_at
            outcome = "claimed"
            return action
        finally:
            elapsed = monotonic() - started_at
            if elapsed >= 1.0:
                caller = sys._getframe(1)
                logging.getLogger(__name__).warning(
                    "slow direct email action claim elapsed_seconds=%.3f "
                    "preflight_seconds=%.3f writer_lock_wait_seconds=%.3f "
                    "locked_recheck_seconds=%.3f claim_mutation_seconds=%.3f "
                    "outcome=%s caller=%s:%s:%s",
                    elapsed,
                    preflight_seconds,
                    lock_wait_seconds,
                    locked_recheck_seconds,
                    claim_mutation_seconds,
                    outcome,
                    Path(caller.f_code.co_filename).name,
                    caller.f_lineno,
                    caller.f_code.co_name,
                )
                del caller

    def _next_direct_action_row(
        self,
        db: sqlite3.Connection,
        claimed_at: str,
        account_ids: Sequence[str] | None,
    ) -> sqlite3.Row | None:
        eligible: list[sqlite3.Row] = []
        candidate_classifications = db.execute(
            f"""
            select distinct a.classification_id
            from ({DIRECT_EMAIL_ACTION_SQL}) as a
            cross join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
            where a.status in ('pending', 'failed')
              and c.status='processed'
              and c.current_action_plan_id=a.action_plan_id
            """
        ).fetchall()
        # One batched read per 500 classifications. The projection cannot push
        # a classification filter down to the action rows, so each lookup scans
        # every direct action; one lookup per candidate made a single claim cost
        # about 17 s with ~600 pending actions, and the locked recheck repeated
        # it while holding the SQLite writer lock.
        candidate_ids = [
            int(candidate["classification_id"])
            for candidate in candidate_classifications
        ]
        siblings_by_classification: dict[int, list[sqlite3.Row]] = {}
        for offset in range(0, len(candidate_ids), 500):
            batch = candidate_ids[offset : offset + 500]
            placeholders = ",".join("?" for _ in batch)
            for sibling in db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.current_action_plan_id, p.actions_json,
                       p.action_plan_version,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=a.action_id and r.attempt_number=a.attempt_count) as explicit_retry_attempt,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=a.action_id and r.attempt_number=a.attempt_count+1) as explicit_retry_scheduled
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                join ({EMAIL_PLAN_SQL}) as p on p.action_plan_id=a.action_plan_id
                where a.classification_id in ({placeholders})
                  and c.status='processed'
                """,
                batch,
            ).fetchall():
                siblings_by_classification.setdefault(
                    int(sibling["classification_id"]), []
                ).append(sibling)
        for classification_id in candidate_ids:
            siblings = siblings_by_classification.get(classification_id)
            if not siblings:
                continue
            if any(sibling["status"] == "processing" for sibling in siblings):
                continue
            if account_ids is not None and any(
                sibling["account_id"] not in account_ids for sibling in siblings
            ):
                continue
            current_siblings = [
                sibling
                for sibling in siblings
                if sibling["action_plan_id"] == sibling["current_action_plan_id"]
            ]
            unfinished = [
                sibling
                for sibling in current_siblings
                if (
                    sibling["status"] == "pending"
                    or _direct_action_failed_retryable(
                        sibling, claimed_at=claimed_at,
                        explicit_retry_attempt=bool(sibling["explicit_retry_attempt"]),
                        explicit_retry_scheduled=bool(sibling["explicit_retry_scheduled"]),
                    )
                )
                and self._direct_action_predecessors_done(db, sibling)
                and self._direct_action_dependency_satisfied(db, sibling)
                and self._direct_retry_configuration_matches(db, sibling)
            ]
            if unfinished:
                eligible.append(
                    min(
                        unfinished,
                        key=lambda sibling: (
                            _DIRECT_ACTION_PRIORITY[sibling["action_type"]],
                            sibling["action_id"],
                        ),
                    )
                )
        return min(
            eligible,
            key=lambda candidate: (
                candidate["updated_at"],
                candidate["classification_id"],
                candidate["action_id"],
            ),
            default=None,
        )

    def direct_action_ids_for_plan(self, action_plan_id: str) -> tuple[int, ...]:
        """Return only the direct action IDs owned by one immutable plan."""

        with self._connect() as db:
            rows = db.execute(
                f"select action_id, action_type from ({DIRECT_EMAIL_ACTION_SQL}) where action_plan_id=?",
                (action_plan_id,),
            ).fetchall()
        action_types = {row["action_type"] for row in rows}

        def plan_priority(row: sqlite3.Row) -> tuple[int, str]:
            action_type = row["action_type"]
            if EmailAction.MOVE.value in action_types:
                historical_order = {
                    EmailAction.MOVE.value: 0,
                    EmailAction.FLAG_IMPORTANT.value: 1,
                    EmailAction.MARK_READ.value: 2,
                }
                if action_type in historical_order:
                    return historical_order[action_type], row["action_id"]
            if EmailAction.TRASH.value in action_types:
                historical_junk_order = {
                    EmailAction.TRASH.value: 0,
                    EmailAction.MARK_READ.value: 1,
                }
                if action_type in historical_junk_order:
                    return historical_junk_order[action_type], row["action_id"]
            return _DIRECT_ACTION_PRIORITY[action_type] + 10, row["action_id"]

        return tuple(
            row["action_id"]
            for row in sorted(rows, key=plan_priority)
        )

    def direct_action_statuses_for_plan(self, action_plan_id: str) -> dict[int, str]:
        with self._connect() as db:
            rows = db.execute(
                f"select action_id, status from ({DIRECT_EMAIL_ACTION_SQL}) where action_plan_id=?",
                (action_plan_id,),
            ).fetchall()
        return {int(row["action_id"]): str(row["status"]) for row in rows}

    def direct_action_receipts_for_plan(
        self, action_plan_id: str
    ) -> tuple[dict[str, Any], ...]:
        """Return durable successful receipts for the plan's completed direct actions."""

        with self._connect() as db:
            rows = db.execute(
                f"""
                select a.action_id, a.action_plan_id, a.action_type, a.status,
                       t.provider_operation, t.provider_target,
                       t.provider_result_id, t.finished_at
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({DIRECT_EMAIL_ATTEMPT_SQL}) as t
                  on t.id=(
                    select max(latest.id)
                    from ({DIRECT_EMAIL_ATTEMPT_SQL}) as latest
                    where latest.action_id=a.action_id and latest.status='done'
                  )
                where a.action_plan_id=? and a.status='done'
                """,
                (action_plan_id,),
            ).fetchall()
        rows = sorted(
            rows,
            key=lambda row: _direct_action_id(
                str(row["action_plan_id"]), EmailAction(row["action_type"])
            ),
        )
        return tuple(
            {
                key: row[key]
                for key in (
                    "action_id",
                    "action_type",
                    "status",
                    "provider_operation",
                    "provider_target",
                    "provider_result_id",
                    "finished_at",
                )
            }
            for row in rows
        )

    def list_missing_unsubscribe_action_tasks(
        self, *, classification_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Find current durable unsubscribe plans without their stable task."""

        missing_ids: list[int] = []
        with self._connect() as db:
            identity_sql = f"""
                select c.id, c.account_id, c.stable_message_identity,
                       p.action_plan_version
                from ({EMAIL_CLASSIFICATION_SQL}) as c
                join ({EMAIL_PLAN_SQL}) as p
                  on p.action_plan_id=c.current_action_plan_id
                where c.status='processed'
                  and instr(p.actions_json, '"unsubscribe"') > 0
            """
            params: tuple[Any, ...] = ()
            if classification_id is not None:
                identity_sql += " and c.id=?"
                params = (classification_id,)
            identity_sql += " order by c.id"
            rows = db.execute(
                identity_sql,
                params,
            ).fetchall()
            for row in rows:
                action_identity = email_action_identity(
                    account_id=row["account_id"],
                    stable_message_identity=row["stable_message_identity"],
                    action_type=EmailAction.UNSUBSCRIBE,
                    action_plan_version=int(row["action_plan_version"]),
                )
                receipt = db.execute(
                    f"""
                    select 1 from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                    where action_identity=?
                    """,
                    (action_identity,),
                ).fetchone()
                if receipt is not None:
                    continue
                task = db.execute(
                    f"""
                    select 1 from ({REPLY_TASK_SQL})
                    where channel='email' and trigger_message_id=?
                    """,
                    (action_identity,),
                ).fetchone()
                if task is None:
                    missing_ids.append(int(row["id"]))
            hydrated_rows: list[sqlite3.Row] = []
            for offset in range(0, len(missing_ids), 500):
                batch = missing_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                hydrated = db.execute(
                    f"""
                    select c.*, p.action_plan_version, p.actions_json
                    from ({EMAIL_CLASSIFICATION_SQL}) as c
                    join ({EMAIL_PLAN_SQL}) as p
                      on p.action_plan_id=c.current_action_plan_id
                    where c.id in ({placeholders})
                    order by c.id
                    """,
                    batch,
                ).fetchall()
                hydrated_rows.extend(hydrated)
            plan_rows = self._current_action_plan_rows(db, hydrated_rows)
        return [self._classification_row(row, plan_rows) for row in hydrated_rows]

    def get_email_unsubscribe_cancellation(self, action_identity: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                f"select decision_json from ({EMAIL_UNSUBSCRIBE_CANCELLATION_SQL}) where action_identity=?",
                (action_identity,),
            ).fetchone()
        return None if row is None else _json_load(
            row["decision_json"], field="unsubscribe cancellation", expected_type=dict,
        )

    def cancel_failed_email_unsubscribe_task(
        self,
        *,
        task_id: int,
        expected_action_identity: str,
        expected_execution_generation: str,
        expected_input_version: int,
        reason: str,
        profile: object,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Record an explicit retention decision, never a provider opt-out receipt."""
        # The same profile lock serializes direct browser execution. Do not
        # wait for a live operation and then mistake its effect for cancellation.
        with profile.lock(timeout_seconds=0), self._connect() as db:
            db.execute("begin immediate")
            decision = self._unsubscribe_cancellation_decision(
                db,
                task_id=task_id,
                expected_action_identity=expected_action_identity,
                expected_execution_generation=expected_execution_generation,
                expected_input_version=expected_input_version,
                reason=reason,
            )
            if (
                not apply
                or db.execute(
                    f"select 1 from ({EMAIL_UNSUBSCRIBE_CANCELLATION_SQL}) where action_identity=?",
                    (expected_action_identity,),
                ).fetchone()
                is not None
            ):
                return decision
            insert_email_action(
                db,
                "email_unsubscribe_cancellations",
                {
                    "action_identity": expected_action_identity,
                    "decision_json": _json_dump(decision),
                },
            )
            changed = db.execute(
                "update tasks set status='skipped', "
                "error='email_unsubscribe_cancelled_by_principal', available_at='', "
                "locked_at=null, updated_at=? "
                "where id=? and kind='reply' and status='failed' "
                "and execution_generation=? and input_version=? and locked_at is null",
                (
                    decision["recorded_at"], task_id,
                    expected_execution_generation, expected_input_version,
                ),
            )
            if changed.rowcount != 1:
                raise EmailActionPlanConflict("unsubscribe cancellation task changed")
            return decision

    @classmethod
    def preview_failed_email_unsubscribe_task(cls, database: str | Path, **identity: Any) -> dict[str, Any]:
        """Read one existing schema without initializing stores or migrations."""
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma query_only=on")
            db.execute("begin")
            if cls._read_schema_version(db) != EMAIL_SCHEMA_VERSION:
                raise EmailPersistenceCorruption(f"cancellation requires deployed email schema {EMAIL_SCHEMA_VERSION}")
            cls._validate_schema_shape(db)
            return cls._unsubscribe_cancellation_decision(db, **identity)

    @classmethod
    def _unsubscribe_cancellation_decision(
        cls, db: sqlite3.Connection, *, task_id: int, expected_action_identity: str,
        expected_execution_generation: str, expected_input_version: int, reason: str,
    ) -> dict[str, Any]:
        from app.email_unsubscribe_audit import _task_payload, _validate_task_identity
        from app.store import AutoReplyStore

        _require_positive_int(task_id, field="task_id")
        _require_positive_int(expected_input_version, field="input_version")
        if not reason.strip() or not expected_action_identity or not expected_execution_generation:
            raise ValueError("explicit cancellation identity and reason are required")
        task = db.execute(f"select * from ({REPLY_TASK_CURRENT_SQL}) as tasks where id=?", (task_id,)).fetchone()
        if (task is None or task["channel"] != "email"
                or task["trigger_message_id"] != expected_action_identity
                or task["execution_generation"] != expected_execution_generation
                or task["input_version"] != expected_input_version):
            raise EmailActionPlanConflict("unsubscribe cancellation task identity changed")
        existing = db.execute(f"select decision_json from ({EMAIL_UNSUBSCRIBE_CANCELLATION_SQL}) where action_identity=?", (expected_action_identity,)).fetchone()
        if existing is not None:
            decision = _json_load(existing["decision_json"], field="unsubscribe cancellation", expected_type=dict)
            if (decision["task_id"] == task_id and task["status"] == "skipped"
                    and decision["execution_generation"] == expected_execution_generation
                    and decision["input_version"] == expected_input_version):
                return decision
            raise EmailActionPlanConflict("unsubscribe cancellation decision changed")
        if task["status"] != "failed" or task["locked_at"]:
            raise EmailActionPlanConflict("unsubscribe cancellation task is active or terminal")
        if db.execute(
            'select 1 from (' + state_projection('service_state') + ') where key=?',
            (f"email_unsubscribe_execution_pending:v1:{expected_action_identity}",),
        ).fetchone():
            raise EmailActionPlanConflict("unsubscribe execution requires reconciliation")
        typed_task = AutoReplyStore._reply_task_from_row(task)
        payload = _task_payload(typed_task)
        _validate_task_identity(typed_task, payload)
        source = db.execute(
            f"select 1 from ({EMAIL_CLASSIFICATION_SQL}) c join ({EMAIL_PLAN_SQL}) p "
            "on p.action_plan_id=c.current_action_plan_id where c.id=? and c.status='processed' "
            "and c.account_id=? and c.stable_message_identity=? "
            "and p.action_plan_id=? and p.action_plan_version=?",
            (payload["classification_id"], payload["account_id"],
             payload["stable_message_identity"], payload["action_plan_id"],
             payload["action_plan_version"]),
        ).fetchone()
        if source is None:
            raise EmailActionPlanConflict("unsubscribe cancellation source changed")
        if (db.execute(f'select 1 from ({AGENT_RUN_SQL}) as agent_runs where reply_task_id=?', (task_id,)).fetchone()
                or db.execute('select 1 from (' + state_projection('dispatcher_claim_leases') + ") where adapter_name in ('reply','scheduled_execution') and source_id=? and owner<>'' and coalesce(terminal_at,'')=''", (str(task_id),)).fetchone()
                or db.execute(f"select 1 from ({EMAIL_UNSUBSCRIBE_CLAIM_SQL}) where action_identity=?", (expected_action_identity,)).fetchone()
                or db.execute(f"select 1 from ({EMAIL_UNSUBSCRIBE_EFFECT_SQL}) where action_identity=?", (expected_action_identity,)).fetchone()
                or db.execute(f"select 1 from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL}) where action_identity=?", (expected_action_identity,)).fetchone()):
            raise EmailActionPlanConflict("unsubscribe cancellation has an active owner or effect")
        last_step = db.execute(
            f"select state from ({EMAIL_UNSUBSCRIBE_STEP_SQL}) where action_identity=? order by sequence desc limit 1",
            (expected_action_identity,),
        ).fetchone()
        if last_step is not None:
            raise EmailActionPlanConflict("unsubscribe cancellation requires effect reconciliation")
        return {
            "decision": "retain_subscription", "task_id": task_id,
            "action_identity": expected_action_identity,
            "execution_generation": expected_execution_generation,
            "input_version": expected_input_version, "reason": reason.strip(),
            "previous_error": task["error"], "recorded_at": cls._now(),
        }

    def list_terminal_unsubscribe_tasks_missing_receipts(self) -> list[dict[str, Any]]:
        """Find closed unsubscribe tasks whose durable receipt projection is absent.

        A `failed` task is not one of them. It ended on a technical fault, not
        on a finding about the link, and writing "no reliable entry" for it
        recorded a conclusion nobody had reached: on 2026-09-25 a task failed
        on a database validation error, and the receipt written for it said its
        link was unavailable, when the same link was in the message all along.
        """

        terminal: list[dict[str, Any]] = []
        with self._connect() as db:
            rows = db.execute(
                f"""
                select c.id, c.account_id, c.stable_message_identity,
                       p.action_plan_version
                from (
                    select classification_id as id, account_id,
                           stable_message_identity,
                           class_current_action_plan_id as current_action_plan_id,
                           class_status as status
                    from main.emails
                    where classification_id is not null
                ) as c
                join ({EMAIL_PLAN_SQL}) as p
                  on p.action_plan_id=c.current_action_plan_id
                where c.status='processed'
                  and instr(p.actions_json, '"unsubscribe"') > 0
                order by c.id
                """
            ).fetchall()
            for row in rows:
                action_identity = email_action_identity(
                    account_id=row["account_id"],
                    stable_message_identity=row["stable_message_identity"],
                    action_type=EmailAction.UNSUBSCRIBE,
                    action_plan_version=int(row["action_plan_version"]),
                )
                receipt = db.execute(
                    f"""
                    select 1 from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
                    where action_identity=?
                    """,
                    (action_identity,),
                ).fetchone()
                if receipt is not None:
                    continue
                cancellation = db.execute(
                    f"select 1 from ({EMAIL_UNSUBSCRIBE_CANCELLATION_SQL}) where action_identity=?",
                    (action_identity,),
                ).fetchone()
                if cancellation is not None:
                    continue
                task = db.execute(
                    f"""
                    select id, status, error, attempts, updated_at
                    from ({REPLY_TASK_SQL})
                    where channel='email' and trigger_message_id=?
                    """,
                    (action_identity,),
                ).fetchone()
                if task is None or task["status"] not in {
                    "done",
                    "skipped",
                    "needs_human",
                }:
                    continue
                classification = db.execute(
                    f"""
                    select c.*, p.action_plan_version, p.actions_json
                    from ({EMAIL_CLASSIFICATION_SQL}) as c
                    join ({EMAIL_PLAN_SQL}) as p
                      on p.action_plan_id=c.current_action_plan_id
                    where c.id=? and c.status='processed'
                      and p.action_plan_version=?
                    """,
                    (row["id"], row["action_plan_version"]),
                ).fetchone()
                if classification is None:
                    continue
                terminal.append(
                    {
                        "classification_row": classification,
                        "unsubscribe_action_identity": action_identity,
                        "unsubscribe_task": dict(task),
                    }
                )
            plan_rows = self._current_action_plan_rows(
                db, [item["classification_row"] for item in terminal]
            )
        return [
            {**self._classification_row(item["classification_row"], plan_rows),
             "unsubscribe_action_identity": item["unsubscribe_action_identity"],
             "unsubscribe_task": item["unsubscribe_task"]}
            for item in terminal
        ]

    def repair_current_action_plan(
        self, action_plan: EmailActionPlan
    ) -> dict[str, Any]:
        """Idempotently restore direct rows for one verified current plan."""

        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (action_plan.classification_id,),
            ).fetchone()
            if row is None or row["current_action_plan_id"] != action_plan.action_plan_id:
                raise EmailActionPlanConflict("model ActionPlan is not current")
            self._persist_action_plan(db, action_plan, now=self._now())
            updated = db.execute(
                f"select * from ({EMAIL_CLASSIFICATION_SQL}) where id=?",
                (action_plan.classification_id,),
            ).fetchone()
            plan_rows = self._current_action_plan_rows(db, [updated] if updated else [])
        assert updated is not None
        return self._classification_row(updated, plan_rows)

    def claim_direct_action(
        self, *, action_id: str, claimed_at: str
    ) -> StoredEmailAction | None:
        """Claim one exact current-plan action without selecting account work."""

        claimed_at = _required_utc_timestamp(claimed_at, field="claimed_at")
        if not isinstance(action_id, (str, int)) or not str(action_id).strip():
            raise ValueError("action_id must be nonblank")
        with claim_connection(self) as db:
            if db is None:
                return None
            db.execute("begin immediate")
            row = db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.current_action_plan_id, p.actions_json,
                       p.action_plan_version,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=a.action_id and r.attempt_number=a.attempt_count) as explicit_retry_attempt,
                       exists(select 1 from ({EMAIL_RETRY_SQL}) r where r.action_id=a.action_id and r.attempt_number=a.attempt_count+1) as explicit_retry_scheduled
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                join ({EMAIL_PLAN_SQL}) as p on p.action_plan_id=a.action_plan_id
                where a.action_id=? and c.status='processed'
                """,
                (action_id,),
            ).fetchone()
            if row is None or row["action_plan_id"] != row["current_action_plan_id"]:
                return None
            siblings = db.execute(
                f"select action_type, status from ({DIRECT_EMAIL_ACTION_SQL}) where action_plan_id=?",
                (row["action_plan_id"],),
            ).fetchall()
            if any(item["status"] == "processing" for item in siblings):
                return None
            retryable = row["status"] == "pending" or (
                _direct_action_failed_retryable(
                    row, claimed_at=claimed_at,
                    explicit_retry_attempt=bool(row["explicit_retry_attempt"]),
                    explicit_retry_scheduled=bool(row["explicit_retry_scheduled"]),
                )
            )
            if (
                not retryable
                or not self._direct_retry_configuration_matches(db, row)
                or not self._direct_action_predecessors_done(db, row)
                or not self._direct_action_dependency_satisfied(db, row)
            ):
                return None
            attempt_number = int(row["attempt_count"]) + 1
            updated = shared_action_lifecycle.claim_direct_email_action(
                db, action_id=row["action_id"], expected_status=row["status"],
                expected_started_at=_stored_action_started_at(db, row["action_id"]),
                attempt_count=row["attempt_count"], claimed_at=claimed_at)
            if updated is None:
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {row['action_id']}"
                )
            return self._claimed_direct_action(
                row, attempt_number=attempt_number, claimed_at=claimed_at
            )

    def list_failed_direct_actions(
        self,
        *,
        account_id: str,
        action_types: Sequence[EmailAction],
        limit: int,
        after_action_id: str = "",
        action_id: str | None = None,
    ) -> list[FailedDirectAction]:
        """List failed actions of the current plan, in `action_id` order.

        Read-only. Rows of a superseded plan are left out: they can no longer be
        completed, so reconciling them would change nothing the service reads.
        """

        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be nonblank")
        types = tuple(action.value for action in action_types)
        if not types or limit < 1:
            return []
        placeholders = ",".join("?" for _ in types)
        with self._connect() as db:
            rows = db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                where a.status='failed' and a.account_id=?
                  and a.action_type in ({placeholders})
                  and c.status='processed'
                  and a.action_plan_id=c.current_action_plan_id
                  and (?='' or a.action_id>?)
                  and (? is null or a.action_id=?)
                order by a.action_id
                limit ?
                """,
                (
                    account_id,
                    *types,
                    after_action_id,
                    after_action_id,
                    action_id,
                    action_id,
                    int(limit),
                ),
            ).fetchall()
        return [
            FailedDirectAction(
                action=self._claimed_direct_action(
                    row,
                    attempt_number=int(row["attempt_count"]) + 1,
                    claimed_at="",
                ),
                provider_operation=str(row["provider_operation"]),
                error=str(row["error"]),
                parameters_json=str(row["parameters_json"]),
            )
            for row in rows
        ]

    def release_source_verified_direct_action(
        self, failed: FailedDirectAction, *, source_locator: StoredEmailLocator,
        source_revision: str, requested_at: str, apply: bool,
        account_configuration_snapshot: str,
    ) -> bool:
        """Release one unchanged failure after exact read-only source verification."""
        requested_at = _required_utc_timestamp(requested_at, field="requested_at")
        expected = failed.action
        if (
            expected.action_type not in (EmailAction.MOVE, EmailAction.TRASH)
            or source_locator != expected.locator
            or not source_revision.strip() or not failed.parameters_json
        ):
            raise ValueError("exact move/trash source verification is required")
        with self._connect() as db:
            db.execute("begin immediate" if apply else "begin")
            account = db.execute(f"select * from ({config_projection('email_accounts')}) where account_id=?", (expected.account_id,)).fetchone()
            if account is None:
                return False
            current_configuration_snapshot = email_imap_configuration_snapshot(dict(account))
            if account_configuration_snapshot != current_configuration_snapshot:
                return False
            row = db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity, p.actions_json,
                       p.action_plan_version
                from ({DIRECT_EMAIL_ACTION_SQL}) a join ({EMAIL_CLASSIFICATION_SQL}) c
                  on c.id=a.classification_id
                join ({EMAIL_PLAN_SQL}) p on p.action_plan_id=a.action_plan_id
                where a.action_id=? and a.status='failed' and c.status='processed'
                  and a.action_plan_id=c.current_action_plan_id
                """, (expected.action_id,),
            ).fetchone()
            if row is None:
                return False
            actual = self._claimed_direct_action(
                row, attempt_number=int(row["attempt_count"]) + 1, claimed_at="",
            )
            if (
                actual != expected or row["parameters_json"] != failed.parameters_json
                or row["next_attempt_at"]
                or db.execute(
                    f"select 1 from ({EMAIL_RETRY_SQL}) where action_id=? and attempt_number=?",
                    (expected.action_id, expected.attempt_number),
                ).fetchone() is not None
                or row["error"] != failed.error
                or row["provider_operation"] != failed.provider_operation
                or db.execute(
                    f"select 1 from ({DIRECT_EMAIL_ACTION_SQL}) where action_plan_id=? and status='processing'",
                    (expected.action_plan_id,),
                ).fetchone() is not None
                or not self._direct_action_predecessors_done(db, row)
                or not self._direct_action_dependency_satisfied(db, row)
            ):
                return False
            if apply:
                proof = sha256(json.dumps({
                    "locator": asdict(source_locator), "revision": source_revision,
                    "plan": expected.action_plan_id, "config": expected.config_version,
                    "parameters": failed.parameters_json,
                }, sort_keys=True).encode("utf-8")).hexdigest()
                insert_action(
                    db,
                    "email_direct_retry_requests",
                    {
                        "action_id": expected.action_id,
                        "attempt_number": expected.attempt_number,
                        "source_readback_digest": proof,
                        "account_configuration_snapshot": current_configuration_snapshot,
                        "requested_at": requested_at,
                    },
                )
                if shared_action_lifecycle.schedule_direct_email_action(
                    db, action_id=expected.action_id, expected_status="failed",
                    next_attempt_at=requested_at, updated_at=requested_at) is None:
                    raise EmailActionAttemptConflict(f"direct action changed for {expected.action_id}")
            return True

    def claim_failed_direct_action_for_reconciliation(
        self,
        *,
        action_id: str,
        expected_attempt_count: int,
        claimed_at: str,
    ) -> StoredEmailAction | None:
        """Claim one failed action so a verified outcome can be recorded on it.

        This is not a retry: the caller has already seen, read-only, that the
        provider carried the action out, and completes the claim as `done`
        without another provider call. It skips the retry schedule and the
        predecessor checks, which gate *doing* an action, not recording one that
        already happened. Returns None if the row is no longer that same failed
        row of the current plan.
        """

        claimed_at = _required_utc_timestamp(claimed_at, field="claimed_at")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError("action_id must be nonblank")
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.current_action_plan_id
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                where a.action_id=? and c.status='processed'
                """,
                (action_id,),
            ).fetchone()
            if (
                row is None
                or row["action_plan_id"] != row["current_action_plan_id"]
                or row["status"] != "failed"
                or int(row["attempt_count"]) != expected_attempt_count
            ):
                return None
            busy = db.execute(
                f"select 1 from ({DIRECT_EMAIL_ACTION_SQL}) "
                "where action_plan_id=? and status='processing'",
                (row["action_plan_id"],),
            ).fetchone()
            if busy is not None:
                return None
            from app.action_entity_storage import get_action

            current_action = get_action(db, int(action_id))
            if current_action is None:
                return None
            updated = shared_action_lifecycle.claim_direct_email_action(
                db, action_id=action_id, expected_status="failed",
                expected_started_at=current_action["started_at"],
                attempt_count=expected_attempt_count,
                claimed_at=claimed_at)
            if updated is None:
                raise EmailActionAttemptConflict(f"direct action claim changed for {action_id}")
            return self._claimed_direct_action(
                row,
                attempt_number=expected_attempt_count + 1,
                claimed_at=claimed_at,
            )

    @staticmethod
    def _direct_action_predecessors_done(
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        """Require semantic and explicit provider-safe dependencies to be done."""

        actions = _json_load(
            row["actions_json"], field="actions_json", expected_type=list
        )
        current_action = str(row["action_type"])
        if current_action not in actions:
            raise EmailPersistenceCorruption(
                "direct action is absent from its ActionPlan"
            )
        siblings = db.execute(
            f"select action_type, status, parameters_json from ({DIRECT_EMAIL_ACTION_SQL}) "
            "where classification_id=? and action_plan_id=?",
            (row["classification_id"], row["action_plan_id"]),
        ).fetchall()
        status_by_type = {
            str(sibling["action_type"]): str(sibling["status"])
            for sibling in siblings
        }
        try:
            typed_actions = tuple(
                EmailAction(action)
                for action in actions
                if action in _DIRECT_ACTION_VALUES
            )
            parameters_by_action = {
                EmailAction(str(sibling["action_type"])): _json_load(
                    sibling["parameters_json"],
                    field="parameters_json",
                    expected_type=dict,
                )
                for sibling in siblings
            }
            dependency_graph = effective_direct_action_dependencies(
                typed_actions,
                parameters_by_action,
            )
            dependencies = dependency_graph[EmailAction(current_action)]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(str(exc)) from exc

        return all(
            status_by_type.get(action_type.value) == "done"
            for action_type in dependencies
        )

    @staticmethod
    def _direct_retry_configuration_matches(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
        request = db.execute(
            f"select account_configuration_snapshot from ({EMAIL_RETRY_SQL}) "
            "where action_id=? and attempt_number=?",
            (row["action_id"], int(row["attempt_count"]) + 1),
        ).fetchone()
        if request is None:
            return True
        account = db.execute(f"select * from ({config_projection('email_accounts')}) where account_id=?", (row["account_id"],)).fetchone()
        return account is not None and request[0] == email_imap_configuration_snapshot(dict(account))

    @staticmethod
    def _direct_action_dependency_satisfied(
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        """Require audited terminal evidence before a dependent junk Trash."""

        if row["action_type"] != EmailAction.TRASH.value:
            return True
        actions = _json_load(
            row["actions_json"], field="actions_json", expected_type=list
        )
        if EmailAction.UNSUBSCRIBE.value not in actions:
            return True
        action_identity = email_action_identity(
            account_id=row["account_id"],
            stable_message_identity=row["stable_message_identity"],
            action_type=EmailAction.UNSUBSCRIBE,
            action_plan_version=int(row["action_plan_version"]),
        )
        receipt = db.execute(
            f"""
            select outcome from ({EMAIL_UNSUBSCRIBE_RECEIPT_SQL})
            where action_identity=? and action_plan_id=? and classification_id=?
            """,
            (action_identity, row["action_plan_id"], row["classification_id"]),
        ).fetchone()
        return receipt is not None and receipt["outcome"] in _EMAIL_UNSUBSCRIBE_OUTCOMES

    @staticmethod
    def _claimed_direct_action(
        row: sqlite3.Row,
        *,
        attempt_number: int,
        claimed_at: str,
    ) -> StoredEmailAction:
        try:
            action_type = EmailAction(row["action_type"])
        except ValueError as exc:
            raise EmailPersistenceCorruption(
                f"invalid direct action type for {row['action_id']}"
            ) from exc
        if action_type.value not in _DIRECT_ACTION_VALUES:
            raise EmailPersistenceCorruption(
                f"non-direct action claimed for {row['action_id']}"
            )
        parameters = _json_load(
            row["parameters_json"],
            field="parameters_json",
            expected_type=dict,
        )
        parameters.pop(ACTION_DEPENDENCY_PARAMETER, None)
        return StoredEmailAction(
            action_id=row["action_id"],
            action_plan_id=row["action_plan_id"],
            classification_id=row["classification_id"],
            account_id=row["account_id"],
            action_type=action_type,
            parameters=parameters,
            config_version=row["config_version"],
            locator=StoredEmailLocator(
                account_id=row["account_id"],
                folder=row["folder"],
                uidvalidity=row["uidvalidity"],
                uid=row["uid"],
                rfc_message_id=row["rfc_message_id"] or None,
                thread_id=row["thread_id"] or None,
                stable_message_identity=row["stable_message_identity"],
            ),
            attempt_number=attempt_number,
            claim_started_at=claimed_at,
        )

    def complete_direct_action_attempt(
        self,
        action: StoredEmailAction,
        *,
        status: str,
        provider_operation: str,
        provider_target: str,
        provider_result_id: str,
        error: str,
        finished_at: str,
        retryable: bool = True,
        updated_locator: StoredEmailLocator | None = None,
    ) -> dict[str, Any]:
        """Append a terminal attempt and update its current projection atomically."""

        if status not in _TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("attempt status must be done, skipped or failed")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be a boolean")
        if not provider_operation:
            raise ValueError("provider_operation must be non-empty")
        if provider_target != action.locator.stable_message_identity:
            raise ValueError("provider_target must match the stable message identity")
        if status in {"done", "skipped"} and (not provider_result_id or error):
            raise ValueError(
                f"{status} attempts require readback receipt and no error"
            )
        if status == "failed" and not error:
            raise ValueError("failed attempts require an error")
        if updated_locator is not None:
            if status != "done":
                raise ValueError("only done attempts can update the provider locator")
            if (
                updated_locator.account_id != action.account_id
                or updated_locator.stable_message_identity
                != action.locator.stable_message_identity
                or not updated_locator.folder.strip()
                or updated_locator.uidvalidity <= 0
                or updated_locator.uid <= 0
            ):
                raise ValueError("updated provider locator does not match the action")
        finished_at = _required_utc_timestamp(finished_at, field="finished_at")
        next_attempt_at = _next_attempt_at(
            finished_at,
            attempt_number=action.attempt_number,
            retryable=retryable and status == "failed",
        )
        with self._connect() as db:
            db.execute("begin immediate")
            row = db.execute(
                f"""
                select a.*, c.folder, c.uidvalidity, c.uid, c.rfc_message_id,
                       c.thread_id, c.stable_message_identity,
                       c.status as classification_status,
                       c.current_action_plan_id
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                where a.action_id=?
                """,
                (action.action_id,),
            ).fetchone()
            if row is None:
                raise EmailActionAttemptConflict(
                    f"unknown direct email action {action.action_id}"
                )
            if db.execute(
                f"select 1 from ({EMAIL_RETRY_SQL}) where action_id=? and attempt_number=?",
                (action.action_id, action.attempt_number),
            ).fetchone() is not None:
                next_attempt_at = ""
            expected = self._claimed_direct_action(
                row,
                attempt_number=action.attempt_number,
                claimed_at=action.claim_started_at,
            )
            immutable_fields = (
                "action_id",
                "action_plan_id",
                "classification_id",
                "account_id",
                "action_type",
                "parameters",
                "config_version",
                "attempt_number",
            )
            if any(
                getattr(expected, field) != getattr(action, field)
                for field in immutable_fields
            ) or (
                expected.locator.stable_message_identity
                != action.locator.stable_message_identity
            ):
                raise EmailActionAttemptConflict(
                    f"immutable direct action changed for {action.action_id}"
                )
            if (
                row["status"] != "processing"
                or row["started_at"] != action.claim_started_at
                or int(row["attempt_count"]) != action.attempt_number - 1
            ):
                raise EmailActionAttemptConflict(
                    f"direct action claim changed for {action.action_id}"
                )
            if (
                row["classification_status"] != "processed"
                or row["current_action_plan_id"] != action.action_plan_id
            ):
                raise EmailActionAttemptConflict(
                    f"direct action is no longer current for {action.action_id}"
                )
            if updated_locator is not None:
                paired_updated = update_email_paired_locator(
                    db, classification_id=action.classification_id,
                    account_id=action.account_id,
                    stable_message_identity=action.locator.stable_message_identity,
                    class_folder=updated_locator.folder,
                    message_folder=updated_locator.folder,
                    uidvalidity=updated_locator.uidvalidity,
                    uid=updated_locator.uid,
                    class_updated_at=finished_at,
                    message_updated_at=finished_at,
                    expected_class_folder=action.locator.folder,
                    expected_message_folder=action.locator.folder,
                    expected_uidvalidity=action.locator.uidvalidity,
                    expected_uid=action.locator.uid,
                )
                if paired_updated != 1:
                    raise EmailActionAttemptConflict(
                        f"provider locator changed for {action.action_id}"
                    )
            receipt_id = shared_action_lifecycle.record_direct_email_action_outcome(
                db, action_id=action.action_id, expected_status='processing',
                expected_started_at=action.claim_started_at,
                expected_attempt_count=action.attempt_number - 1,
                attempt_number=action.attempt_number, status=status,
                provider_operation=provider_operation, provider_target=provider_target,
                provider_result_id=provider_result_id, error=error,
                started_at=action.claim_started_at, finished_at=finished_at,
                next_attempt_at=next_attempt_at, updated_at=finished_at)
            if receipt_id is None:
                raise EmailActionAttemptConflict(f"direct action claim changed for {action.action_id}")
            attempt = db.execute(
                f"select * from ({DIRECT_EMAIL_ATTEMPT_SQL}) where id=?",
                (receipt_id,),
            ).fetchone()
        assert attempt is not None
        return dict(attempt)

    def recover_stale_processing_actions(
        self,
        *,
        stale_before: str,
        recovered_at: str,
    ) -> int:
        """Record interrupted claims as failed so current actions can be retried."""

        stale_before = _required_utc_timestamp(
            stale_before,
            field="stale_before",
        )
        recovered_at = _required_utc_timestamp(
            recovered_at,
            field="recovered_at",
        )
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                f"""
                select a.*, c.stable_message_identity
                from ({DIRECT_EMAIL_ACTION_SQL}) as a
                join ({EMAIL_CLASSIFICATION_SQL}) as c on c.id=a.classification_id
                where a.status='processing' and a.started_at < ?
                order by a.action_id
                """,
                (stale_before,),
            ).fetchall()
            for row in rows:
                attempt_number = int(row["attempt_count"]) + 1
                explicit_retry = db.execute(
                    f"select 1 from ({EMAIL_RETRY_SQL}) where action_id=? and attempt_number=?",
                    (row["action_id"], attempt_number),
                ).fetchone() is not None
                outcome = shared_action_lifecycle.record_direct_email_action_outcome(
                    db, action_id=row['action_id'], expected_status='processing',
                    expected_started_at=row['started_at'],
                    expected_attempt_count=row['attempt_count'], attempt_number=attempt_number,
                    status='failed', provider_operation='startup_recovery',
                    provider_target=row['stable_message_identity'], provider_result_id='',
                    error='stale_processing_recovered', started_at=row['started_at'],
                    finished_at=recovered_at,
                    next_attempt_at=_next_attempt_at(recovered_at, attempt_number=attempt_number,
                                                     retryable=not explicit_retry),
                    updated_at=recovered_at)
                if outcome is None:
                    raise EmailActionAttemptConflict(f"direct action claim changed for {row['action_id']}")
            return len(rows)

    def list_flagged_important_between(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Mail the service flagged important in the mailbox within [start, end)."""
        with self._connect() as db:
            rows = db.execute(
                f"""
                select classifications.id as classification_id, actions.action_id,
                       classifications.sender, classifications.subject,
                       classifications.preview, classifications.category,
                       classifications.received_at, actions.finished_at
                from ({DIRECT_EMAIL_ACTION_SQL}) as actions
                join ({EMAIL_CLASSIFICATION_SQL}) as classifications
                  on classifications.id=actions.classification_id
                where actions.action_type='flag_important'
                  and actions.status='done'
                  and actions.created_at>=? and actions.created_at<?
                order by actions.created_at, actions.action_id
                """,
                (
                    start.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    end.astimezone(timezone.utc).isoformat(timespec="seconds"),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_action_attempts(self, action_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                f"""
                select * from ({DIRECT_EMAIL_ATTEMPT_SQL})
                where action_id=? order by attempt_number
                """,
                (action_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_category_configs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                f"select * from ({config_projection('email_category_configs')}) order by category_key"
            ).fetchall()
        return [self._category_config_row(row) for row in rows]

    def get_category_config(self, category_key: str) -> dict[str, Any] | None:
        category_key = validate_email_category_key(category_key)
        with self._connect() as db:
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
        return None if row is None else self._category_config_row(row)

    def create_category_with_bindings(
        self,
        *,
        category_key: str,
        display_name: str,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        actions: tuple[EmailAction, ...],
        action_parameters: Mapping[EmailAction, Mapping[str, object]],
        enabled: bool,
        description_version: str,
        config_version: str,
        bindings: Sequence[VerifiedEmailFolderBinding],
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        if category_key == MODEL_OTHERS_CATEGORY_KEY:
            raise ValueError(
                "others is the model's out-of-scope class, not a category"
            )
        include_values, exclude_values = validate_category_descriptions(
            display_name=display_name,
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        _validate_config(
            category=category_key,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        if any(
            not isinstance(binding, VerifiedEmailFolderBinding) for binding in bindings
        ):
            raise TypeError(
                "bindings must contain coordinator-produced verified folder binding values"
            )
        if category_key == "junk" and bindings:
            raise ValueError("junk does not accept a business-folder binding")
        for binding in bindings:
            _validate_verified_folder_binding_role(category_key, binding)
        account_ids = [binding.account_id for binding in bindings]
        if len(account_ids) != len(set(account_ids)):
            raise EmailFolderBindingConflict("duplicate category/account binding")
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            enabled_accounts = {
                row["account_id"]
                for row in db.execute(
                    f"select account_id from ({config_projection('email_accounts')}) where enabled=1"
                )
            }
            existing_accounts = {
                row["account_id"]
                for row in db.execute(f"select account_id from ({config_projection('email_accounts')})")
            }
            if not set(account_ids) <= existing_accounts:
                raise EmailFolderBindingConflict("folder binding account is unknown")
            active_accounts = {
                binding.account_id
                for binding in bindings
                if binding.binding_status == "active"
            }
            final_enabled = bool(enabled and enabled_accounts <= active_accounts)
            try:
                insert_config(db, "email_category_configs", {
                    "category_key": category_key, "display_name": display_name,
                    "core_description": core_description,
                    "include_json": _json_dump(list(include_values)),
                    "exclude_json": _json_dump(list(exclude_values)),
                    "threshold": threshold,
                    "actions_json": _json_dump([action.value for action in actions]),
                    "action_parameters_json": _json_dump({
                        action.value: dict(parameters)
                        for action, parameters in action_parameters.items()
                    }),
                    "enabled": int(final_enabled),
                    "description_version": description_version,
                    "config_version": config_version, "updated_at": now,
                })
                for binding in bindings:
                    insert_config(db, "email_category_folder_bindings", {
                        "account_id": binding.account_id,
                        "category_key": category_key,
                        "provider_folder_id": binding.provider_folder_id,
                        "provider_folder_name": binding.provider_folder_name,
                        "binding_status": binding.binding_status,
                        "last_verified_at": binding.last_verified_at,
                    })
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email category or folder binding conflicts with stored state"
                ) from exc
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            self._append_category_description_revision(db, row, current=True)
        assert row is not None
        return self._category_config_row(row)

    @staticmethod
    def _bindings_cover_enabled_accounts(
        db: sqlite3.Connection,
        category_key: str,
    ) -> bool:
        return (
            db.execute(
                f"""
                select not exists (
                    select 1 from ({config_projection('email_accounts')}) as accounts
                    where accounts.enabled=1
                      and not exists (
                          select 1 from ({config_projection('email_category_folder_bindings')}) as bindings
                          where bindings.account_id=accounts.account_id
                            and bindings.category_key=?
                            and bindings.binding_status='active'
                      )
                )
                """,
                (category_key,),
            ).fetchone()[0]
            == 1
        )

    @staticmethod
    def _validate_category_revision_snapshot(config: dict[str, Any]) -> None:
        json_fields = {
            "include": "include_json",
            "exclude": "exclude_json",
            "actions": "actions_json",
            "action_parameters": "action_parameters_json",
        }
        required = (
            set(CATEGORY_CONFIG_COLUMN_CONTRACTS) - set(json_fields.values())
        ) | set(json_fields)
        if set(config) != required:
            raise ValueError(
                "category revision snapshot has incomplete or unexpected fields"
            )
        validate_email_category_key(config["category_key"])
        if type(config["enabled"]) is not bool:
            raise ValueError("category revision enabled must be boolean")
        threshold = config["threshold"]
        if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
            raise ValueError("category revision threshold must be numeric in range")
        for field in ("config_version", "updated_at"):
            if type(config[field]) is not str or not config[field].strip():
                raise ValueError(f"category revision {field} must be nonblank text")
        if (
            type(config["actions"]) is not list
            or type(config["action_parameters"]) is not dict
        ):
            raise ValueError(
                "category revision actions must be a list and parameters an object"
            )
        for action in config["actions"]:
            if type(action) is not str:
                raise ValueError("category revision action must be text")
            EmailAction(action)
        for action, parameters in config["action_parameters"].items():
            EmailAction(action)
            if type(parameters) is not dict:
                raise ValueError("category revision action parameters must be objects")
        # Reconstitute the stored category row and reuse its canonical description
        # and JSON validators, rather than introducing another snapshot format.
        row = {key: value for key, value in config.items() if key not in json_fields}
        row.update(
            {column: _json_dump(config[key]) for key, column in json_fields.items()}
        )
        category_config_row(row)

    def _append_category_description_revision(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        previous_row: sqlite3.Row | None = None,
        expected_current_version: str | None = None,
        current: bool = False,
    ) -> None:
        config = self._category_config_row(row)
        if (
            previous_row is not None
            and previous_row["config_version"] == config["config_version"]
        ):
            previous = self._category_config_row(previous_row)
            if expected_current_version is not None or any(
                previous[field] != config[field]
                for field in config
                if field != "updated_at"
            ):
                raise EmailFolderBindingConflict(
                    "config_version must be new and never used"
                )
            if current:
                update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", config["category_key"]), {
                    "updated_at": previous["updated_at"],
                })
            else:
                db.execute(
                    "update email_category_configs set updated_at=? where category_key=?",
                    (previous["updated_at"], config["category_key"]),
                )
            return
        # A description version identifies semantic content within one category;
        # configuration-only changes may append another snapshot of that content.
        revision_source = (
            f"({config_projection('email_category_description_revisions')})"
            if current else "email_category_description_revisions"
        )
        for revision in db.execute(
            f"select config_json from {revision_source} where category_key=?",
            (config["category_key"],),
        ):
            previous = _json_load(
                revision["config_json"], field="config_json", expected_type=dict
            )
            if previous["config_version"] == config["config_version"]:
                raise EmailFolderBindingConflict(
                    "config_version must be new and never used"
                )
            if previous["description_version"] == config["description_version"] and any(
                previous[field] != config[field]
                for field in ("display_name", "core_description", "include", "exclude")
            ):
                raise EmailFolderBindingConflict(
                    "description_version already identifies different category content"
                )
        created_at = self._next_model_control_timestamp(db, revision_source)
        values = {
            "revision_id": str(uuid4()),
            "category_key": config["category_key"],
            "config_version": config["config_version"],
            "config_json": _json_dump(config),
            "created_at": created_at,
        }
        if current:
            insert_config(db, "email_category_description_revisions", values)
        else:
            db.execute(
                "insert into email_category_description_revisions values (?, ?, ?, ?, ?)",
                tuple(values.values()),
            )

    def list_category_description_revisions(
        self, category_key: str
    ) -> list[dict[str, Any]]:
        category_key = validate_email_category_key(category_key)
        with self._connect() as db:
            return [
                {
                    key: row[key]
                    for key in (
                        "revision_id",
                        "category_key",
                        "config_version",
                        "created_at",
                    )
                }
                | {
                    "config": _json_load(
                        row["config_json"], field="config_json", expected_type=dict
                    )
                }
                for row in db.execute(
                    f"select * from ({config_projection('email_category_description_revisions')}) where category_key=? order by created_at desc",
                    (category_key,),
                )
            ]

    def update_category_descriptions(
        self,
        category_key: str,
        *,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        enabled: bool,
        description_version: str,
        config_version: str,
        expected_current_version: str | None = None,
    ) -> dict[str, Any] | None:
        category_key = validate_email_category_key(category_key)
        include_values, exclude_values = validate_category_descriptions(
            display_name="unchanged",
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ValueError("threshold must be numeric")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ValueError("threshold must be between zero and one")
        if type(config_version) is not str or not config_version.strip():
            raise ValueError("config_version must be non-empty")
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                return None
            if (
                expected_current_version is not None
                and existing["config_version"] != expected_current_version
            ):
                raise EmailFolderBindingConflict("category config changed")
            final_enabled = bool(
                enabled and self._bindings_cover_enabled_accounts(db, category_key)
            )
            update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", category_key), {
                "core_description": core_description,
                "include_json": _json_dump(list(include_values)),
                "exclude_json": _json_dump(list(exclude_values)),
                "threshold": float(threshold), "enabled": int(final_enabled),
                "description_version": description_version,
                "config_version": config_version, "updated_at": self._now(),
            })
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            self._append_category_description_revision(
                db, row, previous_row=existing,
                expected_current_version=expected_current_version,
                current=True,
            )
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?", (category_key,)
            ).fetchone()
        assert row is not None
        return self._category_config_row(row)

    def refresh_category_with_bindings(
        self,
        category_key: str,
        *,
        core_description: str,
        include: Sequence[str],
        exclude: Sequence[str],
        threshold: float,
        enabled: bool,
        description_version: str,
        config_version: str,
        bindings: Sequence[VerifiedEmailFolderBinding],
        expected_current_version: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically replace enabled-account bindings and category configuration."""

        category_key = validate_email_category_key(category_key)
        include_values, exclude_values = validate_category_descriptions(
            display_name="unchanged",
            core_description=core_description,
            include=include,
            exclude=exclude,
            description_version=description_version,
        )
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise ValueError("threshold must be numeric")
        if not 0.0 <= float(threshold) <= 1.0:
            raise ValueError("threshold must be between zero and one")
        if type(config_version) is not str or not config_version.strip():
            raise ValueError("config_version must be non-empty")
        if isinstance(bindings, (str, bytes)) or not isinstance(bindings, Sequence):
            raise TypeError("bindings must be a sequence")
        if any(type(binding) is not VerifiedEmailFolderBinding for binding in bindings):
            raise TypeError("bindings must contain verified folder binding values")
        account_ids = tuple(binding.account_id for binding in bindings)
        if len(account_ids) != len(set(account_ids)):
            raise EmailFolderBindingConflict("duplicate category/account binding")
        for binding in bindings:
            _validate_verified_folder_binding_role(category_key, binding)
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                return None
            if (
                expected_current_version is not None
                and existing["config_version"] != expected_current_version
            ):
                raise EmailFolderBindingConflict("category config changed")
            enabled_accounts = {
                row["account_id"]
                for row in db.execute(
                    f"select account_id from ({config_projection('email_accounts')}) where enabled=1"
                )
            }
            if set(account_ids) != enabled_accounts:
                raise EmailFolderBindingConflict(
                    "folder bindings must cover exactly the enabled accounts"
                )
            try:
                for binding in bindings:
                    _upsert_current_folder_binding(db, category_key, binding)
                final_enabled = bool(
                    enabled and self._bindings_cover_enabled_accounts(db, category_key)
                )
                update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", category_key), {
                    "core_description": core_description,
                    "include_json": _json_dump(list(include_values)),
                    "exclude_json": _json_dump(list(exclude_values)),
                    "threshold": float(threshold), "enabled": int(final_enabled),
                    "description_version": description_version,
                    "config_version": config_version, "updated_at": now,
                })
                self._validate_category_binding_completeness(db)
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email category or folder binding conflicts with stored state"
                ) from exc
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            self._append_category_description_revision(
                db, row, previous_row=existing,
                expected_current_version=expected_current_version,
                current=True,
            )
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?", (category_key,)
            ).fetchone()
        assert row is not None
        return self._category_config_row(row)

    def set_folder_binding_status(
        self,
        *,
        account_id: str,
        category_key: str,
        provider_folder_id: str,
        provider_folder_name: str,
        binding_status: str,
        last_verified_at: str,
        provider_folder_role: FolderRole,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        binding = VerifiedEmailFolderBinding(
            account_id=account_id,
            provider_folder_id=provider_folder_id,
            provider_folder_name=provider_folder_name,
            binding_status=binding_status,
            last_verified_at=last_verified_at,
            provider_folder_role=provider_folder_role,
        )
        _validate_verified_folder_binding_role(category_key, binding)
        with self._connect() as db:
            db.execute("begin immediate")
            try:
                identity = _folder_binding_rowid(db, binding.account_id, category_key)
                updated = update_config(db, "email_category_folder_bindings", identity, {
                    "provider_folder_id": binding.provider_folder_id,
                    "provider_folder_name": binding.provider_folder_name,
                    "binding_status": binding.binding_status,
                    "last_verified_at": binding.last_verified_at,
                }) if identity is not None else 0
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email folder binding conflicts with stored state"
                ) from exc
            if updated != 1:
                raise EmailFolderBindingConflict("email folder binding does not exist")
            if binding.binding_status != "active":
                update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", category_key), {
                    "enabled": 0, "updated_at": self._now(),
                })
            row = db.execute(
                f"select * from ({config_projection('email_category_folder_bindings')}) "
                "where account_id=? and category_key=?",
                (binding.account_id, category_key),
            ).fetchone()
        assert row is not None
        return dict(row)

    def upsert_verified_folder_binding(
        self,
        category_key: str,
        binding: VerifiedEmailFolderBinding,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category_key)
        if type(binding) is not VerifiedEmailFolderBinding:
            raise TypeError("binding must be a VerifiedEmailFolderBinding")
        _validate_verified_folder_binding_role(category_key, binding)
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            try:
                _upsert_current_folder_binding(db, category_key, binding)
            except sqlite3.IntegrityError as exc:
                raise EmailFolderBindingConflict(
                    "email folder binding conflicts with stored state"
                ) from exc
            complete = self._bindings_cover_enabled_accounts(db, category_key)
            if not complete:
                category = db.execute(
                    f"select enabled from ({config_projection('email_category_configs')}) "
                    "where category_key=?", (category_key,),
                ).fetchone()
                if category is not None and category["enabled"] == 1:
                    update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", category_key), {
                        "enabled": 0, "updated_at": now,
                    })
            row = db.execute(
                f"select * from ({config_projection('email_category_folder_bindings')}) "
                "where account_id=? and category_key=?",
                (binding.account_id, category_key),
            ).fetchone()
            self._validate_category_binding_completeness(db)
        assert row is not None
        return dict(row)

    def list_account_folder_bindings(
        self,
        category_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            if category_key is None:
                rows = db.execute(
                    f"select * from ({config_projection('email_category_folder_bindings')}) "
                    "order by account_id, category_key"
                ).fetchall()
            else:
                validated_key = validate_email_category_key(category_key)
                rows = db.execute(
                    f"select * from ({config_projection('email_category_folder_bindings')}) "
                    "where category_key=? order by account_id",
                    (validated_key,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_configs(self) -> list[dict[str, Any]]:
        """Return the established worker-facing shape during its later migration."""

        rows = self.list_category_configs()
        configured = [
            row for row in rows if row["config_version"] != SEEDED_CONFIG_VERSION
        ]
        return [self._legacy_config_row(row) for row in configured or rows]

    def upsert_config(
        self,
        *,
        category: EmailCategory,
        description: str,
        threshold: float,
        actions: tuple[EmailAction, ...],
        action_parameters: Mapping[EmailAction, Mapping[str, object]],
        enabled: bool,
        config_version: str,
    ) -> dict[str, Any]:
        category_key = validate_email_category_key(category.value)
        _validate_config(
            category=category_key,
            threshold=threshold,
            actions=actions,
            action_parameters=action_parameters,
            config_version=config_version,
        )
        now = self._now()
        with self._connect() as db:
            db.execute("begin immediate")
            existing = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
            if existing is None:
                raise ValueError("email category config does not exist")
            final_enabled = bool(
                enabled and self._bindings_cover_enabled_accounts(db, category_key)
            )
            update_config(db, "email_category_configs", _config_source_rowid(db, "email_category_configs", "category_key", category_key), {
                "core_description": description, "threshold": threshold,
                "actions_json": _json_dump([action.value for action in actions]),
                "action_parameters_json": _json_dump({
                    action.value: dict(parameters)
                    for action, parameters in action_parameters.items()
                }),
                "enabled": int(final_enabled),
                "config_version": config_version, "updated_at": now,
            })
            row = db.execute(
                f"select * from ({config_projection('email_category_configs')}) where category_key=?",
                (category_key,),
            ).fetchone()
        assert row is not None
        return self._legacy_config_row(self._category_config_row(row))

    @staticmethod
    def _category_config_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return category_config_row(row)
        except (CategoryConfigDataError, KeyError, TypeError, ValueError) as exc:
            raise EmailPersistenceCorruption(
                "invalid structured email category config"
            ) from exc

    @staticmethod
    def _legacy_config_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return legacy_config_row(row)


def _optional_probability(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be numeric or None")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return result


def _historical_outcome_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    if result["important"] is not None:
        result["important"] = bool(result["important"])
    return result


def _validate_config(
    *,
    category: str,
    threshold: float,
    actions: tuple[EmailAction, ...],
    action_parameters: Mapping[EmailAction, Mapping[str, object]],
    config_version: str,
) -> None:
    if EmailAction.AUTO_REPLY in actions:
        raise ValueError("auto_reply is disabled")
    build_email_action_plan(
        classification_id=1,
        account_id="configuration-validation",
        category=category,
        classification_source="model",
        confidence=threshold,
        model_id="configuration-validation",
        config_version=config_version,
        actions=actions,
        action_parameters={
            action: dict(parameters) for action, parameters in action_parameters.items()
        },
        created_at=datetime.now(timezone.utc),
    )
