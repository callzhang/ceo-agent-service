from __future__ import annotations

import asyncio
import json
import sqlite3

from app.agent_effect_guard import provider_receipts


def _command(output, *, exit_code: int = 0, kind: str = "command_execution"):
    return {
        "type": "item.completed",
        "item": {"type": kind, "exit_code": exit_code, "output": output},
    }


def test_a_send_that_reached_the_provider_is_recognised_by_its_receipt() -> None:
    """The shape run 14017 actually produced when it bypassed the effect gate."""
    output = json.dumps(
        {
            "ok": True,
            "outcome": "pending",
            "data": {"result": {"openTaskId": "nexgdfcCmkvOaqjH6ph6E="}, "success": True},
        },
        ensure_ascii=False,
    )
    assert provider_receipts([_command(output)]) == ("nexgdfcCmkvOaqjH6ph6E=",)


def test_a_read_only_turn_reports_nothing() -> None:
    events = [
        _command(json.dumps({"ok": True, "data": {"minutes": [{"title": "周会"}]}})),
        {"type": "turn.completed", "item": {"type": "agent_message"}},
    ]
    assert provider_receipts(events) == ()


def test_paginated_chat_history_ids_are_not_send_receipts() -> None:
    history = {"result": {"conversationMessagesList": [{"messages": [
        {"content": "existing message", "messageId": "msg-old", "openMessageId": "msg-old"}
    ]}]}}
    assert provider_receipts([_command(json.dumps(history))]) == ()


def test_transformed_chat_history_ids_are_not_send_receipts() -> None:
    history = {
        "success": True,
        "result_preview": [{
            "key": "conversationMessagesList",
            "value": {"sample": {"messages": [
                {"messageId": "msg-existing", "openMessageId": "msg-existing"},
            ]}},
        }],
    }
    assert provider_receipts([_command(json.dumps(history))]) == ()


def test_receipt_names_in_business_content_are_not_send_receipts() -> None:
    response = {
        "success": True,
        "result": {
            "content": json.dumps({"result": {"openTaskId": "quoted-id"}}),
        },
    }
    assert provider_receipts([_command(json.dumps(response))]) == ()


def test_real_send_receipt_survives_adjacent_read_projections() -> None:
    output = json.dumps({"preview": {"openMessageId": "old-id"}})
    output += "\n" + json.dumps({"data": {"result": {"openTaskId": "new-id"}}})
    assert provider_receipts([_command(output)]) == ("new-id",)


def test_rejected_provider_result_is_not_a_send_receipt() -> None:
    response = {"data": {"result": {"success": False, "openTaskId": "rejected-id"}}}
    assert provider_receipts([_command(json.dumps(response))]) == ()


def test_receipt_field_order_is_preserved_within_a_response() -> None:
    response = {"result": {"openTaskId": "task-id", "openMessageId": "message-id"}}
    assert provider_receipts([_command(json.dumps(response))]) == ("task-id", "message-id")


def test_a_failed_command_is_not_treated_as_an_effect() -> None:
    """A non-zero exit has no provider acceptance to record."""
    output = json.dumps({"data": {"result": {"openTaskId": "not-accepted"}}})
    assert provider_receipts([_command(output, exit_code=1)]) == ()


def test_documentation_mentioning_a_receipt_field_is_not_an_effect() -> None:
    """Help text names the field; only a real value counts."""
    helptext = "先拿 openTaskId 再用 chat message query-send-status 确认投递"
    assert provider_receipts([_command(helptext)]) == ()


def test_receipts_are_reported_once_in_first_seen_order() -> None:
    first = json.dumps({"result": {"openTaskId": "a"}})
    echoed = json.dumps({"result": {"openTaskId": "a", "openMessageId": "b"}})
    assert provider_receipts([_command(first), _command(echoed)]) == ("a", "b")


def test_a_non_command_item_is_ignored() -> None:
    output = json.dumps({"result": {"openMessageId": "m"}})
    assert provider_receipts([_command(output, kind="agent_message")]) == ()


def test_malformed_input_is_tolerated() -> None:
    assert provider_receipts(None) == ()
    assert provider_receipts(["", 3, {"item": None}, {"item": {"type": "x"}}]) == ()
    assert provider_receipts([_command("not json at all")]) == ()


def test_consumer_role_keeps_report_documents_without_controlled_write_tools(tmp_path) -> None:
    """The role catalog is the execution boundary; old event diagnosis is not."""
    from app.agent_cli import build_role_server
    from app.consumer_agent import ConsumerAgentRunner

    db_path = tmp_path / "role.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table reply_tasks (id integer primary key, execution_generation text not null)")
        db.execute("insert into reply_tasks values (1, 'catalog-test')")
    consumer = {tool.name for tool in asyncio.run(build_role_server(
        "consumer", task_id=1, db_path=db_path,
    ).list_tools())}
    audit = {tool.name for tool in asyncio.run(build_role_server(
        "audit", task_id=1, db_path=db_path,
    ).list_tools())}

    assert {"daily_report_facts", "weekly_report_materials",
            "validate_weekly_report", "render_weekly_report",
            "consumer_document_write"} <= consumer
    assert "consumer_document_write" not in audit
    assert {"read_dingtalk_oa", "read_dingtalk_document"} <= consumer & audit
    assert {"send_approved_dingtalk_message", "execute_reviewed_write",
            "execute_audited_email_unsubscribe"}.isdisjoint(consumer | audit)
    assert not hasattr(ConsumerAgentRunner, "_report_unreviewed_provider_effects")


def test_consumer_boundary_preserves_report_work_and_proposes_controlled_actions() -> None:
    """The business role can prepare documents while system code owns dispatch."""
    from app.consumer_agent import CONSUMER_ROLE_BOUNDARY as boundary

    flowed = " ".join(boundary.split())
    assert "including report and document work" in flowed
    assert "Controlled structured actions are proposals: do not dispatch them yourself" in flowed
    assert "Audit reads and reviews the complete candidate; system code executes its exact" in flowed
    assert "Supply canonical typed capability/operation, exact target and payload; no shell argv" in flowed


def _mcp_call(result, *, error=None, tool: str = "execute_reviewed_write"):
    """The shape the controlled CLI returns through the MCP tool channel."""
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": "agent_cli",
            "tool": tool,
            "error": error,
            "result": result,
        },
    }


def test_an_effect_routed_through_the_controlled_cli_is_recognised() -> None:
    """The shape run 8374 produced: a real send the guard used to miss entirely.

    The reviewed CLI is the preferred path, and its answers arrive as MCP tool
    results rather than shell output, so reading only `command_execution` made
    the guard blind to exactly the well-behaved case.
    """
    provider = json.dumps(
        {"cli": "dws", "operation": "chat +messages-send",
         "result": {"openTaskId": "G2WsAf7pzHQoDBXm="}, "success": True},
        ensure_ascii=False,
    )
    result = {"content": [{"type": "text", "text": provider}]}
    assert provider_receipts([_mcp_call(result)]) == ("G2WsAf7pzHQoDBXm=",)


def test_approved_message_tool_provider_result_is_a_send_receipt() -> None:
    response = {
        "success": True,
        "delivery_status": "sent",
        "delivery_key": "delivery-1",
        "action_identity": "reply-1",
        "provider_result": {"success": True, "result": {"openTaskId": "approved-send"}},
        "verification": {"state": "sent", "verified": True},
    }
    result = {"content": [{"type": "text", "text": json.dumps(response)}],
              "structuredContent": response}
    assert provider_receipts([
        _mcp_call(result, tool="send_approved_dingtalk_message"),
    ]) == ("approved-send",)


def test_a_failed_tool_call_is_not_treated_as_an_effect() -> None:
    result = {"content": [{"type": "text", "text": json.dumps({"openTaskId": "x"})}]}
    assert provider_receipts([_mcp_call(result, error="refused")]) == ()


def test_a_read_through_the_controlled_cli_reports_nothing() -> None:
    listing = json.dumps({"messages": [{"messageId": "msg-read-1"}]}, ensure_ascii=False)
    result = {"content": [{"type": "text", "text": listing}]}
    assert provider_receipts([_mcp_call(result, tool="execute_reviewed_read")]) == ()
