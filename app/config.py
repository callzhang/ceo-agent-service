import base64
from contextlib import contextmanager
import os
import re
import stat
import tempfile
import threading
from datetime import timedelta
from pathlib import Path


DEFAULT_CEO_CODEX_MODEL = "gpt-5.5"
DEFAULT_CEO_CODEX_MODEL_REASONING_EFFORT = "medium"
_ENCODED_ENV_VALUE_PREFIX = "__CEO_ENV_B64_V1__:"
_EMAIL_SECRET_ENV_KEY = re.compile(
    r"^CEO_EMAIL_[A-Z0-9_]+_(?:IMAP|SMTP)_SECRET$"
)
_ENV_WRITE_THREAD_LOCK = threading.RLock()


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
        # Launchd and explicit shell environment are authoritative; the file
        # supplies defaults for keys that were not already configured.
        os.environ.setdefault(key, value)


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
        values[key] = _decode_env_value(key, value.strip())
    return values


def write_env_values(updates: dict[str, str], path: Path | None = None) -> Path:
    if any("\x00" in key or "\x00" in value for key, value in updates.items()):
        raise ValueError("environment updates must not contain NUL")
    env_path = path or env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with _env_write_lock(env_path):
        return _write_env_values_locked(updates, env_path)


def _write_env_values_locked(updates: dict[str, str], env_path: Path) -> Path:
    existing_lines = (
        env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    )
    written: set[str] = set()
    lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            if key not in written:
                lines.append(f"{key}={_encode_env_value(key, updates[key])}")
                written.add(key)
        else:
            lines.append(raw_line)
    for key, value in updates.items():
        if key not in written:
            lines.append(f"{key}={_encode_env_value(key, value)}")
    mode = stat.S_IMODE(env_path.stat().st_mode) if env_path.exists() else 0o600
    fd, temporary_name = tempfile.mkstemp(
        dir=env_path.parent,
        prefix=f".{env_path.name}.",
    )
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            os.fchmod(temporary.fileno(), mode)
            temporary.write("\n".join(lines).rstrip() + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, env_path)
        replaced = True
    finally:
        if not replaced:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    for key, value in updates.items():
        os.environ[key] = value
    return env_path


@contextmanager
def _env_write_lock(env_path: Path):
    lock_path = env_path.with_name(f".{env_path.name}-write.lock")
    with _ENV_WRITE_THREAD_LOCK:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def effective_env_values(path: Path | None = None) -> dict[str, str]:
    """Return file defaults overlaid by the authoritative process environment."""

    values = read_env_file(path)
    values.update(os.environ)
    return values


def _decode_env_value(key: str, value: str) -> str:
    if _EMAIL_SECRET_ENV_KEY.fullmatch(key) and value.startswith(
        _ENCODED_ENV_VALUE_PREFIX
    ):
        encoded = value.removeprefix(_ENCODED_ENV_VALUE_PREFIX)
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid encoded environment value") from exc
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return os.path.expandvars(value)


def _encode_env_value(key: str, value: str) -> str:
    if _EMAIL_SECRET_ENV_KEY.fullmatch(key) is None:
        if not value or any(character.isspace() for character in value):
            return '"' + value.replace('"', '\\"') + '"'
        return value
    safe_punctuation = frozenset("._:/@+-")
    if (
        value
        and not value.startswith(_ENCODED_ENV_VALUE_PREFIX)
        and all(
            character.isascii()
            and (character.isalnum() or character in safe_punctuation)
            for character in value
        )
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return _ENCODED_ENV_VALUE_PREFIX + encoded


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


def codex_model() -> str:
    return os.getenv("CEO_CODEX_MODEL", DEFAULT_CEO_CODEX_MODEL).strip()


def codex_model_reasoning_effort() -> str:
    return os.getenv(
        "CEO_CODEX_MODEL_REASONING_EFFORT",
        DEFAULT_CEO_CODEX_MODEL_REASONING_EFFORT,
    ).strip()


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
    configured = env_csv(
        "CEO_FORBIDDEN_PATH_PREFIXES",
        (str(Path.home()) + "/",),
    )
    # A bare "~" is shell shorthand, not a path prefix.  Treating it as a
    # substring would reject ordinary ranges such as "40%~50%" in results.
    return tuple(
        prefix
        for prefix in configured
        if prefix not in {"~", "~/"} and prefix.strip()
    )


def parse_duration_value(
    name: str, value: str | None, default: timedelta
) -> timedelta:
    if value is None:
        return default
    text = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = text[-1:]
    if unit not in units:
        raise ValueError(f"{name} must end with one of: s, m, h, d")
    amount_text = text[:-1]
    if not amount_text.isdigit():
        raise ValueError(f"{name} must use an integer duration like 30m or 1h")
    return timedelta(seconds=int(amount_text) * units[unit])


def env_duration(name: str, default: timedelta) -> timedelta:
    return parse_duration_value(name, os.getenv(name), default)


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


def consumer_worker_count() -> int:
    count = env_int("CEO_CONSUMER_WORKERS", 2)
    if not 1 <= count <= 4:
        raise ValueError("CEO_CONSUMER_WORKERS must be between 1 and 4")
    return count


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
    return env_int("CEO_TASK_FOLLOW_UP_INTERVAL_SECONDS", 60)


def repository_upgrade_remote() -> str:
    return os.getenv("CEO_REPOSITORY_UPGRADE_REMOTE", "origin").strip() or "origin"


def repository_upgrade_branch() -> str:
    return os.getenv("CEO_REPOSITORY_UPGRADE_BRANCH", "main").strip() or "main"


def repository_upgrade_check_interval_seconds() -> int:
    return env_int("CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS", 6 * 60 * 60)


def repository_upgrade_enabled() -> bool:
    return not _env_truthy("CEO_REPOSITORY_UPGRADE_DISABLED")


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


def codex_capacity_retry_duration(failure_count: int = 0) -> timedelta:
    base = env_duration("CEO_CODEX_CAPACITY_RETRY_DELAY", timedelta(minutes=30))
    maximum = env_duration("CEO_CODEX_CAPACITY_RETRY_MAX_DELAY", timedelta(hours=4))
    return min(base * (2 ** max(failure_count, 0)), maximum)


def single_chat_read_recovery_window() -> timedelta:
    return env_duration("SINGLE_CHAT_READ_RECOVERY_WINDOW", timedelta(hours=24))


def single_chat_read_recovery_limit() -> int:
    return env_int("SINGLE_CHAT_READ_RECOVERY_LIMIT", 50)


# --- Configurable CLI channels ---
def _env_truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def feishu_cli_binary() -> str:
    return os.getenv("CEO_FEISHU_CLI_BINARY", "lark").strip() or "lark"


def feishu_live_send_enabled() -> bool:
    return _env_truthy("CEO_FEISHU_LIVE_SEND_ENABLED")


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


def wechat_send_min_interval_seconds() -> float:
    """Minimum spacing between WeChat Accessibility navigation attempts.

    The sender is intentionally conservative because each navigation briefly
    foregrounds the personal WeChat client. This limits queued deliveries from
    turning into a burst of UI activity while leaving message content and target
    checks unchanged.
    """
    try:
        return max(0.0, float(os.getenv("CEO_WECHAT_SEND_MIN_INTERVAL_SECONDS", "30")))
    except ValueError:
        return 30.0


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
