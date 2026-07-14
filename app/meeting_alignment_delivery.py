from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.dws_client import DwsUserProfile
from app.meeting_alignment_models import (
    MeetingAlignmentDecision,
    MeetingParticipant,
    MeetingSource,
)


class MeetingDeliveryError(RuntimeError):
    """The delivery target contradicts the authoritative meeting source."""


class MeetingDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["sent", "ambiguous"]
    target_kind: Literal["group", "direct"]
    target_id: str
    target_title: str
    resolved_mentions: list["ResolvedMention"]
    unresolved_mention_names: list[str]
    send_result: dict[str, Any]
    send_verification: dict[str, Any]


class MeetingDeliveryRetry(RuntimeError):
    def __init__(
        self, message: str, *, result: MeetingDeliveryResult | None = None
    ) -> None:
        super().__init__(message)
        self.result = result


class MeetingDeliveryAmbiguous(RuntimeError):
    def __init__(self, message: str, *, result: MeetingDeliveryResult) -> None:
        super().__init__(message)
        self.result = result


class ResolvedMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_name: str
    display_name: str
    user_id: str
    open_dingtalk_id: str


class MeetingDeliveryDws(Protocol):
    def get_conversation_info(self, conversation_id: str) -> dict[str, Any]: ...

    def search_user_profiles(self, query: str) -> list[DwsUserProfile]: ...

    def read_recent_messages(
        self, conversation: DingTalkConversation, limit: int = 50
    ) -> list[DingTalkMessage]: ...

    def send_message(
        self, conversation_id: str | None, text: str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def verify_message_send_result(
        self, send_result: dict[str, Any]
    ) -> dict[str, Any]: ...


def deliver_meeting_alignment(
    decision: MeetingAlignmentDecision,
    source: MeetingSource,
    dws: MeetingDeliveryDws,
) -> MeetingDeliveryResult:
    if decision.action != "send":
        raise MeetingDeliveryError("meeting delivery requires a send decision")
    participant_count = len(source.participants)
    if participant_count < 2:
        raise MeetingDeliveryError("meeting delivery requires at least two participants")

    target = decision.target
    recent_messages: list[DingTalkMessage] = []
    if participant_count > 2:
        if target is None:
            raise MeetingDeliveryRetry("multi-party meeting has no sendable group")
        if target.kind == "direct":
            raise MeetingDeliveryError("multi-party meeting cannot use direct delivery")
        if (
            not target.candidates
            or target.candidates[0].conversation_id != target.conversation_id
        ):
            raise MeetingDeliveryError(
                "group delivery must use the first ranked target candidate"
            )
        info = dws.get_conversation_info(target.conversation_id)
        if not _sendable_group_info(info, target.conversation_id):
            raise MeetingDeliveryRetry("selected target is not a sendable group")
        conversation = DingTalkConversation(
            open_conversation_id=target.conversation_id,
            title=str(info.get("title") or target.title),
            single_chat=False,
            unread_point=0,
        )
        recent_messages = dws.read_recent_messages(conversation, limit=50)
        target_kind = "group"
        target_id = target.conversation_id
        direct_user_id = ""
    else:
        counterpart = _one_to_one_counterpart(source)
        if target is None or target.kind != "direct":
            raise MeetingDeliveryError(
                "1:1 meeting requires a direct target for the other participant"
            )
        if _canonical(target.title) != _canonical(counterpart.name):
            raise MeetingDeliveryError(
                "1:1 direct target must name the other participant"
            )
        if counterpart.user_id:
            if target.direct_user_id != counterpart.user_id:
                raise MeetingDeliveryError(
                    "1:1 direct target must use the other participant user id"
                )
            direct_user_id = counterpart.user_id
        else:
            if target.direct_user_id:
                raise MeetingDeliveryError(
                    "unresolved 1:1 target cannot supply a guessed user id"
                )
            profile = _resolve_profile(counterpart.name, counterpart, dws, [])
            if profile is None or not profile.user_id.strip():
                raise MeetingDeliveryRetry("1:1 target identity is unresolved")
            direct_user_id = profile.user_id.strip()
        target_kind = "direct"
        target_id = direct_user_id

    resolved_mentions, unresolved_names = _resolve_mentions(
        decision.mention_names,
        source,
        dws,
        recent_messages,
    )
    mention_ids = [mention.open_dingtalk_id for mention in resolved_mentions]
    mention_display_names = [mention.display_name for mention in resolved_mentions]
    if target_kind == "group":
        send_result = dws.send_message(
            target_id,
            decision.final_message,
            at_open_dingtalk_ids=mention_ids,
            at_open_dingtalk_names=mention_display_names,
            title=target.title if target is not None else source.title,
        )
    else:
        send_result = dws.send_message(
            None,
            decision.final_message,
            user_id=direct_user_id,
            title=target.title if target is not None else source.title,
        )
    verification = dws.verify_message_send_result(send_result)
    result_status = "sent" if verification.get("state") == "sent" else "ambiguous"
    result = MeetingDeliveryResult(
        status=result_status,
        target_kind=target_kind,
        target_id=target_id,
        target_title=target.title if target is not None else source.title,
        resolved_mentions=resolved_mentions,
        unresolved_mention_names=unresolved_names,
        send_result=send_result,
        send_verification=verification,
    )
    if verification.get("state") == "sent":
        return result
    if verification.get("state") == "failed":
        raise MeetingDeliveryRetry("meeting send confirmed failed", result=result)
    raise MeetingDeliveryAmbiguous(
        "meeting send outcome is ambiguous; do not send again immediately",
        result=result,
    )


def _sendable_group_info(info: dict[str, Any], conversation_id: str) -> bool:
    member_count = info.get("memberCount")
    return (
        info.get("openConversationId") == conversation_id
        and info.get("singleChat") is False
        and isinstance(member_count, int)
        and not isinstance(member_count, bool)
        and member_count > 0
    )


def _one_to_one_counterpart(source: MeetingSource) -> MeetingParticipant:
    counterparts = [
        participant
        for participant in source.participants
        if participant.user_id != source.current_user_id
    ]
    if len(counterparts) != 1:
        raise MeetingDeliveryError(
            "1:1 meeting must identify exactly one other participant"
        )
    return counterparts[0]


def _resolve_mentions(
    mention_names: list[str],
    source: MeetingSource,
    dws: MeetingDeliveryDws,
    recent_messages: list[DingTalkMessage],
) -> tuple[list[ResolvedMention], list[str]]:
    resolved: list[ResolvedMention] = []
    unresolved: list[str] = []
    seen_open_ids: set[str] = set()
    for mention_name in mention_names:
        participants = [
            participant
            for participant in source.participants
            if _canonical(participant.name) == _canonical(mention_name)
        ]
        participant = participants[0] if len(participants) == 1 else None
        if participant is not None and participant.open_dingtalk_id.strip():
            mention = ResolvedMention(
                mention_name=mention_name,
                display_name=participant.name,
                user_id=participant.user_id,
                open_dingtalk_id=participant.open_dingtalk_id.strip(),
            )
        else:
            profile = _resolve_profile(
                mention_name,
                participant,
                dws,
                recent_messages,
            )
            if profile is None or not (profile.open_dingtalk_id or "").strip():
                unresolved.append(mention_name)
                continue
            mention = ResolvedMention(
                mention_name=mention_name,
                display_name=profile.name or profile.nick or mention_name,
                user_id=profile.user_id,
                open_dingtalk_id=(profile.open_dingtalk_id or "").strip(),
            )
        if mention.open_dingtalk_id in seen_open_ids:
            continue
        seen_open_ids.add(mention.open_dingtalk_id)
        resolved.append(mention)
    return resolved, unresolved


def _resolve_profile(
    mention_name: str,
    participant: MeetingParticipant | None,
    dws: MeetingDeliveryDws,
    recent_messages: list[DingTalkMessage],
) -> DwsUserProfile | None:
    candidates = dws.search_user_profiles(mention_name)
    if participant is not None and participant.user_id:
        matching_user_id = [
            candidate
            for candidate in candidates
            if candidate.user_id == participant.user_id
        ]
        if len(matching_user_id) == 1:
            return matching_user_id[0]
        return None
    if participant is not None and participant.open_dingtalk_id:
        matching_open_id = [
            candidate
            for candidate in candidates
            if candidate.open_dingtalk_id == participant.open_dingtalk_id
        ]
        if len(matching_open_id) == 1:
            return matching_open_id[0]
        return None

    exact_candidates = [
        candidate
        for candidate in candidates
        if _profile_name_matches(candidate, mention_name)
    ]
    if len(exact_candidates) == 1:
        return exact_candidates[0]

    recent_user_ids = {
        message.sender_user_id
        for message in recent_messages
        if message.sender_user_id
    }
    recent_open_ids = {
        message.sender_open_dingtalk_id
        for message in recent_messages
        if message.sender_open_dingtalk_id
    }
    recent = [
        candidate
        for candidate in exact_candidates
        if candidate.user_id in recent_user_ids
        or candidate.open_dingtalk_id in recent_open_ids
    ]
    if len(recent) == 1:
        return recent[0]

    contextual = [
        candidate
        for candidate in candidates
        if _profile_name_appears_in_context(candidate, mention_name)
        and _profile_context_score(candidate, mention_name) > 0
    ]
    if len(contextual) == 1:
        return contextual[0]
    return None


def _profile_name_matches(profile: DwsUserProfile, value: str) -> bool:
    wanted = _canonical(value)
    return any(
        candidate and candidate == wanted
        for candidate in (_canonical(profile.name), _canonical(profile.nick))
    )


def _profile_name_appears_in_context(
    profile: DwsUserProfile, value: str
) -> bool:
    wanted = _canonical(value)
    return any(
        candidate and candidate in wanted
        for candidate in (_canonical(profile.name), _canonical(profile.nick))
    )


def _profile_context_score(profile: DwsUserProfile, value: str) -> int:
    wanted = _canonical(value)
    context = [
        profile.title,
        *sorted(profile.department_names),
        *profile.org_labels,
    ]
    return sum(
        1
        for item in context
        if _canonical(item) and _canonical(item) in wanted
    )


def _canonical(value: str) -> str:
    return " ".join(value.split()).casefold()
