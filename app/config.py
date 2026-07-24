import os
import re
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_REFERENCE_RE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)
_ENV_PROVENANCE_MAX_DEPTH = 32
_SENSITIVE_ENV_KEY_MARKERS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "API_KEY",
    "PRIVATE_KEY",
    "AUTHORIZATION",
    "COOKIE",
    "CREDENTIAL",
    "ROBOT_CODE",
    "WEBHOOK",
    "ACCESS_KEY",
    "SIGNING_KEY",
    "PASSPHRASE",
    "BEARER",
)
_SENSITIVE_ENV_KEY_TOKENS = {"PAT"}


def is_sensitive_env_key(key: str) -> bool:
    normalized = str(key or "").upper()
    return any(
        marker in normalized for marker in _SENSITIVE_ENV_KEY_MARKERS
    ) or any(token in normalized.split("_") for token in _SENSITIVE_ENV_KEY_TOKENS)


def env_value_references_sensitive_key(
    value: str,
    *,
    raw_values: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    max_depth: int = _ENV_PROVENANCE_MAX_DEPTH,
) -> bool:
    if not isinstance(value, str):
        return False
    raw_values = raw_values or {}
    environment = os.environ if environment is None else environment
    sensitive_values = _sensitive_env_source_values(raw_values, environment)
    return _env_value_has_sensitive_provenance(
        value,
        raw_values=raw_values,
        environment=environment,
        sensitive_values=sensitive_values,
        visiting=frozenset(),
        depth=0,
        max_depth=max_depth,
    )


def _sensitive_env_source_values(
    *sources: Mapping[str, str],
) -> frozenset[str]:
    return frozenset(
        value
        for source in sources
        for key, value in source.items()
        if is_sensitive_env_key(key) and isinstance(value, str) and value
    )


def _env_value_has_sensitive_provenance(
    value: str,
    *,
    raw_values: Mapping[str, str],
    environment: Mapping[str, str],
    sensitive_values: frozenset[str],
    visiting: frozenset[str],
    depth: int,
    max_depth: int,
) -> bool:
    if any(sensitive_value in value for sensitive_value in sensitive_values):
        return True
    for match in _ENV_REFERENCE_RE.finditer(value):
        referenced_key = match.group(1) or match.group(2) or ""
        if is_sensitive_env_key(referenced_key):
            return True
        if referenced_key in visiting:
            continue
        referenced_values: list[str] = []
        for source in (raw_values, environment):
            referenced_value = source.get(referenced_key)
            if (
                isinstance(referenced_value, str)
                and referenced_value not in referenced_values
            ):
                referenced_values.append(referenced_value)
        if not referenced_values:
            continue
        if depth >= max_depth:
            # An unresolved chain is not provably safe.  Fail closed instead of
            # allowing aliases deeper than the bounded provenance traversal.
            return True
        next_visiting = visiting | {referenced_key}
        if any(
            _env_value_has_sensitive_provenance(
                referenced_value,
                raw_values=raw_values,
                environment=environment,
                sensitive_values=sensitive_values,
                visiting=next_visiting,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for referenced_value in referenced_values
        ):
            return True
    return False


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def env_path(name: str, default: Path | str) -> Path:
    value = os.getenv(name, str(default))
    return Path(_expand_env_value(name, value)).expanduser()


def env_file_path() -> Path:
    return env_path("CEO_ENV_FILE", repo_root() / ".env")


def load_env_file(path: Path | None = None) -> None:
    env_path = path or env_file_path()
    if not env_path.exists():
        return
    for key, value in read_env_file(env_path).items():
        os.environ[key] = value


def read_env_file_raw(path: Path | None = None) -> dict[str, str]:
    env_path = path or env_file_path()
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _decode_env_literal(value.strip())
    return values


def read_env_file(path: Path | None = None) -> dict[str, str]:
    raw_values = read_env_file_raw(path)
    return {
        key: _expand_env_value(key, value, raw_values=raw_values)
        for key, value in raw_values.items()
    }


def write_env_values(updates: dict[str, str], path: Path | None = None) -> Path:
    validated_updates: dict[str, str] = {}
    for key, value in updates.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            raise ValueError("invalid environment variable name")
        if not isinstance(value, str):
            raise ValueError("environment variable value must be text")
        if any(character in value for character in ("\r", "\n", "\x00")):
            raise ValueError("environment variable value contains a forbidden control")
        validated_updates[key] = value
    updates = validated_updates
    env_path = path or env_file_path()
    proposed_raw_values = read_env_file_raw(env_path)
    proposed_raw_values.update(updates)
    for key, value in updates.items():
        if not is_sensitive_env_key(key) and env_value_references_sensitive_key(
            value,
            raw_values=proposed_raw_values,
        ):
            raise ValueError(
                "non-sensitive environment variable cannot reference a sensitive one"
            )
    existing_lines = (
        env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    )
    remaining = dict(updates)
    lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines.append(f"{key}={_encode_env_value(remaining.pop(key))}")
        else:
            lines.append(raw_line)
    for key, value in remaining.items():
        lines.append(f"{key}={_encode_env_value(value)}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value
    return env_path


def _decode_env_literal(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _expand_env_value(
    key: str,
    value: str,
    *,
    raw_values: Mapping[str, str] | None = None,
) -> str:
    if not is_sensitive_env_key(key) and env_value_references_sensitive_key(
        value,
        raw_values=raw_values,
    ):
        return value
    return os.path.expandvars(value)


def _encode_env_value(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


load_env_file()


def work_profile_path() -> Path:
    return env_path(
        "CEO_WORK_PROFILE_PATH",
        repo_root() / "data" / "work-profile" / "work_profile.md",
    )


def profile_evidence_dir() -> Path:
    return env_path(
        "CEO_PROFILE_EVIDENCE_DIR",
        repo_root() / "data" / "profile-evidence",
    )


def workspace_path() -> Path:
    return env_path("CEO_WORKSPACE", Path.home() / "Documents" / "memory")


def worker_db_path() -> Path:
    return env_path(
        "CEO_WORKER_DB",
        Path.home()
        / "Library"
        / "Application Support"
        / "ceo-agent-service"
        / "auto-reply.sqlite3",
    )


def corpus_dir() -> Path:
    return env_path("CEO_CORPUS_DIR", repo_root() / "data" / "corpus")


def env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def principal_name() -> str:
    return os.getenv("CEO_PRINCIPAL_NAME", "the principal")


def user_alias() -> str:
    return os.getenv("USER_ALIAS", principal_name())


def principal_display_name() -> str:
    return user_alias()


def principal_handoff_name() -> str:
    return user_alias()


def memory_connector_user_id() -> str:
    return os.getenv("MEMORY_CONNECTOR_USER_ID", principal_name())


def mention_aliases() -> tuple[str, ...]:
    return env_csv("CEO_MENTION_ALIASES", ("@CEO",))


def broadcast_mention_aliases() -> tuple[str, ...]:
    return env_csv("CEO_BROADCAST_MENTION_ALIASES", ("@所有人", "@all"))


def agent_names() -> tuple[str, ...]:
    configured = env_csv("CEO_AGENT_NAMES", ())
    if configured:
        return configured
    robot_name = os.getenv("CEO_DING_ROBOT_NAME", "").strip()
    return (robot_name,) if robot_name else ()


def agent_mention_aliases() -> tuple[str, ...]:
    return tuple(name if name.startswith("@") else f"@{name}" for name in agent_names())


def chat_bot_names() -> tuple[str, ...]:
    return agent_names()


def assistant_signature() -> str:
    return os.getenv("CEO_ASSISTANT_SIGNATURE", "(via agent)")


def handoff_ack() -> str:
    return os.getenv(
        "CEO_HANDOFF_ACK",
        f"I will ask {principal_display_name()} to take a look. {assistant_signature()}",
    )


def document_extraction_ids() -> tuple[str, ...]:
    return env_csv("DOCUMENT_EXTRACTION_IDS", (user_alias(),))


def forbidden_path_prefixes() -> tuple[str, ...]:
    return env_csv("CEO_FORBIDDEN_PATH_PREFIXES", (str(Path.home()) + "/",))


def env_duration(name: str, default: timedelta) -> timedelta:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip().lower()
    units = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
    }
    unit = text[-1:]
    if unit not in units:
        raise ValueError(f"{name} must end with one of: s, m, h, d")
    amount_text = text[:-1]
    if not amount_text.isdigit():
        raise ValueError(f"{name} must use an integer duration like 30m or 1h")
    return timedelta(seconds=int(amount_text) * units[unit])


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    if not text.isdigit():
        raise ValueError(f"{name} must be an integer")
    return int(text)


def producer_interval_seconds() -> int:
    return env_int("CEO_PRODUCER_INTERVAL_SECONDS", 60)


def consumer_poll_interval_seconds() -> int:
    return env_int("CEO_CONSUMER_POLL_INTERVAL_SECONDS", 10)


def meeting_producer_interval_seconds() -> int:
    return env_int("CEO_MEETING_PRODUCER_INTERVAL_SECONDS", 60)


def meeting_consumer_poll_interval_seconds() -> int:
    return env_int("CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS", 10)


def meeting_settle_seconds() -> int:
    return env_int("CEO_MEETING_SETTLE_SECONDS", 600)


def task_work_item_interval_seconds() -> int:
    return env_int("CEO_TASK_WORK_ITEM_INTERVAL_SECONDS", 60)


def task_daily_interval_seconds() -> int:
    return env_int("CEO_TASK_DAILY_INTERVAL_SECONDS", 86_400)


def task_follow_up_interval_seconds() -> int:
    return env_int("CEO_TASK_FOLLOW_UP_INTERVAL_SECONDS", 3_600)


def embedding_base_url() -> str:
    return os.getenv("CEO_EMBEDDING_BASE_URL", "https://embed.preseen.ai/v1")


def embedding_model() -> str:
    return os.getenv("CEO_EMBEDDING_MODEL", "jinaai/jina-embeddings-v5-text-small")


def embedding_api_key() -> str:
    return os.getenv(
        "CEO_EMBEDDING_API_KEY",
        "s4BVC8bymjW5cDiQjVKEkxq53lRNtvdiUmk-Tozt8JM",
    )


def embedding_timeout_seconds() -> int:
    return env_int("CEO_EMBEDDING_TIMEOUT_SECONDS", 120)


def embedding_enabled() -> bool:
    disabled = os.getenv("CEO_EMBEDDING_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return not disabled and bool(embedding_api_key().strip())


def poll_interval_seconds() -> int:
    return env_int("CEO_POLL_INTERVAL_SECONDS", 30)


def batch_seconds() -> int:
    return env_int("CEO_BATCH_SECONDS", 120)


def notification_bridge_base_url() -> str:
    return os.getenv("CEO_NOTIFICATION_BRIDGE_BASE_URL", "http://127.0.0.1:8765").rstrip(
        "/"
    )


def feedback_spike_vercel_base_url() -> str:
    return os.getenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "").strip().rstrip("/")


def message_recovery_interval() -> timedelta:
    return env_duration("MESSAGE_RECOVERY_INTERVAL", timedelta(hours=1))


def fast_path_unread_backoff_duration() -> timedelta:
    return env_duration("FAST_PATH_UNREAD_BACKOFF", timedelta(minutes=5))


def single_chat_read_recovery_window() -> timedelta:
    return env_duration("SINGLE_CHAT_READ_RECOVERY_WINDOW", timedelta(hours=24))


def single_chat_read_recovery_limit() -> int:
    return env_int("SINGLE_CHAT_READ_RECOVERY_LIMIT", 50)


# --- Configurable CLI channels ---
def _env_truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def feishu_cli_binary() -> str:
    return os.getenv("CEO_FEISHU_CLI_BINARY", "lark").strip() or "lark"


# --- WeChat personal-account channel (disabled by default) ---
def _wechat_truthy(name: str) -> bool:
    return _env_truthy(name)


def wechat_reader_enabled() -> bool:
    return _wechat_truthy("CEO_WECHAT_READER_ENABLED")


def wechat_sender_enabled() -> bool:
    return _wechat_truthy("CEO_WECHAT_SENDER_ENABLED")


def wechat_poll_interval_seconds() -> int:
    return env_int("CEO_WECHAT_POLL_INTERVAL_SECONDS", 15)


def wechat_passphrase_file() -> Path:
    return env_path("CEO_WECHAT_PASSPHRASE_FILE", "~/.config/wx_read/passphrase.hex")


def wechat_mirror_dir() -> Path:
    return env_path("CEO_WECHAT_MIRROR_DIR", "~/.cache/wx_read/plain")


def wechat_reader_socket() -> Path:
    return env_path(
        "CEO_WECHAT_READER_SOCKET",
        "~/Library/Application Support/CEO Agent/WeChatReader/reader.sock",
    )


def wechat_reader_timeout_seconds() -> float:
    try:
        return max(0.1, float(os.getenv("CEO_WECHAT_READER_TIMEOUT_SECONDS", "120")))
    except ValueError:
        return 120.0


def wechat_sender_socket() -> Path:
    return env_path(
        "CEO_WECHAT_SENDER_SOCKET",
        "~/Library/Application Support/CEO Agent/WeChatSender/sender.sock",
    )


def wechat_sender_timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("CEO_WECHAT_SENDER_TIMEOUT_SECONDS", "140")))
    except ValueError:
        return 140.0


def wechat_snapshot_dir() -> Path:
    return env_path("CEO_WECHAT_SNAPSHOT_DIR", "data/wechat-snapshots")


def wechat_self_user_id() -> str:
    """Optional override for the account's own wxid; auto-detected when empty."""
    return os.getenv("CEO_WECHAT_SELF_USER_ID", "").strip()


def wechat_send_idle_seconds() -> float:
    """Seconds the user must be idle (no keyboard/mouse) before a send briefly
    foregrounds WeChat to select the chat. Default 10s."""
    try:
        return float(os.getenv("CEO_WECHAT_SEND_IDLE_SECONDS", "10"))
    except ValueError:
        return 10.0


def wechat_send_mode() -> str:
    """'confirm' (default): hold ready_to_send deliveries for explicit user
    approval; 'auto': the sender loop sends them automatically."""
    mode = os.getenv("CEO_WECHAT_SEND_MODE", "confirm").strip().lower()
    return mode if mode in ("confirm", "auto") else "confirm"


def wechat_fetch_articles() -> bool:
    """Fetch shared-article bodies to enrich Codex context (default on)."""
    return os.getenv("CEO_WECHAT_FETCH_ARTICLES", "1").strip().lower() in ("1", "true", "yes", "on")


def wechat_article_max_chars() -> int:
    return env_int("CEO_WECHAT_ARTICLE_MAX_CHARS", 1500)


# --- Feishu official Bot channel (disabled by default) ---
FEISHU_KEYRING_SERVICE = "ceo-agent-service/feishu"
FEISHU_KEYRING_APP_SECRET_USERNAME = "app_secret"
_FEISHU_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]+$")


def _feishu_truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def _feishu_positive_int(name: str, default: int) -> int:
    value = env_int(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _feishu_bounded_positive_int(name: str, default: int, maximum: int) -> int:
    value = _feishu_positive_int(name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def feishu_enabled() -> bool:
    """Whether the Feishu receive/decision channel may start."""
    return _feishu_truthy("CEO_FEISHU_ENABLED")


def feishu_sender_enabled() -> bool:
    """Whether the separately gated Feishu delivery worker may start."""
    return feishu_enabled() and _feishu_truthy("CEO_FEISHU_SENDER_ENABLED")


def feishu_media_enabled() -> bool:
    """Whether approved inbound attachments may be downloaded and verified."""
    return feishu_enabled() and _feishu_truthy("CEO_FEISHU_MEDIA_ENABLED")


def feishu_reaction_enabled() -> bool:
    """Whether the separately gated Emoji-reaction worker may mutate Feishu."""
    return feishu_sender_enabled() and _feishu_truthy(
        "CEO_FEISHU_REACTION_ENABLED"
    )


def feishu_recall_enabled() -> bool:
    """Whether reviewed recalls may execute; every recall still needs approval."""
    return feishu_sender_enabled() and _feishu_truthy("CEO_FEISHU_RECALL_ENABLED")


def feishu_handoff_enabled() -> bool:
    """Whether handoff notifications may target the local trusted allowlist."""
    return feishu_sender_enabled() and _feishu_truthy("CEO_FEISHU_HANDOFF_ENABLED")


def feishu_reply_mention_sender_enabled() -> bool:
    """Whether group replies may mention the trusted inbound sender identity."""
    return feishu_sender_enabled() and _feishu_truthy(
        "CEO_FEISHU_REPLY_MENTION_SENDER"
    )


def feishu_reply_mention_open_ids() -> tuple[str, ...]:
    """Return the bounded local identity map allowed for reply mentions."""
    values: list[str] = []
    for item in os.getenv("CEO_FEISHU_REPLY_MENTION_OPEN_IDS", "").split(","):
        normalized = item.strip()
        if not normalized:
            continue
        if not _FEISHU_OPEN_ID_RE.fullmatch(normalized):
            raise ValueError(
                "CEO_FEISHU_REPLY_MENTION_OPEN_IDS contains an invalid open_id"
            )
        if normalized not in values:
            values.append(normalized)
    if len(values) > 20:
        raise ValueError(
            "CEO_FEISHU_REPLY_MENTION_OPEN_IDS must contain at most 20 IDs"
        )
    return tuple(values)


def feishu_live_send_allowed() -> bool:
    """Return true only when all Feishu outbound gates are explicitly open."""
    return (
        feishu_enabled()
        and feishu_sender_enabled()
        and os.getenv("CEO_NOT_SEND_MESSAGE", "1").strip() == "0"
    )


def feishu_send_mode() -> str:
    """Return ``confirm`` unless the operator explicitly selects ``auto``."""
    mode = os.getenv("CEO_FEISHU_SEND_MODE", "confirm").strip().lower()
    return mode if mode in ("confirm", "auto") else "confirm"


def feishu_security_mode() -> str:
    """Return the Channel SDK security mode, failing closed to ``strict``."""
    mode = os.getenv("CEO_FEISHU_SECURITY_MODE", "strict").strip().lower()
    return mode if mode in ("audit", "strict") else "strict"


def feishu_stale_event_seconds() -> int:
    return _feishu_positive_int("CEO_FEISHU_STALE_EVENT_SECONDS", 300)


def feishu_context_limit() -> int:
    return _feishu_bounded_positive_int("CEO_FEISHU_CONTEXT_LIMIT", 20, 100)


def feishu_context_lookback_seconds() -> int:
    """Bound prompt context to the shorter of event retention and 30 days."""
    maximum = min(feishu_event_retention_days() * 24 * 60 * 60, 30 * 24 * 60 * 60)
    return _feishu_bounded_positive_int(
        "CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", 24 * 60 * 60, maximum
    )


def feishu_max_sends_per_minute() -> int:
    return _feishu_positive_int("CEO_FEISHU_MAX_SENDS_PER_MINUTE", 10)


def feishu_event_retention_days() -> int:
    return _feishu_positive_int("CEO_FEISHU_EVENT_RETENTION_DAYS", 30)


def feishu_media_retention_days() -> int:
    return _feishu_positive_int("CEO_FEISHU_MEDIA_RETENTION_DAYS", 7)


def feishu_media_max_assets() -> int:
    return _feishu_bounded_positive_int("CEO_FEISHU_MEDIA_MAX_ASSETS", 8, 8)


def feishu_media_max_bytes() -> int:
    return _feishu_bounded_positive_int(
        "CEO_FEISHU_MEDIA_MAX_BYTES", 20 * 1024 * 1024, 20 * 1024 * 1024
    )


def feishu_media_event_max_bytes() -> int:
    return _feishu_bounded_positive_int(
        "CEO_FEISHU_MEDIA_EVENT_MAX_BYTES",
        32 * 1024 * 1024,
        32 * 1024 * 1024,
    )


def feishu_handoff_open_ids() -> tuple[str, ...]:
    """Return a normalized, bounded local allowlist; never accept model targets."""
    values = []
    for item in os.getenv("CEO_FEISHU_HANDOFF_OPEN_IDS", "").split(","):
        normalized = item.strip()
        if normalized and normalized not in values:
            values.append(normalized)
    if len(values) > 20:
        raise ValueError("CEO_FEISHU_HANDOFF_OPEN_IDS must contain at most 20 IDs")
    return tuple(values)


def feishu_app_id() -> str:
    return os.getenv("CEO_FEISHU_APP_ID", "").strip()


def feishu_app_secret() -> str:
    """Read the App Secret from Keychain first, then the debug-only env fallback.

    Keyring/backend errors are intentionally swallowed: exception text from a
    credential backend must never be surfaced by configuration parsing because
    it can contain sensitive backend details or credential material.
    """
    try:
        import keyring

        value = keyring.get_password(
            FEISHU_KEYRING_SERVICE,
            FEISHU_KEYRING_APP_SECRET_USERNAME,
        )
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:  # pragma: no cover - backend behavior is platform-specific
        pass
    return os.getenv("CEO_FEISHU_APP_SECRET", "").strip()
