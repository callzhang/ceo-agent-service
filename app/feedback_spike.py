import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from app.dws_client import DwsClient

DEFAULT_SOURCE = "ceo-agent-spike"


@dataclass(frozen=True)
class FeedbackSpikeLinkMessage:
    feedback_token: str
    callback_url_up: str
    callback_url_down: str
    text: str


@dataclass(frozen=True)
class FeedbackLinkContext:
    feedback_token: str
    vercel_base_url: str


def generate_feedback_token(now_seconds: int | None = None) -> str:
    timestamp = int(now_seconds if now_seconds is not None else time.time())
    return f"spike_{timestamp}_{secrets.token_hex(4)}"


def normalize_vercel_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("Vercel base URL is required")
    if not (normalized.startswith("https://") or normalized.startswith("http://")):
        raise ValueError("Vercel base URL must start with http:// or https://")
    return normalized


def build_callback_url(
    vercel_base_url: str,
    *,
    feedback_token: str,
    rating: str,
    original_text: str = "",
    reply_text: str = "",
) -> str:
    if rating not in {"up", "down"}:
        raise ValueError("rating must be up or down")
    fields = {
        "source": DEFAULT_SOURCE,
        "feedback_token": feedback_token,
        "rating": rating,
    }
    if original_text.strip():
        fields["original_text"] = original_text.strip()
    if reply_text.strip():
        fields["reply_text"] = reply_text.strip()
    query = urlencode(fields)
    return f"{normalize_vercel_base_url(vercel_base_url)}/api/dingtalk-feedback-spike?{query}"


def build_events_url(
    vercel_base_url: str,
    *,
    secret: str,
    limit: int = 20,
) -> str:
    query = urlencode({"secret": secret, "limit": str(limit)})
    return f"{normalize_vercel_base_url(vercel_base_url)}/api/dingtalk-feedback-spike-events?{query}"


def build_feedback_link_text(
    reply_text: str,
    *,
    up_url: str,
    down_url: str,
) -> str:
    stripped_reply = reply_text.strip()
    if not stripped_reply:
        raise ValueError("reply text is required")
    return f"{stripped_reply}\n\n反馈：赞 {up_url}  踩 {down_url}"


def extract_feedback_link_context(text: str) -> FeedbackLinkContext | None:
    for raw_part in text.split():
        part = raw_part.strip("，,。；;：:、()（）[]【】<>《》\"'")
        if "/api/dingtalk-feedback-spike" not in part:
            continue
        parsed = urlparse(part)
        if not parsed.scheme or not parsed.netloc:
            continue
        query = parse_qs(parsed.query)
        token = (query.get("feedback_token") or query.get("feedbackToken") or [""])[0]
        token = token.strip()
        if not token:
            continue
        return FeedbackLinkContext(
            feedback_token=token,
            vercel_base_url=f"{parsed.scheme}://{parsed.netloc}",
        )
    return None


def build_feedback_spike_link_message(
    *,
    vercel_base_url: str,
    reply_text: str,
    original_text: str = "",
    feedback_token: str | None = None,
) -> FeedbackSpikeLinkMessage:
    token = feedback_token or generate_feedback_token()
    up_url = build_callback_url(
        vercel_base_url,
        feedback_token=token,
        rating="up",
        original_text=original_text,
        reply_text=reply_text,
    )
    down_url = build_callback_url(
        vercel_base_url,
        feedback_token=token,
        rating="down",
        original_text=original_text,
        reply_text=reply_text,
    )
    return FeedbackSpikeLinkMessage(
        feedback_token=token,
        callback_url_up=up_url,
        callback_url_down=down_url,
        text=build_feedback_link_text(reply_text, up_url=up_url, down_url=down_url),
    )


def send_feedback_spike_links(
    *,
    vercel_base_url: str,
    reply_text: str,
    original_text: str = "",
    conversation_id: str | None = None,
    user_id: str | None = None,
    open_dingtalk_id: str | None = None,
    dws_bin: str = "dws",
    dws_client: DwsClient | None = None,
    preview: bool = False,
) -> dict[str, object]:
    message = build_feedback_spike_link_message(
        vercel_base_url=vercel_base_url,
        reply_text=reply_text,
        original_text=original_text,
    )
    client = dws_client or DwsClient(dws_bin=dws_bin)
    command = client.build_send_message_command(
        conversation_id,
        message.text,
        user_id=user_id,
        open_dingtalk_id=open_dingtalk_id,
        title=reply_text,
    )
    result: dict[str, object] = {
        "feedback_token": message.feedback_token,
        "callback_url_up": message.callback_url_up,
        "callback_url_down": message.callback_url_down,
        "text": message.text,
        "command": command,
        "preview": preview,
    }
    if preview:
        return result

    result["response"] = client.send_message(
        conversation_id,
        message.text,
        user_id=user_id,
        open_dingtalk_id=open_dingtalk_id,
        title=reply_text,
    )
    return result
