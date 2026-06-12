from app.setup_wizard_models import SetupAction, SetupStepDefinition


SETUP_WIZARD_STEPS: tuple[SetupStepDefinition, ...] = (
    SetupStepDefinition(
        id="preflight",
        title="Preflight",
        phase="Phase 1",
        description="Verify local checkout, Python, Node, and package environment.",
        actions=[
            SetupAction(
                id="check_preflight",
                label="Check",
                step_id="preflight",
                kind="check",
            ),
        ],
    ),
    SetupStepDefinition(
        id="cli_components",
        title="CLI Components",
        phase="Phase 2",
        description="Verify dws, Codex CLI, and Nvwa skill availability.",
        depends_on=["preflight"],
        actions=[
            SetupAction(
                id="check_cli_components",
                label="Check",
                step_id="cli_components",
                kind="check",
            ),
        ],
    ),
    SetupStepDefinition(
        id="mcp",
        title="Memory Connector MCP",
        phase="Phase 2",
        description="Verify or configure the memory_connector MCP entry.",
        depends_on=["cli_components"],
        actions=[
            SetupAction(id="check_mcp", label="Check", step_id="mcp", kind="check"),
            SetupAction(id="setup_mcp", label="Fix automatically", step_id="mcp", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="service_config",
        title="Service Config",
        phase="Phase 3",
        description="Create and validate .env, runtime paths, and dry-run defaults.",
        depends_on=["mcp"],
        actions=[
            SetupAction(
                id="check_service_config",
                label="Check",
                step_id="service_config",
                kind="check",
            ),
            SetupAction(
                id="setup_service_config",
                label="Fix automatically",
                step_id="service_config",
                kind="run",
            ),
        ],
    ),
    SetupStepDefinition(
        id="data_corpus",
        title="Data Corpus",
        phase="Phase 4",
        description="Build local style corpus from workspace and DingTalk samples.",
        depends_on=["service_config"],
        actions=[
            SetupAction(
                id="check_data_corpus",
                label="Check",
                step_id="data_corpus",
                kind="check",
            ),
            SetupAction(
                id="build_data_corpus",
                label="Run",
                step_id="data_corpus",
                kind="run",
            ),
        ],
    ),
    SetupStepDefinition(
        id="work_profile",
        title="Work Profile Distillation",
        phase="Phase 5",
        description="Generate and verify profiles/work_profile.md and evidence index.",
        depends_on=["data_corpus"],
        actions=[
            SetupAction(
                id="check_work_profile",
                label="Check",
                step_id="work_profile",
                kind="check",
            ),
            SetupAction(
                id="build_work_profile",
                label="Run",
                step_id="work_profile",
                kind="run",
            ),
        ],
    ),
    SetupStepDefinition(
        id="dry_run",
        title="Dry-Run Validation",
        phase="Phase 7",
        description="Run dry-run processing and verify audit state has no unresolved backlog.",
        depends_on=["work_profile"],
        actions=[
            SetupAction(
                id="check_dry_run",
                label="Check",
                step_id="dry_run",
                kind="check",
            ),
            SetupAction(id="run_dry_run", label="Run", step_id="dry_run", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="launchd",
        title="Launchd Service",
        phase="Phase 8",
        description="Install or restart launchd only after dry-run is verified.",
        depends_on=["dry_run"],
        actions=[
            SetupAction(
                id="check_launchd",
                label="Check",
                step_id="launchd",
                kind="check",
            ),
            SetupAction(
                id="install_launchd",
                label="Run",
                step_id="launchd",
                kind="run",
                external_side_effect=True,
            ),
        ],
    ),
    SetupStepDefinition(
        id="live_send",
        title="Live Send Verification",
        phase="Phase 9",
        description=(
            "Verify a reviewed DingTalk send from structured state, Computer Use, "
            "or manual fallback."
        ),
        depends_on=["dry_run"],
        actions=[
            SetupAction(
                id="check_live_send",
                label="Check",
                step_id="live_send",
                kind="check",
            ),
            SetupAction(
                id="verify_live_send",
                label="Run",
                step_id="live_send",
                kind="run",
                external_side_effect=True,
            ),
            SetupAction(
                id="confirm_live_send",
                label="Confirm after page inspection",
                step_id="live_send",
                kind="confirm",
            ),
        ],
    ),
)


def get_step_definition(step_id: str) -> SetupStepDefinition:
    for step in SETUP_WIZARD_STEPS:
        if step.id == step_id:
            return step
    raise KeyError(step_id)
