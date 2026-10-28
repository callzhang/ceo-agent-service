import subprocess

import pytest

URL = 'https://github.com/example/synthetic-skills.git'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'example@example.invalid')
    git(root, 'config', 'user.name', 'Example')
    return root


def add_skill(root, path='skills/example', name='example', author='Example Author'):
    folder = root / path
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Example capability\nmetadata:\n  author: {author}\n---\n\nExample body')
    (folder / 'references').mkdir(exist_ok=True)
    (folder / 'references/guide.md').write_text('Guide')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Example change')
    return git(root, 'rev-parse', 'HEAD')


def reader_for(root):
    from app.skill_repositories import SkillRepositoryReader
    def run(args, **kwargs):
        return subprocess.run([str(root) if a == URL else a for a in args], **kwargs)
    return SkillRepositoryReader(run=run)


def test_exact_commit_survives_branch_move(tmp_path):
    root = repository(tmp_path)
    commit = add_skill(root)
    reader = reader_for(root)
    rows = reader.list_skills(URL)
    assert rows[0].author == 'Example Author'
    assert rows[0].author_kind == 'author'
    assert rows[0].updated_at is not None
    assert rows[0].name == 'example'
    original = (root / 'skills/example/SKILL.md').read_bytes()
    (root / 'skills/example/SKILL.md').write_text('changed')
    git(root, 'commit', '-am', 'Move branch')
    package = reader.read_package(URL, commit, 'skills/example')
    assert package.candidate.commit == commit
    assert next(f.content for f in package.files if f.path == 'SKILL.md') == original
    assert next(f.content for f in package.files if f.path == 'references/guide.md') == b'Guide'


def test_multi_skill_repository_lists_only_real_skill_directories(tmp_path):
    root = repository(tmp_path)
    add_skill(root, '.', 'root')
    add_skill(root, 'skills/example', 'example')
    add_skill(root, '.agents/skills/other', 'other')
    (root / 'skills/broken').mkdir()
    (root / 'skills/broken/SKILL.md').write_text('invalid')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Add malformed sibling')
    rows = reader_for(root).list_skills(URL)
    assert {r.name for r in rows if r.available} == {'root', 'example', 'other'}
    assert sum(not r.available for r in rows) == 1


def test_duplicate_declared_name_is_unavailable(tmp_path):
    root = repository(tmp_path)
    add_skill(root, 'skills/one', 'same')
    add_skill(root, 'skills/two', 'same')
    rows = reader_for(root).list_skills(URL)
    assert len(rows) == 2 and not any(r.available for r in rows)


@pytest.mark.parametrize('url', ['https://github.com/example/repo/tree/main/skills/x', 'file:///tmp/anything', 'https://user:secret@github.com/example/repo', '--upload-pack=bad'])
def test_invalid_source_never_reaches_git(url):
    from app.skill_repositories import SkillRepositoryReader
    def unexpected(*args, **kwargs):
        pytest.fail('invalid source reached Git')
    with pytest.raises(ValueError):
        SkillRepositoryReader(run=unexpected).list_skills(url)


def test_native_auth_failure_is_explicit():
    from app.skill_repositories import SkillRepositoryReader, SkillRepositoryError
    def failure(args, **kwargs):
        raise subprocess.CalledProcessError(128, args, stderr=b'private-provider-detail')
    with pytest.raises(SkillRepositoryError, match='service account'):
        SkillRepositoryReader(run=failure).list_skills('git@private.example:team/skills.git')


def test_timeout_is_not_empty_search():
    from app.skill_repositories import SkillRepositoryReader
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])
    with pytest.raises(TimeoutError):
        SkillRepositoryReader(run=timeout).list_skills(URL)


def test_standard_external_frontmatter_keeps_multiline_description_and_nested_metadata(tmp_path):
    root = repository(tmp_path)
    add_skill(root)
    text = '---\nname: example\ndescription: >\n  Reads input records and\n  produces a useful result.\nmetadata:\n  author: Example Author\n  tags:\n    - operations\n---\nExample body'
    (root / 'skills/example/SKILL.md').write_text(text)
    git(root, 'commit', '-am', 'Portable multiline metadata')
    rows = reader_for(root).list_skills(URL)
    assert rows[0].available
    assert rows[0].description == 'Reads input records and produces a useful result.'
    assert rows[0].author == 'Example Author'


def test_root_package_excludes_other_skill_packages_but_keeps_support_files(tmp_path):
    root = repository(tmp_path)
    add_skill(root, '.', 'root')
    add_skill(root, 'skills/other', 'other')
    commit = add_skill(root, '.agents/skills/third', 'third')
    package = reader_for(root).read_package(URL, commit, '.')
    assert {f.path for f in package.files} == {'SKILL.md', 'references/guide.md'}
    assert package.candidate.name == 'root'


def test_duplicate_declared_name_cannot_install_an_unavailable_row(tmp_path):
    root = repository(tmp_path)
    add_skill(root, 'skills/one', 'same')
    commit = add_skill(root, 'skills/two', 'same')
    with pytest.raises(ValueError, match='Duplicate'):
        reader_for(root).read_package(URL, commit, 'skills/one')
