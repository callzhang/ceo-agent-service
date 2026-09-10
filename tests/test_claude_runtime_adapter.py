import json
import os
from pathlib import Path

import pytest
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeFailureClass
from app.claude_runtime_adapter import (
    ClaudeCommandPolicy,
    ClaudeEventPolicyError,
    ClaudeRuntimeAdapter,
    ClaudeRuntimeResultError,
    ClaudeTerminalProof,
)

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
    runtime_adapter = ClaudeRuntimeAdapter(
        workspace=tmp_path,
        config=config,
        claude_bin="claude-test",
    )
    yield runtime_adapter
    runtime_adapter._mcp_proxy.close()


@pytest.fixture
def normalizer(adapter):
    return adapter.new_event_normalizer()


def test_normal_command_delegates_tool_review_to_claude_runtime(
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
                    "new_provider": {
                        "command": "/opt/service/new-provider-mcp",
                    },
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
        policy=ClaudeCommandPolicy.normal(),
    )
    mcp_config = json.loads(
        Path(command[command.index("--mcp-config") + 1]).read_text(encoding="utf-8")
    )

    assert set(mcp_config["mcpServers"]) == {"memory_connector", "new_provider"}
    serialized = json.dumps(mcp_config)
    assert "allowed_tool" not in serialized
    assert "effect_fence" not in serialized
    assert "ceo_runtime_permission" not in serialized
    assert "--allowedTools" not in command
    assert "--disallowedTools" not in command
    assert "--permission-prompt-tool" not in command
    assert command[command.index("--permission-mode") + 1] == "default"


@pytest.mark.parametrize(
    ("tool_name", "expected_type"),
    [
        ("mcp__new_provider__future_action", "mcp_tool_call"),
        ("Bash", "command_execution"),
        ("FutureBuiltin", "provider_tool_call"),
    ],
)
def test_normalizer_records_unknown_tools_without_application_review(
    adapter, tool_name, expected_type
):
    normalizer = adapter.new_event_normalizer()
    normalizer.normalize_event(SYSTEM_INIT)

    started = normalizer.normalize_event(
        {
            "type": "assistant",
            "session_id": "claude-session-1",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_future",
                        "name": tool_name,
                        "input": {"future": "shape"},
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
                        "tool_use_id": "toolu_future",
                        "content": {"ok": True},
                        "is_error": False,
                    }
                ],
            },
        }
    )

    assert started["item"]["type"] == expected_type
    assert completed["item"]["type"] == expected_type
    assert completed["item"]["status"] == "completed"


def test_no_tools_command_is_noninteractive_stream_json_and_prompt_free(adapter, route):
    prompt = "private business prompt"

    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=4,
        policy=ClaudeCommandPolicy.no_tools(),
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
    assert "--disallowedTools" not in command
    assert "--permission-prompt-tool" not in command
    assert prompt not in command
    settings = json.loads(
        Path(command[command.index("--settings") + 1]).read_text(encoding="utf-8")
    )
    mcp_config = json.loads(
        Path(command[command.index("--mcp-config") + 1]).read_text(encoding="utf-8")
    )
    assert settings["enableAllProjectMcpServers"] is False
    assert settings["enabledMcpjsonServers"] == []
    assert mcp_config == {"mcpServers": {}}


def test_claude_command_resumes_only_the_selected_session(adapter, route):
    command = adapter.build_command(
        route=route,
        session_id="claude-session-1",
        max_turns=2,
    )

    assert command[-2:] == ["--resume", "claude-session-1"]


@pytest.mark.parametrize(
    "session_id",
    ["--resume-other", "claude session", "claude\nsession", "claude\x00session"],
)
def test_claude_command_rejects_malformed_resume_session(adapter, route, session_id):
    with pytest.raises(ValueError, match="session_id"):
        adapter.build_command(route=route, session_id=session_id, max_turns=1)


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


def test_native_tool_start_is_visible_before_completion(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)
    event = normalizer.normalize_event(NATIVE_TOOL_START)

    assert event["type"] == "item.started"
    assert event["item"]["id"] == "toolu_native"
    assert event["item"] == {
        "type": "command_execution",
        "id": "toolu_native",
        "status": "in_progress",
        "tool": "Bash",
    }


def test_mcp_tool_start_and_completion_share_provider_identity(normalizer):
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
    assert started["item"] == {
        "type": "mcp_tool_call",
        "id": "toolu_memory",
        "status": "in_progress",
        "server": "memory_connector",
        "tool": "memory_recall",
    }
    assert completed["type"] == "item.completed"
    assert completed["item"]["id"] == started["item"]["id"]
    assert completed["item"] == {**started["item"], "status": "completed"}
    assert "synthetic result" not in repr(completed)


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
    normalizer = adapter.new_event_normalizer(expected_session_id="claude-session-1")
    normalizer.normalize_event(SYSTEM_INIT)

    with pytest.raises(ClaudeEventPolicyError, match="claude_session_mismatch"):
        normalizer.normalize_event(ASSISTANT_TEXT | {"session_id": "different-session"})


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












def test_multiblock_event_records_each_provider_tool_without_review(normalizer):
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

    normalized = normalizer.normalize_events(event)
    assert [item["item"]["type"] for item in normalized] == [
        "mcp_tool_call",
        "provider_tool_call",
    ]


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

    assert (
        adapter.parse_final_result(
            normalizer=first,
            proof=proof,
            parser=lambda raw: raw,
        )
        == '{"ok":true}'
    )
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


def test_mcp_transport_env_is_exact_and_secrets_stay_out_of_files(
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
                        "env_http_headers": {"X-Memory-Auth": "MEMORY_AUTH_TYPE"},
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
        policy=ClaudeCommandPolicy.normal(),
    )

    child_env = adapter.build_env(route, command=command)
    mcp_path = Path(command[command.index("--mcp-config") + 1])
    settings_path = Path(command[command.index("--settings") + 1])
    serialized = "\n".join(
        [
            *command,
            mcp_path.read_text(),
            settings_path.read_text(),
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
            policy=ClaudeCommandPolicy.normal(),
        )
    assert not any(
        "raw-args-secret" in path.read_text(encoding="utf-8")
        for path in Path(adapter._runtime_root.name).iterdir()
    )


def test_proxy_lifecycle_finishes_and_executor_exception_cleans_up(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"memory_connector": {"url": "http://127.0.0.1:9/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    policy = ClaudeCommandPolicy.normal()

    for _ in range(2):
        command = adapter.build_command(
            route=route, session_id=None, max_turns=2, policy=policy
        )
        settings_path = Path(command[command.index("--settings") + 1])
        mcp_path = Path(command[command.index("--mcp-config") + 1])
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert set(mcp["mcpServers"]) == {"memory_connector"}
        assert all(path.exists() for path in (settings_path, mcp_path))
        assert adapter.active_proxy_process_count == 1
        adapter.finish_invocation(command)
        assert adapter.active_proxy_process_count == 0
        assert not any(path.exists() for path in (settings_path, mcp_path))

    command = adapter.build_command(
        route=route, session_id=None, max_turns=2, policy=policy
    )
    with pytest.raises(RuntimeError, match="executor failed"):
        adapter.execute(
            command,
            lambda _command: (_ for _ in ()).throw(RuntimeError("executor failed")),
        )
    assert adapter.active_proxy_process_count == 0


def test_invocation_build_failure_rolls_back_proxy_and_all_artifacts(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"memory_connector": {"url": "http://127.0.0.1:9/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    original_write_text = Path.write_text

    def fail_mcp_write(path, *args, **kwargs):
        if path.name.startswith("mcp-"):
            raise OSError("synthetic config write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_mcp_write)
    with pytest.raises(OSError, match="synthetic config write failure"):
        adapter.build_command(
            route=route,
            session_id=None,
            max_turns=2,
            policy=ClaudeCommandPolicy.normal(),
        )

    assert adapter.active_proxy_process_count == 0
    assert list(Path(adapter._runtime_root.name).glob("settings-*.json")) == []
    assert list(Path(adapter._runtime_root.name).glob("mcp-*.json")) == []


def test_terminal_parse_closes_invocation_proxy(adapter, route, tmp_path, monkeypatch):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"memory_connector": {"url": "http://127.0.0.1:9/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=2,
        policy=ClaudeCommandPolicy.normal(),
    )
    normalizer = adapter.new_event_normalizer(command=command)
    normalizer.normalize_event(SYSTEM_INIT)
    normalizer.normalize_event(FINAL_RESULT)

    assert (
        adapter.parse_final_result(
            normalizer=normalizer,
            proof=normalizer.terminal_proof(),
            parser=lambda raw: raw,
        )
        == '{"ok":true}'
    )
    assert adapter.active_proxy_process_count == 0


def test_terminal_event_failure_closes_proxy_and_unlinks_artifacts(
    adapter, route, tmp_path, monkeypatch
):
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        json.dumps(
            {"servers": {"memory_connector": {"url": "http://127.0.0.1:9/mcp"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))
    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=2,
        policy=ClaudeCommandPolicy.normal(),
    )
    settings_path = Path(command[command.index("--settings") + 1])
    mcp_path = Path(command[command.index("--mcp-config") + 1])
    normalizer = adapter.new_event_normalizer(command=command)

    with pytest.raises(ClaudeEventPolicyError, match="claude_init_missing"):
        normalizer.normalize_event(FINAL_RESULT)

    assert adapter.active_proxy_process_count == 0
    assert not any(path.exists() for path in (settings_path, mcp_path))


@pytest.fixture
def oauth_config():
    return load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "claude_oauth"})


@pytest.fixture
def oauth_adapter(tmp_path, oauth_config):
    runtime_adapter = ClaudeRuntimeAdapter(
        workspace=tmp_path,
        config=oauth_config,
        claude_bin="claude-test",
    )
    yield runtime_adapter
    runtime_adapter._mcp_proxy.close()


def test_claude_oauth_command_defaults_to_sonnet_medium_without_bare(
    oauth_adapter, oauth_config
):
    route = oauth_config.routes[0]

    command = oauth_adapter.build_command(route=route, session_id=None, max_turns=1)

    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "medium"
    # --bare reads Anthropic auth strictly from ANTHROPIC_API_KEY, so the local
    # subscription route must not use it.
    assert "--bare" not in command
    # The remaining isolation flags still keep the caller's CLAUDE.md, skills,
    # plugins, and hooks out of a service run.
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in command


def test_claude_oauth_env_carries_no_api_key_and_no_config_dir_override(
    oauth_adapter, oauth_config, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-secret")
    monkeypatch.setenv("CEO_CLAUDE_API_KEY", "ambient-ceo-secret")

    env = oauth_adapter.build_env(oauth_config.routes[0])

    # The CLI resolves the local login from the caller's HOME, so neither an
    # ambient API key nor a redirected config dir may reach the child.
    assert "ANTHROPIC_API_KEY" not in env
    assert "CEO_CLAUDE_API_KEY" not in env
    assert "CLAUDE_CONFIG_DIR" not in env
    assert env["HOME"] == os.environ["HOME"]


def test_claude_api_command_keeps_bare_and_configured_effort(adapter, route):
    command = adapter.build_command(route=route, session_id=None, max_turns=1)

    assert "--bare" in command
    assert command[command.index("--effort") + 1] == "medium"


def test_claude_command_uses_the_requested_reasoning_effort(adapter, route):
    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=1,
        reasoning_effort="high",
    )

    assert command[command.index("--effort") + 1] == "high"

    with pytest.raises(ValueError, match="reasoning effort"):
        adapter.build_command(
            route=route,
            session_id=None,
            max_turns=1,
            reasoning_effort="ludicrous",
        )


def test_subscription_rate_limit_event_produces_no_runtime_event(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)

    assert (
        normalizer.normalize_events(
            {
                "type": "rate_limit_event",
                "session_id": "claude-session-1",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
            }
        )
        == ()
    )
    assert normalizer.normalize_events(ASSISTANT_TEXT) == (
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"ok":true}'},
        },
    )


def test_rate_limit_event_still_requires_the_active_session(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)

    with pytest.raises(ClaudeEventPolicyError, match="claude_session_mismatch"):
        normalizer.normalize_events(
            {"type": "rate_limit_event", "session_id": "other-session"}
        )


def test_rate_limit_event_before_session_init_is_accepted(normalizer):
    """A subscription transport reports its quota window before the init event."""
    assert (
        normalizer.normalize_events(
            {
                "type": "rate_limit_event",
                "session_id": "claude-session-1",
                "rate_limit_info": {"status": "allowed"},
            }
        )
        == ()
    )
    assert normalizer.normalize_event(SYSTEM_INIT) == {
        "type": "turn.started",
        "session_id": "claude-session-1",
    }


def test_thinking_budget_notice_produces_no_runtime_event(normalizer):
    """--effort raises extended thinking, whose budget notice carries no item."""
    normalizer.normalize_event(SYSTEM_INIT)

    assert (
        normalizer.normalize_events(
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens": 50,
                "estimated_tokens_delta": 50,
                "session_id": "claude-session-1",
            }
        )
        == ()
    )
    assert normalizer.normalize_events(ASSISTANT_TEXT) == (
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"ok":true}'},
        },
    )


def test_unknown_system_subtype_is_still_a_grammar_violation(normalizer):
    normalizer.normalize_event(SYSTEM_INIT)

    with pytest.raises(ClaudeEventPolicyError, match="claude_event_unrecognized"):
        normalizer.normalize_events(
            {
                "type": "system",
                "subtype": "some_future_shape",
                "session_id": "claude-session-1",
            }
        )
