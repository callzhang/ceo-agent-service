from pathlib import Path

import app.business_skills as skills
from app.managed_skills import REPOSITORY_MANAGED_SKILL_NAMES


def test_fresh_public_install_needs_no_enterprise_skills(tmp_path):
    source, target = tmp_path / 'release', tmp_path / 'installed'
    for name in REPOSITORY_MANAGED_SKILL_NAMES:
        directory = source / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Generic example\nmetadata:\n  managed_by: ceo-agent-service\n---\nExample'
        )
        (directory / 'references').mkdir()
        (directory / 'references' / 'guide.txt').write_text('generic reference')
    extra = source / 'company-private'
    extra.mkdir()
    (extra / 'SKILL.md').write_text('private company instructions')
    installed = skills.install_public_skill_baseline(source, target)
    assert len(installed) == len(REPOSITORY_MANAGED_SKILL_NAMES)
    assert not (target / 'company-private').exists()
    assert (target / 'ceo-weekly-report/references/guide.txt').read_text() == 'generic reference'
    local = target / 'ceo-weekly-report/SKILL.md'
    local.write_text(local.read_text() + '\nUser edit')
    skills.install_public_skill_baseline(source, target)
    assert local.read_text().endswith('User edit')


def test_invalid_public_baseline_is_not_partially_installed(tmp_path):
    source = tmp_path / 'release'
    source.mkdir()
    target = tmp_path / 'installed'
    import pytest
    with pytest.raises(skills.BusinessSkillValidationError):
        skills.install_public_skill_baseline(source, target)
    assert not target.exists()
