from __future__ import annotations

from datetime import datetime
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
from app.repository_updater import UpgradeOperation, persist_operation


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
        state = service_factory().load_state()
        return {
            "snapshot": _snapshot_payload(state.snapshot),
            "operation": state.operation.model_dump(mode="json")
            if state.operation is not None
            else None,
        }

    @app.post("/api/repository-upgrade/check")
    def check() -> dict[str, object]:
        snapshot = service_factory().check()
        return {"snapshot": _snapshot_payload(snapshot)}

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
        pid = launcher(operation)
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
            "app.repository_updater",
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
    """Render the self-contained History banner mount point."""
    return """
<section id="repository-upgrade-banner" class="card" aria-live="polite">
  <div class="card-head"><h2>Repository upgrade</h2><span data-upgrade-status>Checking…</span></div>
  <div data-upgrade-details></div>
</section>
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
