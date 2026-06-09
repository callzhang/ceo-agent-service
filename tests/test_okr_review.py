import json

import pytest
from pydantic import ValidationError

from app.agent_envelope import AgentEnvelope
from app.okr_review import (
    DwsLiveOkrSource,
    build_okr_review_prompt,
    current_quarter_period,
    is_okr_review_request,
    process_okr_review_request,
    render_okr_review_reply,
)
from app.okr_models import OkrReviewItem, OkrReviewPayload
from app.store import AutoReplyStore


def test_okr_review_item_requires_two_scores_and_discount_reasons():
    item = OkrReviewItem.model_validate(
        {
            "objective_title": "提升交付质量",
            "objective_weight": 1.0,
            "kr_title": "Q2 完成 3 个客户验收",
            "kr_weight": 0.5,
            "self_progress": "80%",
            "kr_progress_update": "6月20日完成两个客户验收，第三个在推进。",
            "claim_text": "完成两个客户验收，第三个在推进。",
            "claim_completion_time": "2026-06-20",
            "deadline": "2026-06-15",
            "claim_base_score": 80,
            "claim_discount_factor": 0.8,
            "claim_discount_reason": "员工主张完成时间晚于 KR 要求 5 天。",
            "claim_score": 64,
            "verified_completion_time": "2026-06-21",
            "verified_base_score": 60,
            "verified_discount_factor": 0.6,
            "verified_discount_reason": "证据显示实际验收晚于要求且影响交付节奏。",
            "verified_score": 36,
            "evidence_used": [
                {"source": "dws:minutes:abc", "summary": "客户验收会确认两个项目通过。"}
            ],
            "evidence_gap": "缺少第三个客户验收确认。",
            "review_comment": "进展存在，但未完整达到 3 个验收目标。",
            "suggested_follow_up": "补充第三个客户验收记录和客户确认时间。",
        }
    )

    assert item.claim_score == 64
    assert item.verified_score == 36


def test_okr_review_item_rejects_discount_outside_range():
    payload = {
        "objective_title": "提升交付质量",
        "objective_weight": 1.0,
        "kr_title": "Q2 完成 3 个客户验收",
        "kr_weight": 0.5,
        "self_progress": "80%",
        "kr_progress_update": "表达不清。",
        "claim_text": "表达不清。",
        "claim_completion_time": "",
        "deadline": "2026-06-15",
        "claim_base_score": 60,
        "claim_discount_factor": 0.2,
        "claim_discount_reason": "折扣超过允许范围。",
        "claim_score": 54,
        "verified_completion_time": "",
        "verified_base_score": 0,
        "verified_discount_factor": 1.0,
        "verified_discount_reason": "无证据时不适用折扣。",
        "verified_score": 0,
        "evidence_used": [],
        "evidence_gap": "没有独立证据。",
        "review_comment": "证据不足。",
        "suggested_follow_up": "补充可验证材料。",
    }

    with pytest.raises(ValidationError):
        OkrReviewItem.model_validate(payload)


def test_okr_review_payload_contains_items():
    payload = OkrReviewPayload.model_validate(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "共 1 个 KR。",
            "items": [
                {
                    "objective_title": "提升交付质量",
                    "objective_weight": 1.0,
                    "kr_title": "Q2 完成 3 个客户验收",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个客户验收。",
                    "claim_text": "完成两个客户验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现时间或含糊折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少客户验收记录。",
                    "review_comment": "只能确认员工主张，未能核实。",
                    "suggested_follow_up": "提供客户验收材料。",
                }
            ],
        }
    )

    assert payload.items[0].kr_title == "Q2 完成 3 个客户验收"


def test_is_okr_review_request_matches_review_intent():
    assert is_okr_review_request("帮我审核 OKR")
    assert is_okr_review_request("看看我的 KR 进度")
    assert not is_okr_review_request("今天 OKR 系统打不开")


def test_current_quarter_period_uses_current_date():
    period = current_quarter_period("2026-06-08")
    assert period.period_label == "2026 Q2"
    assert period.period_start == "2026-04-01"
    assert period.period_end == "2026-06-30"


def test_build_okr_review_prompt_includes_live_source_and_claim_scoring():
    prompt = build_okr_review_prompt(
        request_id=7,
        person_name="韩露",
        period_label="2026 Q2",
        okr_source_json='{"objectives":[]}',
        trigger_text="帮我审核 OKR",
    )

    assert "request_id: 7" in prompt
    assert "KR进度更新" in prompt
    assert "员工主张信息打分" in prompt
    assert "事实核实后打分" in prompt


def test_render_okr_review_reply_includes_two_scores():
    payload = OkrReviewPayload.model_validate(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "1 个 KR 已审核。",
            "items": [
                {
                    "objective_title": "O",
                    "objective_weight": 1.0,
                    "kr_title": "KR",
                    "kr_weight": 0.5,
                    "self_progress": "80%",
                    "kr_progress_update": "完成两个验收。",
                    "claim_text": "完成两个验收。",
                    "claim_completion_time": "",
                    "deadline": "",
                    "claim_base_score": 60,
                    "claim_discount_factor": 1.0,
                    "claim_discount_reason": "未发现折扣。",
                    "claim_score": 60,
                    "verified_completion_time": "",
                    "verified_base_score": 0,
                    "verified_discount_factor": 1.0,
                    "verified_discount_reason": "无可核验证据。",
                    "verified_score": 0,
                    "evidence_used": [],
                    "evidence_gap": "缺少验收记录。",
                    "review_comment": "证据不足。",
                    "suggested_follow_up": "补充验收记录。",
                }
            ],
        }
    )

    reply = render_okr_review_reply(payload)

    assert "员工主张分: 60" in reply
    assert "事实核实分: 0" in reply
    assert "缺少验收记录" in reply


class FakeStructuredRunnerForOkr:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    def run(self, conversation_id, conversation_title, single_chat, prompt, *, owner):
        self.calls.append((conversation_id, conversation_title, single_chat, prompt, owner))
        return type(
            "Run",
            (),
            {
                "envelope": self.envelope,
                "codex_session_id": "session-okr",
                "transcript_start_line": 1,
                "transcript_end_line": 10,
                "audit_tool_events": [{"tool": "memory_recall"}],
            },
        )()


def test_process_okr_review_request_persists_items_and_marks_done(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    request = store.claim_okr_review_requests(1)[0]
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "okr_review",
            "user_response": {
                "mode": "send_reply",
                "text": "OKR review done",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [{"type": "persist_okr_review", "request_id": request_id}],
            "domain_payload": {
                "person_name": "韩露",
                "period_label": "2026 Q2",
                "summary": "1 个 KR 已审核。",
                "items": [
                    {
                        "objective_title": "O",
                        "objective_weight": 1.0,
                        "kr_title": "KR",
                        "kr_weight": 0.5,
                        "self_progress": "80%",
                        "kr_progress_update": "完成两个验收。",
                        "claim_text": "完成两个验收。",
                        "claim_completion_time": "",
                        "deadline": "",
                        "claim_base_score": 60,
                        "claim_discount_factor": 1.0,
                        "claim_discount_reason": "未发现折扣。",
                        "claim_score": 60,
                        "verified_completion_time": "",
                        "verified_base_score": 0,
                        "verified_discount_factor": 1.0,
                        "verified_discount_reason": "无可核验证据。",
                        "verified_score": 0,
                        "evidence_used": [],
                        "evidence_gap": "缺少验收记录。",
                        "review_comment": "证据不足。",
                        "suggested_follow_up": "补充验收记录。",
                    }
                ],
            },
            "audit": {"summary": "审核完成。", "documents": [], "confidence": 0.8},
        }
    )
    runner = FakeStructuredRunnerForOkr(envelope)

    reply = process_okr_review_request(
        store=store,
        runner=runner,
        request=request,
        single_chat=True,
    )

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert loaded.codex_session_id == "session-okr"
    assert "员工主张分" in reply
    assert runner.calls[0][4] == f"okr_review:{request_id}"
    assert json.loads(runner.envelope.model_dump_json())["kind"] == "okr_review"


def test_process_okr_review_request_preserves_group_conversation_kind(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-group",
        conversation_title="OKR 群",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    request = store.claim_okr_review_requests(1)[0]
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "okr_review",
            "user_response": {
                "mode": "send_reply",
                "text": "OKR review done",
                "sensitivity_kind": "internal_personnel",
            },
            "system_actions": [{"type": "persist_okr_review", "request_id": request_id}],
            "domain_payload": {
                "person_name": "韩露",
                "period_label": "2026 Q2",
                "summary": "1 个 KR 已审核。",
                "items": [
                    {
                        "objective_title": "O",
                        "objective_weight": 1.0,
                        "kr_title": "KR",
                        "kr_weight": 0.5,
                        "self_progress": "80%",
                        "kr_progress_update": "完成两个验收。",
                        "claim_text": "完成两个验收。",
                        "claim_completion_time": "",
                        "deadline": "",
                        "claim_base_score": 60,
                        "claim_discount_factor": 1.0,
                        "claim_discount_reason": "未发现折扣。",
                        "claim_score": 60,
                        "verified_completion_time": "",
                        "verified_base_score": 0,
                        "verified_discount_factor": 1.0,
                        "verified_discount_reason": "无可核验证据。",
                        "verified_score": 0,
                        "evidence_used": [],
                        "evidence_gap": "缺少验收记录。",
                        "review_comment": "证据不足。",
                        "suggested_follow_up": "补充验收记录。",
                    }
                ],
            },
            "audit": {"summary": "审核完成。", "documents": [], "confidence": 0.8},
        }
    )
    runner = FakeStructuredRunnerForOkr(envelope)

    process_okr_review_request(
        store=store,
        runner=runner,
        request=request,
        single_chat=False,
    )

    assert runner.calls[0][2] is False


class FakeDwsForOkr:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"objectives": []}
        self.error = error
        self.calls = []

    def run_json(self, command):
        self.calls.append(command)
        if self.error:
            raise self.error
        return self.payload


def test_dws_live_okr_source_uses_single_configured_command():
    dws = FakeDwsForOkr(payload={"objectives": [{"title": "O"}]})
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=[
            "dws",
            "api",
            "request",
            "--resource",
            "okr",
            "--user-id",
            "{user_id}",
            "--period",
            "{period_label}",
            "--format",
            "json",
        ],
    )

    payload = source.fetch_user_okr(user_id="user-1", period_label="2026 Q2")

    assert payload["objectives"][0]["title"] == "O"
    assert "{user_id}" not in dws.calls[0]
    assert "user-1" in dws.calls[0]


def test_dws_live_okr_source_retries_then_reraises_source_error():
    dws = FakeDwsForOkr(error=RuntimeError("okr unavailable"))
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=["dws", "api", "--user-id", "{user_id}", "--period", "{period_label}"],
        max_attempts=2,
    )

    with pytest.raises(RuntimeError, match="okr unavailable"):
        source.fetch_user_okr(user_id="user-1", period_label="2026 Q2")

    assert len(dws.calls) == 2
