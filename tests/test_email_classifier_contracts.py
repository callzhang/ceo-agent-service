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
    build_email_action_plan,
    build_versioned_email_action_plan,
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
    return build_email_action_plan(**values)  # type: ignore[arg-type]


def _classification(**overrides: object) -> EmailClassification:
    plan = overrides.pop("action_plan", _plan())
    values: dict[str, object] = {
        "classification_id": 11,
        "stable_message_identity": "account-1:message-id:<Msg-1@example.com>",
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


def test_classification_preserves_business_identity_separately_from_moved_locator():
    original = _locator(rfc_message_id=None)

    moved = EmailProviderLocator(
        account_id="account-1",
        folder="Archive/2026",
        uidvalidity=84,
        uid=91,
        rfc_message_id=None,
    )
    classification = _classification(
        stable_message_identity=original.stable_message_identity,
        provider_locator=moved,
        status=EmailClassificationStatus.PENDING_FEEDBACK,
        action_plan=None,
    )

    assert moved.folder == "Archive/2026"
    assert moved.uidvalidity == 84
    assert moved.uid == 91
    assert moved.stable_message_identity != original.stable_message_identity
    assert classification.stable_message_identity == original.stable_message_identity


@pytest.mark.parametrize(
    "message_id",
    (
        "<<message@example.com>>",
        "<message..part@example.com>",
        "<.message@example.com>",
        "<message@example..com>",
        "<message@-example.com>",
        "<message@example.com",
    ),
)
def test_provider_locator_rejects_malformed_rfc_message_ids(message_id: str):
    locator = _locator(rfc_message_id=message_id)

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
        (
            {
                "actions": (EmailAction.LABEL,),
                "action_parameters": {
                    EmailAction.LABEL: {"labels": ["work"], "color": "blue"}
                },
            },
            "unsupported parameters",
        ),
        (
            {
                "actions": (EmailAction.MOVE,),
                "action_parameters": {
                    EmailAction.MOVE: {
                        "target_folder": "Archive/2026",
                        "copy": True,
                    }
                },
            },
            "unsupported parameters",
        ),
        (
            {
                "actions": (EmailAction.AUTO_REPLY,),
                "action_parameters": {
                    EmailAction.AUTO_REPLY: {
                        "instruction": "Acknowledge receipt",
                        "send_as": "other-account",
                    }
                },
            },
            "unsupported parameters",
        ),
        (
            {
                "actions": (EmailAction.MARK_READ,),
                "action_parameters": {EmailAction.MARK_READ: {"flag": "\\Seen"}},
            },
            "does not accept parameters",
        ),
        (
            {
                "actions": (EmailAction.ARCHIVE,),
                "action_parameters": {EmailAction.ARCHIVE: {"folder": "Archive"}},
            },
            "does not accept parameters",
        ),
        (
            {
                "actions": (EmailAction.TRASH,),
                "action_parameters": {EmailAction.TRASH: {"permanent_delete": True}},
            },
            "does not accept parameters",
        ),
        (
            {
                "actions": (EmailAction.TRASH,),
                "action_parameters": {EmailAction.TRASH: {"imap_command": "EXPUNGE"}},
            },
            "does not accept parameters",
        ),
        (
            {
                "actions": (EmailAction.UNSUBSCRIBE,),
                "action_parameters": {
                    EmailAction.UNSUBSCRIBE: {"unsubscribe_url": "https://example.test"}
                },
            },
            "does not accept parameters",
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


def test_action_plan_rejects_reusing_an_identity_for_changed_snapshot_facts():
    plan = _plan()
    changed_snapshot = plan.model_dump()
    changed_snapshot["confidence"] = 0.5

    with pytest.raises(ValidationError, match="action plan identity"):
        EmailActionPlan.model_validate(changed_snapshot)


def test_action_plan_identity_includes_created_at():
    first = _plan(created_at=datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc))
    second = _plan(created_at=datetime(2026, 8, 29, 16, 1, tzinfo=timezone.utc))

    assert first.action_plan_id != second.action_plan_id


def test_public_action_plan_builder_supports_explicit_next_version():
    plan = build_versioned_email_action_plan(
        action_plan_version=2,
        classification_id=11,
        account_id="account-1",
        category=EmailCategory.IMPORTANT,
        classification_source="user",
        confidence=0.93,
        model_id="email/logistic/model-1",
        config_version="email-v2",
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        created_at=CREATED_AT,
    )

    assert plan.action_plan_version == 2
    assert plan.category is EmailCategory.IMPORTANT
    assert plan.classification_source == "user"
    assert plan.model_id == "email/logistic/model-1"
    assert plan.config_version == "email-v2"


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
