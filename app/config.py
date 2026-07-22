import os
from datetime import timedelta
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def env_path(name: str, default: Path | str) -> Path:
    return Path(os.path.expandvars(os.getenv(name, str(default)))).expanduser()


def env_file_path() -> Path:
    return env_path("CEO_ENV_FILE", repo_root() / ".env")


def load_env_file(path: Path | None = None) -> None:
    env_path = path or env_file_path()
    if not env_path.exists():
        return
    for key, value in read_env_file(env_path).items():
        os.environ[key] = value


def read_env_file(path: Path | None = None) -> dict[str, str]:
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
        values[key] = _decode_env_value(value.strip())
    return values


def write_env_values(updates: dict[str, str], path: Path | None = None) -> Path:
    env_path = path or env_file_path()
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


def _decode_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
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
    return env_path("CEO_WORKER_DB", repo_root() / "data" / "auto-reply.sqlite3")


def corpus_dir() -> Path:
    return env_path("CEO_CORPUS_DIR", repo_root() / "data" / "corpus")


def codex_bin() -> str:
    return os.getenv("CEO_CODEX_BIN", "codex").strip() or "codex"


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


def chat_bot_names() -> tuple[str, ...]:
    configured = env_csv("CEO_CHAT_BOT_NAMES", ())
    if configured:
        return configured
    robot_name = os.getenv("CEO_DING_ROBOT_NAME", "").strip()
    return (robot_name,) if robot_name else ()


def assistant_signature() -> str:
    return os.getenv("CEO_ASSISTANT_SIGNATURE", "(via agent)")


def handoff_ack() -> str:
    return os.getenv(
        "CEO_HANDOFF_ACK",
        f"I will ask {principal_display_name()} to take a look. {assistant_signature()}",
    )


def document_extraction_ids() -> tuple[str, ...]:
    return env_csv("DOCUMENT_EXTRACTION_IDS", (user_alias(),))


def codex_dev_wake_phrases() -> tuple[str, ...]:
    return env_csv(
        "CEO_CODEX_DEV_WAKE_PHRASES",
        ("Name Agent，用codex执行这个任务",),
    )


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


# --- WeChat personal-account channel (disabled by default) ---
def _wechat_truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


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
