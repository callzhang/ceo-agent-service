from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import hashlib

import pytest

from app.agent_cron.models import ScheduledTaskSkillRef
from app.store import AutoReplyStore
from app.web_api.scheduled_tasks import ScheduledTaskCreatePayload


NAMES = ("generic-review", "project-review", "finance-review")


def payload(prompt):
    return dict(name="Bound sources", command="produce-once", prompt=prompt,
                cron_expression="0 * * * * *", timezone_name="UTC",
                skill_refs=[dict(skill_source="operation", skill_name=n, position=i)
                            for i, n in enumerate(NAMES)])


@pytest.mark.parametrize("prompt", ["Use $generic-review and applicable business rules.",
                                   "Discover applicable task-bound rules on demand."])
def test_api_keeps_unrequested_sources_bound(prompt):
    parsed = ScheduledTaskCreatePayload.model_validate(payload(prompt))
    assert tuple(ref.skill_name for ref in parsed.skill_refs) == NAMES
    assert parsed.prompt == prompt


@pytest.mark.parametrize("prompt", ["Use $missing-review.",
                                   "Use $finance-review then $generic-review."])
def test_api_explicit_names_remain_bound_in_declared_order(prompt):
    with pytest.raises(ValueError):
        ScheduledTaskCreatePayload.model_validate(payload(prompt))


def test_store_update_preserves_available_sources_and_original_run_snapshot(tmp_path):
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    managed = store.create_managed_skill(NAMES[0], "Generic")
    prefix = "---\nname: generic-review\ndescription: Generic\nmetadata:\n  managed_by: ceo-agent-service\n---\n\n"
    revision = store.create_managed_skill_revision(managed.id, prefix + "ORIGINAL GENERIC BODY", source="settings")
    refs = (ScheduledTaskSkillRef(skill_source="managed", skill_name=NAMES[0], position=0,
                                 managed_skill_id=managed.id, managed_revision_id=revision.id),
            *(ScheduledTaskSkillRef(skill_source="operation", skill_name=n, position=i)
              for i, n in enumerate(NAMES[1:], 1)))
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    original = store.create_scheduled_task(
        name="Bound sources", command="produce-once",
        prompt="Use $generic-review, $project-review and $finance-review.",
        cron_expression="0 * * * * *", timezone_name="UTC", skill_refs=refs, now=now)
    old_run = store.create_scheduled_task_run(
        original.id, trigger_kind="manual", scheduled_for=now, now=now)
    store.create_managed_skill_revision(managed.id, prefix + "NEW GENERIC BODY", source="settings")
    updated = store.update_scheduled_task(
        original.id, expected_version=original.version,
        prompt="Use $generic-review and applicable business criteria.", now=now)
    assert updated.skill_refs == original.skill_refs
    assert updated.version == original.version + 1
    assert old_run.snapshot.prompt == original.prompt
    assert old_run.snapshot.skill_refs == original.skill_refs
    new_run = store.create_scheduled_task_run(
        updated.id, trigger_kind="manual", scheduled_for=now + timedelta(minutes=1), now=now)
    assert new_run.snapshot.prompt == updated.prompt
    assert new_run.snapshot.skill_refs == original.skill_refs

    from app.agent_cron.context import ScheduledAgentContextBuilder

    class Options:
        def resolve_managed_skill_revision(self, *, skill_id, revision_id, skill_name):
            found = store.get_managed_skill_revision(revision_id)
            assert found.skill_id == skill_id
            return found

        def resolve_operation_skill(self, name):
            content = "FROZEN BODY " + name
            return SimpleNamespace(path=tmp_path / (name + ".md"), content=content,
                                   sha256=hashlib.sha256(content.encode()).hexdigest())

    context = ScheduledAgentContextBuilder(Options()).build_consumer_context(new_run)
    assert context.skill_names == NAMES
    assert len(context.skill_materials) == 3
    assert "ORIGINAL GENERIC BODY" in context.skill_materials[0].content
    assert "NEW GENERIC BODY" not in context.skill_materials[0].content
    assert context.skill_materials[1].content.endswith("FROZEN BODY project-review")
    assert context.skill_materials[2].content.endswith("FROZEN BODY finance-review")
    from app.business_skills import render_task_skill_discovery

    assert context.skill_protocol == render_task_skill_discovery(
        NAMES, catalog=(), frozen_names=NAMES,
        frozen_hashes={material.name: hashlib.sha256(material.content.encode()).hexdigest()
                       for material in context.skill_materials},
    )
    assert "Available Task Skill Sources" in context.role_prompt()
    assert "FROZEN BODY" not in context.role_prompt()
    from app.prompt_composition import compose_consumer_task_assembly, load_prompt_configuration

    for continuation in ("", "CURRENT CORRECTION"):
        assembly = compose_consumer_task_assembly(
            load_prompt_configuration(create_missing=False), task_context="CURRENT FACTS",
            scheduled_prompt=context.role_prompt(), skill_protocol=context.skill_protocol,
            skill_protocol_is_custom=False, skill_names=context.skill_names,
            frozen_skill_names=context.skill_names, skill_catalog=(), continuation=continuation,
            frozen_skill_hashes={material.name: hashlib.sha256(material.content.encode()).hexdigest()
                                 for material in context.skill_materials},
        )
        assert assembly.text.count("## Available Task Skill Sources") == 1
        assert assembly.text.count(context.prompt) == 1
        for name in NAMES:
            assert assembly.text.count(f"`{name}`") == 1


@pytest.mark.parametrize("prompt", ["Use $missing-review.",
                                   "Use $finance-review then $generic-review."])
def test_store_explicit_names_remain_bound_in_declared_order(tmp_path, prompt):
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    refs = tuple(ScheduledTaskSkillRef(skill_source="operation", skill_name=n, position=i)
                 for i, n in enumerate(NAMES))
    with pytest.raises(ValueError):
        store.create_scheduled_task(name="Bad", command="produce-once", prompt=prompt,
            cron_expression="0 * * * * *", timezone_name="UTC", skill_refs=refs,
            now=datetime(2026, 10, 13, 12, tzinfo=UTC))


def test_available_source_names_still_cannot_be_ambiguous():
    data = payload("Use $generic-review.")
    data["skill_refs"][2]["skill_name"] = "project-review"
    with pytest.raises(ValueError):
        ScheduledTaskCreatePayload.model_validate(data)
