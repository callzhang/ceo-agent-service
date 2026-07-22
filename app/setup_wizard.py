import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, cast

from app.cli import setup_memory_connector_command
from app.developer_prompt import (
    SEED_DEVELOPER_PROMPT_TEMPLATE,
    SEED_USER_PROMPT_TEMPLATE,
)
from app.memory_setup import codex_memory_connector_url
from app.prompt import DEFAULT_WORK_PROFILE_TEXT
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


def _step(**values: Any) -> SetupStepDefinition:
    return SetupStepDefinition.model_validate(values)


SETUP_WIZARD_STEPS: tuple[SetupStepDefinition, ...] = (
    _step(
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
    _step(
        id="cli_components",
        title="CLI Components",
        phase="Phase 2",
        description="Verify and install dws, Codex CLI, Nvwa skill, and notifications.",
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
    _step(
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
    _step(
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
    _step(
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
    _step(
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
    _step(
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
    _step(
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
    _step(
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
    _step(
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
    status: SetupStatus,
    summary: str,
    evidence: Mapping[str, str | int | bool] | None = None,
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
                values.get("CEO_WORKER_DB", "data/auto-reply.sqlite3"),
            )
            store = AutoReplyStore(db_path)
        return check_dry_run(store=store)
    del store
    if step_id == "preflight":
        return _check_preflight(repo_root=repo_root)
    if step_id == "cli_components":
        return _check_cli_components(repo_root=repo_root)
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
    evidence = {
        "processing_reply_tasks": processing,
        "failed_reply_tasks": failed,
    }
    if processing or failed:
        return _status(
            "dry_run",
            title="Dry-Run Validation",
            status="needs_action",
            summary="Unresolved failed or processing reply tasks exist.",
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
    python_ready = (repo_root / ".venv" / "bin" / "python").exists()
    if missing:
        return _status(
            "preflight",
            title="Preflight",
            status="needs_action",
            summary="Repository checkout is incomplete: " + ", ".join(missing),
            evidence={"python_venv": python_ready},
        )
    return _status(
        "preflight",
        title="Preflight",
        status="done" if python_ready else "needs_action",
        summary=(
            "Repository checkout and virtualenv are ready."
            if python_ready
            else "Repository checkout is present, but .venv/bin/python is missing."
        ),
        evidence={"python_venv": python_ready},
    )


def _check_cli_components(*, repo_root: Path) -> SetupStepStatus:
    del repo_root
    dws_ready = shutil.which("dws") is not None
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
            ("dws", dws_ready),
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
                "dws": dws_ready,
                "codex": codex_ready,
                "nvwa_skill": nvwa_ready,
                "terminal_notifier": terminal_notifier_ready,
            },
        )
    return _status(
        "cli_components",
        title="CLI Components",
        status="done",
        summary="dws, Codex CLI, Nvwa skill, and terminal-notifier are available.",
        evidence={
            "dws": True,
            "codex": True,
            "nvwa_skill": True,
            "terminal_notifier": True,
        },
    )


def run_setup_action(
    action_id: str,
    *,
    repo_root: Path,
    env: dict[str, str] | None = None,
) -> SetupWizardEvent:
    if action_id == "setup_cli_components":
        return _setup_cli_components(repo_root, env or {})
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
    args = [".venv/bin/ceo-agent", "run-once", "--not-send-message"]
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
    evidence: dict[str, str | int | bool] = {
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
        "CEO_WORKER_DB": "data/auto-reply.sqlite3",
        "CEO_CORPUS_DIR": "data/corpus",
        "CEO_WORK_PROFILE_PATH": "data/work-profile/work_profile.md",
        "CEO_DEVELOPER_PROMPT_TEMPLATE_PATH": "data/prompts/developer_prompt.md",
        "CEO_USER_PROMPT_TEMPLATE_PATH": "data/prompts/user_prompt.md",
        "CEO_NOT_SEND_MESSAGE": "1",
    }
    for key, default in defaults.items():
        values[key] = env.get(key, values.get(key) or default)

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
    _seed_missing_file(work_profile, DEFAULT_WORK_PROFILE_TEXT)

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
        },
    )


def _seed_missing_file(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_mcp(
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    codex_config = env.get("CODEX_CONFIG_PATH") or os.getenv("CODEX_CONFIG_PATH", "")
    if not codex_config:
        codex_home = env.get("CODEX_HOME") or os.getenv("CODEX_HOME", "~/.codex")
        codex_config = str(Path(codex_home).expanduser() / "config.toml")
    codex_config_path = Path(codex_config).expanduser()
    memory_url = (
        env.get("MEMORY_CONNECTOR_URL") or os.getenv("MEMORY_CONNECTOR_URL", "")
    ).strip()
    memory_url_source = "environment" if memory_url else ""
    if not memory_url:
        memory_url = codex_memory_connector_url(codex_config_path)
        memory_url_source = "installed_codex_config" if memory_url else ""
    if not memory_url:
        return SetupWizardEvent(
            step_id="mcp",
            action_id="setup_mcp",
            status="failed",
            summary="MEMORY_CONNECTOR_URL is missing.",
        )
    claude_config = env.get("CLAUDE_CONFIG_PATH") or os.getenv(
        "CLAUDE_CONFIG_PATH",
        str(
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        ),
    )

    stdout = StringIO()
    stderr = StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = setup_memory_connector_command(
                memory_url=memory_url,
                codex_config=str(codex_config_path),
                claude_config=claude_config,
            )
    except BaseException as exc:
        return SetupWizardEvent(
            step_id="mcp",
            action_id="setup_mcp",
            status="failed",
            summary=redact_setup_output(str(exc)),
            stdout_excerpt=redact_setup_output(stdout.getvalue()),
            stderr_excerpt=redact_setup_output(stderr.getvalue()),
        )

    stdout_excerpt = "\n".join(
        part
        for part in (
            stdout.getvalue().strip(),
            json.dumps(result, ensure_ascii=False, sort_keys=True),
        )
        if part
    )
    return SetupWizardEvent(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="Memory Connector MCP config checked.",
        evidence={
            "codex_config": redact_setup_output(result["codex_config"]),
            "claude_status": result["claude_status"],
            "memory_url_source": memory_url_source,
        },
        stdout_excerpt=redact_setup_output(stdout_excerpt),
        stderr_excerpt=redact_setup_output(stderr.getvalue()),
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
            invalid_status = persisted_status
            persisted_status = "failed"
            summary = f"Invalid persisted status: {invalid_status}"
        else:
            summary = row["summary"] if row else ""
        persisted_status = cast(SetupStatus, persisted_status)

        statuses.append(
            SetupStepStatus(
                step_id=definition.id,
                title=definition.title,
                status=persisted_status,
                summary=summary,
                available_actions=list(definition.actions),
                manual_confirmation_allowed=any(
                    action.kind == "confirm" for action in definition.actions
                ),
                updated_at=row["updated_at"] if row else "",
            )
        )

    return SetupWizardStatus(steps=statuses)
