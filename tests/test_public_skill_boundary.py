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
