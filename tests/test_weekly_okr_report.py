from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.weekly_okr_report import (
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
    _manager_scorecards,
    _extract_report_payload,
    run_weekly_okr_report,
    weekly_okr_report_window_open,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeStore:
    def __init__(self):
        self.state = {}

    def get_service_state(self, key):
        return self.state.get(key, "")

    def set_service_state(self, key, value):
        self.state[key] = value


class FakeDws:
    dws_bin = "dws"

    def __init__(self, payload):
        self.payload = payload
        self.commands = []

    def run_json(self, command, **_kwargs):
        self.commands.append(command)
        return self.payload


class CreateDecodeRecoveryDws:
    dws_bin = "dws"

    def __init__(self, marker):
        self.marker = marker
        self.list_calls = 0

    def run_json(self, command, **_kwargs):
        if command[1:4] == ["wiki", "node", "list"]:
            self.list_calls += 1
            if self.list_calls == 1:
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


class FakeGateway:
    def __init__(self, managers):
        self.managers = managers
        self.published = []
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

    def publish_document(
        self,
        *,
        workspace_id,
        folder_id,
        name,
        content_file,
        verification_marker,
    ):
        assert workspace_id == "wiki-1"
        assert folder_id == "folder-1"
        content = content_file.read_text(encoding="utf-8")
        assert verification_marker in content
        self.published.append((name, content))
        return PublishedDocument("doc-1", "https://alidocs.example/doc-1")

    def send_group_summary(self, *, conversation_id, title, text):
        assert conversation_id == "cid-ceo-2"
        assert title in text
        assert "https://alidocs.example/doc-1" in text
        self.sent.append(text)
        return "sent"


class FakeSource:
    def __init__(self):
        self.calls = []

    def fetch_user_okr(self, *, user_id, period_label):
        self.calls.append((user_id, period_label))
        return {
            "source": {"system": "叮当OKR Dingteam Web"},
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
    def analyze(self, *, source_path, managers, period_label, week_start, week_end):
        payload = source_path.read_text(encoding="utf-8")
        assert "processed" in payload
        assert period_label == "2026 Q3"
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
        for name, content in gateway.published
        if "评分附录" not in name
    )
    assert "## 管理会审阅页" in published
    assert "### 管理者审阅表" in published
    assert "这是截至本周的证据完成度快照，不是季度末绩效预测" in published
    assert "## 逐人评分附录" in published
    assert "## 附录：逐人证据与逐 KR 评分" not in published
    assert "[甲｜总监](https://alidocs.example/doc-1)" in published
    appendix = next(
        content
        for name, content in gateway.published
        if name.endswith("评分附录｜甲")
    )
    assert "### 甲｜总监" in appendix
    assert "## 附录校验：甲" in appendix
    assert store.state["weekly_okr_report:last_success_date"] == "2026-07-30"


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


def test_weekly_window_rejects_invalid_hour():
    with pytest.raises(ValueError, match="between 0 and 23"):
        weekly_okr_report_window_open(
            datetime(2026, 8, 2, 18, 0, tzinfo=SHANGHAI),
            schedule_hour=24,
        )


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
            "manager": {"name": manager.name},
            "liveOkr": source.fetch_user_okr(user_id=manager.user_id, period_label="2026 Q3"),
        }
        for manager in roster
    ]

    cards = _manager_scorecards(analysis, payloads)

    assert cards["甲"].business_score == 80.0
    assert cards["甲"].leadership_score == 70.0
    assert cards["甲"].culture_score == 80.0
    assert cards["甲"].culture_coefficient == 1.05
    assert cards["甲"].final_score == 80.9


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

    analysis = CodexWeeklyOkrAgent(
        workspace=tmp_path,
        executor=executor,
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
        executor=executor,
    ).analyze(
        source_path=source_path,
        managers=roster,
        period_label="2026 Q3",
        week_start=datetime(2026, 7, 27).date(),
        week_end=datetime(2026, 7, 30).date(),
    )

    assert len(seen) == 2
    assert [review.name for review in cached.manager_reviews] == ["甲", "乙"]


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
