import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_effects import McpToolEffectRegistry
from app.agent_result import EffectKind
from app.agent_runtime_contracts import RuntimeFailureClass
from app.claude_runtime_adapter import (
    ClaudeEventPolicyError,
    ClaudeRuntimeAdapter,
    ClaudeRuntimeResultError,
)
from app.native_cli_metadata import NativeCliMetadataClassifier


SYSTEM_INIT = {
    "type": "system",
    "subtype": "init",
    "session_id": "claude-session-1",
    "cwd": "/sanitized",
    "tools": ["mcp__memory_connector__memory_recall"],
    "apiKeySource": "credential-bearing-source-must-not-persist",
}
ASSISTANT_TEXT = {
    "type": "assistant",
    "session_id": "claude-session-1",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": '{"ok":true}'}],
    },
}
MCP_TOOL_START = {
    "type": "assistant",
    "session_id": "claude-session-1",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_memory",
                "name": "mcp__memory_connector__memory_recall",
                "input": {"query": "synthetic"},
            }
        ],
    },
}
NATIVE_TOOL_START = {
    "type": "assistant",
    "session_id": "claude-session-1",
    "message": {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_native",
                "name": "Bash",
                "input": {
                    "command": "dws chat message send --group cid --text ok --yes"
                },
            }
        ],
    },
}
FINAL_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": '{"ok":true}',
    "session_id": "claude-session-1",
}


@pytest.fixture
def config():
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_MODEL": "claude-sonnet-test",
            "CEO_CLAUDE_API_KEY": "anthropic-secret",
        }
    )


@pytest.fixture
def route(config):
    return config.routes[0]


@pytest.fixture
def adapter(tmp_path, config):
    return ClaudeRuntimeAdapter(
        workspace=tmp_path,
        config=config,
        claude_bin="claude-test",
        effect_registry=McpToolEffectRegistry(
            {
                ("memory_connector", "memory_recall"): EffectKind.READ_ONLY,
                ("agent_cli", "execute_reviewed_write"): EffectKind.EFFECTFUL,
            }
        ),
        native_cli_classifier=NativeCliMetadataClassifier(
            reviewed_effects={
                ("dws", "chat message send"): EffectKind.EFFECTFUL,
            }
        ),
    )


def test_claude_command_is_noninteractive_stream_json_and_prompt_free(
    adapter, route
):
    prompt = "private business prompt"

    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=4,
    )

    assert command == [
        "claude-test",
        "-p",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--model",
        "claude-sonnet-test",
        "--max-turns",
        "4",
        "--verbose",
    ]
    assert prompt not in command


def test_claude_command_resumes_only_the_selected_session(adapter, route):
    command = adapter.build_command(
        route=route,
        session_id="claude-session-1",
        max_turns=2,
    )

    assert command[-2:] == ["--resume", "claude-session-1"]


def test_claude_child_receives_only_configured_anthropic_credential(
    adapter, route, monkeypatch
):
    ambient = {
        "OPENAI_API_KEY": "openai-secret",
        "CODEX_API_KEY": "codex-secret",
        "CEO_CODEX_API_KEY": "ceo-codex-secret",
        "ANTHROPIC_API_KEY": "ambient-anthropic-secret",
        "ANTHROPIC_AUTH_TOKEN": "ambient-token",
        "CEO_CLAUDE_API_KEY": "ambient-ceo-secret",
        "UNRELATED_SERVICE_TOKEN": "unrelated-secret",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)

    env = adapter.build_env(route)

    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret"
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CEO_CODEX_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CEO_CLAUDE_API_KEY" not in env
    assert "UNRELATED_SERVICE_TOKEN" not in env


def test_claude_adapter_rejects_unconfigured_or_codex_route(adapter, config):
    codex = load_runtime_config({}).routes[0]
    with pytest.raises(ValueError, match="unsupported runtime route"):
        adapter.build_env(codex)

    other = config.routes[0].model_copy(update={"model": "different"})
    with pytest.raises(ValueError, match="not configured"):
        adapter.build_command(route=other, session_id=None, max_turns=1)


def test_claude_adapter_requires_positive_bounded_turns(adapter, route):
    with pytest.raises(ValueError, match="max_turns"):
        adapter.build_command(route=route, session_id=None, max_turns=0)


def test_normalize_session_start_uses_existing_turn_contract(adapter):
    event = adapter.normalize_event(SYSTEM_INIT)

    assert event == {
        "type": "turn.started",
        "session_id": "claude-session-1",
    }
    assert "credential-bearing-source-must-not-persist" not in repr(event)


def test_normalize_assistant_text_uses_agent_message_contract(adapter):
    event = adapter.normalize_event(ASSISTANT_TEXT)

    assert event["type"] == "item.completed"
    assert event["item"] == {
        "type": "agent_message",
        "text": '{"ok":true}',
    }


def test_effectful_tool_start_is_visible_before_completion(adapter):
    event = adapter.normalize_event(NATIVE_TOOL_START)

    assert event["type"] == "item.started"
    assert event["item"]["id"] == "toolu_native"
    assert event["item"]["metadata"]["effect"] == "effectful"
    assert event["item"]["metadata"]["capability"] == "agent_cli.dws"
    assert event["item"]["metadata"]["operation"] == "chat message send"


def test_reviewed_mcp_tool_start_and_completion_share_identity(adapter):
    started = adapter.normalize_event(MCP_TOOL_START)
    completed = adapter.normalize_event(
        {
            "type": "user",
            "session_id": "claude-session-1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_memory",
                        "content": "synthetic result",
                        "is_error": False,
                    }
                ],
            },
        }
    )

    assert started["type"] == "item.started"
    assert started["item"]["metadata"]["effect"] == "read_only"
    assert completed["type"] == "item.completed"
    assert completed["item"]["id"] == started["item"]["id"]
    assert completed["item"]["metadata"] == started["item"]["metadata"]


def test_tool_failure_uses_item_failed_contract(adapter):
    adapter.normalize_event(MCP_TOOL_START)

    failed = adapter.normalize_event(
        {
            "type": "user",
            "session_id": "claude-session-1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_memory",
                        "content": "sanitized failure",
                        "is_error": True,
                    }
                ],
            },
        }
    )

    assert failed["type"] == "item.failed"
    assert failed["item"]["status"] == "failed"


@pytest.mark.parametrize("tool_name", ["Write", "mcp__unknown__write"])
def test_unknown_write_capable_tool_fails_closed_before_execution(
    adapter, tool_name
):
    event = {
        "type": "assistant",
        "session_id": "claude-session-1",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_unknown",
                    "name": tool_name,
                    "input": {"path": "/tmp/forbidden"},
                }
            ],
        },
    }

    with pytest.raises(ClaudeEventPolicyError, match="claude_tool_unreviewed"):
        adapter.normalize_event(event)


def test_final_result_uses_turn_completed_and_caller_parser(adapter):
    event = adapter.normalize_event(FINAL_RESULT)
    parsed = adapter.parse_final_result(
        FINAL_RESULT,
        lambda raw: {"parsed": raw},
    )

    assert event == {
        "type": "turn.completed",
        "session_id": "claude-session-1",
        "result": '{"ok":true}',
    }
    assert parsed == {"parsed": '{"ok":true}'}


def test_caller_parser_failure_is_typed_and_failover_closed(adapter):
    def reject(_raw):
        raise ValueError("shape mismatch")

    with pytest.raises(ClaudeRuntimeResultError) as exc:
        adapter.parse_final_result(FINAL_RESULT, reject)

    assert exc.value.failure.failure_class is RuntimeFailureClass.RESULT
    assert exc.value.failure.code == "claude_result_validation_failed"
    assert exc.value.failure.failover_permitted is False
    assert "shape mismatch" not in exc.value.failure.detail


@pytest.mark.parametrize(
    ("stderr", "failure_class", "code", "failover"),
    [
        (
            "authentication_error: invalid x-api-key",
            RuntimeFailureClass.AUTHENTICATION,
            "claude_authentication_failed",
            True,
        ),
        (
            "rate_limit_error: overloaded, status 429",
            RuntimeFailureClass.CAPACITY,
            "claude_capacity_unavailable",
            True,
        ),
        (
            "connection reset before response",
            RuntimeFailureClass.TRANSPORT,
            "claude_transport_failed",
            True,
        ),
        (
            "session not found for resume",
            RuntimeFailureClass.SESSION,
            "claude_session_invalid",
            False,
        ),
        (
            "error_max_turns",
            RuntimeFailureClass.RESULT,
            "claude_result_incomplete",
            False,
        ),
        (
            "unexpected provider failure",
            RuntimeFailureClass.UNCLASSIFIED,
            "claude_runtime_unclassified",
            False,
        ),
    ],
)
def test_claude_failure_classification_is_typed_and_safe(
    adapter, stderr, failure_class, code, failover
):
    failure = adapter.classify_failure("", stderr, 1)

    assert failure.failure_class is failure_class
    assert failure.code == code
    assert failure.failover_permitted is failover
    assert stderr not in failure.detail


def test_timeout_is_bounded_transport_failure(adapter):
    failure = adapter.classify_failure(
        "",
        "",
        1,
        timed_out=True,
        timeout_kind="idle",
    )

    assert failure.failure_class is RuntimeFailureClass.TRANSPORT
    assert failure.code == "claude_idle_timeout"
    assert failure.failover_permitted is True


def test_unknown_documented_event_shape_fails_closed(adapter):
    with pytest.raises(ClaudeEventPolicyError, match="claude_event_unrecognized"):
        adapter.normalize_event(
            {
                "type": "assistant",
                "session_id": "claude-session-1",
                "message": {"role": "assistant", "content": []},
            }
        )
