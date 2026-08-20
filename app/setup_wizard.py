import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timezone
from pathlib import Path

from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.audit_rules import SEED_AUDIT_RULES_TEMPLATE
from app.channel_gate import ChannelGateState, default_channel_gates
from app.developer_prompt import (
    SEED_DEVELOPER_PROMPT_TEMPLATE,
    SEED_USER_PROMPT_TEMPLATE,
)
from app.mcp_doctor import check_mcp_statuses
from app.prompt import DEFAULT_WORK_PROFILE_TEXT
from app.runtime_environment import MINIMUM_PYTHON, central_python
from app.service_codex_config import (
    DEFAULT_SERVICE_MCP_CONFIG_PATH,
    ServiceMcpConfigError,
    load_service_mcp_servers,
    service_mcp_url_is_safe,
)
from app.setup_wizard_models import (
    SetupAction,
    SetupStatus,
    SetupStepDefinition,
    SetupStepStatus,
    SetupWizardEvent,
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
LOCAL_PATH_RE = re.compile(r"(?:/Users|/private/tmp|/private/var|/tmp)/[^\s'\"<>]+")
SETUP_STATUS_VALUES = set(SetupStatus.__args__)


def runtime_route_setup_statuses(
    *,
    env: Mapping[str, str],
    snapshots: Mapping[str, RuntimeCapabilitySnapshot],
    now=lambda: datetime.now(UTC),
) -> tuple[dict[str, object], ...]:
    """Return secret-free setup readiness for every supported runtime route."""

    configured = {
        item.strip()
        for item in env.get("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth").split(",")
        if item.strip()
    }
    checked_now = now()
    statuses: list[dict[str, object]] = []
    for route_name in ("codex_oauth", "codex_api"):
        secret_configured = bool(
            route_name == "codex_api" and env.get("CEO_CODEX_API_KEY", "").strip()
        )
        if route_name not in configured:
            status = "disabled"
        elif route_name == "codex_api" and not secret_configured:
            status = "missing_secret"
        else:
            snapshot = snapshots.get(route_name)
            status = (
                "ready"
                if _runtime_snapshot_is_current(snapshot, checked_now)
                else "probe_failed"
            )
        statuses.append(
            {
                "route_name": route_name,
                "status": status,
                "secret_configured": secret_configured,
            }
        )
    return tuple(statuses)


def _runtime_snapshot_is_current(
    snapshot: RuntimeCapabilitySnapshot | None, now: datetime
) -> bool:
    if snapshot is None or not snapshot.healthy or snapshot.failure is not None:
        return False
    try:
        expires_at = datetime.fromisoformat(snapshot.expires_at)
    except (TypeError, ValueError):
        return False
    if expires_at.tzinfo is None or now.tzinfo is None:
        return False
    return expires_at.astimezone(UTC) > now.astimezone(UTC)


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
        description="Verify and install Codex CLI, Nvwa skill, and notifications.",
        depends_on=["preflight"],
        actions=[
            SetupAction(
                id="check_cli_components",
                label="Check",
                step_id="cli_components",
                kind="check",
            ),
            SetupAction(
                id="setup_cli_components",
                label="Fix automatically",
                step_id="cli_components",
                kind="run",
            ),
        ],
    ),
    SetupStepDefinition(
        id="dingtalk_cli",
        title="DingTalk CLI",
        phase="Phase 2",
        description="Install DWS and open its official configuration flow when needed.",
        depends_on=["preflight"],
        actions=[
            SetupAction(
                id="check_dingtalk_cli",
                label="Check",
                step_id="dingtalk_cli",
                kind="check",
            ),
            SetupAction(
                id="setup_dingtalk_cli",
                label="Install or configure",
                step_id="dingtalk_cli",
                kind="run",
                external_side_effect=True,
            ),
        ],
    ),
    SetupStepDefinition(
        id="lark_cli",
        title="Lark CLI",
        phase="Phase 2",
        description="Install Lark CLI and open its official configuration flow when needed.",
        depends_on=["preflight"],
        actions=[
            SetupAction(
                id="check_lark_cli",
                label="Check",
                step_id="lark_cli",
                kind="check",
            ),
            SetupAction(
                id="setup_lark_cli",
                label="Install or configure",
                step_id="lark_cli",
                kind="run",
                external_side_effect=True,
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
        depends_on=["cli_components"],
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
        id="wechat_connection",
        title="Connect WeChat",
        phase="Phase 3",
        description="Connect the local personal account and check database access.",
        depends_on=["preflight"],
        actions=[
            SetupAction(
                id="check_wechat_connection",
                label="Check",
                step_id="wechat_connection",
                kind="check",
            ),
            SetupAction(
                id="connect_wechat",
                label="Connect WeChat",
                step_id="wechat_connection",
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
        description="Generate and verify data/work-profile/work_profile.md and evidence index.",
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


def get_action_definition(action_id: str) -> SetupAction:
    for step in SETUP_WIZARD_STEPS:
        for action in step.actions:
            if action.id == action_id:
                return action
    raise KeyError(action_id)


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
        values[key.strip()] = os.path.expandvars(value.strip().strip('"').strip("'"))
    return values


def _raw_env_values(env_path: Path) -> dict[str, str]:
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
    path = Path(os.path.expandvars(value)).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def _redact_evidence_path(path: Path) -> str:
    return redact_setup_output(str(path))


def _configured_corpus_dir(repo_root: Path) -> Path:
    values = _env_values(repo_root / ".env")
    return _resolve_repo_path(repo_root, values.get("CEO_CORPUS_DIR", "data/corpus"))


def _configured_work_profile_path(repo_root: Path) -> Path:
    values = _env_values(repo_root / ".env")
    return _resolve_repo_path(
        repo_root,
        values.get("CEO_WORK_PROFILE_PATH", "data/work-profile/work_profile.md"),
    )


def _configured_service_mcp_path(repo_root: Path) -> Path:
    values = _env_values(repo_root / ".env")
    return _resolve_repo_path(
        repo_root,
        values.get("CEO_SERVICE_MCP_CONFIG_PATH", "data/config/service-mcp.json"),
    )


def _contains_sensitive_profile_evidence(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (
            BEARER_RE,
            TOKEN_RE,
            SESSION_KEY_RE,
            SESSION_RE,
            LOCAL_PATH_RE,
        )
    )


def check_service_config(
    *,
    repo_root: Path,
    runtime_snapshots: Mapping[str, RuntimeCapabilitySnapshot] | None = None,
) -> SetupStepStatus:
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
    service_mcp_value = values.get("CEO_SERVICE_MCP_CONFIG_PATH", "")
    workspace = _resolve_repo_path(repo_root, workspace_value)
    db_path = _resolve_repo_path(repo_root, db_value)
    corpus_dir = _resolve_repo_path(repo_root, corpus_value)
    service_mcp_config = _resolve_repo_path(repo_root, service_mcp_value)
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
            (
                "CEO_SERVICE_MCP_CONFIG_PATH",
                service_mcp_value,
                service_mcp_config,
            ),
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
    if runtime_snapshots is None:
        from app.agent_runtime_production import PRODUCTION_RUNTIME_CAPABILITIES

        runtime_snapshots = PRODUCTION_RUNTIME_CAPABILITIES

    runtime_routes_json = json.dumps(
        list(
            runtime_route_setup_statuses(
                env=values,
                snapshots=runtime_snapshots,
            )
        ),
        separators=(",", ":"),
    )
    return _status(
        "service_config",
        title="Service Config",
        status="done",
        summary="Service config and runtime directories are ready.",
        evidence={
            "env_exists": True,
            "dry_run_enabled": True,
            "runtime_routes_json": runtime_routes_json,
        },
    )


def check_data_corpus(*, repo_root: Path) -> SetupStepStatus:
    style_corpus = _configured_corpus_dir(repo_root) / "style_corpus.csv"
    if not style_corpus.exists():
        return _status(
            "data_corpus",
            title="Data Corpus",
            status="needs_action",
            summary="data/corpus/style_corpus.csv is missing.",
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
    profile = _configured_work_profile_path(repo_root)
    evidence = repo_root / "data" / "profile-evidence" / "evidence_index.jsonl"
    style_corpus = _configured_corpus_dir(repo_root) / "style_corpus.csv"
    if not profile.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="data/work-profile/work_profile.md is missing.",
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
            summary="data/corpus/style_corpus.csv is missing.",
        )
    profile_text = profile.read_text(encoding="utf-8")
    if _contains_sensitive_profile_evidence(profile_text):
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="failed",
            summary="data/work-profile/work_profile.md contains sensitive local evidence.",
        )
    return _status(
        "work_profile",
        title="Work Profile Distillation",
        status="done",
        summary="Work profile artifacts are ready.",
    )


def check_setup_step(
    step_id: str,
    *,
    repo_root: Path,
    store: AutoReplyStore | None = None,
) -> SetupStepStatus:
    if step_id == "wechat_connection":
        return _check_wechat_connection(store)
    if step_id == "dry_run":
        if store is None:
            values = _env_values(repo_root / ".env")
            db_path = _resolve_repo_path(
                repo_root,
                values.get(
                    "CEO_WORKER_DB",
                    "$HOME/Library/Application Support/ceo-agent-service/auto-reply.sqlite3",
                ),
            )
            store = AutoReplyStore(db_path)
        return check_dry_run(store=store)
    del store
    if step_id == "preflight":
        return _check_preflight(repo_root=repo_root)
    if step_id == "cli_components":
        return _check_cli_components(repo_root=repo_root)
    if step_id == "dingtalk_cli":
        return _check_channel_cli(
            step_id="dingtalk_cli",
            title="DingTalk CLI",
            binary="dws",
            channel="dingtalk",
        )
    if step_id == "lark_cli":
        return _check_channel_cli(
            step_id="lark_cli",
            title="Lark CLI",
            binary="lark-cli",
            channel="lark",
        )
    if step_id == "mcp":
        return _check_mcp(repo_root=repo_root)
    if step_id == "service_config":
        return check_service_config(repo_root=repo_root)
    if step_id == "data_corpus":
        return check_data_corpus(repo_root=repo_root)
    if step_id == "work_profile":
        return check_work_profile(repo_root=repo_root)
    definition = get_step_definition(step_id)
    return _status(
        definition.id,
        title=definition.title,
        status="needs_action",
        summary=f"{definition.title} requires a run action or external verification.",
    )


def check_dry_run(*, store: AutoReplyStore) -> SetupStepStatus:
    processing = store.count_reply_tasks("processing")
    failed = store.count_reply_tasks("failed")
    recoverable_blocked_attempts = store.count_recoverable_blocked_reply_attempts()
    due_follow_ups = store.count_due_follow_up_drafts(
        due_before=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    )
    evidence = {
        "processing_reply_tasks": processing,
        "failed_reply_tasks": failed,
        "recoverable_blocked_attempts": recoverable_blocked_attempts,
        "due_follow_up_drafts": due_follow_ups,
    }
    if (
        processing
        or failed
        or recoverable_blocked_attempts
        or due_follow_ups
    ):
        return _status(
            "dry_run",
            title="Dry-Run Validation",
            status="needs_action",
            summary="Unresolved reply, action, or follow-up backlog exists.",
            evidence=evidence,
        )
    return _status(
        "dry_run",
        title="Dry-Run Validation",
        status="done",
        summary="Dry-run audit state has no unresolved backlog.",
        evidence=evidence,
    )


def _check_preflight(*, repo_root: Path) -> SetupStepStatus:
    missing = [
        name
        for name in ("README.md", "app", "tests")
        if not (repo_root / name).exists()
    ]
    python = central_python()
    python_ready = python.is_file() and os.access(python, os.X_OK)
    if python_ready:
        version_check = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import sys; raise SystemExit(0 if sys.version_info >= "
                    f"{MINIMUM_PYTHON!r} else 1)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        python_ready = version_check.returncode == 0
    if missing:
        return _status(
            "preflight",
            title="Preflight",
            status="needs_action",
            summary="Repository checkout is incomplete: " + ", ".join(missing),
            evidence={"conda_python": python_ready},
        )
    return _status(
        "preflight",
        title="Preflight",
        status="done" if python_ready else "needs_action",
        summary=(
            "Repository checkout and central Conda Python are ready."
            if python_ready
            else f"Repository checkout is present, but {python} is unavailable or older than Python 3.12."
        ),
        evidence={"conda_python": python_ready},
    )


def _check_cli_components(*, repo_root: Path) -> SetupStepStatus:
    del repo_root
    codex_ready = shutil.which("codex") is not None
    terminal_notifier_ready = shutil.which("terminal-notifier") is not None
    nvwa_ready = any(
        path.exists()
        for path in (
            Path.home() / ".agents" / "skills" / "nuwa" / "SKILL.md",
            Path.home() / ".agents" / "skills" / "huashu-nuwa" / "SKILL.md",
        )
    )
    missing = [
        label
        for label, ready in (
            ("codex", codex_ready),
            ("Nvwa skill", nvwa_ready),
            ("terminal-notifier", terminal_notifier_ready),
        )
        if not ready
    ]
    if missing:
        return _status(
            "cli_components",
            title="CLI Components",
            status="needs_action",
            summary="Missing CLI components: " + ", ".join(missing),
            evidence={
                "codex": codex_ready,
                "nvwa_skill": nvwa_ready,
                "terminal_notifier": terminal_notifier_ready,
            },
        )
    return _status(
        "cli_components",
        title="CLI Components",
        status="done",
        summary="Codex CLI, Nvwa skill, and terminal-notifier are available.",
        evidence={
            "codex": True,
            "nvwa_skill": True,
            "terminal_notifier": True,
        },
    )


def _check_channel_cli(
    *,
    step_id: str,
    title: str,
    binary: str,
    channel: str,
) -> SetupStepStatus:
    path = shutil.which(binary)
    if path is None:
        return _status(
            step_id,
            title=title,
            status="needs_action",
            summary=f"{binary} is not installed.",
            evidence={"installed": False},
        )
    gates = default_channel_gates(
        dws_binary=binary if channel == "dingtalk" else "dws",
        lark_binary=binary if channel == "lark" else "lark-cli",
    )
    result = gates[channel].check()
    ready = result.state is ChannelGateState.READY
    return _status(
        step_id,
        title=title,
        status="done" if ready else "needs_action",
        summary=(
            f"{binary} is installed and configured."
            if ready
            else f"{binary} needs configuration: {result.reason_code}."
        ),
        evidence={
            "installed": True,
            "channel_state": result.state.value,
            "reason_code": result.reason_code,
        },
    )


def _setup_channel_cli(
    *,
    step_id: str,
    title: str,
    binary: str,
    channel: str,
    env: dict[str, str],
) -> SetupWizardEvent:
    merged_env = os.environ.copy()
    merged_env.update(env)
    installed = shutil.which(binary) is not None
    stdout = ""
    stderr = ""
    if not installed:
        install_command = _channel_install_command(
            binary=binary,
            env=merged_env,
        )
        if install_command is None:
            return SetupWizardEvent(
                step_id=step_id,
                action_id=f"setup_{step_id}",
                status="failed",
                summary=(
                    "DWS installer is not configured. Set DWS_INSTALLER_PATH "
                    "or DWS_INSTALL_COMMAND in the service environment."
                ),
                evidence={"installed": False},
            )
        completed = subprocess.run(
            install_command,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = redact_setup_output(completed.stdout or "")
        stderr = redact_setup_output(completed.stderr or "")
        installed = completed.returncode == 0 and shutil.which(binary) is not None
        if not installed:
            return SetupWizardEvent(
                step_id=step_id,
                action_id=f"setup_{step_id}",
                status="failed",
                summary=f"{title} installation failed.",
                evidence={
                    "installed": False,
                    "returncode": completed.returncode,
                },
                stdout_excerpt=stdout[-4000:],
                stderr_excerpt=stderr[-4000:],
            )

    gates = default_channel_gates(
        dws_binary=binary if channel == "dingtalk" else "dws",
        lark_binary=binary if channel == "lark" else "lark-cli",
    )
    result = gates[channel].check()
    if result.state is ChannelGateState.READY:
        return SetupWizardEvent(
            step_id=step_id,
            action_id=f"setup_{step_id}",
            status="done",
            next_step_status="done",
            summary=f"{title} is installed and configured.",
            evidence={
                "installed": True,
                "channel_state": result.state.value,
            },
            stdout_excerpt=stdout[-4000:],
            stderr_excerpt=stderr[-4000:],
        )

    try:
        process = subprocess.Popen(
            [binary, "auth", "login"],
            env=merged_env,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return SetupWizardEvent(
            step_id=step_id,
            action_id=f"setup_{step_id}",
            status="failed",
            summary=f"{title} configuration could not be opened.",
            evidence={
                "installed": True,
                "channel_state": result.state.value,
            },
            stderr_excerpt=redact_setup_output(str(exc)),
        )
    return SetupWizardEvent(
        step_id=step_id,
        action_id=f"setup_{step_id}",
        status="done",
        next_step_status="needs_action",
        summary=(
            f"{title} is installed. Its configuration window was opened; "
            "finish authorization, then click Check."
        ),
        evidence={
            "installed": True,
            "channel_state": result.state.value,
            "login_started": True,
            "pid": process.pid,
        },
        stdout_excerpt=stdout[-4000:],
        stderr_excerpt=stderr[-4000:],
    )


def _channel_install_command(
    *,
    binary: str,
    env: dict[str, str],
) -> list[str] | None:
    if binary == "dws":
        installer_path = env.get("DWS_INSTALLER_PATH", "").strip()
        if installer_path:
            return [installer_path]
        configured = env.get("DWS_INSTALL_COMMAND", "").strip()
        return ["/bin/zsh", "-lc", configured] if configured else None
    configured = env.get("LARK_CLI_INSTALL_COMMAND", "").strip()
    if configured:
        return ["/bin/zsh", "-lc", configured]
    npm = shutil.which("npm")
    if npm:
        return [npm, "install", "-g", "@larksuite/cli"]
    return None


def run_setup_action(
    action_id: str,
    *,
    repo_root: Path,
    env: dict[str, str] | None = None,
) -> SetupWizardEvent:
    if action_id == "setup_cli_components":
        return _setup_cli_components(repo_root, env or {})
    if action_id == "setup_dingtalk_cli":
        return _setup_channel_cli(
            step_id="dingtalk_cli",
            title="DingTalk CLI",
            binary="dws",
            channel="dingtalk",
            env=env or {},
        )
    if action_id == "setup_lark_cli":
        return _setup_channel_cli(
            step_id="lark_cli",
            title="Lark CLI",
            binary="lark-cli",
            channel="lark",
            env=env or {},
        )
    if action_id == "setup_service_config":
        return _setup_service_config(repo_root, env or {})
    if action_id == "setup_mcp":
        return _setup_mcp(repo_root, env or {})
    if action_id == "connect_wechat":
        return _run_wechat_setup_action(action_id)
    if action_id == "run_dry_run":
        return _run_dry_run_action(repo_root, env or {})
    if action_id == "install_launchd":
        return _install_launchd_action(repo_root, env or {})
    try:
        action = get_action_definition(action_id)
    except KeyError:
        return SetupWizardEvent(
            step_id="unknown",
            action_id=action_id,
            status="failed",
            summary=f"Unknown setup action: {action_id}",
        )
    return SetupWizardEvent(
        step_id=action.step_id,
        action_id=action_id,
        status="failed",
        summary=f"{action.label} is not automated yet.",
    )


def _run_dry_run_action(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    merged_env = os.environ.copy()
    merged_env.update(env)
    merged_env["CEO_NOT_SEND_MESSAGE"] = "1"
    args = [
        str(central_python(merged_env)),
        "-m",
        "app.cli",
        "run-once",
        "--not-send-message",
    ]
    completed = subprocess.run(
        args,
        cwd=repo_root,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    succeeded = completed.returncode == 0
    return SetupWizardEvent(
        step_id="dry_run",
        action_id="run_dry_run",
        status="done" if succeeded else "failed",
        summary=(
            "Dry-run validation completed."
            if succeeded
            else f"Dry-run validation failed with exit code {completed.returncode}."
        ),
        evidence={"returncode": completed.returncode},
        stdout_excerpt=redact_setup_output((completed.stdout or "")[-4000:]),
        stderr_excerpt=redact_setup_output((completed.stderr or "")[-4000:]),
    )


def _install_launchd_action(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    merged_env = os.environ.copy()
    merged_env.update(env)
    args = [
        "/bin/zsh",
        "-lc",
        "sleep 1; exec scripts/install-auto-reply-agents.sh",
    ]
    log_path = repo_root / "data" / "setup-launchd-install.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(
            args,
            cwd=repo_root,
            env=merged_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_file.close()
    return SetupWizardEvent(
        step_id="launchd",
        action_id="install_launchd",
        status="done",
        summary="Launchd service install started in background.",
        evidence={
            "pid": process.pid,
            "log_path": _redact_evidence_path(log_path),
        },
    )


def _check_wechat_connection(store) -> SetupStepStatus:
    from app import config
    from app.store import AutoReplyStore
    from app.wechat import service

    try:
        store = store or AutoReplyStore(config.worker_db_path())
        result = service.build_setup_service(store).check()
        status = "done" if result.status == "done" else "needs_action"
        summary = result.summary
    except Exception as exc:  # pragma: no cover - defensive
        status, summary = "needs_action", f"WeChat check unavailable: {exc}"
    return SetupStepStatus(
        step_id="wechat_connection",
        title="Connect WeChat",
        status=status,
        summary=summary,
    )


def _run_wechat_setup_action(action_id: str) -> SetupWizardEvent:
    from app import config
    from app.store import AutoReplyStore
    from app.wechat import service

    _capability_to_step = {"ready": "done", "blocked": "blocked", "failed": "failed"}
    try:
        store = AutoReplyStore(config.worker_db_path())
        setup = service.build_setup_service(store)
        result = setup.verify() if action_id == "verify_wechat" else setup.connect()
    except Exception as exc:  # pragma: no cover - defensive
        return SetupWizardEvent(
            step_id="wechat_connection",
            action_id=action_id,
            status="failed",
            summary=f"WeChat setup error: {exc}",
        )
    return SetupWizardEvent(
        step_id="wechat_connection",
        action_id=action_id,
        status="done" if result.status in ("done", "needs_action") else "failed",
        next_step_status=_capability_to_step.get(
            result.next_step_status, result.next_step_status
        ),
        summary=result.summary,
        evidence={key: str(value) for key, value in (result.evidence or {}).items()},
    )


def _setup_cli_components(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    script = repo_root / "scripts" / "bootstrap-local-components.sh"
    if not script.exists():
        return SetupWizardEvent(
            step_id="cli_components",
            action_id="setup_cli_components",
            status="failed",
            summary="scripts/bootstrap-local-components.sh is missing.",
        )

    merged_env = os.environ.copy()
    merged_env.update(env)
    completed = subprocess.run(
        [str(script), "--format", "json"],
        cwd=repo_root,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = redact_setup_output(completed.stdout)
    stderr = redact_setup_output(completed.stderr)
    evidence: dict[str, object] = {
        "returncode": completed.returncode,
    }
    summary = "Local CLI components were checked and repaired."
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        if payload.get("components"):
            evidence["components_json"] = redact_setup_output(
                json.dumps(payload["components"], ensure_ascii=False, sort_keys=True)
            )
        if isinstance(payload.get("summary"), str) and payload["summary"].strip():
            summary = redact_setup_output(payload["summary"])

    if completed.returncode != 0:
        if not summary or summary == "Local CLI components were checked and repaired.":
            summary = (stderr or stdout or "Component bootstrap failed.").strip()
        return SetupWizardEvent(
            step_id="cli_components",
            action_id="setup_cli_components",
            status="failed",
            summary=summary,
            evidence=evidence,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
        )

    return SetupWizardEvent(
        step_id="cli_components",
        action_id="setup_cli_components",
        status="done",
        summary=summary,
        evidence=evidence,
        stdout_excerpt=stdout,
        stderr_excerpt=stderr,
    )


def _setup_service_config(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    env_path = repo_root / ".env"
    source_path = env_path if env_path.exists() else repo_root / ".env.example"
    values = _raw_env_values(source_path)
    defaults = {
        "CEO_WORKSPACE": "workspace",
        "CEO_WORKER_DB": "$HOME/Library/Application Support/ceo-agent-service/auto-reply.sqlite3",
        "CEO_CORPUS_DIR": "data/corpus",
        "CEO_WORK_PROFILE_PATH": "data/work-profile/work_profile.md",
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH": "data/prompts/developer_prompt.md",
        "CEO_USER_PROMPT_TEMPLATE_PATH": "data/prompts/user_prompt.md",
        "CEO_AUDIT_RULES_TEMPLATE_PATH": "data/prompts/audit_rules.md",
        "CEO_SERVICE_MCP_CONFIG_PATH": "data/config/service-mcp.json",
        "CEO_NOT_SEND_MESSAGE": "1",
        "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth",
        "CEO_CODEX_MODEL": "gpt-5.5",
        "CEO_CODEX_API_MODEL": "gpt-5.5",
        "CEO_RUNTIME_PROBE_INTERVAL": "5m",
        "CEO_RUNTIME_ROUTE_RETRY_DELAY": "30m",
    }
    for key, default in defaults.items():
        values[key] = env.get(key, values.get(key) or default)
    if env.get("CEO_CODEX_API_KEY", values.get("CEO_CODEX_API_KEY", "")).strip():
        values["CEO_CODEX_API_KEY"] = env.get(
            "CEO_CODEX_API_KEY", values.get("CEO_CODEX_API_KEY", "")
        ).strip()

    env_path.write_text(
        "".join(f"{key}={values[key]}\n" for key in sorted(values)),
        encoding="utf-8",
    )

    workspace = _resolve_repo_path(repo_root, values["CEO_WORKSPACE"])
    db_parent = _resolve_repo_path(repo_root, values["CEO_WORKER_DB"]).parent
    corpus_dir = _resolve_repo_path(repo_root, values["CEO_CORPUS_DIR"])
    work_profile = _resolve_repo_path(repo_root, values["CEO_WORK_PROFILE_PATH"])
    developer_prompt = _resolve_repo_path(
        repo_root,
        values["CEO_DEVELOPER_PROMPT_TEMPLATE_PATH"],
    )
    user_prompt = _resolve_repo_path(
        repo_root,
        values["CEO_USER_PROMPT_TEMPLATE_PATH"],
    )
    audit_rules = _resolve_repo_path(
        repo_root,
        values["CEO_AUDIT_RULES_TEMPLATE_PATH"],
    )
    service_mcp_config = _resolve_repo_path(
        repo_root,
        values["CEO_SERVICE_MCP_CONFIG_PATH"],
    )
    workspace.mkdir(parents=True, exist_ok=True)
    db_parent.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    _seed_missing_file(
        developer_prompt,
        SEED_DEVELOPER_PROMPT_TEMPLATE.read_text(encoding="utf-8"),
    )
    _seed_missing_file(
        user_prompt,
        SEED_USER_PROMPT_TEMPLATE.read_text(encoding="utf-8"),
    )
    _seed_missing_file(
        audit_rules,
        SEED_AUDIT_RULES_TEMPLATE.read_text(encoding="utf-8"),
    )
    _seed_missing_file(work_profile, DEFAULT_WORK_PROFILE_TEXT)
    setup_env = os.environ.copy()
    setup_env.update(values)
    setup_env.update(env)
    _seed_or_refresh_service_mcp_manifest(service_mcp_config, env=setup_env)
    try:
        load_service_mcp_servers(service_mcp_config, env=setup_env)
    except ServiceMcpConfigError as exc:
        return SetupWizardEvent(
            step_id="service_config",
            action_id="setup_service_config",
            status="failed",
            summary=exc.reason,
            evidence={
                "env_path": _redact_evidence_path(env_path),
                "service_mcp_config": _redact_evidence_path(service_mcp_config),
            },
        )

    return SetupWizardEvent(
        step_id="service_config",
        action_id="setup_service_config",
        status="done",
        summary="Created .env, runtime directories, and default runtime files.",
        evidence={
            "env_path": _redact_evidence_path(env_path),
            "workspace": _redact_evidence_path(workspace),
            "db_parent": _redact_evidence_path(db_parent),
            "corpus_dir": _redact_evidence_path(corpus_dir),
            "work_profile": _redact_evidence_path(work_profile),
            "developer_prompt": _redact_evidence_path(developer_prompt),
            "user_prompt": _redact_evidence_path(user_prompt),
            "audit_rules": _redact_evidence_path(audit_rules),
            "service_mcp_config": _redact_evidence_path(service_mcp_config),
            "runtime_routes_json": json.dumps(
                [
                    {
                        "route_name": status["route_name"],
                        "status": status["status"],
                        "secret_configured": status["secret_configured"],
                    }
                    for status in runtime_route_setup_statuses(
                        env=values,
                        snapshots={},
                    )
                ],
                separators=(",", ":"),
            ),
        },
    )


def _seed_missing_file(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_or_refresh_service_mcp_manifest(
    path: Path,
    *,
    env: dict[str, str],
) -> None:
    seed = json.loads(DEFAULT_SERVICE_MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    seed_servers = seed["servers"]
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        current_servers = current.get("servers") if isinstance(current, dict) else None
        if not isinstance(current_servers, dict) or any(
            name not in seed_servers or entry != seed_servers[name]
            for name, entry in current_servers.items()
        ):
            return

    selected_servers = {
        name: entry
        for name, entry in seed_servers.items()
        if _service_mcp_entry_is_configured(entry, env=env)
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"servers": selected_servers},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _service_mcp_entry_is_configured(
    entry: object,
    *,
    env: dict[str, str],
) -> bool:
    if not isinstance(entry, dict):
        return False
    references: list[str] = []
    for field in ("url_env", "command_env", "args_env", "bearer_token_env_var"):
        value = entry.get(field)
        if isinstance(value, str):
            references.append(value)
    env_headers = entry.get("env_http_headers")
    if isinstance(env_headers, dict):
        references.extend(
            value for value in env_headers.values() if isinstance(value, str)
        )
    return all(bool(env.get(reference, "").strip()) for reference in references)


def _check_mcp(*, repo_root: Path) -> SetupStepStatus:
    values = _env_values(repo_root / ".env")
    merged_env = os.environ.copy()
    merged_env.update(values)
    statuses = check_mcp_statuses(
        service_config_path=_configured_service_mcp_path(repo_root),
        env=merged_env,
    )
    first_failure = next((status for status in statuses if not status.ready), None)
    if first_failure is not None:
        return _status(
            "mcp",
            title="Memory Connector MCP",
            status="needs_action",
            summary=first_failure.reason,
            evidence={
                "server": first_failure.name,
                "state": first_failure.state,
            },
        )
    return _status(
        "mcp",
        title="Memory Connector MCP",
        status="done",
        summary="Service MCP manifest is ready.",
        evidence={"configured_servers": len(statuses)},
    )


def _setup_mcp(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    def first_nonblank(*values: str | None) -> str:
        for value in values:
            normalized = (value or "").strip()
            if normalized:
                return normalized
        return ""

    env_path = repo_root / ".env"
    persisted_values = _raw_env_values(env_path)
    service_config_value = first_nonblank(
        env.get("CEO_SERVICE_MCP_CONFIG_PATH"),
        persisted_values.get("CEO_SERVICE_MCP_CONFIG_PATH"),
        os.getenv("CEO_SERVICE_MCP_CONFIG_PATH"),
        "data/config/service-mcp.json",
    )
    service_config_path = _resolve_repo_path(repo_root, service_config_value)
    submitted_memory_url = (env.get("MEMORY_CONNECTOR_URL") or "").strip()
    persisted_memory_url = (
        persisted_values.get("MEMORY_CONNECTOR_URL") or ""
    ).strip()
    process_memory_url = (os.getenv("MEMORY_CONNECTOR_URL") or "").strip()
    memory_url = first_nonblank(
        submitted_memory_url,
        persisted_memory_url,
        process_memory_url,
    )
    memory_url_source = "environment" if submitted_memory_url else ""
    if not memory_url_source and persisted_memory_url:
        memory_url_source = "service_env_file"
    if not memory_url_source and process_memory_url:
        memory_url_source = "process_environment"
    if not service_mcp_url_is_safe(memory_url):
        if not memory_url:
            memory_url = ""
        else:
            persisted_values["CEO_SERVICE_MCP_CONFIG_PATH"] = service_config_value
            _write_env_values(env_path, persisted_values)
            return SetupWizardEvent(
                step_id="mcp",
                action_id="setup_mcp",
                status="failed",
                summary=(
                    "MEMORY_CONNECTOR_URL must be an http(s) URL without credentials, "
                    "query, or fragment."
                ),
                evidence={
                    "service_mcp_config": _redact_evidence_path(service_config_path),
                },
            )

    persisted_values["CEO_SERVICE_MCP_CONFIG_PATH"] = service_config_value
    service_env_keys = (
        "MEMORY_CONNECTOR_URL",
        "CONNECTOR_API_KEY",
        "MEMORY_CONNECTOR_AUTH_TYPE",
        "MEMORY_CONNECTOR_CONTENT_TYPE",
        "CEO_XIAOQING_MCP_COMMAND",
        "CEO_XIAOQING_MCP_ARGS_JSON",
    )
    resolved_service_values: dict[str, str] = {}
    for key in service_env_keys:
        value = first_nonblank(
            env.get(key),
            persisted_values.get(key),
            os.getenv(key),
        )
        if value:
            persisted_values[key] = value
            resolved_service_values[key] = value
    _write_env_values(env_path, persisted_values)
    setup_env = os.environ.copy()
    setup_env.update(persisted_values)
    setup_env.update(
        {key: value for key, value in env.items() if key not in service_env_keys}
    )
    setup_env.update(resolved_service_values)
    _seed_or_refresh_service_mcp_manifest(service_config_path, env=setup_env)
    try:
        servers = load_service_mcp_servers(service_config_path, env=setup_env)
    except ServiceMcpConfigError as exc:
        persisted_values["CEO_SERVICE_MCP_CONFIG_PATH"] = service_config_value
        return SetupWizardEvent(
            step_id="mcp",
            action_id="setup_mcp",
            status="failed",
            summary=exc.reason,
            evidence={
                "service_mcp_config": _redact_evidence_path(service_config_path),
            },
        )
    return SetupWizardEvent(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="Service MCP manifest was configured and validated.",
        evidence={
            "service_mcp_config": _redact_evidence_path(service_config_path),
            "memory_url_source": memory_url_source,
            "configured_servers": len(servers),
        },
    )


def _write_env_values(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key}={values[key]}\n" for key in sorted(values)),
        encoding="utf-8",
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
                manual_confirmation_allowed=any(
                    action.kind == "confirm" for action in definition.actions
                ),
                updated_at=row["updated_at"] if row else "",
            )
        )

    return SetupWizardStatus(steps=statuses)
