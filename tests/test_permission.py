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


def test_internal_personnel_missing_profile_source_fails_closed():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            raise RuntimeError("HR unavailable")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "profile source" in result.reason


def test_internal_personnel_profile_identity_must_match_subject():
    class Profile:
        user_id = "different-user"

    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_profile(self, user_id):
            return Profile()

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "profile id mismatch" in result.reason


def test_candidate_hr_is_allowed_and_department_lookup_failure_is_closed():
    class HrDws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return True

    decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
        candidate_department_ids=["dept-a"],
    )
    assert PermissionGate(HrDws()).evaluate(decision, trigger()).action == PermissionAction.ALLOW

    class BrokenDepartmentDws(HrDws):
        def is_hr_user(self, user_id):
            return False

        def get_user_department_ids(self, user_id):
            raise RuntimeError("department unavailable")

    result = PermissionGate(BrokenDepartmentDws()).evaluate(decision, trigger())
    assert result.action == PermissionAction.ERROR
    assert "department unavailable" in result.reason


def test_candidate_sender_resolution_failure_with_known_departments_is_closed():
    class Dws:
        def resolve_message_sender(self, message):
            raise RuntimeError("identity unavailable")

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_department_ids=["dept-a"],
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "identity unavailable" in result.reason


def test_internal_personnel_profile_without_identity_fails_closed():
    class Profile:
        user_id = ""

    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_profile(self, user_id):
            return Profile()

    result = PermissionGate(Dws()).evaluate(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        ),
        trigger(),
    )

    assert result.action == PermissionAction.ERROR
    assert "profile id is missing" in result.reason


def test_unknown_sensitivity_kind_fails_closed():
    decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        sensitivity_kind=SensitivityKind.GENERAL,
    ).model_copy(update={"sensitivity_kind": "future-sensitive-kind"})

    result = PermissionGate(object()).evaluate(decision, trigger())

    assert result.action == PermissionAction.ERROR
    assert "unsupported sensitivity" in result.reason


def test_general_and_group_messages_do_not_apply_private_personnel_checks():
    gate = PermissionGate(object())
    general = CodexDecision(
        action=CodexAction.SEND_REPLY,
        sensitivity_kind=SensitivityKind.GENERAL,
    )
    assert gate.evaluate(general, trigger()).action == PermissionAction.ALLOW

    group_trigger = trigger().model_copy(update={"single_chat": False})
    internal = general.model_copy(
        update={"sensitivity_kind": SensitivityKind.INTERNAL_PERSONNEL}
    )
    candidate = general.model_copy(
        update={"sensitivity_kind": SensitivityKind.EXTERNAL_CANDIDATE}
    )
    assert gate.evaluate(internal, group_trigger).action == PermissionAction.ALLOW
    assert gate.evaluate(candidate, group_trigger).action == PermissionAction.ALLOW


def test_unresolved_requester_without_personnel_subject_gets_safe_refusal():
    class Dws:
        def resolve_message_sender(self, message):
            raise RuntimeError("identity unavailable")

    decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
    )
    result = PermissionGate(Dws()).evaluate(decision, trigger())

    assert result.action == PermissionAction.REPLY
    assert result.reason == "missing personnel subject"


def test_candidate_without_scope_is_allowed_and_matching_department_is_allowed():
    class Dws:
        def resolve_message_sender(self, message):
            return message.sender_user_id

        def is_hr_user(self, user_id):
            return False

        def get_user_department_ids(self, user_id):
            return {"dept-a"}

    base = CodexDecision(
        action=CodexAction.SEND_REPLY,
        sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
    )
    gate = PermissionGate(Dws())

    assert gate.evaluate(base, trigger()).action == PermissionAction.ALLOW
    scoped = base.model_copy(update={"candidate_department_ids": ["dept-a"]})
    assert gate.evaluate(scoped, trigger()).action == PermissionAction.ALLOW
