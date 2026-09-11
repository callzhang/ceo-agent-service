from __future__ import annotations

import json

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


def test_the_consumer_records_an_unreviewed_effect_it_actually_produced(tmp_path) -> None:
    """The wiring, exercised with the event shape run 14017 produced."""
    from types import SimpleNamespace

    from app.consumer_agent import CONSUMER_UNREVIEWED_EFFECT, ConsumerAgentRunner
    from app.store import AutoReplyStore

    store = AutoReplyStore(tmp_path / "effects.sqlite3")
    runner = ConsumerAgentRunner.__new__(ConsumerAgentRunner)
    runner.store = store
    task = SimpleNamespace(id=383537, conversation_id="cid-1", trigger_message_id="msg-1")

    receipt = json.dumps({"data": {"result": {"openTaskId": "nexgdfcC="}}})
    store.get_agent_run = lambda _run_id: SimpleNamespace(  # type: ignore[method-assign]
        tool_events=[_command(receipt)]
    )
    runner._report_unreviewed_provider_effects(task, SimpleNamespace(run_id=14017))

    with store._connect() as db:
        rows = [dict(r) for r in db.execute("select kind, detail from errors")]
    assert [row["kind"] for row in rows] == [CONSUMER_UNREVIEWED_EFFECT]
    assert "nexgdfcC=" in rows[0]["detail"]
    assert "must not be retried blindly" in rows[0]["detail"]

    # A clean proposal turn records nothing.
    store.get_agent_run = lambda _run_id: SimpleNamespace(tool_events=[])  # type: ignore[method-assign]
    runner._report_unreviewed_provider_effects(task, SimpleNamespace(run_id=14018))
    with store._connect() as db:
        assert db.execute("select count(*) from errors").fetchone()[0] == 1
