from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.repository_upgrade import (
    RepositorySnapshot,
    RepositoryUpgradeConflict,
    RepositoryUpgradeService,
    UpgradeStatus,
)
from app.repository_updater import (
    UpgradeOperation,
    persist_operation,
    UPGRADE_OPERATION_STATE_KEY,
)
from app.repository_release_notes import (
    release_notes,
    ReleaseSummaryAgent,
    ReleaseNotesError,
)


class StartUpgradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=120)
    fingerprint: str = Field(min_length=1, max_length=128)
    branch_name: str = ""
    commit_message: str = ""


class SuggestPreservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(min_length=1, max_length=128)


def _snapshot_payload(snapshot: RepositorySnapshot | None) -> dict[str, object] | None:
    return snapshot.model_dump(mode="json") if snapshot is not None else None


def register_repository_upgrade_routes(
    app: FastAPI,
    *,
    service_factory: Callable[[], RepositoryUpgradeService],
    updater_launcher: Callable[[UpgradeOperation], int] | None = None,
) -> None:
    @app.get("/api/repository-upgrade/status")
    def status() -> dict[str, object]:
        service = service_factory()
        state = service.load_state()
        raw_progress = service.store.get_service_state(UPGRADE_OPERATION_STATE_KEY)
        progress = json.loads(raw_progress) if raw_progress else None
        return {
            "current_commit": service.repository.resolve_ref("HEAD"),
            "progress": {
                key: progress.get(key, "")
                for key in (
                    "operation_id",
                    "status",
                    "target_commit",
                    "installed_commit",
                    "error",
                    "updated_at",
                )
            }
            if progress
            else None,
            "snapshot": _snapshot_payload(state.snapshot),
            "operation": state.operation.model_dump(mode="json")
            if state.operation is not None
            else None,
        }

    @app.post("/api/repository-upgrade/check")
    def check() -> dict[str, object]:
        snapshot = service_factory().check()
        return {"snapshot": _snapshot_payload(snapshot)}

    @app.post("/api/repository-upgrade/notes")
    def notes(request: SuggestPreservationRequest) -> dict[str, object]:
        service = service_factory()
        snapshot = service.load_state().snapshot
        if snapshot is None or snapshot.fingerprint != request.fingerprint:
            raise HTTPException(status_code=409, detail="repository_state_changed")
        try:
            return release_notes(
                service.repository,
                service.store,
                snapshot.local_commit,
                snapshot.remote_commit,
                ReleaseSummaryAgent(service.repository.root),
            )
        except ReleaseNotesError as exc:
            return dict(
                local_commit=snapshot.local_commit,
                target_commit=snapshot.remote_commit,
                commits=exc.commits,
                summary="",
                summary_source="ai",
                summary_error=str(exc),
            )

    @app.post("/api/repository-upgrade/start", status_code=202)
    def start(request: StartUpgradeRequest) -> dict[str, object]:
        service = service_factory()
        state = service.load_state()
        snapshot = state.snapshot
        if snapshot is None or snapshot.fingerprint != request.fingerprint:
            raise HTTPException(status_code=409, detail="repository_state_changed")
        if snapshot.status not in {
            UpgradeStatus.UPDATE_AVAILABLE,
            UpgradeStatus.LOCAL_CHANGES,
        }:
            raise HTTPException(status_code=409, detail="repository_not_upgradable")
        if snapshot.status is UpgradeStatus.LOCAL_CHANGES and (
            not request.branch_name.strip() or not request.commit_message.strip()
        ):
            raise HTTPException(
                status_code=422,
                detail="preservation_branch_and_commit_message_required",
            )
        try:
            reservation = service.reserve_operation(
                request.operation_id,
                request.fingerprint,
            )
        except RepositoryUpgradeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if reservation.idempotent:
            return {"operation_id": reservation.operation.operation_id, "pid": None}
        operation = UpgradeOperation(
            operation_id=reservation.operation.operation_id,
            expected_fingerprint=request.fingerprint,
            original_commit=snapshot.local_commit,
            target_commit=snapshot.remote_commit,
            branch_name=request.branch_name,
            commit_message=request.commit_message,
        )
        persist_operation(service.store, operation)
        launcher = updater_launcher or _default_updater_launcher
        try:
            pid = launcher(operation)
        except OSError as exc:
            from app.service_update_runner import record_result

            record_result(service.store, operation, "failed", error="无法启动更新进程")
            service.release_operation(operation.operation_id)
            raise HTTPException(status_code=503, detail="无法启动更新进程") from exc
        return {"operation_id": operation.operation_id, "pid": pid}


def _default_updater_launcher(operation: UpgradeOperation) -> int:
    raise RuntimeError("repository updater launcher is not configured")


def launch_repository_updater(
    operation: UpgradeOperation,
    *,
    repository_root: Path,
    database_path: Path,
) -> int:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.service_update_runner",
            "--operation-id",
            operation.operation_id,
            "--repo",
            str(repository_root),
            "--db",
            str(database_path),
        ],
        cwd=repository_root,
        env=dict(os.environ),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process.pid


def render_repository_upgrade_mount() -> str:
    """Render the compact repository status control in the page header."""
    return """
<div id="repository-upgrade-banner" class="repository-upgrade-control" aria-live="polite">
  <span class="repository-upgrade-label">Repository</span>
  <span class="repository-upgrade-status" data-upgrade-status>Checking…</span>
  <span class="repository-upgrade-details" data-upgrade-details></span>
</div>
<script>
(function () {
  const banner = document.getElementById("repository-upgrade-banner");
  async function refresh() {
    try {
      const response = await fetch("/api/repository-upgrade/status", {headers: {"Accept": "application/json"}});
      if (!response.ok) return;
      const payload = await response.json();
      const snapshot = payload.snapshot || {};
      banner.querySelector("[data-upgrade-status]").textContent = snapshot.status || "idle";
      banner.querySelector("[data-upgrade-details]").textContent = snapshot.commits_behind ? `${snapshot.commits_behind} commit(s) available` : "";
    } catch (_) {}
  }
  refresh();
  window.setInterval(refresh, 60000);
}());
</script>
""".strip()
