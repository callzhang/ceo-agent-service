from datetime import timedelta

import pytest

from app.config import (
    DEFAULT_CEO_CODEX_MODEL,
    DEFAULT_CEO_CODEX_MODEL_REASONING_EFFORT,
    codex_model,
    codex_model_reasoning_effort,
    env_duration,
    parse_duration_value,
    repository_upgrade_branch,
    repository_upgrade_check_interval_seconds,
    repository_upgrade_enabled,
    repository_upgrade_remote,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("1h", timedelta(hours=1)),
        ("2d", timedelta(days=2)),
        (" 15M ", timedelta(minutes=15)),
    ],
)
def test_parse_duration_value_accepts_integer_durations(value, expected):
    assert parse_duration_value("CEO_INTERVAL", value, timedelta(seconds=1)) == expected


def test_parse_duration_value_uses_the_given_default_for_missing_values():
    default = timedelta(minutes=7)

    assert parse_duration_value("CEO_INTERVAL", None, default) == default


@pytest.mark.parametrize("value", ["", "15", "1.5m", "-1m", "seconds"])
def test_parse_duration_value_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_duration_value("CEO_INTERVAL", value, timedelta(seconds=1))


def test_env_duration_retains_environment_lookup_and_default_behavior(monkeypatch):
    default = timedelta(minutes=7)
    monkeypatch.delenv("CEO_INTERVAL", raising=False)
    assert env_duration("CEO_INTERVAL", default) == default

    monkeypatch.setenv("CEO_INTERVAL", "2h")
    assert env_duration("CEO_INTERVAL", default) == timedelta(hours=2)


def test_codex_model_settings_use_configured_values_or_service_defaults(monkeypatch):
    monkeypatch.delenv("CEO_CODEX_MODEL", raising=False)
    monkeypatch.delenv("CEO_CODEX_MODEL_REASONING_EFFORT", raising=False)

    assert codex_model() == DEFAULT_CEO_CODEX_MODEL
    assert (
        codex_model_reasoning_effort()
        == DEFAULT_CEO_CODEX_MODEL_REASONING_EFFORT
    )

    monkeypatch.setenv("CEO_CODEX_MODEL", "gpt-5.6")
    monkeypatch.setenv("CEO_CODEX_MODEL_REASONING_EFFORT", "high")

    assert codex_model() == "gpt-5.6"
    assert codex_model_reasoning_effort() == "high"


def test_repository_upgrade_configuration_has_safe_defaults_and_overrides(monkeypatch):
    for name in (
        "CEO_REPOSITORY_UPGRADE_REMOTE",
        "CEO_REPOSITORY_UPGRADE_BRANCH",
        "CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS",
        "CEO_REPOSITORY_UPGRADE_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    assert repository_upgrade_remote() == "origin"
    assert repository_upgrade_branch() == "main"
    assert repository_upgrade_check_interval_seconds() == 21600
    assert repository_upgrade_enabled() is True

    monkeypatch.setenv("CEO_REPOSITORY_UPGRADE_REMOTE", "upstream")
    monkeypatch.setenv("CEO_REPOSITORY_UPGRADE_BRANCH", "stable")
    monkeypatch.setenv("CEO_REPOSITORY_UPGRADE_CHECK_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("CEO_REPOSITORY_UPGRADE_DISABLED", "1")
    assert repository_upgrade_remote() == "upstream"
    assert repository_upgrade_branch() == "stable"
    assert repository_upgrade_check_interval_seconds() == 30
    assert repository_upgrade_enabled() is False
