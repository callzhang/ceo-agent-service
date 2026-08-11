from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    AuditTurnContext,
    ManualRerunInstruction,
    MaterialReference,
    PriorReceipt,
)
from app.agent_contracts import ConsumerProposal
from app.agent_skill_usage import LoadedSkillReceipt


def _context(
    *,
    materials: tuple[MaterialReference, ...] = (),
    trigger_text: str = "请审核这个材料",
) -> AgentTaskContext:
    return AgentTaskContext(
        task_id=7,
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid",
        trigger_sender="ET",
        trigger_text=trigger_text,
        trigger_create_time="2026-07-28 12:00:00",
        messages=(
            AgentContextMessage(
                message_id="earlier",
                sender="ET",
                text="预算已经确认。",
                create_time="2026-07-28 11:58:00",
            ),
        ),
        materials=materials,
        prior_receipts=(
            PriorReceipt(
                receipt_id="receipt-1",
                operation="comment",
                summary="评论已提交",
                completed=True,
            ),
        ),
    )


def test_context_renders_reference_and_command_without_resolved_body():
    context = _context(
        materials=(
            MaterialReference(
                kind="dingtalk_doc",
                reference="https://alidocs.dingtalk.com/i/nodes/abc",
                source_message_id="mid",
                read_commands=(
                    "dws doc info --node https://alidocs.dingtalk.com/i/nodes/abc --format json",
                ),
            ),
        )
    )

    rendered = context.render()

    assert "dws doc info" in rendered
    assert "resolved_content" not in rendered
    assert "Trusted" not in rendered
    assert "Safe prior execution receipts" in rendered
    assert "评论已提交" in rendered


def test_context_contains_only_the_agreed_business_rules():
    rendered = _context().render()

    assert "execute the provided live DWS commands" in rendered
    assert "do not invent a `--task-id` argument" in rendered
    assert "internal_personnel" in rendered
    assert "HR conversation may skip counterpart identity matching" in rendered
    assert "Never expose credentials" in rendered
    assert "confidence" not in rendered
    assert "trusted target" not in rendered.casefold()


def test_context_reuses_confirmed_facts_without_reasking():
    rendered = _context().render()

    assert "do not ask the user to provide confirmed facts again" in rendered
    assert "预算已经确认" in rendered


def test_context_gives_each_agent_turn_an_explicit_execution_time():
    context = _context(trigger_text="现在出发吃饭吗？")

    consumer = context.render(current_time="2026-07-28 14:15:00 +0800")
    audit = AuditTurnContext(
        task=context,
        proposal_revision=0,
        operation_id="op-time-sensitive",
        proposal=ConsumerProposal.model_validate(
            {
                "objective": "Clarify whether the immediate plan is still active.",
                "actions": [
                    {
                        "description": "Ask whether to leave now.",
                        "capability": "agent_cli.dws",
                        "operation": "chat message send",
                        "target": {"open_dingtalk_id": "recipient-1"},
                        "payload": {
                            "argv": [
                                "dws",
                                "chat",
                                "message",
                                "send",
                                "--open-dingtalk-id",
                                "recipient-1",
                                "--text",
                                "现在出发吗？",
                            ]
                        },
                        "expected_verification": "Read back the sent message.",
                    }
                ],
                "sourced_facts": [
                    {
                        "assertion": "The trigger proposed leaving now.",
                        "references": ["message:mid"],
                    }
                ],
                "authored_judgment": "Ask a timing clarification.",
            }
        ),
        audit_rules="Reject stale time-sensitive actions.",
    ).render(current_time="2026-07-28 14:16:00 +0800")

    assert "Current turn execution time" in consumer
    assert "2026-07-28 14:15:00 +0800" in consumer
    assert "2026-07-28 14:16:00 +0800" in audit
    assert '"create_time": "2026-07-28 12:00:00"' in audit


def test_audit_context_renders_verified_consumer_skill_receipts_as_json():
    receipt = LoadedSkillReceipt(
        name="business-review",
        path="/Users/derek/.agents/skills/business-review/SKILL.md",
        sha256="a" * 64,
    )
    rendered = AuditTurnContext(
        task=_context(),
        proposal_revision=0,
        operation_id="op-skill",
        proposal=ConsumerProposal.model_validate(
            {
                "objective": "Review",
                "actions": [
                    {
                        "description": "Send reviewed result.",
                        "capability": "agent_cli.dws",
                        "operation": "chat message send",
                        "target": {"group": "cid"},
                        "payload": {
                            "argv": [
                                "dws",
                                "chat",
                                "message",
                                "send",
                                "--group",
                                "cid",
                                "--text",
                                "reviewed",
                                "--yes",
                            ]
                        },
                        "expected_verification": "Read back the message.",
                    }
                ],
                "sourced_facts": [],
                "authored_judgment": "Review using the applicable Skill.",
            }
        ),
        audit_rules="",
        consumer_skills=(receipt,),
    ).render()

    assert "Verified Skills read by Consumer A" in rendered
    assert '"name": "business-review"' in rendered
    assert f'"sha256": "{"a" * 64}"' in rendered
    assert '"path": "/Users/derek/.agents/skills/business-review/SKILL.md"' in rendered


def test_agent_rules_require_elapsed_time_review_for_time_sensitive_actions():
    rendered = _context(trigger_text="现在出发吗？").render(
        current_time="2026-07-28 14:15:00 +0800"
    )

    assert "elapsed time" in rendered
    assert "time-sensitive" in rendered
    assert "return no_action" in rendered


def test_context_renders_only_minimal_manual_review_instruction():
    original = _context()
    context = AgentTaskContext(
        **{
            **original.__dict__,
            "manual_rerun": ManualRerunInstruction(
                source_attempt_id=42,
                reviewer_feedback="需要先核对材料，再执行。",
                suggested_reply_text="请按材料中的最新数字回复。",
            ),
        }
    )

    rendered = context.render()
    manual_section = rendered.split("Manual rerun instruction", 1)[1]

    assert '"source_attempt_id": 42' in manual_section
    assert "需要先核对材料，再执行。" in manual_section
    assert "请按材料中的最新数字回复。" in manual_section
    assert "trigger_sender_user_id" not in manual_section


def _oa_context(
    *,
    reference: str,
    read_commands: tuple[str, ...],
    trigger_text: str = "请审核这个审批",
) -> AgentTaskContext:
    return _context(
        trigger_text=trigger_text,
        materials=(
            MaterialReference(
                kind="dingtalk_oa",
                reference=reference,
                source_message_id="mid",
                read_commands=read_commands,
            ),
        )
    )


def _assert_no_service_oa_resolution_fields(rendered: str) -> None:
    for field in (
        "resolved_content",
        "trusted_target",
        "applicant_match",
        "title_match",
        "form_body",
        "target_user_id",
    ):
        assert field not in rendered


def test_oa_complete_form_fields_still_require_live_detail_read():
    context = _oa_context(
        reference="process_instance_id=pid-1; task_id=tid-1",
        read_commands=(
            "dws oa approval detail --instance-id pid-1 --format json",
            "dws oa approval tasks --instance-id pid-1 --format json",
        ),
        trigger_text="申请人 ET；金额 1000；理由 已完整填写；请审核",
    )

    rendered = context.render()

    assert "process_instance_id=pid-1" in rendered
    assert "task_id=tid-1" in rendered
    assert "dws oa approval detail" in rendered
    assert "dws oa approval tasks --instance-id pid-1 --format json" in rendered
    assert "execute the provided live DWS commands" in rendered
    assert "do not select by applicant or title similarity" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_instance_id_only_still_requires_agent_live_detail_read():
    command = "dws oa approval detail --instance-id pid-only --format json"
    rendered = _oa_context(
        reference="process_instance_id=pid-only",
        read_commands=(command,),
    ).render()

    assert "process_instance_id=pid-only" in rendered
    assert command in rendered
    assert "Execute the provided read commands" in rendered
    assert "agent_cli.execute_reviewed_read" in rendered
    assert "local CLI credential store" in rendered
    assert "never emit `proposed_actions`" in rendered
    assert "execute the provided live DWS commands" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_ambiguous_candidates_require_needs_human():
    rendered = _oa_context(
        reference="pending OA candidates",
        read_commands=("dws oa approval tasks --instance-id pid-ambiguous --format json",),
    ).render()

    assert "multiple OA candidates remain" in rendered
    assert "return needs_human" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_completed_task_requires_no_action():
    rendered = _oa_context(
        reference="process_instance_id=pid-completed",
        read_commands=(
            "dws oa approval detail --instance-id pid-completed --format json",
        ),
    ).render()

    assert "already completed" in rendered
    assert "applicant notification is confirmed" in rendered
    assert "return no_action" in rendered
    assert "propose only the missing notification" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_lets_live_api_enforce_task_ownership():
    rendered = _oa_context(
        reference="process_instance_id=pid-foreign; task_id=tid-foreign",
        read_commands=(
            "dws oa approval tasks --instance-id pid-foreign --format json",
        ),
    ).render()

    assert "Let the OA API enforce task ownership" in rendered
    assert "belongs to another user" not in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_context_forbids_diagnosis_only_completion_for_execution_requests():
    rendered = _context().render()

    assert "execute the requested action" in rendered
    assert "diagnosis-only" in rendered
    assert "needs_human or failed" in rendered


def test_context_requires_three_independent_cases_for_shared_infrastructure_change():
    rendered = _context(trigger_text="一个用户反馈线上地址打不开，请修复").render()

    assert "Do not change shared deployment entry points" in rendered
    assert "at least three independently confirmed affected cases" in rendered
    assert "Repeated probes from one machine or network are one case" in rendered
    assert "leave shared configuration unchanged and return needs_human" in rendered


def test_context_allows_exactly_authorized_shared_infrastructure_change():
    rendered = _context(trigger_text="请把生产域名切换到已确认的新域名").render()

    assert "explicit current authorization for that exact change" in rendered


def test_context_forbids_agent_auth_commands():
    rendered = _context().render()

    assert "dws auth login" in rendered
    assert "lark auth login" in rendered
    assert "Never run authentication login, reset, or logout commands" in rendered


def test_consumer_context_is_read_only_and_reuses_supplied_facts():
    rendered = _context().render()

    assert "Consumer Agent A" in rendered
    assert "read-only" in rendered
    assert "no external write" in rendered
    assert "already available" in rendered
    assert "Raw material references and exact read commands" in rendered
    assert "authoritative read path" in rendered
    assert "Do not substitute a similar command" in rendered


def test_consumer_context_turns_recoverable_evidence_gap_into_clarifying_proposal():
    rendered = _context(trigger_text="这个候选人怎么样？").render()

    assert "send one concrete clarifying question" in rendered
    assert "Do not return needs_human for missing evidence" in rendered
    assert "irreducible personal or management decision" in rendered


def test_oa_context_requires_autonomous_review_before_escalating_to_derek():
    rendered = _oa_context(
        reference="process_instance_id=pid-1; task_id=tid-1",
        read_commands=(
            "dws oa approval detail --instance-id pid-1 --format json",
            "dws oa approval tasks --instance-id pid-1 --format json",
        ),
        trigger_text="请审批这个申请",
    ).render()

    assert "review each OA instance to a business outcome" in rendered
    assert "propose the approval action" in rendered
    assert "comment on that OA instance" in rendered
    assert "notify the actual applicant" in rendered
    assert "Do not ask Derek to choose between continuing and clarifying" in rendered
    assert "Only an irreducible management choice" in rendered


def test_single_chat_context_requires_direct_message_target():
    group_context = _context()
    rendered = AgentTaskContext(
        **{**group_context.__dict__, "single_chat": True}
    ).render()

    assert "For a single chat, address the verified participant directly" in rendered
    assert "never pass a single-chat conversation ID to a group-send command" in rendered
    assert "For a group chat, use the supplied group conversation ID" in rendered


def test_audit_context_preserves_complete_proposal_and_raw_oa_commands():
    task = _oa_context(
        reference="process_instance_id=pid-1; task_id=tid-1",
        read_commands=(
            "dws oa approval detail --instance-id pid-1 --format json",
        ),
    )
    proposal = ConsumerProposal.model_validate(
        {
            "objective": "Review the live approval",
            "actions": [
                {
                    "description": "Comment on the approval",
                    "capability": "agent_cli.dws",
                    "operation": "oa approval comment",
                    "target": {"process_instance_id": "pid-1"},
                    "payload": {"remark": "请补充材料。"},
                    "expected_verification": "Read the OA records",
                }
            ],
            "sourced_facts": [
                {
                    "assertion": "The trigger asks for review.",
                    "references": ["message:mid"],
                }
            ],
            "authored_judgment": "Request the missing material.",
        }
    )

    rendered = AuditTurnContext(
        task=task,
        proposal_revision=2,
        operation_id="op-2",
        proposal=proposal,
        audit_rules="Only publish supported facts.",
    ).render()

    assert "Audit Agent B" in rendered
    assert '"proposal_revision": 2' in rendered
    assert '"operation_id": "op-2"' in rendered
    assert "请补充材料。" in rendered
    assert "dws oa approval detail --instance-id pid-1 --format json" in rendered
    assert "Execute the named operation" in rendered
    assert "payload unchanged" in rendered
    assert "return revision_required" in rendered
    assert "Reject a group-send candidate for a single-chat task" in rendered
    assert "OA factual gap" in rendered
    assert "exact OA comment and applicant notification" in rendered
    assert "Effective Audit Rules" not in rendered
    assert "Only publish supported facts." not in rendered
    _assert_no_service_oa_resolution_fields(rendered)
