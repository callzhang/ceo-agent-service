import os
from pathlib import Path


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
