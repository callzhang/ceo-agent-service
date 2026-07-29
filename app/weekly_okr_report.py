from __future__ import annotations

import json
import os
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.codex_decision import _subprocess_failure_reason
from app.codex_runner import CodexRunner
from app.dws_client import DwsClient, DwsError
from app.okr_review import DwsLiveOkrSource, current_quarter_period
from app.process_runner import run_process_with_idle_timeout


DEFAULT_GROUP_NAME = "CEO-2 管理群"
DEFAULT_WIKI_NAME = "🎯  目标与执行"
DEFAULT_FOLDER_NAME = "管理者 OKR 进度周报"
DEFAULT_SCHEDULE_HOUR = 18
DEFAULT_RETRY_SECONDS = 1800
WEEKLY_OKR_REPORT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "weekly_okr_report.schema.json"
)
OKR_REVIEW_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtang-okr-review" / "SKILL.md"
)
LAST_SUCCESS_STATE_KEY = "weekly_okr_report:last_success_date"
LAST_ATTEMPT_STATE_KEY = "weekly_okr_report:last_attempt_at"


class ManagerReportAnalysis(BaseModel):
    name: str
    progress_summary: str
    key_progress: list[str] = Field(default_factory=list)
    independent_evidence: list[str] = Field(default_factory=list)
    evidence_assessment: str
    risks: list[str] = Field(default_factory=list)
    next_week_focus: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class CeoAttentionItem(BaseModel):
    topic: str
    owner_names: list[str] = Field(default_factory=list)
    issue: str
    recommended_decision: str


class WeeklyOkrAnalysis(BaseModel):
    executive_summary: str
    company_progress: list[str] = Field(default_factory=list)
    ceo_attention_items: list[CeoAttentionItem] = Field(default_factory=list)
    manager_reviews: list[ManagerReportAnalysis]
    source_coverage: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ManagerIdentity:
    name: str
    title: str
    user_id: str
    open_dingtalk_id: str


@dataclass(frozen=True)
class GroupRoster:
    name: str
    conversation_id: str
    managers: list[ManagerIdentity]


@dataclass(frozen=True)
class PublishedDocument:
    node_id: str
    url: str


@dataclass(frozen=True)
class WeeklyOkrReportResult:
    status: str
    report_date: str
    period_label: str = ""
    manager_count: int = 0
    document_url: str = ""
    local_report_path: str = ""
    send_state: str = ""


class WeeklyOkrAgent(Protocol):
    def analyze(
        self,
        *,
        source_path: Path,
        managers: list[ManagerIdentity],
        period_label: str,
        week_start: date,
        week_end: date,
    ) -> WeeklyOkrAnalysis: ...


class WeeklyOkrGateway(Protocol):
    def resolve_group_roster(self, group_name: str) -> GroupRoster: ...

    def resolve_wiki(self, wiki_name: str) -> str: ...

    def ensure_folder(
        self,
        *,
        workspace_id: str,
        folder_name: str,
    ) -> str: ...

    def publish_document(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
        content_file: Path,
        verification_marker: str,
    ) -> PublishedDocument: ...

    def send_group_summary(
        self,
        *,
        conversation_id: str,
        title: str,
        text: str,
    ) -> str: ...


class CodexWeeklyOkrAgent:
    def __init__(
        self,
        *,
        workspace: Path,
        timeout_seconds: int = 1800,
        idle_timeout_seconds: int = 900,
        executor: Callable[[list[str], str, dict[str, str]], str] | None = None,
    ):
        self.runner = CodexRunner(workspace=workspace)
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.executor = executor

    def analyze(
        self,
        *,
        source_path: Path,
        managers: list[ManagerIdentity],
        period_label: str,
        week_start: date,
        week_end: date,
    ) -> WeeklyOkrAnalysis:
        prompt = build_weekly_okr_prompt(
            source_path=source_path,
            managers=managers,
            period_label=period_label,
            week_start=week_start,
            week_end=week_end,
        )
        command = self.runner.build_command(
            prompt,
            session_id=None,
            output_schema_path=WEEKLY_OKR_REPORT_SCHEMA_PATH,
        )
        env = self.runner.build_env()
        if self.executor is not None:
            raw = self.executor(command, prompt, env)
        else:
            completed = run_process_with_idle_timeout(
                command,
                prompt=prompt,
                env=env,
                total_timeout_seconds=self.timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
            )
            if completed.timed_out:
                raise RuntimeError(completed.timeout_reason or "weekly OKR agent timed out")
            if completed.returncode != 0:
                raise RuntimeError(
                    _subprocess_failure_reason(completed.stderr, completed.stdout)
                )
            raw = completed.stdout
        return WeeklyOkrAnalysis.model_validate(_extract_report_payload(raw))


class DwsWeeklyOkrGateway:
    def __init__(self, dws: DwsClient):
        self.dws = dws

    def resolve_group_roster(self, group_name: str) -> GroupRoster:
        groups_payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "chat",
                "+chat-search",
                "--query",
                group_name,
                "--limit",
                "20",
                "--format",
                "json",
            ]
        )
        groups = _nested_list(groups_payload, "groups")
        exact_groups = [group for group in groups if group.get("title") == group_name]
        chosen = exact_groups if exact_groups else groups
        if len(chosen) != 1:
            raise DwsError(
                f"unable to resolve exactly one DingTalk group named {group_name!r}"
            )
        group = chosen[0]
        conversation_id = str(group.get("openConversationId") or "").strip()
        if not conversation_id:
            raise DwsError("resolved DingTalk group is missing openConversationId")

        members_payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "chat",
                "+group-members",
                "--group",
                group_name,
                "--format",
                "json",
            ],
            timeout_seconds=120,
        )
        members = _nested_list(members_payload, "members")
        if not members:
            raise DwsError(f"DingTalk group {group_name!r} has no readable members")

        managers: list[ManagerIdentity] = []
        for member in members:
            name = str(member.get("name") or member.get("nick") or "").strip()
            open_id = str(
                member.get("openDingtalkId") or member.get("openDingTalkId") or ""
            ).strip()
            if not name or not open_id:
                raise DwsError("DingTalk group member is missing name or openDingTalkId")
            search_payload = self.dws.run_json(
                [
                    self.dws.dws_bin,
                    "contact",
                    "+search-user",
                    "--query",
                    name,
                    "--format",
                    "json",
                ],
                timeout_seconds=120,
            )
            candidates = _nested_list(search_payload, "users")
            exact = [
                candidate
                for candidate in candidates
                if str(
                    candidate.get("openDingTalkId")
                    or candidate.get("openDingtalkId")
                    or ""
                ).strip()
                == open_id
            ]
            if len(exact) != 1:
                raise DwsError(
                    f"unable to map group member {name!r} to one contact userId"
                )
            contact = exact[0]
            user_id = str(contact.get("userId") or "").strip()
            if not user_id:
                raise DwsError(f"contact record for {name!r} is missing userId")
            managers.append(
                ManagerIdentity(
                    name=name,
                    title=str(contact.get("title") or "").strip() or "职务未填写",
                    user_id=user_id,
                    open_dingtalk_id=open_id,
                )
            )
        return GroupRoster(
            name=str(group.get("title") or group_name),
            conversation_id=conversation_id,
            managers=managers,
        )

    def resolve_wiki(self, wiki_name: str) -> str:
        payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "wiki",
                "space",
                "list",
                "--limit",
                "50",
                "--format",
                "json",
            ]
        )
        spaces = _nested_list(payload, "wikiSpaces")
        exact = [space for space in spaces if space.get("name") == wiki_name]
        chosen = exact if exact else spaces
        if len(chosen) != 1:
            raise DwsError(f"unable to resolve exactly one wiki named {wiki_name!r}")
        workspace_id = str(chosen[0].get("workspaceId") or "").strip()
        if not workspace_id:
            raise DwsError("resolved wiki is missing workspaceId")
        return workspace_id

    def ensure_folder(self, *, workspace_id: str, folder_name: str) -> str:
        payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "wiki",
                "node",
                "list",
                "--workspace",
                workspace_id,
                "--format",
                "json",
            ]
        )
        nodes = _nested_list(payload, "nodes")
        exact = [
            node
            for node in nodes
            if node.get("name") == folder_name and node.get("nodeType") == "folder"
        ]
        if len(exact) > 1:
            raise DwsError(f"wiki contains duplicate folders named {folder_name!r}")
        if exact:
            return str(exact[0].get("nodeId") or "").strip()
        created = self.dws.run_json(
            [
                self.dws.dws_bin,
                "wiki",
                "node",
                "create",
                "--workspace",
                workspace_id,
                "--name",
                folder_name,
                "--type",
                "folder",
                "--format",
                "json",
            ]
        )
        folder_id = _find_nested_string(created, {"nodeId"})
        if not folder_id:
            raise DwsError("wiki folder create did not return a nodeId")
        return folder_id

    def publish_document(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
        content_file: Path,
        verification_marker: str,
    ) -> PublishedDocument:
        existing = self._find_documents(
            workspace_id=workspace_id,
            folder_id=folder_id,
            name=name,
        )
        if len(existing) > 1:
            raise DwsError(
                f"weekly OKR folder contains duplicate documents named {name!r}"
            )
        if existing:
            node_id = str(existing[0].get("nodeId") or "").strip()
            url = str(existing[0].get("docUrl") or "").strip()
            if not node_id:
                raise DwsError("existing weekly OKR document is missing nodeId")
            readback = self.dws.run_json(
                [
                    self.dws.dws_bin,
                    "doc",
                    "read",
                    "--node",
                    node_id,
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
            )
            if not _contains_text(readback, verification_marker):
                raise DwsError("existing weekly OKR document failed content readback")
            return PublishedDocument(
                node_id=node_id,
                url=url or f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
            )

        try:
            payload = self.dws.run_json(
                [
                    self.dws.dws_bin,
                    "doc",
                    "create",
                    "--name",
                    name,
                    "--folder",
                    folder_id,
                    "--content-file",
                    str(content_file),
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
            )
        except UnicodeDecodeError:
            recovered = self._find_documents(
                workspace_id=workspace_id,
                folder_id=folder_id,
                name=name,
            )
            if len(recovered) != 1:
                raise
            payload = recovered[0]
        node_id = _find_nested_string(payload, {"nodeId"})
        url = _find_nested_string(payload, {"docUrl", "url"})
        if not node_id:
            raise DwsError("doc create did not return a nodeId")
        readback = self.dws.run_json(
            [
                self.dws.dws_bin,
                "doc",
                "read",
                "--node",
                node_id,
                "--format",
                "json",
            ],
            timeout_seconds=180,
        )
        if not _contains_text(readback, verification_marker):
            raise DwsError("created weekly OKR document failed content readback")
        if not url:
            url = f"https://alidocs.dingtalk.com/i/nodes/{node_id}"
        return PublishedDocument(node_id=node_id, url=url)

    def _find_documents(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
    ) -> list[dict[str, Any]]:
        listed = self.dws.run_json(
            [
                self.dws.dws_bin,
                "wiki",
                "node",
                "list",
                "--workspace",
                workspace_id,
                "--folder",
                folder_id,
                "--format",
                "json",
            ]
        )
        return [
            node
            for node in _nested_list(listed, "nodes")
            if node.get("name") == name
        ]

    def send_group_summary(
        self,
        *,
        conversation_id: str,
        title: str,
        text: str,
    ) -> str:
        send_result = self.dws.send_message(
            conversation_id,
            text,
            title=title,
        )
        verification = self.dws.verify_message_send_result(send_result)
        state = str(verification.get("state") or "")
        if state != "sent":
            raise DwsError(f"weekly OKR group summary send was not verified: {state}")
        return state


def build_weekly_okr_prompt(
    *,
    source_path: Path,
    managers: list[ManagerIdentity],
    period_label: str,
    week_start: date,
    week_end: date,
) -> str:
    roster = [
        {"name": item.name, "title": item.title, "user_id": item.user_id}
        for item in managers
    ]
    return f"""你是 CEO-2 管理者 OKR 进度周报分析 Agent，只做只读分析，不发送消息、不创建文档。

先完整阅读并遵守技能文件：{OKR_REVIEW_SKILL_PATH}

报告范围：
- OKR 周期：{period_label}
- 本周窗口：{week_start.isoformat()} 至 {week_end.isoformat()}
- 管理者名单：{json.dumps(roster, ensure_ascii=False)}
- 实时叮当 OKR 聚合文件：{source_path}

任务：
1. 读取实时文件中每位管理者的 `processed.objectives`、`processed.okrRows`、KR 数值进度、进度历史和评论/进展。不能只看进度百分比。
2. 对本周有实质进展、风险或承诺的 KR，使用 memory_recall 以及 DWS 的文档、知识库、AI听记、群聊、日志、待办、日历等只读能力寻找独立证据。文档型产出必须找到并读取正文；只找到标题不算已验证。
3. 区分“动作/材料已发生”和“结果/效果已落地”。无法独立访问的业务系统若 OKR 进展给出明确数字，可作为工作事实，但要写清审计缺口。
4. 每位名单成员都必须返回一条 manager_reviews；没有 OKR、没有本周更新或没有独立证据也必须明确写出，不得省略。
5. 这是进度周报，不做绩效打分。重点写本周实际结果、阻塞、下周承诺和需要 CEO 决策的事项。
6. 不要在输出中暴露本地路径、token、cookie、内部命令或原始工具输出。

输出：只返回符合 schema 的 JSON。manager_reviews.name 必须逐字使用名单中的 name，且不多不少。
"""


def run_weekly_okr_report(
    *,
    store,
    gateway: WeeklyOkrGateway,
    source,
    agent: WeeklyOkrAgent,
    workspace: Path,
    now: datetime,
    force: bool,
    deliver: bool,
    group_name: str = DEFAULT_GROUP_NAME,
    wiki_name: str = DEFAULT_WIKI_NAME,
    folder_name: str = DEFAULT_FOLDER_NAME,
    period_label: str = "",
    schedule_hour: int = DEFAULT_SCHEDULE_HOUR,
    retry_seconds: int = DEFAULT_RETRY_SECONDS,
) -> WeeklyOkrReportResult:
    local_now = now.astimezone()
    report_date = local_now.date().isoformat()
    if not force and not _scheduled_run_is_due(
        store,
        now=local_now,
        schedule_hour=schedule_hour,
        retry_seconds=retry_seconds,
    ):
        return WeeklyOkrReportResult(status="not_due", report_date=report_date)

    store.set_service_state(LAST_ATTEMPT_STATE_KEY, local_now.isoformat())
    roster = gateway.resolve_group_roster(group_name)
    if not roster.managers:
        raise RuntimeError("CEO-2 manager roster is empty")

    resolved_period = period_label.strip() or current_quarter_period(
        local_now.date().isoformat()
    ).period_label
    week_start = local_now.date() - timedelta(days=local_now.weekday())
    week_end = local_now.date()
    run_dir = workspace / "OKR周报运行" / report_date
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "live_okr.json"
    report_path = run_dir / "管理者OKR进度周报.md"

    manager_payloads: list[dict[str, Any]] = []
    for manager in roster.managers:
        payload = source.fetch_user_okr(
            user_id=manager.user_id,
            period_label=resolved_period,
        )
        _validate_live_okr_payload(payload, manager=manager)
        manager_payloads.append(
            {
                "manager": {
                    "name": manager.name,
                    "title": manager.title,
                    "userId": manager.user_id,
                },
                "liveOkr": payload,
            }
        )
    raw_path.write_text(
        json.dumps(
            {
                "generatedAt": local_now.isoformat(),
                "periodLabel": resolved_period,
                "weekStart": week_start.isoformat(),
                "weekEnd": week_end.isoformat(),
                "group": roster.name,
                "managers": manager_payloads,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    analysis = agent.analyze(
        source_path=raw_path,
        managers=roster.managers,
        period_label=resolved_period,
        week_start=week_start,
        week_end=week_end,
    )
    _validate_manager_coverage(analysis, roster.managers)
    report_title = (
        f"CEO-2 管理者 OKR 进度周报（{week_start.isoformat()}—{week_end.isoformat()}）"
    )
    report_markdown = render_weekly_okr_report(
        title=report_title,
        period_label=resolved_period,
        analysis=analysis,
        managers=roster.managers,
        manager_payloads=manager_payloads,
    )
    report_path.write_text(report_markdown, encoding="utf-8")
    if not deliver:
        return WeeklyOkrReportResult(
            status="dry_run",
            report_date=report_date,
            period_label=resolved_period,
            manager_count=len(roster.managers),
            local_report_path=str(report_path),
        )

    workspace_id = gateway.resolve_wiki(wiki_name)
    folder_id = gateway.ensure_folder(
        workspace_id=workspace_id,
        folder_name=folder_name,
    )
    document = gateway.publish_document(
        workspace_id=workspace_id,
        folder_id=folder_id,
        name=report_title,
        content_file=report_path,
        verification_marker=report_title,
    )
    summary = render_group_summary(
        title=report_title,
        analysis=analysis,
        document_url=document.url,
        manager_count=len(roster.managers),
    )
    send_state = gateway.send_group_summary(
        conversation_id=roster.conversation_id,
        title=report_title,
        text=summary,
    )
    store.set_service_state(LAST_SUCCESS_STATE_KEY, report_date)
    return WeeklyOkrReportResult(
        status="sent",
        report_date=report_date,
        period_label=resolved_period,
        manager_count=len(roster.managers),
        document_url=document.url,
        local_report_path=str(report_path),
        send_state=send_state,
    )


def weekly_okr_report_command(
    settings,
    *,
    force: bool = False,
    period_label: str = "",
    now: datetime | None = None,
    quiet_not_due: bool = False,
) -> WeeklyOkrReportResult:
    from app.store import AutoReplyStore

    current = now or datetime.now().astimezone()
    store = AutoReplyStore(settings.db_path)
    if not force and not _env_bool("CEO_WEEKLY_OKR_REPORT_ENABLED", True):
        result = WeeklyOkrReportResult(
            status="disabled",
            report_date=current.date().isoformat(),
        )
        if not quiet_not_due:
            print(json.dumps(result.__dict__, ensure_ascii=False), flush=True)
        return result
    schedule_hour = _bounded_hour(
        os.getenv("CEO_WEEKLY_OKR_REPORT_HOUR", str(DEFAULT_SCHEDULE_HOUR))
    )
    retry_seconds = _positive_int(
        os.getenv("CEO_WEEKLY_OKR_RETRY_SECONDS", str(DEFAULT_RETRY_SECONDS))
    )
    if not force and not _scheduled_run_is_due(
        store,
        now=current,
        schedule_hour=schedule_hour,
        retry_seconds=retry_seconds,
    ):
        result = WeeklyOkrReportResult(
            status="not_due",
            report_date=current.date().isoformat(),
        )
        if not quiet_not_due:
            print(json.dumps(result.__dict__, ensure_ascii=False), flush=True)
        return result
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    command_template = shlex.split(os.getenv("CEO_OKR_LIVE_SOURCE_COMMAND", ""))
    if not command_template:
        raise RuntimeError("CEO_OKR_LIVE_SOURCE_COMMAND is required")
    source = DwsLiveOkrSource(
        dws=dws,
        command_template=command_template,
        timeout_seconds=300,
    )
    result = run_weekly_okr_report(
        store=store,
        gateway=DwsWeeklyOkrGateway(dws),
        source=source,
        agent=CodexWeeklyOkrAgent(
            workspace=settings.workspace,
            timeout_seconds=max(settings.codex_timeout_seconds, 1800),
            idle_timeout_seconds=max(settings.codex_idle_timeout_seconds, 900),
        ),
        workspace=settings.workspace,
        now=current,
        force=force,
        deliver=not settings.dry_run,
        group_name=os.getenv("CEO_WEEKLY_OKR_GROUP_NAME", DEFAULT_GROUP_NAME),
        wiki_name=os.getenv("CEO_WEEKLY_OKR_WIKI_NAME", DEFAULT_WIKI_NAME),
        folder_name=os.getenv("CEO_WEEKLY_OKR_FOLDER_NAME", DEFAULT_FOLDER_NAME),
        period_label=period_label,
        schedule_hour=schedule_hour,
        retry_seconds=retry_seconds,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False), flush=True)
    return result


def render_weekly_okr_report(
    *,
    title: str,
    period_label: str,
    analysis: WeeklyOkrAnalysis,
    managers: list[ManagerIdentity],
    manager_payloads: list[dict[str, Any]],
) -> str:
    stats = {
        item["manager"]["name"]: _okr_stats(item["liveOkr"])
        for item in manager_payloads
    }
    reviews = {review.name: review for review in analysis.manager_reviews}
    lines = [
        f"# {title}",
        "",
        f"- OKR 周期：{period_label}",
        f"- 管理者范围：CEO-2 管理群，共 {len(managers)} 人",
        "- 数据口径：实时叮当 OKR 目标、KR 数值进度、进展评论，以及本周可读取的独立渠道证据",
        "",
        "## 管理摘要",
        "",
        analysis.executive_summary,
    ]
    if analysis.company_progress:
        lines.extend(["", "### 本周整体进展", ""])
        lines.extend(f"- {item}" for item in analysis.company_progress)
    lines.extend(["", "## 需要 CEO 关注", ""])
    if analysis.ceo_attention_items:
        lines.extend(
            f"- **{item.topic}**（{ '、'.join(item.owner_names) or '责任人待明确' }）："
            f"{item.issue} 建议决策：{item.recommended_decision}"
            for item in analysis.ceo_attention_items
        )
    else:
        lines.append("- 本周未发现需要 CEO 立即决策的新增事项。")

    lines.extend(["", "## 管理者进度", ""])
    for manager in managers:
        review = reviews[manager.name]
        manager_stats = stats[manager.name]
        lines.extend(
            [
                f"### {manager.name}｜{manager.title}",
                "",
                f"- 系统概况：{manager_stats['objective_count']} 个 O，"
                f"{manager_stats['kr_count']} 个 KR，当前 KR 平均进度 "
                f"{manager_stats['average_progress']}",
                f"- 进度判断：{review.progress_summary}",
                f"- 证据评价：{review.evidence_assessment}",
            ]
        )
        _append_named_items(lines, "关键进展", review.key_progress)
        _append_named_items(lines, "独立证据", review.independent_evidence)
        _append_named_items(lines, "风险与阻塞", review.risks)
        _append_named_items(lines, "下周重点", review.next_week_focus)
        _append_named_items(lines, "数据缺口", review.data_gaps)
        lines.append("")

    lines.extend(["## 数据覆盖与限制", ""])
    coverage = analysis.source_coverage or ["仅使用了实时叮当 OKR 数据"]
    lines.extend(f"- {item}" for item in coverage)
    lines.extend(f"- {item}" for item in analysis.warnings)
    return "\n".join(lines).strip() + "\n"


def render_group_summary(
    *,
    title: str,
    analysis: WeeklyOkrAnalysis,
    document_url: str,
    manager_count: int,
) -> str:
    lines = [
        f"## {title}",
        "",
        f"本周已完成 {manager_count} 位管理者的实时 OKR 进度汇总。",
        analysis.executive_summary,
    ]
    if analysis.ceo_attention_items:
        lines.extend(["", "需要重点关注："])
        for item in analysis.ceo_attention_items[:5]:
            lines.extend(
                [
                    "",
                    f"- {item.topic}（{'、'.join(item.owner_names) or '责任人待明确'}）：{item.issue}",
                ]
            )
    lines.extend(["", f"完整周报：{document_url}"])
    return "\n".join(lines)


def _scheduled_run_is_due(
    store,
    *,
    now: datetime,
    schedule_hour: int,
    retry_seconds: int,
) -> bool:
    if now.weekday() != 6 or now.hour < schedule_hour:
        return False
    report_date = now.date().isoformat()
    if store.get_service_state(LAST_SUCCESS_STATE_KEY) == report_date:
        return False
    raw_attempt = store.get_service_state(LAST_ATTEMPT_STATE_KEY)
    if raw_attempt:
        try:
            last_attempt = datetime.fromisoformat(raw_attempt)
        except ValueError:
            last_attempt = None
        if last_attempt is not None:
            if last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=now.tzinfo)
            if (now - last_attempt).total_seconds() < retry_seconds:
                return False
    return True


def weekly_okr_report_window_open(now: datetime, *, schedule_hour: int) -> bool:
    if schedule_hour < 0 or schedule_hour > 23:
        raise ValueError("CEO_WEEKLY_OKR_REPORT_HOUR must be between 0 and 23")
    local_now = now.astimezone()
    return local_now.weekday() == 6 and local_now.hour >= schedule_hour


def _validate_live_okr_payload(payload: object, *, manager: ManagerIdentity) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"live OKR payload for {manager.name} is not an object")
    processed = payload.get("processed")
    if not isinstance(processed, dict):
        raise ValueError(f"live OKR payload for {manager.name} has no processed data")
    objectives = processed.get("objectives")
    rows = processed.get("okrRows")
    if not isinstance(objectives, list) or not isinstance(rows, list):
        raise ValueError(
            f"live OKR payload for {manager.name} lacks objectives or okrRows"
        )
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"live OKR row for {manager.name} is not an object")
        if str(row.get("level") or "").upper() != "KR":
            continue
        required = (
            "objectiveTitle",
            "objectiveWeight",
            "objectiveProgress",
            "krTitle",
            "krWeight",
            "krProgress",
            "krDetailsUpdatesAggregated",
        )
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(
                f"live OKR KR row for {manager.name} is missing {', '.join(missing)}"
            )


def _validate_manager_coverage(
    analysis: WeeklyOkrAnalysis,
    managers: list[ManagerIdentity],
) -> None:
    expected = [manager.name for manager in managers]
    actual = [review.name for review in analysis.manager_reviews]
    if len(actual) != len(set(actual)):
        raise ValueError("weekly OKR analysis contains duplicate manager rows")
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            f"weekly OKR analysis manager coverage mismatch: missing={missing}, extra={extra}"
        )


def _extract_report_payload(raw: str) -> dict[str, Any]:
    direct: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "manager_reviews" in payload:
            direct.append(payload)
        if not isinstance(payload, dict):
            continue
        for value in (payload.get("message"), (payload.get("item") or {}).get("text") if isinstance(payload.get("item"), dict) else None):
            if not isinstance(value, str):
                continue
            try:
                nested = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(nested, dict) and "manager_reviews" in nested:
                direct.append(nested)
    if not direct:
        raise ValueError("Codex did not return a weekly OKR report payload")
    try:
        return WeeklyOkrAnalysis.model_validate(direct[-1]).model_dump()
    except ValidationError as exc:
        raise ValueError("Codex weekly OKR payload failed validation") from exc


def _okr_stats(payload: dict[str, Any]) -> dict[str, Any]:
    processed = payload.get("processed") or {}
    objectives = processed.get("objectives") or []
    rows = processed.get("okrRows") or []
    kr_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("level") or "").upper() == "KR"
    ]
    progress_values = [
        value
        for value in (_progress_number(row.get("krProgress")) for row in kr_rows)
        if value is not None
    ]
    average = sum(progress_values) / len(progress_values) if progress_values else None
    return {
        "objective_count": len(objectives),
        "kr_count": len(kr_rows),
        "average_progress": "未填写" if average is None else f"{average:.0f}%",
    }


def _progress_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().removesuffix("%").strip()
        try:
            number = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if 0 <= number <= 1:
        number *= 100
    return max(0.0, min(number, 100.0))


def _append_named_items(lines: list[str], label: str, items: list[str]) -> None:
    if not items:
        lines.append(f"- {label}：无")
        return
    lines.append(f"- {label}：")
    lines.extend(f"  - {item}" for item in items)


def _nested_list(payload: object, key: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get(key)
            if isinstance(candidate, list):
                found.extend(item for item in candidate if isinstance(item, dict))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    return found


def _find_nested_string(payload: object, keys: set[str]) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_nested_string(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_nested_string(value, keys)
            if found:
                return found
    return ""


def _contains_text(payload: object, marker: str) -> bool:
    if isinstance(payload, str):
        return marker in payload
    if isinstance(payload, dict):
        return any(_contains_text(value, marker) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_text(value, marker) for value in payload)
    return False


def _bounded_hour(value: str) -> int:
    hour = int(value)
    if hour < 0 or hour > 23:
        raise ValueError("CEO_WEEKLY_OKR_REPORT_HOUR must be between 0 and 23")
    return hour


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("weekly OKR retry seconds must be positive")
    return parsed


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")
