from typing import get_args

import pytest
from pydantic import ValidationError

from app.setup_wizard import SETUP_WIZARD_STEPS, get_step_definition
from app.setup_wizard_models import (
    SetupAction,
    SetupActionStatus,
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
    assert {
        step.id: [
            (
                action.id,
                action.label,
                action.step_id,
                action.kind,
                action.destructive,
                action.external_side_effect,
            )
            for action in step.actions
        ]
        for step in SETUP_WIZARD_STEPS
    } == {
        "preflight": [
            ("check_preflight", "Check", "preflight", "check", False, False),
        ],
        "cli_components": [
            ("check_cli_components", "Check", "cli_components", "check", False, False),
        ],
        "mcp": [
            ("check_mcp", "Check", "mcp", "check", False, False),
            ("setup_mcp", "Fix automatically", "mcp", "run", False, False),
        ],
        "service_config": [
            ("check_service_config", "Check", "service_config", "check", False, False),
            (
                "setup_service_config",
                "Fix automatically",
                "service_config",
                "run",
                False,
                False,
            ),
        ],
        "data_corpus": [
            ("check_data_corpus", "Check", "data_corpus", "check", False, False),
            ("build_data_corpus", "Run", "data_corpus", "run", False, False),
        ],
        "work_profile": [
            ("check_work_profile", "Check", "work_profile", "check", False, False),
            ("build_work_profile", "Run", "work_profile", "run", False, False),
        ],
        "dry_run": [
            ("check_dry_run", "Check", "dry_run", "check", False, False),
            ("run_dry_run", "Run", "dry_run", "run", False, False),
        ],
        "launchd": [
            ("check_launchd", "Check", "launchd", "check", False, False),
            ("install_launchd", "Run", "launchd", "run", False, True),
        ],
        "live_send": [
            ("check_live_send", "Check", "live_send", "check", False, False),
            ("verify_live_send", "Run", "live_send", "run", False, True),
            (
                "confirm_live_send",
                "Confirm after page inspection",
                "live_send",
                "confirm",
                False,
                False,
            ),
        ],
    }


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


def test_setup_action_status_values_are_locked():
    assert get_args(SetupActionStatus) == ("not_started", "running", "done", "failed")


@pytest.mark.parametrize("status", ["not_started", "running", "done", "failed"])
def test_setup_wizard_event_accepts_action_statuses(status: str):
    event = SetupWizardEvent(step_id="mcp", action_id="setup_mcp", status=status)

    assert event.status == status


def test_setup_wizard_event_rejects_unknown_action_status():
    with pytest.raises(ValidationError):
        SetupWizardEvent(step_id="mcp", action_id="setup_mcp", status="skipped")
