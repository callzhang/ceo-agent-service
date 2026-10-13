import json
from transformers import AutoTokenizer
from vllm.tool_parsers.qwen3_engine_tool_parser import Qwen3EngineToolParser
from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
from openai.types.responses import FunctionTool

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
rows = []
for raw in [
    'text("中文 🐈");',
    '\ntext("quoted\\" and \\n");\n',
    "123",
    'const tag = "</parameter>";',
]:
    for encoding, value in [
        ("json_object", json.dumps({"value": raw}, ensure_ascii=False))
    ]:
        xml = (
            "<tool_call>\n<function=exec>\n<parameter=input>"
            + value
            + "</parameter>\n</function>\n</tool_call>"
        )
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
