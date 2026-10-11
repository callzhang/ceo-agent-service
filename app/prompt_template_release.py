"""Publish approved Developer/User defaults in the stopped deploy window."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat

from app.consumer_system_release import _atomic_replace, SHA256_HEX


DEFAULT_MANIFEST = Path("ci/prompt-template-release.json")
TEMPLATE_SOURCES = {
    "developer_prompt": "app/defaults/developer_prompt.md",
    "user_prompt": "app/defaults/user_prompt.md",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class _FilePlan:
    source: str
    target: Path
    old_sha256: str
    new_sha256: str
    old_bytes: bytes
    new_bytes: bytes
    mode: int


class PromptTemplatePublication:
    def __init__(self, plans: tuple[_FilePlan, ...], receipt_dir: Path) -> None:
        self.plans = plans
        self.receipt_dir = receipt_dir
        self.status = "prepared"
        self._written: list[int] = []

    def _save_receipt(self) -> None:
        payload = {
            "release_kind": "prompt_templates_v1",
            "status": self.status,
            "files": [
                {
                    "source": plan.source,
                    "target": str(plan.target),
                    "old_sha256": plan.old_sha256,
                    "new_sha256": plan.new_sha256,
                    "mode": plan.mode,
                    "backup": f"old-{index}",
                }
                for index, plan in enumerate(self.plans)
            ],
        }
        path = self.receipt_dir / "receipt.json"
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("Prompt template release receipt readback mismatch")

    def _original(self, index: int) -> bytes:
        data = (self.receipt_dir / f"old-{index}").read_bytes()
        if _sha256(data) != self.plans[index].old_sha256:
            raise ValueError(f"Prompt template release backup SHA mismatch: {self.plans[index].source}")
        return data

    def publish(self) -> None:
        self.receipt_dir.mkdir(parents=True, exist_ok=False)
        for index, plan in enumerate(self.plans):
            (self.receipt_dir / f"old-{index}").write_bytes(plan.old_bytes)
            self._original(index)
        self._save_receipt()
        for index, plan in enumerate(self.plans):
            if plan.old_sha256 == plan.new_sha256:
                continue
            # Register before replacement so a failure after rename still restores it.
            self._written.append(index)
            _atomic_replace(plan.target, plan.new_bytes, plan.mode)

    def verify_loaded(self) -> None:
        # These settings are file-backed and read on each invocation. This
        # confirms their saved content, not a model invocation or business result.
        for plan in self.plans:
            if _sha256(plan.target.read_bytes()) != plan.new_sha256:
                raise ValueError(f"Prompt template released file changed: {plan.source}")
        for index in range(len(self.plans)):
            self._original(index)

    def rollback(self) -> None:
        for index in reversed(self._written):
            plan = self.plans[index]
            _atomic_replace(plan.target, self._original(index), plan.mode)
            if _sha256(plan.target.read_bytes()) != plan.old_sha256:
                raise ValueError(f"Prompt template rollback readback mismatch: {plan.source}")
        self._written.clear()
        self.status = "rolled_back"
        if self.receipt_dir.exists():
            self._save_receipt()

    def finalize(self) -> None:
        self.status = "verified"
        self._save_receipt()


def prepare_prompt_templates(
    *, root: Path, database_path: Path, operation_id: str,
    developer_prompt_path: Path, user_prompt_path: Path,
    manifest_path: Path | None = None,
) -> PromptTemplatePublication:
    """Snapshot the exact approved defaults without modifying configured files."""
    root = Path(root)
    manifest = json.loads(Path(manifest_path or root / DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(entries, list):
        raise ValueError("Prompt template release manifest is invalid")
    if len(entries) != len(TEMPLATE_SOURCES) or {item.get("source") for item in entries} != set(TEMPLATE_SOURCES.values()):
        raise ValueError("Prompt template release manifest must name the exact Developer/User defaults")
    targets = {
        "developer_prompt": Path(developer_prompt_path),
        "user_prompt": Path(user_prompt_path),
    }
    sources = {(root / source).resolve() for source in TEMPLATE_SOURCES.values()}
    if len({path.resolve() for path in targets.values()}) != len(targets):
        raise ValueError("Prompt template release targets must be separate files")
    plans = []
    for item in entries:
        source_name = item["source"]
        name = next(name for name, source in TEMPLATE_SOURCES.items() if source == source_name)
        if item.get("target") != name:
            raise ValueError(f"Prompt template release target mismatch: {source_name}")
        source, target = root / source_name, targets[name]
        if source.is_symlink() or target.is_symlink() or not source.is_file() or not target.is_file():
            raise ValueError(f"Prompt template release file is missing or linked: {source_name}")
        if target.resolve() in sources:
            raise ValueError(f"Prompt template release cannot change a source default: {source_name}")
        old_sha, new_sha = item.get("old_sha256"), item.get("new_sha256")
        if any(not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha) for sha in (old_sha, new_sha)):
            raise ValueError(f"Prompt template release SHA is invalid: {source_name}")
        old_bytes, new_bytes = target.read_bytes(), source.read_bytes()
        if _sha256(new_bytes) != new_sha:
            raise ValueError(f"Prompt template release source SHA mismatch: {source_name}")
        actual_old = _sha256(old_bytes)
        if actual_old not in (old_sha, new_sha):
            raise ValueError(f"Prompt template migration needed for customized {name}: {target}; nothing was changed")
        plans.append(_FilePlan(
            source_name, target, actual_old, new_sha, old_bytes, new_bytes,
            stat.S_IMODE(target.stat().st_mode),
        ))
    return PromptTemplatePublication(
        tuple(plans), Path(database_path).parent / "release-receipts" / operation_id,
    )
