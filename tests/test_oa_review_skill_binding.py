from __future__ import annotations

from types import SimpleNamespace

from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.agent_cron.context import ScheduledAgentContextBuilder
from app.agent_cron.models import ScheduledTaskSkillRef


class Options:
    def list_managed_skill_options(self):
        return (
            SimpleNamespace(
                skill_id=7,
                name="stardust-oa-project-review",
                revisions=(
                    SimpleNamespace(revision_id=11, revision_number=1, available=True),
                    SimpleNamespace(revision_id=12, revision_number=2, available=True),
                ),
            ),
        )

    def resolve_managed_skill_revision(self, *, skill_id, revision_id, skill_name):
        return SimpleNamespace(
            id=revision_id,
            skill_id=skill_id,
            sha256=f"sha-{revision_id}",
            content=f"project rules revision {revision_id}",
        )

    def resolve_operation_skill(self, name):
        return SimpleNamespace(
            path=f"/skills/{name}/SKILL.md",
            sha256="current-operation-sha",
            content="current operation Skill body",
        )


def test_new_oa_review_round_refreshes_selected_skills_without_rewriting_old_input():
    old_context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review the new OA event.",
        skill_names=("stardust-oa-project-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="stardust-oa-project-review",
                content="old project rules revision 11",
            ),
        ),
        skill_protocol_source="generated_discovery",
    )
    scheduled_task = SimpleNamespace(
        id=3,
        command="scan-oa-approvals",
        prompt="Review the new OA event.",
        skill_refs=(
            ScheduledTaskSkillRef(
                skill_source="managed",
                skill_name="stardust-oa-project-review",
                managed_skill_id=7,
                managed_revision_id=11,
                position=0,
            ),
        ),
    )

    refreshed = ScheduledAgentContextBuilder(Options()).refresh_oa_review_context(
        old_context, scheduled_task
    )

    assert refreshed.scheduled_task_run_id == old_context.scheduled_task_run_id
    assert refreshed.skill_materials[0].content.endswith("project rules revision 12")
    assert old_context.skill_materials[0].content == "old project rules revision 11"
