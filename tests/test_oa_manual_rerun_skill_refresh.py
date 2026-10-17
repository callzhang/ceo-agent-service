import json
from types import SimpleNamespace

import pytest

from app.agent_cron.commands import (
    ServiceCommandConsumerContext,
    ServiceCommandSkillMaterial,
)
from app.agent_cron.models import ScheduledTaskSkillRef
from app.audit_web import refresh_oa_rerun_skill_snapshot


class Options:
    def list_managed_skill_options(self):
        return (
            SimpleNamespace(
                skill_id=7,
                name="stardust-oa-people-review",
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
            content=f"People rules revision {revision_id}",
        )

    def resolve_operation_skill(self, name):
        return SimpleNamespace(
            path=f"/skills/{name}/SKILL.md",
            sha256="current-operation-sha",
            content="current operation Skill body",
        )


def test_manual_oa_rerun_binds_latest_selected_skill_without_mutating_prior_input():
    prior_context = ServiceCommandConsumerContext(
        scheduled_task_id=3,
        scheduled_task_run_id=20,
        prompt="Review this OA event.",
        skill_names=("stardust-oa-people-review",),
        skill_protocol="read exact frozen Skills",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="stardust-oa-people-review", content="old people rules revision 11"
            ),
        ),
        skill_protocol_source="generated_discovery",
    )
    original = {
        "raw_payload": {
            "source_field": "retain",
            "scheduled_consumer": prior_context.to_payload(),
        },
        "message_id": "trigger-1",
    }

    refreshed_json = refresh_oa_rerun_skill_snapshot(
        json.dumps(original),
        store=SimpleNamespace(
            get_scheduled_task=lambda task_id: SimpleNamespace(
                id=task_id,
                command="scan-oa-approvals",
                prompt="Review this OA event.",
                skill_refs=(
                    ScheduledTaskSkillRef(
                        skill_source="managed",
                        skill_name="stardust-oa-people-review",
                        managed_skill_id=7,
                        managed_revision_id=11,
                        position=0,
                    ),
                ),
            )
        ),
        skill_options=Options(),
    )
    refreshed = json.loads(refreshed_json)
    new_context = ServiceCommandConsumerContext.from_payload(
        refreshed["raw_payload"]["scheduled_consumer"]
    )

    assert new_context is not None
    assert new_context.skill_materials[0].content.endswith("People rules revision 12")
    assert refreshed["raw_payload"]["source_field"] == "retain"
    assert refreshed["message_id"] == "trigger-1"
    assert prior_context.skill_materials[0].content == "old people rules revision 11"


def test_manual_oa_rerun_refuses_to_copy_a_snapshot_when_refresh_is_unavailable():
    original = json.dumps({"raw_payload": {"source_field": "retain"}})

    with pytest.raises(ValueError, match="OA rerun Skill snapshot"):
        refresh_oa_rerun_skill_snapshot(
            original,
            store=SimpleNamespace(get_scheduled_task=lambda _: None),
            skill_options=None,
        )
