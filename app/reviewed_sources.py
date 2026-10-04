"""Capture and reread concrete source facts bound into a reviewed candidate."""
from app.agent_contracts import ConsumerAgentResult, ReviewedSourceBinding
from app.agent_result import AgentError
from app.dws_client import DwsError


class ReviewedSourceReadError(ValueError):
    """Preparation could not read the original candidate source."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        authorization = isinstance(cause, DwsError) and (cause.needs_authorization or cause.needs_login)
        self.error = AgentError(
            code="authorization_required" if authorization else "provider_read_failed",
            retryable=(cause.retryable_external_dependency if isinstance(cause, DwsError) else True),
            authorization_required=authorization,
            stage="candidate_source",
            source=str(getattr(cause, "server_key", "") or type(cause).__name__),
            source_code=str(getattr(cause, "code", "") or "reviewed_source_unavailable"),
            session_continuable=True,
        )


def context_source(context):
    return {
        "trigger_text": context.trigger_text,
        "trigger_raw_payload": context.trigger_raw_payload,
        "messages": [{"message_id": m.message_id, "sender": m.sender, "text": m.text} for m in context.messages],
        "materials": [{"kind": m.kind, "reference": m.reference, "source_message_id": m.source_message_id} for m in context.materials],
    }


def capture_candidate_sources(candidate: ConsumerAgentResult, context, dws):
    bindings = [ReviewedSourceBinding(provider="task_context", object_ref=context.trigger_message_id, value=context_source(context))]
    plans = [candidate.proposal] if candidate.proposal else []
    plans.extend(option.plan for option in candidate.decision_options if option.plan)
    objects = set()
    for plan in plans:
        for action in plan.actions:
            if action.capability == "dingtalk-oa":
                ref = action.target.get("process_instance_id")
                provider = "dingtalk-oa"
            elif action.capability == "dingtalk-doc":
                ref = action.target.get("node_id")
                provider = "dingtalk-doc"
            else:
                continue
            if not isinstance(ref, str) or not ref or (provider, ref) in objects:
                continue
            objects.add((provider, ref))
            try:
                value = read_provider_source(dws, provider, ref)
            except (DwsError, ValueError) as exc:
                raise ReviewedSourceReadError(exc) from exc
            bindings.append(ReviewedSourceBinding(provider=provider, object_ref=ref, value=value))
    return candidate.model_copy(update={"source_bindings": tuple(bindings)})


def read_provider_source(dws, provider, object_ref):
    if dws is None:
        raise ValueError("reviewed source client unavailable")
    if provider == "dingtalk-oa":
        data = dws.read_oa_approval_detail(object_ref)
        if isinstance(data, dict) and data.get("success") is False:
            code = data.get("errcode") if "errcode" in data else data.get("errorCode")
            message = data.get("errmsg") if "errmsg" in data else data.get("errorMessage")
            raise DwsError(
                message if isinstance(message, str) and message else "OA source detail unavailable",
                code=str(code) if type(code) in (str, int) else None,
                server_key="dingtalk-oa",
            )
        if not isinstance(data, dict) or data.get("success") is not True or not isinstance(data.get("result"), dict):
            raise ValueError("OA source detail unavailable")
        result = data["result"]
        if result.get("processInstanceId") != object_ref or not isinstance(result.get("formValueVOS"), list):
            raise ValueError("OA source identity or form unavailable")
        # Native provider shape verified read-only: stable original form and
        # applicant/process identity; task transitions have separate handlers.
        return {key: result.get(key) for key in ("processInstanceId", "processCode", "originatorUserid", "originatorDeptId", "processInstanceTitle", "formValueVOS", "attachedProcessInstanceIds")}

    if provider == "dingtalk-doc":
        return dws.read_doc(object_ref)
    raise ValueError("unsupported reviewed source provider")


def changed_candidate_sources(candidate, context, dws, *, check_provider=True):
    changes = []
    for binding in candidate.source_bindings:
        if binding.provider == "task_context":
            if context is None or context.trigger_message_id != binding.object_ref:
                raise ValueError("reviewed source context unavailable")
            actual = context_source(context)
        elif check_provider:
            actual = read_provider_source(dws, binding.provider, binding.object_ref)
        else:
            continue
        if actual != binding.value:
            changes.append({"provider": binding.provider, "object_ref": binding.object_ref,
                            "reviewed_value": binding.value, "current_value": actual})
    return changes
