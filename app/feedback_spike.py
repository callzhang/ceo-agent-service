import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from app.codex_decision import append_signature
from app.dws_client import DwsClient
from app.leak_check import contains_forbidden_leak

MAX_FEEDBACK_CONTEXT_CHARS = 30
FEEDBACK_UP_LINK_LABEL = "👍 有帮助"
FEEDBACK_DOWN_LINK_LABEL = "👎 需改进"


@dataclass(frozen=True)
class FeedbackSpikeLinkMessage:
    feedback_token: str
    callback_url_up: str
    callback_url_down: str
    text: str


@dataclass(frozen=True)
class FeedbackReplyText:
    feedback_token: str
    text: str


@dataclass(frozen=True)
class PreparedOutgoingReplyText:
    feedback_token: str
    text: str


@dataclass(frozen=True)
class FeedbackLinkContext:
    feedback_token: str
    vercel_base_url: str
    attempt_id: str = ""


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
    attempt_id: int | str | None = None,
) -> str:
    if rating not in {"up", "down"}:
        raise ValueError("rating must be up or down")
    fields = {
        "feedback_token": feedback_token,
        "rating": rating,
    }
    if attempt_id is not None and str(attempt_id).strip():
        fields["attempt_id"] = str(attempt_id).strip()
    original_excerpt = _safe_feedback_context_excerpt(original_text)
    if original_excerpt:
        fields["original_text"] = original_excerpt
    reply_excerpt = _safe_feedback_context_excerpt(reply_text)
    if reply_excerpt:
        fields["reply_text"] = reply_excerpt
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
    link_prefix: str = "反馈：",
) -> str:
    stripped_reply = reply_text.strip()
    if not stripped_reply:
        raise ValueError("reply text is required")
    return (
        f"{stripped_reply}\n\n"
        f"{link_prefix}[{FEEDBACK_UP_LINK_LABEL}]({up_url})"
        f"｜[{FEEDBACK_DOWN_LINK_LABEL}]({down_url})"
    )


def _feedback_context_excerpt(text: str) -> str:
    stripped = " ".join(text.strip().split())
    if len(stripped) <= MAX_FEEDBACK_CONTEXT_CHARS:
        return stripped
    return stripped[: max(0, MAX_FEEDBACK_CONTEXT_CHARS - 3)].rstrip() + "..."


def _safe_feedback_context_excerpt(text: str) -> str:
    excerpt = _feedback_context_excerpt(text)
    if contains_forbidden_leak(excerpt):
        return ""
    return excerpt


def extract_feedback_link_context(text: str) -> FeedbackLinkContext | None:
    url_candidates = re.findall(r"https?://[^\s)）]+", text)
    token_candidates = text.split()
    for raw_part in [*url_candidates, *token_candidates]:
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
        attempt_id = (query.get("attempt_id") or query.get("attemptId") or [""])[0]
        return FeedbackLinkContext(
            feedback_token=token,
            vercel_base_url=f"{parsed.scheme}://{parsed.netloc}",
            attempt_id=attempt_id.strip(),
        )
    return None


def contains_forbidden_leak_outside_feedback_links(
    text: str,
    *,
    vercel_base_url: str,
    feedback_token: str,
    attempt_id: int | str | None,
) -> bool:
    """Ignore only the two callback URLs generated for this exact reply."""
    if not feedback_token or not vercel_base_url:
        return contains_forbidden_leak(text)
    expected_base = urlparse(normalize_vercel_base_url(vercel_base_url))
    expected_attempt_id = str(attempt_id or "").strip()
    ratings: set[str] = set()
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"https?://[^\s)）]+", text):
        parsed = urlparse(match.group(0))
        query = parse_qs(parsed.query)
        rating = (query.get("rating") or [""])[0]
        token = (query.get("feedback_token") or [""])[0]
        callback_attempt_id = (query.get("attempt_id") or [""])[0]
        if (
            parsed.scheme == expected_base.scheme
            and parsed.netloc == expected_base.netloc
            and parsed.path == "/api/dingtalk-feedback-spike"
            and token == feedback_token
            and rating in {"up", "down"}
            and callback_attempt_id == expected_attempt_id
        ):
            ratings.add(rating)
            spans.append(match.span())
    if ratings != {"up", "down"}:
        return contains_forbidden_leak(text)
    body = text
    for start, end in reversed(spans):
        body = body[:start] + body[end:]
    return contains_forbidden_leak(body)


def build_feedback_spike_link_message(
    *,
    vercel_base_url: str,
    reply_text: str,
    original_text: str = "",
    attempt_id: int | str | None = None,
    feedback_token: str | None = None,
    link_prefix: str = "反馈：",
) -> FeedbackSpikeLinkMessage:
    token = feedback_token or generate_feedback_token()
    up_url = build_callback_url(
        vercel_base_url,
        feedback_token=token,
        rating="up",
        original_text=original_text,
        reply_text=reply_text,
        attempt_id=attempt_id,
    )
    down_url = build_callback_url(
        vercel_base_url,
        feedback_token=token,
        rating="down",
        original_text=original_text,
        reply_text=reply_text,
        attempt_id=attempt_id,
    )
    return FeedbackSpikeLinkMessage(
        feedback_token=token,
        callback_url_up=up_url,
        callback_url_down=down_url,
        text=build_feedback_link_text(
            reply_text,
            up_url=up_url,
            down_url=down_url,
            link_prefix=link_prefix,
        ),
    )


def append_feedback_links(
    *,
    vercel_base_url: str,
    reply_text: str,
    original_text: str = "",
    attempt_id: int | str | None = None,
    feedback_token: str | None = None,
    link_prefix: str = "反馈：",
) -> FeedbackReplyText:
    existing_context = extract_feedback_link_context(reply_text)
    if existing_context is not None:
        return FeedbackReplyText(
            feedback_token=existing_context.feedback_token,
            text=reply_text,
        )
    message = build_feedback_spike_link_message(
        vercel_base_url=vercel_base_url,
        reply_text=reply_text,
        original_text=original_text,
        attempt_id=attempt_id,
        feedback_token=feedback_token,
        link_prefix=link_prefix,
    )
    return FeedbackReplyText(feedback_token=message.feedback_token, text=message.text)


def prepare_outgoing_reply_text(
    *,
    reply_text: str,
    original_text: str = "",
    attempt_id: int | str | None = None,
    feedback_base_url: str = "",
    feedback_token: str | None = None,
    feedback_link_prefix: str = "反馈：",
    feedback_link_appender: Callable[..., FeedbackReplyText] = append_feedback_links,
) -> PreparedOutgoingReplyText:
    text = append_signature(reply_text)
    if not feedback_base_url:
        return PreparedOutgoingReplyText(feedback_token="", text=text)
    feedback_reply = feedback_link_appender(
        vercel_base_url=feedback_base_url,
        reply_text=text,
        original_text=original_text,
        attempt_id=attempt_id,
        feedback_token=feedback_token,
        link_prefix=feedback_link_prefix,
    )
    return PreparedOutgoingReplyText(
        feedback_token=feedback_reply.feedback_token,
        text=feedback_reply.text,
    )


def send_feedback_spike_links(
    *,
    vercel_base_url: str,
    reply_text: str,
    original_text: str = "",
    attempt_id: int | str | None = None,
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
        attempt_id=attempt_id,
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
