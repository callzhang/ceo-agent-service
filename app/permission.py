from enum import StrEnum

from pydantic import BaseModel

from app.dingtalk_models import (
    CodexDecision,
    DingTalkMessage,
    SensitivityKind,
)


INTERNAL_PERSONNEL_PRIVATE_REFUSAL = "这个涉及其他人的人事信息，我不能直接回答。"
INTERNAL_PERSONNEL_GROUP_REFUSAL = "这个涉及个人敏感信息，不适合在群里展开，单独同步我。"
CANDIDATE_DEPARTMENT_REFUSAL = "这个候选人信息只回答相关部门的人。"


class PermissionAction(StrEnum):
    ALLOW = "allow"
    REPLY = "reply"
    ERROR = "error"


class PermissionResult(BaseModel):
    action: PermissionAction
    reply_text: str = ""
    reason: str = ""


class PermissionGate:
    def __init__(self, dws):
        self.dws = dws

    def evaluate(
        self, decision: CodexDecision, trigger: DingTalkMessage
    ) -> PermissionResult:
        if decision.sensitivity_kind == SensitivityKind.GENERAL:
            return PermissionResult(action=PermissionAction.ALLOW)
        if decision.sensitivity_kind == SensitivityKind.INTERNAL_PERSONNEL:
            return self._evaluate_internal_personnel(decision, trigger)
        if decision.sensitivity_kind == SensitivityKind.EXTERNAL_CANDIDATE:
            return self._evaluate_external_candidate(decision, trigger)
        return PermissionResult(
            action=PermissionAction.ERROR,
            reason=f"unsupported sensitivity kind: {decision.sensitivity_kind}",
        )

    def _evaluate_internal_personnel(
        self, decision: CodexDecision, trigger: DingTalkMessage
    ) -> PermissionResult:
        if not trigger.single_chat:
            return PermissionResult(action=PermissionAction.ALLOW)
        try:
            requester_user_id = self.dws.resolve_message_sender(trigger)
        except Exception as exc:
            if decision.personnel_subject_user_id:
                return PermissionResult(action=PermissionAction.ERROR, reason=str(exc))
            return PermissionResult(
                action=PermissionAction.REPLY,
                reply_text=INTERNAL_PERSONNEL_PRIVATE_REFUSAL,
                reason="missing personnel subject",
            )
        if (
            decision.personnel_subject_user_id
            and requester_user_id == decision.personnel_subject_user_id
        ):
            return PermissionResult(action=PermissionAction.ALLOW)
        try:
            if self.dws.is_hr_user(requester_user_id):
                return PermissionResult(action=PermissionAction.ALLOW)
        except Exception:
            pass
        if not decision.personnel_subject_user_id:
            return PermissionResult(
                action=PermissionAction.REPLY,
                reply_text=INTERNAL_PERSONNEL_PRIVATE_REFUSAL,
                reason="missing personnel subject",
            )
        subject_error = self._invalid_internal_personnel_subject_reason(
            decision.personnel_subject_user_id
        )
        if subject_error:
            return PermissionResult(
                action=PermissionAction.ERROR,
                reason=subject_error,
            )
        return PermissionResult(
            action=PermissionAction.REPLY,
            reply_text=INTERNAL_PERSONNEL_PRIVATE_REFUSAL,
            reason="private requester is not personnel subject",
        )

    def _invalid_internal_personnel_subject_reason(
        self, personnel_subject_user_id: str
    ) -> str:
        get_user_profile = getattr(self.dws, "get_user_profile", None)
        if get_user_profile is None:
            return "personnel subject profile source is not configured"
        try:
            profile = get_user_profile(personnel_subject_user_id)
        except Exception as exc:
            return f"invalid personnel subject user id: {exc}"
        profile_user_id = getattr(profile, "user_id", None)
        if not profile_user_id:
            return "invalid personnel subject user id: profile id is missing"
        if str(profile_user_id) != personnel_subject_user_id:
            return "invalid personnel subject user id: profile id mismatch"
        return ""

    def _evaluate_external_candidate(
        self, decision: CodexDecision, trigger: DingTalkMessage
    ) -> PermissionResult:
        if not trigger.single_chat:
            return PermissionResult(action=PermissionAction.ALLOW)
        candidate_department_ids = set(decision.candidate_department_ids)
        try:
            requester_user_id = self.dws.resolve_message_sender(trigger)
            if self.dws.is_hr_user(requester_user_id):
                return PermissionResult(action=PermissionAction.ALLOW)
        except Exception as exc:
            if candidate_department_ids:
                return PermissionResult(action=PermissionAction.ERROR, reason=str(exc))
            return PermissionResult(action=PermissionAction.ALLOW)
        if not candidate_department_ids:
            return PermissionResult(action=PermissionAction.ALLOW)
        try:
            requester_department_ids = self.dws.get_user_department_ids(requester_user_id)
        except Exception as exc:
            return PermissionResult(action=PermissionAction.ERROR, reason=str(exc))
        if not requester_department_ids:
            return PermissionResult(
                action=PermissionAction.ERROR,
                reason=f"department data is missing for requester {requester_user_id}",
            )
        if requester_department_ids & candidate_department_ids:
            return PermissionResult(action=PermissionAction.ALLOW)
        return PermissionResult(
            action=PermissionAction.REPLY,
            reply_text=CANDIDATE_DEPARTMENT_REFUSAL,
            reason="requester department is unrelated",
        )
