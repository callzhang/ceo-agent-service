import json

import pytest

from app.skill_features import FeatureRegistry


def write_registry(tmp_path, payload):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def feature(feature_id="meeting_summary", *, skills=None, default_enabled=True):
    return {
        "feature_id": feature_id,
        "name": "Meeting Summary",
        "description": "会议摘要",
        "skills": ["ceo-meeting-work"] if skills is None else skills,
        "default_enabled": default_enabled,
    }


def test_registry_supports_many_skills_per_feature_and_shared_skills(tmp_path):
    registry = write_registry(
        tmp_path,
        {
            "features": [
                feature(
                    "meeting_summary",
                    skills=["ceo-meeting-work", "ceo-document-review"],
                ),
                feature(
                    "document_review",
                    skills=["ceo-document-review"],
                ),
            ]
        },
    )
    catalog = FeatureRegistry(registry_path=registry, state_path=tmp_path / "state.json")

    assert catalog.skills_for("meeting_summary") == (
        "ceo-meeting-work",
        "ceo-document-review",
    )
    assert catalog.features_for_skill("ceo-document-review") == (
        "document_review",
        "meeting_summary",
    )


def test_missing_state_uses_default_and_toggle_survives_reload(tmp_path):
    registry = write_registry(tmp_path, {"features": [feature()]})
    state = tmp_path / "state.json"
    catalog = FeatureRegistry(registry_path=registry, state_path=state)

    assert catalog.is_enabled("meeting_summary") is True
    updated = catalog.set_enabled("meeting_summary", False)

    assert updated.feature_id == "meeting_summary"
    assert updated.enabled is False
    assert FeatureRegistry(registry_path=registry, state_path=state).is_enabled(
        "meeting_summary"
    ) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"features": [feature(), feature()]},
        {"features": [feature(skills=[])]},
        {"features": [feature(default_enabled="yes")]},
    ],
)
def test_registry_rejects_duplicate_empty_or_non_boolean_definitions(tmp_path, payload):
    with pytest.raises(ValueError):
        FeatureRegistry(
            registry_path=write_registry(tmp_path, payload),
            state_path=tmp_path / "state.json",
        )


def test_unknown_feature_and_invalid_toggle_are_rejected(tmp_path):
    registry = write_registry(tmp_path, {"features": [feature()]})
    catalog = FeatureRegistry(registry_path=registry, state_path=tmp_path / "state.json")

    with pytest.raises(KeyError):
        catalog.is_enabled("unknown")
    with pytest.raises(ValueError):
        catalog.set_enabled("meeting_summary", "false")


@pytest.mark.parametrize(
    "definition",
    [
        feature(feature_id=["not-a-string"]),
        feature(skills=[{"name": "not-a-string"}]),
    ],
)
def test_registry_rejects_unhashable_or_non_string_names_with_value_error(
    tmp_path, definition
):
    with pytest.raises(ValueError):
        FeatureRegistry(
            registry_path=write_registry(tmp_path, {"features": [definition]}),
            state_path=tmp_path / "state.json",
        )


def test_feature_status_reports_missing_skill_dependency(tmp_path):
    registry = write_registry(tmp_path, {"features": [feature()]})
    catalog = FeatureRegistry(registry_path=registry, state_path=tmp_path / "state.json")

    assert catalog.feature_status("meeting_summary", {"ceo-meeting-work"}) == "ready"
    assert catalog.feature_status("meeting_summary", set()) == "incomplete"


def test_failed_atomic_replacement_preserves_previous_state(tmp_path, monkeypatch):
    registry = write_registry(tmp_path, {"features": [feature()]})
    state = tmp_path / "state.json"
    catalog = FeatureRegistry(registry_path=registry, state_path=state)
    catalog.set_enabled("meeting_summary", True)
    original = state.read_text(encoding="utf-8")

    def fail_replace(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("app.skill_features.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        catalog.set_enabled("meeting_summary", False)

    assert state.read_text(encoding="utf-8") == original
    assert catalog.is_enabled("meeting_summary") is True


def test_toggles_from_two_instances_merge_without_losing_updates(tmp_path):
    registry = write_registry(
        tmp_path,
        {"features": [feature(), feature("document_review")]},
    )
    state = tmp_path / "state.json"
    first = FeatureRegistry(registry_path=registry, state_path=state)
    second = FeatureRegistry(registry_path=registry, state_path=state)

    first.set_enabled("meeting_summary", False)
    second.set_enabled("document_review", False)

    final = FeatureRegistry(registry_path=registry, state_path=state)
    assert final.is_enabled("meeting_summary") is False
    assert final.is_enabled("document_review") is False


@pytest.mark.parametrize(
    "operation",
    [
        lambda catalog: catalog.is_enabled(["meeting_summary"]),
        lambda catalog: catalog.skills_for({"meeting_summary"}),
        lambda catalog: catalog.features_for_skill({"ceo-meeting-work"}),
        lambda catalog: catalog.feature_status(["meeting_summary"], set()),
    ],
)
def test_public_name_arguments_reject_non_string_values(tmp_path, operation):
    registry = write_registry(tmp_path, {"features": [feature()]})
    catalog = FeatureRegistry(registry_path=registry, state_path=tmp_path / "state.json")

    with pytest.raises(ValueError):
        operation(catalog)
