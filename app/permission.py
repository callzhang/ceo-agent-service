from enum import StrEnum

from pydantic import BaseModel

from ceo_agent_service.dingtalk_models import (
    CodexDecision,
    DingTalkMessage,
    SensitivityKind,
)


INTERNAL_PERSONNEL_CLARIFICATION = "这个是关于谁的问题？"
INTERNAL_PERSONNEL_REFUSAL = "这个涉及内部人事隐私，我不能回答。"
CANDIDATE_DEPARTMENT_CLARIFICATION = "这个候选人是哪个岗位/部门的？"
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
        try:
            requester_user_id = self.dws.resolve_message_sender(trigger)
            if not decision.personnel_subject_user_id:
                if self.dws.is_hr_user(requester_user_id):
                    return PermissionResult(action=PermissionAction.ALLOW)
                return PermissionResult(
                    action=PermissionAction.REPLY,
                    reply_text=INTERNAL_PERSONNEL_CLARIFICATION,
                    reason="missing personnel subject",
                )
            if requester_user_id == decision.personnel_subject_user_id:
                return PermissionResult(action=PermissionAction.ALLOW)
            if self.dws.is_hr_user(requester_user_id):
                return PermissionResult(action=PermissionAction.ALLOW)
            is_manager = self.dws.user_in_manager_chain(
                requester_user_id, decision.personnel_subject_user_id
            )
        except Exception as exc:
            return PermissionResult(action=PermissionAction.ERROR, reason=str(exc))
        if is_manager:
            return PermissionResult(action=PermissionAction.ALLOW)
        return PermissionResult(
            action=PermissionAction.REPLY,
            reply_text=INTERNAL_PERSONNEL_REFUSAL,
            reason="requester is not HR or subject manager",
        )

    def _evaluate_external_candidate(
        self, decision: CodexDecision, trigger: DingTalkMessage
    ) -> PermissionResult:
        candidate_department_ids = set(decision.candidate_department_ids)
        if not decision.candidate_context_known:
            return PermissionResult(
                action=PermissionAction.REPLY,
                reply_text=CANDIDATE_DEPARTMENT_CLARIFICATION,
                reason="missing candidate context",
            )
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
