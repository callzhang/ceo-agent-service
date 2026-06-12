from app.setup_wizard import SETUP_WIZARD_STEPS, get_step_definition
from app.setup_wizard_models import SetupStepStatus, SetupWizardStatus


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
    assert get_step_definition("mcp").depends_on == ["cli_components"]
    assert get_step_definition("launchd").depends_on == ["dry_run"]
    assert get_step_definition("live_send").depends_on == ["dry_run"]


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
