"""The public distribution is usable without company policy packages."""
from pathlib import Path
import json

from app.managed_skills import REPOSITORY_MANAGED_SKILL_NAMES

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'ci/shared-skills'


def test_company_oa_policy_packages_are_not_in_the_public_distribution():
    assert not list(BASELINE.glob('stardust-oa-*'))


def test_public_baseline_has_generic_identity_and_no_internal_document_links():
    for name in REPOSITORY_MANAGED_SKILL_NAMES:
        root = BASELINE / name
        assert (root / 'SKILL.md').is_file(), name
        for path in root.rglob('*.md'):
            text = path.read_text()
            assert 'Derek' not in text, path.relative_to(ROOT)
            assert 'alidocs.dingtalk.com/i/nodes/' not in text, path.relative_to(ROOT)


def test_explicit_public_release_contains_only_available_generic_assets():
    manifest = json.loads((ROOT / 'ci/consumer-system-contract-release.json').read_text())
    for item in manifest['files']:
        assert 'stardust-oa-' not in item['source']
        assert item['source'] != 'ci/shared-skills/dingtalk-oa-approval/SKILL.md'
        assert (ROOT / item['source']).is_file()


def test_public_approval_integration_has_platform_discovery_scope():
    from app.business_skills import _parse_portable_skill_frontmatter
    path = BASELINE / 'dingtalk-oa-approval/SKILL.md'
    metadata = _parse_portable_skill_frontmatter(path.read_text(), path)
    assert metadata['name'] == 'dingtalk-oa-approval'
    assert metadata['description'].startswith('Use when')


def test_public_reporting_packages_contain_only_generic_workflow_and_script_descriptors():
    # Organization policy belongs to independently installable private packages,
    # including helpers, examples, rubrics and their policy-specific tests.
    for name in ('ceo-weekly-report', 'ceo-weekly-okr-report',
                 'ceo-daily-report', 'ceo-sales-weekly-report'):
        files = {p.relative_to(BASELINE / name).as_posix()
                 for p in (BASELINE / name).rglob('*') if p.is_file()
                 and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
        assert files <= {'SKILL.md', 'consumer-scripts.json'}, (name, files)
        text = (BASELINE / name / 'SKILL.md').read_text()
        assert 'organization' in text
        assert 'read_skill' in text


def test_company_okr_tenant_helper_is_not_a_public_distribution_asset():
    assert not (BASELINE / 'dingtang-okr-review').exists()


def test_public_oa_replay_uses_explicit_policy_inputs(tmp_path):
    import importlib.util
    path = ROOT / 'scripts/eval_oa_skill_replay.py'
    spec = importlib.util.spec_from_file_location('oa_replay_boundary', path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    source = tmp_path / 'policy'
    source.mkdir()
    (source / 'SKILL.md').write_text('Synthetic organization policy')
    assert 'Synthetic organization policy' in replay.skills_at(
        None, root=tmp_path, paths=('policy/SKILL.md',))


def test_generic_report_publication_does_not_adopt_existing_company_sources():
    manifest = json.loads((ROOT / 'ci/consumer-system-contract-release.json').read_text())
    for name in ('ceo-weekly-report', 'ceo-weekly-okr-report', 'ceo-daily-report'):
        entry = next(row for row in manifest['files']
                     if row['source'] == f'ci/shared-skills/{name}/SKILL.md')
        # Publication is scoped to the generic baseline. The previously installed
        # company digest must never be its overwrite/adoption precondition.
        assert entry['old_sha256'] == entry['new_sha256']
        assert entry['old_managed_sha256'] == entry['new_sha256']
