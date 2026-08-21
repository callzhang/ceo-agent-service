import json
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import RuntimeFailureClass
from app.agent_runtime_probe import (
    AgentRuntimeProbe,
    RuntimeCapabilityRefresher,
    _PROBE_SCHEMA,
)
from app.agent_runtime_production import RuntimeCapabilityRegistry
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def test_probe_schema_types_its_constant_boolean() -> None:
    assert _PROBE_SCHEMA["properties"]["ok"] == {
        "type": "boolean",
        "const": True,
    }


def test_probe_accepts_non_effectful_runtime_information_item() -> None:
    from app.agent_runtime_probe import _probe_stream_failure_code

    stream = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "probe"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "error", "message": "runtime information"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"ok":true}'},
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )

    assert _probe_stream_failure_code(stream) is None


def _config(monkeypatch, *, routes: str = "codex_oauth,codex_api"):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", routes)
    monkeypatch.setenv("CEO_CODEX_API_KEY", "test-api-secret")
    monkeypatch.setenv("CEO_CLAUDE_API_KEY", "test-anthropic-secret")
    monkeypatch.setenv("CEO_RUNTIME_PROBE_INTERVAL", "5m")
    monkeypatch.setenv("CEO_RUNTIME_ROUTE_RETRY_DELAY", "30m")
    return load_runtime_config(dict(__import__("os").environ))


def _successful_probe_stream(*, session_id: str = "probe-session") -> str:
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": session_id}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps({"ok": True}),
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )


def _successful_claude_probe_stream(*, effect: bool) -> str:
    payloads = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "claude-probe-session",
        }
    ]
    if effect:
        payloads.extend(
            [
                {
                    "type": "assistant",
                    "session_id": "claude-probe-session",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_probe",
                                "name": "mcp__runtime_probe__record_effect_start",
                                "input": {"marker": "ceo-agent-runtime-probe-v1"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "session_id": "claude-probe-session",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_probe",
                                "content": '{"recorded":true}',
                                "is_error": False,
                            }
                        ],
                    },
                },
            ]
        )
    payloads.extend(
        [
            {
                "type": "assistant",
                "session_id": "claude-probe-session",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": '{"ok":true}'}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": '{"ok":true}',
                "session_id": "claude-probe-session",
            },
        ]
    )
    return "\n".join(json.dumps(payload) for payload in payloads)


def _adversarial_claude_effect_stream(mode: str) -> str:
    payloads = [
        json.loads(line)
        for line in _successful_claude_probe_stream(effect=True).splitlines()
    ]
    if mode == "failed":
        payloads[2]["message"]["content"][0]["is_error"] = True
    elif mode == "duplicate":
        second_start = json.loads(json.dumps(payloads[1]))
        second_start["message"]["content"][0]["id"] = "toolu_probe_duplicate"
        second_result = json.loads(json.dumps(payloads[2]))
        second_result["message"]["content"][0]["tool_use_id"] = "toolu_probe_duplicate"
        payloads[3:3] = [second_start, second_result]
    elif mode == "mismatch":
        payloads[2]["message"]["content"][0]["tool_use_id"] = "toolu_other"
    elif mode == "wrong_digest":
        payloads[1]["message"]["content"][0]["input"]["marker"] = "wrong-marker"
    elif mode == "extra_tool":
        payloads[1]["message"]["content"].append(
            {
                "type": "tool_use",
                "id": "toolu_foreign",
                "name": "mcp__runtime_probe__foreign",
                "input": {},
            }
        )
    elif mode == "missing_completion":
        payloads.pop(2)
    else:
        raise AssertionError(mode)
    return "\n".join(json.dumps(payload) for payload in payloads)


def _adversarial_claude_text_stream(*, effect: bool, mode: str) -> str:
    payloads = [
        json.loads(line)
        for line in _successful_claude_probe_stream(effect=effect).splitlines()
    ]
    text_index = 3 if effect else 1
    if mode == "wrong_text":
        payloads[text_index]["message"]["content"][0]["text"] = '{"ok": false}'
    elif mode == "duplicate_text":
        payloads.insert(text_index, json.loads(json.dumps(payloads[text_index])))
    elif mode == "extra_tool":
        payloads.insert(
            text_index,
            {
                "type": "assistant",
                "session_id": "claude-probe-session",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_extra",
                            "name": "mcp__runtime_probe__record_effect_start",
                            "input": {"marker": "ceo-agent-runtime-probe-v1"},
                        }
                    ],
                },
            },
        )
    elif mode == "extra_user":
        payloads.insert(
            text_index,
            {
                "type": "user",
                "session_id": "claude-probe-session",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "unexpected"}],
                },
            },
        )
    elif mode == "extra_system":
        payloads.insert(
            text_index,
            {
                "type": "system",
                "subtype": "init",
                "session_id": "claude-probe-session",
            },
        )
    elif mode == "failure":
        payloads[-1]["subtype"] = "error_during_execution"
        payloads[-1]["is_error"] = True
    else:
        raise AssertionError(mode)
    return "\n".join(json.dumps(payload) for payload in payloads)


def _adversarial_claude_terminal_stream(*, effect: bool, mode: str) -> str:
    payloads = [
        json.loads(line)
        for line in _successful_claude_probe_stream(effect=effect).splitlines()
    ]
    if mode == "integer_one":
        payloads[-1]["result"] = '{"ok":1}'
    elif mode == "float_one":
        payloads[-1]["result"] = '{"ok":1.0}'
    elif mode == "whitespace":
        payloads[-1]["result"] = '{"ok": true}'
    elif mode == "key_order":
        payloads[-1]["result"] = '{"extra":0,"ok":true}'
    elif mode == "assistant_final_mismatch":
        payloads[-1]["result"] = '{"ok": true}'
        assert payloads[-2]["message"]["content"][0]["text"] == '{"ok":true}'
    else:
        raise AssertionError(mode)
    return "\n".join(json.dumps(payload) for payload in payloads)


def test_claude_probe_proves_base_and_effect_visibility_without_business_tools(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="claude_api")
    calls = []

    def executor(command, **kwargs):
        mcp_config = (
            __import__("pathlib")
            .Path(command[command.index("--mcp-config") + 1])
            .read_text(encoding="utf-8")
        )
        calls.append((command, kwargs, mcp_config))
        return ProcessRunResult(
            0,
            _successful_claude_probe_stream(
                effect="mcp__runtime_probe__record_effect_start" in mcp_config
            ),
            "",
        )

    snapshot = AgentRuntimeProbe(
        config=config,
        claude_bin="claude-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="claude_api")

    assert snapshot.healthy is True
    assert snapshot.failure is None
    assert snapshot.capabilities == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "consumer_read_only_enforcement",
            "audit_effect_visibility",
        }
    )
    assert len(calls) == 2
    baseline, effect = calls
    assert baseline[0][0:2] == ["claude-test", "-p"]
    assert baseline[0][baseline[0].index("--tools") + 1] == ""
    assert "--strict-mcp-config" in effect[0]
    assert "--mcp-config" in effect[0]
    effect_mcp = effect[2]
    assert "mcp__runtime_probe__record_effect_start" in effect_mcp
    assert (
        "mcp__runtime_probe__record_effect_start"
        not in effect[0][effect[0].index("--allowedTools") + 1 :]
    )
    assert baseline[1]["env"]["ANTHROPIC_API_KEY"] == "test-anthropic-secret"
    assert "CEO_CLAUDE_API_KEY" not in baseline[1]["env"]
    combined = "\n".join(
        [*baseline[0], *effect[0], baseline[1]["prompt"], effect[1]["prompt"]]
    ).casefold()
    assert "dws" not in combined
    assert "lark" not in combined
    assert "memory_connector" not in combined


def test_claude_probe_fails_when_dedicated_effect_start_is_not_visible(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="claude_api")

    snapshot = AgentRuntimeProbe(
        config=config,
        claude_bin="claude-test",
        executor=lambda command, **_kwargs: ProcessRunResult(
            0,
            _successful_claude_probe_stream(effect=False),
            "",
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="claude_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_effect_visibility_missing"


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate",
        "failed",
        "mismatch",
        "wrong_digest",
        "extra_tool",
        "missing_completion",
    ],
)
def test_claude_probe_rejects_non_exact_effect_evidence(monkeypatch, tmp_path, mode):
    config = _config(monkeypatch, routes="claude_api")

    def executor(command, **_kwargs):
        mcp_config = (
            __import__("pathlib")
            .Path(command[command.index("--mcp-config") + 1])
            .read_text(encoding="utf-8")
        )
        return ProcessRunResult(
            0,
            (
                _adversarial_claude_effect_stream(mode)
                if "mcp__runtime_probe__record_effect_start" in mcp_config
                else _successful_claude_probe_stream(effect=False)
            ),
            "",
        )

    snapshot = AgentRuntimeProbe(
        config=config,
        claude_bin="claude-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="claude_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()


@pytest.mark.parametrize("phase", ["baseline", "effect"])
@pytest.mark.parametrize(
    "mode",
    [
        "wrong_text",
        "duplicate_text",
        "extra_tool",
        "extra_user",
        "extra_system",
        "failure",
    ],
)
def test_claude_probe_rejects_non_exact_normalized_grammar(
    monkeypatch, tmp_path, phase, mode
):
    config = _config(monkeypatch, routes="claude_api")

    def executor(command, **_kwargs):
        mcp_config = (
            __import__("pathlib")
            .Path(command[command.index("--mcp-config") + 1])
            .read_text(encoding="utf-8")
        )
        effect = "mcp__runtime_probe__record_effect_start" in mcp_config
        adversarial = phase == ("effect" if effect else "baseline")
        return ProcessRunResult(
            0,
            (
                _adversarial_claude_text_stream(effect=effect, mode=mode)
                if adversarial
                else _successful_claude_probe_stream(effect=effect)
            ),
            "",
        )

    snapshot = AgentRuntimeProbe(
        config=config,
        claude_bin="claude-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="claude_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()
    assert snapshot.failure is not None
    assert snapshot.failure.code in {
        "runtime_probe_grammar_invalid",
        "runtime_probe_failed",
    }


@pytest.mark.parametrize("phase", ["baseline", "effect"])
@pytest.mark.parametrize(
    "mode",
    [
        "integer_one",
        "float_one",
        "whitespace",
        "key_order",
        "assistant_final_mismatch",
    ],
)
def test_claude_probe_rejects_noncanonical_terminal_result(
    monkeypatch, tmp_path, phase, mode
):
    config = _config(monkeypatch, routes="claude_api")

    def executor(command, **_kwargs):
        mcp_config = (
            __import__("pathlib")
            .Path(command[command.index("--mcp-config") + 1])
            .read_text(encoding="utf-8")
        )
        effect = "mcp__runtime_probe__record_effect_start" in mcp_config
        adversarial = phase == ("effect" if effect else "baseline")
        return ProcessRunResult(
            0,
            (
                _adversarial_claude_terminal_stream(effect=effect, mode=mode)
                if adversarial
                else _successful_claude_probe_stream(effect=effect)
            ),
            "",
        )

    snapshot = AgentRuntimeProbe(
        config=config,
        claude_bin="claude-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="claude_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_failed"


def test_probe_requires_structured_completion(monkeypatch, tmp_path):
    config = _config(monkeypatch, routes="codex_api")
    probe = AgentRuntimeProbe(
        config=config,
        codex_bin="codex-test",
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0,
            json.dumps({"type": "turn.started"}),
            "",
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    )

    snapshot = probe.run(route_name="codex_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_incomplete"
    assert snapshot.checked_at == NOW.isoformat()
    assert snapshot.expires_at == (NOW + timedelta(minutes=5)).isoformat()


def test_probe_rejects_any_started_action_even_with_valid_final_result(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_oauth")
    stream = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "probe-session"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "memory_connector",
                        "tool": "memory_write",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps({"ok": True}),
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )
    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, stream, ""),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.healthy is False
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_policy_violation"


@pytest.mark.parametrize(
    "extra_payload",
    [
        {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
        {"type": "item.failed", "item": {"type": "agent_message"}},
        {"type": "future.dynamic.event"},
    ],
)
def test_probe_rejects_every_event_outside_exact_no_tools_grammar(
    monkeypatch, tmp_path, extra_payload
):
    config = _config(monkeypatch, routes="codex_oauth")
    stream = _successful_probe_stream().splitlines()
    stream.insert(-1, json.dumps(extra_payload))

    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, "\n".join(stream), ""),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.healthy is False
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_policy_violation"


def test_probe_uses_isolated_read_only_command_and_validates_complete_stream(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_api")
    calls = []

    def executor(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessRunResult(0, _successful_probe_stream(), "")

    snapshot = AgentRuntimeProbe(
        config=config,
        codex_bin="codex-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_api")

    assert snapshot.healthy is True
    assert snapshot.failure is None
    assert {
        "structured_output",
        "local_schema_validation",
        "consumer_read_only_enforcement",
    }.issubset(snapshot.capabilities)
    [(command, kwargs)] = calls
    assert command[:2] == ["codex-test", "exec"]
    assert "--skip-git-repo-check" in command
    assert "--json" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--cd") + 1].startswith(str(tmp_path))
    assert "tools.enabled_tools=[]" in command
    assert 'web_search="disabled"' in command
    assert kwargs["prompt"].startswith("Return only the synthetic probe result")
    assert kwargs["env"]["OPENAI_API_KEY"] == "test-api-secret"
    assert "CEO_CODEX_API_KEY" not in kwargs["env"]


def test_refresher_opens_and_closes_only_the_probed_route_pause(monkeypatch, tmp_path):
    config = _config(monkeypatch)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()
    outcomes = {
        "codex_oauth": ProcessRunResult(1, "", "login failed"),
        "codex_api": ProcessRunResult(0, _successful_probe_stream(), ""),
    }

    def executor(command, **kwargs):
        route = "codex_api" if "OPENAI_API_KEY" in kwargs["env"] else "codex_oauth"
        return outcomes[route]

    refresher = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=AgentRuntimeProbe(
            config=config,
            executor=executor,
            now=lambda: NOW,
            temporary_root=tmp_path,
        ),
        now=lambda: NOW,
    )

    first = refresher.refresh_expired(force=True)

    assert first["codex_oauth"].healthy is False
    assert first["codex_api"].healthy is True
    assert store.active_runtime_route_pause("codex_oauth", now=NOW) is not None
    assert store.active_runtime_route_pause("codex_api", now=NOW) is None
    assert registry["codex_api"].healthy is True

    outcomes["codex_oauth"] = ProcessRunResult(
        0, _successful_probe_stream(session_id="oauth-recovered"), ""
    )
    second = refresher.refresh_expired(route_names=("codex_oauth",), force=True)

    assert second["codex_oauth"].healthy is True
    assert store.active_runtime_route_pause("codex_oauth", now=NOW) is None
    assert registry["codex_api"].healthy is True


def test_refresher_isolates_unexpected_probe_exception_and_continues_routes(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()

    class Probe:
        def run(self, *, route_name):
            if route_name == "codex_oauth":
                raise RuntimeError("secret path and credential detail")
            return AgentRuntimeProbe(
                config=config,
                executor=lambda *_args, **_kwargs: ProcessRunResult(
                    0, _successful_probe_stream(), ""
                ),
                now=lambda: NOW,
                temporary_root=tmp_path,
            ).run(route_name=route_name)

    snapshots = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=Probe(),
        now=lambda: NOW,
    ).refresh_expired(force=True)

    assert snapshots["codex_oauth"].healthy is False
    assert snapshots["codex_oauth"].failure is not None
    assert snapshots["codex_oauth"].failure.code == "runtime_probe_failed"
    assert "secret" not in snapshots["codex_oauth"].model_dump_json()
    assert snapshots["codex_api"].healthy is True


def test_refresher_reprobes_unclassified_route_on_health_cadence(monkeypatch, tmp_path):
    config = _config(monkeypatch, routes="codex_oauth")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()
    refresher = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=AgentRuntimeProbe(
            config=config,
            executor=lambda *_args, **_kwargs: ProcessRunResult(
                1, "", "unrecognized runtime failure"
            ),
            now=lambda: NOW,
            temporary_root=tmp_path,
        ),
        now=lambda: NOW,
    )

    snapshots = refresher.refresh_expired(force=True)

    assert snapshots["codex_oauth"].failure is not None
    assert (
        snapshots["codex_oauth"].failure.failure_class
        is RuntimeFailureClass.UNCLASSIFIED
    )
    with store._connect() as db:
        row = db.execute(
            "select retry_at from runtime_route_pauses where route_name=?",
            ("codex_oauth",),
        ).fetchone()
    assert row is not None
    assert datetime.fromisoformat(row["retry_at"]).replace(tzinfo=UTC) == (
        NOW + timedelta(minutes=5)
    )


def test_successful_no_tools_probe_does_not_claim_unverified_business_capabilities(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_oauth")

    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0, _successful_probe_stream(), ""
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.capabilities == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "consumer_read_only_enforcement",
        }
    )
    assert "reviewed_read_tools" not in snapshot.capabilities
    assert "memory_connector_read" not in snapshot.capabilities
    assert "reviewed_write_tools" not in snapshot.capabilities


def test_refresher_skips_current_snapshots(monkeypatch, tmp_path):
    config = _config(monkeypatch, routes="codex_oauth")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()
    calls = []
    probe = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: (
            calls.append("probe") or ProcessRunResult(0, _successful_probe_stream(), "")
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    )
    refresher = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=probe,
        now=lambda: NOW,
    )

    refresher.refresh_expired()
    refresher.refresh_expired()

    assert calls == ["probe"]
