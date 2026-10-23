"""Source integration and Skill ports of the former weekly OKR workflow cases."""

import importlib.util
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from app.weekly_okr_report import (
    DEFAULT_ARCHIVE_DIR_NAME,
    LATEST_ARCHIVE_INDEX_NAME,
    LATEST_ARCHIVE_RAW_NAME,
    DwsWeeklyOkrGateway,
    GroupRoster,
    ManagerIdentity,
    collect_weekly_okr_member,
    refresh_company_okr_archive,
)
from app.codex_decision import append_signature

SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "ci/shared-skills/ceo-weekly-okr-report/scripts/report.py"
)
spec = importlib.util.spec_from_file_location("weekly_okr_skill", SKILL_SCRIPT)
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)
CeoAttentionItem = report.CeoAttentionItem
DimensionScoreReview = report.DimensionScoreReview
KrScoreReview = report.KrScoreReview
ManagerReportAnalysis = report.ManagerReportAnalysis


def test_display_rounding_uses_half_up_at_one_decimal():
    assert report._rounded(71.25) == 71.3
    assert report._rounded(71.24) == 71.2
    assert report._rounded(report._manager_final_score(60, 70, 0.95)) == 59.9
    assert report._display_average([91.4, 59.1]) == "75.3"
    assert report._manager_final_score(60, 70, 0.95) == 59.85


def test_markdown_table_cell_newlines_use_visible_separator_not_html_breaks():
    assert report._md_cell("第一条\n第二条") == "第一条；第二条"


WeeklyOkrAnalysis = report.WeeklyOkrAnalysis
PublishedDocument = report.PublishedDocument
render_group_summary = report.render_group_summary
_manager_scorecards = report._manager_scorecards
weekly_okr_report_module = report
SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeDws:
    dws_bin = "dws"

    def __init__(self, payload):
        self.payload = payload
        self.commands = []

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        return self.payload


class CurrentDwsRoster:
    dws_bin = "dws"

    def __init__(self):
        self.commands = []

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        if command[1:3] == ["chat", "+chat-search"]:
            return {
                "success": True,
                "result": {
                    "groups": [
                        {
                            "title": "CEO-2 管理群",
                            "openConversationId": "cid-ceo-2",
                        }
                    ],
                    "hasMore": False,
                },
            }
        if command[1:3] == ["chat", "+chat-members-list"]:
            return {
                "complete": True,
                "users": [
                    {"name": "甲", "openDingtalkId": "open-1"},
                    {"name": "乙", "openDingtalkId": "open-2"},
                ],
            }
        if command[1:3] == ["contact", "+search-user"]:
            name = command[command.index("--query") + 1]
            suffix = "1" if name == "甲" else "2"
            return {
                "users": [
                    {
                        "name": name,
                        "openDingTalkId": f"open-{suffix}",
                        "userId": f"user-{suffix}",
                        "title": "总监",
                    }
                ]
            }
        raise AssertionError(command)


class SearchOnlyDwsRoster(CurrentDwsRoster):
    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        if command[1:3] == ["chat", "+chat-search"]:
            return {
                "success": True,
                "result": {
                    "groups": [
                        {
                            "title": "CEO-2 管理群",
                            "openConversationId": "cid-ceo-2",
                        }
                    ],
                    "hasMore": False,
                },
            }
        if command[1:3] == ["chat", "+chat-members-list"]:
            return {
                "complete": True,
                "users": [
                    {"name": "甲", "openDingtalkId": "open-1"},
                    {"name": "乙", "openDingtalkId": "open-2"},
                ],
            }
        if command[1:3] == ["contact", "+search-user"]:
            name = command[command.index("--query") + 1]
            suffix = "1" if name == "甲" else "2"
            return {
                "users": [
                    {
                        "name": name,
                        "openDingTalkId": f"open-{suffix}",
                        "userId": f"user-{suffix}",
                        "title": "总监",
                    }
                ]
            }
        raise AssertionError(command)


class FakeSource:
    def __init__(self):
        self.calls = []

    def fetch_user_okr(self, *, user_id, period_label):
        self.calls.append((user_id, period_label))
        return {
            "source": {
                "system": "叮当OKR Dingteam Web (direct API)",
                "capturedAt": "2026-07-30T04:00:00Z",
                "objectiveListReceipt": {
                    "providerCode": 0,
                    "complete": True,
                    "count": 1,
                },
            },
            "userId": user_id,
            "periodLabel": period_label,
            "period": {"name": period_label, "okrId": "period-1"},
            "periods": [{"name": period_label, "okrId": "period-1"}],
            "objectiveList": [{"id": "objective-1"}],
            "processed": {
                "objectives": [{"ownerName": user_id, "title": "O1"}],
                "okrRows": [
                    {
                        "level": "KR",
                        "objectiveId": "objective-1",
                        "objectiveTitle": "O1",
                        "objectiveWeight": 100,
                        "objectiveProgress": 50,
                        "krId": "kr-1",
                        "krTitle": "KR1",
                        "krWeight": 100,
                        "krProgress": 50,
                        "krDetailsUpdatesAggregated": "2026-07-29 | 进度 50% | 已交付",
                    }
                ],
            },
        }


class FakeAgent:
    period_label = "2026 Q3"

    def analyze(self, *, source_path, managers, period_label, week_start, week_end):
        payload = source_path.read_text(encoding="utf-8")
        assert "processed" in payload
        assert period_label == self.period_label
        assert week_start <= week_end
        return WeeklyOkrAnalysis(
            executive_summary="本周两个管理目标均有实质推进。",
            company_progress=["交付节奏稳定"],
            ceo_attention_items=[
                CeoAttentionItem(
                    topic="资源冲突",
                    owner_names=[managers[0].name],
                    issue="需要明确优先级。",
                    recommended_decision="周一前确认资源。",
                )
            ],
            manager_reviews=[
                ManagerReportAnalysis(
                    name=manager.name,
                    role_level="总监" if manager.title == "总监" else "经理",
                    role_level_evidence=f"钉钉通讯录当前职务为{manager.title}",
                    progress_summary="本周完成一个可核验里程碑。",
                    key_progress=["KR 有新增进展"],
                    independent_evidence=["相关文档已读取"],
                    evidence_assessment="结果与系统更新一致。",
                    risks=[],
                    next_week_focus=["关闭剩余事项"],
                    data_gaps=[],
                    kr_reviews=[
                        KrScoreReview(
                            kr_id="kr-1",
                            objective_title="O1",
                            kr_title="KR1",
                            category="业务OKR",
                            system_progress="系统 50%，本周评论称已交付",
                            independent_evidence="相关文档已读取并核对交付内容",
                            evidence_assessment="产出已形成，但缺少使用效果数据。",
                            base_score=80,
                            time_discount="未适用",
                            score=80,
                            improvement="补充验收和使用效果。",
                        )
                    ],
                    leadership_dimensions=_dimensions(4, 70),
                    culture_dimensions=_dimensions(3, 80),
                )
                for manager in managers
            ],
            source_coverage=["实时叮当 OKR", "钉钉文档"],
            warnings=[],
        )


def _empty_live_payload(user_id, period_label):
    payload = FakeSource().fetch_user_okr(user_id=user_id, period_label=period_label)
    payload["objectiveList"] = []
    payload["source"]["objectiveListReceipt"]["count"] = 0
    payload["processed"] = {"objectives": [], "okrRows": []}
    return payload


def managers():
    return [
        ManagerIdentity("甲", "总监", "u1", "o1"),
        ManagerIdentity("乙", "经理", "u2", "o2"),
    ]


def _dimensions(count, score):
    return [
        DimensionScoreReview(
            dimension=f"维度{i + 1}",
            required_behavior="按当前职级稳定履责",
            positive_evidence="有本周具体行为案例",
            missing_or_contrary_evidence="跨团队效果仍需补充",
            score=score,
            next_band_evidence="补充可复用结果和采用记录",
        )
        for i in range(count)
    ]


def _missing_period_envelope(user_id, period_label):
    return {
        "source": {
            "system": "Dingteam personal-period API",
            "capturedAt": "2026-10-07T04:00:00Z",
        },
        "userId": user_id,
        "periodLabel": period_label,
        "availability": {
            "status": "goals_not_established",
            "providerCode": 0,
            "periodsComplete": True,
        },
        "periods": [{"name": "2026 Q3", "okrId": "q3"}],
    }


class FakeGateway:
    def __init__(self, members):
        self.members = members

    def resolve_group_roster(self, group_name):
        return GroupRoster(group_name, "cid-ceo-2", self.members)


def _collect(source, period="2026 Q3"):
    return [
        collect_weekly_okr_member(source=source, manager=member, period_label=period)
        for member in managers()
    ]


def test_group_summary_keeps_signature_outside_document_link():
    document_url = "https://alidocs.example/report?utm_scene=team_space"
    summary = render_group_summary(
        title="CEO-2 管理者 OKR 进度周报（2026-08-31—2026-09-06）",
        analysis=WeeklyOkrAnalysis(
            executive_summary="已完成分析",
            manager_reviews=[],
        ),
        document_url=document_url,
        manager_count=0,
        manager_payloads=[],
    )

    signed = append_signature(summary)

    assert f"完整周报：[打开完整周报]({document_url})" in signed
    assert f"{document_url}（by" not in signed


def test_refresh_company_okr_archive_writes_raw_and_latest_index(tmp_path):
    gateway = FakeGateway(managers())
    source = FakeSource()

    result = refresh_company_okr_archive(
        gateway=gateway,
        source=source,
        workspace=tmp_path,
        now=datetime(2026, 8, 5, 12, tzinfo=SHANGHAI),
        period_label="2026 Q3",
    )

    assert result.status == "archived"
    assert result.manager_count == 2
    assert result.kr_count == 2
    assert source.calls == [("u1", "2026 Q3"), ("u2", "2026 Q3")]
    raw_path = (
        tmp_path / DEFAULT_ARCHIVE_DIR_NAME / "2026q3" / "company_okr_2026q3_raw.json"
    )
    index_path = (
        tmp_path / DEFAULT_ARCHIVE_DIR_NAME / "2026q3" / "company_okr_2026q3_index.md"
    )
    latest_raw = tmp_path / DEFAULT_ARCHIVE_DIR_NAME / LATEST_ARCHIVE_RAW_NAME
    latest_index = tmp_path / DEFAULT_ARCHIVE_DIR_NAME / LATEST_ARCHIVE_INDEX_NAME
    assert result.raw_path == str(raw_path)
    assert result.index_path == str(index_path)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["periodLabel"] == "2026 Q3"
    assert payload["managers"][0]["manager"]["name"] == "甲"
    assert latest_raw.read_text(encoding="utf-8") == raw_path.read_text(
        encoding="utf-8"
    )
    index = index_path.read_text(encoding="utf-8")
    assert "# 公司 OKR 索引（2026 Q3）" in index
    assert "- KR 数：2" in index
    assert "## 甲｜总监" in index
    assert "- O：O1" in index
    assert "  - KR：KR1（进度 50%，权重 100）" in index
    assert "不是 TODO 完成证据" in index
    assert latest_index.read_text(encoding="utf-8") == index


def test_resolve_group_roster_uses_current_dws_conversation_contract():
    dws = CurrentDwsRoster()

    roster = DwsWeeklyOkrGateway(dws).resolve_group_roster("CEO-2 管理群")

    assert roster.name == "CEO-2 管理群"
    assert roster.conversation_id == "cid-ceo-2"
    assert [(item.name, item.user_id, item.title) for item in roster.managers] == [
        ("甲", "user-1", "总监"),
        ("乙", "user-2", "总监"),
    ]
    assert dws.commands[0] == [
        "dws",
        "chat",
        "+chat-search",
        "--query",
        "CEO-2 管理群",
        "--limit",
        "100",
        "--format",
        "json",
    ]
    assert dws.commands[1] == [
        "dws",
        "chat",
        "+chat-members-list",
        "--conversation-id",
        "cid-ceo-2",
        "--member-types",
        "user",
        "--format",
        "json",
    ]


def test_resolve_group_roster_accepts_live_chat_search_payload():
    dws = CurrentDwsRoster()
    original_run_json = dws.run_json

    def run_json(command, **kwargs):
        payload = original_run_json(command, **kwargs)
        if command[1:3] == ["chat", "+chat-search"]:
            return {
                "chats": payload["result"]["groups"],
                "hasMore": False,
            }
        return payload

    dws.run_json = run_json

    roster = DwsWeeklyOkrGateway(dws).resolve_group_roster("CEO-2 管理群")

    assert roster.conversation_id == "cid-ceo-2"
    assert [manager.user_id for manager in roster.managers] == ["user-1", "user-2"]


def test_resolve_group_roster_searches_by_name_when_group_is_not_recent():
    dws = SearchOnlyDwsRoster()

    roster = DwsWeeklyOkrGateway(dws).resolve_group_roster("CEO-2 管理群")

    assert roster.conversation_id == "cid-ceo-2"
    assert dws.commands[0] == [
        "dws",
        "chat",
        "+chat-search",
        "--query",
        "CEO-2 管理群",
        "--limit",
        "100",
        "--format",
        "json",
    ]


def test_manager_final_score_uses_business_leadership_and_culture_formula(tmp_path):
    source_path = tmp_path / "source.json"
    source_path.write_text('{"processed": true}', encoding="utf-8")
    roster = managers()
    analysis = FakeAgent().analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )
    source = FakeSource()
    payloads = [
        {
            "manager": {"name": manager.name, "userId": manager.user_id},
            "liveOkr": source.fetch_user_okr(
                user_id=manager.user_id, period_label="2026 Q3"
            ),
        }
        for manager in roster
    ]

    cards = _manager_scorecards(analysis, payloads)

    assert cards["u1"].business_score == 80.0
    assert cards["u1"].leadership_score == 70.0
    assert cards["u1"].culture_score == 80.0
    assert cards["u1"].culture_coefficient == 1.05
    assert cards["u1"].final_score == 80.9


def test_kr_binding_uses_live_order_when_model_paraphrases_all_titles():
    review = ManagerReportAnalysis.model_construct(
        name="甲",
        kr_reviews=[
            KrScoreReview.model_construct(
                kr_id="", objective_title="改写后的目标", kr_title="改写后的第一条"
            ),
            KrScoreReview.model_construct(
                kr_id="", objective_title="改写后的目标", kr_title="改写后的第二条"
            ),
        ],
    )
    expected = {
        "live-1": {"objectiveTitle": "O", "krTitle": "第一条"},
        "live-2": {"objectiveTitle": "O", "krTitle": "第二条"},
    }

    weekly_okr_report_module._bind_kr_reviews_to_live_rows(review, expected)

    assert [item.kr_id for item in review.kr_reviews] == ["live-1", "live-2"]


@pytest.mark.parametrize(
    "broken_field",
    ["userId", "periodLabel", "source", "period", "periods", "objectiveList"],
)
def test_empty_source_without_authority_receipt_is_source_failed(
    tmp_path, broken_field
):
    class IncompleteSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                payload = _empty_live_payload(user_id, period_label)
                payload.pop(broken_field)
                return payload
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    items = _collect(IncompleteSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert outcomes[0]["kind"] == "source_failed"
    assert outcomes[0]["scope"] == "shared"
    assert outcomes[0]["validationPhase"] == "receipt"


@pytest.mark.parametrize(
    "corruption",
    [
        "wrong_user",
        "wrong_period",
        "wrong_selected_period",
        "raw_objectives",
        "no_capture",
    ],
)
def test_inconsistent_empty_receipt_cannot_establish_goals_missing(
    tmp_path, corruption
):
    class InconsistentSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id != "u1":
                return super().fetch_user_okr(
                    user_id=user_id, period_label=period_label
                )
            payload = _empty_live_payload(user_id, period_label)
            if corruption == "wrong_user":
                payload["userId"] = "someone_else"
            elif corruption == "wrong_period":
                payload["periodLabel"] = "2026 Q2"
            elif corruption == "wrong_selected_period":
                payload["period"]["name"] = "2026 Q2"
                payload["periods"] = [payload["period"]]
            elif corruption == "raw_objectives":
                payload["objectiveList"] = [{"id": "still-present"}]
                payload["source"]["objectiveListReceipt"]["count"] = 1
            else:
                payload["source"].pop("capturedAt")
            return payload

    items = _collect(InconsistentSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert outcomes[0]["kind"] == "source_failed"
    assert outcomes[0]["scope"] == (
        "member" if corruption == "raw_objectives" else "shared"
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "user",
        "requested_period",
        "provider_code",
        "boolean_code",
        "incomplete",
        "source",
        "timestamp",
        "status",
        "has_more",
        "current_period_alias",
        "missing_period_id",
        "zero_period_id",
        "not_a_period_list",
        "fabricated_processed",
    ],
)
def test_unverified_period_absence_envelopes_are_source_failed(tmp_path, corruption):
    class BrokenReceiptSource:
        def fetch_user_okr(self, *, user_id, period_label):
            payload = _missing_period_envelope(user_id, period_label)
            if corruption == "user":
                payload["userId"] = "other-user"
            elif corruption == "requested_period":
                payload["periodLabel"] = "2026 Q3"
            elif corruption == "provider_code":
                payload["availability"]["providerCode"] = 401
            elif corruption == "boolean_code":
                payload["availability"]["providerCode"] = False
            elif corruption == "incomplete":
                payload["availability"]["periodsComplete"] = False
            elif corruption == "source":
                payload["source"]["system"] = "unverified adapter"
            elif corruption == "timestamp":
                payload["source"]["capturedAt"] = "2026-10-07"
            elif corruption == "status":
                payload["availability"]["status"] = "authentication_failed"
            elif corruption == "has_more":
                payload["availability"]["hasMore"] = True
            elif corruption == "current_period_alias":
                payload["periods"] = [{"name": "2026年第四季度", "okrId": "q4"}]
            elif corruption == "missing_period_id":
                payload["periods"] = [{"name": "2026 Q3"}]
            elif corruption == "zero_period_id":
                payload["periods"] = [{"name": "2026 Q3", "okrId": 0}]
            elif corruption == "not_a_period_list":
                payload["periods"] = {"list": []}
            else:
                payload["processed"] = {"objectives": [], "okrRows": []}
            return payload

    items = _collect(BrokenReceiptSource(), "2026 Q4")
    outcomes = [item["sourceOutcome"] for item in items]
    assert all(
        outcome["kind"] == "source_failed" and outcome["scope"] == "shared"
        for outcome in outcomes
    )


@pytest.mark.parametrize(
    "proof",
    [
        None,
        {"providerCode": 401, "complete": True, "count": 0},
        {"providerCode": False, "complete": True, "count": 0},
        {"providerCode": 0, "complete": False, "count": 0},
        {"providerCode": 0, "complete": True, "count": 1},
        {"providerCode": 0, "complete": True, "count": False},
    ],
)
def test_empty_live_objectives_require_complete_provider_receipt(tmp_path, proof):
    class UnprovenSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id != "u1":
                return super().fetch_user_okr(
                    user_id=user_id, period_label=period_label
                )
            payload = _empty_live_payload(user_id, period_label)
            if proof is not None:
                payload["source"]["objectiveListReceipt"] = proof
            else:
                payload["source"].pop("objectiveListReceipt", None)
            return payload

    items = _collect(UnprovenSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert outcomes[0]["kind"] == "source_failed"
    assert outcomes[0]["scope"] == "shared"
    assert outcomes[0]["validationPhase"] == "receipt"


@pytest.mark.parametrize("invalid_id", ["", "duplicate"])
def test_invalid_source_kr_ids_are_collection_gaps(tmp_path, invalid_id):
    class InvalidKrSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            payload = super().fetch_user_okr(user_id=user_id, period_label=period_label)
            if user_id == "u1":
                rows = payload["processed"]["okrRows"]
                if invalid_id == "":
                    rows[0]["krId"] = ""
                else:
                    rows.append(dict(rows[0]))
            return payload

    class LaterOnlyAgent(FakeAgent):
        def analyze(self, **kwargs):
            assert [member.user_id for member in kwargs["managers"]] == ["u2"]
            return super().analyze(**kwargs)

    items = _collect(InvalidKrSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert outcomes[0]["kind"] == "source_failed"
    assert outcomes[0]["scope"] == "member"


def test_all_shared_auth_401_source_failures_still_block_publication(tmp_path):
    from app.okr_review import OkrLiveSourceError

    class SharedAuthSource:
        def fetch_user_okr(self, *, user_id, period_label):
            raise OkrLiveSourceError(
                scope="shared",
                code="401",
                detail="shared auth unavailable",
                user_id=user_id,
                period_label=period_label,
            )

    items = _collect(SharedAuthSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert all(
        outcome["kind"] == "source_failed" and outcome["scope"] == "shared"
        for outcome in outcomes
    )


@pytest.mark.parametrize(
    "corruption",
    ["identity", "source_authority", "objective_proof", "absence_identity"],
)
def test_untrusted_contract_after_valid_source_blocks_independent_of_count(
    tmp_path, corruption
):
    class InvalidContractSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            payload = super().fetch_user_okr(user_id=user_id, period_label=period_label)
            if user_id == "u2":
                if corruption == "identity":
                    payload["userId"] = "wrong-user"
                elif corruption == "source_authority":
                    payload["source"]["system"] = "untrusted provider"
                elif corruption == "objective_proof":
                    payload["source"]["objectiveListReceipt"]["complete"] = False
                else:
                    payload = _missing_period_envelope("wrong-user", period_label)
            return payload

    items = _collect(InvalidContractSource(), "2026 Q3")
    outcomes = [item["sourceOutcome"] for item in items]
    assert outcomes[0]["kind"] == "collected"
    assert outcomes[1]["kind"] == "source_failed"
    assert outcomes[1]["scope"] == "shared"


def _prepared(tmp_path, *, members=None, source=None, period="2026 Q3"):
    members = members or managers()
    source = source or FakeSource()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    roster = {
        "name": "CEO-2 管理群",
        "conversation_id": "cid-ceo-2",
        "managers": [asdict(member) for member in members],
    }
    report._write_json(inputs / "okr-roster.json", roster)
    for member in members:
        item = collect_weekly_okr_member(
            source=source, manager=member, period_label=period
        )
        report._write_json(inputs / f"okr-member-{member.user_id}.json", item)
    prepared = tmp_path / "prepared"
    plan = report.prepare(
        roster_file=inputs / "okr-roster.json",
        members_dir=inputs,
        period_label=period,
        report_date="2026-07-30" if period == "2026 Q3" else "2026-10-07",
        output_dir=prepared,
    )
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    for entry in plan["managers"]:
        if entry["outcome"] != "collected":
            continue
        member = next(item for item in members if item.user_id == entry["user_id"])
        agent = FakeAgent()
        agent.period_label = period
        analysis = agent.analyze(
            source_path=Path(entry["source_file"]),
            managers=[member],
            period_label=period,
            week_start=date(2026, 7, 1),
            week_end=date(2026, 7, 30),
        )
        report._write_json(analyses / entry["analysis_file"], {**analysis.model_dump(), "_source_sha256": entry["source_sha256"]})
    return plan, analyses


def test_skill_prepares_exact_schema_and_period_to_date(tmp_path):
    plan, _ = _prepared(tmp_path)
    assert plan["score_start"] == "2026-07-01"
    assert plan["score_end"] == "2026-07-30"
    source = report._read_json(Path(plan["source_file"]))
    assert source["currentWeekStart"] == "2026-07-24"
    assert source["groupConversationId"] == "cid-ceo-2"
    assert source["managers"][0]["manager"]["openDingtalkId"] == "o1"
    for entry in plan["managers"]:
        schema = report._read_json(Path(entry["schema_file"]))
        reviews = schema["properties"]["manager_reviews"]
        assert reviews["minItems"] == reviews["maxItems"] == 1
        props = reviews["items"]["properties"]
        assert props["kr_reviews"]["minItems"] == props["kr_reviews"]["maxItems"] == 1
        assert (
            props["culture_dimensions"]["minItems"]
            == props["culture_dimensions"]["maxItems"]
            == 3
        )
        assert "$ref" not in props["culture_dimensions"]


def test_skill_roundtrip_renders_complete_report_appendices_and_group_summary(tmp_path):
    plan, analyses = _prepared(tmp_path)
    links = tmp_path / "links.json"
    report._write_json(
        links, {"u1": "https://docs.example/u1", "u2": "https://docs.example/u2"}
    )
    result = report.render(
        source_file=Path(plan["source_file"]),
        analysis_dir=analyses,
        output_dir=tmp_path / "rendered",
        appendix_links_file=links,
        document_url="https://docs.example/main",
    )
    assert result["manager_count"] == result["scored_count"] == 2
    assert len(result["appendices"]) == 2
    main = Path(result["main_file"]).read_text()
    assert "2026-07-01—2026-07-30" in main
    assert "https://docs.example/u1" in main
    assert "https://docs.example/u2" in main
    assert "80.9" in main
    summary = Path(result["summary_file"]).read_text()
    assert "完整周报：[打开完整周报](https://docs.example/main)" in summary
    assert "publishable" not in result


def test_member_not_found_gap_hides_internal_http_status():
    text = report._gap_evidence(
        {
            "sourceOutcome": {"kind": "source_failed"},
            "providerReceipt": {
                "http_status": 404,
                "error_code": "member_not_found",
            },
        }
    )
    assert "404" not in text
    assert "无法确认该成员是否设有目标" in text
    assert "同轮名单及其他成员读取成功" in text


def test_same_named_members_keep_distinct_scored_appendices(tmp_path):
    members = [
        ManagerIdentity("甲", "总监", "u1", "o1"),
        ManagerIdentity("甲", "经理", "u2", "o2"),
    ]
    plan, analyses = _prepared(tmp_path, members=members)
    result = report.render(
        source_file=Path(plan["source_file"]),
        analysis_dir=analyses,
        output_dir=tmp_path / "rendered",
    )
    assert {item["user_id"] for item in result["appendices"]} == {"u1", "u2"}
    assert len({item["path"] for item in result["appendices"]}) == 2
    for item in result["appendices"]:
        assert f"甲 [{item['user_id']}]" in Path(item["path"]).read_text()


@pytest.mark.parametrize("all_absent", [False, True])
def test_verified_missing_q4_periods_render_full_roster(tmp_path, all_absent):
    class PeriodSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1" or all_absent:
                return _missing_period_envelope(user_id, period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    plan, analyses = _prepared(tmp_path, source=PeriodSource(), period="2026 Q4")
    result = report.render(
        source_file=Path(plan["source_file"]),
        analysis_dir=analyses,
        output_dir=tmp_path / "rendered",
    )
    assert len(result["appendices"]) == 2
    assert result["scored_count"] == (0 if all_absent else 1)
    public = Path(result["full_file"]).read_text()
    assert "低于 60 分：" in public
    assert "系统回填尚未闭环" not in public
    assert "当前主要原因是" not in public
    assert "当前周期暂无已登记目标" in public
    assert "goals_missing" not in public
    assert "2026-10-01" in public
    assert "暂不形成评分，不按零分处理" in public
    assert "providerCode" not in public
    assert "2026 Q3" not in public


@pytest.mark.parametrize("scope", ["member", "shared"])
def test_source_failure_scope_remains_explicit_and_diagnostics_private(tmp_path, scope):
    from app.okr_review import OkrLiveSourceError

    class Source(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            raise OkrLiveSourceError(
                scope=scope,
                code="403",
                detail="PRIVATE_PATH token=PRIVATE_TOKEN",
                user_id=user_id,
                period_label=period_label,
            )

    if scope == "shared":
        with pytest.raises(ValueError, match="shared source collection failed"):
            _prepared(tmp_path, source=Source())
        return
    plan, analyses = _prepared(tmp_path, source=Source())
    result = report.render(
        source_file=Path(plan["source_file"]),
        analysis_dir=analyses,
        output_dir=tmp_path / "rendered",
    )
    assert result["scored_count"] == 0
    text = Path(result["full_file"]).read_text()
    assert "HTTP 403" not in text
    assert "当前周期成员目标记录未能读取" in text
    assert "source_failed" not in text
    assert "PRIVATE_PATH" not in text and "PRIVATE_TOKEN" not in text
    assert "PRIVATE_PATH" in Path(plan["source_file"]).read_text()


def test_unexpected_collection_typeerror_propagates():
    class BuggySource:
        def fetch_user_okr(self, **kwargs):
            raise TypeError("source programming bug")

    with pytest.raises(TypeError, match="programming bug"):
        _collect(BuggySource())


def test_terminal_analysis_diagnostics_are_not_public_evidence(tmp_path):
    plan, analyses = _prepared(tmp_path)
    report._write_json(
        analyses / plan["managers"][0]["analysis_file"],
        {"_source_sha256": plan["managers"][0]["source_sha256"], "analysis_failure": {"diagnostic": "/PRIVATE_ARTIFACT token=PRIVATE_TOKEN"}},
    )
    result = report.render(
        source_file=Path(plan["source_file"]),
        analysis_dir=analyses,
        output_dir=tmp_path / "rendered",
    )
    assert result["scored_count"] == 1
    text = Path(result["full_file"]).read_text()
    assert "analysis_failed" in text
    assert "PRIVATE_ARTIFACT" not in text and "PRIVATE_TOKEN" not in text


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_member",
        "duplicate_member",
        "wrong_owner",
        "wrong_name",
        "missing_kr",
        "extra_kr",
        "score_above_base",
        "culture",
        "leadership",
    ],
)
def test_skill_rejects_incomplete_or_unbound_analysis(tmp_path, corruption):
    plan, analyses = _prepared(tmp_path)
    path = analyses / plan["managers"][0]["analysis_file"]
    payload = report._read_json(path)
    review = payload["manager_reviews"][0]
    if corruption == "missing_member":
        payload["manager_reviews"] = []
    elif corruption == "duplicate_member":
        payload["manager_reviews"].append(review)
    elif corruption == "wrong_owner":
        review["user_id"] = "u2"
    elif corruption == "wrong_name":
        review["name"] = "另一人"
    elif corruption == "missing_kr":
        review["kr_reviews"] = []
    elif corruption == "extra_kr":
        review["kr_reviews"].append(review["kr_reviews"][0])
    elif corruption == "score_above_base":
        review["kr_reviews"][0]["score"] = 100
    elif corruption == "culture":
        review["culture_dimensions"].pop()
    else:
        review["leadership_dimensions"].pop()
    report._write_json(path, payload)
    with pytest.raises(ValueError):
        report.render(
            source_file=Path(plan["source_file"]),
            analysis_dir=analyses,
            output_dir=tmp_path / "rendered",
        )
    assert not (tmp_path / "rendered/main.md").exists()


@pytest.mark.parametrize(
    "value, expected",
    [
        (100, 1.2),
        (99, 1.1),
        (90, 1.1),
        (89, 1.05),
        (80, 1.05),
        (79, 1.0),
        (70, 1.0),
        (69, 0.95),
        (50, 0.95),
        (49, 0.8),
    ],
)
def test_culture_coefficient_boundaries(value, expected):
    assert report._culture_coefficient(value) == expected


@pytest.mark.parametrize(
    "role, leadership_count, expected",
    [("专业贡献者", 0, 84.0), ("专业贡献者", 4, 84.0), ("职级待确认", 0, None)],
)
def test_individual_contributor_and_unknown_role_formulas(
    tmp_path, role, leadership_count, expected
):
    plan, analyses = _prepared(tmp_path)
    path = analyses / plan["managers"][0]["analysis_file"]
    payload = report._read_json(path)
    payload["manager_reviews"][0]["role_level"] = role
    payload["manager_reviews"][0]["leadership_dimensions"] = [
        item.model_dump() for item in _dimensions(leadership_count, 10)
    ]
    report._write_json(path, payload)
    source = report._read_json(Path(plan["source_file"]))
    analysis, _ = report._analysis_from_files(source, analyses)
    assert (
        report._manager_scorecards(analysis, source["managers"])["u1"].final_score
        == expected
    )


def test_missing_analysis_file_is_not_silently_converted_into_a_gap(tmp_path):
    plan, analyses = _prepared(tmp_path)
    with pytest.raises(FileNotFoundError):
        report.render(
            source_file=Path(plan["source_file"]),
            analysis_dir=tmp_path / "absent",
            output_dir=tmp_path / "rendered",
        )


def test_skill_runs_standalone_without_application_imports(tmp_path):
    import ast
    import os
    import subprocess

    plan, analyses = _prepared(tmp_path)
    env = dict(os.environ, PYTHONPATH="")
    output_dir = tmp_path / "standalone"
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_SCRIPT),
            "render",
            "--source",
            plan["source_file"],
            "--analysis-dir",
            str(analyses),
            "--output-dir",
            str(output_dir),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["scored_count"] == 2
    rendered = (output_dir / "main.md").read_text(encoding="utf-8")
    assert "### 已核实进展" in rendered
    assert "本周重点进展" not in rendered
    assert "截至本周" not in rendered
    assert "goals_missing" not in rendered
    assert "source_failed" not in rendered
    import runpy

    gap_status = runpy.run_path(str(SKILL_SCRIPT))["_gap_status_label"]
    gap_evidence = runpy.run_path(str(SKILL_SCRIPT))["_gap_evidence"]
    assert gap_status("goals_missing") == "当前周期暂无已登记目标"
    assert gap_status("source_failed") == "当前周期成员目标记录未能读取"
    assert gap_evidence({"sourceOutcome": {"kind": "goals_missing"}}) == (
        "已核查当前周期目标列表为空，当前没有登记该成员的 OKR 目标。"
    )
    assert gap_evidence({
        "sourceOutcome": {"kind": "source_failed"},
        "providerReceipt": {"http_status": 404, "error_code": "member_not_found"},
    }) == "当前季度 OKR 目标记录未能读取，无法确认该成员是否设有目标；同轮名单及其他成员读取成功。"
    imports = [
        node.module
        for node in ast.walk(ast.parse(SKILL_SCRIPT.read_text()))
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any(name and name.startswith("app") for name in imports)


def test_source_module_has_no_hidden_agent_publisher_or_report_models():
    import app.weekly_okr_report as source_module

    for name in [
        "CodexWeeklyOkrAgent",
        "run_weekly_okr_report",
        "weekly_okr_report_command",
        "_WeeklyOkrReportRunLease",
        "ManagerReportAnalysis",
        "_manager_scorecards",
        "render_weekly_okr_report",
    ]:
        assert not hasattr(source_module, name)
    assert not hasattr(source_module.DwsWeeklyOkrGateway, "publish_document")
    assert not hasattr(source_module.DwsWeeklyOkrGateway, "send_group_summary")


def test_business_formula_uses_o_times_kr_weights_and_excludes_other_categories(
    tmp_path,
):
    import copy

    plan, analyses = _prepared(tmp_path)
    source = report._read_json(Path(plan["source_file"]))
    analysis, _ = report._analysis_from_files(source, analyses)
    review = analysis.manager_reviews[0]
    rows = source["managers"][0]["liveOkr"]["processed"]["okrRows"]
    rows[0]["objectiveWeight"] = 20
    rows.append({**rows[0], "krId": "kr-2", "objectiveWeight": 80})
    rows.append({**rows[0], "krId": "kr-3", "objectiveWeight": 100})
    second = copy.deepcopy(review.kr_reviews[0])
    second.kr_id = "kr-2"
    second.score = second.base_score = 50
    third = copy.deepcopy(review.kr_reviews[0])
    third.kr_id = "kr-3"
    third.score = third.base_score = 0
    third.category = "领导力"
    review.kr_reviews.extend([second, third])
    review.culture_dimensions = _dimensions(3, 100)
    card = report._manager_scorecards(analysis, source["managers"])["u1"]
    assert card.business_score == 56.0
    assert card.final_score == 72.2
    rows[0]["objectiveWeight"] = rows[1]["objectiveWeight"] = 0
    card = report._manager_scorecards(analysis, source["managers"])["u1"]
    assert card.business_score is None
    assert card.final_score is None
    assert card.final_status == "缺少可计权业务 OKR"


def test_member_api_is_one_bounded_read_and_returns_source_artifact(
    monkeypatch, capsys
):
    import app.weekly_okr_report as source_module

    calls = []

    class ConfiguredSource(FakeSource):
        def __init__(self, **kwargs):
            super().__init__()
            calls.append(kwargs)

    monkeypatch.setenv(
        "CEO_OKR_LIVE_SOURCE_COMMAND",
        "python source.py --user-id {user_id} --period-label {period_label}",
    )
    monkeypatch.setattr(
        source_module, "_configured_dws", lambda settings: "configured-dws"
    )
    monkeypatch.setattr(source_module, "DwsLiveOkrSource", ConfiguredSource)
    settings = SimpleNamespace(task_codex_timeout_seconds=7200)
    item = source_module.weekly_okr_member(
        settings,
        user_id="u1",
        name="甲",
        title="总监",
        open_dingtalk_id="o1",
        period_label="2026 Q3",
    )
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["timeout_seconds"] == 240
    assert item["manager"]["openDingtalkId"] == "o1"
    assert item["sourceOutcome"]["kind"] == "collected"
    assert capsys.readouterr().out == ""


def test_changed_member_source_rejects_reuse_of_previous_analysis(tmp_path):
    plan, analyses = _prepared(tmp_path)
    source = report._read_json(Path(plan["source_file"]))
    source["managers"][0]["liveOkr"]["generatedAt"] = "new source revision"
    with pytest.raises(ValueError, match="source changed"):
        report._analysis_from_files(source, analyses)


def test_audit_source_artifact_does_not_enter_consumer_preparation(tmp_path):
    plan, _ = _prepared(tmp_path)
    member_input = tmp_path / "inputs"
    original = next(member_input.glob("okr-member-*.json"))
    (member_input / ("audit-" + original.name)).write_bytes(original.read_bytes())
    repeated = report.prepare(roster_file=member_input / "okr-roster.json", members_dir=member_input,
        period_label="2026 Q3", report_date="2026-07-30", output_dir=tmp_path / "again")
    assert len(repeated["managers"]) == len(plan["managers"])
