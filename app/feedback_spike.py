import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_SOURCE = "ceo-agent-spike"
DEFAULT_DINGTALK_CONFIG_PATH = "~/.dingtalk-skills/config"
DINGTALK_ACCESS_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
DINGTALK_INTERACTIVE_CARD_SEND_URL = (
    "https://api.dingtalk.com/v1.0/im/v1.0/robot/interactiveCards/send"
)


@dataclass(frozen=True)
class FeedbackSpikeCard:
    feedback_token: str
    callback_url_up: str
    callback_url_down: str
    card_data: dict[str, object]
    request_body: dict[str, object]


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
        "config": {
            "autoLayout": True,
            "enableForward": False,
        },
        "header": {
            "title": {
                "type": "text",
                "text": "CEO agent feedback",
            },
        },
        "contents": [
            {
                "type": "markdown",
                "text": stripped_reply,
                "id": "reply_text",
            },
            {
                "type": "action",
                "actions": [
                    {
                        "type": "button",
                        "label": {
                            "type": "text",
                            "text": "赞",
                            "id": "label_up",
                        },
                        "actionType": "openLink",
                        "url": {"all": up_url},
                        "status": "primary",
                        "id": "button_up",
                    },
                    {
                        "type": "button",
                        "label": {
                            "type": "text",
                            "text": "踩",
                            "id": "label_down",
                        },
                        "actionType": "openLink",
                        "url": {"all": down_url},
                        "status": "normal",
                        "id": "button_down",
                    },
                ],
                "id": "feedback_actions",
            },
        ],
        "metadata": {
            "source": DEFAULT_SOURCE,
            "feedbackToken": feedback_token,
        },
    }


def build_dingtalk_interactive_card_request_body(
    *,
    conversation_id: str,
    robot_code: str,
    feedback_token: str,
    card_data: dict[str, object],
    card_template_id: str = "",
) -> dict[str, object]:
    if not conversation_id.strip():
        raise ValueError("conversation id is required")
    if not robot_code.strip():
        raise ValueError("robot code is required")
    return {
        "cardTemplateId": card_template_id.strip() or "StandardCard",
        "openConversationId": conversation_id.strip(),
        "cardBizId": feedback_token,
        "robotCode": robot_code.strip(),
        "cardData": json.dumps(card_data, ensure_ascii=False, separators=(",", ":")),
    }


def build_feedback_spike_card(
    *,
    vercel_base_url: str,
    conversation_id: str,
    robot_code: str,
    reply_text: str,
    card_template_id: str = "",
    feedback_token: str | None = None,
) -> FeedbackSpikeCard:
    token = feedback_token or generate_feedback_token()
    card_data = build_card_data(
        reply_text,
        vercel_base_url=vercel_base_url,
        feedback_token=token,
    )
    request_body = build_dingtalk_interactive_card_request_body(
        conversation_id=conversation_id,
        robot_code=robot_code,
        feedback_token=token,
        card_data=card_data,
        card_template_id=card_template_id,
    )
    up_url = _card_action_url(card_data, "button_up")
    down_url = _card_action_url(card_data, "button_down")
    return FeedbackSpikeCard(
        feedback_token=token,
        callback_url_up=up_url,
        callback_url_down=down_url,
        card_data=card_data,
        request_body=request_body,
    )


def send_feedback_spike_card(
    *,
    vercel_base_url: str,
    conversation_id: str,
    robot_code: str,
    reply_text: str,
    card_template_id: str = "",
    dingtalk_config_path: str = DEFAULT_DINGTALK_CONFIG_PATH,
    preview: bool = False,
) -> dict[str, object]:
    card = build_feedback_spike_card(
        vercel_base_url=vercel_base_url,
        conversation_id=conversation_id,
        robot_code=robot_code,
        reply_text=reply_text,
        card_template_id=card_template_id,
    )
    result: dict[str, object] = {
        "feedback_token": card.feedback_token,
        "callback_url_up": card.callback_url_up,
        "callback_url_down": card.callback_url_down,
        "card_data": card.card_data,
        "request_body": card.request_body,
        "preview": preview,
    }
    if preview:
        return result

    access_token = get_dingtalk_access_token(
        read_dingtalk_app_credentials(dingtalk_config_path)
    )
    response = post_dingtalk_json(
        DINGTALK_INTERACTIVE_CARD_SEND_URL,
        card.request_body,
        access_token=access_token,
    )
    result["response"] = response
    return result


def read_dingtalk_app_credentials(
    config_path: str = DEFAULT_DINGTALK_CONFIG_PATH,
) -> dict[str, str]:
    values: dict[str, str] = {}
    expanded = Path(os.path.expanduser(config_path))
    if expanded.exists():
        with expanded.open(encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    for key in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET"):
        if os.getenv(key):
            values[key] = os.getenv(key, "")
    missing = [
        key
        for key in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET")
        if not values.get(key)
    ]
    if missing:
        raise RuntimeError("DingTalk app credentials are missing")
    return values


def get_dingtalk_access_token(credentials: dict[str, str]) -> str:
    response = post_dingtalk_json(
        DINGTALK_ACCESS_TOKEN_URL,
        {
            "appKey": credentials["DINGTALK_APP_KEY"],
            "appSecret": credentials["DINGTALK_APP_SECRET"],
        },
    )
    token = response.get("accessToken") or response.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("DingTalk access token response did not include a token")
    return token


def post_dingtalk_json(
    url: str,
    payload: dict[str, object],
    *,
    access_token: str = "",
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["x-acs-dingtalk-access-token"] = access_token
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError("DingTalk OpenAPI request failed") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DingTalk OpenAPI returned non-json response") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("DingTalk OpenAPI returned invalid response")
    return parsed


def _card_action_url(card_data: dict[str, object], action_id: str) -> str:
    for item in card_data.get("contents", []):
        if not isinstance(item, dict) or item.get("type") != "action":
            continue
        for action in item.get("actions", []):
            if not isinstance(action, dict) or action.get("id") != action_id:
                continue
            url = action.get("url")
            if isinstance(url, dict) and isinstance(url.get("all"), str):
                return url["all"]
    raise RuntimeError(f"card action url not found: {action_id}")
