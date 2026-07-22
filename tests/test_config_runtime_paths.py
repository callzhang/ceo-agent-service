from pathlib import Path

import pytest

from app import config
from app import audit_web
from app.cli import WorkerSettings
from scripts import backfill_follow_up_todo_ids


def test_worker_database_defaults_to_application_support(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/example")
    monkeypatch.delenv("CEO_WORKER_DB", raising=False)

    assert config.worker_db_path() == Path(
        "/Users/example/Library/Application Support/ceo-agent-service/auto-reply.sqlite3"
    )


def test_worker_settings_database_default_is_outside_repository():
    assert WorkerSettings().db_path == (
        Path.home()
        / "Library"
        / "Application Support"
        / "ceo-agent-service"
        / "auto-reply.sqlite3"
    )


def test_audit_web_uses_shared_runtime_database(monkeypatch, tmp_path):
    expected = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(audit_web, "worker_db_path", lambda: expected)

    assert audit_web._configured_worker_db_path() == expected


def test_follow_up_backfill_defaults_to_shared_runtime_database(
    monkeypatch, tmp_path
):
    expected = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(
        backfill_follow_up_todo_ids,
        "worker_db_path",
        lambda: expected,
    )

    args = backfill_follow_up_todo_ids.build_parser().parse_args([])

    assert args.db == str(expected)


@pytest.mark.parametrize(
    "updates",
    [
        {"INVALID-NAME": "value"},
        {"SAFE_VALUE": "line one\nCEO_FEISHU_APP_SECRET=injected"},
        {"SAFE_VALUE": "line one\rCEO_FEISHU_APP_SECRET=injected"},
        {"SAFE_VALUE": "value\x00suffix"},
    ],
)
def test_write_env_values_rejects_unsafe_updates_atomically(tmp_path, updates):
    env_path = tmp_path / ".env"
    original = "CEO_FEISHU_APP_SECRET=original\nSAFE_VALUE=original\n"
    env_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        config.write_env_values(updates, env_path)

    assert env_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "reference",
    ["$CEO_FEISHU_APP_SECRET", "${CEO_FEISHU_APP_SECRET}"],
)
def test_write_env_values_rejects_sensitive_aliases(tmp_path, reference):
    env_path = tmp_path / ".env"
    original = "CEO_FEISHU_APP_SECRET=original\nCEO_WORKSPACE=/tmp/original\n"
    env_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        config.write_env_values({"CEO_WORKSPACE": reference}, env_path)

    assert env_path.read_text(encoding="utf-8") == original


def test_env_expansion_preserves_safe_sources_but_not_secret_aliases(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CEO_FEISHU_APP_SECRET=runtime-secret\n"
        "SAFE_HOME_PATH=$HOME/runtime\n"
        "SAFE_ALIAS=$CEO_FEISHU_APP_SECRET\n"
        "SAFE_BRACED_ALIAS=${CEO_FEISHU_APP_SECRET}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", "/Users/example")
    monkeypatch.setenv("CEO_FEISHU_APP_SECRET", "runtime-secret")

    raw = config.read_env_file_raw(env_path)
    expanded = config.read_env_file(env_path)

    assert raw["SAFE_HOME_PATH"] == "$HOME/runtime"
    assert expanded["SAFE_HOME_PATH"] == "/Users/example/runtime"
    assert expanded["SAFE_ALIAS"] == "$CEO_FEISHU_APP_SECRET"
    assert expanded["SAFE_BRACED_ALIAS"] == "${CEO_FEISHU_APP_SECRET}"
    assert "runtime-secret" not in expanded["SAFE_ALIAS"]
    assert "runtime-secret" not in expanded["SAFE_BRACED_ALIAS"]


@pytest.mark.parametrize(
    "key",
    ["PAT", "GITHUB_PAT", "PAT_VENDOR", "GITHUB_PAT_READ"],
)
def test_sensitive_env_key_recognizes_pat_tokens(key):
    assert config.is_sensitive_env_key(key)


@pytest.mark.parametrize(
    "key",
    ["PATH", "PYTHONPATH", "PATTERN", "COMPAT_MODE", "CEO_WORK_PROFILE_PATH"],
)
def test_sensitive_env_key_does_not_confuse_path_or_pattern_with_pat(key):
    assert not config.is_sensitive_env_key(key)


def test_sensitive_provenance_traces_raw_aliases_and_handles_cycles(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CEO_SECRET=synthetic-secret-value\n"
        "SAFE_ALIAS=$CEO_SECRET\n"
        "CEO_WORKSPACE=$SAFE_ALIAS\n"
        "PURE_LOOP_A=$PURE_LOOP_B\n"
        "PURE_LOOP_B=$PURE_LOOP_A\n"
        "MIXED_LOOP_A=$MIXED_LOOP_B\n"
        "MIXED_LOOP_B=$MIXED_LOOP_A:$CEO_SECRET\n",
        encoding="utf-8",
    )
    for key in (
        "CEO_SECRET",
        "SAFE_ALIAS",
        "CEO_WORKSPACE",
        "PURE_LOOP_A",
        "PURE_LOOP_B",
        "MIXED_LOOP_A",
        "MIXED_LOOP_B",
    ):
        monkeypatch.delenv(key, raising=False)

    raw = config.read_env_file_raw(env_path)
    expanded = config.read_env_file(env_path)

    assert config.env_value_references_sensitive_key(
        raw["CEO_WORKSPACE"], raw_values=raw, environment={}
    )
    assert expanded["CEO_WORKSPACE"] == "$SAFE_ALIAS"
    assert not config.env_value_references_sensitive_key(
        raw["PURE_LOOP_A"], raw_values=raw, environment={}
    )
    assert config.env_value_references_sensitive_key(
        raw["MIXED_LOOP_A"], raw_values=raw, environment={}
    )


def test_sensitive_provenance_fails_closed_at_maximum_depth():
    raw = {
        f"SAFE_ALIAS_{index}": f"$SAFE_ALIAS_{index + 1}"
        for index in range(4)
    }
    raw["SAFE_ALIAS_4"] = "safe-terminal"

    assert config.env_value_references_sensitive_key(
        "$SAFE_ALIAS_0",
        raw_values=raw,
        environment={},
        max_depth=2,
    )


def test_sensitive_provenance_detects_inherited_and_embedded_secret_values():
    secret = "synthetic-secret-value"
    raw = {"CEO_SECRET": secret}
    environment = {
        "CEO_SECRET": secret,
        "SAFE_ALIAS": f"https://example.invalid/{secret}/callback",
    }

    assert config.env_value_references_sensitive_key(
        "$SAFE_ALIAS",
        raw_values=raw,
        environment=environment,
    )
    assert config.env_value_references_sensitive_key(
        f"prefix-{secret}-suffix",
        raw_values=raw,
        environment=environment,
    )


def test_write_env_values_rejects_recursive_sensitive_alias_atomically(tmp_path):
    env_path = tmp_path / ".env"
    original = (
        "CEO_SECRET=synthetic-secret-value\n"
        "SAFE_ALIAS=$CEO_SECRET\n"
        "CEO_WORKSPACE=/tmp/original\n"
        "CEO_PRODUCER_INTERVAL_SECONDS=60\n"
    )
    env_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        config.write_env_values(
            {
                "CEO_PRODUCER_INTERVAL_SECONDS": "99",
                "CEO_WORKSPACE": "$SAFE_ALIAS",
            },
            env_path,
        )

    assert env_path.read_text(encoding="utf-8") == original


def test_write_env_values_rejects_inherited_secret_alias_atomically(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    secret = "synthetic-secret-value"
    original = (
        f"CEO_SECRET={secret}\n"
        "CEO_WORKSPACE=/tmp/original\n"
        "CEO_PRODUCER_INTERVAL_SECONDS=60\n"
    )
    env_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("SAFE_ALIAS", f"prefix-{secret}-suffix")

    with pytest.raises(ValueError):
        config.write_env_values(
            {
                "CEO_PRODUCER_INTERVAL_SECONDS": "99",
                "CEO_WORKSPACE": "$SAFE_ALIAS",
            },
            env_path,
        )

    assert env_path.read_text(encoding="utf-8") == original
