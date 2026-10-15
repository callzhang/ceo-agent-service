import json

from app.agent_context import _json


def test_context_json_is_compact_and_preserves_all_original_values():
    value = {
        'source': {'literal': '  原文\n段落\t"引文"\\路径  ', 'id': '00123'},
        'array': [None, False, 0, 1.25, {'odd/key~': ['a b', 'a\nb']}],
    }
    rendered = _json(value)
    assert json.loads(rendered) == value
    assert rendered == json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    assert len(rendered) < len(json.dumps(value, ensure_ascii=False, indent=2))
