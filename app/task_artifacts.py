"""Private, task-scoped temporary artifact directories."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from app import config as app_config


TASK_ARTIFACT_ROOT = Path("/tmp").resolve() / "ceo-agent-service-task-artifacts"
logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = frozenset({"done", "failed", "needs_human", "skipped"})


def _valid_component(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and Path(value).name == value
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _legacy_task_artifact_directory(task_id: int) -> Path:
    workspace = app_config.workspace_path().resolve()
    if workspace.is_relative_to(app_config.repo_root().resolve()):
        raise ValueError("task file workspace overlaps service source")
    legacy_root = workspace / "consumer-artifacts"
    if legacy_root.is_symlink():
        raise ValueError("legacy task file directory is linked")
    if legacy_root.resolve() == TASK_ARTIFACT_ROOT.resolve():
        raise ValueError("legacy and temporary task file roots overlap")
    return legacy_root / str(task_id)


def migrate_legacy_task_artifacts(task_id: int) -> None:
    """Move this task's old workspace artifacts to /tmp before role access."""
    if type(task_id) is not int or task_id <= 0:
        raise ValueError("task file binding is invalid")
    source = _legacy_task_artifact_directory(task_id)
    if source.is_symlink():
        raise ValueError("legacy task file directory is linked")
    if not source.exists():
        return
    if not source.is_dir():
        raise ValueError("legacy task file directory is invalid")

    task_root = TASK_ARTIFACT_ROOT / str(task_id)
    if TASK_ARTIFACT_ROOT.is_symlink():
        raise ValueError("task file directory is linked")
    if task_root.is_symlink():
        raise ValueError("task file directory is linked")
    TASK_ARTIFACT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    TASK_ARTIFACT_ROOT.chmod(0o700)
    if not task_root.exists():
        os.replace(source, task_root)
        task_root.chmod(0o700)
        return
    if not task_root.is_dir():
        raise ValueError("task file directory is invalid")

    for generation in source.iterdir():
        if generation.is_symlink() or not generation.is_dir():
            raise ValueError("legacy task generation directory is invalid")
        if not _valid_component(generation.name):
            raise ValueError("legacy task generation is invalid")
        destination = task_root / generation.name
        if destination.is_symlink() or destination.exists():
            raise ValueError("legacy task generation conflicts with temporary artifacts")
        os.replace(generation, destination)
        destination.chmod(0o700)
    source.rmdir()


def task_artifact_directory(
    task_id: int, generation: str, *, create: bool
) -> Path:
    if type(task_id) is not int or task_id <= 0 or not _valid_component(generation):
        raise ValueError("task file binding is invalid")

    root = TASK_ARTIFACT_ROOT
    task_root = root / str(task_id)
    generation_root = task_root / generation
    for directory in (root, task_root, generation_root):
        if directory.is_symlink():
            raise ValueError("task file directory is linked")
        if create:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("task file directory is invalid")
            directory.chmod(0o700)
        elif directory.exists() and not directory.is_dir():
            raise ValueError("task file directory is invalid")
    return generation_root


def carry_forward_task_artifacts(task_id: int, generation: str) -> None:
    """Copy prior task generations into the current generation without moving them."""
    current = task_artifact_directory(task_id, generation, create=True)
    task_root = current.parent
    prior_generations = []
    for path in task_root.iterdir():
        if path.name == generation:
            continue
        if path.is_symlink() or not path.is_dir() or not _valid_component(path.name):
            raise ValueError("task file generation is invalid")
        prior_generations.append(path)
    prior_generations = sorted(
        prior_generations,
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for prior in prior_generations:
        for source in prior.iterdir():
            if source.name.startswith(".task-artifact-"):
                continue
            if source.is_symlink() or not source.is_file() or not _valid_component(source.name):
                raise ValueError("task file is invalid")
            destination = current / source.name
            if destination.is_symlink():
                raise ValueError("task file is linked")
            if destination.exists():
                continue
            shutil.copyfile(source, destination)
            destination.chmod(0o600)


def cleanup_task_artifacts(task_id: int) -> None:
    if type(task_id) is not int or task_id <= 0:
        raise ValueError("task file binding is invalid")
    legacy_task_root = _legacy_task_artifact_directory(task_id)
    root = TASK_ARTIFACT_ROOT
    task_root = root / str(task_id)
    if root.is_symlink() or task_root.is_symlink():
        raise ValueError("task file directory is linked")
    if task_root.exists():
        if not task_root.is_dir():
            raise ValueError("task file directory is invalid")
        shutil.rmtree(task_root)

    if legacy_task_root.is_symlink():
        raise ValueError("legacy task file directory is linked")
    if legacy_task_root.exists():
        if not legacy_task_root.is_dir():
            raise ValueError("legacy task file directory is invalid")
        shutil.rmtree(legacy_task_root)


def cleanup_terminal_task_artifacts(store, task_id: int) -> None:
    task = store.get_reply_task(task_id)
    if task is None or task.status not in TERMINAL_TASK_STATUSES:
        return
    if task.status == "failed" and getattr(task, "channel", None) == "scheduled":
        return
    try:
        cleanup_task_artifacts(task_id)
    except (OSError, ValueError) as exc:
        logger.warning("task artifact cleanup failed for task %s: %s", task_id, exc)
