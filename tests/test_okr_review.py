import json

import pytest
from pydantic import ValidationError

from app.agent_envelope import AgentEnvelope
from app.okr_review import (
    DwsAgoalApiOkrSource,
    DwsLiveOkrSource,
    build_okr_review_prompt,
    compact_okr_source_for_review_prompt,
    current_quarter_period,
    is_okr_review_request,
    normalize_okr_review_domain_payload,
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
        okr_source_json='{"processed":{"objectives":[],"okrRows":[]}}',
        trigger_text="帮我审核 OKR",
    )

    assert "request_id: 7" in prompt
    assert "KR进度更新" in prompt
    assert "员工主张信息打分" in prompt
    assert "事实核实后打分" in prompt
    assert "不要输出旧格式 `request_id/status/result`" in prompt
    assert "`system_actions` 必须包含" in prompt
    assert '"request_id":7' in prompt


def test_build_okr_review_prompt_compacts_raw_live_source():
    prompt = build_okr_review_prompt(
        request_id=7,
        person_name="韩露",
        period_label="2026 Q2",
        okr_source_json=json.dumps(
            {
                "source": {"system": "叮当OKR Dingteam Web"},
                "period": {"name": "2026年2季度"},
                "objectiveList": [{"large": "raw-objective"}],
                "objectiveDetails": [{"large": "raw-detail"}],
                "processed": {
                    "objectives": [{"title": "O"}],
                    "okrRows": [{"level": "KR", "krTitle": "KR"}],
                },
            },
            ensure_ascii=False,
        ),
        trigger_text="帮我审核 OKR",
    )

    assert "raw-objective" not in prompt
    assert "raw-detail" not in prompt
    assert "叮当OKR Dingteam Web" in prompt
    assert '"krTitle": "KR"' in prompt


def test_build_okr_review_prompt_preserves_kr_progress_comments():
    prompt = build_okr_review_prompt(
        request_id=7,
        person_name="Claire",
        period_label="2026 Q2",
        okr_source_json=json.dumps(
            {
                "source": {"system": "叮当OKR Dingteam Web"},
                "period": {"name": "2026年2季度"},
                "processed": {
                    "objectives": [
                        {
                            "title": "打穿 Friday PMF",
                            "keyResults": [
                                {
                                    "title": "拿到首批付费用户",
                                    "progressUpdates": [
                                        {
                                            "createdAt": "2026-06-07T17:53:40.000Z",
                                            "progressChange": "进度 20%",
                                            "content": "subway 的 2 个 poc 马上开始",
                                        }
                                    ],
                                    "progressUpdatesAggregated": (
                                        "2026-06-07T17:53:40.000Z | 进度 20% | "
                                        "subway 的 2 个 poc 马上开始"
                                    ),
                                }
                            ],
                        }
                    ],
                    "okrRows": [
                        {
                            "level": "KR",
                            "krTitle": "拿到首批付费用户",
                            "krDetailsUpdatesAggregated": (
                                "2026-06-07T17:53:40.000Z | 进度 20% | "
                                "subway 的 2 个 poc 马上开始"
                            ),
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        trigger_text="帮我审核 OKR",
    )

    assert "subway 的 2 个 poc 马上开始" in prompt
    assert "progressUpdates" in prompt
    assert "krDetailsUpdatesAggregated" in prompt


def test_compact_okr_source_requires_processed_rows():
    with pytest.raises(ValueError, match="processed"):
        compact_okr_source_for_review_prompt('{"objectiveList":[]}')


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


def test_normalize_okr_review_domain_payload_accepts_agent_aliases():
    normalized = normalize_okr_review_domain_payload(
        {
            "person_name": "Claire",
            "period_label": "2026 Q2",
            "summary": {"overall_comment": "整体有进展，但缺硬结果。"},
            "items": [
                {
                    "objective": "打穿 Friday PMF",
                    "kr": "拿到首批付费用户",
                    "kr_weight": 33.33,
                    "self_progress": 20,
                    "kr_progress_notes": "Subway 的 2 个 POC 即将开始",
                    "employee_claims": ["Subway 的 2 个 POC 即将开始"],
                    "employee_claim_score": 20,
                    "evidence_used": ["Subway POC 仍在 proposal 阶段"],
                    "evidence_gaps": "缺少付费用户证据。",
                    "deadline": "2026 Q2",
                    "actual_completion_time": "未完成",
                    "fact_checked_base_score": 10,
                    "time_discount": "未适用",
                    "fact_checked_score": 10,
                    "ceo_comment": "POC 不等于付费用户。",
                    "suggested_follow_up": "补充合同或付款证据。",
                }
            ],
        }
    )

    payload = OkrReviewPayload.model_validate(normalized)

    assert payload.summary == "整体有进展，但缺硬结果。"
    assert payload.items[0].objective_title == "打穿 Friday PMF"
    assert payload.items[0].kr_title == "拿到首批付费用户"
    assert payload.items[0].kr_weight == pytest.approx(0.3333)
    assert payload.items[0].verified_score == 10
    assert payload.items[0].evidence_used[0].summary == "Subway POC 仍在 proposal 阶段"


def test_normalize_okr_review_domain_payload_accepts_final_score_aliases():
    normalized = normalize_okr_review_domain_payload(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "已审核。",
            "items": [
                {
                    "objective": "O1 标准Offer与高质量解决方案",
                    "kr": "LLM数据领先性",
                    "kr_weight": 30,
                    "self_progress": 0,
                    "kr_progress_notes": "[未撰写进度]",
                    "employee_claim_score": 0,
                    "evidence_used": ["VLM 立项方案存在，但缺发布证明。"],
                    "evidence_gaps": "缺发布证明。",
                    "deadline": "2026 Q2",
                    "actual_completion_time": "未验证完成",
                    "base_score": 20,
                    "time_discount": "未适用",
                    "final_score": 20,
                    "ceo_comment": "只能认定为早期推进。",
                    "suggested_follow_up": "补版本发布链接。",
                }
            ],
        }
    )

    payload = OkrReviewPayload.model_validate(normalized)

    assert payload.items[0].verified_base_score == 20
    assert payload.items[0].verified_score == 20


def test_normalize_okr_review_domain_payload_accepts_claim_score_alias():
    # The agent emits `claim_score` (not `employee_claim_score`); both must work.
    normalized = normalize_okr_review_domain_payload(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": "已审核。",
            "items": [
                {
                    "objective": "O1",
                    "kr": "LLM数据领先性",
                    "kr_weight": 30,
                    "claim_score": 35,
                    "base_score": 30,
                    "final_score": 30,
                    "time_discount": "未适用",
                    "evidence_used": ["VLM 立项方案存在。"],
                    "evidence_gaps": "缺发布证明。",
                    "suggested_follow_up": "补版本发布链接。",
                }
            ],
        }
    )

    payload = OkrReviewPayload.model_validate(normalized)
    assert payload.items[0].claim_score == 35
    assert payload.items[0].claim_base_score == 35
    assert payload.items[0].verified_score == 30


def test_normalize_okr_review_domain_payload_renders_assessment_and_gaps():
    # A summary dict with overall_assessment + key_gaps must render as readable
    # text, not a raw JSON blob.
    normalized = normalize_okr_review_domain_payload(
        {
            "person_name": "韩露",
            "period_label": "2026 Q2",
            "summary": {
                "overall_assessment": "有真实推进，但缺硬结果。",
                "key_gaps": ["缺发布证明。", "缺 CRM 成单数据。"],
                "claim_weighted_score": 32,
            },
            "items": [],
        }
    )

    summary = normalized["summary"]
    assert summary.startswith("有真实推进，但缺硬结果。")
    assert "关键差距：" in summary
    assert "- 缺发布证明。" in summary
    assert "- 缺 CRM 成单数据。" in summary
    assert "{" not in summary  # no raw JSON


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
        okr_source_json='{"processed":{"objectives":[],"okrRows":[]}}',
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
        okr_source_json='{"processed":{"objectives":[],"okrRows":[]}}',
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


class FakeAgoalDws:
    def __init__(self, *, rules=None, progress_items=None):
        self.rules = rules or [
            {
                "objectiveRuleId": "rule-1",
                "objectiveRuleName": "公司 OKR",
            }
        ]
        self.progress_items = progress_items
        self.calls = []

    def read_agoal_objective_rule_list(self):
        self.calls.append(("rules",))
        return {"content": {"result": self.rules}}

    def read_agoal_objective_rule_period_list(self, objective_rule_id):
        self.calls.append(("periods", objective_rule_id))
        return {
            "content": [
                {
                    "periodId": "period-q2",
                    "name": "2026年二季度",
                    "startDate": 1774915200000,
                    "endDate": 1782748799000,
                }
            ]
        }

    def read_agoal_user_objective_list(
        self,
        *,
        ding_user_id,
        objective_rule_id,
        period_ids,
    ):
        self.calls.append(("objectives", ding_user_id, objective_rule_id, period_ids))
        return {
            "content": [
                {
                    "objectiveId": "objective-1",
                    "title": "提升交付质量",
                    "weight": 1.0,
                }
            ]
        }

    def read_agoal_objective_detail(self, objective_id):
        self.calls.append(("detail", objective_id))
        return {
            "content": {
                "objectiveId": objective_id,
                "title": "提升交付质量",
                "keyResults": [
                    {
                        "keyResultId": "kr-1",
                        "title": "完成 3 个客户验收",
                        "weight": 0.5,
                        "progress": 60,
                    }
                ],
            }
        }

    def read_agoal_objective_progress_list(self, objective_id, page_size):
        self.calls.append(("progress", objective_id, page_size))
        progress_items = self.progress_items
        if progress_items is None:
            progress_items = [
                {
                    "progressId": "progress-1",
                    "objectiveId": objective_id,
                    "updated": 1782000000000,
                    "htmlContent": "6 月完成两个客户验收。",
                    "keyResults": [
                        {
                            "keyResultId": "kr-1",
                            "title": "完成 3 个客户验收",
                            "progress": 60,
                        }
                    ],
                }
            ]
        return {
            "content": {
                "result": progress_items
            }
        }


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


def test_agoal_api_okr_source_fetches_objectives_details_and_progresses():
    dws = FakeAgoalDws()
    source = DwsAgoalApiOkrSource(dws=dws, max_attempts=1)

    payload = source.fetch_user_okr(user_id="ding-user-1", period_label="2026 Q2")

    assert payload["source"]["system"] == "叮当OKR Agoal OpenAPI"
    assert payload["source"]["objectiveRuleId"] == "rule-1"
    assert payload["period"]["periodId"] == "period-q2"
    assert payload["objectives"][0]["objectiveId"] == "objective-1"
    assert (
        payload["objectiveDetails"][0]["payload"]["keyResults"][0]["title"]
        == "完成 3 个客户验收"
    )
    assert (
        payload["objectiveProgresses"][0]["payload"]["result"][0]["htmlContent"]
        == "6 月完成两个客户验收。"
    )
    assert payload["processed"]["okrRows"] == [
        {
            "level": "O",
            "objectiveId": "objective-1",
            "objectiveTitle": "提升交付质量",
            "objectiveWeight": 1.0,
            "objectiveProgress": None,
            "krId": "",
            "krTitle": "",
            "krWeight": "",
            "krProgress": "",
            "krDetailsUpdatesAggregated": "",
        },
        {
            "level": "KR",
            "objectiveId": "objective-1",
            "objectiveTitle": "提升交付质量",
            "objectiveWeight": 1.0,
            "objectiveProgress": None,
            "krId": "kr-1",
            "krTitle": "完成 3 个客户验收",
            "krWeight": 0.5,
            "krProgress": 60,
            "krDetailsUpdatesAggregated": "2026-06-21T00:00:00+00:00 | KR进度=60 | 6 月完成两个客户验收。",
        },
    ]
    assert dws.calls == [
        ("rules",),
        ("periods", "rule-1"),
        ("objectives", "ding-user-1", "rule-1", ["period-q2"]),
        ("detail", "objective-1"),
        ("progress", "objective-1", 100),
    ]


def test_agoal_api_okr_source_requires_explicit_rule_when_multiple_rules_exist():
    dws = FakeAgoalDws(
        rules=[
            {"objectiveRuleId": "rule-1", "objectiveRuleName": "公司 OKR"},
            {"objectiveRuleId": "rule-2", "objectiveRuleName": "销售 PBC"},
        ]
    )
    source = DwsAgoalApiOkrSource(dws=dws, max_attempts=1)

    with pytest.raises(RuntimeError, match="CEO_OKR_OBJECTIVE_RULE_ID"):
        source.fetch_user_okr(user_id="ding-user-1", period_label="2026 Q2")


def test_agoal_api_okr_source_marks_missing_kr_progress_as_not_written():
    dws = FakeAgoalDws(progress_items=[])
    source = DwsAgoalApiOkrSource(dws=dws, max_attempts=1)

    payload = source.fetch_user_okr(user_id="ding-user-1", period_label="2026 Q2")

    kr_row = payload["processed"]["okrRows"][1]
    assert kr_row["krDetailsUpdatesAggregated"] == "[未撰写进度]"
    assert (
        payload["processed"]["objectives"][0]["keyResults"][0][
            "progressUpdatesAggregated"
        ]
        == "[未撰写进度]"
    )
