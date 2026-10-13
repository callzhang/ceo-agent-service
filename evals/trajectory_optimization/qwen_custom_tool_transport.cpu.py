import json
import unittest
from lark.exceptions import LarkError
from openai.types.responses import (
    CustomTool,
    NamespaceTool,
    FunctionTool,
    ResponseCustomToolCall,
)
from vllm.entrypoints.openai.responses.utils import (
    construct_tool_dicts,
    build_response_output_items,
    construct_chat_messages_with_tool_call,
    construct_input_messages,
)
from vllm.entrypoints.openai.responses.streaming_events import (
    SimpleStreamingEventProcessor,
)
from vllm.tool_parsers.utils import (
    find_tool_name,
    find_tool_properties,
    get_json_schema_from_tools,
)
from openai.types.responses import ToolChoiceFunction, ToolChoiceCustom
from vllm.entrypoints.generate.base.protocol import (
    FunctionCall,
    DeltaMessage,
    DeltaToolCall,
    DeltaFunctionCall,
)

RAW = '// @exec: {"max_output_tokens":1000}\ntext("引号\\" 🐈");\nconst x = {"a": [1, null]};\n'
CUSTOM = CustomTool(
    type="custom",
    name="arbitrary_code",
    description="Native freeform code",
    format={"type": "grammar", "syntax": "lark", "definition": "start: /[\\s\\S]+/"},
)
TOOLS = [
    CUSTOM,
    FunctionTool(
        type="function",
        name="ordinary_read",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    NamespaceTool(
        type="namespace",
        name="arbitrary_namespace",
        description="Mixed native tools",
        tools=[
            CustomTool(type="custom", name="another_code", format={"type": "text"}),
            {
                "type": "function",
                "name": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        ],
    ),
]


class Transport(unittest.TestCase):
    def test_declarations(self):
        out = construct_tool_dicts(TOOLS, "auto")
        names = [t["function"]["name"] for t in out]
        self.assertEqual(
            names,
            [
                "arbitrary_code",
                "ordinary_read",
                "arbitrary_namespace__another_code",
                "arbitrary_namespace__read",
            ],
        )
        self.assertEqual(
            json.loads(
                out[0]["function"]["description"].split("Custom input format: ", 1)[1]
            ),
            CUSTOM.format.model_dump(),
        )

    def test_nonstream_plain_and_namespace(self):
        for name, ns, real in [
            ("arbitrary_code", None, "arbitrary_code"),
            (
                "arbitrary_namespace__another_code",
                "arbitrary_namespace",
                "another_code",
            ),
        ]:
            with self.subTest(name=name):
                out = build_response_output_items(
                    None,
                    None,
                    [
                        FunctionCall(
                            name=name,
                            arguments=json.dumps({"input": RAW}),
                            id="call_native",
                        )
                    ],
                    tools=TOOLS,
                )
                self.assertEqual(len(out), 1)
                self.assertEqual(out[0].type, "custom_tool_call")
                self.assertEqual(out[0].input, RAW)
                self.assertEqual(out[0].name, real)
                self.assertEqual(out[0].namespace, ns)
                self.assertEqual(out[0].call_id, "call_native")

    def test_function_unchanged(self):
        args = '{"key":"00123"}'
        out = build_response_output_items(
            None,
            None,
            [FunctionCall(name="ordinary_read", arguments=args, id="read_native")],
            tools=TOOLS,
        )[0]
        self.assertEqual(out.type, "function_call")
        self.assertEqual(out.arguments, args)

    def test_history_object_and_dict(self):
        for item in [
            ResponseCustomToolCall(
                type="custom_tool_call",
                call_id="call_native",
                name="arbitrary_code",
                input=RAW,
            ),
            {
                "type": "custom_tool_call",
                "call_id": "call_native",
                "name": "arbitrary_code",
                "input": RAW,
            },
        ]:
            with self.subTest(kind=type(item).__name__):
                msgs = construct_chat_messages_with_tool_call(
                    [
                        item,
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "call_native",
                            "output": "exact tool receipt",
                        },
                    ]
                )
                self.assertEqual(len(msgs), 2)
                self.assertEqual(
                    json.loads(msgs[0]["tool_calls"][0]["function"]["arguments"]),
                    {"input": RAW},
                )
                self.assertEqual(msgs[1]["content"], "exact tool receipt")
                self.assertEqual(msgs[1]["tool_call_id"], "call_native")

    def test_stream_one_character_chunks(self):
        for name, ns in [
            ("arbitrary_code", None),
            ("arbitrary_namespace__another_code", "arbitrary_namespace"),
        ]:
            with self.subTest(name=name):
                proc = SimpleStreamingEventProcessor(tools=TOOLS)
                events = []
                args = json.dumps({"input": RAW})
                for idx, char in enumerate(args):
                    dm = DeltaMessage(
                        tool_calls=[
                            DeltaToolCall(
                                index=0,
                                function=DeltaFunctionCall(
                                    name=name if idx == 0 else None, arguments=char
                                ),
                            )
                        ]
                    )
                    state, call = proc.resolve_target_state(dm)
                    if proc.needs_transition(state, call):
                        events += proc.close_current()
                        events += proc.open(state, call)
                    events += proc.emit_delta(dm, None)
                events += proc.close_current()
                self.assertEqual(events[0].item.type, "custom_tool_call")
                self.assertEqual(
                    "".join(
                        e.delta
                        for e in events
                        if e.type == "response.custom_tool_call_input.delta"
                    ),
                    RAW,
                )
                self.assertEqual(events[-1].item.input, RAW)
                self.assertEqual(events[-1].item.namespace, ns)
                self.assertFalse(
                    any(
                        e.type.startswith("response.function_call_arguments.")
                        for e in events
                    )
                )

    def test_custom_grammar_and_input_shape(self):
        constrained = CustomTool(
            type="custom",
            name="strict_grammar",
            format={"type": "grammar", "syntax": "lark", "definition": 'start: "OK"'},
        )
        for value in [{"input": "wrong"}, {"input": 42}, {"input": "OK", "extra": 1}]:
            with self.subTest(value=value):
                with self.assertRaises((ValueError, LarkError)):
                    build_response_output_items(
                        None,
                        None,
                        [
                            FunctionCall(
                                name="strict_grammar",
                                arguments=json.dumps(value),
                                id="bad",
                            )
                        ],
                        tools=[constrained],
                    )

    def test_function_stream_unchanged(self):
        proc = SimpleStreamingEventProcessor(tools=TOOLS)
        dm = DeltaMessage(
            tool_calls=[
                DeltaToolCall(
                    index=0,
                    function=DeltaFunctionCall(
                        name="ordinary_read", arguments='{"key":"00123"}'
                    ),
                )
            ]
        )
        state, call = proc.resolve_target_state(dm)
        events = (
            proc.open(state, call) + proc.emit_delta(dm, None) + proc.close_current()
        )
        self.assertEqual(events[0].item.type, "function_call")
        self.assertEqual(events[-1].item.arguments, '{"key":"00123"}')
        self.assertTrue(
            any(e.type == "response.function_call_arguments.delta" for e in events)
        )

    def test_regex_grammar(self):
        tool = CustomTool(
            type="custom",
            name="arbitrary_regex",
            format={"type": "grammar", "syntax": "regex", "definition": "[A-Z]+"},
        )
        good = build_response_output_items(
            None,
            None,
            [
                FunctionCall(
                    name="arbitrary_regex",
                    arguments=json.dumps({"input": "ABC"}),
                    id="good",
                )
            ],
            tools=[tool],
        )[0]
        self.assertEqual(good.input, "ABC")
        with self.assertRaises(ValueError):
            build_response_output_items(
                None,
                None,
                [
                    FunctionCall(
                        name="arbitrary_regex",
                        arguments=json.dumps({"input": "bad"}),
                        id="bad",
                    )
                ],
                tools=[tool],
            )

    def test_stored_previous_response_custom_call(self):
        call = ResponseCustomToolCall(
            type="custom_tool_call",
            call_id="stored_call",
            name="arbitrary_code",
            input=RAW,
        )
        msgs = construct_input_messages(
            request_instructions=None,
            request_input=[
                {
                    "type": "custom_tool_call_output",
                    "call_id": "stored_call",
                    "output": "stored receipt",
                }
            ],
            prev_msg=[{"role": "user", "content": "original task"}],
            prev_response_output=[call],
        )
        self.assertEqual(len(msgs), 3)
        self.assertEqual(
            json.loads(msgs[1]["tool_calls"][0]["function"]["arguments"]),
            {"input": RAW},
        )
        self.assertEqual(msgs[1]["tool_calls"][0]["id"], "stored_call")
        self.assertEqual(msgs[2]["tool_call_id"], "stored_call")

    def test_lookup_and_required_forced_choices(self):
        self.assertTrue(find_tool_name(TOOLS, "arbitrary_code"))
        self.assertEqual(
            find_tool_properties(TOOLS, "arbitrary_code"), {"input": {"type": "string"}}
        )
        required = get_json_schema_from_tools("required", TOOLS)
        required = json.loads(required) if isinstance(required, str) else required
        self.assertEqual(
            [x["properties"]["name"]["enum"][0] for x in required["items"]["anyOf"]],
            [
                "arbitrary_code",
                "ordinary_read",
                "arbitrary_namespace__another_code",
                "arbitrary_namespace__read",
            ],
        )
        for choice in [
            ToolChoiceCustom(type="custom", name="arbitrary_code"),
            ToolChoiceFunction(type="function", name="arbitrary_code"),
        ]:
            self.assertEqual(
                get_json_schema_from_tools(choice, TOOLS),
                {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                    "additionalProperties": False,
                },
            )

    def test_multiple_stream_call_identity_and_order(self):
        proc = SimpleStreamingEventProcessor(tools=TOOLS)
        events = []
        specs = [
            ("arbitrary_code", {"input": RAW}),
            ("ordinary_read", {"key": "00123"}),
            ("arbitrary_namespace__another_code", {"input": RAW}),
        ]
        for idx, (name, value) in enumerate(specs):
            for pos, char in enumerate(json.dumps(value)):
                dm = DeltaMessage(
                    tool_calls=[
                        DeltaToolCall(
                            index=idx,
                            function=DeltaFunctionCall(
                                name=name if pos == 0 else None, arguments=char
                            ),
                        )
                    ]
                )
                state, call = proc.resolve_target_state(dm)
                if proc.needs_transition(state, call):
                    events += proc.close_current()
                    events += proc.open(state, call)
                events += proc.emit_delta(dm, None)
        events += proc.close_current()
        added = [e for e in events if e.type == "response.output_item.added"]
        done = [e for e in events if e.type == "response.output_item.done"]
        self.assertEqual(len(added), 3)
        self.assertEqual(len(done), 3)
        self.assertEqual(
            [e.item.type for e in done],
            ["custom_tool_call", "function_call", "custom_tool_call"],
        )
        for idx, (start, end) in enumerate(zip(added, done)):
            self.assertEqual(start.output_index, idx)
            self.assertEqual(end.output_index, idx)
            self.assertEqual(start.item.id, end.item.id)
            self.assertEqual(start.item.call_id, end.item.call_id)
            if end.item.type == "custom_tool_call":
                matching = [
                    e
                    for e in events
                    if e.type == "response.custom_tool_call_input.done"
                    and e.item_id == end.item.id
                ]
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0].input, RAW)
                self.assertEqual(matching[0].output_index, idx)
                self.assertEqual(end.item.input, RAW)

    def test_required_and_forced_structural_tag(self):
        from vllm.tool_parsers.structural_tag_registry import get_model_structural_tag
        import xgrammar as xg

        info = xg.TokenizerInfo(
            [b"{", b"}", b"<tool_call>", b"<eos>"],
            vocab_type=xg.VocabType.RAW,
            stop_token_ids=[3],
        )
        compiler = xg.GrammarCompiler(info)
        text = '<tool_call>\n<function=arbitrary_code>\n<parameter=input>text("OK");</parameter>\n</function>\n</tool_call>'
        for choice in [
            "required",
            ToolChoiceCustom(type="custom", name="arbitrary_code"),
        ]:
            tag = get_model_structural_tag("qwen_3_coder", TOOLS, choice, False)
            compiled = compiler.compile_structural_tag(tag)
            m = xg.GrammarMatcher(compiled)
            self.assertTrue(m.accept_string(text))
            self.assertTrue(m.accept_token(3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
