import json
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_effects import McpToolEffectRegistry
from app.agent_result import EffectKind
from app.agent_runtime_contracts import RuntimeFailureClass
from app.claude_runtime_adapter import (
    ClaudeCommandPolicy,
    ClaudeEventPolicyError,
    ClaudeRuntimeAdapter,
    ClaudeRuntimeResultError,
    ClaudeTerminalProof,
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


@pytest.fixture
def normalizer(adapter):
    return adapter.new_event_normalizer()


def test_claude_command_is_noninteractive_stream_json_and_prompt_free(
    adapter, route
):
    prompt = "private business prompt"

    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=4,
    )

    assert command[:2] == ["claude-test", "-p"]
    assert command[command.index("--input-format") + 1] == "text"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert command[command.index("--model") + 1] == "claude-sonnet-test"
    assert command[command.index("--max-turns") + 1] == "4"
    assert "--bare" in command
    assert "--strict-mcp-config" in command
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--tools") + 1] == ""
    assert "--disallowedTools" in command
    assert "Bash" in command
    assert "Write" in command
    assert "Edit" in command
    assert "--permission-prompt-tool" not in command
    assert prompt not in command
    settings = json.loads(
        Path(command[command.index("--settings") + 1]).read_text(encoding="utf-8")
    )
    mcp_config = json.loads(
        Path(command[command.index("--mcp-config") + 1]).read_text(
            encoding="utf-8"
        )
    )
    assert settings["permissions"]["allow"] == []
    assert set(settings["permissions"]["deny"]) == set(
        command[command.index("--disallowedTools") + 1 :]
    )
    assert settings["enabledMcpjsonServers"] == []
    assert mcp_config == {"mcpServers": {}}


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
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(adapter.workspace))
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


def test_normalize_session_start_uses_existing_turn_contract(normalizer):
    event = normalizer.normalize_event(SYSTEM_INIT)

    assert event == {
        "type": "turn.started",
        "session_id": "claude-session-1",
    }
    assert "credential-bearing-source-must-not-persist" not in repr(event)


def test_normalize_assistant_text_uses_agent_message_contract(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    event = normalizer.normalize_event(ASSISTANT_TEXT)

    assert event["type"] == "item.completed"
    assert event["item"] == {
        "type": "agent_message",
        "text": '{"ok":true}',
    }


def test_effectful_tool_start_is_visible_before_completion(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    event = normalizer.normalize_event(NATIVE_TOOL_START)

    assert event["type"] == "item.started"
    assert event["item"]["id"] == "toolu_native"
    assert event["item"]["metadata"]["effect"] == "effectful"
    assert event["item"]["metadata"]["capability"] == "agent_cli.dws"
    assert event["item"]["metadata"]["operation"] == "chat message send"


def test_reviewed_mcp_tool_start_and_completion_share_identity(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    started = normalizer.normalize_event(MCP_TOOL_START)
    completed = normalizer.normalize_event(
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


def test_tool_failure_uses_item_failed_contract(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(MCP_TOOL_START)

    failed = normalizer.normalize_event(
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
    normalizer, tool_name
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
        normalizer.normalize_event(SYSTEM_INIT)
        normalizer.normalize_event(event)


def test_final_result_uses_turn_completed_and_caller_parser(adapter, normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    event = normalizer.normalize_event(FINAL_RESULT)
    parsed = adapter.parse_final_result(
        normalizer=normalizer,
        proof=normalizer.terminal_proof(),
        parser=lambda raw: {"parsed": raw},
    )

    assert event == {
        "type": "turn.completed",
        "session_id": "claude-session-1",
        "result": '{"ok":true}',
    }
    assert parsed == {"parsed": '{"ok":true}'}


def test_caller_parser_failure_is_typed_and_failover_closed(adapter, normalizer):
    def reject(_raw):
        raise ValueError("shape mismatch")

    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(FINAL_RESULT)
    with pytest.raises(ClaudeRuntimeResultError) as exc:
        adapter.parse_final_result(
            normalizer=normalizer,
            proof=normalizer.terminal_proof(),
            parser=reject,
        )

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


def test_unknown_documented_event_shape_fails_closed(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    with pytest.raises(ClaudeEventPolicyError, match="claude_event_unrecognized"):
        normalizer.normalize_event(
            {
                "type": "assistant",
                "session_id": "claude-session-1",
                "message": {"role": "assistant", "content": []},
            }
        )


def test_normalizer_binds_resume_session_and_rejects_cross_session(adapter):
    normalizer = adapter.new_event_normalizer(
        expected_session_id="claude-session-1"
    )
    normalizer.normalize_event(SYSTEM_INIT)

    with pytest.raises(ClaudeEventPolicyError, match="claude_session_mismatch"):
        normalizer.normalize_event(
            ASSISTANT_TEXT | {"session_id": "different-session"}
        )


def test_normalizer_rejects_duplicate_init_and_call_id(adapter, normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    with pytest.raises(ClaudeEventPolicyError, match="claude_init_duplicate"):
        normalizer.normalize_event(SYSTEM_INIT)

    call_normalizer = adapter.new_event_normalizer()
    call_normalizer.normalize_event(SYSTEM_INIT)
    call_normalizer.normalize_event(MCP_TOOL_START)
    with pytest.raises(ClaudeEventPolicyError, match="claude_tool_id_duplicate"):
        call_normalizer.normalize_event(MCP_TOOL_START)


def test_normalizer_rejects_cross_session_tool_result(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(MCP_TOOL_START)

    with pytest.raises(ClaudeEventPolicyError, match="claude_session_mismatch"):
        normalizer.normalize_event(
            {
                "type": "user",
                "session_id": "different-session",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_memory",
                            "content": "result",
                            "is_error": False,
                        }
                    ],
                },
            }
        )


def test_normalizer_requires_closed_items_and_one_last_result(adapter, normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(MCP_TOOL_START)
    with pytest.raises(ClaudeEventPolicyError, match="claude_open_tool_items"):
        normalizer.normalize_event(FINAL_RESULT)
    with pytest.raises(ClaudeEventPolicyError, match="claude_invocation_failed"):
        normalizer.normalize_event(FINAL_RESULT)

    valid = adapter.new_event_normalizer()
    valid.normalize_event(SYSTEM_INIT)
    valid.normalize_event(MCP_TOOL_START)
    completed = valid.normalize_event(
        {
            "type": "user",
            "session_id": "claude-session-1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_memory",
                        "content": "result",
                        "is_error": False,
                    }
                ],
            },
        }
    )
    assert completed["type"] == "item.completed"
    valid.normalize_event(FINAL_RESULT)
    valid.finalize()
    with pytest.raises(ClaudeEventPolicyError, match="claude_event_after_result"):
        valid.normalize_event(ASSISTANT_TEXT)


def test_normalizer_instances_do_not_share_invocation_state(adapter):
    first = adapter.new_event_normalizer()
    second = adapter.new_event_normalizer()

    first.normalize_event(SYSTEM_INIT)
    second.normalize_event(SYSTEM_INIT | {"session_id": "claude-session-2"})

    assert first.session_id == "claude-session-1"
    assert second.session_id == "claude-session-2"


def test_documented_thinking_blocks_are_ignored_without_persistence(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    events = normalizer.normalize_events(
        {
            "type": "assistant",
            "session_id": "claude-session-1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "redacted_thinking", "data": "opaque-secret"},
                    {"type": "text", "text": '{"ok":true}'},
                ],
            },
        }
    )

    assert len(events) == 1
    assert "private reasoning" not in repr(events)
    assert "opaque-secret" not in repr(events)


def test_success_result_text_cannot_spoof_auth_failure(adapter):
    stdout = __import__("json").dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "authentication_error invalid x-api-key",
            "session_id": "claude-session-1",
        }
    )

    failure = adapter.classify_failure(stdout, "", 1)

    assert failure.failure_class is RuntimeFailureClass.UNCLASSIFIED
    assert failure.failover_permitted is False


def test_reviewed_command_policy_uses_exact_tools_without_wildcards(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "command": "/opt/service/memory-mcp",
                        "args": ["serve", "--stdio"],
                    },
                    "foreign": {"url": "https://foreign.invalid/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    policy = ClaudeCommandPolicy.reviewed(
        mcp_tools=("mcp__memory_connector__memory_recall",),
        allow_native_cli=True,
    )
    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=2,
        policy=policy,
    )

    allowed = command[command.index("--allowedTools") + 1]
    assert allowed == "mcp__ceo_runtime_permission__permission_prompt"
    assert "*" not in allowed
    assert "mcp__memory_connector" not in allowed
    assert command[command.index("--tools") + 1] == "Bash"

    settings = json.loads(
        Path(command[command.index("--settings") + 1]).read_text(encoding="utf-8")
    )
    mcp_config = json.loads(
        Path(command[command.index("--mcp-config") + 1]).read_text(
            encoding="utf-8"
        )
    )
    permission_server = mcp_config["mcpServers"]["ceo_runtime_permission"]
    policy_path = Path(permission_server["args"][-1])
    broker_policy = json.loads(policy_path.read_text(encoding="utf-8"))

    assert settings["permissions"]["allow"] == []
    assert "Bash" not in settings["permissions"]["deny"]
    assert settings["enabledMcpjsonServers"] == [
        "ceo_runtime_permission",
        "memory_connector",
    ]
    assert set(mcp_config["mcpServers"]) == {
        "ceo_runtime_permission",
        "memory_connector",
    }
    memory_transport = mcp_config["mcpServers"]["memory_connector"]
    assert memory_transport["type"] == "stdio"
    assert memory_transport["command"] != "/opt/service/memory-mcp"
    assert memory_transport["args"] == [
        "-m",
        "app.claude_mcp_proxy",
        "--exec",
        "/opt/service/memory-mcp",
        "serve",
        "--stdio",
    ]
    assert "foreign" not in mcp_config["mcpServers"]
    assert broker_policy == {
        "allowed_mcp_tools": ["mcp__memory_connector__memory_recall"],
        "allow_native_cli": True,
    }


def test_reviewed_mcp_policy_rejects_unreviewed_or_missing_transport(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"foreign": {"url": "https://foreign.invalid/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))

    with pytest.raises(ValueError, match="reviewed MCP tool"):
        adapter.build_command(
            route=route,
            session_id=None,
            max_turns=2,
            policy=ClaudeCommandPolicy.reviewed(
                mcp_tools=("mcp__foreign__write",)
            ),
        )
    with pytest.raises(ValueError, match="transport"):
        adapter.build_command(
            route=route,
            session_id=None,
            max_turns=2,
            policy=ClaudeCommandPolicy.reviewed(
                mcp_tools=("mcp__memory_connector__memory_recall",)
            ),
        )


def test_reviewed_mcp_policy_exposes_only_local_service_proxy(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "url": "https://memory.example.test/mcp"
                    },
                    "foreign": {"url": "https://foreign.invalid/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))

    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=2,
        policy=ClaudeCommandPolicy.reviewed(
            mcp_tools=("mcp__memory_connector__memory_recall",)
        ),
    )
    mcp_config = json.loads(
        Path(command[command.index("--mcp-config") + 1]).read_text(
            encoding="utf-8"
        )
    )

    memory_transport = mcp_config["mcpServers"]["memory_connector"]
    assert memory_transport["type"] == "http"
    assert memory_transport["url"].startswith("http://127.0.0.1:")
    assert "memory.example.test" not in json.dumps(mcp_config)
    assert "foreign" not in mcp_config["mcpServers"]
    assert "mcp__memory_connector__memory_recall" not in command[
        command.index("--allowedTools") + 1 :
    ]


def test_multiblock_event_failure_is_atomic_and_terminal(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    event = {
        "type": "assistant",
        "session_id": "claude-session-1",
        "message": {
            "role": "assistant",
            "content": [
                MCP_TOOL_START["message"]["content"][0],
                {
                    "type": "tool_use",
                    "id": "toolu_forbidden",
                    "name": "Write",
                    "input": {"path": "/tmp/no"},
                },
            ],
        },
    }

    with pytest.raises(ClaudeEventPolicyError, match="claude_tool_unreviewed"):
        normalizer.normalize_events(event)
    with pytest.raises(ClaudeEventPolicyError, match="claude_invocation_failed"):
        normalizer.normalize_event(FINAL_RESULT)


def test_parser_rejects_raw_result_without_terminal_state_proof(adapter):
    with pytest.raises(ClaudeRuntimeResultError) as exc:
        adapter.parse_final_result(  # type: ignore[arg-type]
            normalizer=adapter.new_event_normalizer(),
            proof=FINAL_RESULT,
            parser=lambda raw: raw,
        )

    assert exc.value.failure.code == "claude_result_incomplete"


def test_terminal_proof_is_owner_bound_unforgeable_and_single_consume(
    adapter, config, tmp_path
):
    first = adapter.new_event_normalizer()
    second = adapter.new_event_normalizer()
    first.normalize_event(SYSTEM_INIT)
    first.normalize_event(FINAL_RESULT)
    proof = first.terminal_proof()
    forged = ClaudeTerminalProof(
        result=proof.result,
        session_id=proof.session_id,
        nonce=proof.nonce,
    )
    other_adapter = ClaudeRuntimeAdapter(
        workspace=tmp_path,
        config=config,
        claude_bin="claude-other",
    )

    for normalizer, candidate in ((second, proof), (first, forged)):
        with pytest.raises(ClaudeRuntimeResultError) as exc:
            adapter.parse_final_result(
                normalizer=normalizer,
                proof=candidate,
                parser=lambda raw: raw,
            )
        assert exc.value.failure.code == "claude_result_incomplete"
    with pytest.raises(ClaudeRuntimeResultError):
        other_adapter.parse_final_result(
            normalizer=first,
            proof=proof,
            parser=lambda raw: raw,
        )

    assert adapter.parse_final_result(
        normalizer=first,
        proof=proof,
        parser=lambda raw: raw,
    ) == '{"ok":true}'
    with pytest.raises(ClaudeRuntimeResultError):
        adapter.parse_final_result(
            normalizer=first,
            proof=proof,
            parser=lambda raw: raw,
        )


def test_terminal_proof_is_consumed_even_when_caller_parser_fails(adapter):
    normalizer = adapter.new_event_normalizer()
    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(FINAL_RESULT)
    proof = normalizer.terminal_proof()

    with pytest.raises(ClaudeRuntimeResultError):
        adapter.parse_final_result(
            normalizer=normalizer,
            proof=proof,
            parser=lambda _raw: (_ for _ in ()).throw(ValueError("invalid")),
        )
    with pytest.raises(ClaudeRuntimeResultError):
        adapter.parse_final_result(
            normalizer=normalizer,
            proof=proof,
            parser=lambda raw: raw,
        )


def test_reviewed_transport_env_is_exact_and_secrets_stay_out_of_files(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "url": "https://memory.example.test/mcp",
                        "bearer_token_env_var": "CONNECTOR_API_KEY",
                        "env_http_headers": {
                            "X-Memory-Auth": "MEMORY_AUTH_TYPE"
                        },
                    },
                    "foreign": {
                        "url": "https://foreign.invalid/mcp",
                        "bearer_token_env_var": "FOREIGN_API_KEY",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    monkeypatch.setenv("CONNECTOR_API_KEY", "raw-memory-secret")
    monkeypatch.setenv("MEMORY_AUTH_TYPE", "raw-auth-secret")
    monkeypatch.setenv("FOREIGN_API_KEY", "raw-foreign-secret")
    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=2,
        policy=ClaudeCommandPolicy.reviewed(
            mcp_tools=("mcp__memory_connector__memory_recall",)
        ),
    )

    child_env = adapter.build_env(route, command=command)
    mcp_path = Path(command[command.index("--mcp-config") + 1])
    settings_path = Path(command[command.index("--settings") + 1])
    mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
    broker_policy_path = Path(
        mcp_config["mcpServers"]["ceo_runtime_permission"]["args"][-1]
    )
    serialized = "\n".join(
        [
            *command,
            mcp_path.read_text(),
            settings_path.read_text(),
            broker_policy_path.read_text(),
        ]
    )

    assert "CONNECTOR_API_KEY" not in child_env
    assert "MEMORY_AUTH_TYPE" not in child_env
    assert "FOREIGN_API_KEY" not in child_env
    assert "raw-memory-secret" not in serialized
    assert "raw-auth-secret" not in serialized
    assert "raw-foreign-secret" not in serialized
    assert "CONNECTOR_API_KEY" not in serialized
    assert "MEMORY_AUTH_TYPE" not in serialized


def test_normalized_tool_events_never_retain_raw_arguments_or_results(normalizer):
    secret = "runtime-event-secret"
    normalizer.normalize_event(SYSTEM_INIT)
    started = normalizer.normalize_event(
        {
            **MCP_TOOL_START,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        **MCP_TOOL_START["message"]["content"][0],
                        "input": {"query": secret},
                    }
                ],
            },
        }
    )
    completed = normalizer.normalize_event(
        {
            "type": "user",
            "session_id": "claude-session-1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_memory",
                        "content": secret,
                        "is_error": False,
                    }
                ],
            },
        }
    )

    assert secret not in repr(started)
    assert secret not in repr(completed)
    assert "arguments" not in started["item"]
    assert "result" not in completed["item"]


def test_environment_backed_mcp_args_fail_closed_before_serialization(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "command": "/opt/service/memory-mcp",
                        "args_env": "MEMORY_MCP_ARGS",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    monkeypatch.setenv("MEMORY_MCP_ARGS", '["--token","raw-args-secret"]')

    with pytest.raises(ValueError, match="args_env"):
        adapter.build_command(
            route=route,
            session_id=None,
            max_turns=2,
            policy=ClaudeCommandPolicy.reviewed(
                mcp_tools=("mcp__memory_connector__memory_recall",)
            ),
        )
    assert not any(
        "raw-args-secret" in path.read_text(encoding="utf-8")
        for path in Path(adapter._runtime_root.name).iterdir()
    )
