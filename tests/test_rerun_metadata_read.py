from types import SimpleNamespace

import pytest

from app.audit_web import handle_rerun_attempt_post


@pytest.mark.parametrize("refused", [False, True])
def test_rerun_uses_terminal_metadata_without_native_hydration(refused):
    attempt = SimpleNamespace(
        id=123, agent_run_id=456, resolved_at="", resolution="",
        send_status="failed", send_error="agent_reported_failure",
        channel="dingtalk", conversation_id="cid", trigger_message_id="msg",
    )
    seen = []

    class QueuingBoundaryReached(Exception):
        pass

    class Store:
        def get_reply_attempt(self, attempt_id):
            assert attempt_id == attempt.id
            return attempt

        def get_agent_run(self, run_id, *, load_events=True):
            assert run_id == attempt.agent_run_id
            assert load_events is False, "rerun must not discover/read native files"
            seen.append(run_id)
            return SimpleNamespace(
                id=run_id,
                structured_error_json=(
                    '{"code":"agent_reported_failure","source_code":"provider_risk_rejected"}'
                    if refused else '{"code":"audit_revision_exhausted"}'
                ),
            )

        def get_reply_task_for_message(self, *args, **kwargs):
            if refused:
                return None
            raise QueuingBoundaryReached

        def get_conversation(self, *args, **kwargs):
            raise AssertionError("a refused rerun is decided before the conversation lookup")

    if refused:
        status, _, body = handle_rerun_attempt_post(Store(), attempt.id)
        assert status == 409
        assert "此入口不能重放历史候选" in body
    else:
        with pytest.raises(QueuingBoundaryReached):
            handle_rerun_attempt_post(Store(), attempt.id)
    assert seen == [attempt.agent_run_id]
