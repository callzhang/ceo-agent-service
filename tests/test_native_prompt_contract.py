import json

import pytest

from app.agent_wire_contracts import ConsumerAgentWireResult, AuditAgentWireResult
from app.consumer_agent import consumer_developer_sections, audit_developer_sections
from app.native_agent_output import (
    native_developer_sections,
    native_canonical_constraints,
)
from app.prompt_composition import PromptConfiguration, render_prompt_sections

CONFIG = PromptConfiguration("", "", "{{task_context}}", "")


def unsupported_nodes(value, path=""):
    result = {}
    if isinstance(value, dict):
        assertions = {
            key: item
            for key, item in value.items()
            if key
            in {
                "oneOf",
                "allOf",
                "if",
                "then",
                "else",
                "not",
                "dependentRequired",
                "dependentSchemas",
            }
        }
        if assertions:
            result[path] = assertions
        for key, item in value.items():
            if key not in assertions:
                result.update(
                    unsupported_nodes(
                        item, path + "/" + key.replace("~", "~0").replace("/", "~1")
                    )
                )
    elif isinstance(value, list):
        for i, item in enumerate(value):
            result.update(unsupported_nodes(item, path + "/" + str(i)))
    return result


@pytest.mark.parametrize(
    "role,model",
    [("consumer", ConsumerAgentWireResult), ("audit", AuditAgentWireResult)],
)
def test_native_residual_retains_every_unsupported_assertion_exactly(role, model):
    assert native_canonical_constraints(role) == unsupported_nodes(
        model.model_json_schema()
    )


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_native_developer_uses_structure_channel_and_retains_other_sources(role):
    original = (
        consumer_developer_sections(
            runtime_context="", work_profile="PROFILE", prompt_configuration=CONFIG
        )
        if role == "consumer"
        else audit_developer_sections(
            "RULE",
            runtime_context="",
            work_profile="PROFILE",
            prompt_configuration=CONFIG,
        )
    )
    compact = native_developer_sections(original, role)
    assert len(compact) == len(original)
    for before, after in zip(original, compact):
        if before.name != "输出契约":
            assert after == before
        else:
            assert after.source == "Codex 原生结构与补充业务约束"
            assert "## Pydantic Wire Contract" not in after.text
            residual = native_canonical_constraints(role)
            if residual:
                assert (
                    json.loads(
                        after.text.split(
                            "\nCanonical constraints by schema JSON Pointer:\n", 1
                        )[1]
                    )
                    == residual
                )
    assert len(render_prompt_sections(compact, placement="developer")) < len(
        render_prompt_sections(original, placement="developer")
    )
    # Compaction never mutates the assembly used by Claude/Friday.
    assert "## Pydantic Wire Contract" in next(
        s.text for s in original if s.name == "输出契约"
    )


def test_partial_receipts_never_drop_unlisted_developer_text():
    from app.native_agent_output import native_developer_assembly

    sections = consumer_developer_sections(
        runtime_context="", work_profile="", prompt_configuration=CONFIG
    )
    contract = next(s for s in sections if s.name == "输出契约")
    original = "Unlisted prefix\n\n" + contract.text + "\n\nUnlisted suffix"
    assembly = native_developer_assembly(original, (contract,), "consumer")
    assert assembly.text.startswith("Unlisted prefix\n\n")
    assert assembly.text.endswith("\n\nUnlisted suffix")
    assert contract.text not in assembly.text
    assert assembly.sections[0].text in assembly.text


def test_unmatched_section_receipt_cannot_claim_compaction():
    from app.native_agent_output import native_developer_assembly

    sections = consumer_developer_sections(
        runtime_context="", work_profile="", prompt_configuration=CONFIG
    )
    contract = next(s for s in sections if s.name == "输出契约")
    assembly = native_developer_assembly(
        "Unlisted complete developer", (contract,), "consumer"
    )
    assert assembly.text == "Unlisted complete developer"
    assert assembly.sections == (contract,)


def test_repeated_schema_body_does_not_relabel_a_different_source():
    from app.native_agent_output import native_developer_assembly

    sections = consumer_developer_sections(
        runtime_context="", work_profile="", prompt_configuration=CONFIG
    )
    contract = next(s for s in sections if s.name == "输出契约")
    original = "Quoted copy:\n" + contract.text + "\nActual contract:\n" + contract.text
    assembly = native_developer_assembly(original, (contract,), "consumer")
    assert assembly.text == original
    assert assembly.sections == (contract,)
