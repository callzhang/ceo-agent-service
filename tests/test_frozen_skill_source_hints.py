"""Per-turn discovery identifies the exact body available from task lookup."""
import hashlib
import json

import pytest

from app.business_skills import frozen_task_skill_binding, render_task_skill_discovery
from app.agent_cron.commands import ServiceCommandConsumerContext, ServiceCommandSkillMaterial


def materials(content):
    context = ServiceCommandConsumerContext(
        scheduled_task_id=11, scheduled_task_run_id=29, prompt="Review document",
        skill_names=("ceo-document-review",), skill_protocol="Read selected task Skill on demand.",
        skill_materials=(ServiceCommandSkillMaterial("ceo-document-review", content),),
    )
    trigger = json.dumps({"raw_payload": {"scheduled_consumer": context.to_payload()}})
    return frozen_task_skill_binding(trigger).materials


@pytest.mark.parametrize("content", ["完整冻结正文\n", "Changed frozen body\n"])
def test_discovery_exposes_frozen_lookup_hash_without_body(content):
    rendered = render_task_skill_discovery(
        ("ceo-document-review",), catalog=(),
        frozen_names=("ceo-document-review",), frozen_materials=materials(content),
    )
    assert hashlib.sha256(content.encode()).hexdigest() in rendered
    assert content.strip() not in rendered
    assert "read_task_skill" in rendered


def test_unselected_or_ambiguous_material_cannot_supply_a_source_hint():
    body = "Frozen body"
    sources = materials(body)
    sha = hashlib.sha256(body.encode()).hexdigest()
    for selected, frozen, saved in [
        ((), (), sources),
        (("ceo-document-review",), ("ceo-document-review",), sources * 2),
    ]:
        rendered = render_task_skill_discovery(
            selected, catalog=(), frozen_names=frozen, frozen_materials=saved,
        )
        assert sha not in rendered


def test_consumer_task_and_source_section_share_the_frozen_fingerprint(tmp_path, monkeypatch):
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    user = tmp_path / "user.md"
    user.write_text("{{task_context}}")
    monkeypatch.setenv("CEO_USER_PROMPT_TEMPLATE_PATH", str(user))
    body = "Body available only through task lookup"
    assembly = compose_consumer_task_assembly(
        load_prompt_configuration(create_missing=False), task_context="Current facts",
        skill_names=("ceo-document-review",), frozen_skill_names=("ceo-document-review",),
        frozen_skill_materials=materials(body),
    )
    sha = hashlib.sha256(body.encode()).hexdigest()
    assert assembly.text.count(sha) == 1
    assert next(s for s in assembly.sections if s.name == "任务 Skill 入口").text.count(sha) == 1
    assert body not in assembly.text


def test_frozen_discovery_is_an_entry_not_a_repeated_read_workflow():
    content = "Frozen business procedure"
    rendered = render_task_skill_discovery(
        ("ceo-document-review",), catalog=(), frozen_names=("ceo-document-review",),
        frozen_materials=materials(content),
    )
    assert "ceo-document-review" in rendered
    assert hashlib.sha256(content.encode()).hexdigest() in rendered
    assert "agent_cli.read_task_skill(name)" in rendered
    assert (
        "reuse the complete body only when it remains visible earlier in this same "
        "session and its SHA-256 is listed here and matches"
    ) in rendered
    assert "otherwise read the exact frozen body" in rendered
    assert "When a selected Skill names another" not in rendered


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_discovery_hash_matches_exact_task_reader_receipt(tmp_path, role):
    from app.agent_cli import build_role_server
    from tests.test_frozen_task_skill_discovery import _context, _task_db

    content = "## Managed Skill\nrevision_id: 41\nsha256: inner-body-hash\n\n完整正文\n"
    material = ServiceCommandSkillMaterial("ceo-calendar-invite", content)
    payload = _context(material).to_payload()
    db = tmp_path / "task.sqlite3"
    _task_db(db, scheduled_consumer=payload)
    server = build_role_server(role, task_id=7, db_path=db, execution_generation="first")
    receipt = server._tool_manager.get_tool("read_task_skill").fn(name=material.name)
    binding = frozen_task_skill_binding(json.dumps({"raw_payload": {"scheduled_consumer": payload}}))
    rendered = render_task_skill_discovery(
        binding.declared_names, catalog=(), frozen_names=binding.declared_names,
        frozen_materials=binding.materials,
    )
    assert receipt["sha256"] in rendered
    assert receipt["content"] == content
    assert "inner-body-hash" not in rendered


def test_settings_current_guidance_displays_frozen_hash_without_rewriting_history(tmp_path):
    from app.agent_runtime_config import load_runtime_config
    from app.prompt_preview import current_prompt_preview, historical_prompt_preview
    from app.store import AutoReplyStore
    from tests.test_prompt_preview import task_and_run

    content = "EXACT SAVED SKILL BODY"
    context = ServiceCommandConsumerContext(
        scheduled_task_id=11, scheduled_task_run_id=29, prompt="Review document",
        skill_names=("ceo-document-review",), skill_protocol="Read task Skill",
        skill_materials=(ServiceCommandSkillMaterial("ceo-document-review", content),),
    )
    store = AutoReplyStore(tmp_path / "preview.sqlite3")
    task, run = task_and_run(store, trigger_message_json=json.dumps({
        "raw_payload": {"scheduled_consumer": context.to_payload()},
    }))
    store.append_agent_run_event(run.id, {
        "type": "runtime.prompt", "role": "consumer", "task_prompt": "Old complete input",
        "rendered_at": "2026-10-13T10:00:00-07:00",
        "runtime_attempt_id": 81, "runtime_kind": "codex_cli", "route_name": "codex_oauth",
        "model": "fixture-model", "task_id": task.id, "developer_instructions": "Old instructions",
        "submitted_input": "Old complete input", "runtime_context": "Old context", "redacted": False,
        "invocation_facts": {"skill_names": ["ceo-document-review"]},
    }, owner="test")
    current = current_prompt_preview(store, role="consumer", config=load_runtime_config({}), task_id=task.id)
    historical = historical_prompt_preview(store, run_id=run.id)
    sha = hashlib.sha256(content.encode()).hexdigest()
    assert sha in current["task_prompt"]
    assert content not in current["task_prompt"]
    assert historical["task_prompt"] == "Old complete input"
    assert sha not in historical["task_prompt"]


def test_task_discovery_metadata_does_not_change_stable_conversation_fingerprint(monkeypatch):
    from app.consumer_agent import consumer_wire_contract_hash
    from app.prompt_composition import load_prompt_configuration

    configuration = load_prompt_configuration(create_missing=False)
    before = consumer_wire_contract_hash(prompt_configuration=configuration)

    def never_fingerprint_per_task_discovery(*args, **kwargs):
        raise AssertionError('per-task Skill metadata must not fingerprint the stable conversation')

    monkeypatch.setattr('app.business_skills.render_task_skill_discovery', never_fingerprint_per_task_discovery)
    assert consumer_wire_contract_hash(prompt_configuration=configuration) == before
