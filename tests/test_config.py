from datetime import timedelta

import pytest

from app.config import env_duration, parse_duration_value


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
