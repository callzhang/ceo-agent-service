import json

from app.store import AutoReplyStore
from app.task_models import ProjectStatus, TodoStatus


def _is_low_risk(risk_check_json: str) -> bool:
    try:
        risk = json.loads(risk_check_json or "{}")
    except json.JSONDecodeError:
        return False
    if risk.get("sensitive") is True:
        return False
    if risk.get("sensitive") is not False:
        return False
    if risk.get("owner_in_group") is not True:
        return False
    return True


def _has_completion_evidence(completion_evidence_json: str) -> bool:
    try:
        evidence = json.loads(completion_evidence_json or "{}")
    except json.JSONDecodeError:
        return bool(completion_evidence_json.strip())
    return bool(evidence)


def _completion_supported_by_current_evidence(store: AutoReplyStore, draft) -> tuple[bool, str]:
    project = store.get_work_project(draft.project_id)
    if project is not None and str(project.status) == ProjectStatus.DONE.value:
        return True, "project status is done"

    if draft.todo_id <= 0:
        return False, ""

    todo = store.get_work_todo(draft.todo_id)
    if todo is None:
        return False, ""
    if str(todo.status) == TodoStatus.DONE.value:
        return True, "todo status is done"
    if _has_completion_evidence(todo.completion_evidence_json):
        return True, "todo has completion evidence"
    return False, ""


def _skip_completed_follow_up(store: AutoReplyStore, draft, *, now: str, reason: str) -> None:
    store.update_follow_up_draft(
        draft.id,
        status="skipped",
        sent_at=now,
        send_result_json=json.dumps(
            {
                "skipped": True,
                "reason": reason,
                "evidence_check": "completion_supported",
            },
            ensure_ascii=False,
        ),
    )


def _owner_open_dingtalk_target(
    dws,
    *,
    owner_user_id: str,
    fallback_name: str,
) -> tuple[str, str]:
    if not owner_user_id:
        return "", fallback_name.strip()
    profile = dws.get_user_profile(owner_user_id)
    return profile.open_dingtalk_id or "", (profile.name or fallback_name).strip()


def process_due_follow_ups(
    store: AutoReplyStore,
    dws,
    *,
    now: str,
    auto_send: bool,
    limit: int = 50,
) -> int:
    sent = 0
    drafts = store.list_follow_up_drafts(
        statuses=("draft", "approved"),
        due_before=now,
        limit=limit,
    )
    for draft in drafts:
        should_send = auto_send and (
            draft.status == "approved"
            or _is_low_risk(draft.risk_check_json)
        )
        if not should_send:
            continue
        completed, reason = _completion_supported_by_current_evidence(store, draft)
        if completed:
            _skip_completed_follow_up(store, draft, now=now, reason=reason)
            continue
        try:
            open_dingtalk_id, at_name = _owner_open_dingtalk_target(
                dws,
                owner_user_id=draft.owner_user_id,
                fallback_name=draft.owner_name,
            )
            at_open_dingtalk_ids = [open_dingtalk_id] if open_dingtalk_id else []
            at_open_dingtalk_names = [at_name] if at_name else []
            if draft.target_conversation_id:
                result = dws.send_message(
                    draft.target_conversation_id,
                    draft.question_text,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    at_open_dingtalk_names=at_open_dingtalk_names,
                )
            else:
                result = dws.send_message(
                    None,
                    draft.question_text,
                    at_open_dingtalk_ids=at_open_dingtalk_ids,
                    user_id=draft.owner_user_id or None,
                )
        except Exception as exc:
            store.update_follow_up_draft(
                draft.id,
                status="failed",
                send_result_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
            store.record_error(
                draft.target_conversation_id,
                None,
                "follow_up",
                str(exc),
            )
            continue
        store.update_follow_up_draft(
            draft.id,
            status="sent",
            send_result_json=json.dumps(
                {
                    "at_open_dingtalk_ids": at_open_dingtalk_ids,
                    "at_open_dingtalk_names": at_open_dingtalk_names,
                    "send_result": result or {},
                },
                ensure_ascii=False,
            ),
            sent_at=now,
        )
        sent += 1
    return sent
