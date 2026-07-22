import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.leak_check import FORBIDDEN_MARKERS
from app.store import AutoReplyStore
from app.permission import PermissionAction, PermissionResult
from app.universal_context import UniversalContextMessage, UniversalTaskContext
from app.universal_executor import (
    UniversalActionExecution,
    UniversalActionExecutionState,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.dws_client import DwsCalendarEvent
from app.worker import DingTalkAutoReplyWorker, ReplyDeliveryError


class FakeDws:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def ding_self(self, text: str) -> None:
        self.calls.append(("ding_self", text))


class UniversalReactionFakeDws(FakeDws):
    def __init__(self) -> None:
        super().__init__()
        self.emoji_result: object = {
            "result": {"receipt": {"reactionId": 731}},
            "success": True,
        }
        self.emoji_error: Exception | None = None
        self.created_text_emotions: list[tuple[str, str, str]] = []
        self.added_text_emotions: list[tuple[object, ...]] = []
        self.text_emotion_create_result: object = {
            "result": {"emotion": {"emotionId": 902, "backgroundId": 17}}
        }
        self.text_emotion_create_error: Exception | None = None
        self.text_emotion_add_results: list[object] = [
            {"result": {"receipt": {"reactionId": 903}}, "success": True}
        ]
        self.text_emotion_add_error: Exception | None = None

    def add_message_emoji(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str,
    ) -> object:
        self.calls.append(("emoji", conversation_id, message_id, emoji))
        if self.emoji_error is not None:
            raise self.emoji_error
        return self.emoji_result

    def create_message_text_emotion(
        self,
        *,
        text: str,
        emotion_name: str,
        background_id: str,
    ) -> object:
        self.created_text_emotions.append((text, emotion_name, background_id))
        if self.text_emotion_create_error is not None:
            raise self.text_emotion_create_error
        return self.text_emotion_create_result

    def add_message_text_emotion(
        self,
        conversation_id: str,
        message_id: str,
        *,
        text: str,
        emotion_id: str,
        emotion_name: str,
        background_id: str,
    ) -> object:
        self.added_text_emotions.append(
            (
                conversation_id,
                message_id,
                text,
                emotion_id,
                emotion_name,
                background_id,
            )
        )
        if self.text_emotion_add_error is not None:
            raise self.text_emotion_add_error
        if len(self.text_emotion_add_results) > 1:
            return self.text_emotion_add_results.pop(0)
        return self.text_emotion_add_results[0]


class UniversalDocumentFakeDws(FakeDws):
    def __init__(self) -> None:
        super().__init__()
        self.created_documents: list[tuple[str, str]] = []
        self.permission_calls: list[tuple[str, list[str]]] = []
        self.sent_links: list[tuple[str, str, str]] = []
        self.send_error: Exception | None = None
        self.send_results: list[object] = [
            {"result": {"message": {"messageId": 843}}, "success": True}
        ]
        self.document_result: object = {
            "result": {
                "document": {
                    "nodeId": 841,
                    "url": "https://alidocs.dingtalk.com/i/nodes/841",
                }
            }
        }
        self.permission_results: list[object] = [{"success": True}]

    def create_markdown_doc(self, title: str, text: str) -> object:
        self.created_documents.append((title, text))
        return self.document_result

    def add_doc_editor_permission(self, node_id: str, user_ids: list[str]) -> object:
        self.permission_calls.append((node_id, user_ids))
        if len(self.permission_results) > 1:
            return self.permission_results.pop(0)
        return self.permission_results[0]

    def send_reply_to_trigger(self, conversation, trigger, text, **kwargs) -> object:
        self.sent_links.append(
            (conversation.open_conversation_id, trigger.open_message_id, text)
        )
        if self.send_error is not None:
            raise self.send_error
        if len(self.send_results) > 1:
            return self.send_results.pop(0)
        return self.send_results[0]

    def read_recent_messages(self, conversation) -> list:
        return []


class NativeReplyFakeDws(FakeDws):
    def resolve_message_sender(self, message) -> str:
        return message.sender_user_id or "resolved-user"

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text,
        **kwargs,
    ) -> dict:
        assert trigger.sender_open_dingtalk_id == "open-context-sender"
        assert trigger.sender_user_id == "user-context-sender"
        assert trigger.create_time == "2026-07-20 10:00:00"
        self.calls.append((conversation, trigger, text, kwargs))
        return {"success": True, "messageId": "sent-native-1"}

    def read_recent_messages(self, conversation):
        raise RuntimeError("visibility unavailable in offline test")


class FakeCodex:
    pass


class UniversalMailCalendarFakeDws(FakeDws):
    def __init__(self) -> None:
        super().__init__()
        self.mail_replies: list[tuple[str, str, str, str]] = []
        self.mail_error: Exception | None = None
        self.mail_build_error: Exception | None = None
        self.mail_result: dict = {"success": True, "messageId": "mail-receipt-1"}
        self.calendar_responses: list[tuple[str, str]] = []
        self.calendar_error: Exception | None = None
        self.calendar_get_error: Exception | None = None
        self.calendar_event = None
        self.apply_calendar_response = True
        self.hide_calendar_after_response = False

    def build_mail_reply_command(self, *, mailbox, message_id, subject, content):
        if self.mail_build_error is not None:
            raise self.mail_build_error
        if not all((mailbox, message_id, subject, content)):
            raise ValueError("mail target is incomplete")
        return ["dws", "mail", "message", "reply", "--content", content]

    def reply_mail(self, mailbox, message_id, subject, content):
        self.mail_replies.append((mailbox, message_id, subject, content))
        if self.mail_error is not None:
            raise self.mail_error
        return self.mail_result

    def get_calendar_event(self, event_id):
        if self.calendar_get_error is not None:
            raise self.calendar_get_error
        assert self.calendar_event is None or self.calendar_event.event_id == event_id
        return self.calendar_event

    def respond_calendar_event(self, event_id, response_status):
        self.calendar_responses.append((event_id, response_status))
        if self.calendar_error is not None:
            raise self.calendar_error
        if self.calendar_event is not None and self.apply_calendar_response:
            self.calendar_event = self.calendar_event.model_copy(
                update={"self_response_status": response_status}
            )
        if self.hide_calendar_after_response:
            self.calendar_event = None
        return {"success": True, "requestId": "calendar-receipt-1"}


class UniversalOaFakeDws(FakeDws):
    def __init__(
        self,
        *,
        owner: str = "principal-user-1",
        task_status: str = "RUNNING",
        process_status: str = "RUNNING",
    ) -> None:
        super().__init__()
        self.current_user_id = "principal-user-1"
        self.owner = owner
        self.tasks_owner = owner
        self.task_status = task_status
        self.process_status = process_status
        self.action_calls: list[tuple[str, str, str, str]] = []
        self.comment_calls: list[tuple[str, str]] = []
        self.revert_calls: list[tuple[str, str, str, str, str]] = []
        self.records: list[dict] = []
        self.raise_after_apply = False
        self.raise_without_apply = False
        self.hide_task_after_apply = False
        self.complete_process_when_hiding = False
        self.task_hidden = False
        self.action_result: object = {
            "success": True,
            "requestId": "request-action-1",
            "result": [],
        }
        self.comment_result: object = {
            "success": True,
            "requestId": "request-comment-1",
            "result": [],
        }
        self.revert_activities: dict = {
            "_mock": True,
            "_tool": "get_inst_revert_activities",
            "result": [[{
                "activityId": "activity-1",
                "revertActions": ["REVERT_FOR_APPROVAL", "REVERT_FOR_RESUBMIT"],
            }]],
            "success": True,
        }
        self.action_value_error_after_apply = False
        self.record_action_after_apply = True
        self.recorded_action_override = ""

    def get_current_user_id(self) -> str:
        self.calls.append("get_current_user_id")
        return self.current_user_id

    def read_oa_approval_detail(self, process_instance_id: str) -> dict:
        self.calls.append(("detail", process_instance_id))
        return {
            "result": {
                "processInstanceId": process_instance_id,
                "status": self.process_status,
                "tasks": [] if self.task_hidden else [self._task()],
            }
        }

    def read_oa_approval_tasks(self, process_instance_id: str) -> dict:
        self.calls.append(("tasks", process_instance_id))
        if self.task_hidden:
            return {"result": {"tasks": []}}
        task = self._task()
        task["userId"] = self.tasks_owner
        return {"result": {"tasks": [task]}}

    def read_oa_approval_records(self, process_instance_id: str) -> dict:
        self.calls.append(("records", process_instance_id))
        return {"result": [self.records]}

    def read_oa_revert_activities(self, task_id: str) -> dict:
        self.calls.append(("revert-activities", task_id))
        return self.revert_activities

    def read_oa_process_instance_openapi(self, process_instance_id: str) -> dict:
        self.calls.append(("openapi", process_instance_id))
        return self.read_oa_approval_detail(process_instance_id)

    def execute_oa_approval_action(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ) -> dict:
        self.action_calls.append((process_instance_id, task_id, action, remark))
        if self.raise_after_apply:
            self._apply(action)
            raise TimeoutError("approval response timeout")
        if self.raise_without_apply:
            raise TimeoutError("approval response timeout")
        self._apply(action)
        if self.action_value_error_after_apply:
            raise ValueError("invalid response after OA request")
        return self.action_result

    def comment_oa_approval(self, process_instance_id: str, text: str) -> dict:
        self.comment_calls.append((process_instance_id, text))
        if self.raise_without_apply:
            raise TimeoutError("comment response timeout")
        return self.comment_result

    def revert_oa_approval_task(
        self,
        *,
        process_instance_id: str,
        task_id: str,
        target_activity_id: str,
        revert_action: str,
        remark: str,
    ) -> dict:
        self.revert_calls.append(
            (process_instance_id, task_id, target_activity_id, revert_action, remark)
        )
        self._apply("退回")
        return {"success": True, "requestId": "request-revert-1", "result": []}

    def _task(self) -> dict:
        return {
            "taskId": "task-1",
            "status": self.task_status,
            "userId": self.owner,
        }

    def _apply(self, action: str) -> None:
        if self.record_action_after_apply:
            self.records.append(
                {
                    "taskId": "task-1",
                    "userId": self.current_user_id,
                    "operationType": self.recorded_action_override or action,
                }
            )
        if self.hide_task_after_apply:
            self.task_hidden = True
            if self.complete_process_when_hiding:
                self.process_status = "COMPLETED"
            return
        self.task_status = "COMPLETED"
        self.process_status = "COMPLETED"


def _execution(
    store: AutoReplyStore,
    *,
    kind: PlannedActionKind,
    target: dict | None = None,
    payload: dict | None = None,
    sensitivity_kind: str | None = None,
    personnel_subject_user_id: str | None = None,
    candidate_context_known: bool = False,
    candidate_department_ids: list[str] | None = None,
    trusted_oa_target: bool = True,
    trusted_oa_process_instance_id: str | None = None,
    trusted_oa_task_id: str | None = None,
    trusted_mail_target: tuple[str, str, str] | None = None,
    trusted_calendar_target: tuple[str, str, str] | None = None,
    trusted_document_url: str = "",
) -> UniversalActionExecution:
    inserted = store.enqueue_reply_task(
        conversation_id="cid-context",
        conversation_title="Context title",
        single_chat=False,
        trigger_message_id="msg-context",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Context sender",
        trigger_text="Context trigger",
    )
    assert inserted is True
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id="cid-context",
        conversation_title="Context title",
        single_chat=False,
        trigger_message_id="msg-context",
        trigger_create_time=task.trigger_create_time,
        trigger_sender="Context sender",
        trigger_text="Context trigger",
        context_messages=(
            UniversalContextMessage(
                sender_name="Earlier sender",
                open_message_id="msg-earlier",
                content="Earlier context",
            ),
            UniversalContextMessage(
                sender_name="Context sender",
                open_message_id="msg-context",
                content="Context trigger",
                sender_open_dingtalk_id="open-context-sender",
                sender_user_id="user-context-sender",
                message_type="text",
                create_time="2026-07-20 10:00:00",
                mentioned_user_ids=("mentioned-user",),
                quoted_message_id="quoted-message",
                quoted_content="quoted-content",
                raw_payload_json='{"source":"reply-task"}',
            ),
        ),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
        trusted_oa_process_instance_id=(
            trusted_oa_process_instance_id
            if trusted_oa_process_instance_id is not None
            else (
                str((target or {}).get("process_instance_id") or "")
                if kind is PlannedActionKind.OA_APPROVAL and trusted_oa_target
                else ""
            )
        ),
        trusted_oa_task_id=(
            trusted_oa_task_id
            if trusted_oa_task_id is not None
            else (
                str((target or {}).get("task_id") or "")
                if kind is PlannedActionKind.OA_APPROVAL and trusted_oa_target
                else ""
            )
        ),
        trusted_mail_mailbox=(trusted_mail_target or ("", "", ""))[0],
        trusted_mail_message_id=(trusted_mail_target or ("", "", ""))[1],
        trusted_mail_subject=(trusted_mail_target or ("", "", ""))[2],
        trusted_calendar_event_id=(trusted_calendar_target or ("", "", ""))[0],
        trusted_calendar_response_status=(trusted_calendar_target or ("", "", ""))[1],
        trusted_calendar_organizer=(trusted_calendar_target or ("", "", ""))[2],
        trusted_document_url=trusted_document_url,
    )
    action = PlannedAction(
        kind=kind,
        reason=f"Reason for {kind.value}",
        sensitivity_kind=(
            sensitivity_kind
            if sensitivity_kind is not None
            else (
                "general"
                if kind
                in {
                    PlannedActionKind.SEND_REPLY,
                    PlannedActionKind.ASK_CLARIFYING_QUESTION,
                }
                else None
            )
        ),
        personnel_subject_user_id=personnel_subject_user_id,
        candidate_context_known=candidate_context_known,
        candidate_department_ids=candidate_department_ids or [],
        target=target or {},
        payload=payload or {},
    )
    plan = UniversalPlan(
        task_kind="message_handling",
        reason="Handle immutable trigger",
        actions=[action],
        audit=UniversalAudit(
            summary="Universal action test",
            confidence=0.9,
        ),
    )
    plan_execution = store.create_universal_plan_execution(context, plan)
    return build_universal_action_execution(
        context,
        plan_execution,
        plan_execution.plan.actions[0],
        0,
    )


def _worker(store: AutoReplyStore) -> DingTalkAutoReplyWorker:
    return DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws(),
        codex=FakeCodex(),
    )


def test_universal_reaction_rejects_planner_target_spoof(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-spoof", "message_id": "msg-spoof"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    worker = _worker(store)

    assert worker.execute_universal_message_reaction(execution) is True

    assert worker.dws.calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "untrusted_message_reaction_target"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_reaction_strips_one_square_bracket_pair(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "[👍]"},
    )
    dws = UniversalReactionFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True

    assert dws.calls == [("emoji", "cid-context", "msg-context", "👍")]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_error == "emoji: 👍"


def test_universal_reaction_persists_nested_numeric_receipt(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert json.loads(attempt.reaction_action_result_json) == dws.emoji_result


def test_universal_reaction_duplicate_execution_does_not_react_twice(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True
    assert worker.execute_universal_message_reaction(execution) is True

    assert dws.calls == [("emoji", "cid-context", "msg-context", "👍")]
    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1


def test_universal_reaction_definite_pre_call_failure_is_retryable(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "[]"},
    )
    dws = UniversalReactionFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ValueError, match="emoji is required"):
        worker.execute_universal_message_reaction(execution)

    assert dws.calls == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


def test_universal_reaction_post_call_failure_is_unknown(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    dws.emoji_error = TimeoutError("reaction response timeout")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(TimeoutError, match="reaction response timeout"):
        worker.execute_universal_message_reaction(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "universal_action_outcome_unknown" in attempt.send_error
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )


def test_universal_reaction_explicit_failure_response_is_retryable(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    dws.emoji_result = {"success": False, "result": {}}
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="reaction receipt"):
        worker.execute_universal_message_reaction(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert json.loads(attempt.reaction_action_result_json) == dws.emoji_result
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


@pytest.mark.parametrize(
    ("executor_name", "expected_message"),
    [
        ("execute_universal_document_reply", "non-document action"),
        ("execute_universal_message_reaction", "non-reaction action"),
    ],
)
def test_universal_document_and_reaction_executors_reject_wrong_action_kind(
    tmp_path: Path,
    executor_name: str,
    expected_message: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = DingTalkAutoReplyWorker(store=store, dws=FakeDws(), codex=FakeCodex())

    with pytest.raises(ValueError, match=expected_message):
        getattr(worker, executor_name)(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


@pytest.mark.parametrize(
    "result",
    [
        {"success": True},
        {"success": True, "result": {"reactionId": 0}},
        {"result": {"receipt": {"reactionId": "0"}}},
        {"success": True, "result": {"emotionId": 902}},
    ],
)
def test_universal_reaction_missing_receipt_is_non_blocking(
    tmp_path: Path,
    result: dict,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    dws.emoji_result = result
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "reaction_receipt_missing_non_blocking"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


@pytest.mark.parametrize(
    "result",
    [
        {"errorCode": "REACTION_DENIED", "message": "denied"},
        {"result": {"code": 500, "message": "server rejected request"}},
    ],
)
def test_universal_reaction_error_code_response_is_retryable(
    tmp_path: Path,
    result: dict,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "emoji", "emoji": "👍"},
    )
    dws = UniversalReactionFakeDws()
    dws.emoji_result = result
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="reaction receipt reports failure"):
        worker.execute_universal_message_reaction(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


def test_universal_text_emotion_parses_nested_numeric_ids(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={
            "reaction_type": "text_emotion",
            "text": "我去摇人",
            "emotion_name": "我去摇人",
        },
    )
    dws = UniversalReactionFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True

    assert dws.added_text_emotions == [
        ("cid-context", "msg-context", "我去摇人", "902", "我去摇人", "17")
    ]


def test_universal_text_emotion_create_without_id_is_unknown_and_not_recreated(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "text_emotion", "text": "我去摇人"},
    )
    dws = UniversalReactionFakeDws()
    dws.text_emotion_create_result = {"success": True}
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="create receipt is missing"):
        worker.execute_universal_message_reaction(execution)

    assert len(dws.created_text_emotions) == 1
    assert dws.added_text_emotions == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_message_reaction(execution)
    assert len(dws.created_text_emotions) == 1


def test_universal_text_emotion_retries_add_from_durable_create_checkpoint(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "text_emotion", "text": "我去摇人"},
    )
    dws = UniversalReactionFakeDws()
    dws.text_emotion_add_results = [
        {"success": False, "errorCode": "REACTION_DENIED"},
        {"success": True, "result": {"receipt": {"reactionId": 904}}},
    ]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="add receipt reports failure"):
        worker.execute_universal_message_reaction(execution)

    assert len(dws.created_text_emotions) == 1
    assert len(dws.added_text_emotions) == 1
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    checkpoint = json.loads(attempt.reaction_action_result_json)
    assert checkpoint["create"]["emotion_id"] == "902"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    assert worker.execute_universal_message_reaction(execution) is True

    assert len(dws.created_text_emotions) == 1
    assert len(dws.added_text_emotions) == 2


def test_universal_text_emotion_recovers_started_create_checkpoint_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "text_emotion", "text": "我去摇人"},
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=UniversalReactionFakeDws(),
        codex=FakeCodex(),
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt_id = worker._record_universal_reply_attempt(
        execution,
        send_status="pending",
    )
    store.update_reply_attempt(
        attempt_id,
        reaction_action_result_json=json.dumps(
            {
                "reaction_type": "text_emotion",
                "create": {
                    "background_id": "17",
                    "emotion_id": "902",
                    "result": {
                        "result": {
                            "emotion": {"emotionId": 902, "backgroundId": 17}
                        }
                    },
                    "trusted": True,
                },
            }
        ),
    )

    reopened = AutoReplyStore(db_path)
    dws = UniversalReactionFakeDws()
    resumed_worker = DingTalkAutoReplyWorker(
        store=reopened,
        dws=dws,
        codex=FakeCodex(),
    )

    assert resumed_worker.execute_universal_message_reaction(execution) is True

    assert dws.created_text_emotions == []
    assert len(dws.added_text_emotions) == 1


def test_universal_text_emotion_ambiguous_add_is_non_blocking(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MESSAGE_REACTION,
        target={"conversation_id": "cid-context", "message_id": "msg-context"},
        payload={"reaction_type": "text_emotion", "text": "我去摇人"},
    )
    dws = UniversalReactionFakeDws()
    dws.text_emotion_add_results = [{"success": True}]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_message_reaction(execution) is True

    assert len(dws.created_text_emotions) == 1
    assert len(dws.added_text_emotions) == 1
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "reaction_receipt_missing_non_blocking"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_document_rejects_planner_target_spoof(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-spoof",
            "trigger_message_id": "msg-spoof",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    worker = _worker(store)

    assert worker.execute_universal_document_reply(execution) is True

    assert worker.dws.calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "untrusted_markdown_document_reply_target"


def test_universal_document_rejects_planner_document_url_spoof(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
            "document_url": "https://alidocs.dingtalk.com/i/nodes/spoof",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
        trusted_document_url="https://alidocs.dingtalk.com/i/nodes/source-1",
    )
    worker = _worker(store)

    assert worker.execute_universal_document_reply(execution) is True

    assert worker.dws.calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_error == "untrusted_markdown_document_reply_target"


def test_universal_document_creates_document_and_delivers_verified_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "0")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_document_reply(execution) is True

    assert dws.created_documents == [("方案", "# 方案\n\n正文")]
    assert dws.permission_calls == [("841", ["user-context-sender"])]
    assert len(dws.sent_links) == 1
    assert "https://alidocs.dingtalk.com/i/nodes/841" in dws.sent_links[0][2]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "sent"
    receipt = json.loads(attempt.document_action_result_json)
    assert receipt["node_id"] == "841"
    assert receipt["delivery"]["result"]["message"]["messageId"] == 843
    assert store.get_sent_reply("cid-context", "msg-context") is not None
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_document_recovers_durable_receipt_without_recreating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "0")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt_id = worker._record_universal_reply_attempt(
        execution,
        draft_reply_text="# 方案\n\n正文",
        send_status="failed",
    )
    durable_receipt = {
        "title": "方案",
        "url": "https://alidocs.dingtalk.com/i/nodes/841",
        "node_id": "841",
        "doc_result": {
            "result": {
                "document": {
                    "nodeId": 841,
                    "url": "https://alidocs.dingtalk.com/i/nodes/841",
                }
            }
        },
        "permission_result": {
            "success": True,
        },
    }
    store.update_reply_attempt(
        attempt_id,
        document_action_result_json=json.dumps(durable_receipt),
    )
    store.mark_universal_action_execution_failed(execution, "delivery_not_started")

    assert worker.execute_universal_document_reply(execution) is True

    assert dws.created_documents == []
    assert dws.permission_calls == []
    assert len(dws.sent_links) == 1


def test_universal_document_recovers_started_create_checkpoint_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "0")
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=UniversalDocumentFakeDws(),
        codex=FakeCodex(),
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt_id = worker._record_universal_reply_attempt(
        execution,
        draft_reply_text="# 方案\n\n正文",
        send_status="pending",
    )
    durable_receipt = {
        "title": "方案",
        "url": "https://alidocs.dingtalk.com/i/nodes/841",
        "node_id": "841",
        "doc_result": {
            "result": {
                "document": {
                    "nodeId": 841,
                    "url": "https://alidocs.dingtalk.com/i/nodes/841",
                }
            }
        },
    }
    store.update_reply_attempt(
        attempt_id,
        document_action_result_json=json.dumps(durable_receipt),
    )

    reopened = AutoReplyStore(db_path)
    dws = UniversalDocumentFakeDws()
    resumed_worker = DingTalkAutoReplyWorker(
        store=reopened,
        dws=dws,
        codex=FakeCodex(),
    )

    assert resumed_worker.execute_universal_document_reply(execution) is True

    assert dws.created_documents == []
    assert dws.permission_calls == [("841", ["user-context-sender"])]
    assert len(dws.sent_links) == 1


def test_universal_document_recovery_claim_is_atomic(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=UniversalDocumentFakeDws(),
        codex=FakeCodex(),
    )
    store.claim_universal_action_execution(execution)
    attempt_id = worker._record_universal_reply_attempt(
        execution,
        draft_reply_text="# 方案\n\n正文",
        send_status="pending",
    )
    store.update_reply_attempt(
        attempt_id,
        document_action_result_json=json.dumps(
            {
                "title": "方案",
                "url": "https://alidocs.dingtalk.com/i/nodes/841",
                "node_id": "841",
                "doc_result": {
                    "result": {
                        "document": {
                            "nodeId": 841,
                            "url": "https://alidocs.dingtalk.com/i/nodes/841",
                        }
                    }
                },
            }
        ),
    )

    def claim() -> UniversalActionExecutionState:
        recovered, _ = AutoReplyStore(
            db_path
        ).claim_universal_action_execution_recovery(
            execution,
            checkpoint_column="document_action_result_json",
        )
        return recovered

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = sorted(
            (future.result().value for future in (pool.submit(claim), pool.submit(claim)))
        )

    assert states == ["not_started", "unknown"]


def test_universal_document_retries_definite_permission_failure_from_checkpoint(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    dws.permission_results = [
        {"success": False, "errorCode": "PERMISSION_DENIED"},
        {"success": True},
    ]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="permission.*receipt"):
        worker.execute_universal_document_reply(execution)

    assert len(dws.created_documents) == 1
    assert len(dws.permission_calls) == 1
    assert dws.sent_links == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    assert worker.execute_universal_document_reply(execution) is True

    assert len(dws.created_documents) == 1
    assert len(dws.permission_calls) == 2
    assert len(dws.sent_links) == 1
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_document_retries_definite_link_failure_from_checkpoint(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    dws.send_results = [
        {"success": False, "errorCode": "DELIVERY_REJECTED"},
        {"success": True, "result": {"message": {"messageId": 843}}},
    ]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="link delivery.*failure"):
        worker.execute_universal_document_reply(execution)

    assert len(dws.created_documents) == 1
    assert len(dws.permission_calls) == 1
    assert len(dws.sent_links) == 1
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    assert worker.execute_universal_document_reply(execution) is True

    assert len(dws.created_documents) == 1
    assert len(dws.permission_calls) == 1
    assert len(dws.sent_links) == 2
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_document_ambiguous_link_delivery_is_unknown_without_retry(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    dws.send_error = TimeoutError("link delivery response timeout")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(TimeoutError, match="link delivery response timeout"):
        worker.execute_universal_document_reply(execution)

    assert len(dws.created_documents) == 1
    assert len(dws.sent_links) == 1
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_document_reply(execution)
    assert len(dws.created_documents) == 1


def test_universal_document_rejects_explicit_failed_creation_receipt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY,
        target={
            "conversation_id": "cid-context",
            "trigger_message_id": "msg-context",
        },
        payload={"title": "方案", "text": "# 方案\n\n正文"},
    )
    dws = UniversalDocumentFakeDws()
    dws.document_result = {
        "success": False,
        "result": {
            "document": {
                "nodeId": 841,
                "url": "https://alidocs.dingtalk.com/i/nodes/841",
            }
        },
    }
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="document creation receipt"):
        worker.execute_universal_document_reply(execution)

    assert dws.permission_calls == []
    assert dws.sent_links == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


def test_universal_mail_reply_uses_trusted_target_and_persists_receipt(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.MAIL_REPLY,
        target={
            "mailbox": "derek@example.com",
            "message_id": "mail-1",
            "subject": "Approval request",
        },
        payload={"content": "Approved."},
        trusted_mail_target=("derek@example.com", "mail-1", "Approval request"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.mail_result = {
        "ok": True,
        "result": {"messageId": "mail-receipt-1"},
    }
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_mail_reply(execution) is True
    assert worker.execute_universal_mail_reply(execution) is True

    assert dws.mail_replies == [
        ("derek@example.com", "mail-1", "Approval request", "Approved.")
    ]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "sent"
    assert json.loads(attempt.mail_action_result_json) == dws.mail_result
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.SUCCEEDED


def test_universal_mail_reply_blocks_spoofed_target(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.MAIL_REPLY,
        target={"mailbox": "attacker@example.com", "message_id": "mail-1", "subject": "S"},
        payload={"content": "Send this."},
        trusted_mail_target=("derek@example.com", "mail-1", "S"),
    )
    dws = UniversalMailCalendarFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_mail_reply(execution) is True
    assert dws.mail_replies == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "untrusted_mail_reply_target"


def test_universal_mail_reply_timeout_is_unknown_and_not_replayed(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.MAIL_REPLY,
        target={"mailbox": "derek@example.com", "message_id": "mail-1", "subject": "S"},
        payload={"content": "Send once."},
        trusted_mail_target=("derek@example.com", "mail-1", "S"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.mail_error = TimeoutError("response timeout")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    worker._notify = lambda **kwargs: None

    with pytest.raises(ReplyDeliveryError):
        worker.execute_universal_mail_reply(execution)
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_mail_reply(execution)

    assert len(dws.mail_replies) == 1
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.UNKNOWN


def test_universal_mail_reply_definite_failure_can_retry_same_execution(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.MAIL_REPLY,
        target={"mailbox": "derek@example.com", "message_id": "mail-1", "subject": "S"},
        payload={"content": "Retry safely."},
        trusted_mail_target=("derek@example.com", "mail-1", "S"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.mail_build_error = ValueError("mail request is invalid")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    worker._notify = lambda **kwargs: None

    with pytest.raises(ValueError, match="mail request is invalid"):
        worker.execute_universal_mail_reply(execution)
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.NOT_STARTED

    dws.mail_build_error = None
    assert worker.execute_universal_mail_reply(execution) is True
    assert len(dws.mail_replies) == 1
    attempts = [
        attempt
        for attempt in store.list_reply_attempts(limit=20)
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].retry_count == 1
    assert attempts[0].send_status == "sent"


@pytest.mark.parametrize(
    "mail_result",
    [
        {"success": False},
        {"ok": True, "result": {}},
        {"ok": False, "result": {"messageId": "not-success"}},
    ],
)
def test_universal_mail_reply_without_success_receipt_is_unknown(
    tmp_path: Path,
    mail_result: dict,
) -> None:
    store = AutoReplyStore(tmp_path / "mail-no-receipt.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.MAIL_REPLY,
        target={"mailbox": "derek@example.com", "message_id": "mail-1", "subject": "S"},
        payload={"content": "Send once."},
        trusted_mail_target=("derek@example.com", "mail-1", "S"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.mail_result = mail_result
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="success receipt"):
        worker.execute_universal_mail_reply(execution)
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.UNKNOWN


def test_universal_calendar_response_prechecks_and_verifies_state(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "accepted"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.calendar_event = DwsCalendarEvent(
        event_id="event-1", organizer="Mina", self_response_status="tentative"
    )
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_calendar_response(execution) is True
    assert worker.execute_universal_calendar_response(execution) is True

    assert dws.calendar_responses == [("event-1", "accepted")]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "calendar"
    assert json.loads(attempt.calendar_response_result_json) == {
        "requestId": "calendar-receipt-1",
        "success": True,
    }


def test_universal_calendar_response_already_set_and_organizer_are_terminal_noops(tmp_path: Path) -> None:
    for organizer_error in (False, True):
        store = AutoReplyStore(tmp_path / f"calendar-{organizer_error}.sqlite3")
        execution = _execution(
            store,
            kind=PlannedActionKind.CALENDAR_RESPONSE,
            target={"event_id": "event-1"},
            payload={"response_status": "accepted"},
            trusted_calendar_target=("event-1", "accepted" if not organizer_error else "tentative", "Mina"),
        )
        dws = UniversalMailCalendarFakeDws()
        dws.calendar_event = DwsCalendarEvent(
            event_id="event-1",
            organizer="Mina",
            self_response_status="accepted" if not organizer_error else "tentative",
        )
        if organizer_error:
            dws.calendar_error = RuntimeError("Cannot change response status of event organizer")
        worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

        assert worker.execute_universal_calendar_response(execution) is True
        attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
        assert attempt is not None
        result = json.loads(attempt.calendar_response_result_json)
        assert result["noop_reason"] in {
            "calendar_response_already_set",
            "calendar_event_organizer",
        }


def test_universal_calendar_response_blocks_spoof_and_marks_postcheck_mismatch_unknown(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "calendar-spoof.sqlite3")
    spoof = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-other"},
        payload={"response_status": "declined"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    assert worker.execute_universal_calendar_response(spoof) is True
    assert dws.calendar_responses == []

    mismatch_store = AutoReplyStore(tmp_path / "calendar-mismatch.sqlite3")
    mismatch = _execution(
        mismatch_store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "declined"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws.calendar_event = DwsCalendarEvent(event_id="event-1", self_response_status="tentative")
    dws.apply_calendar_response = False
    mismatch_worker = DingTalkAutoReplyWorker(store=mismatch_store, dws=dws, codex=FakeCodex())
    mismatch_worker._notify = lambda **kwargs: None
    with pytest.raises(ReplyDeliveryError):
        mismatch_worker.execute_universal_calendar_response(mismatch)
    assert mismatch_store.get_universal_action_execution_state(mismatch) is UniversalActionExecutionState.UNKNOWN
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        mismatch_worker.execute_universal_calendar_response(mismatch)
    assert dws.calendar_responses == [("event-1", "declined")]


def test_universal_calendar_timeout_is_unknown_and_not_replayed(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "calendar-timeout.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "accepted"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.calendar_event = DwsCalendarEvent(event_id="event-1", self_response_status="tentative")
    dws.calendar_error = TimeoutError("calendar response timeout")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    worker._notify = lambda **kwargs: None

    with pytest.raises(ReplyDeliveryError):
        worker.execute_universal_calendar_response(execution)
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_calendar_response(execution)

    assert dws.calendar_responses == [("event-1", "accepted")]
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.UNKNOWN


def test_universal_calendar_preflight_timeout_is_definite_and_retryable(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "calendar-preflight-timeout.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "accepted"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.calendar_get_error = TimeoutError("calendar read timeout")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(TimeoutError):
        worker.execute_universal_calendar_response(execution)
    assert dws.calendar_responses == []
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.NOT_STARTED

    dws.calendar_get_error = None
    dws.calendar_event = DwsCalendarEvent(event_id="event-1", self_response_status="accepted")
    assert worker.execute_universal_calendar_response(execution) is True
    assert dws.calendar_responses == []


def test_universal_calendar_missing_live_state_fails_before_external_call(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "calendar-no-readback.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "accepted"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.calendar_event = None
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="live state unavailable"):
        worker.execute_universal_calendar_response(execution)
    assert dws.calendar_responses == []
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.NOT_STARTED


def test_universal_calendar_live_state_disappears_after_call_is_unknown(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "calendar-disappears.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.CALENDAR_RESPONSE,
        target={"event_id": "event-1"},
        payload={"response_status": "accepted"},
        trusted_calendar_target=("event-1", "tentative", "Mina"),
    )
    dws = UniversalMailCalendarFakeDws()
    dws.calendar_event = DwsCalendarEvent(
        event_id="event-1",
        self_response_status="tentative",
    )
    dws.hide_calendar_after_response = True
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="verification unavailable"):
        worker.execute_universal_calendar_response(execution)
    assert dws.calendar_responses == [("event-1", "accepted")]
    assert store.get_universal_action_execution_state(execution) is UniversalActionExecutionState.UNKNOWN


def test_universal_reply_native_delivery_receives_immutable_sender_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Native reply"},
    )
    dws = NativeReplyFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)
    monkeypatch.setattr("app.worker.feedback_spike_vercel_base_url", lambda: "")

    assert worker.execute_universal_send_reply(execution) is True

    assert len(dws.calls) == 1
    sent = store.get_sent_reply("cid-context", "msg-context")
    assert sent is not None
    assert "Native reply" in sent.reply_text


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.SEND_REPLY,
        PlannedActionKind.ASK_CLARIFYING_QUESTION,
    ],
)
def test_universal_reply_uses_immutable_context_and_completes_after_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PlannedActionKind,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=kind,
        target={
            "conversation_id": "cid-model-must-not-win",
            "trigger_message_id": "msg-model-must-not-win",
        },
        payload={"text": "Reply from plan"},
    )
    worker = _worker(store)
    captured: dict[str, object] = {}

    def fake_send_reply(
        conversation,
        trigger,
        new_messages,
        reply_text,
        reason,
        attempt_id,
        **kwargs,
    ) -> None:
        captured.update(
            conversation=conversation,
            trigger=trigger,
            new_messages=new_messages,
            reply_text=reply_text,
            reason=reason,
            attempt_id=attempt_id,
            kwargs=kwargs,
        )
        store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            send_status="sent",
        )
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
            send_result_json='{"success":true}',
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True

    conversation = captured["conversation"]
    trigger = captured["trigger"]
    assert conversation.open_conversation_id == "cid-context"
    assert trigger.open_message_id == "msg-context"
    assert captured["reply_text"] == "Reply from plan"
    assert captured["reason"] == f"Reason for {kind.value}"
    assert captured["kwargs"] == {"raise_on_delivery_failure": True}
    assert [message.open_message_id for message in captured["new_messages"]] == [
        "msg-context",
    ]
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.action == kind.value
    assert attempt.send_status == "sent"
    event = json.loads(attempt.audit_tool_events_json)[0]
    assert event["execution_id"] == execution.execution_id
    assert event["execution_scope_id"] == execution.execution_scope_id


def test_universal_reply_succeeded_is_idempotent_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply once"},
    )
    worker = _worker(store)
    sends = 0

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs) -> None:
        nonlocal sends
        sends += 1
        store.update_reply_attempt(attempt_id, send_status="sent")
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert worker.execute_universal_send_reply(execution) is True
    assert sends == 1


def test_universal_reply_existing_delivery_completes_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Must be deduplicated"},
    )
    store.record_sent_reply(
        "cid-context",
        "msg-context",
        "Already delivered",
    )
    worker = _worker(store)

    def fail_if_called(*args, **kwargs) -> None:
        raise AssertionError("duplicate delivery must not be attempted")

    monkeypatch.setattr(worker, "_send_reply", fail_if_called)

    assert worker.execute_universal_send_reply(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "duplicate_sent_reply_for_trigger"
    with store._connect() as db:
        row = db.execute(
            "select result_json from universal_action_executions where execution_id=?",
            (execution.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["result_json"] == json.dumps(
        {
            "action_kind": "send_reply",
            "execution_id": execution.execution_id,
            "execution_scope_id": execution.execution_scope_id,
            "outcome": "duplicate_existing_delivery",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_universal_reply_unknown_fails_closed_without_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Must not replay"},
    )
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    store.mark_universal_action_execution_unknown(execution, "delivery uncertain")
    worker = _worker(store)

    def fail_if_called(*args, **kwargs) -> None:
        raise AssertionError("send must not be replayed")

    monkeypatch.setattr(worker, "_send_reply", fail_if_called)

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_send_reply(execution)


def test_universal_reply_exception_salvages_proven_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Delivered before exception"},
    )
    worker = _worker(store)

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs) -> None:
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )
        raise ReplyDeliveryError("post-send verification failed")

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "sent"


def test_universal_reply_exception_without_delivery_marks_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Uncertain reply"},
    )
    worker = _worker(store)

    def fake_send_reply(*args, **kwargs) -> None:
        raise ReplyDeliveryError("network disconnected")

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    with pytest.raises(ReplyDeliveryError, match="network disconnected"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "universal_action_outcome_unknown" in attempt.send_error


def test_universal_reply_permission_refusal_is_sent_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        sensitivity_kind="internal_personnel",
        personnel_subject_user_id="another-user",
        payload={"text": "Sensitive answer"},
    )
    worker = _worker(store)
    evaluated: dict[str, object] = {}

    def evaluate_permission(decision, trigger) -> PermissionResult:
        evaluated.update(decision=decision, trigger=trigger)
        return PermissionResult(
            action=PermissionAction.REPLY,
            reply_text="Safe refusal",
            reason="requester is unrelated",
        )

    worker.permission_gate = type(
        "Gate",
        (),
        {"evaluate": staticmethod(evaluate_permission)},
    )()
    sent_texts: list[str] = []

    def fake_send_reply(conversation, trigger, reply_text, attempt_id, **kwargs):
        sent_texts.append(reply_text)
        store.update_reply_attempt(attempt_id, send_status="sent")
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", fake_send_reply)

    assert worker.execute_universal_send_reply(execution) is True
    assert sent_texts == ["Safe refusal"]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.sensitivity_kind == "internal_personnel"
    assert attempt.permission_action == "reply"
    assert attempt.permission_reason == "requester is unrelated"
    decision = evaluated["decision"]
    assert decision.sensitivity_kind.value == "internal_personnel"
    assert decision.personnel_subject_user_id == "another-user"


def test_universal_reply_permission_error_is_definite_retryable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        sensitivity_kind="external_candidate",
        candidate_context_known=True,
        candidate_department_ids=["dept-1"],
        payload={"text": "Sensitive answer"},
    )
    worker = _worker(store)
    worker.permission_gate = type(
        "Gate",
        (),
        {
            "evaluate": staticmethod(
                lambda decision, trigger: PermissionResult(
                    action=PermissionAction.ERROR,
                    reason="requester identity unavailable",
                )
            )
        },
    )()
    monkeypatch.setattr(
        worker,
        "_send_reply",
        lambda *args, **kwargs: pytest.fail("permission error must not send"),
    )

    with pytest.raises(RuntimeError, match="requester identity unavailable"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.permission_action == "error"
    assert attempt.send_status == "failed"


def test_universal_reply_recipient_preflight_failure_is_not_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])
    monkeypatch.setattr(
        worker,
        "_default_reply_at_targets",
        lambda trigger: (_ for _ in ()).throw(RuntimeError("recipient lookup failed")),
    )
    monkeypatch.setattr(
        worker,
        "_send_reply",
        lambda *args, **kwargs: pytest.fail("recipient failure must not send"),
    )

    with pytest.raises(RuntimeError, match="recipient lookup failed"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


def test_universal_reply_second_recipient_resolution_failure_is_not_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply"},
    )
    worker = _worker(store)
    resolution_calls = 0

    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])

    def resolve_targets(trigger):
        nonlocal resolution_calls
        resolution_calls += 1
        if resolution_calls == 1:
            return []
        raise RuntimeError("late recipient lookup failed")

    monkeypatch.setattr(worker, "_default_reply_at_targets", resolve_targets)
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)

    with pytest.raises(ReplyDeliveryError, match="late recipient lookup failed"):
        worker.execute_universal_send_reply(execution)

    assert resolution_calls == 2
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "late recipient lookup failed"


def test_universal_reply_leak_check_block_is_definite_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": f"Blocked reply {FORBIDDEN_MARKERS[0]}"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_regenerate_reply_after_leak_check", lambda **kwargs: "")
    monkeypatch.setattr(worker, "_notify", lambda **kwargs: None)
    monkeypatch.setattr("app.worker.feedback_spike_vercel_base_url", lambda: "")

    with pytest.raises(ReplyDeliveryError, match="leak_check"):
        worker.execute_universal_send_reply(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )


@pytest.mark.parametrize(
    ("kind", "send_status"),
    [
        (PlannedActionKind.NO_REPLY, "skipped"),
        (PlannedActionKind.HANDOFF_TO_HUMAN, "skipped"),
        (PlannedActionKind.BLOCKED, "blocked"),
        (PlannedActionKind.STOP_WITH_ERROR, "blocked"),
    ],
)
def test_universal_terminal_actions_record_attempt_and_complete(
    tmp_path: Path,
    kind: PlannedActionKind,
    send_status: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=kind)
    worker = _worker(store)

    assert worker.execute_universal_terminal_action(execution) is True

    if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
        assert any(call[0] == "ding_self" for call in worker.dws.calls)
    else:
        assert worker.dws.calls == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.action == kind.value
    assert attempt.send_status == send_status
    assert attempt.send_error == f"{kind.value}: Reason for {kind.value}"
    event = json.loads(attempt.audit_tool_events_json)[0]
    assert event["execution_id"] == execution.execution_id
    if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
        handoff_event = json.loads(attempt.audit_tool_events_json)[-1]
        assert handoff_event["tool"] == "universal_handoff"
        assert json.loads(handoff_event["output"])["notification_invoked"] is True
    expected_seen = kind is not PlannedActionKind.STOP_WITH_ERROR
    assert store.has_seen("msg-context") is expected_seen


@pytest.mark.parametrize(
    "kind",
    [
        PlannedActionKind.NO_REPLY,
        PlannedActionKind.HANDOFF_TO_HUMAN,
        PlannedActionKind.BLOCKED,
    ],
)
def test_universal_terminal_side_effects_happen_before_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: PlannedActionKind,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=kind)
    worker = _worker(store)
    enqueued: list[int] = []
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: enqueued.append(kwargs["attempt_id"]),
    )
    original_complete = store.complete_universal_action_execution

    def assert_side_effects_before_complete(*args, **kwargs):
        assert store.has_seen("msg-context") is True
        assert enqueued
        if kind is PlannedActionKind.HANDOFF_TO_HUMAN:
            assert any(call[0] == "ding_self" for call in worker.dws.calls)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "complete_universal_action_execution",
        assert_side_effects_before_complete,
    )

    assert worker.execute_universal_terminal_action(execution) is True


def test_universal_no_reply_local_queue_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    assert store.has_seen("msg-context") is False
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "queue unavailable"


def test_universal_handoff_local_failure_precedes_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.HANDOFF_TO_HUMAN)
    worker = _worker(store)
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )
    monkeypatch.setattr(
        worker,
        "_execute_message_reactions",
        lambda **kwargs: pytest.fail("reaction must not start"),
    )
    monkeypatch.setattr(
        worker,
        "_notify_handoff",
        lambda **kwargs: pytest.fail("notification must not start"),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    assert worker.dws.calls == []
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "queue unavailable"


def test_universal_handoff_failure_after_notification_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.HANDOFF_TO_HUMAN)
    worker = _worker(store)
    monkeypatch.setattr(worker, "_execute_message_reactions", lambda **kwargs: True)

    def notify_then_fail(**kwargs):
        worker.dws.ding_self("handoff started")
        raise RuntimeError("notification outcome uncertain")

    monkeypatch.setattr(worker, "_notify_handoff", notify_then_fail)

    with pytest.raises(RuntimeError, match="notification outcome uncertain"):
        worker.execute_universal_terminal_action(execution)

    assert store.has_seen("msg-context") is True
    assert worker.dws.calls == [("ding_self", "handoff started")]
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert "universal_action_outcome_unknown" in attempt.send_error


def test_universal_terminal_retry_reuses_owned_attempt_and_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    enqueue_work_item = worker._enqueue_conversation_work_item
    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        worker.execute_universal_terminal_action(execution)

    first_attempt = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert first_attempt is not None
    assert first_attempt.send_status == "failed"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    monkeypatch.setattr(
        worker,
        "_enqueue_conversation_work_item",
        enqueue_work_item,
    )

    assert worker.execute_universal_terminal_action(execution) is True

    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].id == first_attempt.id
    assert attempts[0].retry_count == 1
    assert attempts[0].send_status == "skipped"
    assert attempts[0].send_error == "no_reply: Reason for no_reply"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_reply_pre_send_retry_reuses_owned_attempt_and_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.SEND_REPLY,
        payload={"text": "Reply after retry"},
    )
    worker = _worker(store)
    monkeypatch.setattr(worker, "_explicit_reply_at_targets", lambda *args: [])
    monkeypatch.setattr(
        worker,
        "_default_reply_at_targets",
        lambda trigger: (_ for _ in ()).throw(RuntimeError("recipient unavailable")),
    )

    with pytest.raises(ReplyDeliveryError, match="recipient unavailable"):
        worker.execute_universal_send_reply(execution)

    first_attempt = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert first_attempt is not None
    assert first_attempt.send_status == "failed"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    monkeypatch.setattr(worker, "_default_reply_at_targets", lambda trigger: [])

    def deliver(conversation, trigger, reply_text, attempt_id, **kwargs):
        store.update_reply_attempt(
            attempt_id,
            final_reply_text=reply_text,
            send_status="sent",
        )
        store.record_sent_reply(
            conversation.open_conversation_id,
            trigger.open_message_id,
            reply_text,
        )

    monkeypatch.setattr(worker, "_send_reply", deliver)

    assert worker.execute_universal_send_reply(execution) is True

    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].id == first_attempt.id
    assert attempts[0].retry_count == 1
    assert attempts[0].send_status == "sent"
    assert attempts[0].send_error == ""
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_concurrent_universal_attempt_retry_reuses_one_owned_row(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    worker = _worker(store)
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )
    first_attempt_id = worker._record_universal_reply_attempt(
        execution,
        send_status="skipped",
        send_error="first failure",
    )
    store.mark_universal_action_execution_failed(execution, "first failure")
    assert (
        store.claim_universal_action_execution(execution)
        is UniversalActionExecutionState.NOT_STARTED
    )

    def record_retry(_: int) -> int:
        return worker._record_universal_reply_attempt(
            execution,
            send_status="skipped",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_attempt_ids = list(pool.map(record_retry, range(2)))

    assert retry_attempt_ids == [first_attempt_id, first_attempt_id]
    attempts = [
        attempt
        for attempt in store.list_reply_attempts()
        if attempt.universal_execution_id == execution.execution_id
    ]
    assert len(attempts) == 1
    assert attempts[0].universal_execution_scope_id == execution.execution_scope_id
    assert attempts[0].conversation_id == execution.context.conversation_id
    assert attempts[0].trigger_message_id == execution.context.trigger_message_id
    assert attempts[0].action == execution.action.kind.value


def test_universal_terminal_unknown_fails_closed_without_new_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    store.claim_universal_action_execution(execution)
    worker = _worker(store)

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_terminal_action(execution)

    assert store.list_reply_attempts() == []


def test_universal_terminal_preserves_unrelated_prior_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    prior_id = store.record_reply_attempt(
        conversation_id="cid-context",
        conversation_title="Original title",
        trigger_message_id="msg-context",
        trigger_sender="Original sender",
        trigger_text="Original trigger",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="Original failure",
        draft_reply_text="Original draft",
        audit_summary="Original audit",
        send_status="failed",
    )
    store.update_reply_attempt(prior_id, send_error="original_send_error")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)

    assert _worker(store).execute_universal_terminal_action(execution) is True

    attempts = store.list_reply_attempts()
    assert len(attempts) == 2
    original = next(attempt for attempt in attempts if attempt.id == prior_id)
    universal = next(attempt for attempt in attempts if attempt.id != prior_id)
    assert original.action == "send_reply"
    assert original.codex_reason == "Original failure"
    assert original.draft_reply_text == "Original draft"
    assert original.audit_summary == "Original audit"
    assert original.send_status == "failed"
    assert original.send_error == "original_send_error"
    assert universal.action == "no_reply"
    assert universal.universal_execution_id == execution.execution_id
    assert universal.universal_execution_scope_id == execution.execution_scope_id


def test_legacy_trigger_attempt_does_not_overwrite_universal_owned_attempt(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(store, kind=PlannedActionKind.NO_REPLY)
    assert _worker(store).execute_universal_terminal_action(execution) is True
    universal = store.get_latest_reply_attempt_for_trigger(
        "cid-context", "msg-context"
    )
    assert universal is not None

    legacy_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-context",
        conversation_title="Legacy title",
        trigger_message_id="msg-context",
        trigger_sender="Legacy sender",
        trigger_text="Legacy trigger",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="Legacy retry",
        send_status="pending",
    )

    assert legacy_id != universal.id
    preserved = store.get_reply_attempt(universal.id)
    assert preserved is not None
    assert preserved.action == "no_reply"
    assert preserved.universal_execution_id == execution.execution_id
    assert preserved.send_status == "skipped"


@pytest.mark.parametrize("action", ["同意", "拒绝", "退回"])
def test_universal_oa_owner_action_executes_and_verifies_final_state(
    tmp_path: Path,
    action: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={
            "action": action,
            "remark": "按已核实材料处理",
            **(
                {
                    "target_activity_id": "activity-1",
                    "revert_action": "REVERT_FOR_APPROVAL",
                }
                if action == "退回"
                else {}
            ),
        },
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    if action == "退回":
        assert dws.action_calls == []
        assert dws.revert_calls == [
            ("proc-1", "task-1", "activity-1", "REVERT_FOR_APPROVAL", "按已核实材料处理")
        ]
    else:
        assert dws.action_calls == [
            ("proc-1", "task-1", "通过" if action == "同意" else action, "按已核实材料处理")
        ]
    assert dws.comment_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_action == action
    assert attempt.oa_remark == "按已核实材料处理"
    result = json.loads(attempt.oa_action_result_json)
    assert result["outcome"] == "applied"
    assert result["dws_action_result"]["requestId"].startswith("request-")
    assert store.has_seen("msg-context") is True
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


def test_universal_oa_comment_uses_comment_api_and_completes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "comment", "remark": "请补充可验证材料"},
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.comment_calls == [("proc-1", "请补充可验证材料")]
    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "commented"
    result = json.loads(attempt.oa_action_result_json)
    assert result["outcome"] == "commented"
    assert result["dws_action_result"]["requestId"] == "request-comment-1"


def test_universal_oa_comment_allows_trusted_process_without_task_id(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1"},
        payload={"action": "comment", "remark": "请补充可验证材料"},
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.comment_calls == [("proc-1", "请补充可验证材料")]
    assert dws.action_calls == []
    assert dws.revert_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "commented"
    assert attempt.send_error == ""
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == ""


def test_universal_oa_comment_falls_back_to_tasks_when_detail_parse_fails(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "comment", "remark": "请补充可验证材料"},
    )
    dws = UniversalOaFakeDws()

    def detail_parse_failure(process_instance_id: str) -> dict:
        del process_instance_id
        raise RuntimeError("DWS detail response parse failed")

    dws.read_oa_approval_detail = detail_parse_failure
    dws.read_oa_approval_tasks = lambda process_id: {
        "result": {"taskIdList": [{"taskId": "task-1"}]}
    }
    dws.read_oa_approval_records = lambda process_id: {
        "result": {
            "processInstanceId": process_id,
            "operationRecords": [],
        }
    }
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.comment_calls == [("proc-1", "请补充可验证材料")]
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "commented"
    assert attempt.send_error == ""


@pytest.mark.parametrize(
    "comment_result",
    [
        {"success": False, "error": "comment failed"},
        {"errorCode": "500", "errorMessage": "unknown result"},
        {},
        {"result": []},
    ],
)
def test_universal_oa_comment_without_explicit_receipt_is_unknown(
    tmp_path: Path,
    comment_result: dict,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "comment", "remark": "请补充材料"},
    )
    dws = UniversalOaFakeDws()
    dws.comment_result = comment_result
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="verifiable receipt"):
        worker.execute_universal_oa_approval(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "unknown"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )


def test_universal_oa_post_call_failure_payload_salvages_only_from_live_proof(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "拒绝", "remark": "材料不成立"},
    )
    dws = UniversalOaFakeDws()
    dws.action_result = {"success": False, "error": "transport response invalid"}
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "salvaged"


def test_universal_oa_post_call_invalid_payload_without_live_proof_is_unknown(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    dws.action_result = []
    dws.hide_task_after_apply = True
    dws.record_action_after_apply = False
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="verifiable receipt"):
        worker.execute_universal_oa_approval(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "unknown"


def test_universal_oa_value_error_after_mutating_call_is_unknown_without_proof(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    dws.hide_task_after_apply = True
    dws.action_value_error_after_apply = True
    dws.record_action_after_apply = False
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ValueError, match="after OA request"):
        worker.execute_universal_oa_approval(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "unknown"
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )


def test_universal_oa_value_error_before_revert_call_is_final_blocked(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={
            "action": "退回",
            "remark": "请补充材料",
            "target_activity_id": "activity-1",
            "revert_action": "REVERT_FOR_APPROVAL",
        },
    )
    dws = UniversalOaFakeDws()

    def invalid_material(task_id: str) -> dict:
        raise ValueError("invalid revert material")

    dws.read_oa_revert_activities = invalid_material
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.revert_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert "oa_revert_material_invalid" in attempt.send_error


def test_universal_oa_requires_trusted_context_target_before_dws_read(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-model", "task_id": "task-model"},
        payload={"action": "同意", "remark": "同意"},
        trusted_oa_target=False,
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "missing_trusted_oa_target"


def test_universal_oa_rejects_model_target_mismatch_before_dws_read(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-model", "task_id": "task-model"},
        payload={"action": "同意", "remark": "同意"},
        trusted_oa_process_instance_id="proc-trigger",
        trusted_oa_task_id="task-trigger",
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True
    assert dws.calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_error == "oa_target_mismatch"


def test_universal_oa_dws_mock_empty_results_are_missing_preflight_evidence(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    empty = {"_mock": True, "result": [], "success": True}
    dws.read_oa_approval_detail = lambda process_id: dict(empty)
    dws.read_oa_approval_tasks = lambda process_id: dict(empty)
    dws.read_oa_approval_records = lambda process_id: dict(empty)
    dws.read_oa_process_instance_openapi = lambda process_id: dict(empty)
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True
    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "missing_oa_task_ownership"


def test_universal_oa_completed_process_without_current_user_ownership_is_blocked(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws(owner="other-user", process_status="COMPLETED")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "oa_task_not_current_user"


@pytest.mark.parametrize(
    ("target", "owner", "expected_error"),
    [
        ({"task_id": "task-1"}, "principal-user-1", "missing_trusted_oa_target"),
        ({"process_instance_id": "proc-1"}, "principal-user-1", "missing_trusted_oa_target"),
        (
            {"process_instance_id": "proc-1", "task_id": "task-1"},
            "other-user",
            "oa_task_not_current_user",
        ),
        (
            {"process_instance_id": "proc-1", "task_id": "task-1"},
            "",
            "missing_oa_task_ownership",
        ),
    ],
)
def test_universal_oa_preflight_failure_is_final_without_external_action(
    tmp_path: Path,
    target: dict[str, str],
    owner: str,
    expected_error: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target=target,
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws(owner=owner)
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.action_calls == []
    assert dws.comment_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == expected_error
    assert store.has_seen("msg-context") is True
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.SUCCEEDED
    )


@pytest.mark.parametrize(
    ("task_status", "tasks_owner", "expected_error"),
    [
        ("", "principal-user-1", "missing_oa_task_status"),
        ("RUNNING", "other-user", "oa_task_ownership_conflict"),
    ],
)
def test_universal_oa_incomplete_or_conflicting_live_material_is_blocked(
    tmp_path: Path,
    task_status: str,
    tasks_owner: str,
    expected_error: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws(task_status=task_status)
    dws.tasks_owner = tasks_owner
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == expected_error


def test_universal_oa_already_handled_is_idempotent_success(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws(task_status="COMPLETED", process_status="COMPLETED")
    dws.records = [{
        "taskId": "task-1",
        "userId": "principal-user-1",
        "operationType": "通过",
    }]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "oa_already_handled"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "already_handled"


@pytest.mark.parametrize(
    ("expected_action", "recorded_action"),
    [
        ("拒绝", "通过"),
        ("同意", "拒绝"),
    ],
)
def test_universal_oa_preflight_different_recorded_action_is_terminal_not_success(
    tmp_path: Path,
    expected_action: str,
    recorded_action: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": expected_action, "remark": "按规则处理"},
    )
    dws = UniversalOaFakeDws(task_status="COMPLETED", process_status="COMPLETED")
    dws.records = [{
        "taskId": "task-1",
        "userId": "principal-user-1",
        "operationType": recorded_action,
    }]
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "oa_handled_by_different_action"
    assert (
        json.loads(attempt.oa_action_result_json)["outcome"]
        == "handled_by_different_action"
    )


def test_universal_oa_completed_task_without_action_record_is_not_expected_success(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws(task_status="COMPLETED", process_status="COMPLETED")
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.action_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "oa_terminal_action_unverified"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "blocked"


def test_universal_oa_transport_error_is_salvaged_from_live_state(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "拒绝", "remark": "材料不成立"},
    )
    dws = UniversalOaFakeDws()
    dws.raise_after_apply = True
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "salvaged"


@pytest.mark.parametrize(
    ("expected_action", "recorded_action"),
    [
        ("拒绝", "通过"),
        ("同意", "拒绝"),
    ],
)
def test_universal_oa_salvage_different_recorded_action_is_not_expected_success(
    tmp_path: Path,
    expected_action: str,
    recorded_action: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": expected_action, "remark": "按规则处理"},
    )
    dws = UniversalOaFakeDws()
    dws.recorded_action_override = recorded_action
    dws.raise_after_apply = True
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert len(dws.action_calls) == 1
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "oa_handled_by_different_action"
    assert (
        json.loads(attempt.oa_action_result_json)["outcome"]
        == "handled_by_different_action"
    )


def test_universal_oa_missing_task_after_action_is_unknown_without_explicit_proof(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    dws.hide_task_after_apply = True
    dws.record_action_after_apply = False
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="verifiable final state"):
        worker.execute_universal_oa_approval(execution)

    assert dws.process_status == "RUNNING"
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "unknown"


def test_universal_oa_completed_process_with_disappeared_task_is_still_unknown(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    dws.hide_task_after_apply = True
    dws.complete_process_when_hiding = True
    dws.record_action_after_apply = False
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(ReplyDeliveryError, match="verifiable final state"):
        worker.execute_universal_oa_approval(execution)

    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert json.loads(attempt.oa_action_result_json)["outcome"] == "unknown"


def test_universal_oa_return_requires_selected_activity_to_allow_revert_action(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={
            "action": "退回",
            "remark": "请补充材料",
            "target_activity_id": "activity-1",
            "revert_action": "REVERT_FOR_RESUBMIT",
        },
    )
    dws = UniversalOaFakeDws()
    dws.revert_activities = {
        "success": True,
        "result": [{
            "activityId": "activity-1",
            "revertActions": ["REVERT_FOR_APPROVAL"],
        }],
    }
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True

    assert dws.revert_calls == []
    attempt = store.get_latest_reply_attempt_for_trigger("cid-context", "msg-context")
    assert attempt is not None
    assert attempt.send_status == "blocked"
    assert attempt.send_error == "missing_oa_revert_material"


def test_universal_oa_ambiguous_transport_error_marks_unknown_and_retry_fails_closed(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    dws.raise_without_apply = True
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    with pytest.raises(TimeoutError, match="approval response timeout"):
        worker.execute_universal_oa_approval(execution)

    assert len(dws.action_calls) == 1
    assert (
        store.get_universal_action_execution_state(execution)
        is UniversalActionExecutionState.UNKNOWN
    )
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        worker.execute_universal_oa_approval(execution)
    assert len(dws.action_calls) == 1


def test_universal_oa_succeeded_retry_is_noop(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    execution = _execution(
        store,
        kind=PlannedActionKind.OA_APPROVAL,
        target={"process_instance_id": "proc-1", "task_id": "task-1"},
        payload={"action": "同意", "remark": "同意"},
    )
    dws = UniversalOaFakeDws()
    worker = DingTalkAutoReplyWorker(store=store, dws=dws, codex=FakeCodex())

    assert worker.execute_universal_oa_approval(execution) is True
    assert worker.execute_universal_oa_approval(execution) is True

    assert len(dws.action_calls) == 1
    attempts = [
        attempt for attempt in store.list_reply_attempts() if attempt.action == "oa_approval"
    ]
    assert len(attempts) == 1
