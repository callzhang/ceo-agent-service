from app.agent_context import (
    AgentContextMessage,
    AgentTaskContext,
    MaterialReference,
    PriorReceipt,
)


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
    assert "评论已提交" in rendered


def test_context_contains_only_the_agreed_business_rules():
    rendered = _context().render()

    assert "current OA task owner" in rendered
    assert "internal_personnel" in rendered
    assert "HR conversation may skip counterpart identity matching" in rendered
    assert "Never expose credentials" in rendered
    assert "confidence" not in rendered
    assert "trusted target" not in rendered.casefold()


def test_context_reuses_confirmed_facts_without_reasking():
    rendered = _context().render()

    assert "do not ask the user to provide confirmed facts again" in rendered
    assert "预算已经确认" in rendered


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


def test_oa_complete_form_fields_still_require_live_detail_and_ownership_read():
    context = _oa_context(
        reference="process_instance_id=pid-1; task_id=tid-1",
        read_commands=(
            "dws oa approval detail --instance-id pid-1 --format json",
            "dws oa approval task list --instance-id pid-1 --format json",
        ),
        trigger_text="申请人 ET；金额 1000；理由 已完整填写；请审核",
    )

    rendered = context.render()

    assert "process_instance_id=pid-1" in rendered
    assert "task_id=tid-1" in rendered
    assert "dws oa approval detail" in rendered
    assert "dws oa approval task list --instance-id pid-1 --format json" in rendered
    assert "query live task ownership" in rendered
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
    assert "execute the provided read commands" in rendered
    assert "current OA task owner" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_ambiguous_candidates_require_needs_human_without_write():
    rendered = _oa_context(
        reference="pending OA candidates",
        read_commands=("dws oa approval task list --status pending --format json",),
    ).render()

    assert "multiple OA candidates remain" in rendered
    assert "return needs_human" in rendered
    assert "do not execute an approval write" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_completed_task_requires_no_approval_write():
    rendered = _oa_context(
        reference="process_instance_id=pid-completed",
        read_commands=(
            "dws oa approval detail --instance-id pid-completed --format json",
        ),
    ).render()

    assert "already completed" in rendered
    assert "return no_action" in rendered
    assert "do not execute an approval write" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_oa_not_current_user_requires_no_approval_write():
    rendered = _oa_context(
        reference="process_instance_id=pid-foreign; task_id=tid-foreign",
        read_commands=(
            "dws oa approval task list --instance-id pid-foreign --format json",
        ),
    ).render()

    assert "belongs to another user" in rendered
    assert "return needs_human" in rendered
    assert "do not execute an approval write" in rendered
    _assert_no_service_oa_resolution_fields(rendered)


def test_context_forbids_diagnosis_only_completion_for_execution_requests():
    rendered = _context().render()

    assert "execute and verify" in rendered
    assert "diagnosis-only" in rendered
    assert "needs_human or failed" in rendered


def test_context_forbids_agent_auth_commands():
    rendered = _context().render()

    assert "dws auth login" in rendered
    assert "lark auth login" in rendered
    assert "Never run authentication login, reset, or logout commands" in rendered
