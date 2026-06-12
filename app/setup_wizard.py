import re
from pathlib import Path

from app.setup_wizard_models import (
    SetupAction,
    SetupStatus,
    SetupStepDefinition,
    SetupStepStatus,
    SetupWizardStatus,
)
from app.store import AutoReplyStore


BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")
TOKEN_RE = re.compile(
    r"(?i)([\"']?(?:token|api[_-]?key|apikey|secret)[\"']?\s*[:=]\s*)"
    r"(?:[\"'][^\"'\s<>]+[\"']|[^\s<>]+)"
)
SESSION_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4,}(?:-[0-9a-f]{4,})+\b")
SESSION_KEY_RE = re.compile(r"(?i)session[_-]?id=\S+")
LOCAL_PATH_RE = re.compile(r"(?:/Users|/private/tmp|/tmp)/[^\s'\"<>]+")
SETUP_STATUS_VALUES = set(SetupStatus.__args__)


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


def redact_setup_output(text: str) -> str:
    redacted = BEARER_RE.sub("Bearer [REDACTED_BEARER]", text)
    redacted = TOKEN_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED_TOKEN]",
        redacted,
    )
    redacted = SESSION_KEY_RE.sub("[REDACTED_SESSION]", redacted)
    redacted = SESSION_RE.sub("[REDACTED_SESSION]", redacted)
    redacted = LOCAL_PATH_RE.sub("[REDACTED_PATH]", redacted)
    return redacted


def _status(
    step_id: str,
    *,
    title: str,
    status: str,
    summary: str,
    evidence: dict[str, str | int | bool] | None = None,
) -> SetupStepStatus:
    return SetupStepStatus(
        step_id=step_id,
        title=title,
        status=status,
        summary=summary,
        evidence=evidence or {},
    )


def _env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def check_service_config(*, repo_root: Path) -> SetupStepStatus:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary=".env is missing.",
            evidence={"env_exists": False},
        )
    values = _env_values(env_path)
    workspace_value = values.get("CEO_WORKSPACE", "")
    db_value = values.get("CEO_WORKER_DB", "")
    corpus_value = values.get("CEO_CORPUS_DIR", "")
    workspace = _resolve_repo_path(repo_root, workspace_value)
    db_path = _resolve_repo_path(repo_root, db_value)
    corpus_dir = _resolve_repo_path(repo_root, corpus_value)
    dry_run_enabled = (
        values.get("CEO_NOT_SEND_MESSAGE") == "1"
        or values.get("CEO_DRY_RUN") == "1"
    )
    missing = [
        label
        for label, value, path in (
            ("CEO_WORKSPACE", workspace_value, workspace),
            ("CEO_WORKER_DB parent", db_value, db_path.parent),
            ("CEO_CORPUS_DIR", corpus_value, corpus_dir),
        )
        if not value or not path.exists()
    ]
    if missing:
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary="Missing runtime paths: " + ", ".join(missing),
            evidence={"env_exists": True, "dry_run_enabled": dry_run_enabled},
        )
    if not dry_run_enabled:
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary="Dry-run is not enabled.",
            evidence={"env_exists": True, "dry_run_enabled": False},
        )
    return _status(
        "service_config",
        title="Service Config",
        status="done",
        summary="Service config and runtime directories are ready.",
        evidence={"env_exists": True, "dry_run_enabled": True},
    )


def check_data_corpus(*, repo_root: Path) -> SetupStepStatus:
    style_corpus = repo_root / "corpus" / "style_corpus.csv"
    if not style_corpus.exists():
        return _status(
            "data_corpus",
            title="Data Corpus",
            status="needs_action",
            summary="corpus/style_corpus.csv is missing.",
            evidence={"style_corpus_exists": False},
        )
    return _status(
        "data_corpus",
        title="Data Corpus",
        status="done",
        summary="Style corpus exists.",
        evidence={"style_corpus_exists": True},
    )


def check_work_profile(*, repo_root: Path) -> SetupStepStatus:
    profile = repo_root / "profiles" / "work_profile.md"
    evidence = repo_root / "data" / "profile-evidence" / "evidence_index.jsonl"
    style_corpus = repo_root / "corpus" / "style_corpus.csv"
    if not profile.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="profiles/work_profile.md is missing.",
            evidence={"profile_exists": False},
        )
    if not evidence.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="data/profile-evidence/evidence_index.jsonl is missing.",
        )
    if not style_corpus.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="corpus/style_corpus.csv is missing.",
        )
    profile_text = profile.read_text(encoding="utf-8")
    if (
        "/Users/" in profile_text
        or "Bearer " in profile_text
        or "session_id=" in profile_text
    ):
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="failed",
            summary="profiles/work_profile.md contains sensitive local evidence.",
        )
    return _status(
        "work_profile",
        title="Work Profile Distillation",
        status="done",
        summary="Work profile artifacts are ready.",
    )


def build_wizard_status(store: AutoReplyStore) -> SetupWizardStatus:
    persisted = {row["step_id"]: row for row in store.list_setup_wizard_steps()}
    complete = {
        step_id
        for step_id, row in persisted.items()
        if row["status"] == "done"
    }
    statuses: list[SetupStepStatus] = []

    for definition in SETUP_WIZARD_STEPS:
        row = persisted.get(definition.id)
        missing_dependency = next(
            (
                dependency
                for dependency in definition.depends_on
                if dependency not in complete
            ),
            "",
        )
        if missing_dependency:
            dependency_title = get_step_definition(missing_dependency).title
            statuses.append(
                SetupStepStatus(
                    step_id=definition.id,
                    title=definition.title,
                    status="blocked",
                    summary=f"Blocked until {dependency_title} is complete.",
                    updated_at=row["updated_at"] if row else "",
                )
            )
            continue

        persisted_status = row["status"] if row else "not_started"
        if persisted_status not in SETUP_STATUS_VALUES:
            persisted_status = "failed"
            summary = f"Invalid persisted status: {row['status']}"
        else:
            summary = row["summary"] if row else ""

        statuses.append(
            SetupStepStatus(
                step_id=definition.id,
                title=definition.title,
                status=persisted_status,
                summary=summary,
                available_actions=definition.actions,
                updated_at=row["updated_at"] if row else "",
            )
        )

    return SetupWizardStatus(steps=statuses)
