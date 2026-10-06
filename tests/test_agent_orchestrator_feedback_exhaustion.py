"""Content feedback is bounded independently of technical execution attempts."""

from test_agent_orchestrator import (
    ScriptedAudit,
    ScriptedConsumer,
    candidate,
    orchestrator,
    task_and_context,
)


def test_fourth_audit_return_is_terminal_without_fifth_consumer(tmp_path):
    store, task, context = task_and_context(tmp_path)
    consumer = ScriptedConsumer(
        store, candidate("first"), candidate("second"),
        candidate("third"), candidate("fourth"),
    )
    audit = ScriptedAudit(store, "return", "return", "return", "return")
    driver, handler = orchestrator(store, consumer, audit)
    result = driver.process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.code == "audit_revision_exhausted"
    assert len(consumer.calls) == len(audit.calls) == 4
    assert handler.calls == []
