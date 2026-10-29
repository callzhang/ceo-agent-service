from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.agent_cli import _approved_consumer_skill_script, build_role_server


def _reviewed_private_package(tmp_path, monkeypatch, name='private-report'):
    source = tmp_path / 'reviewed'
    source.mkdir()
    installed = tmp_path / '.agents/skills' / name
    scripts = installed / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'report.py').write_text("print('synthetic report')\n")
    (scripts / 'helpers.py').write_text('VALUE = 42\n')
    digest = hashlib.sha256()
    for path in sorted(scripts.iterdir()):
        relative = path.relative_to(installed).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, 'big'))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, 'big'))
        digest.update(content)
    (source / f'{name}.json').write_text(json.dumps({
        'version': 1, 'entrypoints': ['scripts/report.py'],
        'script_tree_sha256': digest.hexdigest(),
    }))
    monkeypatch.setattr('app.agent_cli.CONSUMER_SCRIPT_SOURCE_ROOT', source)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    return scripts


def test_private_script_is_verified_without_public_source_copy(tmp_path, monkeypatch):
    scripts = _reviewed_private_package(tmp_path, monkeypatch)
    assert _approved_consumer_skill_script('private-report', 'scripts/report.py') == scripts / 'report.py'


def test_private_script_dependency_change_is_rejected(tmp_path, monkeypatch):
    scripts = _reviewed_private_package(tmp_path, monkeypatch)
    (scripts / 'helpers.py').write_text('VALUE = 99\n')
    with pytest.raises(ValueError, match='not reviewed'):
        _approved_consumer_skill_script('private-report', 'scripts/report.py')


def test_private_script_still_requires_explicit_reviewed_entrypoint(tmp_path, monkeypatch):
    _reviewed_private_package(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='not reviewed'):
        _approved_consumer_skill_script('private-report', 'scripts/helpers.py')


def test_private_script_added_python_dependency_is_rejected(tmp_path, monkeypatch):
    scripts = _reviewed_private_package(tmp_path, monkeypatch)
    (scripts.parent / 'extra.py').write_text('VALUE = 99\n')
    with pytest.raises(ValueError, match='not reviewed'):
        _approved_consumer_skill_script('private-report', 'scripts/report.py')


def test_private_script_symlink_is_rejected(tmp_path, monkeypatch):
    scripts = _reviewed_private_package(tmp_path, monkeypatch)
    (scripts / 'linked.py').symlink_to(scripts / 'helpers.py')
    with pytest.raises(ValueError, match='unavailable'):
        _approved_consumer_skill_script('private-report', 'scripts/report.py')


def test_person_facing_skill_script_is_not_a_consumer_entrypoint():
    with pytest.raises(ValueError, match='not reviewed'):
        _approved_consumer_skill_script('ceo-wechat', 'scripts/sender.py')


def test_consumer_tool_rejects_sender_before_starting_a_process(monkeypatch):
    monkeypatch.setattr('app.agent_cli._read_bound_task_skill', lambda *args: {})
    monkeypatch.setattr('app.agent_cli.subprocess.run', lambda *args, **kwargs: pytest.fail('sender process must not start'))
    tool = build_role_server('consumer')._tool_manager.get_tool('consumer_run_skill_script')
    with pytest.raises(ValueError, match='not reviewed'):
        tool.fn('ceo-wechat', 'scripts/sender.py', ['approve', '--id', '1'])
