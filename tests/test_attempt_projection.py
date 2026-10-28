from types import SimpleNamespace

import pytest

from app.attempt_projection import project_attempt_status


def test_latest_generation_run_overrides_stale_attempt_status():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="done", execution_generation="g2")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
        SimpleNamespace(id=2, execution_generation="g2", turn_attempt=0, proposal_revision=0, status="completed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "done"


def test_current_unresolved_execution_uncertainty_overrides_terminal_task():
    attempt = SimpleNamespace(send_status="done", resolved_at="")
    task = SimpleNamespace(status="done", execution_generation="g1")
    run = SimpleNamespace(
        id=2, execution_generation="g1", turn_attempt=0, proposal_revision=0,
        role="audit", status="completed",
    )
    assert project_attempt_status(
        attempt, task, [run], current_execution_status="uncertain"
    ) == "uncertain"


def test_resolved_attempt_keeps_resolution_projection_despite_old_uncertainty():
    attempt = SimpleNamespace(send_status="skipped", resolved_at="verified-return", agent_run_id=1)
    task = SimpleNamespace(status="done", execution_generation="g1")
    assert project_attempt_status(
        attempt,
        task,
        [SimpleNamespace(
            id=1,
            execution_generation="g1",
            turn_attempt=0,
            proposal_revision=0,
            status="completed",
        )],
        current_execution_status="uncertain",
    ) == "skipped"


def test_last_effective_run_wins_within_current_generation():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="processing", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="completed"),
        SimpleNamespace(id=2, execution_generation="g1", turn_attempt=1, proposal_revision=0, status="failed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "failed"


def test_no_task_preserves_historical_fallback():
    attempt = SimpleNamespace(send_status="pending")
    assert project_attempt_status(attempt, None, []) == "pending"


def test_closed_task_outranks_a_run_that_failed_inside_it():
    """Attempt 9136 read `failed` on its page and `skipped` in the list.

    Reply task 383933 closed `skipped` on 2026-09-15 because a later weekly OKR
    report succeeded, while its last run stayed `failed` from 2026-09-12. The
    detail page called it failed indefinitely, and every check that walks the
    History list saw the stored `skipped`, so nobody could reconcile the two.
    """
    attempt = SimpleNamespace(send_status="skipped")
    task = SimpleNamespace(status="skipped", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
    ]

    assert project_attempt_status(attempt, task, runs) == "skipped"


def test_a_live_task_still_reports_its_failing_run():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="processing", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
    ]

    assert project_attempt_status(attempt, task, runs) == "failed"


@pytest.mark.parametrize("generation,conversation,task_id,error,expected", [
    ("g2", "current", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "skipped"),
    ("g1", "current", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "other", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 2, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 1, "expired_after_target_open_retries", "done"),
    ("g2", "current", 1, "note: superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 1, "superseded_by_principal_reply:", "done"),
])
def test_only_current_formally_superseded_wechat_delivery_retains_skipped(
    generation, conversation, task_id, error, expected,
):
    attempt = SimpleNamespace(send_status="skipped", channel="wechat", conversation_id="current")
    task = SimpleNamespace(id=1, status="done", execution_generation="g2")
    delivery = SimpleNamespace(task_id=task_id, execution_generation=generation,
                               conversation_id=conversation, status="skipped", error=error)
    assert project_attempt_status(attempt, task, [], delivery=delivery) == expected


@pytest.mark.parametrize("linked_run_id, generation, expected", [
    (2, "g2", "skipped"),
    (1, "g1", "done"),
    (1, "g2", "done"),
])
def test_done_task_preserves_only_current_terminal_attempt_skip(linked_run_id, generation, expected):
    attempt = SimpleNamespace(send_status="skipped", agent_run_id=linked_run_id)
    task = SimpleNamespace(status="done", execution_generation="g2")
    runs = [
        SimpleNamespace(id=1, execution_generation=generation, turn_attempt=0, proposal_revision=0, status="completed"),
        SimpleNamespace(id=2, execution_generation="g2", turn_attempt=0, proposal_revision=1, status="completed"),
    ]
    assert project_attempt_status(attempt, task, runs) == expected


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_closed_attempt_links_do_not_request_processing(status):
    from app.attempt_rerun_presentation import RerunPresentation
    from app.web_api.attempts import _action_links

    attempt = SimpleNamespace(id=1, send_status=status, channel="dingtalk")
    links = _action_links(attempt, [], None, None, None, RerunPresentation())
    assert links["terminal"] is True
    assert links["action_label"] == "无需操作"


def test_later_revision_skip_outranks_an_earlier_revision_retry():
    attempt = SimpleNamespace(send_status="skipped", agent_run_id=3)
    task = SimpleNamespace(status="done", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", proposal_revision=0, turn_attempt=1, status="completed"),
        SimpleNamespace(id=2, execution_generation="g1", proposal_revision=1, turn_attempt=0, status="completed"),
        SimpleNamespace(id=3, execution_generation="g1", proposal_revision=1, turn_attempt=0, status="completed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "skipped"


def test_completed_audit_follows_its_retried_consumer_within_one_revision():
    attempt = SimpleNamespace(send_status="skipped", agent_run_id=2)
    task = SimpleNamespace(status="done", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, role="consumer", execution_generation="g1", proposal_revision=0, turn_attempt=2, status="completed"),
        SimpleNamespace(id=2, role="audit", execution_generation="g1", proposal_revision=0, turn_attempt=0, status="completed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "skipped"


def test_expired_oa_source_projects_uncertain_follow_up_as_skipped_without_claiming_delivery():
    import json

    resolution = json.dumps({
        "business_id": "business-1",
        "checked_at": "2026-10-15T10:37:10+00:00",
        "closure_reason": "source process terminated; follow-up is no longer actionable",
        "current_principal_task_ids": [],
        "external_write_count": 0,
        "historical_chat_delivery": "unverified; original uncertain delivery remains unchanged",
        "native_and_openapi_business_id_match": True,
        "original_message_id": "trigger-1",
        "original_oa_task_id": "task-1",
        "original_oa_task_status": "CANCELED",
        "process_finished_at": "2026-10-14 17:44:07",
        "process_instance_id": "process-1",
        "process_status": "TERMINATED",
        "source": "exact original DingTalk message and native OA detail/OpenAPI readback",
    })
    attempt = SimpleNamespace(
        send_status="failed",
        send_error="external_action_uncertain",
        resolved_at="2026-10-15T10:37:10+00:00",
        resolution=resolution,
        channel="dingtalk",
        trigger_message_id="trigger-1",
    )
    task = SimpleNamespace(status="done", execution_generation="g1")
    run = SimpleNamespace(
        id=2, execution_generation="g1", turn_attempt=0, proposal_revision=1,
        role="audit", status="completed",
    )

    assert project_attempt_status(
        attempt, task, [run], current_execution_status="uncertain"
    ) == "skipped"


def test_expired_oa_projection_requires_exact_source_and_zero_external_writes():
    import json

    attempt = SimpleNamespace(
        send_status="failed",
        send_error="external_action_uncertain",
        resolved_at="2026-10-15T10:37:10+00:00",
        resolution=json.dumps({
            "external_write_count": 1,
            "native_and_openapi_business_id_match": True,
            "original_message_id": "trigger-1",
            "original_oa_task_id": "task-1",
            "original_oa_task_status": "CANCELED",
            "process_instance_id": "process-1",
            "process_status": "TERMINATED",
            "source": "exact original DingTalk message and native OA detail/OpenAPI readback",
        }),
        channel="dingtalk",
        trigger_message_id="trigger-1",
    )
    task = SimpleNamespace(status="done", execution_generation="g1")

    assert project_attempt_status(
        attempt, task, [], current_execution_status="uncertain"
    ) == "uncertain"


def test_terminal_oa_skip_message_does_not_claim_delivery():
    import json

    from app.web_api.attempts import _status_message

    attempt = SimpleNamespace(
        send_status="skipped",
        send_error="external_action_uncertain",
        resolved_at="2026-10-15T10:37:10+00:00",
        resolution=json.dumps({
            "current_principal_task_ids": [],
            "external_write_count": 0,
            "historical_chat_delivery": "unverified; original uncertain delivery remains unchanged",
            "native_and_openapi_business_id_match": True,
            "original_message_id": "trigger-1",
            "original_oa_task_id": "task-1",
            "original_oa_task_status": "CANCELED",
            "process_instance_id": "process-1",
            "process_status": "TERMINATED",
            "source": "exact original DingTalk message and native OA detail/OpenAPI readback",
        }),
        channel="dingtalk",
        trigger_message_id="trigger-1",
    )

    message, requires_decision = _status_message(attempt, None)

    assert "已跳过" in message
    assert "仍未核实" in message
    assert not requires_decision


def test_execution_uncertainty_does_not_overwrite_terminal_skip_explanation():
    import json

    from app.web_api.attempts import _execution_status_message

    attempt = SimpleNamespace(
        send_status="skipped",
        send_error="external_action_uncertain",
        resolved_at="2026-10-15T10:37:10+00:00",
        resolution=json.dumps({
            "current_principal_task_ids": [],
            "external_write_count": 0,
            "historical_chat_delivery": "unverified; original uncertain delivery remains unchanged",
            "native_and_openapi_business_id_match": True,
            "original_message_id": "trigger-1",
            "original_oa_task_id": "task-1",
            "original_oa_task_status": "CANCELED",
            "process_instance_id": "process-1",
            "process_status": "TERMINATED",
            "source": "exact original DingTalk message and native OA detail/OpenAPI readback",
        }),
        channel="dingtalk",
        trigger_message_id="trigger-1",
    )

    message = _execution_status_message(
        attempt, "uncertain", "已审核方案没有全部核验完成，外部结果仍待核验。"
    )

    assert message == "原 OA 流程已终止，此跟进已跳过；原消息送达情况仍未核实。"


def test_reconciled_wechat_task_explains_sent_reply_instead_of_execution_failure():
    from app.web_api.attempts import _reconciled_wechat_message

    attempt = SimpleNamespace(channel="wechat", conversation_id="melody115", trigger_message_id="wx-1")
    calls = []

    class Store:
        def get_wechat_reply_task_reconciliation(self, conversation_id, trigger_message_id):
            calls.append((conversation_id, trigger_message_id))
            return SimpleNamespace(kind="wechat_reply_task_done_reconciled")

    message = _reconciled_wechat_message(Store(), attempt, SimpleNamespace(status="done"))
    assert message == "已通过微信读回确认这条回复已发送；原执行回执保留，无需操作。"
    assert calls == [("melody115", "wx-1")]

    class Skipped(Store):
        def get_wechat_reply_task_reconciliation(self, conversation_id, trigger_message_id):
            return SimpleNamespace(kind="wechat_reply_task_skipped_reconciled")

    assert "旧候选已跳过" in _reconciled_wechat_message(Skipped(), attempt, SimpleNamespace(status="skipped"))


def test_reconciled_wechat_message_stays_silent_without_a_settlement_or_for_other_channels():
    from app.web_api.attempts import _reconciled_wechat_message

    class Empty:
        def get_wechat_reply_task_reconciliation(self, conversation_id, trigger_message_id):
            return None

    wechat = SimpleNamespace(channel="wechat", conversation_id="c", trigger_message_id="m")
    assert _reconciled_wechat_message(Empty(), wechat, SimpleNamespace(status="done")) == ""
    assert _reconciled_wechat_message(Empty(), wechat, SimpleNamespace(status="failed")) == ""
    dingtalk = SimpleNamespace(channel="dingtalk", conversation_id="c", trigger_message_id="m")
    assert _reconciled_wechat_message(object(), dingtalk, SimpleNamespace(status="done")) == ""


def test_console_command_record_renders_what_who_where_when():
    import json

    from app.web_api.attempts import _console_command_display

    ui = SimpleNamespace(kind="console_rerun_requested", created_at="2026-10-28 19:02:04", detail=json.dumps({
        "attempt_id": 16295, "command": "rerun", "actor": "console-ui",
        "client": "127.0.0.1", "user_agent": "Mozilla/5.0",
    }))
    assert _console_command_display(ui) == "重新处理 · console-ui · 127.0.0.1 · 2026-10-28 19:02:04"

    script = SimpleNamespace(kind="console_retry_requested", created_at="2026-10-28 19:26:24", detail=json.dumps({
        "attempt_id": 16295, "command": "retry", "actor": "", "client": "127.0.0.1", "user_agent": "curl/8.7.1",
    }))
    assert _console_command_display(script) == "重试（读回确认） · 未标识来源（curl/8.7.1） · 127.0.0.1 · 2026-10-28 19:26:24"
