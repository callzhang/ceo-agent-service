from types import SimpleNamespace

from evals.trajectory_optimization.meeting_skill_scope_native import _checks


def test_business_eval_rejects_mention_of_unassigned_implementer():
    def decision(mentions):
        return SimpleNamespace(
            action="send",
            sensitive_private_message=None,
            audience_scope="business",
            target=SimpleNamespace(conversation_id="synthetic-group-1"),
            topics=[SimpleNamespace(state="aligned")],
            final_message="先进行 10% 灰度。",
            message_title="上线范围",
            mention_names=mentions,
        )

    assert _checks("business_alignment", decision(("Avery",)))["only_assigned_owners"]
    assert not _checks("business_alignment", decision(("Alex", "Avery")))[
        "only_assigned_owners"
    ]
