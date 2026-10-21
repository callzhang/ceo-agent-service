from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from app.agent_cron.models import ScheduledTaskSkillRef
from app.store import AutoReplyStore
from app.consumer_system_release import prepare_consumer_system_contracts
from app.repository_upgrade import GitRepository
from app.repository_updater import (
    RepositoryUpdater,
    UpgradeFailed,
    UpgradeOperation,
    UPGRADE_OPERATION_STATE_KEY,
)


SKILLS = (
    "ceo-calendar-invite",
    "ceo-daily-report",
    "ceo-document-review",
    "ceo-mail-review",
    "ceo-message-triage",
    "ceo-weekly-report",
    "ceo-weekly-okr-report",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_repository_consumer_release_manifest_tracks_oa_skill_digest():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entry = next(
        item for item in manifest["files"]
        if item["source"] == "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
    )

    # The old digest is a release-time precondition. After a successful
    # deployment the installed file has the new digest, so do not compare the
    # old digest with mutable machine state in this repository-level test.
    assert len(entry["old_sha256"]) == 64
    assert all(char in "0123456789abcdef" for char in entry["old_sha256"])
    assert entry["new_sha256"] == _digest((root / entry["source"]).read_bytes())
    assert "old_managed_sha256" not in entry
    audit_source = "app/defaults/audit_rules.md"
    audit_entry = next(item for item in manifest["files"] if item["source"] == audit_source)
    assert audit_entry["old_sha256"] == "5dcfd33f48d23268296da5d89346eaf3a76ffec665e5d9a353c33654a5de8237"


def test_release_manifest_pins_current_finance_card_and_active_managed_skill_hashes():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entries = {item["source"]: item for item in manifest["files"]}
    finance_source = "ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md"
    finance_skill_source = "ci/shared-skills/stardust-oa-finance-review/SKILL.md"
    installed_finance_card = Path.home().joinpath(
        ".agents/skills/stardust-oa-finance-review/references/finance-rule-cards.md"
    )
    installed_finance_skill = Path.home().joinpath(
        ".agents/skills/stardust-oa-finance-review/SKILL.md"
    )

    # Already-published source bytes are a valid no-op release entry: old_sha256
    # must match today's target, and new_sha256 must match the committed source.
    assert _digest(installed_finance_card.read_bytes()) in {
        entries[finance_source]["old_sha256"], entries[finance_source]["new_sha256"],
    }
    assert entries[finance_source]["new_sha256"] == _digest((root / finance_source).read_bytes())
    installed_digest = _digest(installed_finance_skill.read_bytes())
    assert installed_digest in {
        entries[finance_skill_source]["old_sha256"],
        entries[finance_skill_source]["new_sha256"],
    }
    assert entries[finance_skill_source]["new_sha256"] == _digest(
        (root / finance_skill_source).read_bytes()
    )
    assert {
        source: entries[source]["old_managed_sha256"]
        for source in (
            "ci/shared-skills/ceo-calendar-invite/SKILL.md",
            "ci/shared-skills/ceo-message-triage/SKILL.md",
            "ci/shared-skills/ceo-weekly-report/SKILL.md",
        )
    } == {
        "ci/shared-skills/ceo-calendar-invite/SKILL.md": "8fa06610ac6f272b3d9121167dd71da211ab7659d43e0f60f4f40f374a68137f",
        "ci/shared-skills/ceo-message-triage/SKILL.md": "35070baeda959d85b5e1188bb89e003778331880252abf7143e889d476b11d36",
        "ci/shared-skills/ceo-weekly-report/SKILL.md": "ececf8bf22e740573b68c0a4e1f1d6cd7db166a2fb2c6991737cce03890d22a9",
    }


def test_finance_rule_card_counts_only_valid_applicant_resolvable_returns():
    root = Path(__file__).resolve().parents[1]
    card = (
        root / "ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md"
    ).read_text(encoding="utf-8")

    assert "Prior return count is history, not an action rule" in card
    assert (
        "Count only returns that independently established the same material gap from readable, "
        "source-verified evidence and gave the applicant a fair chance to resolve it."
    ) in card
    assert "不能把审核方无法读取归为申请人未提交" in card
    assert "不等于客户验收，也不代表客户未验收" in card


def test_repository_consumer_release_manifest_tracks_message_triage_skill_digest():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    entry = next(
        item for item in manifest["files"]
        if item["source"] == "ci/shared-skills/ceo-message-triage/SKILL.md"
    )

    assert entry["new_sha256"] == _digest((root / entry["source"]).read_bytes())


def test_repository_consumer_release_manifest_tracks_every_source_digest():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )

    assert all(
        entry["new_sha256"] == _digest((root / entry["source"]).read_bytes())
        for entry in manifest["files"]
    )


def test_repository_consumer_release_manifest_includes_finance_review_assets():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    sources = {entry["source"] for entry in manifest["files"]}

    assert "ci/shared-skills/stardust-oa-finance-review/SKILL.md" in sources
    assert "ci/shared-skills/stardust-oa-finance-review/references/finance-rule-cards.md" in sources


def test_repository_consumer_release_manifest_includes_people_review_skill():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    source = "ci/shared-skills/stardust-oa-people-review/SKILL.md"
    entry = next(item for item in manifest["files"] if item["source"] == source)

    assert entry["target"] == "skills/stardust-oa-people-review/SKILL.md"
    assert entry["new_sha256"] == _digest((root / source).read_bytes())


def test_repository_consumer_release_manifest_includes_current_attendance_review_skill():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    source = "ci/shared-skills/stardust-oa-attendance-travel-review/SKILL.md"
    entry = next(item for item in manifest["files"] if item["source"] == source)

    assert entry["target"] == "skills/stardust-oa-attendance-travel-review/SKILL.md"
    assert entry["old_sha256"] == "edb3fd74d5a26512fff9c225b0cdf694412e611a5efdffdea0f392d9c37b8d2e"
    assert entry["new_sha256"] == _digest((root / source).read_bytes())


@pytest.mark.parametrize("script", ("report_contract.py", "validate_run.py"))
def test_repository_consumer_release_manifest_tracks_weekly_report_scripts(script):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "ci/consumer-system-contract-release.json").read_text(encoding="utf-8")
    )
    source = f"ci/shared-skills/ceo-weekly-report/scripts/{script}"
    entry = next(item for item in manifest["files"] if item["source"] == source)

    assert entry["target"] == f"skills/ceo-weekly-report/scripts/{script}"
    assert entry["new_sha256"] == _digest((root / source).read_bytes())


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
    for name in (
        *SKILLS,
        "dingtalk-oa-approval",
        "stardust-oa-finance-review",
        "stardust-oa-people-review",
        "stardust-oa-attendance-travel-review",
        "ceo-wechat",
    ):
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
        entries.append(
            {
                "source": f"ci/shared-skills/{rel}",
                "target": f"skills/{rel}",
                "old_sha256": _digest(previous),
                "new_sha256": _digest(replacement),
                **({"old_managed_sha256": _digest(previous)} if name in SKILLS else {}),
            }
        )
    rel = "ceo-weekly-report/references/dingtalk-runbook.md"
    old[rel], new[rel] = b"old reference\n", b"new reference\n"
    target = skills / rel
    source = root / "ci/shared-skills" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(old[rel])
    source.write_bytes(new[rel])
    entries.append(
        {
            "source": f"ci/shared-skills/{rel}",
            "target": f"skills/{rel}",
            "old_sha256": _digest(old[rel]),
            "new_sha256": _digest(new[rel]),
        }
    )
    for rel in (
        "ceo-weekly-report/scripts/report_contract.py",
        "ceo-weekly-report/scripts/validate_run.py",
    ):
        old[rel] = f"old {Path(rel).name}\n".encode()
        new[rel] = f"new {Path(rel).name}\n".encode()
        target = skills / rel
        source = root / "ci/shared-skills" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        source.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(old[rel])
        source.write_bytes(new[rel])
        entries.append(
            {
                "source": f"ci/shared-skills/{rel}",
                "target": f"skills/{rel}",
                "old_sha256": _digest(old[rel]),
                "new_sha256": _digest(new[rel]),
            }
        )
    rel = "stardust-oa-finance-review/references/finance-rule-cards.md"
    old[rel], new[rel] = b"old finance rules\n", b"new finance rules\n"
    target = skills / rel
    source = root / "ci/shared-skills" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(old[rel])
    source.write_bytes(new[rel])
    entries.append(
        {
            "source": f"ci/shared-skills/{rel}",
            "target": f"skills/{rel}",
            "old_sha256": _digest(old[rel]),
            "new_sha256": _digest(new[rel]),
        }
    )
    old["audit_rules"], new["audit_rules"] = b"old audit rules\n", b"new audit rules\n"
    rules.parent.mkdir(parents=True)
    rules.write_bytes(old["audit_rules"])
    source = root / "app/defaults/audit_rules.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(new["audit_rules"])
    entries.append(
        {
            "source": "app/defaults/audit_rules.md",
            "target": "audit_rules",
            "old_sha256": _digest(old["audit_rules"]),
            "new_sha256": _digest(new["audit_rules"]),
        }
    )
    from app.consumer_system_release import EXPECTED_SOURCES

    present = {entry["source"] for entry in entries}
    for source_name in sorted(EXPECTED_SOURCES - present):
        rel = source_name.removeprefix("ci/shared-skills/")
        old[rel], new[rel] = b"old script\n", b"new script\n"
        source = root / source_name
        target = skills / rel
        source.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(new[rel])
        target.write_bytes(old[rel])
        entries.append(
            {
                "source": source_name,
                "target": "skills/" + rel,
                "old_sha256": _digest(old[rel]),
                "new_sha256": _digest(new[rel]),
            }
        )
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

    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="offline-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()

    assert (skills / "ceo-daily-report/SKILL.md").read_bytes() == new["ceo-daily-report/SKILL.md"]
    assert rules.read_bytes() == new["audit_rules"]
    for rel in (
        "ceo-weekly-report/scripts/report_contract.py",
        "ceo-weekly-report/scripts/validate_run.py",
    ):
        assert (skills / rel).read_bytes() == new[rel]
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
    for rel in (
        "ceo-weekly-report/scripts/report_contract.py",
        "ceo-weekly-report/scripts/validate_run.py",
    ):
        assert (skills / rel).read_bytes() == old[rel]
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
    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="loaded-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()
    with pytest.raises(ValueError, match="did not activate"):
        receipt.verify_loaded(wait_timeout_seconds=0)
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


def test_loaded_readback_accepts_exact_active_descendant_of_published_config(tmp_path: Path):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, _old_config_id, _task_id = _store_with_refs(db, old)
    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="descendant-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()
    published = store.get_pending_or_active_runtime_skill_config()
    bindings = store.list_runtime_skill_bindings(published.id)
    descendant = store.create_runtime_skill_config(
        [
            {
                "skill_id": item.skill_id,
                "revision_id": item.revision_id,
                "enabled": item.enabled,
                "load_order": item.load_order,
                "purpose": item.purpose,
            }
            for item in bindings
        ],
        expected_parent_id=published.id,
    )
    store.record_runtime_skill_load(
        descendant.id, pid=os.getpid(),
        loaded={
            binding.skill_id: store.get_managed_skill_revision(binding.revision_id).sha256
            for binding in store.list_runtime_skill_bindings(descendant.id)
            if binding.enabled
        },
    )

    receipt.verify_loaded()


@pytest.mark.parametrize("mutation", ("remove_managed", "replace_source_and_name"))
def test_loaded_readback_rejects_missing_or_replaced_scheduled_refs(
    tmp_path: Path, mutation: str,
):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, _old_config_id, task_id = _store_with_refs(db, old)
    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id=f"readback-{mutation}",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()
    config = store.get_pending_or_active_runtime_skill_config()
    store.record_runtime_skill_load(
        config.id, pid=os.getpid(),
        loaded={
            binding.skill_id: store.get_managed_skill_revision(binding.revision_id).sha256
            for binding in store.list_runtime_skill_bindings(config.id) if binding.enabled
        },
    )
    receipt.verify_loaded()
    task = store.get_scheduled_task(task_id)
    if mutation == "remove_managed":
        changed = tuple(
            replace(ref, position=index)
            for index, ref in enumerate(
                ref for ref in task.skill_refs if ref.skill_source == "operation"
            )
        )
    else:
        changed = tuple(
            replace(
                ref, skill_source="operation", skill_name="dingtalk-chat",
                managed_skill_id=None, managed_revision_id=None,
            ) if ref.skill_name == "ceo-mail-review" else ref
            for ref in task.skill_refs
        )
    store.update_scheduled_task(
        task_id, expected_version=task.version, skill_refs=changed,
    )

    with pytest.raises(ValueError, match="scheduled Skill reference changed"):
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

    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="pinned-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()

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
        prepare_consumer_system_contracts(
            root=root, database_path=db, operation_id="invalid-manifest",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )
    assert (skills / "ceo-calendar-invite/SKILL.md").read_bytes() == old["ceo-calendar-invite/SKILL.md"]


def test_stale_installed_digest_fails_before_any_release_write(tmp_path: Path):
    root, skills, rules, manifest, old, _new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    _store_with_refs(db, old)
    payload = json.loads(manifest.read_text())
    audit_rules = next(
        item for item in payload["files"]
        if item["source"] == "app/defaults/audit_rules.md"
    )
    audit_rules["old_sha256"] = _digest(b"stale installed audit rules\n")
    manifest.write_text(json.dumps(payload))

    with pytest.raises(
        ValueError, match="SHA mismatch: app/defaults/audit_rules.md"
    ):
        prepare_consumer_system_contracts(
            root=root, database_path=db, operation_id="stale-runtime-digest",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )

    assert rules.read_bytes() == old["audit_rules"]
    assert (skills / "ceo-calendar-invite/SKILL.md").read_bytes() == old[
        "ceo-calendar-invite/SKILL.md"
    ]


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
    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="task-race",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    with pytest.raises(Exception, match="version conflict"):
        receipt.publish()
    receipt.rollback()

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
        publication=lambda: prepare_consumer_system_contracts(
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

    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="rollback-import",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    receipt.publish()
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
    receipt = prepare_consumer_system_contracts(
        root=root, database_path=db, operation_id="partial-release",
        skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
    )
    with pytest.raises(OSError, match="synthetic"):
        receipt.publish()
    receipt.rollback()
    assert all((skills / rel).read_bytes() == data for rel, data in old.items() if rel != "audit_rules")
    assert rules.read_bytes() == old["audit_rules"]


def test_partial_publication_and_rollback_failure_never_start_old_service(tmp_path, monkeypatch):
    from tests.test_repository_updater_publication import _repo, _State
    import app.consumer_system_release as release

    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, _, first_task_id = _store_with_refs(db, old)
    first_ref = store.get_scheduled_task(first_task_id).skill_refs[0]
    second = store.create_scheduled_task(
        name="Second", description="Second", prompt="Before",
        runtime_id="codex_cli", cron_expression="0 1 * * * *", timezone_name="UTC",
        skill_refs=(replace(first_ref, position=0, scheduled_task_id=0),), enabled=False,
    )
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    local, operation = _repo(repository_path)
    events = []
    state = _State()
    monkeypatch.setattr(release, "AutoReplyStore", lambda _path: store)
    original_update = store.update_scheduled_task

    def fail_with_concurrent_edit(task_id, *, expected_version, **kwargs):
        if task_id == second.id:
            current = store.get_scheduled_task(first_task_id)
            original_update(first_task_id, expected_version=current.version, prompt="Concurrent edit")
            raise OSError("publication failed after first task update")
        return original_update(task_id, expected_version=expected_version, **kwargs)

    monkeypatch.setattr(store, "update_scheduled_task", fail_with_concurrent_edit)
    updater = RepositoryUpdater(
        local, state, database_path=db,
        stop=lambda: events.append("stop"), restart=lambda: events.append("start"),
        health=lambda: events.append("health") or True,
        publication=lambda: prepare_consumer_system_contracts(
            root=root, database_path=db, operation_id=operation.operation_id,
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        ),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events == ["stop"]
    assert (local / "version").read_text() == "new\n"
    assert store.get_scheduled_task(first_task_id).prompt == "Concurrent edit"
    assert (skills / "ceo-calendar-invite/SKILL.md").read_bytes() == new["ceo-calendar-invite/SKILL.md"]
    assert json.loads(state.values[UPGRADE_OPERATION_STATE_KEY])["status"] == "needs_manual"
    assert (db.parent / "release-receipts" / operation.operation_id / "receipt.json").exists()


def test_release_publishes_new_skill_asset_and_rollback_removes_only_that_asset(
    tmp_path,
):
    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    _store_with_refs(db, old)
    rel = "ceo-daily-report/scripts/window.py"
    (skills / rel).unlink()
    payload = json.loads(manifest.read_text())
    next(x for x in payload["files"] if x["source"] == "ci/shared-skills/" + rel)[
        "old_sha256"
    ] = None
    manifest.write_text(json.dumps(payload))
    publication = prepare_consumer_system_contracts(
        root=root,
        database_path=db,
        operation_id="new-asset",
        skills_root=skills,
        audit_rules_path=rules,
        manifest_path=manifest,
    )
    publication.publish()
    assert (skills / rel).read_bytes() == new[rel]
    publication.rollback()
    assert not (skills / rel).exists()
    assert (skills / "ceo-daily-report/SKILL.md").read_bytes() == old[
        "ceo-daily-report/SKILL.md"
    ]


def test_release_migrates_okr_definition_before_restart_and_rolls_back_exact_config(
    tmp_path, monkeypatch
):
    from app.agent_cron import seeds

    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, old_config_id, _ = _store_with_refs(db, old)
    task = store.create_scheduled_task(
        migration_key=seeds.WEEKLY_OKR_MIGRATION_KEY,
        name="Custom OKR",
        description="custom description",
        command="weekly-okr-report",
        prompt="",
        cron_expression="0 10 18 * * 0",
        timezone_name="UTC",
        enabled=False,
    )
    monkeypatch.setattr(seeds, "_agent_runtime", lambda options: "codex_oauth")

    def selected(options, *, managed=(), operation=()):
        result = []
        for name in managed:
            skill = store.get_managed_skill_by_name(name)
            revision = store.list_managed_skill_revisions(skill.id)[-1]
            result.append(
                ScheduledTaskSkillRef(
                    skill_source="managed",
                    skill_name=name,
                    managed_skill_id=skill.id,
                    managed_revision_id=revision.id,
                    position=len(result),
                )
            )
        return tuple(result)

    monkeypatch.setattr(seeds, "_consumer_skill_refs", selected)
    publication = prepare_consumer_system_contracts(
        root=root,
        database_path=db,
        operation_id="definition-migration",
        skills_root=skills,
        audit_rules_path=rules,
        manifest_path=manifest,
    )
    publication.publish()
    migrated = store.get_scheduled_task(task.id)
    assert migrated.command == "" and migrated.runtime_id == "codex_oauth"
    assert (
        migrated.cron_expression == task.cron_expression
        and migrated.timezone_name == task.timezone_name
    )
    assert (
        migrated.name == task.name
        and migrated.description == task.description
        and not migrated.enabled
    )
    assert publication.task_definitions[task.id][1].version == migrated.version
    default = next(
        x
        for x in seeds.SCHEDULED_TASK_DEFAULTS
        if x.migration_key == task.migration_key
    )
    again = seeds._seed_task(default, store=store, options=None, now=None)
    assert again.version == migrated.version
    publication.rollback()
    restored = store.get_scheduled_task(task.id)
    assert restored.command == task.command and restored.runtime_id == task.runtime_id
    assert restored.prompt == task.prompt and restored.skill_refs == task.skill_refs
    assert (
        restored.cron_expression == task.cron_expression
        and restored.enabled == task.enabled
    )
    assert store.get_pending_or_active_runtime_skill_config().parent_id != old_config_id


def test_release_new_binding_preserves_sparse_existing_load_orders(tmp_path):
    root, skills, rules, manifest, old, new = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, config_id, task_id = _store_with_refs(db, old)
    name = "ceo-weekly-okr-report"
    skill = store.get_managed_skill_by_name(name)
    bindings = [b for b in store.list_runtime_skill_bindings(config_id) if b.skill_id != skill.id]
    sparse = store.create_runtime_skill_config([
        {"skill_id": b.skill_id, "revision_id": b.revision_id, "enabled": b.enabled,
         "load_order": index * 2, "purpose": b.purpose} for index, b in enumerate(bindings)
    ], expected_parent_id=config_id)
    task = store.get_scheduled_task(task_id)
    refs = [ref for ref in task.skill_refs if ref.skill_name != name]
    store.update_scheduled_task(task_id, expected_version=task.version,
        skill_refs=tuple(replace(ref, position=i) for i, ref in enumerate(refs)))
    path = skills / name / "SKILL.md"
    path.unlink()
    payload = json.loads(manifest.read_text())
    entry = next(x for x in payload["files"] if x["source"] == f"ci/shared-skills/{name}/SKILL.md")
    entry.update(old_sha256=None, old_managed_sha256=None)
    manifest.write_text(json.dumps(payload))
    publication = prepare_consumer_system_contracts(root=root, database_path=db,
        operation_id="sparse-binding", skills_root=skills, audit_rules_path=rules, manifest_path=manifest)
    publication.publish()
    updated = store.list_runtime_skill_bindings(publication.new_config_id)
    assert len({b.load_order for b in updated}) == len(updated)
    assert next(b for b in updated if b.skill_id == skill.id).load_order == max(b.load_order for b in store.list_runtime_skill_bindings(sparse.id)) + 1
    publication.rollback()
    assert not path.exists()


def test_release_drains_legacy_snapshot_even_after_current_task_reconfiguration(tmp_path):
    from datetime import UTC, datetime
    root, skills, rules, manifest, old, _ = _fixture(tmp_path)
    db = tmp_path / "service.sqlite3"
    store, _, task_id = _store_with_refs(db, old)
    run = store.create_scheduled_task_run(
        task_id, trigger_kind="manual", scheduled_for=datetime(2026, 10, 19, tzinfo=UTC)
    )
    current = store.get_scheduled_task(task_id)
    store.update_scheduled_task(task_id, expected_version=current.version,
                                prompt="Different ordinary task", skill_refs=())
    with pytest.raises(ValueError, match=f"drain.*scheduled run {run.id}"):
        prepare_consumer_system_contracts(
            root=root, database_path=db, operation_id="snapshot-drain",
            skills_root=skills, audit_rules_path=rules, manifest_path=manifest,
        )
    assert not (root / "data/consumer-system-releases/snapshot-drain").exists()
