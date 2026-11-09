"""Regression: native Codex login-required signatures from current CLI.

Reply attempt 17034 (CEO daily report): the native local OAuth refresh
token was invalidated server-side. Codex CLI reports the condition with
the ``Failed to refresh token ... invalid_refresh_token ... Please log out
and sign in again`` log lines and the task-complete error ``workspace
routing discovery unauthorized (401)``. The login-required markers only
covered the older wordings (``session has ended``, ``invalid refresh
token``, ``token_invalidated``), so the turn fell into the unclassified
catch-all, the route was never paused and the generic process failure
consumed the whole consumer turn budget.
"""
import json
from pathlib import Path

import pytest

from app.agent_turn_runner import _is_terminal_codex_auth_failure, _process_failure_code
from app.agent_runtime_config import load_runtime_config
from app.codex_failure import is_codex_login_required_error
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import ProcessRunResult
from tests.runtime_route_env import codex_api_env

REFRESH_TOKEN_STDERR = (
    "2026-11-08T14:02:41.623523Z ERROR codex_login::auth::manager: "
    "Failed to refresh token status=401 Unauthorized "
    'detail=TokenErrorDetail { error_code: Some("invalid_refresh_token"), '
    'error_message: Some("Could not validate your refresh token. '
    'Please try signing in again."), .. } | '
    "2026-11-08T14:02:41.623581Z ERROR codex_login::auth::manager: "
    "Failed to refresh token: Your access token could not be refreshed. "
    "Please log out and sign in again."
)

ROUTING_DISCOVERY_STDOUT = json.dumps(
    {
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "message": "workspace routing discovery unauthorized (401)",
            "codex_error_info": "other",
        },
    }
)


def test_current_cli_refresh_token_signatures_are_login_required():
    assert is_codex_login_required_error(REFRESH_TOKEN_STDERR)
    assert is_codex_login_required_error(ROUTING_DISCOVERY_STDOUT)


def test_legacy_login_required_signatures_stay_login_required():
    assert is_codex_login_required_error("failed to refresh token: session has ended")
    assert is_codex_login_required_error(
        "failed to refresh token: invalid refresh token"
    )
    assert is_codex_login_required_error("token_invalidated")


def test_unrelated_failures_are_not_login_required():
    assert not is_codex_login_required_error(
        "unexpected status 401 unauthorized"
    )
    assert not is_codex_login_required_error(
        "stream disconnected before completion"
    )
    assert not is_codex_login_required_error("workspace is out of credits")


@pytest.fixture
def adapter(tmp_path: Path):
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            **codex_api_env("service-secret"),
        }
    )
    return CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")


def test_invalid_refresh_token_turn_is_authentication_failure(adapter):
    failure = adapter.classify_failure(
        stdout=ROUTING_DISCOVERY_STDOUT,
        stderr=REFRESH_TOKEN_STDERR,
        returncode=1,
    )

    assert failure.failure_class.value == "authentication"
    assert failure.code == "codex_login_required"
    assert failure.failover_permitted is True
    assert failure.route_pause_required is True


def test_routing_discovery_401_alone_is_login_required(adapter):
    failure = adapter.classify_failure(
        stdout=ROUTING_DISCOVERY_STDOUT, stderr="", returncode=1
    )

    assert failure.failure_class.value == "authentication"
    assert failure.code == "codex_login_required"
    assert failure.route_pause_required is True


def test_process_failure_code_reports_login_required_not_process_failed():
    process = ProcessRunResult(1, ROUTING_DISCOVERY_STDOUT, REFRESH_TOKEN_STDERR)
    code = _process_failure_code(process)

    assert code.startswith("codex_login_required:")
    assert _is_terminal_codex_auth_failure(code)


def test_provider_auth_run_code_stays_terminal():
    process = ProcessRunResult(
        1,
        "",
        "unexpected status 401 unauthorized: missing bearer or basic "
        "authentication, url: https://api.openai.com/v1/responses",
    )
    code = _process_failure_code(process)

    assert code.startswith("codex_provider_auth_failed:")
    assert _is_terminal_codex_auth_failure(code)
