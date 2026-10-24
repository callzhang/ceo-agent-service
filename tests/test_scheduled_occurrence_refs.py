import pytest

from app.run_entity_schema import _replace_json_integer_leaf


def test_occurrence_leaf_translation_preserves_unrelated_source_bytes_and_inverse():
    source = '{  "other" : "7003", "scheduled_consumer" : { "scheduled_task_run_id" : 7003 } }'

    native, changed = _replace_json_integer_leaf(
        source,
        ("scheduled_consumer", "scheduled_task_run_id"),
        {7003: 9503}.__getitem__,
    )
    assert changed == 1
    assert native == source.replace(" : 7003", " : 9503")

    restored, changed = _replace_json_integer_leaf(
        native,
        ("scheduled_consumer", "scheduled_task_run_id"),
        {9503: 7003}.__getitem__,
    )
    assert changed == 1
    assert restored == source


def test_occurrence_leaf_translation_rejects_duplicate_path_keys():
    source = '{"scheduled_consumer":{"scheduled_task_run_id":7003,"scheduled_task_run_id":8003}}'
    with pytest.raises(ValueError, match="duplicate key"):
        _replace_json_integer_leaf(
            source,
            ("scheduled_consumer", "scheduled_task_run_id"),
            {7003: 9503}.__getitem__,
        )


def test_occurrence_leaf_translation_rejects_non_integer_or_unmapped_refs():
    path = ("scheduled_consumer", "scheduled_task_run_id")
    with pytest.raises(ValueError, match="positive integer"):
        _replace_json_integer_leaf(
            '{"scheduled_consumer":{"scheduled_task_run_id":true}}', path,
            {1: 2}.__getitem__,
        )
    with pytest.raises(KeyError):
        _replace_json_integer_leaf(
            '{"scheduled_consumer":{"scheduled_task_run_id":7003}}', path,
            {}.__getitem__,
        )


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
