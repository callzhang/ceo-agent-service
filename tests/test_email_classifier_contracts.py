from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.email_classifier_contracts import (
    AGENT_ACTIONS,
    DIRECT_ACTIONS,
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    EmailProviderLocator,
)


CREATED_AT = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)


def _locator(
    *,
    account_id: str = "account-1",
    rfc_message_id: str | None = " <Msg-1@Example.COM> ",
) -> EmailProviderLocator:
    return EmailProviderLocator(
        account_id=account_id,
        folder="INBOX",
        uidvalidity=42,
        uid=7,
        rfc_message_id=rfc_message_id,
        thread_id="thread-1",
    )


def _plan(**overrides: object) -> EmailActionPlan:
    values: dict[str, object] = {
        "action_plan_id": "plan-1",
        "action_plan_version": 1,
        "classification_id": 11,
        "account_id": "account-1",
        "category": EmailCategory.WORK,
        "classification_source": "model",
        "confidence": 0.93,
        "model_id": "email/logistic/model-1",
        "config_version": "email-v1",
        "actions": (
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.LABEL,
            EmailAction.UNSUBSCRIBE,
        ),
        "action_parameters": {
            EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt"},
            EmailAction.LABEL: {"labels": ["work", "follow-up"]},
        },
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return EmailActionPlan.model_validate(values)


def _classification(**overrides: object) -> EmailClassification:
    plan = overrides.pop("action_plan", _plan())
    values: dict[str, object] = {
        "classification_id": 11,
        "provider_locator": _locator(),
        "category": EmailCategory.WORK,
        "confidence": 0.93,
        "margin": 0.41,
        "probabilities": {"work": 0.93, "important": 0.07},
        "model_id": "email/logistic/model-1",
        "config_version": "email-v1",
        "status": EmailClassificationStatus.PROCESSED,
        "classification_source": "model",
        "action_plan": plan,
    }
    values.update(overrides)
    return EmailClassification.model_validate(values)


def test_action_plan_splits_direct_and_agent_actions_in_configured_order():
    plan = _plan()

    assert DIRECT_ACTIONS == (
        EmailAction.LABEL,
        EmailAction.MARK_READ,
        EmailAction.ARCHIVE,
        EmailAction.MOVE,
        EmailAction.TRASH,
    )
    assert AGENT_ACTIONS == (EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE)
    assert plan.direct_actions == (EmailAction.MARK_READ, EmailAction.LABEL)
    assert plan.agent_actions == (EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE)


def test_pending_feedback_contains_model_suggestion_but_no_action_plan():
    classification = _classification(
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        action_plan=None,
    )

    assert classification.category is EmailCategory.WORK
    assert classification.confidence == 0.93
    assert classification.action_plan is None


def test_processed_classification_requires_an_action_plan():
    with pytest.raises(ValidationError, match="processed classification requires"):
        _classification(action_plan=None)


def test_pending_feedback_rejects_an_action_plan():
    with pytest.raises(ValidationError, match="pending feedback cannot have"):
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            action_plan=_plan(),
        )


def test_user_confirmed_classification_must_be_processed():
    with pytest.raises(ValidationError, match="user classification must be processed"):
        _classification(
            status=EmailClassificationStatus.PENDING_FEEDBACK,
            classification_source="user",
            action_plan=None,
        )


def test_provider_locator_normalizes_rfc_message_id_and_is_account_aware():
    first = _locator(account_id="account-1")
    second = _locator(account_id="account-2")

    assert first.rfc_message_id == "<Msg-1@example.com>"
    assert first.stable_message_identity == "account-1:message-id:<Msg-1@example.com>"
    assert second.stable_message_identity == "account-2:message-id:<Msg-1@example.com>"
    assert first.stable_message_identity != second.stable_message_identity


def test_provider_locator_falls_back_to_account_folder_and_imap_uid():
    locator = _locator(rfc_message_id="not a valid message id")

    assert locator.rfc_message_id is None
    assert locator.stable_message_identity == "account-1:imap:INBOX:42:7"


@pytest.mark.parametrize(
    ("field", "value"),
    (("account_id", ""), ("folder", ""), ("uidvalidity", 0), ("uid", 0)),
)
def test_provider_locator_rejects_unstable_coordinates(field: str, value: object):
    values = _locator().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        EmailProviderLocator.model_validate(values)


def test_attachment_metadata_is_immutable_and_cannot_hold_payload_content():
    attachment = EmailAttachmentMetadata(
        filename="report.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        inline=False,
    )

    with pytest.raises(ValidationError):
        attachment.filename = "changed.pdf"
    with pytest.raises(ValidationError):
        EmailAttachmentMetadata.model_validate(
            {
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 123,
                "inline": False,
                "content": "forbidden",
            }
        )
    assert "payload" not in EmailAttachmentMetadata.model_fields
    assert "content" not in EmailAttachmentMetadata.model_fields


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"actions": (EmailAction.ARCHIVE, EmailAction.ARCHIVE)}, "unique"),
        (
            {
                "actions": (EmailAction.LABEL,),
                "action_parameters": {EmailAction.LABEL: {"labels": [" "]}},
            },
            "non-blank label",
        ),
        (
            {
                "actions": (EmailAction.MOVE,),
                "action_parameters": {EmailAction.MOVE: {"target_folder": " "}},
            },
            "target_folder",
        ),
        (
            {"actions": (EmailAction.ARCHIVE, EmailAction.TRASH), "action_parameters": {}},
            "mutually exclusive",
        ),
        (
            {"actions": (EmailAction.MOVE, EmailAction.TRASH), "action_parameters": {}},
            "mutually exclusive",
        ),
        (
            {
                "actions": (EmailAction.AUTO_REPLY,),
                "action_parameters": {EmailAction.AUTO_REPLY: {"instruction": ""}},
            },
            "instruction",
        ),
        (
            {
                "actions": (EmailAction.MARK_READ,),
                "action_parameters": {EmailAction.TRASH: {}},
            },
            "not configured",
        ),
    ),
)
def test_action_plan_validates_configured_actions(
    overrides: dict[str, object], message: str
):
    with pytest.raises(ValidationError, match=message):
        _plan(**overrides)


def test_action_plan_is_an_immutable_execution_authorization_snapshot():
    plan = _plan()

    with pytest.raises(ValidationError):
        plan.confidence = 0.1
    with pytest.raises(TypeError):
        plan.action_parameters[EmailAction.LABEL]["labels"] = ("changed",)
    assert "is_execution_authorization" not in EmailActionPlan.model_fields


@pytest.mark.parametrize(
    ("classification_overrides", "plan_overrides", "message"),
    (
        ({"classification_id": 12}, {}, "classification ids"),
        ({}, {"account_id": "account-2"}, "accounts"),
        ({"category": EmailCategory.IMPORTANT}, {}, "categories"),
        ({"classification_source": "user"}, {}, "sources"),
        ({"confidence": 0.5}, {}, "confidence"),
        ({"model_id": "different/model"}, {}, "model ids"),
        ({"config_version": "email-v2"}, {}, "configs"),
    ),
)
def test_processed_classification_validates_action_plan_consistency(
    classification_overrides: dict[str, object],
    plan_overrides: dict[str, object],
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        _classification(
            action_plan=_plan(**plan_overrides),
            **classification_overrides,
        )


def test_contract_has_no_permanent_delete_action():
    assert "EXPUNGE" not in {action.value.upper() for action in EmailAction}
    assert "permanent_delete" not in {action.value for action in EmailAction}
