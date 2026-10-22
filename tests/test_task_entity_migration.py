from app.task_entity_migration import (
    owner_indexes, restore_feedback_workbench_ref,
    translate_action_feedback_parent, translate_feedback_workbench_refs,
)


def test_workbench_feedback_translation_rewrites_only_exact_resolved_text_refs():
    task_indexes = owner_indexes([
        {"source_kind": "workbench_tasks", "physical_id": 8,
         "public_key": "wb-task-a", "target_id": 800},
    ])
    turn_indexes = owner_indexes([
        {"source_kind": "workbench_turns", "physical_id": 9,
         "public_key": "wb-turn-a", "target_id": 900},
    ])
    markers = []
    assert translate_feedback_workbench_refs(
        task_ref="wb-task-a", turn_ref="old-unresolved-turn",
        task_indexes=task_indexes, turn_indexes=turn_indexes, unresolved=markers,
    ) == ("800", "old-unresolved-turn")
    assert markers == [{"field": "workbench_turn_id",
                        "value": "old-unresolved-turn", "type": "text"}]
    assert restore_feedback_workbench_ref(
        field="workbench_task_id", current_value="800", indexes=task_indexes,
    ) == "wb-task-a"
    assert restore_feedback_workbench_ref(
        field="workbench_turn_id", current_value="old-unresolved-turn", indexes=turn_indexes,
    ) == "old-unresolved-turn"


def test_action_public_feedback_ids_resolve_to_actual_owner_ids_for_frozen_zero_family():
    indexes = owner_indexes([
        {"source_kind": "feedback_iteration_decisions", "physical_id": 410,
         "public_key": 12, "target_id": 410},
        {"source_kind": "feedback_processing_rounds", "physical_id": 411,
         "public_key": 13, "target_id": 411},
    ])
    assert translate_action_feedback_parent(
        source_kind="feedback_iteration_decisions", source_public_id=12,
        indexes=indexes,
    ) == 410
    assert translate_action_feedback_parent(
        source_kind="feedback_processing_rounds", source_public_id=13,
        indexes=indexes,
    ) == 411


def test_public_id_lookup_is_kind_and_storage_type_qualified():
    indexes = owner_indexes([
        {"source_kind": "feedback_iteration_decisions", "physical_id": 1,
         "public_key": "12", "target_id": 51},
    ])
    assert translate_action_feedback_parent(
        source_kind="feedback_iteration_decisions", source_public_id="12",
        indexes=indexes,
    ) == 51
    try:
        translate_action_feedback_parent(
            source_kind="feedback_iteration_decisions", source_public_id=12,
            indexes=indexes,
        )
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("integer public ID was conflated with TEXT public ID")
