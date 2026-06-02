import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlencode


DEFAULT_SOURCE = "ceo-agent-spike"


@dataclass(frozen=True)
class FeedbackSpikeCard:
    feedback_token: str
    callback_url_up: str
    callback_url_down: str
    card_data: dict[str, object]
    command: list[str]


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
) -> str:
    if rating not in {"up", "down"}:
        raise ValueError("rating must be up or down")
    query = urlencode(
        {
            "source": DEFAULT_SOURCE,
            "feedback_token": feedback_token,
            "rating": rating,
        }
    )
    return f"{normalize_vercel_base_url(vercel_base_url)}/api/dingtalk-feedback-spike?{query}"


def build_events_url(
    vercel_base_url: str,
    *,
    secret: str,
    limit: int = 20,
) -> str:
    query = urlencode({"secret": secret, "limit": str(limit)})
    return f"{normalize_vercel_base_url(vercel_base_url)}/api/dingtalk-feedback-spike-events?{query}"


def build_card_data(
    reply_text: str,
    *,
    vercel_base_url: str,
    feedback_token: str,
) -> dict[str, object]:
    stripped_reply = reply_text.strip()
    if not stripped_reply:
        raise ValueError("reply text is required")
    up_url = build_callback_url(
        vercel_base_url,
        feedback_token=feedback_token,
        rating="up",
    )
    down_url = build_callback_url(
        vercel_base_url,
        feedback_token=feedback_token,
        rating="down",
    )
    return {
        "source": DEFAULT_SOURCE,
        "feedbackToken": feedback_token,
        "replyText": stripped_reply,
        "cardParamMap": {
            "source": DEFAULT_SOURCE,
            "feedbackToken": feedback_token,
            "replyText": stripped_reply,
            "upUrl": up_url,
            "downUrl": down_url,
            "upText": "赞",
            "downText": "踩",
        },
        "actions": [
            {"label": "赞", "rating": "up", "url": up_url},
            {"label": "踩", "rating": "down", "url": down_url},
        ],
    }


def build_dws_send_card_command(
    *,
    conversation_id: str,
    receiver_open_dingtalk_id: str,
    reply_text: str,
    card_data: dict[str, object],
    card_template_id: str = "",
    dws_bin: str = "dws",
) -> list[str]:
    if not conversation_id.strip():
        raise ValueError("conversation id is required")
    if not receiver_open_dingtalk_id.strip():
        raise ValueError("receiver open DingTalk id is required")
    command = [
        dws_bin,
        "chat",
        "message",
        "send-card",
        "--group",
        conversation_id.strip(),
        "--user",
        receiver_open_dingtalk_id.strip(),
        "--msg-content",
        reply_text.strip(),
        "--card-data",
        json.dumps(card_data, ensure_ascii=False, separators=(",", ":")),
        "--format",
        "json",
    ]
    if card_template_id.strip():
        command.extend(["--card-template-id", card_template_id.strip()])
    return command


def build_feedback_spike_card(
    *,
    vercel_base_url: str,
    conversation_id: str,
    receiver_open_dingtalk_id: str,
    reply_text: str,
    card_template_id: str = "",
    dws_bin: str = "dws",
    feedback_token: str | None = None,
) -> FeedbackSpikeCard:
    token = feedback_token or generate_feedback_token()
    card_data = build_card_data(
        reply_text,
        vercel_base_url=vercel_base_url,
        feedback_token=token,
    )
    command = build_dws_send_card_command(
        conversation_id=conversation_id,
        receiver_open_dingtalk_id=receiver_open_dingtalk_id,
        reply_text=reply_text,
        card_data=card_data,
        card_template_id=card_template_id,
        dws_bin=dws_bin,
    )
    return FeedbackSpikeCard(
        feedback_token=token,
        callback_url_up=card_data["actions"][0]["url"],  # type: ignore[index]
        callback_url_down=card_data["actions"][1]["url"],  # type: ignore[index]
        card_data=card_data,
        command=command,
    )


def send_feedback_spike_card(
    *,
    vercel_base_url: str,
    conversation_id: str,
    receiver_open_dingtalk_id: str,
    reply_text: str,
    card_template_id: str = "",
    dws_bin: str = "dws",
    preview: bool = False,
) -> dict[str, object]:
    card = build_feedback_spike_card(
        vercel_base_url=vercel_base_url,
        conversation_id=conversation_id,
        receiver_open_dingtalk_id=receiver_open_dingtalk_id,
        reply_text=reply_text,
        card_template_id=card_template_id,
        dws_bin=dws_bin,
    )
    result: dict[str, object] = {
        "feedback_token": card.feedback_token,
        "callback_url_up": card.callback_url_up,
        "callback_url_down": card.callback_url_down,
        "card_data": card.card_data,
        "command": card.command,
        "preview": preview,
    }
    if preview:
        return result

    completed = subprocess.run(
        card.command,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    result.update(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "dws send-card failed "
            f"returncode={completed.returncode} stderr={completed.stderr.strip()}"
        )
    return result
