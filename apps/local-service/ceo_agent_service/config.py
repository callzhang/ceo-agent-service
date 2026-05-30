import os
from datetime import timedelta
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def env_file_path() -> Path:
    return Path(os.getenv("CEO_ENV_FILE", str(repo_root() / ".env")))


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
    return Path(
        os.getenv(
            "CEO_WORK_PROFILE_PATH",
            str(repo_root() / "profiles" / "work_profile.md"),
        )
    )


def profile_evidence_dir() -> Path:
    return Path(
        os.getenv(
            "CEO_PROFILE_EVIDENCE_DIR",
            str(repo_root() / "data" / "profile-evidence"),
        )
    )


def env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def principal_name() -> str:
    return os.getenv("CEO_PRINCIPAL_NAME", "the principal")


def principal_display_name() -> str:
    return os.getenv("CEO_PRINCIPAL_DISPLAY_NAME", principal_name())


def principal_handoff_name() -> str:
    return os.getenv("CEO_PRINCIPAL_HANDOFF_NAME", principal_display_name())


def mention_aliases() -> tuple[str, ...]:
    return env_csv("CEO_MENTION_ALIASES", ("@CEO",))


def broadcast_mention_aliases() -> tuple[str, ...]:
    return env_csv("CEO_BROADCAST_MENTION_ALIASES", ("@所有人", "@all"))


def current_user_display_names() -> tuple[str, ...]:
    return env_csv("CEO_CURRENT_USER_DISPLAY_NAMES", (principal_display_name(),))


def assistant_signature() -> str:
    return os.getenv("CEO_ASSISTANT_SIGNATURE", "(via agent)")


def handoff_ack() -> str:
    return os.getenv(
        "CEO_HANDOFF_ACK",
        f"I will ask {principal_display_name()} to take a look. {assistant_signature()}",
    )


def responsibility_summary() -> str:
    return os.getenv(
        "CEO_RESPONSIBILITY_SUMMARY",
        "Use the configured organization responsibility rules to decide whether the principal should reply.",
    )


def style_speaker_names() -> tuple[str, ...]:
    return env_csv("CEO_STYLE_SPEAKER_NAMES", (principal_display_name(),))


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


def message_recovery_interval() -> timedelta:
    return env_duration("MESSAGE_RECOVERY_INTERVAL", timedelta(hours=1))


def single_chat_read_recovery_window() -> timedelta:
    return env_duration("SINGLE_CHAT_READ_RECOVERY_WINDOW", timedelta(hours=24))


def single_chat_read_recovery_limit() -> int:
    return env_int("SINGLE_CHAT_READ_RECOVERY_LIMIT", 50)


def group_read_recovery_window() -> timedelta:
    return env_duration("GROUP_READ_RECOVERY_WINDOW", timedelta(hours=24))


def group_read_recovery_limit() -> int:
    return env_int("GROUP_READ_RECOVERY_LIMIT", 3)
