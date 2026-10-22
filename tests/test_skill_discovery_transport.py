import csv
import io
from pathlib import Path

from app import agent_cli


def decode_catalog(payload):
    text = payload['catalog'].split('Root\tPath\n', 1)[1]
    rows = list(csv.reader(io.StringIO(text), delimiter='\t'))
    separator = rows.index([])
    roots = dict(rows[:separator])
    header = rows[separator + 1]
    assert header in (['Name', 'Root', 'Relative path'],
                      ['Name', 'Root', 'Relative path', 'Description'])
    result = []
    for row in rows[separator + 2:]:
        name, alias, relative = row[:3]
        description = row[3] if len(row) == 4 else ''
        result.append({'name': name, 'path': str(Path(roots[alias]) / relative), 'description': description})
    return result


def test_discovery_transport_preserves_every_metadata_field_without_repeated_paths(
    tmp_path, monkeypatch
):
    roots = (tmp_path / 'first skill root', tmp_path / 'second skill root')
    expected = []
    for i in range(80):
        name = f'guide-{i}' if i != 79 else 'guide-0'
        root = roots[i % 2]
        path = root / f'folder-{i}' / 'SKILL.md'
        path.parent.mkdir(parents=True)
        description = 'Review facts: preserve "quotes", 中文, tabs\tand\nnewlines.'
        path.write_text(f'---\nname: {name}\ndescription: |\n  {description.replace(chr(10), chr(10) + "  ")}\n---\n\nNOT A DIRECTORY BODY\n')
        expected.append({'name': name, 'path': str(path.resolve()), 'description': description})
    monkeypatch.setattr('app.agent_skill_usage.AGENT_SKILL_ROOTS', roots)
    tool = agent_cli.build_role_server('audit')._tool_manager.get_tool('read_skill').fn
    result = tool()
    assert 'catalog' in result
    compact_expected = [{**row, 'description': ''} for row in expected]
    assert decode_catalog(result) == sorted(compact_expected, key=lambda row: row['path'])
    assert decode_catalog(tool(query='review facts')) == sorted(expected, key=lambda row: row['path'])
    assert len(result['catalog']) < len(tool(query='review facts')['catalog']) / 2
    assert 'NOT A DIRECTORY BODY' not in result['catalog']
    assert result['catalog'].count(str(roots[0])) == 1
    assert result['catalog'].count(str(roots[1])) == 1
    assert tool(path=expected[0]['path'])['content'].endswith('NOT A DIRECTORY BODY\n')
