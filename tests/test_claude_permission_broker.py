from app.agent_effects import McpToolEffectRegistry
from app.agent_result import EffectKind
from app.claude_permission_broker import ClaudePermissionBroker
from app.native_cli_metadata import NativeCliMetadataClassifier


def _broker():
    return ClaudePermissionBroker(
        allowed_mcp_tools=frozenset(
            {"mcp__memory_connector__memory_recall"}
        ),
        allow_native_cli=True,
        effect_registry=McpToolEffectRegistry(
            {("memory_connector", "memory_recall"): EffectKind.READ_ONLY}
        ),
        native_cli_classifier=NativeCliMetadataClassifier(
            reviewed_effects={
                ("dws", "chat message send"): EffectKind.EFFECTFUL,
            }
        ),
    )


def test_broker_denies_unknown_write_before_dispatch():
    calls = []

    decision = _broker().dispatch_if_authorized(
        "Write",
        {"path": "/tmp/forbidden", "content": "no"},
        lambda *_args: calls.append("executed"),
    )

    assert decision == {
        "behavior": "deny",
        "message": "claude_tool_unreviewed",
    }
    assert calls == []


def test_broker_allows_only_exact_reviewed_mcp_name_and_arguments():
    broker = _broker()

    allowed = broker.authorize(
        "mcp__memory_connector__memory_recall", {"query": "synthetic"}
    )
    typo = broker.authorize(
        "mcp__memory_connector__memory_write", {"data": "forbidden"}
    )

    assert allowed["behavior"] == "allow"
    assert allowed["updatedInput"] == {"query": "synthetic"}
    assert typo["behavior"] == "deny"


def test_broker_classifies_native_cli_before_dispatch():
    calls = []
    broker = _broker()

    decision = broker.dispatch_if_authorized(
        "Bash",
        {"command": "dws chat message send --group cid --text ok --yes"},
        lambda tool, arguments: calls.append((tool, arguments)),
    )

    assert decision["behavior"] == "allow"
    assert calls == [
        (
            "Bash",
            {"command": "dws chat message send --group cid --text ok --yes"},
        )
    ]


def test_broker_denies_native_shell_environment_expansion_before_dispatch():
    calls = []

    for command in (
        "dws chat message send --group cid --text $CONNECTOR_API_KEY --yes",
        "dws chat message send --group cid --text ${CONNECTOR_API_KEY} --yes",
    ):
        decision = _broker().dispatch_if_authorized(
            "Bash",
            {"command": command},
            lambda *_args: calls.append("executed"),
        )
        assert decision["behavior"] == "deny"
    assert calls == []
