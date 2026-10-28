import pytest











def test_historical_empty_grammar_requires_complete_counts_and_allows_email_scan_inputs():
    from app.scheduled_occurrence_storage import historical_zero_result

    assert historical_zero_result(
        "email-message-check-once",
        "email-message-check-once accounts=1 discovered=0 failures=0",
    )
    assert not historical_zero_result(
        "email-message-check-once",
        "email-message-check-once accounts=0 discovered=0",
    )
    assert not historical_zero_result("recover-tasks", "recover-tasks closed=0 queued=0")
    assert not historical_zero_result("scan-meetings", "scan-meetings queued=0")


def test_command_registry_rejects_legacy_string_result_without_rerunning():
    from app.agent_cron.commands import SERVICE_COMMAND_OPTIONS, ServiceCommandRegistry
    from app.agent_cron.commands import ServiceCommandResult

    implementations = {
        option.name: (lambda: ServiceCommandResult("unused", None, False))
        for option in SERVICE_COMMAND_OPTIONS
    }
    calls = 0

    def legacy_string():
        nonlocal calls
        calls += 1
        return "produce-once queued=0"

    implementations["produce-once"] = legacy_string
    registry = ServiceCommandRegistry(implementations)
    with pytest.raises(TypeError, match="must return a typed result"):
        registry.run_with_result("produce-once")
    assert calls == 1
