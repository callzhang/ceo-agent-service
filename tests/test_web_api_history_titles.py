"""Derek, 2026-09-25: an approval waiting on him must say which approval it is.

History titled it 审批待办 (the conversation) with the scanner's prompt as the
body, so he could not tell 刘紫煜's supplier payment from 黄楚's reimbursement
without opening each one.
"""

from __future__ import annotations

from pathlib import Path

from app.store import AutoReplyStore
from tests.test_console_web_api import _client


def _needs_human(store: AutoReplyStore, *, message_id: str, trigger: str, reason: str) -> None:
    store.record_reply_attempt(
        conversation_id="oa-pending",
        conversation_title="审批待办",
        trigger_message_id=message_id,
        trigger_sender="OA审批",
        trigger_text=trigger,
        action="agent_run",
        sensitivity_kind="general",
        codex_reason=reason,
        audit_summary=reason,
        send_status="needs_human",
        channel="dingtalk",
    )


def test_an_approval_waiting_on_derek_is_titled_by_its_name_and_reason(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _needs_human(
        store,
        message_id="oa-1",
        trigger="审批待办扫描发现新增或有新消息的待处理审批：刘紫煜提交的供应商付款申请\n[查看审批](https://example.test/a)",
        reason="付款金额超出规则卡授权，需要 Derek 决定是否批准。\n详细依据……",
    )
    _needs_human(
        store,
        message_id="ding-1",
        trigger="[Ding]黄楚提醒您审批他的资金调拨申请单",
        reason="资金调拨一律由 Derek 本人决定。",
    )
    _needs_human(
        store,
        message_id="oa-2",
        trigger="黄楚在黄楚提交的费用报销对外付款综合审批单里提到了你\n“关于表格口径……”",
        reason="规则卡缺少预算与税务例外，不能自动审批。",
    )

    with _client(tmp_path) as client:
        items = client.get("/api/console/history?status=needs_human").json()["items"]

    by_title = {item["title"]: item for item in items}
    assert "刘紫煜提交的供应商付款申请" in by_title
    assert by_title["刘紫煜提交的供应商付款申请"]["summary"] == (
        "付款金额超出规则卡授权，需要 Derek 决定是否批准。"
    )
    assert "黄楚提交的费用报销对外付款综合审批单（黄楚提到你）" in by_title
    assert "资金调拨申请单（黄楚催办）" in by_title
    assert "审批待办" not in by_title
