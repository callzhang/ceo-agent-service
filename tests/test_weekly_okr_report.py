from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.weekly_okr_report import (
    CeoAttentionItem,
    DwsWeeklyOkrGateway,
    GroupRoster,
    ManagerIdentity,
    ManagerReportAnalysis,
    PublishedDocument,
    WeeklyOkrAnalysis,
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
        if command[1:3] == ["doc", "read"]:
            return {"title": self.marker}
        raise AssertionError(command)


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
                        "objectiveTitle": "O1",
                        "objectiveWeight": 100,
                        "objectiveProgress": 50,
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
                    progress_summary="本周完成一个可核验里程碑。",
                    key_progress=["KR 有新增进展"],
                    independent_evidence=["相关文档已读取"],
                    evidence_assessment="结果与系统更新一致。",
                    risks=[],
                    next_week_focus=["关闭剩余事项"],
                    data_gaps=[],
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
    assert "甲｜总监" in gateway.published[0][1]
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
                "progress_summary": "推进中",
                "key_progress": [],
                "independent_evidence": [],
                "evidence_assessment": "证据不足",
                "risks": [],
                "next_week_focus": [],
                "data_gaps": ["缺少验收记录"],
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


def json_string(value):
    import json

    return json.dumps(json.dumps(value, ensure_ascii=False), ensure_ascii=False)
