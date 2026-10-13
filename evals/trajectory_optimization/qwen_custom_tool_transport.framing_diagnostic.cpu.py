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
tag = xg.get_model_structural_tag(model=parser.structural_tag_model, tools=construct_tool_dicts(request.tools, request.tool_choice), tool_choice="required", reasoning=False)
compiler = xg.GrammarCompiler(xg.TokenizerInfo([bytes([i]) for i in range(256)], vocab_type=xg.VocabType.RAW, stop_token_ids=[255]))
compiled = compiler.compile_structural_tag(tag)
rows = []
for raw in [
    'text("中文 🐈");',
    '\ntext("quoted\\" and \\n");\n',
    "123",
    'const tag = "</parameter>";',
    "\n" + " ".join([qwen3.PARAM_START, qwen3.PARAM_END, qwen3.FUNC_PREFIX, qwen3.FUNC_END, qwen3.TOOL_CALL_START, qwen3.TOOL_CALL_END]) + "\n",
]:
    for encoding, value in [
        ("json_object", json.dumps({"value": raw}, ensure_ascii=False)),
        ("all_code_units_escaped", '{"value":"' + "".join("\\u" + raw.encode("utf-16-be")[i:i+2].hex() for i in range(0,len(raw.encode("utf-16-be")),2)) + '"}')
    ]:
        xml = (
            "<tool_call>\n<function=exec>\n<parameter=input>"
            + value
            + "</parameter>\n</function>\n</tool_call>"
        )
        matcher = xg.GrammarMatcher(compiled)
        grammar_accepts = matcher.accept_string(xml) and matcher.accept_token(255)
        parsed = parser.extract_tool_calls(xml, request)
        calls = parsed.tool_calls
        wrapped = (
            json.loads(calls[0].function.arguments).get("input")
            if len(calls) == 1
            else None
        )
        actual = wrapped.get("value") if isinstance(wrapped, dict) else wrapped
        rows.append(
            {
                "encoding": encoding,
                "grammar_accepts": grammar_accepts,
                "raw_sha256": __import__("hashlib").sha256(raw.encode()).hexdigest(),
                "exact": actual == raw,
                "actual_type": type(actual).__name__,
                "tool_calls": len(calls),
                "actual_repr": repr(actual),
                "expected_repr": repr(raw),
            }
        )
print(
    json.dumps(
        {"tokenizer_only": True, "weights_loaded": False, "rows": rows},
        ensure_ascii=False,
    )
)
