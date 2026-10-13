import copy
from xgrammar.structural_tag import GrammarFormat
import json
from transformers import AutoTokenizer
from vllm.tool_parsers.qwen3_engine_tool_parser import Qwen3EngineToolParser
from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
from openai.types.responses import FunctionTool
from vllm.parser import qwen3
import xgrammar as xg
from vllm.entrypoints.openai.responses.utils import construct_tool_dicts

snapshot = "/home/stardust/.cache/huggingface/hub/models--gittensor-model-hub--Qwen3.8-27B-NVFP4-RTX5090/snapshots/5b7a687fc8211a5d631c8ca6a593dd37eb26ce33"
tok = AutoTokenizer.from_pretrained(
    snapshot, local_files_only=True, trust_remote_code=False
)
parser = Qwen3EngineToolParser(tok)
request = ResponsesRequest(
    model="qwen3.8-27b",
    input="Anonymous CPU parser control",
    tools=[
        FunctionTool(
            type="function",
            name="exec",
            parameters={
                "type": "object",
                "properties": {
                    "input": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    }
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        )
    ],
    tool_choice="auto",
)
tag = xg.get_model_structural_tag(
    model=parser.structural_tag_model,
    tools=construct_tool_dicts(request.tools, request.tool_choice),
    tool_choice="required",
    reasoning=False,
)
compiler = xg.GrammarCompiler(
    xg.TokenizerInfo(
        [bytes([i]) for i in range(256)],
        vocab_type=xg.VocabType.RAW,
        stop_token_ids=[255],
    )
)
compiled = compiler.compile_structural_tag(tag)

# Private transport framing: XML parameter containing one JSON string value.
# Literal '<' is encoded by JSON unicode escape, so XML tokens cannot be data.
value_grammar = r"""root ::= "<parameter=input>" ws "{" ws "\"value\"" ws ":" ws string ws "}" ws "</parameter>"
ws ::= [ \n\r\t]*
string ::= "\"" char* "\""
char ::= [^\"\\\x00-\x1f<] | "\\" escape
escape ::= [\"\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
"""
# Qwen XML body owns the parameter delimiter; keep outer model tag unchanged.

candidate_tag = copy.deepcopy(tag)
candidate_tag.format.tags[0].content = GrammarFormat(grammar=value_grammar)
candidate_compiled = compiler.compile_structural_tag(candidate_tag)
rows = []
for raw in [
    'text("中文 🐈");',
    '\ntext("quoted\\" and \\n");\n',
    "123",
    'const tag = "</parameter>";',
    "\n"
    + " ".join(
        [
            qwen3.PARAM_START,
            qwen3.PARAM_END,
            qwen3.FUNC_PREFIX,
            qwen3.FUNC_END,
            qwen3.TOOL_CALL_START,
            qwen3.TOOL_CALL_END,
        ]
    )
    + "\n",
]:
    for encoding, value in [
        ("json_object", json.dumps({"value": raw}, ensure_ascii=False)),
        (
            "only_markup_start_escaped",
            json.dumps({"value": raw}, ensure_ascii=False).replace("<", "\\u003c"),
        ),
        (
            "all_code_units_escaped",
            '{"value":"'
            + "".join(
                "\\u" + raw.encode("utf-16-be")[i : i + 2].hex()
                for i in range(0, len(raw.encode("utf-16-be")), 2)
            )
            + '"}',
        ),
    ]:
        xml = (
            "<tool_call>\n<function=exec>\n<parameter=input>"
            + value
            + "</parameter>\n</function>\n</tool_call>"
        )
        matcher = xg.GrammarMatcher(compiled)
        grammar_accepts = matcher.accept_string(xml) and matcher.accept_token(255)
        candidate_matcher = xg.GrammarMatcher(candidate_compiled)
        candidate_accepts = candidate_matcher.accept_string(
            xml
        ) and candidate_matcher.accept_token(255)
        parsed = parser.extract_tool_calls(xml, request)
        calls = parsed.tool_calls
        wrapped = (
            json.loads(calls[0].function.arguments).get("input")
            if len(calls) == 1
            else None
        )
        actual = wrapped.get("value") if isinstance(wrapped, dict) else wrapped
        stream_checks = {}
        if candidate_accepts:
            for chunk_size in (1, 7, len(xml)):
                stream_parser = Qwen3EngineToolParser(tok)
                fragments = []
                previous = ""
                for pos in range(0, len(xml), chunk_size):
                    delta = xml[pos : pos + chunk_size]
                    current = previous + delta
                    message = stream_parser.extract_tool_calls_streaming(
                        previous_text=previous,
                        current_text=current,
                        delta_text=delta,
                        previous_token_ids=[],
                        current_token_ids=tok.encode(delta, add_special_tokens=False)
                        if chunk_size == len(xml)
                        else [],
                        delta_token_ids=tok.encode(delta, add_special_tokens=False)
                        if chunk_size == len(xml)
                        else [],
                        request=request,
                    )
                    if message and message.tool_calls:
                        fragments.extend(
                            t.function.arguments or ""
                            for t in message.tool_calls
                            if t.function
                        )
                    previous = current
                message = stream_parser.finish_streaming()
                if message and message.tool_calls:
                    fragments.extend(
                        t.function.arguments or ""
                        for t in message.tool_calls
                        if t.function
                    )
                streamed = json.loads("".join(fragments))
                stream_checks[str(chunk_size)] = streamed["input"]["value"] == raw
        rows.append(
            {
                "encoding": encoding,
                "grammar_accepts": grammar_accepts,
                "candidate_accepts": candidate_accepts,
                "stream_checks": stream_checks,
                "raw_sha256": __import__("hashlib").sha256(raw.encode()).hexdigest(),
                "exact": actual == raw,
                "actual_type": type(actual).__name__,
                "tool_calls": len(calls),
                "actual_repr": repr(actual),
                "expected_repr": repr(raw),
            }
        )
assert all(
    not r["candidate_accepts"] or (r["exact"] and all(r["stream_checks"].values()))
    for r in rows
)
assert sum(r["candidate_accepts"] for r in rows) == 13
assert sum(r["grammar_accepts"] and not r["exact"] for r in rows) == 2
print(
    json.dumps(
        {"tokenizer_only": True, "weights_loaded": False, "rows": rows},
        ensure_ascii=False,
    )
)
