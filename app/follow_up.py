import json

from app.store import AutoReplyStore


def _is_low_risk(risk_check_json: str) -> bool:
    try:
        risk = json.loads(risk_check_json or "{}")
    except json.JSONDecodeError:
        return False
    if risk.get("sensitive") is True:
        return False
    if risk.get("owner_in_group") is False:
        return False
    return True


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
        should_send = draft.status == "approved" or (
            auto_send and _is_low_risk(draft.risk_check_json)
        )
        if not should_send:
            continue
        try:
            if draft.target_kind == "direct":
                result = dws.send_message(
                    None,
                    draft.question_text,
                    user_id=draft.owner_user_id or None,
                )
            else:
                result = dws.send_message(
                    draft.target_conversation_id,
                    draft.question_text,
                    at_users=[draft.owner_user_id] if draft.owner_user_id else [],
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
            send_result_json=json.dumps(result or {}, ensure_ascii=False),
            sent_at=now,
        )
        sent += 1
    return sent
