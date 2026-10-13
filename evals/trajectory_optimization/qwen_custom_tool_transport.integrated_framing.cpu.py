import json
import unittest
from __main__ import _FIXTURE
import xgrammar as xg
from transformers import AutoTokenizer
from openai.types.responses import (
    CustomTool,
    NamespaceTool,
    FunctionTool,
    ToolChoiceCustom,
    ResponseCustomToolCall,
)
from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
from vllm.entrypoints.openai.responses.utils import (
    build_response_output_items,
    construct_chat_messages_with_tool_call,
)
from vllm.entrypoints.generate.base.protocol import FunctionCall
from vllm.tool_parsers.qwen3_engine_tool_parser import Qwen3EngineToolParser

TOKENIZER = AutoTokenizer.from_pretrained(
    "/home/stardust/.cache/huggingface/hub/models--gittensor-model-hub--Qwen3.8-27B-NVFP4-RTX5090/snapshots/5b7a687fc8211a5d631c8ca6a593dd37eb26ce33",
    local_files_only=True,
    trust_remote_code=False,
)
TOOLS = [
    CustomTool(type="custom", name="arbitrary_code", format={"type": "text"}),
    NamespaceTool(
        type="namespace",
        name="mixed",
        description="CPU only",
        tools=[CustomTool(type="custom", name="code", format={"type": "text"})],
    ),
    FunctionTool(
        type="function",
        name="ordinary",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    ),
]
REQUEST = ResponsesRequest(
    model="qwen3.8-27b",
    input="CPU only",
    tools=TOOLS,
    tool_choice="auto",
    text={
        "format": {
            "type": "json_schema",
            "name": "probe",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    },
)
COMPILER = xg.GrammarCompiler(
    xg.TokenizerInfo(
        [bytes([i]) for i in range(256)],
        vocab_type=xg.VocabType.RAW,
        stop_token_ids=[255],
    )
)
VALUES = [
    '\ntext("中文 🐈");\n',
    "123",
    'const marker="</parameter> </function> </tool_call> <tool_call> <parameter=x> <think> <|im_end|>";',
    '\\"\n\t\r\0',
    "",
]


def xml(name, raw, escaped=True):
    value = json.dumps({"value": raw}, ensure_ascii=False)
    if escaped:
        value = value.replace("<", "\\u003c")
    return (
        "<tool_call>\n<function="
        + name
        + ">\n<parameter=input>"
        + value
        + "</parameter>\n</function>\n</tool_call>"
    )


class Integrated(unittest.TestCase):
    def test_native_roundtrip(self):
        for name in ("arbitrary_code", "mixed__code"):
            parser = Qwen3EngineToolParser(TOKENIZER)
            grammar = COMPILER.compile_structural_tag(
                parser.get_structural_tag(REQUEST, reasoning=False)
            )
            for raw in VALUES:
                with self.subTest(name=name, raw=repr(raw)):
                    text = xml(name, raw)
                    matcher = xg.GrammarMatcher(grammar)
                    self.assertTrue(
                        matcher.accept_string(text) and matcher.accept_token(255)
                    )
                    calls = parser.extract_tool_calls(text, REQUEST).tool_calls
                    self.assertEqual(len(calls), 1)
                    outputs = build_response_output_items(
                        None,
                        None,
                        [
                            FunctionCall(
                                name=calls[0].function.name,
                                arguments=calls[0].function.arguments,
                                id="call_cpu",
                            )
                        ],
                        tools=TOOLS,
                    )
                    self.assertEqual(outputs[0].input, raw)
                    self.assertEqual(
                        outputs[0].namespace,
                        "mixed" if name.startswith("mixed__") else None,
                    )

    def test_ambiguous_input_rejected(self):
        grammar = COMPILER.compile_structural_tag(
            Qwen3EngineToolParser(TOKENIZER).get_structural_tag(
                REQUEST, reasoning=False
            )
        )
        matcher = xg.GrammarMatcher(grammar)
        self.assertFalse(matcher.accept_string(xml("arbitrary_code", VALUES[2], False)))

    def test_real_token_stream(self):
        for raw in VALUES:
            parser = Qwen3EngineToolParser(TOKENIZER)
            text = xml("arbitrary_code", raw)
            ids = TOKENIZER.encode(text, add_special_tokens=False)
            previous = ""
            sent = 0
            fragments = []
            deltas = 0
            for end in range(1, len(ids) + 1):
                current = TOKENIZER.decode(ids[:end], skip_special_tokens=False)
                # UTF-8 codepoints may span tokens; submit only stable decoded prefixes.
                if current.endswith("\ufffd"):
                    continue
                self.assertTrue(current.startswith(previous))
                message = parser.extract_tool_calls_streaming(
                    previous_text=previous,
                    current_text=current,
                    delta_text=current[len(previous) :],
                    previous_token_ids=ids[:sent],
                    current_token_ids=ids[:end],
                    delta_token_ids=ids[sent:end],
                    request=REQUEST,
                )
                if message and message.tool_calls:
                    fragments.extend(
                        t.function.arguments or ""
                        for t in message.tool_calls
                        if t.function
                    )
                sent = end
                previous = current
                deltas += 1
            self.assertEqual(sent, len(ids))
            self.assertGreater(deltas, 1)
            message = parser.finish_streaming()
            if message and message.tool_calls:
                fragments.extend(
                    t.function.arguments or "" for t in message.tool_calls if t.function
                )
            restored = json.loads("".join(fragments))
            self.assertEqual(restored["input"]["value"], raw)

    def test_auto_without_final_schema(self):
        request = ResponsesRequest.model_validate(
            {**REQUEST.model_dump(by_alias=True), "text": None}
        )
        parser = Qwen3EngineToolParser(TOKENIZER)
        tag = parser.get_structural_tag(request, reasoning=False)
        self.assertIsNotNone(tag)
        auto_grammar = COMPILER.compile_structural_tag(tag)
        matcher = xg.GrammarMatcher(auto_grammar)
        self.assertTrue(
            matcher.accept_string("Plain final text 中文") and matcher.accept_token(255)
        )
        matcher = xg.GrammarMatcher(auto_grammar)
        ordinary = "<tool_call>\n<function=ordinary>\n<parameter=value>plain</parameter>\n</function>\n</tool_call>"
        self.assertTrue(matcher.accept_string(ordinary) and matcher.accept_token(255))
        self.assertIsNone(
            parser.get_structural_tag(
                ResponsesRequest.model_validate(
                    {**request.model_dump(by_alias=True), "tool_choice": "none"}
                ),
                reasoning=False,
            )
        )
        matcher = xg.GrammarMatcher(COMPILER.compile_structural_tag(tag))
        self.assertTrue(
            matcher.accept_string(xml("arbitrary_code", VALUES[2]))
            and matcher.accept_token(255)
        )
        matcher = xg.GrammarMatcher(COMPILER.compile_structural_tag(tag))
        self.assertFalse(matcher.accept_string(xml("arbitrary_code", VALUES[2], False)))

    def test_namespace_required_forced(self):
        for choice in ("required", ToolChoiceCustom(type="custom", name="mixed__code")):
            request = ResponsesRequest.model_validate(
                {**REQUEST.model_dump(by_alias=True), "tool_choice": choice}
            )
            parser = Qwen3EngineToolParser(TOKENIZER)
            matcher = xg.GrammarMatcher(
                COMPILER.compile_structural_tag(
                    parser.get_structural_tag(request, reasoning=False)
                )
            )
            self.assertTrue(
                matcher.accept_string(xml("mixed__code", VALUES[2]))
                and matcher.accept_token(255)
            )

    def test_chat_function_control(self):
        from vllm.entrypoints.openai.chat_completion.protocol import (
            ChatCompletionRequest,
        )

        request = ChatCompletionRequest(
            model="qwen3.8-27b",
            messages=[{"role": "user", "content": "CPU only"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "ordinary",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            tool_choice="required",
        )
        parser = Qwen3EngineToolParser(TOKENIZER)
        tag = parser.get_structural_tag(request, reasoning=False)
        matcher = xg.GrammarMatcher(COMPILER.compile_structural_tag(tag))
        text = "<tool_call>\n<function=ordinary>\n<parameter=value>plain</parameter>\n</function>\n</tool_call>"
        self.assertTrue(matcher.accept_string(text) and matcher.accept_token(255))
        self.assertEqual(
            json.loads(
                parser.extract_tool_calls(text, request)
                .tool_calls[0]
                .function.arguments
            ),
            {"value": "plain"},
        )

    def test_history_envelope(self):
        raw = VALUES[2]
        item = ResponseCustomToolCall(
            type="custom_tool_call",
            id="ctc_cpu",
            call_id="call_cpu",
            name="arbitrary_code",
            input=raw,
        )
        messages = construct_chat_messages_with_tool_call([item])
        self.assertEqual(
            json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]),
            {"input": {"value": raw}},
        )

    def test_required_and_forced(self):
        for choice in (
            "required",
            ToolChoiceCustom(type="custom", name="arbitrary_code"),
        ):
            request = ResponsesRequest.model_validate(
                {**REQUEST.model_dump(by_alias=True), "tool_choice": choice}
            )
            parser = Qwen3EngineToolParser(TOKENIZER)
            grammar = COMPILER.compile_structural_tag(
                parser.get_structural_tag(request, reasoning=False)
            )
            matcher = xg.GrammarMatcher(grammar)
            self.assertTrue(
                matcher.accept_string(xml("arbitrary_code", VALUES[2]))
                and matcher.accept_token(255)
            )

    def test_current_full_schema(self):
        request = ResponsesRequest(
            model="qwen3.8-27b",
            input="CPU only",
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
        parser = Qwen3EngineToolParser(TOKENIZER)
        tag = parser.get_structural_tag(request, reasoning=False)
        self.assertEqual(tag.format.elements[1].json_schema, _FIXTURE["schema"])
        grammar = COMPILER.compile_structural_tag(tag)
        matcher = xg.GrammarMatcher(grammar)
        final = json.dumps(
            {k: _FIXTURE["result"][k] for k in _FIXTURE["schema"]["properties"]}
        )
        self.assertTrue(matcher.accept_string(final) and matcher.accept_token(255))
        matcher = xg.GrammarMatcher(grammar)
        raw = 'text("OK");'
        text = xml("exec", raw)
        self.assertTrue(matcher.accept_string(text) and matcher.accept_token(255))
        calls = parser.extract_tool_calls(text, request).tool_calls
        outputs = build_response_output_items(
            None,
            None,
            [
                FunctionCall(
                    name="exec", arguments=calls[0].function.arguments, id="call_exec"
                )
            ],
            tools=request.tools,
        )
        self.assertEqual(outputs[0].input, raw)

    def test_function_control(self):
        text = "<tool_call>\n<function=ordinary>\n<parameter=value>plain</parameter>\n</function>\n</tool_call>"
        parser = Qwen3EngineToolParser(TOKENIZER)
        matcher = xg.GrammarMatcher(
            COMPILER.compile_structural_tag(
                parser.get_structural_tag(REQUEST, reasoning=False)
            )
        )
        self.assertTrue(matcher.accept_string(text) and matcher.accept_token(255))
        calls = parser.extract_tool_calls(text, REQUEST).tool_calls
        self.assertEqual(json.loads(calls[0].function.arguments), {"value": "plain"})


if __name__ == "__main__":
    unittest.main()
