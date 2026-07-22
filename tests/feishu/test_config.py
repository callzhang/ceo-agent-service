import pytest

from app import config


FEISHU_ENV_NAMES = (
    "CEO_FEISHU_ENABLED",
    "CEO_FEISHU_SENDER_ENABLED",
    "CEO_FEISHU_SEND_MODE",
    "CEO_FEISHU_SECURITY_MODE",
    "CEO_FEISHU_STALE_EVENT_SECONDS",
    "CEO_FEISHU_CONTEXT_LIMIT",
    "CEO_FEISHU_MAX_SENDS_PER_MINUTE",
    "CEO_FEISHU_EVENT_RETENTION_DAYS",
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
    assert config.feishu_live_send_allowed() is False
    assert config.feishu_send_mode() == "confirm"
    assert config.feishu_security_mode() == "strict"
    assert config.feishu_stale_event_seconds() == 300
    assert config.feishu_context_limit() == 20
    assert config.feishu_max_sends_per_minute() == 10
    assert config.feishu_event_retention_days() == 30
    assert config.feishu_app_id() == ""


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_feishu_switches_require_explicit_truthy_values(monkeypatch, value):
    monkeypatch.setenv("CEO_FEISHU_ENABLED", value)
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", value)

    assert config.feishu_enabled() is True
    assert config.feishu_sender_enabled() is True


def test_feishu_live_send_requires_both_outbound_gates(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    assert config.feishu_live_send_allowed() is False

    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "0")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "0")
    assert config.feishu_live_send_allowed() is False

    monkeypatch.setenv("CEO_FEISHU_SENDER_ENABLED", "1")
    assert config.feishu_live_send_allowed() is True

    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "false")
    assert config.feishu_live_send_allowed() is False


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
    monkeypatch.setenv("CEO_FEISHU_MAX_SENDS_PER_MINUTE", "2")
    monkeypatch.setenv("CEO_FEISHU_EVENT_RETENTION_DAYS", "7")

    assert config.feishu_stale_event_seconds() == 60
    assert config.feishu_context_limit() == 5
    assert config.feishu_max_sends_per_minute() == 2
    assert config.feishu_event_retention_days() == 7


@pytest.mark.parametrize(
    ("name", "getter"),
    [
        ("CEO_FEISHU_STALE_EVENT_SECONDS", config.feishu_stale_event_seconds),
        ("CEO_FEISHU_CONTEXT_LIMIT", config.feishu_context_limit),
        ("CEO_FEISHU_MAX_SENDS_PER_MINUTE", config.feishu_max_sends_per_minute),
        ("CEO_FEISHU_EVENT_RETENTION_DAYS", config.feishu_event_retention_days),
    ],
)
def test_feishu_numeric_settings_reject_zero(monkeypatch, name, getter):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="greater than zero"):
        getter()


def test_feishu_app_id_is_trimmed(monkeypatch):
    monkeypatch.setenv("CEO_FEISHU_APP_ID", "  cli_test  ")
    assert config.feishu_app_id() == "cli_test"


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
