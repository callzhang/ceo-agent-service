import pytest

from app import config


FEISHU_ENV_NAMES = (
    "CEO_FEISHU_ENABLED",
    "CEO_FEISHU_SENDER_ENABLED",
    "CEO_FEISHU_MEDIA_ENABLED",
    "CEO_FEISHU_REACTION_ENABLED",
    "CEO_FEISHU_RECALL_ENABLED",
    "CEO_FEISHU_HANDOFF_ENABLED",
    "CEO_FEISHU_REPLY_MENTION_SENDER",
    "CEO_FEISHU_REPLY_MENTION_OPEN_IDS",
    "CEO_FEISHU_SEND_MODE",
    "CEO_FEISHU_SECURITY_MODE",
    "CEO_FEISHU_STALE_EVENT_SECONDS",
    "CEO_FEISHU_CONTEXT_LIMIT",
    "CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS",
    "CEO_FEISHU_MAX_SENDS_PER_MINUTE",
    "CEO_FEISHU_EVENT_RETENTION_DAYS",
    "CEO_FEISHU_MEDIA_RETENTION_DAYS",
    "CEO_FEISHU_MEDIA_MAX_ASSETS",
    "CEO_FEISHU_MEDIA_MAX_BYTES",
    "CEO_FEISHU_MEDIA_EVENT_MAX_BYTES",
    "CEO_FEISHU_HANDOFF_OPEN_IDS",
    "CEO_FEISHU_APP_ID",
    "CEO_FEISHU_APP_SECRET",
    "CEO_NOT_SEND_MESSAGE",
)


@pytest.fixture(autouse=True)
def clear_feishu_environment(monkeypatch):
    for name in FEISHU_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_feishu_defaults_are_disabled_and_fail_closed():
    assert config.feishu_enabled() is False
    assert config.feishu_sender_enabled() is False
    assert config.feishu_media_enabled() is False
    assert config.feishu_reaction_enabled() is False
    assert config.feishu_recall_enabled() is False
    assert config.feishu_handoff_enabled() is False
    assert config.feishu_reply_mention_sender_enabled() is False
    assert config.feishu_live_send_allowed() is False
    assert config.feishu_send_mode() == "confirm"
    assert config.feishu_security_mode() == "strict"
    assert config.feishu_stale_event_seconds() == 300
    assert config.feishu_context_limit() == 20
    assert config.feishu_context_lookback_seconds() == 86400
    assert config.feishu_max_sends_per_minute() == 10
    assert config.feishu_event_retention_days() == 30
    assert config.feishu_media_retention_days() == 7
    assert config.feishu_media_max_assets() == 8
    assert config.feishu_media_max_bytes() == 20 * 1024 * 1024
    assert config.feishu_media_event_max_bytes() == 32 * 1024 * 1024
    assert config.feishu_handoff_open_ids() == ()
    assert config.feishu_reply_mention_open_ids() == ()
    assert config.feishu_app_id() == ""


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_feishu_switches_require_explicit_truthy_values(monkeypatch, value):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", value)
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", value)

    assert config.feishu_enabled() is True
    assert config.feishu_sender_enabled() is True


def test_feishu_live_send_requires_master_sender_and_global_gates(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    assert config.feishu_sender_enabled() is True
    assert config.feishu_live_send_allowed() is False

    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")
    assert config.feishu_live_send_allowed() is True

    monkeypatch.setenv("CEO_FEISHU_ENABLED", "0")
    assert config.feishu_sender_enabled() is False
    assert config.feishu_live_send_allowed() is False

    monkeypatch.setenv("CEO_FEISHU_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "0")
    assert config.feishu_live_send_allowed() is False

    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    assert config.feishu_live_send_allowed() is True

    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "false")
    assert config.feishu_live_send_allowed() is False


def test_feishu_master_switch_closes_every_effective_capability(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", "0")
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_REACTION_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_RECALL_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_HANDOFF_ENABLED", "1")
    monkeypatch.setenv("CEO_FEISHU_REPLY_MENTION_SENDER", "1")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")

    assert config.feishu_sender_enabled() is False
    assert config.feishu_media_enabled() is False
    assert config.feishu_reaction_enabled() is False
    assert config.feishu_recall_enabled() is False
    assert config.feishu_handoff_enabled() is False
    assert config.feishu_reply_mention_sender_enabled() is False
    assert config.feishu_live_send_allowed() is False


def test_feishu_rich_capability_switches_require_parent_gates(monkeypatch):
    for name in (
        "CEO_FEISHU_MEDIA_ENABLED",
        "CEO_FEISHU_REACTION_ENABLED",
        "CEO_FEISHU_RECALL_ENABLED",
        "CEO_FEISHU_HANDOFF_ENABLED",
        "CEO_FEISHU_REPLY_MENTION_SENDER",
    ):
        monkeypatch.setenv(name, "1")

    assert config.feishu_media_enabled() is False
    assert config.feishu_reaction_enabled() is False
    assert config.feishu_recall_enabled() is False
    assert config.feishu_handoff_enabled() is False
    assert config.feishu_reply_mention_sender_enabled() is False

    monkeypatch.setenv("CEO_FEISHU_ENABLED", "1")
    assert config.feishu_media_enabled() is True
    assert config.feishu_reaction_enabled() is False

    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    assert config.feishu_reaction_enabled() is True
    assert config.feishu_recall_enabled() is True
    assert config.feishu_handoff_enabled() is True
    assert config.feishu_reply_mention_sender_enabled() is True


def test_feishu_reply_mention_allowlist_is_strict_deduplicated_and_bounded(
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_FEISHU_REPLY_MENTION_OPEN_IDS",
        " ou_alice,ou_bob-2,ou_alice ",
    )
    assert config.feishu_reply_mention_open_ids() == (
        "ou_alice",
        "ou_bob-2",
    )

    monkeypatch.setenv(
        "CEO_FEISHU_REPLY_MENTION_OPEN_IDS",
        ",".join(f"ou_user_{index}" for index in range(21)),
    )
    with pytest.raises(ValueError, match="at most 20"):
        config.feishu_reply_mention_open_ids()


@pytest.mark.parametrize(
    "value",
    ("u_alice", "ou_", "ou_alice bob", "on_alice", "ou_张三", "ou_a/b"),
)
def test_feishu_reply_mention_allowlist_rejects_invalid_open_ids(
    monkeypatch, value
):
    monkeypatch.setenv("CEO_FEISHU_REPLY_MENTION_OPEN_IDS", value)
    with pytest.raises(ValueError, match="invalid open_id"):
        config.feishu_reply_mention_open_ids()


def test_feishu_modes_accept_known_values_and_fail_closed(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_SEND_MODE", "AUTO")
    monkeypatch.setenv("CEO_FEISHU_SECURITY_MODE", "audit")
    assert config.feishu_send_mode() == "auto"
    assert config.feishu_security_mode() == "audit"

    monkeypatch.setenv("CEO_FEISHU_SEND_MODE", "unexpected")
    monkeypatch.setenv("CEO_FEISHU_SECURITY_MODE", "compat")
    assert config.feishu_send_mode() == "confirm"
    assert config.feishu_security_mode() == "strict"


def test_feishu_numeric_settings_accept_positive_overrides(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_STALE_EVENT_SECONDS", "60")
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LIMIT", "5")
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", "3600")
    monkeypatch.setenv("CEO_FEISHU_MAX_SENDS_PER_MINUTE", "2")
    monkeypatch.setenv("CEO_FEISHU_EVENT_RETENTION_DAYS", "7")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_RETENTION_DAYS", "3")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_MAX_ASSETS", "4")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_MAX_BYTES", "1024")
    monkeypatch.setenv("CEO_FEISHU_MEDIA_EVENT_MAX_BYTES", "2048")

    assert config.feishu_stale_event_seconds() == 60
    assert config.feishu_context_limit() == 5
    assert config.feishu_context_lookback_seconds() == 3600
    assert config.feishu_max_sends_per_minute() == 2
    assert config.feishu_event_retention_days() == 7
    assert config.feishu_media_retention_days() == 3
    assert config.feishu_media_max_assets() == 4
    assert config.feishu_media_max_bytes() == 1024
    assert config.feishu_media_event_max_bytes() == 2048


@pytest.mark.parametrize(
    ("name", "getter"),
    [
        ("CEO_FEISHU_STALE_EVENT_SECONDS", config.feishu_stale_event_seconds),
        ("CEO_FEISHU_CONTEXT_LIMIT", config.feishu_context_limit),
        (
            "CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS",
            config.feishu_context_lookback_seconds,
        ),
        ("CEO_FEISHU_MAX_SENDS_PER_MINUTE", config.feishu_max_sends_per_minute),
        ("CEO_FEISHU_EVENT_RETENTION_DAYS", config.feishu_event_retention_days),
        ("CEO_FEISHU_MEDIA_RETENTION_DAYS", config.feishu_media_retention_days),
        ("CEO_FEISHU_MEDIA_MAX_ASSETS", config.feishu_media_max_assets),
        ("CEO_FEISHU_MEDIA_MAX_BYTES", config.feishu_media_max_bytes),
        (
            "CEO_FEISHU_MEDIA_EVENT_MAX_BYTES",
            config.feishu_media_event_max_bytes,
        ),
    ],
)
def test_feishu_numeric_settings_reject_zero(monkeypatch, name, getter):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="greater than zero"):
        getter()


def test_feishu_context_lookback_is_bounded_by_retention_and_30_days(
    monkeypatch,
):
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", str(30 * 86400))
    assert config.feishu_context_lookback_seconds() == 30 * 86400

    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", str(30 * 86400 + 1))
    with pytest.raises(ValueError, match="must not exceed 2592000"):
        config.feishu_context_lookback_seconds()


def test_feishu_context_limit_has_a_hard_upper_bound(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LIMIT", "100")
    assert config.feishu_context_limit() == 100

    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LIMIT", "101")
    with pytest.raises(ValueError, match="must not exceed 100"):
        config.feishu_context_limit()

    monkeypatch.setenv("CEO_FEISHU_EVENT_RETENTION_DAYS", "7")
    monkeypatch.setenv("CEO_FEISHU_CONTEXT_LOOKBACK_SECONDS", str(7 * 86400 + 1))
    with pytest.raises(ValueError, match="must not exceed 604800"):
        config.feishu_context_lookback_seconds()


def test_feishu_app_id_is_trimmed(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_APP_ID", "  cli_test  ")
    assert config.feishu_app_id() == "cli_test"


def test_feishu_handoff_allowlist_is_trimmed_deduplicated_and_bounded(monkeypatch):
    monkeypatch.setenv(
        "CEO_FEISHU_HANDOFF_OPEN_IDS", " ou_a,ou_b, ou_a, ,ou_c "
    )
    assert config.feishu_handoff_open_ids() == ("ou_a", "ou_b", "ou_c")

    monkeypatch.setenv(
        "CEO_FEISHU_HANDOFF_OPEN_IDS",
        ",".join(f"ou_{number}" for number in range(21)),
    )
    with pytest.raises(ValueError, match="at most 20"):
        config.feishu_handoff_open_ids()


@pytest.mark.parametrize(
    ("name", "getter", "value"),
    [
        ("CEO_FEISHU_MEDIA_MAX_ASSETS", config.feishu_media_max_assets, "9"),
        (
            "CEO_FEISHU_MEDIA_MAX_BYTES",
            config.feishu_media_max_bytes,
            str((20 * 1024 * 1024) + 1),
        ),
        (
            "CEO_FEISHU_MEDIA_EVENT_MAX_BYTES",
            config.feishu_media_event_max_bytes,
            str((32 * 1024 * 1024) + 1),
        ),
    ],
)
def test_feishu_media_limits_reject_unsafe_upper_bounds(
    monkeypatch, name, getter, value
):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="must not exceed"):
        getter()


def test_feishu_app_secret_prefers_keychain(monkeypatch):
    import keyring

    calls = []

    def get_password(service, username):
        calls.append((service, username))
        return "  keychain-secret  "

    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setenv("CEO_FEISHU_APP_SECRET", "env-secret")

    assert config.feishu_app_secret() == "keychain-secret"
    assert calls == [
        (
            config.FEISHU_KEYRING_SERVICE,
            config.FEISHU_KEYRING_APP_SECRET_USERNAME,
        )
    ]


def test_feishu_app_secret_uses_env_fallback_when_keychain_is_empty(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *_: None)
    monkeypatch.setenv("CEO_FEISHU_APP_SECRET", "  env-secret  ")

    assert config.feishu_app_secret() == "env-secret"


def test_feishu_app_secret_does_not_surface_keychain_exception(monkeypatch):
    import keyring

    leaked_secret = "must-not-appear"

    def fail_without_propagating(*_):
        raise RuntimeError(f"backend failed with {leaked_secret}")

    monkeypatch.setattr(keyring, "get_password", fail_without_propagating)
    monkeypatch.setenv("CEO_FEISHU_APP_SECRET", "safe-fallback")

    assert config.feishu_app_secret() == "safe-fallback"
