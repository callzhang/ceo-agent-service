import pytest
from pydantic import ValidationError

from app.setup_wizard import SETUP_WIZARD_STEPS, get_step_definition
from app.setup_wizard_models import (
    SetupAction,
    SetupStepStatus,
    SetupWizardEvent,
    SetupWizardStatus,
)


def test_setup_wizard_steps_are_ordered_and_gated():
    assert [step.id for step in SETUP_WIZARD_STEPS] == [
        "preflight",
        "cli_components",
        "mcp",
        "service_config",
        "data_corpus",
        "work_profile",
        "dry_run",
        "launchd",
        "live_send",
    ]
    assert get_step_definition("mcp").depends_on == ("cli_components",)
    assert get_step_definition("launchd").depends_on == ("dry_run",)
    assert get_step_definition("live_send").depends_on == ("dry_run",)


def test_setup_wizard_action_metadata_is_gated():
    actions = {
        step.id: {
            action.id: action
            for action in step.actions
        }
        for step in SETUP_WIZARD_STEPS
    }

    assert [(action.id, action.label, action.kind) for action in actions["mcp"].values()] == [
        ("check_mcp", "Check", "check"),
        ("setup_mcp", "Fix automatically", "run"),
    ]
    assert actions["launchd"]["install_launchd"].external_side_effect is True
    assert actions["live_send"]["verify_live_send"].external_side_effect is True
    assert actions["live_send"]["confirm_live_send"].kind == "confirm"
    assert actions["live_send"]["confirm_live_send"].label == "Confirm after page inspection"


def test_get_step_definition_rejects_unknown_step():
    with pytest.raises(KeyError) as error:
        get_step_definition("unknown")

    assert error.value.args == ("unknown",)


def test_setup_wizard_static_definitions_are_immutable():
    preflight = get_step_definition("preflight")

    with pytest.raises(AttributeError):
        preflight.actions.append(
            SetupAction(
                id="mutate",
                label="Mutate",
                step_id="preflight",
                kind="run",
            )
        )
    with pytest.raises(ValidationError):
        preflight.actions[0].label = "Mutated"


def test_setup_step_status_defaults_to_not_started():
    status = SetupStepStatus(step_id="mcp", title="MCP")

    assert status.status == "not_started"
    assert status.summary == ""
    assert status.available_actions == []
    assert status.manual_confirmation_allowed is False


def test_setup_wizard_status_serializes_steps():
    status = SetupWizardStatus(
        steps=[
            SetupStepStatus(
                step_id="preflight",
                title="Preflight",
                status="done",
                summary="Python is available",
            )
        ]
    )

    payload = status.model_dump()

    assert payload["steps"][0]["step_id"] == "preflight"
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["summary"] == "Python is available"


def test_setup_wizard_event_defaults_and_serialization():
    event = SetupWizardEvent(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        evidence={"configured": True},
    )

    payload = event.model_dump()

    assert payload["id"] == 0
    assert payload["step_id"] == "mcp"
    assert payload["action_id"] == "setup_mcp"
    assert payload["status"] == "done"
    assert payload["summary"] == ""
    assert payload["evidence"] == {"configured": True}
    assert payload["stdout_excerpt"] == ""
    assert payload["stderr_excerpt"] == ""
