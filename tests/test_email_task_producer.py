from datetime import datetime, timezone
from types import SimpleNamespace

from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
)
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    build_email_action_plan,
)
from app.email_task_producer import (
    EmailActionTaskProducer,
    EmailClassificationTaskProducer,
)


def _plan(actions: tuple[EmailAction, ...]):
    parameters = (
        {EmailAction.LABEL: {"labels": ["Work"]}}
        if EmailAction.LABEL in actions
        else {}
    )
    return build_email_action_plan(
        classification_id=17,
        account_id="account-1",
        category=(
            EmailCategory.JUNK
            if EmailAction.UNSUBSCRIBE in actions
            else EmailCategory.WORK
        ),
        classification_source="user",
        confidence=0.61,
        model_id="email-model:v1",
        config_version="email-config:v1",
        actions=actions,
        action_parameters=parameters,
        created_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls = []

    def ensure_action_plan_tasks(self, action_plan, task_input):
        self.calls.append((action_plan, task_input))
        return (SimpleNamespace(action_type=EmailAction.UNSUBSCRIBE),)


def test_only_unsubscribe_creates_an_agent_task():
    adapter = _RecordingAdapter()
    task_producer = object.__new__(EmailActionTaskProducer)
    task_producer.adapter = adapter
    task_producer.email_store = object()
    task_producer._task_input = lambda _plan, _message: object()
    direct_only_plan = _plan((EmailAction.LABEL, EmailAction.ARCHIVE))
    unsubscribe_plan = _plan((EmailAction.UNSUBSCRIBE,))

    assert task_producer.produce(direct_only_plan, {}) == ()
    assert tuple(
        task.action_type for task in task_producer.produce(unsubscribe_plan, {})
    ) == (EmailAction.UNSUBSCRIBE,)
    assert len(adapter.calls) == 1


def test_classification_producer_enqueues_without_creating_generic_reply_task():
    calls = []
    producer = object.__new__(EmailClassificationTaskProducer)
    producer.adapter = SimpleNamespace(
        ensure_task=lambda task_input: (
            calls.append(task_input)
            or SimpleNamespace(status="pending", channel="email")
        )
    )

    task = producer.produce(
        {
            "accountId": "account-1",
            "folder": "INBOX",
            "uidValidity": 42,
            "uid": 7,
            "messageId": "<message-7@example.com>",
            "providerUnread": True,
        },
        allowed_category_keys=("work", "junk"),
        category_descriptions={
            "work": {"core": "Business."},
            "junk": {"core": "Unwanted."},
        },
        folder_targets={"work": "Work"},
        config_version="config-v1",
        unsubscribe_candidates=(),
    )

    assert task.status == "pending"
    assert task.channel == "email"
    assert len(calls) == 1


def test_classification_producer_captures_the_current_cron_consumer_context():
    calls = []
    producer = object.__new__(EmailClassificationTaskProducer)
    producer.adapter = SimpleNamespace(
        ensure_task=lambda task_input: calls.append(task_input) or object()
    )
    context = ServiceCommandConsumerContext(
        scheduled_task_id=10,
        scheduled_task_run_id=101,
        prompt="使用 $ceo-email-classifier 分类真实新邮件。",
        skill_names=("ceo-email-classifier",),
        skill_protocol="## Managed Skill: ceo-email-classifier\nSELECTED",
    )

    def produce():
        return producer.produce(
            {
                "accountId": "account-1",
                "folder": "INBOX",
                "uidValidity": 42,
                "uid": 7,
                "messageId": "<message-7@example.com>",
                "providerUnread": True,
            },
            allowed_category_keys=("work", "junk"),
            category_descriptions={
                "work": {"core": "Business."},
                "junk": {"core": "Unwanted."},
            },
            folder_targets={"work": "Work"},
            config_version="config-v1",
            unsubscribe_candidates=(),
        )

    implementations = {option.name: (lambda: "unused") for option in SERVICE_COMMAND_OPTIONS}
    implementations["produce-once"] = produce
    ServiceCommandRegistry(implementations).run(
        "produce-once", consumer_context=context
    )

    assert calls[0].scheduled_consumer == context.to_payload()
