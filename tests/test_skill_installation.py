from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from app.skill_repositories import PackageFile, RepositorySkill, SkillPackage, package_digest
from app.store import AutoReplyStore


def setup_install(tmp_path):
    from app.skill_installation import SkillInstallationService
    files = (PackageFile('SKILL.md', b'---\nname: example\ndescription: Example capability\n---\nExample'), PackageFile('scripts/run.py', b'print("example")', True), PackageFile('references/guide.md', b'Example guide'))
    candidate = RepositorySkill('https://github.com/example/skills', 'a' * 40, 'skills/example', 'example', 'Example capability')
    package = SkillPackage(candidate, files, package_digest(files))
    class Reader:
        def read_package(self, url, commit, path):
            assert (url, commit, path) == (candidate.repo_url, candidate.commit, candidate.skill_path)
            return package
    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    return SkillInstallationService(store=store, reader=Reader(), skills_root=tmp_path / 'installed'), store, candidate, files


def perform(installer, candidate):
    return installer.install(candidate.repo_url, candidate.commit, candidate.skill_path)


def test_install_copies_scripts_references_and_exact_skill_bytes(tmp_path):
    installer, store, candidate, files = setup_install(tmp_path)
    result = perform(installer, candidate)
    root = tmp_path / 'installed/example'
    assert (root / 'SKILL.md').read_bytes() == files[0].content
    assert (root / 'scripts/run.py').read_bytes() == b'print("example")'
    assert (root / 'scripts/run.py').stat().st_mode & 0o111
    assert (root / 'references/guide.md').read_text() == 'Example guide'
    revision = store.get_managed_skill_revision(result.revision_id)
    assert revision.content.encode() == files[0].content
    assert json.loads(revision.source)['commit'] == 'a' * 40
    assert result.activation_status == 'pending_restart'
    assert store.get_active_runtime_skill_config() is None
    assert not (tmp_path / 'checkout/skills/example').exists()


def test_same_install_is_idempotent(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    first = perform(installer, candidate)
    again = perform(installer, candidate)
    assert again.install_status == 'already_installed'
    assert first.revision_id == again.revision_id
    assert len(store.list_managed_skill_revisions(first.skill_id)) == 1


def test_name_collision_preserves_existing_skill(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    existing = store.create_managed_skill('example', 'User owned')
    with pytest.raises(ValueError, match='conflict'):
        perform(installer, candidate)
    assert store.list_managed_skill_revisions(existing.id) == ()
    assert not (tmp_path / 'installed/example').exists()


def test_concurrent_duplicate_installs_return_one_revision(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: perform(installer, candidate), range(2)))
    assert len({r.revision_id for r in results}) == 1
    assert len(store.list_managed_skills()) == 1


def test_failed_db_write_restores_package(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    # Real abort after package publication exercises both SQLite and file rollback.
    with store._connect() as db:
        db.execute("create trigger fail_install before insert on configs when new.record_type='runtime_skill_configs' begin select raise(abort,'test failure'); end")
    with pytest.raises(sqlite3.IntegrityError):
        perform(installer, candidate)
    assert store.get_managed_skill_by_name('example') is None
    assert not (tmp_path / 'installed/example').exists()


def test_failed_package_swap_creates_no_revision(tmp_path, monkeypatch):
    installer, store, candidate, _ = setup_install(tmp_path)
    import app.skill_installation as module
    def fail(*args):
        raise OSError('test disk failure')
    monkeypatch.setattr(module.os, 'rename', fail)
    with pytest.raises(OSError):
        perform(installer, candidate)
    assert store.get_managed_skill_by_name('example') is None


def test_install_preserves_existing_configuration_and_feedback_capability(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    current = store.create_runtime_skill_config([], expected_parent_id=None, feedback_iteration_enabled=False)
    perform(installer, candidate)
    pending = store.get_pending_or_active_runtime_skill_config()
    assert pending.parent_id == current.id
    assert not store.feedback_iteration_enabled(pending.id)


def test_optional_external_edit_is_versioned_without_ownership_marker(tmp_path):
    from app.managed_skills import capture_runtime_skill_edits
    installer, store, candidate, _ = setup_install(tmp_path)
    original = perform(installer, candidate)
    path = tmp_path / 'installed/example/SKILL.md'
    path.write_text(path.read_text() + '\nLocal improvement')
    changes = capture_runtime_skill_edits(store, skills_root=tmp_path / 'installed')
    assert [change.name for change in changes] == ['example']
    revisions = store.list_managed_skill_revisions(original.skill_id)
    assert len(revisions) == 2
    assert revisions[0].id == original.revision_id
    assert revisions[1].content.endswith('Local improvement')
    path.unlink()
    assert store.get_managed_skill_revision(original.revision_id).content.endswith('Example')


def test_changed_installed_content_is_not_overwritten_on_repeat(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    perform(installer, candidate)
    path = tmp_path / 'installed/example/references/guide.md'
    path.write_text('Local improvement')
    with pytest.raises(ValueError, match='conflict'):
        perform(installer, candidate)
    assert path.read_text() == 'Local improvement'


def test_crlf_content_and_local_edits_keep_exact_revision_bytes(tmp_path):
    import hashlib
    from app.managed_skills import capture_runtime_skill_edits
    installer, store, candidate, files = setup_install(tmp_path)
    expected = files[0].content.replace(b'\n', b'\r\n')
    changed = (PackageFile('SKILL.md', expected), *files[1:])
    installer.reader.read_package = lambda *args: SkillPackage(candidate, changed, package_digest(changed))
    result = perform(installer, candidate)
    assert store.get_managed_skill_revision(result.revision_id).content.encode() == expected
    assert result.sha256 == hashlib.sha256(expected).hexdigest()
    path = tmp_path / 'installed/example/SKILL.md'
    assert path.read_bytes() == expected
    assert capture_runtime_skill_edits(store, skills_root=tmp_path / 'installed') == ()
    updated = expected + b'\r\nLocal edit'
    path.write_bytes(updated)
    captured = capture_runtime_skill_edits(store, skills_root=tmp_path / 'installed')
    assert len(captured) == 1
    assert store.list_managed_skill_revisions(result.skill_id)[-1].content.encode() == updated


def test_install_after_load_failure_inherits_last_active_configuration(tmp_path):
    installer, store, candidate, _ = setup_install(tmp_path)
    active = store.create_runtime_skill_config([], expected_parent_id=None, feedback_iteration_enabled=False)
    store.record_runtime_skill_load(active.id, pid=101, loaded={})
    failed = store.create_runtime_skill_config([], expected_parent_id=active.id, feedback_iteration_enabled=True)
    store.record_runtime_skill_load_failure(failed.id, pid=102, error='Synthetic load failure')
    result = perform(installer, candidate)
    assert result.install_status == 'installed'
    current = store.get_pending_or_active_runtime_skill_config()
    assert current.parent_id == failed.id
    assert not store.feedback_iteration_enabled(current.id)
    assert store.get_runtime_skill_config(failed.id).status == 'load_failed'


def test_multiline_external_frontmatter_installs_without_rewriting(tmp_path):
    installer, store, candidate, files = setup_install(tmp_path)
    content = b'---\nname: example\ndescription: >\n  Reads input records and\n  produces a useful result.\nmetadata:\n  author: Example Author\n  tags:\n    - operations\n---\nExample body'
    changed = (PackageFile('SKILL.md', content), *files[1:])
    installer.reader.read_package = lambda *args: SkillPackage(candidate, changed, package_digest(changed))
    result = perform(installer, candidate)
    assert store.get_managed_skill_revision(result.revision_id).content.encode() == content
    assert (tmp_path / 'installed/example/SKILL.md').read_bytes() == content


def test_concurrent_config_change_is_not_overwritten(tmp_path, monkeypatch):
    installer, store, candidate, _ = setup_install(tmp_path)
    original = store.get_latest_runtime_skill_config
    def changed_after_read():
        prior = original()
        store.create_runtime_skill_config([], expected_parent_id=prior.id if prior else None, feedback_iteration_enabled=False)
        return prior
    monkeypatch.setattr(store, 'get_latest_runtime_skill_config', changed_after_read)
    with pytest.raises(ValueError, match='parent conflict'):
        perform(installer, candidate)
    assert store.get_managed_skill_by_name('example') is None
    assert not (tmp_path / 'installed/example').exists()
    assert not store.feedback_iteration_enabled(store.get_pending_or_active_runtime_skill_config().id)
