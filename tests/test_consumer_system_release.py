from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from app.agent_cron.models import ScheduledTaskSkillRef
from app.store import AutoReplyStore
from app.consumer_system_release import publish_consumer_system_contracts
from app.repository_upgrade import GitRepository
from app.repository_updater import RepositoryUpdater, UpgradeFailed, UpgradeOperation


SKILLS = (
    "ceo-calendar-invite", "ceo-daily-report", "ceo-document-review",
    "ceo-mail-review", "ceo-message-triage", "ceo-weekly-report",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _skill(name: str, marker: bool, suffix: str) -> bytes:
    metadata = "metadata:\n  managed_by: ceo-agent-service\n" if marker else ""
    return (
        f"---\nname: {name}\ndescription: Test {name}\n{metadata}---\n\n{suffix}\n"
    ).encode()


def _fixture(tmp_path: Path):
    root = tmp_path / "checkout"
    skills = tmp_path / "installed"
    rules = tmp_path / "data/prompts/audit_rules.md"
    entries = []
    old = {}
    new = {}
    for name in (*SKILLS, "dingtalk-oa-approval"):
        rel = f"{name}/SKILL.md"
        previous = _skill(name, name in SKILLS, "old")
        replacement = _skill(name, name in SKILLS, "new")
        old[rel], new[rel] = previous, replacement
        target = skills / rel
        source = root / "ci/shared-skills" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        source.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(previous)
        source.write_bytes(replacement)
        entries.append({
            "source": f"ci/shared-skills/{rel}", "target": f"skills/{rel}",
            "old_sha256": _digest(previous), "new_sha256": _digest(replacement),
            **({"old_managed_sha256": _digest(previous)} if name in SKILLS else {}),
        })
    rel = "ceo-weekly-report/references/dingtalk-runbook.md"
    old[rel], new[rel] = b"old reference\n", b"new reference\n"
    target = skills / rel
    source = root / "ci/shared-skills" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(old[rel])
    source.write_bytes(new[rel])
    entries.append({
        "source": f"ci/shared-skills/{rel}", "target": f"skills/{rel}",
        "old_sha256": _digest(old[rel]), "new_sha256": _digest(new[rel]),
    })
    old["audit_rules"], new["audit_rules"] = b"old audit rules\n", b"new audit rules\n"
    rules.parent.mkdir(parents=True)
    rules.write_bytes(old["audit_rules"])
    source = root / "app/defaults/audit_rules.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(new["audit_rules"])
    entries.append({
        "source": "app/defaults/audit_rules.md", "target": "audit_rules",
        "old_sha256": _digest(old["audit_rules"]),
        "new_sha256": _digest(new["audit_rules"]),
    })
    manifest = root / "ci/consumer-system-contract-release.json"
    manifest.write_text(json.dumps({"version": 1, "files": entries}))
    return root, skills, rules, manifest, old, new


def _store_with_refs(
    db: Path, old: dict[str, bytes], *, pinned: tuple[str, ...] = (),
    prior_runtime_edit: tuple[str, ...] = (),
) -> tuple[AutoReplyStore, int, int]:
    store = AutoReplyStore(db)
    bindings = []
    refs = []
    for index, name in enumerate(SKILLS):
        skill = store.create_managed_skill(name, name)
        if name in prior_runtime_edit:
            store.create_managed_skill_revision(
                skill.id, _skill(name, True, "repository baseline").decode(),
                source="repository:skills",
            )
            store.create_managed_skill_revision(
                skill.id, old[f"{name}/SKILL.md"].decode(),
                source="runtime:agents-skills",
            )
        content = _skill(name, True, "pinned") if name in pinned else old[f"{name}/SKILL.md"]
        revision = store.create_managed_skill_revision(
            skill.id, content.decode(), source="repository:skills"
        )
        bindings.append({
            "skill_id": skill.id, "revision_id": revision.id,
            "enabled": True, "load_order": index, "purpose": "repository_import",
        })
        refs.append(ScheduledTaskSkillRef(
            skill_source="managed", skill_name=name, position=index,
            managed_skill_id=skill.id, managed_revision_id=revision.id,
        ))
    config = store.create_runtime_skill_config(bindings, expected_parent_id=None)
    store.record_runtime_skill_load(
        config.id, pid=12345,
        loaded={binding["skill_id"]: store.get_managed_skill_revision(
            binding["revision_id"]
        ).sha256 for binding in bindings},
    )
    task = store.create_scheduled_task(
        name="Release test", description="Release test", prompt="Use the Skills",
        runtime_id="codex_cli", cron_expression="0 0 * * * *",
        timezone_name="UTC", skill_refs=tuple(refs) + (
            ScheduledTaskSkillRef(
                skill_source="operation", skill_name="dingtalk-oa-approval",
                position=len(refs),
            ),
        ), enabled=False,
    )
    return store, config.id, task.id


def test_publish_then_rollback_restores_exact_files_refs_and_config(tmp_path: Path):
    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, task_id = _store_with_refs(db, old)
    untouched = skills / "ceo-weekly-report/references/operator-note.md"
    untouched.write_text("keep me")

    receipt = publish_consumer_system_contracts(
        root=root, database_path=db, operation_id="offline-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )

    assert (skills / "ceo-daily-report/SKILL.md").read_bytes() == new["ceo-daily-report/SKILL.md"]
    assert rules.read_bytes() == new["audit_rules"]
    assert untouched.read_text() == "keep me"
    updated = store.get_scheduled_task(task_id)
    assert updated.skill_refs[-1].skill_source == "operation"
    assert updated.skill_refs[-1].managed_revision_id is None
    assert all(
        store.get_managed_skill_revision(ref.managed_revision_id).sha256
        == _digest(new[f"{ref.skill_name}/SKILL.md"])
        for ref in updated.skill_refs if ref.skill_source == "managed"
    )
    new_config = store.get_pending_or_active_runtime_skill_config()
    assert new_config.id != old_config_id
    assert (db.parent / "release-receipts/offline-release/receipt.json").exists()

    receipt.rollback()

    assert (skills / "ceo-daily-report/SKILL.md").read_bytes() == old["ceo-daily-report/SKILL.md"]
    assert rules.read_bytes() == old["audit_rules"]
    assert untouched.read_text() == "keep me"
    assert store.get_scheduled_task(task_id).skill_refs == _store_refs(store, old_config_id)
    rollback_config = store.get_pending_or_active_runtime_skill_config()
    assert {b.revision_id for b in store.list_runtime_skill_bindings(rollback_config.id)} == {
        b.revision_id for b in store.list_runtime_skill_bindings(old_config_id)
    }


def test_loaded_readback_requires_the_new_config_and_exact_files(tmp_path: Path):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, _old_config_id, _task_id = _store_with_refs(db, old)
    receipt = publish_consumer_system_contracts(
        root=root, database_path=db, operation_id="loaded-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    with pytest.raises(ValueError, match="did not activate"):
        receipt.verify_loaded()
    config = store.get_pending_or_active_runtime_skill_config()
    store.record_runtime_skill_load(
        config.id, pid=os.getpid(),
        loaded={
            binding.skill_id: store.get_managed_skill_revision(binding.revision_id).sha256
            for binding in store.list_runtime_skill_bindings(config.id) if binding.enabled
        },
    )
    receipt.verify_loaded()
    receipt.finalize()
    retained = db.parent / "release-receipts/loaded-release"
    assert (retained / "old-0").exists()
    assert json.loads((retained / "receipt.json").read_text())["status"] == "verified"
    rules.write_text("operator edit")
    with pytest.raises(ValueError, match="released file changed"):
        receipt.verify_loaded()


def test_explicit_old_managed_digest_can_differ_from_installed_file(tmp_path: Path):
    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, task_id = _store_with_refs(db, old, pinned=("ceo-mail-review",))
    payload = json.loads(manifest.read_text())
    mail_entry = next(
        entry for entry in payload["files"]
        if entry["source"] == "ci/shared-skills/ceo-mail-review/SKILL.md"
    )
    assert mail_entry["old_sha256"] != _digest(_skill("ceo-mail-review", True, "pinned"))
    mail_entry["old_managed_sha256"] = _digest(_skill("ceo-mail-review", True, "pinned"))
    manifest.write_text(json.dumps(payload))

    receipt = publish_consumer_system_contracts(
        root=root, database_path=db, operation_id="pinned-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )

    task = store.get_scheduled_task(task_id)
    mail_ref = next(ref for ref in task.skill_refs if ref.skill_name == "ceo-mail-review")
    assert store.get_managed_skill_revision(mail_ref.managed_revision_id).sha256 == _digest(
        new["ceo-mail-review/SKILL.md"]
    )
    receipt.rollback()
    old_binding = next(
        binding for binding in store.list_runtime_skill_bindings(old_config_id)
        if store.get_managed_skill(binding.skill_id).name == "ceo-mail-review"
    )
    restored_ref = next(
        ref for ref in store.get_scheduled_task(task_id).skill_refs
        if ref.skill_name == "ceo-mail-review"
    )
    assert restored_ref.managed_revision_id == old_binding.revision_id


def test_manifest_requires_64_hex_expected_old_digest(tmp_path: Path):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    _store_with_refs(db, old)
    payload = json.loads(manifest.read_text())
    payload["files"][0]["old_sha256"] = payload["files"][0]["old_sha256"][:-1]
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="old SHA is invalid"):
        publish_consumer_system_contracts(
            root=root, database_path=db, operation_id="invalid-manifest",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )
    assert (skills / "ceo-calendar-invite/SKILL.md").read_bytes() == old["ceo-calendar-invite/SKILL.md"]


def test_concurrent_scheduled_edit_is_not_overwritten_by_publication_rollback(
    tmp_path: Path, monkeypatch,
):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, first_task_id = _store_with_refs(db, old)
    second_task = store.create_scheduled_task(
        name="Second task", description="Second task", prompt="Before",
        runtime_id="codex_cli", cron_expression="0 1 * * * *",
        timezone_name="UTC", skill_refs=(
            replace(
                next(ref for ref in store.get_scheduled_task(first_task_id).skill_refs
                     if ref.skill_name == "ceo-mail-review"),
                position=0, scheduled_task_id=0,
            ),
        ), enabled=False,
    )
    import app.consumer_system_release as release

    monkeypatch.setattr(release, "AutoReplyStore", lambda _path: store)
    original_update = store.update_scheduled_task
    raced = False

    def concurrent_edit(task_id: int, *, expected_version: int, **kwargs):
        nonlocal raced
        if task_id == second_task.id and not raced:
            raced = True
            original_update(
                task_id, expected_version=expected_version, prompt="Concurrent operator edit",
            )
        return original_update(task_id, expected_version=expected_version, **kwargs)

    monkeypatch.setattr(store, "update_scheduled_task", concurrent_edit)
    with pytest.raises(Exception, match="version conflict"):
        publish_consumer_system_contracts(
            root=root, database_path=db, operation_id="task-race",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )

    first = store.get_scheduled_task(first_task_id)
    second = store.get_scheduled_task(second_task.id)
    assert second.prompt == "Concurrent operator edit"
    assert first.skill_refs == _store_refs(store, old_config_id)
    assert (skills / "ceo-calendar-invite/SKILL.md").read_bytes() == old["ceo-calendar-invite/SKILL.md"]
    assert rules.read_bytes() == old["audit_rules"]


def test_failed_health_restores_real_release_files_and_refs_before_old_restart(
    tmp_path: Path,
):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, task_id = _store_with_refs(db, old)

    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        ).stdout.strip()

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "init", "--initial-branch=main", str(root))
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "version").write_text("old\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "old")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    second = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(second))
    git(second, "config", "user.name", "Test")
    git(second, "config", "user.email", "test@example.com")
    (second / "version").write_text("new\n")
    git(second, "add", "version")
    git(second, "commit", "-m", "new")
    git(second, "push", "origin", "main")
    repo = GitRepository(root)
    repo.fetch("origin")
    old_commit = repo.resolve_ref("refs/heads/main")
    new_commit = repo.resolve_ref("refs/remotes/origin/main")
    operation = UpgradeOperation(
        operation_id="real-rollback",
        expected_fingerprint=repo.fingerprint("main", old_commit, new_commit, []),
        original_commit=old_commit, target_commit=new_commit,
    )
    events: list[str] = []

    class State:
        values: dict[str, str] = {}

        def set_service_state(self, key: str, value: str) -> None:
            self.values[key] = value

        def get_service_state(self, key: str) -> str | None:
            return self.values.get(key)

    updater = RepositoryUpdater(
        root, State(), database_path=db,
        stop=lambda: events.append("stop"),
        restart=lambda: events.append("start"),
        health=lambda: events.append("health") or events.count("health") > 1,
        publication=lambda: publish_consumer_system_contracts(
            root=root, database_path=db, operation_id=operation.operation_id,
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        ),
    )

    with pytest.raises(UpgradeFailed):
        updater.execute(operation)

    assert events == ["stop", "start", "health", "stop", "start", "health"]
    assert (root / "version").read_text() == "old\n"
    assert all((skills / rel).read_bytes() == data for rel, data in old.items() if rel != "audit_rules")
    assert rules.read_bytes() == old["audit_rules"]
    assert store.get_scheduled_task(task_id).skill_refs == _store_refs(store, old_config_id)
    current_config = store.get_pending_or_active_runtime_skill_config()
    assert {b.revision_id for b in store.list_runtime_skill_bindings(current_config.id)} == {
        b.revision_id for b in store.list_runtime_skill_bindings(old_config_id)
    }


def test_post_rollback_startup_preserves_old_binding_with_runtime_edit_file(
    tmp_path: Path, monkeypatch,
):
    """The installed old file may have a different SHA than the active binding."""
    import app.managed_skills as managed_skills

    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, _task_id = _store_with_refs(
        db, old, pinned=("ceo-mail-review",),
        prior_runtime_edit=("ceo-mail-review",),
    )
    payload = json.loads(manifest.read_text())
    mail_entry = next(
        entry for entry in payload["files"]
        if entry["source"] == "ci/shared-skills/ceo-mail-review/SKILL.md"
    )
    mail_entry["old_managed_sha256"] = _digest(_skill("ceo-mail-review", True, "pinned"))
    manifest.write_text(json.dumps(payload))
    mail_skill = store.get_managed_skill_by_name("ceo-mail-review")
    prior_revisions = store.list_managed_skill_revisions(mail_skill.id)
    assert [revision.source for revision in prior_revisions] == [
        "repository:skills", "runtime:agents-skills", "repository:skills",
    ]

    receipt = publish_consumer_system_contracts(
        root=root, database_path=db, operation_id="rollback-import",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.rollback()
    assert (skills / "ceo-mail-review/SKILL.md").read_bytes() == old["ceo-mail-review/SKILL.md"]
    assert store.get_pending_or_active_runtime_skill_config().id != old_config_id

    monkeypatch.setattr(
        managed_skills, "_repository_managed_skills",
        lambda: (("ceo-mail-review", (skills / "ceo-mail-review/SKILL.md").read_text()),),
    )
    snapshot = managed_skills.resolve_pending_runtime_skills(store, pid=os.getpid())
    managed_skills.capture_runtime_skill_edits(store, skills_root=skills)

    binding = next(
        binding for binding in store.list_runtime_skill_bindings(snapshot.config_id)
        if binding.skill_id == mail_skill.id
    )
    assert store.get_managed_skill_revision(binding.revision_id).sha256 == _digest(
        _skill("ceo-mail-review", True, "pinned")
    )
    matching_old_file = [
        revision for revision in store.list_managed_skill_revisions(mail_skill.id)
        if revision.sha256 == _digest(old["ceo-mail-review/SKILL.md"])
    ]
    assert {revision.source for revision in matching_old_file} == {
        "runtime:agents-skills", "repository:skills",
    }
    assert all(revision.content == matching_old_file[0].content for revision in matching_old_file)


def _store_refs(store: AutoReplyStore, config_id: int):
    revisions = {b.skill_id: b.revision_id for b in store.list_runtime_skill_bindings(config_id)}
    task = next(task for task in store.list_scheduled_tasks() if task.name == "Release test")
    return tuple(
        replace(ref, managed_revision_id=revisions[ref.managed_skill_id])
        if ref.skill_source == "managed" else ref
        for ref in task.skill_refs
    )


def test_partial_file_publish_failure_restores_every_changed_file(tmp_path: Path, monkeypatch):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    _store_with_refs(db, old)
    import app.consumer_system_release as release

    original = release._atomic_replace
    calls = 0

    def fail_second(path: Path, data: bytes, mode: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic file publish failure")
        return original(path, data, mode)

    monkeypatch.setattr(release, "_atomic_replace", fail_second)
    with pytest.raises(OSError, match="synthetic"):
        publish_consumer_system_contracts(
            root=root, database_path=db, operation_id="partial-release",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )
    assert all((skills / rel).read_bytes() == data for rel, data in old.items() if rel != "audit_rules")
    assert rules.read_bytes() == old["audit_rules"]
