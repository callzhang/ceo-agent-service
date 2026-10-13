from __main__ import _FIXTURE  # Supplied by the recorded CPU runner.
import json
import importlib
import xgrammar as xg
from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
from vllm.entrypoints.openai.responses.utils import construct_tool_dicts

module = importlib.import_module("vllm.tool_parsers.qwen3_engine_tool_parser")
module = importlib.reload(module)
parser = object.__new__(module.Qwen3EngineToolParser)
request = ResponsesRequest(
    model="qwen3.8-27b",
    input="Anonymous CPU grammar control",
    tools=_FIXTURE["tools"],
    tool_choice="auto",
    text={
        "format": {
            "type": "json_schema",
            "name": "consumer",
            "schema": _FIXTURE["schema"],
            "strict": True,
        }
    },
)
tag = parser.get_structural_tag(request, reasoning=False)
info = xg.TokenizerInfo(
    [b"{", b"}", b"<tool_call>", b"<eos>"],
    vocab_type=xg.VocabType.RAW,
    stop_token_ids=[3],
)
compiler = xg.GrammarCompiler(info)
compiled = compiler.compile_structural_tag(tag)


def accepts(s):
    m = xg.GrammarMatcher(compiled)
    return m.accept_string(s) and m.accept_token(3)


xml = '<tool_call>\n<function=exec>\n<parameter=input>text("中文 🐈");\n</parameter>\n</function>\n</tool_call>'
rows = {
    "custom_exec_xml": accepts(xml),
    "full_final_schema_in_declared_order": accepts(
        json.dumps({k: _FIXTURE["result"][k] for k in _FIXTURE["schema"]["properties"]})
    ),
    "canonical_wire_order_accepted": accepts(json.dumps(_FIXTURE["result"])),
    "free_text_rejected": not accepts("The answer is OK."),
    "invalid_final_enum_rejected": not accepts(
        json.dumps(
            {
                k: ("invented" if k == "outcome" else _FIXTURE["result"][k])
                for k in _FIXTURE["schema"]["properties"]
            }
        )
    ),
    "final_schema_exact": tag.format.elements[1].json_schema == _FIXTURE["schema"],
    "converter_count": len(construct_tool_dicts(request.tools, "auto")),
}
print(json.dumps(rows, ensure_ascii=False))

for label, text in [
    ("result", json.dumps(_FIXTURE["result"])),
    (
        "schema_order",
        json.dumps(
            {k: _FIXTURE["result"][k] for k in _FIXTURE["schema"]["properties"]}
        ),
    ),
]:
    m = xg.GrammarMatcher(compiled)
    failed_at = None
    for idx, ch in enumerate(text):
        if not m.accept_string(ch):
            failed_at = idx
            break
    print(
        json.dumps(
            {
                "diagnostic": label,
                "prefix_failure_offset": failed_at,
                "failure_context": text[
                    max(0, (failed_at or 0) - 35) : (failed_at or 0) + 35
                ]
                if failed_at is not None
                else None,
                "completed": m.is_completed(),
                "eos": m.accept_token(3) if failed_at is None else False,
            }
        )
    )
