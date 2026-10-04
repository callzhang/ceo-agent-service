import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, TaskAttentionProjectionReceipt, WorkItem


def _independent_project_result(**changes):
    return {
        "project_decisions": [
            {
                "registration": {
                    "title": "客户验收项目",
                    "reason": "会议明确项目目标",
                    "source_excerpt": "会议决定启动客户验收项目",
                    "authority": "meeting_decision",
                },
                "context": {
                    "goal": "完成客户验收",
                    "scope": "本轮交付",
                    "overall_owner": None,
                    "responsibilities": [],
                    "facts": [],
                },
                "evidence": [
                    {
                        "source_ref": "meeting:42",
                        "source_excerpt": "会议决定启动客户验收项目",
                    }
                ],
                "reason": "保存项目定义，不需要制造一个任务",
            }
        ],
        "task_decisions": [],
        "project_assessments": [
            {
                "project_decision_index": 0,
                "project_title": "客户验收项目",
                "outcome": "not_needed",
                "reason": "本轮按计划推进，无需 CEO 关注",
                "assessment_basis": "current_observation",
                "evidence": [
                    {"source_ref": "meeting:42", "source_excerpt": "本轮按计划推进"}
                ],
            }
        ],
        "update_summary": "项目有进展，本轮没有新的具体任务",
        **changes,
    }


def test_independent_project_envelope_requires_all_three_lists():
    valid = {
        "project_decisions": [],
        "task_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有业务项目或具体任务",
    }
    for name in ("project_decisions", "task_decisions", "project_assessments"):
        with pytest.raises(ValidationError, match=name):
            TaskAgentDecision.model_validate(
                {key: value for key, value in valid.items() if key != name}
            )
    assert TaskAgentDecision.model_validate(valid).project_decisions == []


def test_independent_project_can_be_registered_and_assessed_without_tasks():
    result = TaskAgentDecision.model_validate(_independent_project_result())
    assert result.task_decisions == []
    assert result.project_assessments[0].project_decision_index == 0
    assert result.project_decisions[0].context.goal == "完成客户验收"


def test_independent_project_selector_indexes_projects_not_tasks():
    payload = _independent_project_result()
    payload["task_decisions"] = [
        _decision(
            project={"project_decision_index": 0},
            project_link_evidence=[
                {"source_ref": "message:42", "source_excerpt": "美国报价可以研究一下"}
            ],
        )
    ]
    result = TaskAgentDecision.model_validate(payload)
    assert result.task_decisions[0].project.project_decision_index == 0
    payload["task_decisions"][0]["project"] = {"project_decision_index": 1}
    with pytest.raises(ValidationError, match="out of bounds"):
        TaskAgentDecision.model_validate(payload)
    payload["task_decisions"][0]["project"] = {
        "anchor_id": 3,
        "project_decision_index": 0,
    }
    with pytest.raises(ValidationError, match="exactly one"):
        TaskAgentDecision.model_validate(payload)


@pytest.mark.parametrize(
    "legacy_field", ["project_proposal", "project_link_proposal", "attention_proposal"]
)
def test_independent_project_rejects_old_nested_current_wire(legacy_field):
    payload = _independent_project_result(
        task_decisions=[_decision(**{legacy_field: {}})]
    )
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(
        item["loc"] == ("task_decisions", 0, legacy_field)
        and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


def test_independent_project_attention_belongs_to_assessment_without_task_carrier():
    payload = _independent_project_result()
    assessment = payload["project_assessments"][0]
    assessment.update(outcome="needs_attention", reason="验收延期影响交付")
    assessment["attention_proposal"] = {
        key: value for key, value in _attention().items() if key != "anchor_id"
    }
    result = TaskAgentDecision.model_validate(payload)
    assert result.project_assessments[0].attention_proposal.category == "watch"
    assert result.project_assessments[0].decision_indexes == []


def test_independent_project_cannot_repeat_registered_title_as_unknown_clue():
    payload = _independent_project_result()
    clue = {
        **payload["project_assessments"][0],
        "outcome": "insufficient_evidence",
        "reason": "身份未知",
    }
    clue.pop("project_decision_index")
    payload["project_assessments"].append(clue)
    with pytest.raises(ValidationError, match="exact.*title"):
        TaskAgentDecision.model_validate(payload)


def test_independent_project_new_task_support_requires_project_selector():
    payload = _independent_project_result(task_decisions=[_decision()])
    payload["project_assessments"][0]["decision_indexes"] = [0]
    with pytest.raises(ValidationError, match="new supporting Task"):
        TaskAgentDecision.model_validate(payload)


def test_independent_project_suggestion_cannot_claim_assignment_metadata():
    payload = _independent_project_result(
        task_decisions=[
            _decision(
                project={"project_decision_index": 0},
                owner_kind="individual",
                owner_relation="explicit_assignment",
                suggestion={
                    "reason": "按项目需要建议研究",
                    "basis_evidence": [
                        {
                            "source_ref": "message:42",
                            "source_excerpt": "美国报价可以研究一下",
                        }
                    ],
                },
            )
        ]
    )
    with pytest.raises(ValidationError, match="actual owner"):
        TaskAgentDecision.model_validate(payload)


@pytest.mark.parametrize(
    "model_name", ["TaskAttentionProposal", "TaskProjectAssessment"]
)
def test_assessment_basis_schema_requires_supplied_originals_for_explicit_comparison(
    model_name,
):
    import app.task_models as models

    description = getattr(models, model_name).model_json_schema()["properties"][
        "assessment_basis"
    ]["description"]
    assert "When the current source explicitly compares earlier facts" in description
    assert "matching original Signals are delivered" in description
    assert (
        "verify that comparison against the originals and use historical_comparison"
        in description
    )
    assert "If the originals are unavailable" in description


def test_projection_receipt_preserves_per_assessment_application_readback():
    receipt = TaskAttentionProjectionReceipt.model_validate(
        {
            "project_decisions": [],
            "status": "completed",
            "source_type": "reply_attempt",
            "task_decision_count": 1,
            "project_link_count": 1,
            "proposal_count": 1,
            "project_assessments": [
                {
                    "assessment_index": 0,
                    "anchor_id": 4,
                    "task_ids": [7],
                    "attention_id": 9,
                    "status": "applied",
                    "reason": "Attention proposal applied.",
                    "evidence": [
                        {
                            "source_ref": "message:42",
                            "source_excerpt": "验收延期",
                            "signal_id": 11,
                            "source_time": "2026-10-02T09:00:00Z",
                            "source_link": "https://example.test/message/42",
                        }
                    ],
                }
            ],
        }
    )

    assert receipt.project_assessments[0].task_ids == [7]
    assert receipt.project_assessments[0].evidence[0].signal_id == 11


def _decision(**changes):
    return {
        "action": "record_candidate",
        "transition": "none",
        "source_excerpt": "美国报价可以研究一下",
        "source_ref": "message:42",
        "title": "研究美国报价",
        "missing_evidence": ["deliverable", "authority"],
        **changes,
    }


def _attention(**changes):
    return {
        "category": "watch",
        "title": "交付风险",
        "why_attention": "影响交付",
        "current_state": "验收延期",
        "ceo_action": "观察验收",
        "assessment_basis": "current_observation",
        "material_trigger": "risk_escalation",
        "evidence": [{"source_ref": "message:42", "source_excerpt": "验收延期"}],
        **changes,
    }


def _project_assessment(**changes):
    return {
        "project_title": "示例项目",
        "outcome": "not_needed",
        "reason": "本轮只有正常进展，没有需要 CEO 关注的重大影响。",
        "assessment_basis": "current_observation",
        "anchor_id": 3,
        "decision_indexes": [],
        "task_ids": [7],
        "evidence": [
            {
                "source_ref": "message:42",
                "source_excerpt": "当前按计划完成第一阶段验收",
            }
        ],
        **changes,
    }


def _linked_decision(anchor_id=3, **changes):
    return _decision(
        project={"anchor_id": anchor_id},
        project_link_evidence=[
            {"source_ref": "message:42", "source_excerpt": "美国报价可以研究一下"}
        ],
        **changes,
    )


def _registration_decision(title="示例项目", **changes):
    return {
        "registration": {
            "title": title,
            "reason": "会议明确立项",
            "source_excerpt": f"会议决定启动{title}",
            "authority": "meeting_decision",
        },
        "evidence": [
            {"source_ref": "message:42", "source_excerpt": f"会议决定启动{title}"}
        ],
        "reason": "登记独立项目定义",
        **changes,
    }


def test_project_assessments_is_required_without_a_default():
    with pytest.raises(ValidationError, match="project_assessments"):
        TaskAgentDecision.model_validate(
            {"project_decisions": [], "task_decisions": []}
        )

    schema = TaskAgentDecision.model_json_schema()
    assert "project_assessments" in schema["required"]
    assert "default" not in schema["properties"]["project_assessments"]
    assessment_description = schema["properties"]["project_assessments"]["description"]
    assert (
        "current source and current Tasks' confirmed Project links"
        in assessment_description
    )
    assert "not limited to structured selectors" in assessment_description


def test_project_assessments_explicit_empty_collection_is_allowed():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
        }
    )
    assert decision.project_assessments == []


def test_empty_project_assessments_requires_nonblank_no_project_summary():
    with pytest.raises(ValidationError, match="update_summary"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [],
                "update_summary": " ",
            }
        )


# Task 5 replaces the old per-Task Project/Attention copy matrix.
# These cases preserve identity/coverage/conflict rules at the independent envelope;
# canonical registration -> persisted-anchor equality is verified against SQLite.
@pytest.mark.parametrize(
    "projects,tasks",
    [
        ([], [_linked_decision()]),
        ([_registration_decision()], []),
        (
            [
                {
                    "anchor_id": 3,
                    "reason": "更新项目",
                    "evidence": [
                        {"source_ref": "message:42", "source_excerpt": "当前进展"}
                    ],
                }
            ],
            [],
        ),
    ],
)
def test_empty_project_assessments_rejects_relevant_structured_project(projects, tasks):
    with pytest.raises(ValidationError, match="project assessment"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": projects,
                "task_decisions": tasks,
                "project_assessments": [],
                "update_summary": "无项目",
            }
        )


@pytest.mark.parametrize(
    "change,problem",
    [
        ({"decision_indexes": [1]}, "out of bounds"),
        ({"decision_indexes": [1]}, "skip"),
    ],
)
def test_project_assessment_rejects_out_of_bounds_or_skip_support(change, problem):
    tasks = [_linked_decision()]
    if problem == "skip":
        tasks.append({"action": "skip", "transition": "none", "skip_reason": "无工作"})
    with pytest.raises(ValidationError, match=problem):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": tasks,
                "project_assessments": [_project_assessment(**change)],
            }
        )


def test_project_assessment_project_index_selects_only_top_level_projects():
    with pytest.raises(ValidationError, match="project_decision_index.*out of bounds"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [_decision()],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        project_decision_index=0,
                        decision_indexes=[0],
                        outcome="insufficient_evidence",
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "task_project", [{"anchor_id": 4}, {"project_decision_index": 0}]
)
def test_known_anchor_assessment_rejects_supporting_decision_for_another_project(
    task_project,
):
    with pytest.raises(ValidationError, match="supporting Task selector"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [_registration_decision("其他项目")],
                "task_decisions": [_decision(project=task_project)],
                "project_assessments": [
                    _project_assessment(anchor_id=3, decision_indexes=[0])
                ],
            }
        )


def test_exact_current_project_title_is_assessed_once_across_two_tasks():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [_registration_decision(), _registration_decision()],
            "task_decisions": [
                _decision(project={"project_decision_index": index}) for index in (0, 1)
            ],
            "project_assessments": [
                _project_assessment(
                    anchor_id=None,
                    project_decision_index=1,
                    decision_indexes=[0, 1],
                    task_ids=[],
                )
            ],
        }
    )
    assert len(result.project_assessments) == 1
    assert result.project_assessments[0].project_decision_index == 1


def test_known_project_index_and_direct_anchor_share_one_judgment():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "anchor_id": 3,
                    "reason": "当前更新",
                    "evidence": [
                        {"source_ref": "message:42", "source_excerpt": "当前更新"}
                    ],
                }
            ],
            "task_decisions": [
                _decision(project={"project_decision_index": 0}),
                _linked_decision(),
            ],
            "project_assessments": [
                _project_assessment(anchor_id=3, decision_indexes=[0, 1])
            ],
        }
    )
    assert len(result.project_assessments) == 1


def test_registration_title_must_exactly_match_assessment_title():
    with pytest.raises(ValidationError, match="project_title"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [_registration_decision()],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        project_decision_index=0,
                        project_title="示例项目二期",
                    )
                ],
            }
        )


def test_one_assessment_cannot_mix_two_current_project_titles():
    with pytest.raises(ValidationError, match="supporting Task selector"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [
                    _registration_decision("项目A"),
                    _registration_decision("项目B"),
                ],
                "task_decisions": [
                    _decision(project={"project_decision_index": index})
                    for index in (0, 1)
                ],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        project_decision_index=0,
                        project_title="项目A",
                        decision_indexes=[0, 1],
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "assessment",
    [
        _project_assessment(),
        _project_assessment(anchor_id=None, project_decision_index=0),
    ],
)
def test_duplicate_known_anchor_or_exact_current_title_assessment_is_rejected(
    assessment,
):
    with pytest.raises(ValidationError, match="exactly one"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [_registration_decision()],
                "task_decisions": [],
                "project_assessments": [assessment, assessment],
            }
        )


def test_unknown_project_clue_may_reference_only_unselected_current_decisions():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [_decision()],
            "project_assessments": [
                _project_assessment(
                    anchor_id=None,
                    outcome="insufficient_evidence",
                    reason="项目身份未确定",
                    decision_indexes=[0],
                    task_ids=[],
                )
            ],
        }
    )
    assert result.project_assessments[0].outcome == "insufficient_evidence"
    with pytest.raises(ValidationError, match="supporting Task selector"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [_linked_decision()],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        outcome="insufficient_evidence",
                        decision_indexes=[0],
                        task_ids=[],
                    )
                ],
            }
        )


def test_needs_attention_requires_existing_card_or_own_current_proposal():
    with pytest.raises(
        ValidationError, match="existing_attention_id.*attention_proposal"
    ):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(outcome="needs_attention", task_ids=[])
                ],
            }
        )
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention", existing_attention_id=11, task_ids=[]
                )
            ],
        }
    )
    assert result.project_assessments[0].existing_attention_id == 11


@pytest.mark.parametrize("outcome", ["not_needed", "insufficient_evidence"])
def test_negative_assessment_cannot_carry_attention_proposal(outcome):
    with pytest.raises(ValidationError, match="needs_attention"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        outcome=outcome, attention_proposal=_attention()
                    )
                ],
            }
        )


def test_project_assessment_accepts_known_project_negative_with_current_quote():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [_linked_decision()],
            "project_assessments": [_project_assessment(decision_indexes=[0])],
        }
    )
    assert result.project_assessments[0].task_ids == [7]


def test_project_assessment_accepts_new_registration_position_without_task_carrier():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [_registration_decision()],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    anchor_id=None,
                    project_decision_index=0,
                    outcome="needs_attention",
                    task_ids=[],
                    attention_proposal=_attention(),
                )
            ],
        }
    )
    assert result.project_assessments[0].project_decision_index == 0


def test_attention_allows_old_proof_claim_alongside_current_proposal():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention",
                    existing_attention_id=11,
                    attention_proposal=_attention(),
                )
            ],
        }
    )
    assert result.project_assessments[0].existing_attention_id == 11
    assert result.project_assessments[0].attention_proposal is not None


def test_attention_and_assessment_cannot_disagree_about_historical_basis():
    with pytest.raises(ValidationError, match="same assessment_basis"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        outcome="needs_attention",
                        assessment_basis="historical_comparison",
                        evidence=[
                            {"source_ref": "message:42", "source_excerpt": "本周延期"},
                            {
                                "signal_id": 7,
                                "source_ref": "report:7",
                                "source_excerpt": "上周正常",
                            },
                        ],
                        attention_proposal=_attention(),
                    )
                ],
            }
        )


def test_project_assessment_allows_unknown_project_clue_only_when_evidence_is_insufficient():
    assessment = _project_assessment(
        anchor_id=None,
        outcome="insufficient_evidence",
        reason="来源提到示例项目，但无法确认对应正式 Project 或本轮登记决定。",
    )
    assert (
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [assessment],
            }
        )
        .project_assessments[0]
        .anchor_id
        is None
    )


@pytest.mark.parametrize("outcome", ["needs_attention", "not_needed"])
def test_project_assessment_rejects_positive_or_negative_outcome_for_unknown_project(
    outcome,
):
    with pytest.raises(ValidationError, match="unknown Project"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        outcome=outcome,
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"project_title": " "},
        {"reason": "\t"},
    ],
)
def test_project_assessment_rejects_blank_title_or_reason(change):
    with pytest.raises(ValidationError, match="title and reason"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [_project_assessment(**change)],
            }
        )


def test_project_assessment_rejects_both_project_identity_references():
    with pytest.raises(ValidationError, match="anchor_id or project_decision_index"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [_project_assessment(project_decision_index=0)],
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"decision_indexes": [0, 0]},
        {"task_ids": [7, 7]},
    ],
)
def test_project_assessment_rejects_duplicate_task_references(change):
    with pytest.raises(ValidationError, match="duplicate"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [_project_assessment(**change)],
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"anchor_id": -1},
        {"anchor_id": True},
        {"anchor_id": None, "project_decision_index": -1},
        {"anchor_id": None, "project_decision_index": True},
        {"existing_attention_id": -1},
        {"decision_indexes": [-1]},
        {"decision_indexes": [True]},
        {"task_ids": [0]},
        {"task_ids": [True]},
    ],
)
def test_project_assessment_rejects_negative_or_boolean_references(change):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [_project_assessment(**change)],
            }
        )


def test_project_assessment_rejects_boolean_existing_attention_id_as_type_error():
    with pytest.raises(ValidationError) as exc_info:
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        outcome="needs_attention",
                        existing_attention_id=True,
                    )
                ],
            }
        )

    assert ("project_assessments", 0, "existing_attention_id") in {
        error["loc"] for error in exc_info.value.errors()
    }


@pytest.mark.parametrize("outcome", ["not_needed", "insufficient_evidence"])
def test_existing_attention_card_requires_needs_attention_outcome(outcome):
    with pytest.raises(
        ValidationError, match="existing_attention_id requires needs_attention"
    ):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        outcome=outcome,
                        existing_attention_id=11,
                    )
                ],
            }
        )


def test_existing_attention_card_is_allowed_for_needs_attention_outcome():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention",
                    existing_attention_id=11,
                )
            ],
        }
    )
    assert decision.project_assessments[0].existing_attention_id == 11


@pytest.mark.parametrize(
    "basis,evidence",
    [
        (
            "current_observation",
            [
                {
                    "signal_id": 7,
                    "source_ref": "report:7",
                    "source_excerpt": "上周正常",
                },
            ],
        ),
        (
            "historical_comparison",
            [
                {"source_ref": "message:42", "source_excerpt": "本周延期"},
            ],
        ),
        (
            "historical_comparison",
            [
                {
                    "signal_id": 7,
                    "source_ref": "report:7",
                    "source_excerpt": "上周正常",
                },
            ],
        ),
    ],
)
def test_project_assessment_basis_requires_expected_citation_shape(basis, evidence):
    with pytest.raises(ValidationError, match=f"{basis} requires"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        assessment_basis=basis,
                        evidence=evidence,
                    )
                ],
            }
        )


def test_project_assessment_historical_basis_accepts_current_and_persisted_quotes():
    evidence = [
        {"source_ref": "message:42", "source_excerpt": "本周延期"},
        {"signal_id": 7, "source_ref": "report:7", "source_excerpt": "上周正常"},
    ]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    assessment_basis="historical_comparison",
                    evidence=evidence,
                )
            ],
        }
    )
    assert len(decision.project_assessments[0].evidence) == 2


def test_attention_requires_explicit_assessment_basis():
    from app.task_models import TaskAttentionProposal

    payload = _attention()
    payload.pop("assessment_basis")
    with pytest.raises(ValidationError, match="assessment_basis"):
        TaskAttentionProposal.model_validate(payload)


@pytest.mark.parametrize(
    "evidence",
    [
        [{"source_ref": "message:42", "source_excerpt": "验收延期"}],
        [{"signal_id": 7, "source_ref": "report:7", "source_excerpt": "原计划延期"}],
    ],
)
def test_historical_comparison_requires_current_and_persisted_citations(evidence):
    from app.task_models import TaskAttentionProposal

    with pytest.raises(ValidationError, match="historical_comparison requires"):
        TaskAttentionProposal.model_validate(
            _attention(assessment_basis="historical_comparison", evidence=evidence)
        )


@pytest.mark.parametrize("action", ["record_candidate", "create_task"])
def test_new_action_attention_requires_matching_existing_project_link(action):
    with pytest.raises(ValidationError, match="new supporting Task"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        outcome="needs_attention",
                        decision_indexes=[0],
                        attention_proposal=_attention(),
                    )
                ],
                "task_decisions": [
                    _decision(
                        action=action,
                        formal_basis="meeting_action_item"
                        if action == "create_task"
                        else None,
                    )
                ],
            }
        )


def test_existing_task_attention_reuses_link_without_new_proposal():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention",
                    task_ids=[1],
                    attention_proposal=_attention(),
                )
            ],
        }
    )
    assert decision.task_decisions == []


def test_historical_comparison_accepts_two_original_citation_shapes():
    from app.task_models import TaskAttentionProposal

    payload = _attention(assessment_basis="historical_comparison")
    payload["evidence"].append(
        {"signal_id": 7, "source_ref": "report:7", "source_excerpt": "原计划延期"}
    )
    proposal = TaskAttentionProposal.model_validate(payload)
    assert proposal.assessment_basis == "historical_comparison"
    schema = TaskAttentionProposal.model_json_schema()
    assert "assessment_basis" in schema["required"]
    assert "default" not in schema["properties"]["assessment_basis"]


def test_current_observation_can_include_original_history_as_corroboration():
    from app.task_models import TaskAttentionProposal

    payload = _attention()
    payload["evidence"].append(
        {"signal_id": 7, "source_ref": "report:7", "source_excerpt": "原计划延期"}
    )
    assert (
        TaskAttentionProposal.model_validate(payload).assessment_basis
        == "current_observation"
    )


@pytest.mark.parametrize("direction", ["current_to_related", "related_to_current"])
def test_relation_proposal_names_only_existing_related_task_and_direction(direction):
    from app.task_models import TaskRelationProposal

    relation = TaskRelationProposal.model_validate(
        {
            "related_task_id": 7,
            "direction": direction,
            "relation_type": "supports",
            "reason": "当前行动支持相关交付",
        }
    )
    assert relation.endpoints(12) == (
        (12, 7) if direction == "current_to_related" else (7, 12)
    )


def test_update_relation_shape_rejects_current_id_as_related_target():
    with pytest.raises(
        ValidationError, match="relation requires a different related Task"
    ):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        action="update_task",
                        transition="update_fields",
                        task_id=7,
                        relation_proposals=[
                            {
                                "related_task_id": 7,
                                "direction": "current_to_related",
                                "relation_type": "related_to",
                            }
                        ],
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"from_task_id": 1, "to_task_id": 2, "relation_type": "related_to"},
        {
            "related_task_id": 0,
            "direction": "current_to_related",
            "relation_type": "related_to",
        },
        {
            "related_task_id": True,
            "direction": "current_to_related",
            "relation_type": "related_to",
        },
        {"related_task_id": 7, "relation_type": "related_to"},
    ],
)
def test_relation_proposal_rejects_old_unbound_or_missing_endpoint_shape(payload):
    from app.task_models import TaskRelationProposal

    with pytest.raises(ValidationError):
        TaskRelationProposal.model_validate(payload)


@pytest.mark.parametrize("action", ["record_candidate", "create_task"])
def test_new_action_attention_accepts_explicit_project_link(action):
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention",
                    decision_indexes=[0],
                    task_ids=[],
                    attention_proposal=_attention(),
                )
            ],
            "task_decisions": [
                _decision(
                    action=action,
                    formal_basis="meeting_action_item"
                    if action == "create_task"
                    else None,
                    project={"anchor_id": 3},
                    project_link_evidence=[
                        {
                            "source_ref": "message:42",
                            "source_excerpt": "复核示例项目验收计划",
                        }
                    ],
                )
            ],
        }
    )
    assert decision.task_decisions[0].project.anchor_id == 3


def test_work_item_does_not_require_project_name():
    item = WorkItem.model_validate(
        {
            "source": {"type": "reply_attempt", "ref": "42"},
            "summary": "美国报价可以研究一下",
            "context": {"source_conversation_kind": "group"},
        }
    )
    assert "project_name" not in item.model_dump()


def test_existing_project_link_has_separate_selector_and_original_evidence():
    proof = {"source_ref": "message:42", "source_excerpt": "复核示例项目验收计划。"}
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [_project_assessment(decision_indexes=[0])],
            "task_decisions": [
                _decision(project={"anchor_id": 3}, project_link_evidence=[proof])
            ],
        }
    )
    assert decision.task_decisions[0].project.anchor_id == 3
    assert (
        decision.task_decisions[0]
        .project_link_evidence[0]
        .model_dump(exclude_none=True)
        == proof
    )


@pytest.mark.parametrize(
    "selector,evidence",
    [
        ({"anchor_id": 0}, [{"source_ref": "message:42", "source_excerpt": "原文"}]),
        ({"anchor_id": True}, [{"source_ref": "message:42", "source_excerpt": "原文"}]),
        ({"anchor_id": 3}, [{"source_ref": " ", "source_excerpt": "原文"}]),
        ({"anchor_id": 3}, [{"source_ref": "message:42", "source_excerpt": " "}]),
    ],
)
def test_existing_project_link_requires_valid_identity_and_quote(selector, evidence):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [_project_assessment()],
                "task_decisions": [
                    _decision(project=selector, project_link_evidence=evidence)
                ],
            }
        )


def test_project_link_proof_without_project_selector_is_rejected():
    with pytest.raises(ValidationError, match="Project selector"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        project_link_evidence=[
                            {"source_ref": "message:42", "source_excerpt": "原文"}
                        ]
                    )
                ],
            }
        )


def test_task_selector_cannot_combine_registered_anchor_and_current_registration():
    with pytest.raises(ValidationError, match="exactly one"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [_registration_decision()],
                "project_assessments": [_project_assessment()],
                "task_decisions": [
                    _decision(project={"anchor_id": 3, "project_decision_index": 0})
                ],
            }
        )


def test_current_project_proof_can_supplement_a_task_with_earlier_provenance():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [_project_assessment()],
            "task_decisions": [
                _decision(
                    evidence_origin="memory",
                    source_description="历史出处",
                    project={"anchor_id": 3},
                    project_link_evidence=[
                        {
                            "signal_id": 9,
                            "source_ref": "meeting:original",
                            "source_excerpt": "实际项目证明",
                        }
                    ],
                )
            ],
        }
    )
    assert result.task_decisions[0].project_link_evidence[0].signal_id == 9


def test_task_project_and_assessment_must_reference_same_anchor():
    with pytest.raises(ValidationError, match="supporting Task selector"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=4,
                        outcome="needs_attention",
                        decision_indexes=[0],
                        attention_proposal=_attention(),
                    )
                ],
                "task_decisions": [_linked_decision(anchor_id=3)],
            }
        )


def test_vague_source_is_candidate_with_missing_evidence():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [_decision()],
        }
    )
    assert decision.task_decisions[0].action == "record_candidate"
    assert decision.task_decisions[0].formal_basis is None


def test_one_task_agent_contract_supports_task_and_completion_transitions():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "task_decisions": [_decision()],
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": 17,
                    "completion_evidence": {
                        "source": "message:42",
                        "reason": "Owner confirmed delivery",
                        "description": "The requested quote was submitted.",
                        "completed_at": "2026-09-23T10:00:00+08:00",
                        "checked_at": "2026-09-23T10:05:00+08:00",
                    },
                }
            ],
            "search_trace": [
                {
                    "source_kind": "dingtalk_message",
                    "result": "Owner confirmed delivery",
                    "source_ref": "message:42",
                    "reason": "Direct completion confirmation",
                    "source_created_at": "2026-09-23T10:00:00+08:00",
                    "retrieved_at": "2026-09-23T10:05:00+08:00",
                    "audit_call_ids": ["call-17"],
                }
            ],
            "update_summary": "Recorded a candidate and confirmed an existing TODO.",
        }
    )

    assert decision.task_decisions[0].action == "record_candidate"
    assert decision.todo_changes[0].todo_id == 17
    assert decision.search_trace[0].audit_call_ids == ["call-17"]


def test_completion_operation_targets_one_business_task_without_legacy_todo_id():
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "todo_changes": [
                {
                    "action": "close",
                    "business_task_id": 42,
                    "completion_evidence": {"source": "message:42", "reason": "done"},
                }
            ],
        }
    )
    assert decision.todo_changes[0].business_task_id == 42
    assert decision.todo_changes[0].todo_id is None


def test_one_source_can_emit_several_source_grounded_tasks():
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                _decision(
                    source_excerpt="请王明提交报价",
                    title="提交报价",
                    action="create_task",
                    formal_basis="explicit_assignment",
                    owner_user_id="wang",
                    owner_name="王明",
                    owner_evidence={
                        "source_ref": "message:42",
                        "excerpt": "请王明提交报价",
                    },
                    missing_evidence=[],
                    date_evidence=[
                        {
                            "kind": "requested_deadline_at",
                            "value": "2026-09-25T18:00:00+08:00",
                            "source_ref": "message:42",
                            "source_excerpt": "周五前",
                            "actor_name": "指派人",
                        }
                    ],
                ),
                _decision(source_excerpt="安排客户演示", title="安排演示"),
            ],
        }
    )
    assert len(result.task_decisions) == 2
    assert result.task_decisions[0].formal_basis.value == "explicit_assignment"
    assert result.task_decisions[0].date_evidence[0].kind == "requested_deadline_at"
    assert result.task_decisions[0].project is None


def test_acceptance_has_dedicated_transition_and_existing_task_id():
    accepted = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                _decision(
                    action="update_task",
                    transition="apply_acceptance",
                    task_id=12,
                    source_excerpt="我接受报价任务，周五交第一版",
                    missing_evidence=[],
                    acceptance_polarity="accepted",
                    acceptance_target_signal_id=55,
                )
            ],
        }
    ).task_decisions[0]
    assert accepted.task_id == 12
    assert accepted.transition == "apply_acceptance"


@pytest.mark.parametrize("polarity", ["declined", "ambiguous", None])
def test_nonaccepted_polarity_cannot_transition_commitment(polarity):
    with pytest.raises(
        ValidationError, match="apply_acceptance requires explicit accepted polarity"
    ):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        action="update_task",
                        transition="apply_acceptance",
                        task_id=12,
                        source_excerpt="我不确定",
                        missing_evidence=[],
                        acceptance_polarity=polarity,
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "create_project"},
        {"action": "create_task", "commitment_status": "accepted"},
        {"action": "create_task", "deadline_at": "2026-09-25"},
        {"action": "create_task", "project": {"title": "美国客户"}},
        {"action": "create_task", "project_name": "美国客户"},
    ],
)
def test_old_project_and_model_authored_commitment_fields_are_rejected(payload):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [_decision(**payload)],
            }
        )


def test_merge_proposal_requires_ids_and_structured_identity_evidence():
    valid = _decision(
        action="update_task",
        transition="merge_identity",
        task_id=8,
        target_task_id=9,
        identity_proposal={
            "source_task_id": 8,
            "target_task_id": 9,
            "identity_evidence": {
                "basis": "same_external_task_id",
                "source_signal_id": 21,
                "target_signal_id": 22,
            },
        },
    )
    TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [valid],
        }
    )
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        action="update_task",
                        transition="merge_identity",
                        task_id=8,
                        identity_proposal={"reason": "sounds similar"},
                    )
                ],
            }
        )


def test_project_candidate_requires_existing_cluster_and_registration_requires_authority():
    TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有正式项目",
            "task_decisions": [
                _decision(
                    project_candidate_proposal={
                        "cluster_id": 3,
                        "title": "美国客户成交",
                        "reason": "相关任务持续",
                    }
                )
            ],
        }
    )
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        project_candidate_proposal={
                            "title": "美国客户成交",
                            "reason": "相关任务持续",
                        }
                    )
                ],
            }
        )
    TaskAgentDecision.model_validate(
        {
            "project_decisions": [_registration_decision("美国客户成交")],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    anchor_id=None,
                    project_decision_index=0,
                    project_title="美国客户成交",
                    task_ids=[],
                )
            ],
        }
    )
    proposal = _registration_decision("美国客户成交")
    proposal["registration"].pop("authority")
    with pytest.raises(ValidationError, match="authority"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [proposal],
                "task_decisions": [],
                "project_assessments": [],
            }
        )
    with pytest.raises(ValidationError, match="formal Project"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        project={"anchor_id": 3},
                        project_candidate_proposal={
                            "cluster_id": 3,
                            "title": "美国客户成交",
                            "reason": "相关任务持续",
                        },
                    )
                ],
            }
        )


def test_attention_proposal_requires_material_trigger_and_confirmed_project():
    from app.task_models import TaskAttentionProposal

    payload = _attention()
    payload.pop("material_trigger")
    with pytest.raises(ValidationError, match="material_trigger"):
        TaskAttentionProposal.model_validate(payload)
    with pytest.raises(ValidationError, match="unknown Project"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [
                    _project_assessment(
                        anchor_id=None,
                        outcome="needs_attention",
                        attention_proposal=_attention(),
                    )
                ],
            }
        )
    result = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                _project_assessment(
                    outcome="needs_attention", attention_proposal=_attention()
                )
            ],
        }
    )
    assert result.project_assessments[0].anchor_id == 3


def test_required_and_optional_null_contract():
    model = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [_decision(owner_evidence=None, date_evidence=None)],
        }
    )
    assert model.task_decisions[0].owner_evidence == {}
    assert model.task_decisions[0].date_evidence == []
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [_decision(action=None)],
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"source_excerpt": ""},
        {"source_ref": ""},
        {"action": "create_task", "formal_basis": None},
        {"action": "record_candidate", "formal_basis": "explicit_assignment"},
        {
            "action": "create_task",
            "formal_basis": "explicit_assignment",
            "owner_name": "王明",
            "owner_evidence": {},
        },
    ],
)
def test_source_grounding_and_formality_shape(change):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [_decision(**change)],
            }
        )


def test_typed_date_requires_provenance_and_merge_target_must_match():
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        date_evidence=[
                            {"kind": "estimated_deadline_at", "value": "2026-09-30"}
                        ]
                    )
                ],
            }
        )
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        action="update_task",
                        transition="merge_identity",
                        task_id=8,
                        target_task_id=10,
                        identity_proposal={
                            "source_task_id": 8,
                            "target_task_id": 9,
                            "identity_evidence": {
                                "basis": "same_external_task_id",
                                "source_signal_id": 21,
                                "target_signal_id": 22,
                            },
                        },
                    )
                ],
            }
        )


@pytest.mark.parametrize(
    "identity_evidence",
    [
        {"same_external_task_id": True},
        {
            "same_deliverable": True,
            "same_owner": True,
            "same_context": True,
            "compatible_time_window": True,
        },
        {"basis": "same_external_task_id", "source_signal_id": 21},
        {
            "basis": "same_external_task_id",
            "source_signal_id": 0,
            "target_signal_id": 22,
        },
    ],
)
def test_merge_identity_cannot_use_unverifiable_agent_assertions(identity_evidence):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        action="update_task",
                        transition="merge_identity",
                        task_id=8,
                        target_task_id=9,
                        identity_proposal={
                            "source_task_id": 8,
                            "target_task_id": 9,
                            "identity_evidence": identity_evidence,
                        },
                    )
                ],
            }
        )


@pytest.mark.parametrize("actor", [{}, {"actor_name": "  ", "actor_user_id": ""}])
def test_committed_deadline_requires_nonblank_actor_provenance(actor):
    with pytest.raises(ValidationError, match="committed deadline"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "task_decisions": [
                    _decision(
                        date_evidence=[
                            {
                                "kind": "committed_deadline_at",
                                "value": "2026-09-30T18:00:00+08:00",
                                "source_ref": "message:42",
                                "source_excerpt": "我周三交付",
                                **actor,
                            }
                        ]
                    )
                ],
            }
        )
