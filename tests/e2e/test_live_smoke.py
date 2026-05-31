import os
from pathlib import Path

import pytest

from app.codex_decision import CodexDecisionRunner
from app.dingtalk_models import CodexAction
from app.dws_client import DwsClient


def _enabled(name: str) -> bool:
    return os.getenv(name) == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not _enabled("CEO_LIVE_DWS_E2E"),
    reason="set CEO_LIVE_DWS_E2E=1 to run live read-only dws smoke test",
)
def test_live_dws_read_only_smoke():
    dws = DwsClient()

    current_user_id = dws.get_current_user_id()
    conversations = dws.list_unread_conversations(count=1)

    assert current_user_id
    assert isinstance(conversations, list)


@pytest.mark.live
@pytest.mark.skipif(
    not _enabled("CEO_LIVE_CODEX_E2E"),
    reason="set CEO_LIVE_CODEX_E2E=1 to run live Codex exec smoke test",
)
def test_live_codex_exec_json_smoke():
    workspace = Path(os.getenv("CEO_CODEX_E2E_WORKSPACE", "/Users/principal/Documents/memory"))
    assert workspace.exists()
    runner = CodexDecisionRunner(workspace=workspace, timeout_seconds=120)

    decision = runner.decide(
        prompt=(
            "只输出合法 JSON，不要解释。"
            '{"action":"no_reply","reason":"live smoke","reply_text":""}'
        ),
        session_id=None,
    )

    assert decision.action in {CodexAction.NO_REPLY, CodexAction.STOP_WITH_ERROR}
    if decision.action == CodexAction.STOP_WITH_ERROR:
        pytest.fail(decision.reason)


@pytest.mark.live
@pytest.mark.skipif(
    not _enabled("CEO_LIVE_DING_SEND_E2E"),
    reason="set CEO_LIVE_DING_SEND_E2E=1 to send a live DING smoke test",
)
def test_live_ding_send_smoke():
    dws = DwsClient(
        ding_robot_code=os.getenv("CEO_DING_ROBOT_CODE")
        or os.getenv("DINGTALK_DING_ROBOT_CODE"),
        ding_receiver_user_id=os.getenv("CEO_DING_RECEIVER_USER_ID"),
    )

    dws.ding_self("CEO agent live DING smoke test")


@pytest.mark.live
@pytest.mark.skipif(
    not _enabled("CEO_LIVE_DINGTALK_SEND_E2E"),
    reason=(
        "set CEO_LIVE_DINGTALK_SEND_E2E=1 plus CEO_E2E_CONVERSATION_ID "
        "to send a live DingTalk chat smoke test"
    ),
)
def test_live_dingtalk_chat_send_smoke():
    conversation_id = os.getenv("CEO_E2E_CONVERSATION_ID")
    if not conversation_id:
        pytest.skip("CEO_E2E_CONVERSATION_ID is required")
    dws = DwsClient()

    dws.send_message(conversation_id, "CEO agent live chat smoke test（by明哥分身）")
