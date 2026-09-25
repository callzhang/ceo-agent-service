"""Send one Task follow-up when Derek clicks 发送催办.

Derek, 2026-09-25: follow-ups are no longer sent automatically. The only send
path is the console button, so there is no due-time sweep, no working-hours
gate, and no Agent review of a failed send. Legacy ``follow_up_drafts`` rows
are history only and are never sent.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.service_message_sender import ServiceMessageSender
from app.store import AutoReplyStore


FOLLOW_UP_SEND_LEASE = timedelta(minutes=5)


class FollowUpNotSendable(ValueError):
    """The follow-up changed or is no longer open, so the click sent nothing."""


def send_business_task_follow_up(
    store: AutoReplyStore,
    dws,
    *,
    follow_up_id: int,
    expected_revision: int,
    now: str,
    feedback_base_url: str = "",
) -> tuple[str, str]:
    """Send one follow-up because Derek clicked 发送催办 (Derek 2026-09-25).

    No working-hours gate and no Agent review: the person clicking decides.
    A send that fails stays ``failed`` with its result, shown on the Task;
    clicking again sends a new revision.
    """
    store.recover_expired_business_task_follow_up_sends(now=now)
    claim_token = str(uuid4())
    revision_uuid = str(uuid4())
    draft = store.claim_business_task_follow_up_for_send(
        follow_up_id=follow_up_id, expected_revision=expected_revision, now=now,
        claim_token=claim_token, lease_owner=f"business-task-follow-up:{uuid4()}",
        lease_until=_lease_until(now, FOLLOW_UP_SEND_LEASE),
        idempotency_uuid=revision_uuid,
    )
    if draft is None:
        raise FollowUpNotSendable("这条催办已变化、已发送或 Task 已关闭，请刷新后再看")
    outcome = _send_claimed_business_task_follow_up(
        store, dws, draft, claim_token=claim_token, revision_uuid=revision_uuid,
        now=now, feedback_base_url=feedback_base_url,
    )
    if outcome is None:
        raise FollowUpNotSendable("这条催办正在被另一处发送，请刷新后再看")
    return outcome


def _send_claimed_business_task_follow_up(
    store: AutoReplyStore, dws, draft, *, claim_token: str, revision_uuid: str,
    now: str, feedback_base_url: str,
) -> tuple[str, str] | None:
    """Send one claimed follow-up; return (status, result_json), or None if lost."""
    if not store.transition_business_task_follow_up_to_sending(
        draft_id=int(draft["id"]), revision=int(draft["revision"]),
        claim_token=claim_token,
    ):
        return None
    try:
        owner_user_id, open_dingtalk_id, at_name = _owner_dingtalk_target(
            store, dws, owner_user_id=str(draft["owner_user_id"]),
            fallback_name=str(draft["owner_name"]),
        )
        if not owner_user_id:
            raise ValueError("Task follow-up owner is unresolved")
        task = store.get_business_task(int(draft["business_task_id"]))
        if task is None or task.status.value not in {"open", "waiting"}:
            raise ValueError("Task follow-up Task is no longer open")
        body = f"**请确认：** {draft['question_text']}\n\n**事项**\n- {task.title}"
        sender = ServiceMessageSender(store=store, dingtalk=dws)
        prepared = sender.prepare(
            channel="dingtalk", delivery_key=f"business-task-follow-up:{revision_uuid}",
            body=body, original_text=body, feedback_base_url=feedback_base_url,
        )
        is_group = draft["target_kind"] == "group"
        receipt = sender.send_dingtalk_prepared(
            prepared,
            conversation_id=str(draft["target_conversation_id"]) if is_group else None,
            at_users=[owner_user_id] if is_group else [],
            at_open_dingtalk_ids=[open_dingtalk_id] if is_group and open_dingtalk_id else [],
            at_open_dingtalk_names=[at_name] if is_group and at_name else [],
            user_id=None if is_group or open_dingtalk_id else owner_user_id,
            open_dingtalk_id=open_dingtalk_id if not is_group else None,
            idempotency_uuid=revision_uuid,
        )
        provider_result = receipt.provider_result
        status = (
            "failed" if isinstance(provider_result, dict)
            and provider_result.get("success") is False else "sent"
        )
        result_json = json.dumps(
            {"send_result": provider_result or {},
             "target_conversation_id": draft["target_conversation_id"],
             "idempotency_uuid": revision_uuid,
             "delivered_text": prepared.final_body}, ensure_ascii=False,
        )
    except Exception as exc:
        # A call may have reached DingTalk before its exception was observed.
        # Retain the uncertain attempt for explicit receipt reconciliation.
        status = "unknown"
        result_json = json.dumps({"error": str(exc), "idempotency_uuid": revision_uuid}, ensure_ascii=False)
    finished = store.finish_business_task_follow_up_send(
        draft_id=int(draft["id"]), revision=int(draft["revision"]),
        claim_token=claim_token, status=status,
        result_json=result_json, now=now,
    )
    if not finished:
        return None
    return status, result_json


def _parse_follow_up_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _lease_until(now: str, duration: timedelta) -> str:
    current = _parse_follow_up_datetime(now) or datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    return (current + duration).strftime("%Y-%m-%d %H:%M:%S")


def _owner_dingtalk_target(
    store: AutoReplyStore,
    dws,
    *,
    owner_user_id: str,
    fallback_name: str,
) -> tuple[str, str, str]:
    owner_user_id = owner_user_id.strip()
    fallback_name = fallback_name.strip()
    if not owner_user_id:
        return "", "", fallback_name
    cached = store.get_org_user_profile(owner_user_id)
    if cached is not None and (cached.open_dingtalk_id or cached.name):
        return owner_user_id, cached.open_dingtalk_id or "", (
            cached.name or fallback_name
        ).strip()
    profile = dws.get_user_profile(owner_user_id)
    return owner_user_id, profile.open_dingtalk_id or "", (
        profile.name or fallback_name
    ).strip()
