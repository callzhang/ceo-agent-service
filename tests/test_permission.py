from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkMessage,
    SensitivityKind,
)
from app.permission import PermissionAction, PermissionGate


def trigger() -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="Friday",
        single_chat=True,
        sender_name="HR",
        sender_user_id="hr-user-1",
        create_time="2026-05-13 18:00:00",
        content="张三转正怎么看？",
    )


def test_internal_personnel_private_requester_cannot_receive_other_person_reply():
    class Profile:
        user_id = "subject-user-1"

    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_profile(self, user_id):
            return Profile()

        def user_in_manager_chain(self, manager_user_id, subject_user_id):
            raise RuntimeError("manager chain should not be called")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.REPLY
    assert "其他人的人事信息" in result.reply_text
    assert result.reason == "private requester is not personnel subject"


def test_internal_personnel_unknown_subject_id_errors_instead_of_refusing():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_profile(self, user_id):
            raise RuntimeError("profile not found")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="calendar-uid-2287838390",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "invalid personnel subject user id" in result.reason
    assert result.reply_text == ""


def test_internal_personnel_hr_private_requester_can_receive_other_person_reply():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return True

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ALLOW


def test_internal_personnel_hr_private_requester_can_receive_reply_without_subject():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return True

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ALLOW


def test_internal_personnel_private_request_without_subject_refuses_instead_of_asking():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
        ),
        trigger(),
    )

    assert result.action == PermissionAction.REPLY
    assert "其他人的人事信息" in result.reply_text
    assert result.reason == "missing personnel subject"


def test_internal_personnel_subject_can_receive_reply_about_self():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            raise RuntimeError("HR membership should not be needed")

        def user_in_manager_chain(self, manager_user_id, subject_user_id):
            raise RuntimeError("manager chain should not be needed")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="hr-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ALLOW


def test_internal_personnel_sender_resolution_failure_is_error():
    class Dws:
        def resolve_message_sender(self, message):
            raise RuntimeError("sender identity source is not configured")

        def is_hr_user(self, user_id):
            raise RuntimeError("HR membership should not be needed")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "sender identity" in result.reason


def test_candidate_empty_requester_departments_is_error():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_department_ids(self, user_id):
            return set()

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "department" in result.reason


def test_candidate_unknown_context_is_left_to_agent():
    result = PermissionGate(object()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ALLOW
    assert result.reply_text == ""


def test_candidate_known_context_without_department_ids_allows():
    class Dws:
        def resolve_message_sender(self, message):
            raise RuntimeError("not cached")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ALLOW
