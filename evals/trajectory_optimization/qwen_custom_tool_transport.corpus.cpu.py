# Loaded after integrated_framing.cpu.py definitions, using the recorded CPU loader.
from __main__ import (
    unittest,
    json,
    VALUES,
    Qwen3EngineToolParser,
    TOKENIZER,
    COMPILER,
    REQUEST,
    xml,
    build_response_output_items,
    FunctionCall,
    TOOLS,
)
import hashlib
import random
import xgrammar as xg


class Corpus(unittest.TestCase):
    def test_seeded_text_roundtrip(self):
        rng = random.Random(20261013)
        pool = [chr(i) for i in range(128)] + [
            "中",
            "文",
            "🐈",
            "𝄞",
            "\u2028",
            "\u2029",
            "\u00a0",
        ]
        values = ["".join(pool), "\n" * 40, "\\" * 40, '"' * 40]
        for _ in range(120):
            values.append(
                "".join(rng.choice(pool) for _ in range(rng.randrange(0, 240)))
            )
        # Protocol delimiters are anonymous data, never instructions for a model.
        values.extend(VALUES)
        parser = Qwen3EngineToolParser(TOKENIZER)
        grammar = COMPILER.compile_structural_tag(
            parser.get_structural_tag(REQUEST, reasoning=False)
        )
        digests = []
        for raw in values:
            with self.subTest(sha256=hashlib.sha256(raw.encode()).hexdigest()):
                text = xml("arbitrary_code", raw)
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
                digests.append(hashlib.sha256(raw.encode()).hexdigest())
        print(
            json.dumps(
                {
                    "seed": 20261013,
                    "cases": len(values),
                    "exact": len(digests),
                    "case_digest": hashlib.sha256(
                        "".join(digests).encode()
                    ).hexdigest(),
                    "scope": "generated anonymous text, no model inference",
                }
            )
        )


result = unittest.TextTestRunner(verbosity=1).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(Corpus)
)
if not result.wasSuccessful():
    raise SystemExit(1)
