import json
import threading
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import app.weekly_okr_report as weekly_okr_report_module
from app.agent_runtime_router import CodexCommandFactory
from app.codex_decision import append_signature
from app.store import AutoReplyStore
from app.weekly_okr_report import (
    DEFAULT_ARCHIVE_DIR_NAME,
    LATEST_ARCHIVE_INDEX_NAME,
    LATEST_ARCHIVE_RAW_NAME,
    CeoAttentionItem,
    CodexWeeklyOkrAgent,
    DimensionScoreReview,
    DwsWeeklyOkrGateway,
    GroupRoster,
    KrScoreReview,
    ManagerIdentity,
    ManagerReportAnalysis,
    PublishedDocument,
    WeeklyOkrAnalysis,
    WeeklyOkrReportResult,
    _extract_report_payload,
    _manager_scorecards,
    refresh_company_okr_archive,
    render_group_summary,
    run_weekly_okr_report,
    weekly_okr_report_window_open,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


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


class FakeStore:
    def __init__(self):
        self.state = {}

    def get_service_state(self, key):
        return self.state.get(key, "")

    def set_service_state(self, key, value):
        self.state[key] = value


class CallbackRouted:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        raw = self.callback([], kwargs["prompt"], {})
        try:
            value = kwargs["parser"](raw)
        except Exception as exc:
            retry = kwargs.get("result_validation_retry")
            if retry is None:
                raise
            corrected = retry.corrected_prompt(kwargs["prompt"], exc)
            raw = self.callback([], corrected, {})
            value = kwargs["parser"](raw)
        return SimpleNamespace(
            value=value,
            session_id="weekly-test-session",
        )


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


class CreateDecodeRecoveryDws:
    dws_bin = "dws"

    def __init__(self, marker):
        self.marker = marker
        self.list_calls = 0

    def run_json(self, command, **_kwargs):
        if command[1:4] == ["wiki", "node", "list"]:
            self.list_calls += 1
            if self.list_calls <= 2:
                return {"nodes": []}
            return {
                "nodes": [
                    {
                        "name": self.marker,
                        "nodeId": "doc-recovered",
                        "docUrl": "https://alidocs.example/doc-recovered",
                    }
                ]
            }
        if command[1:3] == ["doc", "create"]:
            raise UnicodeDecodeError("utf-8", b"\xe5", 0, 1, "invalid")
        if command[1:3] == ["doc", "update"]:
            return {"success": True}
        if command[1:3] == ["doc", "read"]:
            return {"title": self.marker}
        raise AssertionError(command)


class ExistingDocumentDws:
    dws_bin = "dws"

    def __init__(self, marker):
        self.marker = marker
        self.commands = []
        self.read_calls = 0

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        if command[1:4] == ["wiki", "node", "list"]:
            return {
                "nodes": [
                    {
                        "name": "weekly-title",
                        "nodeId": "doc-existing",
                        "docUrl": "https://alidocs.example/doc-existing",
                    }
                ]
            }
        if command[1:3] == ["doc", "read"]:
            self.read_calls += 1
            return {"content": "旧内容" if self.read_calls == 1 else self.marker}
        if command[1:3] == ["doc", "update"]:
            return {"success": True}
        raise AssertionError(command)


class ExistingDocumentUpdateDecodeDws(ExistingDocumentDws):
    def run_json(self, command, **kwargs):
        if command[1:3] == ["doc", "update"]:
            self.commands.append(command)
            raise UnicodeDecodeError("utf-8", b"\xe4", 0, 1, "invalid")
        return super().run_json(command, **kwargs)


class RelocateExistingDocumentDws:
    dws_bin = "dws"

    def __init__(self, marker):
        self.marker = marker
        self.commands = []
        self.target_list_calls = 0
        self.read_calls = 0

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        if command[1:4] == ["wiki", "node", "list"]:
            folder_id = command[command.index("--folder") + 1]
            if folder_id == "doc-main":
                self.target_list_calls += 1
                if self.target_list_calls == 1:
                    return {"nodes": []}
                return {
                    "nodes": [
                        {
                            "name": "weekly-title｜评分附录｜甲",
                            "nodeId": "doc-appendix",
                            "docUrl": "https://alidocs.example/doc-appendix",
                        }
                    ]
                }
            if folder_id == "folder-old":
                return {
                    "nodes": [
                        {
                            "name": "weekly-title｜评分附录｜甲",
                            "nodeId": "doc-appendix",
                            "docUrl": "https://alidocs.example/doc-appendix",
                        }
                    ]
                }
        if command[1:4] == ["wiki", "node", "move"]:
            return {"success": True}
        if command[1:3] == ["doc", "read"]:
            self.read_calls += 1
            return {"content": "旧内容" if self.read_calls == 1 else self.marker}
        if command[1:3] == ["doc", "update"]:
            return {"success": True}
        raise AssertionError(command)


class FakeGateway:
    def __init__(self, managers):
        self.managers = managers
        self.published = []
        self.ensured = []
        self.sent = []

    def resolve_group_roster(self, group_name):
        assert group_name == "CEO-2 管理群"
        return GroupRoster(group_name, "cid-ceo-2", self.managers)

    def resolve_wiki(self, wiki_name):
        assert wiki_name == "🎯  目标与执行"
        return "wiki-1"

    def ensure_folder(self, *, workspace_id, folder_name):
        assert workspace_id == "wiki-1"
        assert folder_name == "管理者 OKR 进度周报"
        return "folder-1"

    def ensure_document(self, *, workspace_id, folder_id, name):
        assert workspace_id == "wiki-1"
        assert folder_id == "folder-1"
        self.ensured.append((folder_id, name))
        return PublishedDocument("doc-main", "https://alidocs.example/doc-main")

    def publish_document(
        self,
        *,
        workspace_id,
        folder_id,
        name,
        content_file,
        verification_marker,
        migration_folder_id="",
    ):
        assert workspace_id == "wiki-1"
        if "评分附录" in name:
            assert folder_id == "doc-main"
            assert migration_folder_id == "folder-1"
        else:
            assert folder_id == "folder-1"
            assert migration_folder_id == ""
        content = content_file.read_text(encoding="utf-8")
        assert verification_marker in content
        self.published.append((name, content, folder_id))
        if "评分附录" in name:
            slug = name.rsplit("｜", 1)[-1]
            return PublishedDocument(
                f"doc-{slug}", f"https://alidocs.example/doc-{slug}"
            )
        return PublishedDocument("doc-main", "https://alidocs.example/doc-main")

    def send_group_summary(self, *, conversation_id, title, text):
        assert conversation_id == "cid-ceo-2"
        assert title in text
        assert "https://alidocs.example/doc-main" in text
        self.sent.append(text)
        return "sent"


class MessageDws:
    def __init__(self):
        self.sent = []

    def send_message(self, conversation_id, text, **kwargs):
        self.sent.append(
            {"conversation_id": conversation_id, "text": text, **kwargs}
        )
        return {"success": True}

    def verify_message_send_result(self, send_result):
        raise AssertionError("application must not read back a provider success")


def test_weekly_okr_group_summary_includes_assistant_postfix(tmp_path):
    dws = MessageDws()
    store = AutoReplyStore(tmp_path / "weekly.sqlite3")

    assert DwsWeeklyOkrGateway(dws, store=store).send_group_summary(
        conversation_id="cid-ceo-2",
        title="2026-W35 管理周报",
        text="2026-W35 管理周报\n\n本周完成 3 项。",
    ) == "sent"

    assert dws.sent[0]["text"].endswith("（by明哥分身）")
    prepared = store.get_outbound_postfix(
        "dingtalk", "weekly-okr:cid-ceo-2:2026-W35 管理周报"
    )
    assert prepared is not None
    assert prepared.final_body == dws.sent[0]["text"]
    assert DwsWeeklyOkrGateway(dws, store=store).send_group_summary(
        conversation_id="cid-ceo-2",
        title="2026-W35 管理周报",
        text="重试时不应重写正文",
    ) == "sent"
    assert len(dws.sent) == 1


class FakeSource:
    def __init__(self):
        self.calls = []

    def fetch_user_okr(self, *, user_id, period_label):
        self.calls.append((user_id, period_label))
        return {
            "source": {"system": "叮当OKR Dingteam Web (direct API)", "capturedAt": "2026-07-30T04:00:00Z", "objectiveListReceipt": {"providerCode": 0, "complete": True, "count": 1}},
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


def test_force_run_publishes_verified_document_then_group_summary(tmp_path):
    store = FakeStore()
    gateway = FakeGateway(managers())
    source = FakeSource()

    result = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=source,
        agent=FakeAgent(),
        workspace=tmp_path,
        now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True,
        deliver=True,
        period_label="2026 Q3",
    )

    assert result.status == "sent"
    assert result.manager_count == 2
    assert result.send_state == "sent"
    assert source.calls == [("u1", "2026 Q3"), ("u2", "2026 Q3")]
    assert gateway.published and gateway.sent
    assert len(gateway.published) == 3
    published = next(
        content
        for name, content, _folder_id in gateway.published
        if "评分附录" not in name
    )
    assert "## 管理会审阅页" in published
    assert "### 管理者审阅表" in published
    assert "这是截至本周的证据完成度快照，不是季度末绩效预测" in published
    assert "## 逐人评分附录" in published
    assert "## 附录：逐人证据与逐 KR 评分" not in published
    assert "[甲｜总监](https://alidocs.example/doc-甲)" in published
    appendix = next(
        content
        for name, content, _folder_id in gateway.published
        if name.endswith("评分附录｜甲")
    )
    assert "### 甲｜总监" in appendix
    assert "## 附录校验：甲" in appendix
    assert gateway.ensured == [
        (
            "folder-1",
            "CEO-2 管理者 OKR 进度周报（2026 Q3 至今：2026-07-01—2026-07-30）",
        )
    ]
    assert {
        folder_id
        for name, _content, folder_id in gateway.published
        if "评分附录" in name
    } == {"doc-main"}
    assert store.state["weekly_okr_report:last_success_date"] == "2026-07-30"


def test_a_report_with_one_isolated_manager_does_not_publish(tmp_path):
    """A partial roster must stop publication until every member is scored."""
    store = FakeStore()
    gateway = FakeGateway(managers())
    source = FakeSource()

    class PartiallyIsolatingAgent(FakeAgent):
        def analyze(self, **kwargs):
            analysis = super().analyze(**kwargs)
            kept = [
                review for review in analysis.manager_reviews if review.name == "甲"
            ]
            return analysis.model_copy(
                update={
                    "executive_summary": "已完成 1 / 2 位 CEO-2 成员的逐 KR 综合证据评分；"
                    "本次未能完成评分的成员：乙，其区块缺失，不代表这些成员没有进展。",
                    "manager_reviews": kept,
                    "warnings": ["乙 的 KR 评分未完成，本次报告缺少该成员区块：runtime_result_validation_failed"],
                }
            )

    with pytest.raises(ValueError, match="manager coverage mismatch"):
        run_weekly_okr_report(
            store=store,
            gateway=gateway,
            source=source,
            agent=PartiallyIsolatingAgent(),
            workspace=tmp_path,
            now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True,
            deliver=True,
            period_label="2026 Q3",
        )

    assert gateway.published == []
    assert "weekly_okr_report:last_success_date" not in store.state


@pytest.mark.parametrize("message", [
    "opaque source failure",
    "Dingteam OKR period not found: 2026 Q3",
    "source diagnostic " + "x" * 300,
])
def test_source_failure_collects_later_members_and_persists_failure(tmp_path, message):
    from app.okr_review import OkrLiveSourceError

    class BrokenSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                self.calls.append((user_id, period_label))
                raise OkrLiveSourceError(scope="member", code="member_read_failed", detail=message, user_id=user_id, period_label=period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    source = BrokenSource()
    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=source, agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert result.status == "sent"
    assert [user_id for user_id, _ in source.calls] == ["u1", "u2"]
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "source_failed"
    assert raw["managers"][0]["sourceOutcome"]["scope"] == "member"
    assert "liveOkr" not in raw["managers"][0]
    assert raw["managers"][1]["sourceOutcome"]["kind"] == "collected"
    assert len(gateway.published) == 3
    appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
    assert "source_failed" in appendix
    assert message not in appendix
    assert f"OkrLiveSourceError: member_read_failed: {message}" in raw["managers"][0]["sourceOutcome"]["diagnostic"]
    assert "goals_missing" not in appendix
    assert "#### 逐 KR 评分" not in appendix
    assert "已形成最终分：1 人；暂不形成：1 人" in gateway.published[-1][1]


def test_validated_empty_source_keeps_every_member_appendix(tmp_path):
    class EmptySource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                return _empty_live_payload(user_id, period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=EmptySource(), agent=FakeAgent(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.manager_count == 2
    appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
    assert "goals_missing" in appendix
    assert "u1" in appendix
    assert "周期已建立" in appendix
    assert "完整实时目标列表为空" in appendix
    assert "个人周期尚未建立" not in appendix
    assert "#### 逐 KR 评分" not in appendix
    assert len(gateway.published) == 3


def test_report_orchestration_returns_typed_wait_result_for_in_progress(tmp_path):
    store = FakeStore()
    gateway = FakeGateway(managers())

    class InProgressAgent:
        def analyze(self, **_kwargs):
            raise weekly_okr_report_module.WeeklyOkrAnalysisInProgress(
                job_id=17,
                manager_user_id="u1",
            )

    result = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=FakeSource(),
        agent=InProgressAgent(),
        workspace=tmp_path,
        now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True,
        deliver=True,
        period_label="2026 Q3",
    )

    assert result == WeeklyOkrReportResult(
        status="analysis_in_progress",
        report_date="2026-07-30",
        period_label="2026 Q3",
        manager_count=2,
    )
    assert gateway.published == []
    assert gateway.sent == []


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
    raw_path = tmp_path / DEFAULT_ARCHIVE_DIR_NAME / "2026q3" / "company_okr_2026q3_raw.json"
    index_path = tmp_path / DEFAULT_ARCHIVE_DIR_NAME / "2026q3" / "company_okr_2026q3_index.md"
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


def test_scheduled_run_waits_until_sunday_hour_and_deduplicates(tmp_path):
    store = FakeStore()
    gateway = FakeGateway(managers())
    source = FakeSource()
    agent = FakeAgent()

    before = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=source,
        agent=agent,
        workspace=tmp_path,
        now=datetime(2026, 8, 2, 17, 59, tzinfo=SHANGHAI),
        force=False,
        deliver=True,
        period_label="2026 Q3",
    )
    assert before.status == "not_due"
    assert source.calls == []

    sent = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=source,
        agent=agent,
        workspace=tmp_path,
        now=datetime(2026, 8, 2, 18, 0, tzinfo=SHANGHAI),
        force=False,
        deliver=True,
        period_label="2026 Q3",
    )
    assert sent.status == "sent"

    duplicate = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=source,
        agent=agent,
        workspace=tmp_path,
        now=datetime(2026, 8, 2, 20, 0, tzinfo=SHANGHAI),
        force=False,
        deliver=True,
        period_label="2026 Q3",
    )
    assert duplicate.status == "not_due"
    assert len(gateway.sent) == 1


def test_scheduled_run_recovers_a_missed_sunday_on_monday(tmp_path):
    store = FakeStore()
    store.state["weekly_okr_report:last_success_date"] = "2026-07-26"
    gateway = FakeGateway(managers())
    source = FakeSource()

    result = run_weekly_okr_report(
        store=store,
        gateway=gateway,
        source=source,
        agent=FakeAgent(),
        workspace=tmp_path,
        now=datetime(2026, 8, 3, 9, tzinfo=SHANGHAI),
        force=False,
        deliver=True,
        period_label="2026 Q3",
    )

    assert result.status == "sent"
    assert result.report_date == "2026-08-02"
    assert store.state["weekly_okr_report:last_success_date"] == "2026-08-02"
    assert gateway.ensured == [
        (
            "folder-1",
            "CEO-2 管理者 OKR 进度周报（2026 Q3 至今：2026-07-01—2026-08-02）",
        )
    ]
    assert weekly_okr_report_window_open(
        datetime(2026, 8, 3, 9, tzinfo=SHANGHAI),
        schedule_hour=18,
    )


def test_extract_report_payload_reads_final_codex_jsonl_message():
    payload = {
        "executive_summary": "摘要",
        "company_progress": [],
        "ceo_attention_items": [],
        "manager_reviews": [
            {
                "name": "甲",
                "role_level": "总监",
                "role_level_evidence": "钉钉通讯录当前职务为总监",
                "progress_summary": "推进中",
                "key_progress": [],
                "independent_evidence": [],
                "evidence_assessment": "证据不足",
                "risks": [],
                "next_week_focus": [],
                "data_gaps": ["缺少验收记录"],
                "kr_reviews": [
                    {
                        "kr_id": "kr-1",
                        "objective_title": "O1",
                        "kr_title": "KR1",
                        "category": "业务OKR",
                        "system_progress": "系统进度 50%",
                        "independent_evidence": "已读取交付文档",
                        "evidence_assessment": "有产出，效果待验证",
                        "base_score": 80,
                        "time_discount": "未适用",
                        "score": 80,
                        "improvement": "补充验收记录",
                    }
                ],
                "leadership_dimensions": [item.model_dump() for item in _dimensions(4, 70)],
                "culture_dimensions": [item.model_dump() for item in _dimensions(3, 80)],
            }
        ],
        "source_coverage": ["实时叮当 OKR"],
        "warnings": [],
    }
    raw = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"item":{"type":"agent_message","text":'
            + json_string(payload)
            + "}}",
        ]
    )

    assert _extract_report_payload(raw)["executive_summary"] == "摘要"


def test_extract_report_payload_validation_failure_names_the_bad_field():
    # Derek 2026-09-28 "所有底层错误码都要带着到 agent 的报错里面": this used to
    # raise a fixed "Codex weekly OKR payload failed validation" with no field
    # detail -- the persisted weekly_okr_analysis_jobs.error column then said
    # only that, with no way to tell what Codex actually got wrong.
    payload = {
        "executive_summary": "摘要",
        "company_progress": [],
        "ceo_attention_items": [],
        "manager_reviews": [
            {
                # "name" deliberately omitted: a required field.
                "role_level": "总监",
                "role_level_evidence": "钉钉通讯录当前职务为总监",
                "progress_summary": "推进中",
                "key_progress": [],
                "independent_evidence": [],
                "evidence_assessment": "证据不足",
                "risks": [],
                "next_week_focus": [],
                "data_gaps": [],
                "kr_reviews": [],
                "leadership_dimensions": [item.model_dump() for item in _dimensions(4, 70)],
                "culture_dimensions": [item.model_dump() for item in _dimensions(3, 80)],
            }
        ],
        "source_coverage": ["实时叮当 OKR"],
        "warnings": [],
    }
    raw = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"item":{"type":"agent_message","text":' + json_string(payload) + "}}",
        ]
    )

    with pytest.raises(ValueError) as excinfo:
        _extract_report_payload(raw)

    message = str(excinfo.value)
    assert "Codex weekly OKR payload failed validation" in message
    assert "name" in message
    assert message != "Codex weekly OKR payload failed validation"


def test_weekly_window_rejects_invalid_hour():
    with pytest.raises(ValueError, match="between 0 and 23"):
        weekly_okr_report_window_open(
            datetime(2026, 8, 2, 18, 0, tzinfo=SHANGHAI),
            schedule_hour=24,
        )


def test_weekly_command_schedule_is_owned_by_cron_not_legacy_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CEO_WEEKLY_OKR_REPORT_ENABLED", "false")
    monkeypatch.setenv("CEO_WEEKLY_OKR_REPORT_HOUR", "0")

    result = weekly_okr_report_module.weekly_okr_report_command(
        SimpleNamespace(db_path=tmp_path / "worker.sqlite3"),
        now=datetime(2026, 8, 2, 17, 0, tzinfo=SHANGHAI),
        quiet_not_due=True,
    )

    assert result.status == "not_due"


def test_resolve_wiki_lists_spaces_for_exact_emoji_name():
    dws = FakeDws(
        {
            "wikiSpaces": [
                {
                    "name": "🎯  目标与执行",
                    "workspaceId": "wiki-target",
                }
            ]
        }
    )

    assert DwsWeeklyOkrGateway(dws).resolve_wiki("🎯  目标与执行") == "wiki-target"
    assert dws.commands == [
        ["dws", "wiki", "space", "list", "--limit", "50", "--format", "json"]
    ]


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


def test_publish_document_recovers_when_cli_output_decode_fails_after_create(
    tmp_path,
):
    title = "CEO-2 管理者 OKR 进度周报（2026-07-27—2026-07-30）"
    content_file = tmp_path / "report.md"
    content_file.write_text(f"# {title}\n", encoding="utf-8")

    published = DwsWeeklyOkrGateway(CreateDecodeRecoveryDws(title)).publish_document(
        workspace_id="wiki-target",
        folder_id="folder-target",
        name=title,
        content_file=content_file,
        verification_marker=title,
    )

    assert published.node_id == "doc-recovered"
    assert published.url == "https://alidocs.example/doc-recovered"


def test_existing_weekly_document_is_overwritten_and_read_back(tmp_path):
    content_file = tmp_path / "report.md"
    content_file.write_text("# weekly-title\n\n综合证据评分\n", encoding="utf-8")
    dws = ExistingDocumentDws("综合证据评分")

    published = DwsWeeklyOkrGateway(dws).publish_document(
        workspace_id="wiki-target",
        folder_id="folder-target",
        name="weekly-title",
        content_file=content_file,
        verification_marker="综合证据评分",
    )

    assert published.node_id == "doc-existing"
    assert dws.read_calls == 2
    update = next(command for command in dws.commands if command[1:3] == ["doc", "update"])
    assert ["--mode", "overwrite"] == update[update.index("--mode") : update.index("--mode") + 2]
    assert "--yes" in update


def test_existing_weekly_document_uses_readback_after_update_decode_error(tmp_path):
    content_file = tmp_path / "report.md"
    content_file.write_text("# weekly-title\n\n末尾校验\n", encoding="utf-8")
    dws = ExistingDocumentUpdateDecodeDws("末尾校验")

    published = DwsWeeklyOkrGateway(dws).publish_document(
        workspace_id="wiki-target",
        folder_id="folder-target",
        name="weekly-title",
        content_file=content_file,
        verification_marker="末尾校验",
    )

    assert published.node_id == "doc-existing"
    assert dws.read_calls == 2


def test_existing_appendix_is_moved_under_main_document_and_read_back(tmp_path):
    content_file = tmp_path / "appendix.md"
    content_file.write_text("# 甲\n\n附录校验：甲\n", encoding="utf-8")
    dws = RelocateExistingDocumentDws("附录校验：甲")

    published = DwsWeeklyOkrGateway(dws).publish_document(
        workspace_id="wiki-target",
        folder_id="doc-main",
        migration_folder_id="folder-old",
        name="weekly-title｜评分附录｜甲",
        content_file=content_file,
        verification_marker="附录校验：甲",
    )

    assert published.node_id == "doc-appendix"
    move = next(
        command for command in dws.commands if command[1:4] == ["wiki", "node", "move"]
    )
    assert move == [
        "dws",
        "wiki",
        "node",
        "move",
        "--workspace",
        "wiki-target",
        "--node",
        "doc-appendix",
        "--folder",
        "doc-main",
        "--format",
        "json",
    ]
    assert dws.target_list_calls == 2
    assert dws.read_calls == 2


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
            "liveOkr": source.fetch_user_okr(user_id=manager.user_id, period_label="2026 Q3"),
        }
        for manager in roster
    ]

    cards = _manager_scorecards(analysis, payloads)

    assert cards["u1"].business_score == 80.0
    assert cards["u1"].leadership_score == 70.0
    assert cards["u1"].culture_score == 80.0
    assert cards["u1"].culture_coefficient == 1.05
    assert cards["u1"].final_score == 80.9


def test_codex_agent_analyzes_each_manager_in_a_bounded_source_file(tmp_path):
    import json
    from pathlib import Path

    roster = managers()
    source = FakeSource()
    source_path = tmp_path / "live.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                    for manager in roster
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen = []

    def executor(_command, prompt, _env):
        source_line = next(
            line for line in prompt.splitlines() if line.startswith("- 实时叮当 OKR 聚合文件：")
        )
        filtered_path = Path(source_line.split("：", 1)[1])
        filtered = json.loads(filtered_path.read_text(encoding="utf-8"))
        assert len(filtered["managers"]) == 1
        name = filtered["managers"][0]["manager"]["name"]
        seen.append(name)
        return json.dumps(_weekly_payload_for(name), ensure_ascii=False)

    store = AutoReplyStore(tmp_path / "weekly-bounded.sqlite3")
    routed = CallbackRouted(executor)
    analysis = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=routed,
    ).analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )

    assert set(seen) == {"甲", "乙"}
    assert [review.name for review in analysis.manager_reviews] == ["甲", "乙"]
    assert all(review.kr_reviews[0].kr_id == "kr-1" for review in analysis.manager_reviews)

    refreshed_source = json.loads(source_path.read_text(encoding="utf-8"))
    for item in refreshed_source["managers"]:
        item["liveOkr"]["source"]["fetchedAt"] = "2026-07-30T04:00:00+08:00"
    source_path.write_text(
        json.dumps(refreshed_source, ensure_ascii=False),
        encoding="utf-8",
    )
    cached = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=CallbackRouted(executor),
    ).analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )

    assert len(seen) == 2
    assert [review.name for review in cached.manager_reviews] == ["甲", "乙"]


def test_codex_agent_parent_lease_covers_turn_and_validation_retry(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly-lease.sqlite3")
    agent = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=CallbackRouted(lambda *_: ""),
        timeout_seconds=300,
        idle_timeout_seconds=300,
    )

    # The routed attempt lease is 300 + 300 + 300 seconds.  The parent job
    # must not expire earlier while the model is still running or retrying.
    assert agent._job_lease_seconds == 900


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


def test_codex_agent_claims_exact_weekly_job_before_routed_execution(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly.sqlite3")
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-routed.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Routed:
        def execute(self, **kwargs):
            calls.append(kwargs)
            with store._connect() as db:
                parent = db.execute(
                    "select status from weekly_okr_analysis_jobs where "
                    "week_end=? and manager_user_id=? and source_digest=?",
                    tuple(kwargs["workload_key"].split(":", 3)[:3]),
                ).fetchone()
            assert parent["status"] == "running"
            value = kwargs["parser"](
                json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)
            )
            return SimpleNamespace(value=value, session_id="weekly-session")

    result = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=Routed(),
    ).analyze(
        source_path=source_path,
        managers=[manager],
        period_label="2026 Q3",
        week_start=datetime(2026, 8, 17).date(),
        week_end=datetime(2026, 8, 23).date(),
    )

    assert result.manager_reviews[0].name == manager.name
    assert calls[0]["workload_kind"] == "weekly_okr"
    assert calls[0]["workload_key"].startswith("2026-08-23:u1:")
    assert calls[0]["conversation_id"] is None
    assert calls[0]["required_capabilities"] >= {
        "structured_output",
        "local_schema_validation",
    }
    with store._connect() as db:
        row = db.execute("select status from weekly_okr_analysis_jobs").fetchone()
    assert row["status"] == "completed"


def test_codex_agent_reclaim_uses_new_runtime_generation_after_validation_failure(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "weekly-retry-generation.sqlite3")
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-retry-generation.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    keys = []

    class Routed:
        def execute(self, **kwargs):
            keys.append(kwargs["workload_key"])
            if len(keys) == 1:
                raise RuntimeError("runtime_result_validation_retry_consumed")
            value = kwargs["parser"](
                json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)
            )
            return SimpleNamespace(value=value, session_id="weekly-session")

    agent = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=Routed(),
    )
    arguments = {
        "source_path": source_path,
        "managers": [manager],
        "period_label": "2026 Q3",
        "week_start": datetime(2026, 8, 17).date(),
        "week_end": datetime(2026, 8, 23).date(),
    }

    with pytest.raises(RuntimeError, match="validation_retry_consumed"):
        agent.analyze(**arguments)

    result = agent.analyze(**arguments)

    assert result.manager_reviews[0].name == manager.name
    assert len(keys) == 2
    assert keys[0] != keys[1]


def test_codex_agent_reclaims_completed_job_when_cache_artifact_is_missing(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly-reclaim.sqlite3")
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-reclaim.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def execute(_command, _prompt, _env):
        return json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)

    def analyze(routed):
        return CodexWeeklyOkrAgent(
            workspace=tmp_path,
            store=store,
            routed_execution=routed,
        ).analyze(
            source_path=source_path,
            managers=[manager],
            period_label="2026 Q3",
            week_start=datetime(2026, 8, 17).date(),
            week_end=datetime(2026, 8, 23).date(),
        )

    analyze(CallbackRouted(execute))
    cache_path = next(tmp_path.glob("analysis.manager-*.json"))
    cache_path.unlink()
    recovered_route = CallbackRouted(execute)

    recovered = analyze(recovered_route)

    assert recovered.manager_reviews[0].name == manager.name
    assert len(recovered_route.calls) == 1
    with store._connect() as db:
        jobs = db.execute("select id, status from weekly_okr_analysis_jobs").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"


def test_codex_agent_in_progress_non_owner_never_spawns_or_terminalizes(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly-concurrent.sqlite3")
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-concurrent.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    entered = threading.Event()
    release = threading.Event()
    first_results = []
    first_errors = []

    class OwnerRouted:
        calls = 0

        def execute(self, **kwargs):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            raw = json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)
            return SimpleNamespace(value=kwargs["parser"](raw), session_id="owner")

    class NonOwnerRouted:
        calls = 0

        def execute(self, **_kwargs):
            self.calls += 1
            raise AssertionError("non-owner must not spawn a routed model child")

    owner_routed = OwnerRouted()
    non_owner_routed = NonOwnerRouted()

    def analyze(routed):
        return CodexWeeklyOkrAgent(
            workspace=tmp_path,
            store=store,
            routed_execution=routed,
        ).analyze(
            source_path=source_path,
            managers=[manager],
            period_label="2026 Q3",
            week_start=datetime(2026, 8, 17).date(),
            week_end=datetime(2026, 8, 23).date(),
        )

    def run_owner():
        try:
            first_results.append(analyze(owner_routed))
        except Exception as exc:  # noqa: BLE001 - surfaced after thread join
            first_errors.append(exc)

    thread = threading.Thread(target=run_owner)
    thread.start()
    assert entered.wait(timeout=5)

    with pytest.raises(
        weekly_okr_report_module.WeeklyOkrAnalysisInProgress
    ) as exc_info:
        analyze(non_owner_routed)
    assert exc_info.value.job_id > 0
    assert non_owner_routed.calls == 0
    with store._connect() as db:
        assert db.execute(
            "select status from weekly_okr_analysis_jobs"
        ).fetchone()["status"] == "running"

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_errors == []
    assert len(first_results) == 1
    assert owner_routed.calls == 1
    with store._connect() as db:
        assert (
            db.execute("select status from weekly_okr_analysis_jobs").fetchone()[
                "status"
            ]
            == "completed"
        )


def test_codex_agent_recovers_expired_owner_from_durable_cache_without_child(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "weekly-crash-cache.sqlite3")
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-crash-cache.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id, period_label="2026 Q3"
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    routed = CallbackRouted(
        lambda *_args: json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)
    )
    first = CodexWeeklyOkrAgent(
        workspace=tmp_path, store=store, routed_execution=routed
    )
    arguments = {
        "source_path": source_path,
        "managers": [manager],
        "period_label": "2026 Q3",
        "week_start": datetime(2026, 8, 17).date(),
        "week_end": datetime(2026, 8, 23).date(),
    }
    first.analyze(**arguments)
    with store._connect() as db:
        db.execute(
            "update weekly_okr_analysis_jobs set status='running', "
            "lease_owner='crashed-owner', lease_expires_at='2026-08-20 00:00:00', "
            "finished_at=''"
        )

    class NoChild:
        calls = 0

        def execute(self, **_kwargs):
            self.calls += 1
            raise AssertionError("durable cache recovery must not spawn a child")

    no_child = NoChild()
    recovered = CodexWeeklyOkrAgent(
        workspace=tmp_path, store=store, routed_execution=no_child
    ).analyze(**arguments)

    assert recovered.manager_reviews[0].name == manager.name
    assert no_child.calls == 0
    with store._connect() as db:
        row = db.execute(
            "select status, lease_owner, lease_expires_at from weekly_okr_analysis_jobs"
        ).fetchone()
    assert tuple(row) == ("completed", "", "")


def test_codex_agent_retries_incomplete_kr_coverage_once(tmp_path):
    roster = managers()[:1]
    source = FakeSource()
    source_path = tmp_path / "live.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": roster[0].name, "userId": roster[0].user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=roster[0].user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompts = []

    def executor(_command, prompt, _env):
        prompts.append(prompt)
        payload = _weekly_payload_for("甲")
        if len(prompts) == 1:
            payload["manager_reviews"][0]["kr_reviews"] = []
        return json.dumps(payload, ensure_ascii=False)

    store = AutoReplyStore(tmp_path / "weekly-invalid.sqlite3")
    analysis = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=store,
        routed_execution=CallbackRouted(executor),
    ).analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )

    assert [review.name for review in analysis.manager_reviews] == ["甲"]
    assert len(prompts) == 2
    assert "必须返回恰好 1 条 kr_reviews" in prompts[0]
    assert "上一轮输出未通过结构化校验" in prompts[1]


def test_weekly_command_honors_configured_codex_deadlines(tmp_path, monkeypatch):
    captured = {}

    def fake_run_weekly_okr_report(**kwargs):
        captured["agent"] = kwargs["agent"]
        return WeeklyOkrReportResult(status="dry_run", report_date="2026-08-17")

    monkeypatch.setattr(
        weekly_okr_report_module,
        "run_weekly_okr_report",
        fake_run_weekly_okr_report,
    )
    monkeypatch.setenv("CEO_OKR_LIVE_SOURCE_COMMAND", "echo")
    settings = SimpleNamespace(
        db_path=tmp_path / "auto-reply.sqlite3",
        ding_robot_code="",
        ding_robot_name="",
        ding_receiver_user_id="",
        dws_transient_retry_attempts=1,
        dws_transient_retry_delay_seconds=0.1,
        workspace=tmp_path,
        codex_timeout_seconds=37,
        codex_idle_timeout_seconds=19,
        dry_run=True,
    )

    weekly_okr_report_module.weekly_okr_report_command(settings, force=True)

    assert captured["agent"].timeout_seconds == 37
    assert captured["agent"].idle_timeout_seconds == 19


@pytest.mark.parametrize(
    ("dry_run", "expected_deliver"),
    ((True, False), (False, True)),
)
def test_weekly_command_inherits_scheduled_parent_execution_mode(
    tmp_path, monkeypatch, dry_run, expected_deliver
):
    from app.agent_cron.consumer import scheduled_execution_environment
    from app.cli import build_parser, settings_from_args

    monkeypatch.delenv("CEO_DRY_RUN", raising=False)
    monkeypatch.delenv("CEO_NOT_SEND_MESSAGE", raising=False)
    for key, value in scheduled_execution_environment(dry_run).items():
        monkeypatch.setenv(key, value)
    args = build_parser().parse_args(
        [
            "weekly-okr-report",
            "--force",
            "--db",
            str(tmp_path / "worker.sqlite3"),
            "--workspace",
            str(tmp_path),
        ]
    )
    settings = settings_from_args(args)
    captured = {}
    gateway = FakeGateway(managers())

    def fake_run_weekly_okr_report(**kwargs):
        captured.update(kwargs)
        return run_weekly_okr_report(
            store=FakeStore(),
            gateway=gateway,
            source=FakeSource(),
            agent=FakeAgent(),
            workspace=tmp_path,
            now=datetime(2026, 9, 6, 18, tzinfo=SHANGHAI),
            force=True,
            deliver=kwargs["deliver"],
            period_label="2026 Q3",
        )

    monkeypatch.setattr(
        weekly_okr_report_module,
        "run_weekly_okr_report",
        fake_run_weekly_okr_report,
    )
    monkeypatch.setenv("CEO_OKR_LIVE_SOURCE_COMMAND", "echo")

    weekly_okr_report_module.weekly_okr_report_command(
        settings,
        force=True,
        now=datetime(2026, 9, 6, 18, tzinfo=SHANGHAI),
    )

    assert settings.dry_run is dry_run
    assert captured["deliver"] is expected_deliver
    if dry_run:
        assert gateway.published == []
        assert gateway.sent == []
    else:
        assert gateway.published
        assert gateway.sent


def test_weekly_command_uses_emergency_cap_and_idle_watchdog(tmp_path, monkeypatch):
    captured = {}

    def fake_run_weekly_okr_report(**kwargs):
        captured["agent"] = kwargs["agent"]
        return WeeklyOkrReportResult(status="dry_run", report_date="2026-08-17")

    monkeypatch.setattr(
        weekly_okr_report_module,
        "run_weekly_okr_report",
        fake_run_weekly_okr_report,
    )
    monkeypatch.setenv("CEO_OKR_LIVE_SOURCE_COMMAND", "echo")
    settings = SimpleNamespace(
        db_path=tmp_path / "auto-reply.sqlite3",
        ding_robot_code="",
        ding_robot_name="",
        ding_receiver_user_id="",
        dws_transient_retry_attempts=1,
        dws_transient_retry_delay_seconds=0.1,
        workspace=tmp_path,
        codex_timeout_seconds=7200,
        codex_idle_timeout_seconds=300,
        dry_run=True,
    )

    weekly_okr_report_module.weekly_okr_report_command(settings, force=True)

    assert captured["agent"].timeout_seconds == 7200
    assert captured["agent"].idle_timeout_seconds == 300


def test_weekly_command_allows_only_one_report_run(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run_weekly_okr_report(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return WeeklyOkrReportResult(status="dry_run", report_date="2026-09-06")

    monkeypatch.setattr(
        weekly_okr_report_module,
        "run_weekly_okr_report",
        fake_run_weekly_okr_report,
    )
    monkeypatch.setenv("CEO_OKR_LIVE_SOURCE_COMMAND", "echo")
    monkeypatch.setenv("CEO_WEEKLY_OKR_RUN_LEASE_SECONDS", "60")
    settings = SimpleNamespace(
        db_path=tmp_path / "auto-reply.sqlite3",
        ding_robot_code="",
        ding_robot_name="",
        ding_receiver_user_id="",
        dws_transient_retry_attempts=1,
        dws_transient_retry_delay_seconds=0.1,
        workspace=tmp_path,
        codex_timeout_seconds=37,
        codex_idle_timeout_seconds=19,
        dry_run=True,
    )
    first_result = []

    thread = threading.Thread(
        target=lambda: first_result.append(
            weekly_okr_report_module.weekly_okr_report_command(
                settings,
                force=True,
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    second = weekly_okr_report_module.weekly_okr_report_command(
        settings,
        force=True,
    )

    assert second.status == "analysis_in_progress"
    assert len(calls) == 1
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_result[0].status == "dry_run"


def test_codex_agent_delegates_route_model_selection_to_runtime_adapter(tmp_path):
    manager = managers()[0]
    source = FakeSource()
    source_path = tmp_path / "live-auth.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": manager.name, "userId": manager.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=manager.user_id,
                            period_label="2026 Q3",
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    def executor(_command, _prompt, _env):
        return json.dumps(_weekly_payload_for(manager.name), ensure_ascii=False)
    routed = CallbackRouted(executor)
    agent = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=AutoReplyStore(tmp_path / "weekly-route.sqlite3"),
        routed_execution=routed,
    )

    agent.analyze(
        source_path=source_path,
        managers=[manager],
        period_label="2026 Q3",
        week_start=datetime(2026, 8, 17).date(),
        week_end=datetime(2026, 8, 23).date(),
    )

    assert routed.calls[0]["conversation_id"] is None
    assert isinstance(routed.calls[0]["command_factory"], CodexCommandFactory)
    instructions = routed.calls[0]["command_factory"].developer_instructions
    assert "CEO_PYTHON" in instructions
    assert "never invoke a repository-local .venv/bin/python path" in instructions


def _weekly_payload_for(name):
    return {
        "executive_summary": f"{name}摘要",
        "company_progress": [f"{name}进展"],
        "ceo_attention_items": [],
        "manager_reviews": [
            {
                "name": name,
                "role_level": "总监" if name == "甲" else "经理",
                "role_level_evidence": "钉钉通讯录当前职务",
                "progress_summary": "推进中",
                "key_progress": [],
                "independent_evidence": [],
                "evidence_assessment": "已综合判断",
                "risks": [],
                "next_week_focus": [],
                "data_gaps": [],
                "kr_reviews": [
                    {
                        "kr_id": "wrong-model-id",
                        "objective_title": "O1（模型改写）",
                        "kr_title": "KR1（模型改写）",
                        "category": "业务OKR",
                        "system_progress": "系统 50%",
                        "independent_evidence": "已读取交付文档",
                        "evidence_assessment": "结果部分落地",
                        "base_score": 80,
                        "time_discount": "未适用",
                        "score": 80,
                        "improvement": "补充验收记录",
                    }
                ],
                "leadership_dimensions": [item.model_dump() for item in _dimensions(4, 70)],
                "culture_dimensions": [item.model_dump() for item in _dimensions(3, 80)],
            }
        ],
        "source_coverage": ["实时叮当 OKR"],
        "warnings": [],
    }


def json_string(value):
    import json

    return json.dumps(json.dumps(value, ensure_ascii=False), ensure_ascii=False)


def _write_live_source(tmp_path: Path, roster) -> Path:
    source = FakeSource()
    source_path = tmp_path / "live.json"
    source_path.write_text(
        json.dumps(
            {
                "managers": [
                    {
                        "manager": {"name": m.name, "userId": m.user_id},
                        "liveOkr": source.fetch_user_okr(
                            user_id=m.user_id, period_label="2026 Q3"
                        ),
                    }
                    for m in roster
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return source_path


def _manager_name_from_prompt(prompt: str) -> str:
    source_line = next(
        line for line in prompt.splitlines()
        if line.startswith("- 实时叮当 OKR 聚合文件：")
    )
    filtered = json.loads(
        Path(source_line.split("：", 1)[1]).read_text(encoding="utf-8")
    )
    return filtered["managers"][0]["manager"]["name"]



def test_the_output_schema_pins_the_exact_kr_row_count(tmp_path: Path) -> None:
    """A model cannot satisfy the contract with one summary row.

    The shared schema says `minItems: 1`, and on 2026-09-12 three members came
    back with exactly one row each, every retry repeated it, and the report was
    abandoned.  The count the service already knows now binds the structured
    output itself.
    """
    roster = (
        ManagerIdentity("甲", "总监", "u1", "o1"),
        ManagerIdentity("乙", "经理", "u2", "o2"),
    )
    source_path = _write_live_source(tmp_path, roster)
    schemas: list[dict] = []

    def executor(_command, prompt, _env):
        name = _manager_name_from_prompt(prompt)
        return json.dumps(_weekly_payload_for(name), ensure_ascii=False)

    class SchemaCapturingRouted(CallbackRouted):
        def execute(self, **kwargs):
            factory = kwargs["command_factory"]
            schema_path = Path(factory.output_schema_path)
            schemas.append(json.loads(schema_path.read_text(encoding="utf-8")))
            return super().execute(**kwargs)

    CodexWeeklyOkrAgent(
        workspace=tmp_path,
        store=AutoReplyStore(tmp_path / "weekly-schema.sqlite3"),
        routed_execution=SchemaCapturingRouted(executor),
    ).analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )

    assert len(schemas) == 2
    for schema in schemas:
        rows = schema["properties"]["manager_reviews"]["items"]["properties"][
            "kr_reviews"
        ]
        assert rows["minItems"] == rows["maxItems"] == 1
        manager_rows = schema["properties"]["manager_reviews"]
        assert manager_rows["minItems"] == manager_rows["maxItems"] == 1
        culture_rows = schema["properties"]["manager_reviews"]["items"][
            "properties"
        ]["culture_dimensions"]
        assert culture_rows["minItems"] == culture_rows["maxItems"] == 3


def test_a_member_the_model_cannot_score_has_a_typed_gap(
    tmp_path: Path,
) -> None:
    """Terminal result failure preserves the member without fabricated scores."""
    roster = (
        ManagerIdentity("甲", "总监", "u1", "o1"),
        ManagerIdentity("乙", "经理", "u2", "o2"),
    )
    source_path = _write_live_source(tmp_path, roster)

    def executor(_command, prompt, _env):
        name = _manager_name_from_prompt(prompt)
        if name == "乙":
            from app.agent_runtime_router import RoutedResultValidationError
            raise RoutedResultValidationError("invalid member result")
        return json.dumps(_weekly_payload_for(name), ensure_ascii=False)

    analysis = CodexWeeklyOkrAgent(
            workspace=tmp_path,
            store=AutoReplyStore(tmp_path / "weekly-partial.sqlite3"),
            routed_execution=CallbackRouted(executor),
        ).analyze(
            source_path=source_path,
            managers=roster,
            period_label="2026 Q3",
            week_start=datetime(2026, 7, 27).date(),
            week_end=datetime(2026, 7, 30).date(),
        )
    assert [review.name for review in analysis.manager_reviews] == ["甲"]
    assert [(gap.user_id, gap.stage, gap.kind) for gap in analysis.member_gaps] == [
        ("u2", "analysis", "analysis_failed")
    ]
    text = weekly_okr_report_module.render_weekly_okr_report(
        title="test", period_label="2026 Q3", analysis=analysis,
        managers=list(roster), manager_payloads=json.loads(source_path.read_text())["managers"],
    )
    assert "### 乙｜经理" in text
    assert "共 2 人" in text
    assert "analysis_failed" in text
    assert "u2" in text


def test_all_terminal_member_failures_have_unscored_sections(
    tmp_path: Path,
) -> None:
    roster = (ManagerIdentity("甲", "总监", "u1", "o1"),)
    source_path = _write_live_source(tmp_path, roster)

    def executor(_command, _prompt, _env):
        from app.agent_runtime_router import RoutedResultValidationError
        raise RoutedResultValidationError("invalid member result")

    analysis = CodexWeeklyOkrAgent(
            workspace=tmp_path,
            store=AutoReplyStore(tmp_path / "weekly-none.sqlite3"),
            routed_execution=CallbackRouted(executor),
        ).analyze(
            source_path=source_path,
            managers=roster,
            period_label="2026 Q3",
            week_start=datetime(2026, 7, 27).date(),
            week_end=datetime(2026, 7, 30).date(),
        )
    assert analysis.manager_reviews == []
    assert len(analysis.member_gaps) == 1


def test_model_output_cannot_declare_member_gaps():
    payload = _weekly_payload_for("甲")
    payload["member_gaps"] = [{"user_id": "u2", "kind": "goals_missing"}]
    with pytest.raises(ValueError):
        WeeklyOkrAnalysis.model_validate(payload)


@pytest.mark.parametrize("user_ids", [("u1", "u1"), ("", "u2")])
def test_collection_rejects_duplicate_or_missing_roster_ids(tmp_path, user_ids):
    roster = [ManagerIdentity(name, "经理", user_id, name) for name, user_id in zip(["甲", "乙"], user_ids)]
    source = FakeSource()
    with pytest.raises(ValueError, match="user IDs"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=FakeGateway(roster), source=source, agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert source.calls == []


@pytest.mark.parametrize("failure_class", ["authentication", "capability", "unclassified"])
def test_shared_analysis_failure_is_not_a_member_gap(tmp_path, failure_class):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError
    roster = managers()
    source_path = _write_live_source(tmp_path, roster)

    def executor(_command, _prompt, _env):
        raise RoutedCodexExecutionError("shared failure", failure_class=RuntimeFailureClass(failure_class))

    with pytest.raises(RoutedCodexExecutionError, match="shared failure"):
        CodexWeeklyOkrAgent(
            workspace=tmp_path, store=AutoReplyStore(tmp_path / "shared.sqlite3"),
            routed_execution=CallbackRouted(executor),
        ).analyze(
            source_path=source_path, managers=roster, period_label="2026 Q3",
            week_start=date(2026, 7, 1), week_end=date(2026, 7, 30),
        )


def test_terminal_analysis_gap_publishes_full_roster_without_scores(tmp_path):
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError
    gateway = FakeGateway(managers())

    def executor(_command, prompt, _env):
        name = _manager_name_from_prompt(prompt)
        if name == "甲":
            raise RoutedCodexExecutionError("terminal result", failure_class=RuntimeFailureClass.RESULT)
        return json.dumps(_weekly_payload_for(name), ensure_ascii=False)

    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=FakeSource(),
        agent=CodexWeeklyOkrAgent(
            workspace=tmp_path, store=AutoReplyStore(tmp_path / "isolated.sqlite3"),
            routed_execution=CallbackRouted(executor),
        ),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"
    assert len(gateway.published) == 3
    appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
    assert "analysis_failed" in appendix
    assert "#### 逐 KR 评分" not in appendix
    report = gateway.published[-1][1]
    assert "共 2 人" in report
    assert "已形成最终分：1 人；暂不形成：1 人" in report


def test_malformed_source_is_not_goals_missing(tmp_path):
    class MalformedSource(FakeSource):
        def fetch_user_okr(self, **kwargs):
            payload = super().fetch_user_okr(**kwargs)
            payload["processed"].pop("okrRows")
            return payload

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=MalformedSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert result.status == "sent"
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "source_failed"
    assert len(raw["managers"]) == 2
    assert raw["managers"][0]["sourceOutcome"]["scope"] == "member"
    assert raw["managers"][0]["sourceOutcome"]["validationPhase"] == "member_data"
    assert len(gateway.published) == 3
    assert "已形成最终分：0 人；暂不形成：2 人" in gateway.published[-1][1]


def test_all_empty_sources_publish_unscored_sections_without_model(tmp_path):
    class EmptySource:
        def fetch_user_okr(self, *, user_id, period_label):
            return _empty_live_payload(user_id, period_label)

    class NoAnalysis:
        def analyze(self, **_kwargs):
            raise AssertionError("Empty authoritative sources need no invented analysis")

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=EmptySource(), agent=NoAnalysis(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"
    assert len(gateway.published) == 3
    assert "已形成最终分：0 人；暂不形成：2 人" in gateway.published[-1][1]


@pytest.mark.parametrize("user_ids", [("u1", "u1"), ("", "u2")])
def test_analysis_rejects_duplicate_or_missing_source_ids(tmp_path, user_ids):
    source_path = _write_live_source(tmp_path, managers())
    raw = json.loads(source_path.read_text())
    for item, user_id in zip(raw["managers"], user_ids):
        item["manager"]["userId"] = user_id
    source_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="user IDs"):
        CodexWeeklyOkrAgent(
            workspace=tmp_path, store=AutoReplyStore(tmp_path / "invalid-source.sqlite3"),
            routed_execution=CallbackRouted(lambda *_args: pytest.fail("must not analyze")),
        ).analyze(
            source_path=source_path, managers=managers(), period_label="2026 Q3",
            week_start=date(2026, 7, 1), week_end=date(2026, 7, 30),
        )


@pytest.mark.parametrize("message", ["opaque auth failure", "Dingteam OKR period not found: 2026 Q3"])
def test_all_source_failures_do_not_publish_or_send(tmp_path, message):
    class OutageSource:
        def fetch_user_okr(self, **_kwargs):
            raise RuntimeError(message)

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=OutageSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert [item["sourceOutcome"]["kind"] for item in raw["managers"]] == ["source_failed", "source_failed"]
    assert gateway.published == gateway.sent == []


@pytest.mark.parametrize("broken_field", ["userId", "periodLabel", "source", "period", "periods", "objectiveList"])
def test_empty_source_without_authority_receipt_is_source_failed(tmp_path, broken_field):
    class IncompleteSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                payload = _empty_live_payload(user_id, period_label)
                payload.pop(broken_field)
                return payload
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=IncompleteSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "source_failed"
    assert raw["managers"][0]["sourceOutcome"]["scope"] == "shared"
    assert raw["managers"][0]["sourceOutcome"]["validationPhase"] == "receipt"
    assert gateway.published == gateway.sent == []


def test_unexpected_collection_typeerror_propagates(tmp_path):
    class BuggySource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                raise TypeError("source programming bug")
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    with pytest.raises(TypeError, match="source programming bug"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=BuggySource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert gateway.published == gateway.sent == []


@pytest.mark.parametrize("corruption", ["wrong_user", "wrong_period", "wrong_selected_period", "raw_objectives", "no_capture"])
def test_inconsistent_empty_receipt_cannot_establish_goals_missing(tmp_path, corruption):
    class InconsistentSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id != "u1":
                return super().fetch_user_okr(user_id=user_id, period_label=period_label)
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

    gateway = FakeGateway(managers())
    def run():
        return run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=InconsistentSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )

    if corruption == "raw_objectives":
        assert run().status == "sent"
        appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
        assert "source_failed" in appendix
        assert "goals_missing" not in appendix
    else:
        with pytest.raises(RuntimeError, match="source collection failed"):
            run()
        assert gateway.published == gateway.sent == []
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    expected_scope = "member" if corruption == "raw_objectives" else "shared"
    assert raw["managers"][0]["sourceOutcome"]["scope"] == expected_scope


def test_all_unverified_empty_payloads_are_overall_source_failure(tmp_path):
    class UnverifiedSource:
        def fetch_user_okr(self, **_kwargs):
            return {"processed": {"objectives": [], "okrRows": []}}

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=UnverifiedSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert [item["sourceOutcome"]["kind"] for item in raw["managers"]] == ["source_failed", "source_failed"]
    assert gateway.published == gateway.sent == []


def _missing_period_envelope(user_id, period_label):
    return {
        "source": {"system": "Dingteam personal-period API", "capturedAt": "2026-10-07T04:00:00Z"},
        "userId": user_id,
        "periodLabel": period_label,
        "availability": {"status": "goals_not_established", "providerCode": 0, "periodsComplete": True},
        "periods": [{"name": "2026 Q3", "okrId": "q3"}],
    }


@pytest.mark.parametrize("all_absent", [False, True])
def test_verified_missing_q4_periods_publish_full_roster(tmp_path, all_absent):
    class PeriodSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1" or all_absent:
                return _missing_period_envelope(user_id, period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    class Q4Agent(FakeAgent):
        period_label = "2026 Q4"

        def analyze(self, **kwargs):
            assert kwargs["period_label"] == "2026 Q4"
            assert [manager.user_id for manager in kwargs["managers"]] == ["u2"]
            assert kwargs["week_start"] == date(2026, 10, 1)
            return super().analyze(**kwargs)

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=PeriodSource(), agent=Q4Agent(),
        workspace=tmp_path, now=datetime(2026, 10, 7, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q4",
    )
    assert result.status == "sent"
    assert result.period_label == "2026 Q4"
    assert len(gateway.published) == 3
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "goals_missing"
    assert "liveOkr" not in raw["managers"][0]
    assert raw["managers"][0]["availabilityReceipt"] == _missing_period_envelope("u1", "2026 Q4")
    appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
    assert "goals_missing" in appendix
    assert "2026 Q4" in appendix
    assert "个人周期尚未建立" in appendix
    assert "#### 逐 KR 评分" not in appendix
    assert "2026-10-01" in gateway.published[-1][0]
    expected_scored = 0 if all_absent else 1
    assert f"已形成最终分：{expected_scored} 人；暂不形成：{2 - expected_scored} 人" in gateway.published[-1][1]


@pytest.mark.parametrize("corruption", [
    "user", "requested_period", "provider_code", "boolean_code", "incomplete",
    "source", "timestamp", "status", "has_more", "current_period_alias",
    "missing_period_id", "zero_period_id", "not_a_period_list", "fabricated_processed",
])
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

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=BrokenReceiptSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 10, 7, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q4",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert all(item["sourceOutcome"]["kind"] == "source_failed" for item in raw["managers"])
    assert gateway.published == gateway.sent == []


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

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=InvalidKrSource(), agent=LaterOnlyAgent(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "source_failed"
    appendix = next(content for name, content, _ in gateway.published if name.endswith("评分附录｜甲"))
    assert "source_failed" in appendix
    assert "#### 逐 KR 评分" not in appendix


def test_valid_chinese_period_alias_is_not_a_collection_failure(tmp_path):
    class AliasSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            payload = super().fetch_user_okr(user_id=user_id, period_label=period_label)
            payload["period"]["name"] = "2026年第三季度"
            payload["periods"] = [payload["period"]]
            return payload

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=AliasSource(), agent=FakeAgent(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"


def test_unrelated_source_cannot_establish_empty_goals(tmp_path):
    class UnrelatedSource:
        def fetch_user_okr(self, *, user_id, period_label):
            payload = _empty_live_payload(user_id, period_label)
            payload["source"]["system"] = "unrelated provider"
            return payload

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=UnrelatedSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert gateway.published == gateway.sent == []


def test_same_named_members_keep_distinct_scored_appendices(tmp_path):
    roster = [ManagerIdentity("同名", "经理", "u1", "o1"), ManagerIdentity("同名", "经理", "u2", "o2")]
    gateway = FakeGateway(roster)
    class DistinctProgressSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            payload = super().fetch_user_okr(user_id=user_id, period_label=period_label)
            payload["processed"]["okrRows"][0]["krProgress"] = 20 if user_id == "u1" else 75
            return payload

    def score_each_identity(_command, prompt, _env):
        source_line = next(line for line in prompt.splitlines() if line.startswith("- 实时叮当 OKR 聚合文件："))
        filtered = json.loads(Path(source_line.split("：", 1)[1]).read_text(encoding="utf-8"))
        uid = filtered["managers"][0]["manager"]["userId"]
        payload = _weekly_payload_for("同名")
        score = 40 if uid == "u1" else 90
        payload["manager_reviews"][0]["kr_reviews"][0]["score"] = score
        payload["manager_reviews"][0]["kr_reviews"][0]["base_score"] = score
        return json.dumps(payload, ensure_ascii=False)

    routed = CallbackRouted(score_each_identity)
    agent = CodexWeeklyOkrAgent(
        workspace=tmp_path, store=AutoReplyStore(tmp_path / "same-name.sqlite3"),
        routed_execution=routed,
    )
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=DistinctProgressSource(), agent=agent,
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"
    assert len(gateway.published) == 3
    appendix_names = [name for name, _, _ in gateway.published if "评分附录" in name]
    assert len(set(appendix_names)) == 2
    for uid in ["u1", "u2"]:
        name, content, _ = next(item for item in gateway.published if "评分附录" in item[0] and uid in item[0])
        assert "同名" in name
        assert f"成员 ID：{uid}" in content
        assert "#### 逐 KR 评分" in content
        expected_score = "40.0" if uid == "u1" else "90.0"
        assert f"业务 OKR {expected_score}" in content
        progress = "20%" if uid == "u1" else "75%"
        assert f"当前 KR 平均进度 {progress}" in content
    assert "已形成最终分：2 人" in gateway.published[-1][1]
    raw_path = next(tmp_path.rglob("live_okr.json"))
    recovered = agent.analyze(
        source_path=raw_path, managers=roster, period_label="2026 Q3",
        week_start=date(2026, 7, 1), week_end=date(2026, 7, 30),
    )
    assert [review.user_id for review in recovered.manager_reviews] == ["u1", "u2"]
    assert len(routed.calls) == 2


@pytest.mark.parametrize("scope", ["shared", "unknown_runtime", "unknown_value", "unknown_os"])
def test_shared_or_unknown_source_failure_after_success_blocks_publication(tmp_path, scope):
    from app.okr_review import OkrLiveSourceError

    class SharedFailureSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u2":
                if scope == "shared":
                    raise OkrLiveSourceError(scope="shared", code="prerequisite_unavailable", detail="opaque prerequisite failure", user_id=user_id, period_label=period_label)
                error_type = {"unknown_runtime": RuntimeError, "unknown_value": ValueError, "unknown_os": OSError}[scope]
                raise error_type("unclassified source failure")
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    store = FakeStore()
    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=store, gateway=gateway, source=SharedFailureSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "collected"
    assert raw["managers"][1]["sourceOutcome"]["kind"] == "source_failed"
    assert raw["managers"][1]["sourceOutcome"]["scope"] == "shared"
    assert gateway.published == gateway.sent == []
    assert "weekly_okr_report:last_success_date" not in store.state


def test_same_named_scored_and_missing_members_have_distinct_sections(tmp_path):
    roster = [ManagerIdentity("同名", "经理", "u1", "o1"), ManagerIdentity("同名", "经理", "u2", "o2")]

    class MixedSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                return _missing_period_envelope(user_id, period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(roster)
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=MixedSource(),
        agent=CodexWeeklyOkrAgent(
            workspace=tmp_path, store=AutoReplyStore(tmp_path / "mixed-name.sqlite3"),
            routed_execution=CallbackRouted(lambda *_args: json.dumps(_weekly_payload_for("同名"), ensure_ascii=False)),
        ),
        workspace=tmp_path, now=datetime(2026, 10, 7, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q4",
    )
    assert result.status == "sent"
    first = next(content for name, content, _ in gateway.published if "评分附录" in name and "u1" in name)
    second = next(content for name, content, _ in gateway.published if "评分附录" in name and "u2" in name)
    assert "goals_missing" in first and "#### 逐 KR 评分" not in first
    assert "goals_missing" not in second and "#### 逐 KR 评分" in second


def test_model_cannot_supply_review_owner_id():
    payload = _weekly_payload_for("甲")
    payload["manager_reviews"][0]["user_id"] = "u2"
    with pytest.raises(ValueError):
        WeeklyOkrAnalysis.model_validate(payload)


def test_all_identity_bound_member_403_failures_publish_unscored_roster(tmp_path):
    from app.okr_review import OkrLiveSourceError

    class MemberForbiddenSource:
        def fetch_user_okr(self, *, user_id, period_label):
            raise OkrLiveSourceError(scope="member", code="403", detail="member access denied", user_id=user_id, period_label=period_label)

    class NoAnalysis:
        def analyze(self, **_kwargs):
            raise AssertionError("member source gaps cannot be scored")

    gateway = FakeGateway(managers())
    result = run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=MemberForbiddenSource(), agent=NoAnalysis(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    assert result.status == "sent"
    assert len(gateway.published) == 3
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert all(item["sourceOutcome"]["kind"] == "source_failed" and item["sourceOutcome"]["scope"] == "member" for item in raw["managers"])
    for name, content, _ in gateway.published[:-1]:
        assert "评分附录" in name
        assert "source_failed" in content
        assert "403" in content
        assert "member access denied" not in content
        assert "goals_missing" not in content
        assert "#### 逐 KR 评分" not in content
    assert "已形成最终分：0 人；暂不形成：2 人" in gateway.published[-1][1]


def test_gap_score_table_cells_match_header_and_status_column(tmp_path):
    class EmptyFirstSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                return _empty_live_payload(user_id, period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=EmptyFirstSource(), agent=FakeAgent(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    section = gateway.published[-1][1].split("### 分项评分表", 1)[1].split("## ", 1)[0]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in section.splitlines() if line.startswith("|")]
    header, _separator, *data = rows
    assert len(header) == 8
    assert all(len(row) == len(header) for row in data)
    gap_row = next(row for row in data if row[0] == "甲")
    assert gap_row[header.index("状态")] == "goals_missing"


def test_all_shared_auth_401_source_failures_still_block_publication(tmp_path):
    from app.okr_review import OkrLiveSourceError

    class SharedAuthSource:
        def fetch_user_okr(self, *, user_id, period_label):
            raise OkrLiveSourceError(scope="shared", code="401", detail="shared auth unavailable", user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=SharedAuthSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    assert gateway.published == gateway.sent == []


@pytest.mark.parametrize("proof", [None, {"providerCode": 401, "complete": True, "count": 0}, {"providerCode": False, "complete": True, "count": 0}, {"providerCode": 0, "complete": False, "count": 0}, {"providerCode": 0, "complete": True, "count": 1}, {"providerCode": 0, "complete": True, "count": False}])
def test_empty_live_objectives_require_complete_provider_receipt(tmp_path, proof):
    class UnprovenSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id != "u1":
                return super().fetch_user_okr(user_id=user_id, period_label=period_label)
            payload = _empty_live_payload(user_id, period_label)
            if proof is not None:
                payload["source"]["objectiveListReceipt"] = proof
            else:
                payload["source"].pop("objectiveListReceipt", None)
            return payload

    gateway = FakeGateway(managers())
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=FakeStore(), gateway=gateway, source=UnprovenSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "source_failed"
    assert raw["managers"][0]["sourceOutcome"]["scope"] == "shared"
    assert raw["managers"][0]["sourceOutcome"]["validationPhase"] == "receipt"
    assert gateway.published == gateway.sent == []


def test_absence_receipt_metrics_are_private_not_public_report(tmp_path):
    receipt = _missing_period_envelope("u1", "2026 Q4")
    receipt["periods"][0].update({"grade": "PRIVATE_HISTORICAL_GRADE", "progress": "PRIVATE_HISTORICAL_PROGRESS", "okrId": "PRIVATE_PERIOD_ID"})

    class PrivateReceiptSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                return receipt
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    class Q4Agent(FakeAgent):
        period_label = "2026 Q4"

    gateway = FakeGateway(managers())
    run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=PrivateReceiptSource(), agent=Q4Agent(),
        workspace=tmp_path, now=datetime(2026, 10, 7, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q4",
    )
    public = "\n".join(content for _, content, _ in gateway.published) + "\n".join(gateway.sent)
    for marker in ["PRIVATE_HISTORICAL_GRADE", "PRIVATE_HISTORICAL_PROGRESS", "PRIVATE_PERIOD_ID", "periodsComplete", "providerCode", "2026 Q3"]:
        assert marker not in public
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["availabilityReceipt"] == receipt


def test_source_failure_diagnostic_paths_and_tokens_stay_private(tmp_path):
    from app.okr_review import OkrLiveSourceError
    private_detail = "/private/originals/PRIVATE_SOURCE.txt token=PRIVATE_AUTH_TOKEN\nPRIVATE_PROVIDER_BODY"

    class PrivateFailureSource(FakeSource):
        def fetch_user_okr(self, *, user_id, period_label):
            if user_id == "u1":
                raise OkrLiveSourceError(scope="member", code="403", detail=private_detail, user_id=user_id, period_label=period_label)
            return super().fetch_user_okr(user_id=user_id, period_label=period_label)

    gateway = FakeGateway(managers())
    run_weekly_okr_report(
        store=FakeStore(), gateway=gateway, source=PrivateFailureSource(), agent=FakeAgent(),
        workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
        force=True, deliver=True, period_label="2026 Q3",
    )
    public = "\n".join(content for _, content, _ in gateway.published) + "\n".join(gateway.sent)
    assert "403" in public
    for marker in ["PRIVATE_SOURCE", "PRIVATE_AUTH_TOKEN", "PRIVATE_PROVIDER_BODY"]:
        assert marker not in public
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert private_detail in raw["managers"][0]["sourceOutcome"]["diagnostic"]


def test_terminal_analysis_diagnostics_are_not_public_evidence(tmp_path):
    from app.agent_runtime_router import RoutedResultValidationError
    private_detail = "/private/analysis/PRIVATE_ARTIFACT token=PRIVATE_ANALYSIS_TOKEN"
    roster = managers()
    source_path = _write_live_source(tmp_path, roster)

    def executor(_command, prompt, _env):
        name = _manager_name_from_prompt(prompt)
        if name == "甲":
            raise RoutedResultValidationError(private_detail)
        return json.dumps(_weekly_payload_for(name), ensure_ascii=False)

    analysis = CodexWeeklyOkrAgent(
        workspace=tmp_path, store=AutoReplyStore(tmp_path / "private-analysis.sqlite3"),
        routed_execution=CallbackRouted(executor),
    ).analyze(
        source_path=source_path, managers=roster, period_label="2026 Q3",
        week_start=date(2026, 7, 1), week_end=date(2026, 7, 30),
    )
    assert analysis.member_gaps[0].diagnostic == private_detail
    public = weekly_okr_report_module.render_weekly_okr_report(
        title="report", period_label="2026 Q3", analysis=analysis,
        managers=roster, manager_payloads=json.loads(source_path.read_text())["managers"],
    )
    assert "analysis_failed" in public
    assert "PRIVATE_ARTIFACT" not in public
    assert "PRIVATE_ANALYSIS_TOKEN" not in public


@pytest.mark.parametrize("corruption", ["identity", "source_authority", "objective_proof", "absence_identity"])
def test_untrusted_contract_after_valid_source_blocks_independent_of_count(tmp_path, corruption):
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

    gateway = FakeGateway(managers())
    store = FakeStore()
    with pytest.raises(RuntimeError, match="source collection failed"):
        run_weekly_okr_report(
            store=store, gateway=gateway, source=InvalidContractSource(), agent=FakeAgent(),
            workspace=tmp_path, now=datetime(2026, 7, 30, 12, tzinfo=SHANGHAI),
            force=True, deliver=True, period_label="2026 Q3",
        )
    raw = json.loads(next(tmp_path.rglob("live_okr.json")).read_text())
    assert raw["managers"][0]["sourceOutcome"]["kind"] == "collected"
    assert raw["managers"][1]["sourceOutcome"]["scope"] == "shared"
    assert gateway.published == gateway.sent == []
    assert "weekly_okr_report:last_success_date" not in store.state
