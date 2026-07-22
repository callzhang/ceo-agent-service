from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanaryConnector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    test_target_ref: str = ""
    allowlist: tuple[str, ...] = ()
    canary_command: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_enabled_connector(self) -> CanaryConnector:
        if not self.enabled:
            return self
        if not self.test_target_ref or self.test_target_ref not in self.allowlist:
            raise ValueError("enabled connector test target must be explicitly allowlisted")
        if not self.canary_command:
            raise ValueError("enabled connector requires a canary command")
        return self


class SandboxProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    live_enabled: bool = False
    connectors: dict[str, CanaryConnector]

    @model_validator(mode="after")
    def validate_live_gate(self) -> SandboxProfile:
        if not any(connector.enabled for connector in self.connectors.values()):
            raise ValueError("sandbox must enable at least one connector canary")
        if not self.live_enabled:
            raise ValueError("sandbox live_enabled must be explicit")
        return self

    @classmethod
    def load(cls, path: Path) -> SandboxProfile:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class CanaryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    connector: str
    status: Literal["passed", "failed"]
    reason: str = ""


def run_sandbox_canaries(
    profile: SandboxProfile, *, timeout_seconds: int = 120
) -> tuple[CanaryResult, ...]:
    results: list[CanaryResult] = []
    for name, connector in profile.connectors.items():
        if not connector.enabled:
            continue
        command = [
            argument.replace("{target_ref}", connector.test_target_ref)
            for argument in connector.canary_command
        ]
        environment = dict(os.environ)
        environment["CEO_QUALITY_CANARY"] = "1"
        environment["CEO_QUALITY_CANARY_TARGET_REF"] = connector.test_target_ref
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(
                CanaryResult(connector=name, status="failed", reason=type(exc).__name__)
            )
            continue
        results.append(
            CanaryResult(
                connector=name,
                status="passed" if completed.returncode == 0 else "failed",
                reason="" if completed.returncode == 0 else "canary_nonzero_exit",
            )
        )
    return tuple(results)


class LocalReleaseManager:
    def __init__(self, *, repo: Path, release_root: Path, keep: int = 3):
        self.repo = repo.resolve()
        self.release_root = release_root.resolve()
        self.releases = self.release_root / "releases"
        self.current = self.release_root / "current"
        self.keep = keep

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def build(self, commit: str) -> Path:
        exact_commit = self._git("rev-parse", "--verify", f"{commit}^{{commit}}")
        target = self.releases / exact_commit
        marker = target / ".ceo-release.json"
        if target.exists():
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("commit") != exact_commit:
                raise RuntimeError("existing release commit marker does not match")
            return target
        self.releases.mkdir(parents=True, exist_ok=True)
        temporary = self.releases / f".building-{exact_commit}-{uuid4().hex}"
        temporary.mkdir()
        try:
            archive = subprocess.run(
                ["git", "archive", "--format=tar", exact_commit],
                cwd=self.repo,
                check=True,
                capture_output=True,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                bundle.extractall(temporary, filter="data")
            if (temporary / "uv.lock").is_file() and (temporary / "pyproject.toml").is_file():
                subprocess.run(
                    ["uv", "sync", "--frozen", "--python", "3.12", "--extra", "reader-build"],
                    cwd=temporary,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            (temporary / ".ceo-release.json").write_text(
                json.dumps({"commit": exact_commit}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.rename(target)
        except Exception:
            if temporary.exists():
                for path in sorted(temporary.rglob("*"), reverse=True):
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                temporary.rmdir()
            raise
        return target

    def activate(self, release: Path) -> Path | None:
        release = release.resolve()
        if release.parent != self.releases or not (release / ".ceo-release.json").is_file():
            raise ValueError("release must be an immutable managed release")
        previous = self.current.resolve() if self.current.is_symlink() else None
        temporary = self.release_root / f".current-{uuid4().hex}"
        self.release_root.mkdir(parents=True, exist_ok=True)
        temporary.symlink_to(release)
        os.replace(temporary, self.current)
        self.prune()
        return previous

    def rollback(self, previous: Path | None) -> None:
        if previous is None:
            if self.current.is_symlink():
                self.current.unlink()
            return
        self.activate(previous)

    def prune(self) -> None:
        current = self.current.resolve() if self.current.is_symlink() else None
        releases = sorted(
            (
                path
                for path in self.releases.iterdir()
                if path.is_dir() and not path.name.startswith(".building-")
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removable = [path for path in releases if path.resolve() != current][
            max(self.keep - 1, 0) :
        ]
        for stale in removable:
            for path in sorted(stale.rglob("*"), reverse=True):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            stale.rmdir()
