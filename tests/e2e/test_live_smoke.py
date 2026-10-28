import os
from pathlib import Path

import pytest

from app.codex_decision import CodexDecisionRunner
from app.dingtalk_models import CodexAction
from app.dws_client import DwsClient
from app.store import AutoReplyStore
from tests.test_routed_codex_execution import seed_agent_run_parent


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
def test_live_codex_exec_json_smoke(tmp_path):
    workspace = Path(os.getenv("CEO_CODEX_E2E_WORKSPACE", "/Users/principal/Documents/memory"))
    assert workspace.exists()
    store = AutoReplyStore(tmp_path / "live-smoke.sqlite3")
    run_id = seed_agent_run_parent(store)
    runner = CodexDecisionRunner(workspace=workspace, store=store, timeout_seconds=120)

    decision = runner.decide(
        prompt=(
            "只输出合法的 AgentEnvelope JSON，不要解释。"
            '{"kind":"no_action","user_response":{"mode":"no_reply",'
            '"text":"","sensitivity_kind":"general"},"system_actions":[],'
            '"domain_payload":{},"audit":{"summary":"live smoke",'
            '"documents":[],"confidence":0.8}}'
        ),
        session_id=None,
        run_id=run_id,
    )

    assert decision.action in {CodexAction.NO_REPLY, CodexAction.STOP_WITH_ERROR}
    if decision.action == CodexAction.STOP_WITH_ERROR:
        pytest.fail(decision.reason)


def test_codex_smoke_uses_isolated_store_and_persisted_run(tmp_path, monkeypatch):
    seen = {}

    class Runner:
        def __init__(self, *, workspace, store, timeout_seconds):
            seen.update(workspace=workspace, store=store, timeout=timeout_seconds)

        def decide(self, *, prompt, session_id, run_id):
            seen.update(prompt=prompt, session_id=session_id, run_id=run_id)
            return type("Decision", (), {"action": CodexAction.NO_REPLY})()

    monkeypatch.setenv("CEO_CODEX_E2E_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(__import__(__name__, fromlist=["CodexDecisionRunner"]), "CodexDecisionRunner", Runner)
    test_live_codex_exec_json_smoke(tmp_path)
    assert seen["store"].get_agent_run(seen["run_id"]) is not None
    assert seen["workspace"] == tmp_path
    assert '"kind":"no_action"' in seen["prompt"]


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
