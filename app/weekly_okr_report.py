from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.codex_decision import _subprocess_failure_reason
from app.codex_runner import CodexRunner
from app.dws_client import DwsClient, DwsError
from app.okr_review import DwsLiveOkrSource, current_quarter_period
from app.process_runner import run_process_with_idle_timeout


DEFAULT_GROUP_NAME = "CEO-2 管理群"
DEFAULT_WIKI_NAME = "🎯  目标与执行"
DEFAULT_FOLDER_NAME = "管理者 OKR 进度周报"
DEFAULT_ARCHIVE_DIR_NAME = "OKR档案"
LATEST_ARCHIVE_INDEX_NAME = "latest_company_okr_index.md"
LATEST_ARCHIVE_RAW_NAME = "latest_company_okr_raw.json"
DEFAULT_SCHEDULE_HOUR = 18
DEFAULT_RETRY_SECONDS = 1800
# A complete manager review can require several DWS evidence reads before the
# structured result is emitted. Keep the process bounded, but do not cut off a
# valid review at the generic five-minute task limit.
WEEKLY_OKR_AGENT_MAX_TIMEOUT_SECONDS = 900
WEEKLY_OKR_AGENT_MAX_IDLE_TIMEOUT_SECONDS = 180
WEEKLY_OKR_REPORT_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "weekly_okr_report.schema.json"
)
OKR_REVIEW_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtang-okr-review" / "SKILL.md"
)
LAST_SUCCESS_STATE_KEY = "weekly_okr_report:last_success_date"
LAST_ATTEMPT_STATE_KEY = "weekly_okr_report:last_attempt_at"


class KrScoreReview(BaseModel):
    kr_id: str
    objective_title: str
    kr_title: str
    category: Literal["业务OKR", "领导力", "文化价值观"]
    system_progress: str
    independent_evidence: str
    evidence_assessment: str
    base_score: float = Field(ge=0, le=100)
    time_discount: str
    score: float = Field(ge=0, le=100)
    improvement: str


class DimensionScoreReview(BaseModel):
    dimension: str
    required_behavior: str
    positive_evidence: str
    missing_or_contrary_evidence: str
    score: float = Field(ge=0, le=100)
    next_band_evidence: str


class ManagerReportAnalysis(BaseModel):
    name: str
    role_level: Literal["专业贡献者", "经理", "总监", "VP", "CXO", "职级待确认"]
    role_level_evidence: str
    progress_summary: str
    key_progress: list[str] = Field(default_factory=list)
    independent_evidence: list[str] = Field(default_factory=list)
    evidence_assessment: str
    risks: list[str] = Field(default_factory=list)
    next_week_focus: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    kr_reviews: list[KrScoreReview]
    leadership_dimensions: list[DimensionScoreReview]
    culture_dimensions: list[DimensionScoreReview]


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


@dataclass(frozen=True)
class CompanyOkrArchiveResult:
    status: str
    generated_at: str
    period_label: str
    manager_count: int
    kr_count: int
    raw_path: str
    index_path: str


@dataclass(frozen=True)
class ManagerScorecard:
    business_score: float | None
    leadership_score: float | None
    culture_score: float | None
    culture_coefficient: float | None
    final_score: float | None
    final_status: str


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

    def ensure_document(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
    ) -> PublishedDocument: ...

    def publish_document(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
        content_file: Path,
        verification_marker: str,
        migration_folder_id: str = "",
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
        source_payload = json.loads(source_path.read_text(encoding="utf-8"))
        source_managers = source_payload.get("managers")
        if not isinstance(source_managers, list):
            raise ValueError("weekly OKR source has no manager payloads")
        payload_by_user_id = {
            str(item.get("manager", {}).get("userId") or ""): item
            for item in source_managers
            if isinstance(item, dict)
        }
        jobs: list[tuple[ManagerIdentity, Path, Path, str]] = []
        for index, manager in enumerate(managers, start=1):
            manager_payload = payload_by_user_id.get(manager.user_id)
            if manager_payload is None:
                raise ValueError(f"weekly OKR source is missing {manager.name}")
            manager_source = source_path.with_name(
                f"{source_path.stem}.manager-{index:02d}{source_path.suffix}"
            )
            filtered_payload = dict(source_payload)
            filtered_payload["managers"] = [manager_payload]
            manager_source.write_text(
                json.dumps(filtered_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            source_hash = _analysis_source_hash(manager_payload["liveOkr"])
            manager_cache_key = hashlib.sha256(
                manager.user_id.encode("utf-8")
            ).hexdigest()[:16]
            analysis_path = source_path.with_name(
                f"analysis.manager-{manager_cache_key}.json"
            )
            jobs.append((manager, manager_source, analysis_path, source_hash))

        executor = ThreadPoolExecutor(max_workers=min(3, len(jobs)))
        futures = {
            manager.user_id: executor.submit(
                self._analyze_one,
                source_path=manager_source,
                analysis_path=analysis_path,
                source_hash=source_hash,
                manager=manager,
                period_label=period_label,
                week_start=week_start,
                week_end=week_end,
            )
            for manager, manager_source, analysis_path, source_hash in jobs
        }
        try:
            results = [futures[manager.user_id].result() for manager in managers]
        except BaseException:
            for future in futures.values():
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)

        return WeeklyOkrAnalysis(
            executive_summary=(
                f"已完成 {len(managers)} 位 CEO-2 成员的逐 KR 综合证据评分；"
                "系统进度仅作为线索，最终判断以评论/进展、独立证据、实际效果和完成时间为准。"
            ),
            company_progress=_unique_strings(
                item for result in results for item in result.company_progress[:1]
            ),
            ceo_attention_items=[
                item for result in results for item in result.ceo_attention_items[:1]
            ],
            manager_reviews=[result.manager_reviews[0] for result in results],
            source_coverage=_unique_strings(
                item for result in results for item in result.source_coverage
            ),
            warnings=_unique_strings(
                item for result in results for item in result.warnings
            ),
        )

    def _analyze_one(
        self,
        *,
        source_path: Path,
        analysis_path: Path,
        source_hash: str,
        manager: ManagerIdentity,
        period_label: str,
        week_start: date,
        week_end: date,
    ) -> WeeklyOkrAnalysis:
        if analysis_path.exists():
            cached = json.loads(analysis_path.read_text(encoding="utf-8"))
            if cached.get("source_hash") == source_hash:
                analysis = WeeklyOkrAnalysis.model_validate(cached.get("analysis"))
                _validate_manager_coverage(analysis, [manager])
                filtered_payload = json.loads(source_path.read_text(encoding="utf-8"))
                _validate_kr_coverage(analysis, filtered_payload["managers"])
                return analysis
        filtered_payload = json.loads(source_path.read_text(encoding="utf-8"))
        expected_kr_count = len(
            _live_kr_rows(filtered_payload["managers"], manager.name)
        )
        prompt = build_weekly_okr_prompt(
            source_path=source_path,
            managers=[manager],
            period_label=period_label,
            week_start=week_start,
            week_end=week_end,
        )
        prompt += (
            f"\n硬性输出校验：{manager.name} 必须返回恰好 {expected_kr_count} 条 "
            "kr_reviews；少一条或多一条都不可提交。"
        )
        env = self.runner.build_env()
        validation_error = ""
        for attempt in range(2):
            attempt_prompt = prompt
            if validation_error:
                attempt_prompt += (
                    "\n上一轮输出未通过结构化校验："
                    f"{validation_error}。请完整重做该成员的全部 KR，不得只返回示例行。"
                )
            command = self.runner.build_command(
                attempt_prompt,
                session_id=None,
                output_schema_path=WEEKLY_OKR_REPORT_SCHEMA_PATH,
                preserve_native_instructions=True,
                ignore_user_config=True,
            )
            if self.executor is not None:
                raw = self.executor(command, attempt_prompt, env)
            else:
                completed = run_process_with_idle_timeout(
                    command,
                    prompt=attempt_prompt,
                    env=env,
                    total_timeout_seconds=self.timeout_seconds,
                    idle_timeout_seconds=self.idle_timeout_seconds,
                )
                if completed.timed_out:
                    raise RuntimeError(
                        completed.timeout_reason or "weekly OKR agent timed out"
                    )
                if completed.returncode != 0:
                    raise RuntimeError(
                        _subprocess_failure_reason(completed.stderr, completed.stdout)
                    )
                raw = completed.stdout
            try:
                analysis = WeeklyOkrAnalysis.model_validate(_extract_report_payload(raw))
                _validate_manager_coverage(analysis, [manager])
                _validate_kr_coverage(analysis, filtered_payload["managers"])
            except ValueError as exc:
                if attempt == 0:
                    validation_error = str(exc)
                    continue
                raise
            analysis_path.write_text(
                json.dumps(
                    {"source_hash": source_hash, "analysis": analysis.model_dump()},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return analysis
        raise AssertionError("weekly OKR validation retry exhausted")


class DwsWeeklyOkrGateway:
    def __init__(self, dws: DwsClient):
        self.dws = dws

    def resolve_group_roster(self, group_name: str) -> GroupRoster:
        conversations_payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "chat",
                "+conversation-list",
                "--page-all",
                "--limit",
                "100",
                "--format",
                "json",
            ]
        )
        conversations = _nested_list(conversations_payload, "conversations")
        exact_groups = [
            conversation
            for conversation in conversations
            if conversation.get("conversationName") == group_name
        ]
        if len(exact_groups) != 1:
            raise DwsError(
                f"unable to resolve exactly one DingTalk group named {group_name!r}"
            )
        group = exact_groups[0]
        conversation_id = str(group.get("openConversationId") or "").strip()
        if not conversation_id:
            raise DwsError("resolved DingTalk group is missing openConversationId")

        members_payload = self.dws.run_json(
            [
                self.dws.dws_bin,
                "chat",
                "+chat-members-list",
                "--conversation-id",
                conversation_id,
                "--member-types",
                "user",
                "--format",
                "json",
            ],
            timeout_seconds=120,
        )
        members = _nested_list(members_payload, "users")
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
            name=str(group.get("conversationName") or group_name),
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
        migration_folder_id: str = "",
    ) -> PublishedDocument:
        existing = self._find_documents(
            workspace_id=workspace_id,
            folder_id=folder_id,
            name=name,
        )
        if len(existing) > 1:
            raise DwsError(
                f"weekly OKR parent contains duplicate documents named {name!r}"
            )
        if not existing and migration_folder_id and migration_folder_id != folder_id:
            existing = self._move_existing_document(
                workspace_id=workspace_id,
                source_folder_id=migration_folder_id,
                target_folder_id=folder_id,
                name=name,
            )
        if existing:
            node_id = str(existing[0].get("nodeId") or "").strip()
            url = str(existing[0].get("docUrl") or "").strip()
            if not node_id:
                raise DwsError("existing weekly OKR document is missing nodeId")
            self.dws.run_json(
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
            self._overwrite_document(
                node_id=node_id,
                content_file=content_file,
            )
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
                raise DwsError("updated weekly OKR document failed content readback")
            return PublishedDocument(
                node_id=node_id,
                url=url or f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
            )

        document = self.ensure_document(
            workspace_id=workspace_id,
            folder_id=folder_id,
            name=name,
        )
        node_id = document.node_id
        url = document.url
        self._overwrite_document(
            node_id=node_id,
            content_file=content_file,
        )
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

    def ensure_document(
        self,
        *,
        workspace_id: str,
        folder_id: str,
        name: str,
    ) -> PublishedDocument:
        existing = self._find_documents(
            workspace_id=workspace_id,
            folder_id=folder_id,
            name=name,
        )
        if len(existing) > 1:
            raise DwsError(
                f"weekly OKR parent contains duplicate documents named {name!r}"
            )
        if existing:
            node_id = str(existing[0].get("nodeId") or "").strip()
            url = str(existing[0].get("docUrl") or "").strip()
            if not node_id:
                raise DwsError("existing weekly OKR document is missing nodeId")
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
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
            )
        except (UnicodeDecodeError, DwsError):
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
        return PublishedDocument(
            node_id=node_id,
            url=url or f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
        )

    def _move_existing_document(
        self,
        *,
        workspace_id: str,
        source_folder_id: str,
        target_folder_id: str,
        name: str,
    ) -> list[dict[str, Any]]:
        source_documents = self._find_documents(
            workspace_id=workspace_id,
            folder_id=source_folder_id,
            name=name,
        )
        if len(source_documents) > 1:
            raise DwsError(
                f"weekly OKR source contains duplicate documents named {name!r}"
            )
        if not source_documents:
            return []
        source_node_id = str(source_documents[0].get("nodeId") or "").strip()
        if not source_node_id:
            raise DwsError("weekly OKR source document is missing nodeId")
        self.dws.run_json(
            [
                self.dws.dws_bin,
                "wiki",
                "node",
                "move",
                "--workspace",
                workspace_id,
                "--node",
                source_node_id,
                "--folder",
                target_folder_id,
                "--format",
                "json",
            ]
        )
        moved = self._find_documents(
            workspace_id=workspace_id,
            folder_id=target_folder_id,
            name=name,
        )
        if len(moved) != 1 or str(moved[0].get("nodeId") or "") != source_node_id:
            raise DwsError("weekly OKR document move failed hierarchy readback")
        return moved

    def _overwrite_document(self, *, node_id: str, content_file: Path) -> None:
        try:
            self.dws.run_json(
                [
                    self.dws.dws_bin,
                    "doc",
                    "update",
                    "--node",
                    node_id,
                    "--content-file",
                    str(content_file),
                    "--mode",
                    "overwrite",
                    "--yes",
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
            )
        except UnicodeDecodeError:
            # Long-document progress previews can contain malformed UTF-8 even
            # after the write completed. The mandatory readback below is the
            # source of truth for whether all chunks reached DingTalk.
            return

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
2. 必须按实时文件中的原始顺序，对每一个 KR 返回且只返回一条 kr_reviews；objective_title 和 kr_title 尽量逐字复制实时源，程序以顺序和标题共同绑定系统 KR，kr_id 仅作辅助字段。系统百分比和自述只作为线索，综合评论/进展、独立证据、目标承诺、实际效果和完成时间，按技能中的 0/20/40/60/80/100 校准规则给出 base_score 和应用时间折扣后的 score。不得用系统进度直接换算评分。
3. 使用 memory_recall 以及 DWS 的文档、知识库、AI听记、群聊、日志、待办、日历等只读能力寻找独立证据。文档型产出必须找到并读取正文；只找到标题按未找到处理。无法独立访问的业务系统若进展给出明确数字，可作为工作事实，但要写清审计缺口。
4. 将 KR 语义分类为业务OKR、领导力或文化价值观。业务/GTM、产品、工程类分别应用技能中的效果证据门槛；多项承诺逐项评价；先按结果质量给基础分，再按 DDL 应用时间折扣。
5. 使用当前钉钉通讯录 title 作为职级授权来源，确认专业贡献者、经理、总监、VP、CXO；无法可靠确认则写职级待确认。管理者按四个领导力维度分别评分；专业贡献者只有在系统明确分配领导力考核时才返回四维候选分，且候选分不进入专业贡献者最终公式。所有人按三个文化价值观维度分别评分。80+ 必须有超出标准的具体案例，90+ 必须有相应榜样范围证据，低于 70 必须写明未满足行为或反面案例。不要用业务得分替代领导力或文化得分。
6. 每位名单成员都必须返回一条 manager_reviews；没有 OKR、没有本周更新或没有独立证据也不得省略。重点进展、风险、下周承诺和 CEO 决策事项保持简洁。
7. 不要自行汇总最终绩效分；程序会用 KR score 按 O 权重×KR 权重计算业务 OKR 分，领导力四维取算术平均，文化三维取算术平均，再按技能公式计算。不要在输出中暴露本地路径、token、cookie、内部命令或原始工具输出。

输出：只返回符合 schema 的 JSON。manager_reviews.name 必须逐字使用名单中的 name，且不多不少；每个 KR 的说明控制在管理者可读的简洁篇幅。
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
    scheduled_report_date = _scheduled_report_date(
        local_now,
        schedule_hour=schedule_hour,
    )
    report_end = local_now.date() if force else scheduled_report_date
    report_date = report_end.isoformat()
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
    week_start = (
        local_now.date() - timedelta(days=local_now.weekday())
        if force
        else report_end - timedelta(days=6)
    )
    week_end = report_end
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
    _validate_kr_coverage(analysis, manager_payloads)
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
    master_path = run_dir / "管理者OKR进度周报-完整底稿.md"
    master_path.write_text(report_markdown, encoding="utf-8")
    summary_prefix, limits_section, appendices = _report_publication_parts(
        report_markdown,
        title=report_title,
        period_label=resolved_period,
        managers=roster.managers,
    )
    appendix_dir = run_dir / "评分附录"
    appendix_dir.mkdir(parents=True, exist_ok=True)
    appendix_paths: dict[str, Path] = {}
    for manager in roster.managers:
        appendix_path = appendix_dir / (
            f"manager-{hashlib.sha256(manager.user_id.encode('utf-8')).hexdigest()[:16]}.md"
        )
        appendix_path.write_text(appendices[manager.name], encoding="utf-8")
        appendix_paths[manager.name] = appendix_path
    report_path.write_text(
        _render_management_report(
            summary_prefix=summary_prefix,
            limits_section=limits_section,
            managers=roster.managers,
            appendix_documents={},
        ),
        encoding="utf-8",
    )
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
    document = gateway.ensure_document(
        workspace_id=workspace_id,
        folder_id=folder_id,
        name=report_title,
    )
    appendix_documents: dict[str, PublishedDocument] = {}
    for manager in roster.managers:
        appendix_documents[manager.name] = gateway.publish_document(
            workspace_id=workspace_id,
            folder_id=document.node_id,
            name=f"{report_title}｜评分附录｜{manager.name}",
            content_file=appendix_paths[manager.name],
            verification_marker=f"附录校验：{manager.name}",
            migration_folder_id=folder_id,
        )
    report_path.write_text(
        _render_management_report(
            summary_prefix=summary_prefix,
            limits_section=limits_section,
            managers=roster.managers,
            appendix_documents=appendix_documents,
        ),
        encoding="utf-8",
    )
    document = gateway.publish_document(
        workspace_id=workspace_id,
        folder_id=folder_id,
        name=report_title,
        content_file=report_path,
        verification_marker="数据覆盖与限制",
    )
    summary = render_group_summary(
        title=report_title,
        analysis=analysis,
        document_url=document.url,
        manager_count=len(roster.managers),
        manager_payloads=manager_payloads,
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
            timeout_seconds=min(
                settings.codex_timeout_seconds,
                WEEKLY_OKR_AGENT_MAX_TIMEOUT_SECONDS,
            ),
            idle_timeout_seconds=min(
                settings.codex_idle_timeout_seconds,
                WEEKLY_OKR_AGENT_MAX_IDLE_TIMEOUT_SECONDS,
            ),
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


def refresh_company_okr_archive_command(
    settings,
    *,
    period_label: str = "",
    group_name: str = DEFAULT_GROUP_NAME,
    now: datetime | None = None,
) -> CompanyOkrArchiveResult:
    current = now or datetime.now().astimezone()
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
    result = refresh_company_okr_archive(
        gateway=DwsWeeklyOkrGateway(dws),
        source=source,
        workspace=settings.workspace,
        now=current,
        group_name=group_name,
        period_label=period_label,
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
    scorecards = _manager_scorecards(analysis, manager_payloads)
    finalized_scores = [
        card.final_score for card in scorecards.values() if card.final_score is not None
    ]
    pending_count = len(managers) - len(finalized_scores)
    lines = [
        f"# {title}",
        "",
        f"- OKR 周期：{period_label}",
        f"- 管理者范围：CEO-2 管理群，共 {len(managers)} 人",
        "- 数据口径：实时叮当 OKR 目标、KR 评论/进展，以及文档、知识库、AI听记、群聊等可读取的独立证据",
        "- 综合证据评分：系统进度仅作为线索；每个 KR 按实际结果、证据强度和完成时间评分",
        "- 评分公式：业务 OKR 按 O 权重×KR 权重汇总；管理者最终分 =（业务 OKR×70% + 领导力×30%）×文化系数",
        "- 使用提醒：这是截至本周的证据完成度快照，不是季度末绩效预测；管理会应重点审阅证据缺口、风险和下周检查点",
        "",
        "## 管理会审阅页",
        "",
        analysis.executive_summary,
        "",
        "### 评分概览",
        "",
        f"- 已形成最终分：{len(finalized_scores)} 人；暂不形成：{pending_count} 人",
        f"- 已形成最终分平均值：{sum(finalized_scores) / len(finalized_scores):.1f}"
        if finalized_scores
        else "- 已形成最终分平均值：暂不形成",
        f"- 低于 60 分：{sum(score < 60 for score in finalized_scores)} 人；"
        "当前主要原因是结果证据、业务效果或系统回填尚未闭环",
    ]
    if analysis.company_progress:
        lines.extend(["", "### 本周重点进展", ""])
        lines.extend(f"- {item}" for item in analysis.company_progress[:6])
    lines.extend(["", "### 管理会待决策与主要风险", ""])
    if analysis.ceo_attention_items:
        lines.extend(
            f"- **{item.topic}**（{ '、'.join(item.owner_names) or '责任人待明确' }）："
            f"{item.issue} 建议决策：{item.recommended_decision}"
            for item in analysis.ceo_attention_items[:8]
        )
    else:
        lines.append("- 本周未发现需要管理会立即决策的新增事项。")

    lines.extend(["", "### 管理者审阅表", ""])
    lines.extend(
        [
            "| 管理者 | 最终分/状态 | 本周重点进展 | 主要风险 | 下周检查点 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for manager in managers:
        review = reviews[manager.name]
        scorecard = scorecards[manager.name]
        lines.append(
            f"| {_md_cell(manager.name)} | "
            f"{_score_text(scorecard.final_score)} / {_md_cell(scorecard.final_status)} | "
            f"{_brief_cell(review.key_progress)} | {_brief_cell(review.risks)} | "
            f"{_brief_cell(review.next_week_focus)} |"
        )

    lines.extend(["", "### 分项评分表", ""])
    lines.extend(
        [
            "| 管理者 | 职级 | 业务 OKR | 领导力 | 文化价值观 | 文化系数 | 最终分 | 状态 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for manager in managers:
        review = reviews[manager.name]
        scorecard = scorecards[manager.name]
        lines.append(
            f"| {_md_cell(manager.name)} | {_md_cell(review.role_level)} | "
            f"{_score_text(scorecard.business_score)} | "
            f"{_score_text(scorecard.leadership_score)} | "
            f"{_score_text(scorecard.culture_score)} | "
            f"{_coefficient_text(scorecard.culture_coefficient)} | "
            f"{_score_text(scorecard.final_score)} | {_md_cell(scorecard.final_status)} |"
        )

    lines.extend(["", "## 附录：逐人证据与逐 KR 评分", ""])
    for manager in managers:
        review = reviews[manager.name]
        manager_stats = stats[manager.name]
        scorecard = scorecards[manager.name]
        lines.extend(
            [
                f"### {manager.name}｜{manager.title}",
                "",
                f"- 系统概况：{manager_stats['objective_count']} 个 O，"
                f"{manager_stats['kr_count']} 个 KR，当前 KR 平均进度 "
                f"{manager_stats['average_progress']}",
                f"- 职级判断：{review.role_level}（{review.role_level_evidence}）",
                f"- 综合评分：业务 OKR {_score_text(scorecard.business_score)}；"
                f"领导力 {_score_text(scorecard.leadership_score)}；"
                f"文化价值观 {_score_text(scorecard.culture_score)}；"
                f"文化系数 {_coefficient_text(scorecard.culture_coefficient)}；"
                f"最终分 {_score_text(scorecard.final_score)}（{scorecard.final_status}）",
                f"- 进度判断：{review.progress_summary}",
                f"- 证据评价：{review.evidence_assessment}",
            ]
        )
        _append_named_items(lines, "关键进展", review.key_progress)
        _append_named_items(lines, "独立证据", review.independent_evidence)
        _append_named_items(lines, "风险与阻塞", review.risks)
        _append_named_items(lines, "下周重点", review.next_week_focus)
        _append_named_items(lines, "数据缺口", review.data_gaps)
        if review.leadership_dimensions:
            lines.extend(
                [
                    "",
                    "#### 领导力评分",
                    "",
                    "| 维度 | 要求 | 正向证据 | 缺口/反面证据 | 评分 | 升档证据 |",
                    "| --- | --- | --- | --- | ---: | --- |",
                ]
            )
            lines.extend(_dimension_row(item) for item in review.leadership_dimensions)
        if review.culture_dimensions:
            lines.extend(
                [
                    "",
                    "#### 文化价值观评分",
                    "",
                    "| 维度 | 要求 | 正向证据 | 缺口/反面证据 | 评分 | 升档证据 |",
                    "| --- | --- | --- | --- | ---: | --- |",
                ]
            )
            lines.extend(_dimension_row(item) for item in review.culture_dimensions)
        lines.extend(
            [
                "",
                "#### 逐 KR 评分",
                "",
                "| KR | 权重 | 系统KR进度 | 独立检索证据内容 | 证据评价 | 评分 | 提升建议 |",
                "| --- | ---: | --- | --- | --- | ---: | --- |",
            ]
        )
        row_by_id = _live_kr_rows(manager_payloads, manager.name)
        for kr in review.kr_reviews:
            live_row = row_by_id[kr.kr_id]
            weight = f"O {live_row.get('objectiveWeight', 0)}% × KR {live_row.get('krWeight', 0)}%"
            lines.append(
                f"| {_md_cell(live_row.get('krTitle', kr.kr_title))} | {_md_cell(weight)} | "
                f"{_md_cell(kr.system_progress)} | {_md_cell(kr.independent_evidence)} | "
                f"{_md_cell(kr.evidence_assessment)} | {kr.score:.1f} | "
                f"{_md_cell(kr.improvement)} |"
            )
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
    manager_payloads: list[dict[str, Any]],
) -> str:
    scorecards = _manager_scorecards(analysis, manager_payloads)
    finalized = [item.final_score for item in scorecards.values() if item.final_score is not None]
    pending = manager_count - len(finalized)
    lines = [
        f"## {title}",
        "",
        "已按综合证据口径更新：系统进度仅作线索，结合评论/进展和独立证据逐 KR 评分。",
        f"评分概览：{manager_count} 人中 {len(finalized)} 人形成最终分，{pending} 人因职级或维度证据待补充暂不形成最终分"
        + (f"；已形成最终分的平均值为 {sum(finalized) / len(finalized):.1f}。" if finalized else "。"),
    ]
    if analysis.company_progress:
        lines.extend(["", "重点进展："])
        lines.extend(f"- {item}" for item in analysis.company_progress[:3])
    if analysis.ceo_attention_items:
        lines.extend(["", "主要风险："])
        for item in analysis.ceo_attention_items[:3]:
            lines.extend(
                [
                    "",
                    f"- {item.topic}（{'、'.join(item.owner_names) or '责任人待明确'}）：{item.issue}",
                ]
            )
    lines.extend(["", f"完整周报：{document_url}"])
    return "\n".join(lines)


def _report_publication_parts(
    report_markdown: str,
    *,
    title: str,
    period_label: str,
    managers: list[ManagerIdentity],
) -> tuple[str, str, dict[str, str]]:
    summary_prefix, appendix_marker, remainder = report_markdown.partition(
        "## 附录：逐人证据与逐 KR 评分"
    )
    if not appendix_marker:
        raise ValueError("weekly OKR report is missing the appendix marker")
    manager_details, limits_marker, limits_body = remainder.partition(
        "## 数据覆盖与限制"
    )
    if not limits_marker:
        raise ValueError("weekly OKR report is missing the limits marker")

    appendices: dict[str, str] = {}
    for index, manager in enumerate(managers):
        start_marker = f"### {manager.name}｜"
        start = manager_details.find(start_marker)
        if start < 0:
            raise ValueError(f"weekly OKR report is missing appendix for {manager.name}")
        if index + 1 < len(managers):
            next_marker = f"### {managers[index + 1].name}｜"
            end = manager_details.find(next_marker, start + len(start_marker))
            if end < 0:
                raise ValueError(
                    f"weekly OKR report is missing appendix boundary for {manager.name}"
                )
        else:
            end = len(manager_details)
        section = manager_details[start:end].strip()
        appendices[manager.name] = (
            f"# {title}｜评分附录｜{manager.name}\n\n"
            f"- OKR 周期：{period_label}\n"
            "- 评分口径：综合系统评论/进展、独立证据、实际效果和完成时间；系统进度仅作为线索\n\n"
            f"{section}\n\n"
            f"## 附录校验：{manager.name}\n"
        )
    limits_section = f"## 数据覆盖与限制{limits_body}".strip() + "\n"
    return summary_prefix.strip() + "\n", limits_section, appendices


def _render_management_report(
    *,
    summary_prefix: str,
    limits_section: str,
    managers: list[ManagerIdentity],
    appendix_documents: dict[str, PublishedDocument],
) -> str:
    lines = [summary_prefix.rstrip(), "", "## 逐人评分附录", ""]
    for manager in managers:
        label = f"{manager.name}｜{manager.title}"
        document = appendix_documents.get(manager.name)
        if document is None:
            lines.append(f"- {label}（发布时生成线上链接）")
        else:
            lines.append(f"- [{label}]({document.url})")
    lines.extend(["", limits_section.rstrip(), ""])
    return "\n".join(lines)


def _scheduled_run_is_due(
    store,
    *,
    now: datetime,
    schedule_hour: int,
    retry_seconds: int,
) -> bool:
    local_now = now.astimezone()
    if local_now.weekday() == 6 and local_now.hour < schedule_hour:
        return False
    report_date = _scheduled_report_date(
        local_now,
        schedule_hour=schedule_hour,
    ).isoformat()
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
                last_attempt = last_attempt.replace(tzinfo=local_now.tzinfo)
            if (local_now - last_attempt).total_seconds() < retry_seconds:
                return False
    return True


def _scheduled_report_date(now: datetime, *, schedule_hour: int) -> date:
    if schedule_hour < 0 or schedule_hour > 23:
        raise ValueError("CEO_WEEKLY_OKR_REPORT_HOUR must be between 0 and 23")
    local_now = now.astimezone()
    days_since_sunday = (local_now.weekday() - 6) % 7
    report_date = local_now.date() - timedelta(days=days_since_sunday)
    if local_now.weekday() == 6 and local_now.hour < schedule_hour:
        report_date -= timedelta(days=7)
    return report_date


def weekly_okr_report_window_open(now: datetime, *, schedule_hour: int) -> bool:
    _scheduled_report_date(now, schedule_hour=schedule_hour)
    local_now = now.astimezone()
    return not (local_now.weekday() == 6 and local_now.hour < schedule_hour)


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
            "objectiveId",
            "objectiveTitle",
            "objectiveWeight",
            "objectiveProgress",
            "krId",
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


def _validate_kr_coverage(
    analysis: WeeklyOkrAnalysis,
    manager_payloads: list[dict[str, Any]],
) -> None:
    payloads = {item["manager"]["name"]: item for item in manager_payloads}
    for review in analysis.manager_reviews:
        expected_rows = _live_kr_rows(manager_payloads, review.name)
        _bind_kr_reviews_to_live_rows(review, expected_rows)
        actual_ids = [item.kr_id for item in review.kr_reviews]
        if len(actual_ids) != len(set(actual_ids)):
            raise ValueError(f"weekly OKR analysis contains duplicate KRs for {review.name}")
        if set(actual_ids) != set(expected_rows):
            missing = sorted(set(expected_rows) - set(actual_ids))
            extra = sorted(set(actual_ids) - set(expected_rows))
            raise ValueError(
                f"weekly OKR KR coverage mismatch for {review.name}: "
                f"missing={missing}, extra={extra}"
            )
        for item in review.kr_reviews:
            if item.score > item.base_score:
                raise ValueError(f"time-discounted score exceeds base score for KR {item.kr_id}")
        if len(review.culture_dimensions) != 3:
            raise ValueError(f"culture scoring must contain three dimensions for {review.name}")
        if review.role_level in {"经理", "总监", "VP", "CXO"}:
            if len(review.leadership_dimensions) != 4:
                raise ValueError(
                    f"leadership scoring must contain four dimensions for {review.name}"
                )
        elif len(review.leadership_dimensions) not in {0, 4}:
            raise ValueError(
                f"candidate leadership scoring must contain zero or four dimensions for {review.name}"
            )
        if review.name not in payloads:
            raise ValueError(f"missing live OKR payload for {review.name}")


def _bind_kr_reviews_to_live_rows(
    review: ManagerReportAnalysis,
    expected_rows: dict[str, dict[str, Any]],
) -> None:
    expected_ids = list(expected_rows)
    if len(review.kr_reviews) != len(expected_ids):
        raise ValueError(
            f"weekly OKR KR row count mismatch for {review.name}: "
            f"expected={len(expected_ids)}, actual={len(review.kr_reviews)}"
        )
    ids_by_titles: dict[tuple[str, str], list[str]] = {}
    for kr_id, row in expected_rows.items():
        key = (
            _normalized_title(row.get("objectiveTitle")),
            _normalized_title(row.get("krTitle")),
        )
        ids_by_titles.setdefault(key, []).append(kr_id)
    resolved_ids: dict[int, str] = {}
    for index, item in enumerate(review.kr_reviews):
        key = (
            _normalized_title(item.objective_title),
            _normalized_title(item.kr_title),
        )
        matches = ids_by_titles.get(key, [])
        if len(matches) == 1:
            resolved_ids[index] = matches[0]
    if len(set(resolved_ids.values())) != len(resolved_ids):
        raise ValueError(f"weekly OKR analysis contains duplicate KR titles for {review.name}")
    if len(expected_ids) > 1 and not resolved_ids:
        raise ValueError(f"weekly OKR analysis has no title anchors for {review.name}")
    displaced = [
        (index, kr_id)
        for index, kr_id in resolved_ids.items()
        if expected_ids[index] != kr_id
    ]
    if displaced:
        raise ValueError(
            f"weekly OKR KR order mismatch for {review.name}: displaced={displaced}"
        )
    for index, item in enumerate(review.kr_reviews):
        item.kr_id = expected_ids[index]


def _normalized_title(value: object) -> str:
    return " ".join(str(value or "").split())


def _manager_scorecards(
    analysis: WeeklyOkrAnalysis,
    manager_payloads: list[dict[str, Any]],
) -> dict[str, ManagerScorecard]:
    cards: dict[str, ManagerScorecard] = {}
    for review in analysis.manager_reviews:
        live_rows = _live_kr_rows(manager_payloads, review.name)
        weighted_scores: list[tuple[float, float]] = []
        for kr in review.kr_reviews:
            if kr.category != "业务OKR":
                continue
            row = live_rows[kr.kr_id]
            objective_weight = _weight_number(row.get("objectiveWeight"))
            kr_weight = _weight_number(row.get("krWeight"))
            combined_weight = objective_weight * kr_weight
            if combined_weight > 0:
                weighted_scores.append((kr.score, combined_weight))
        business_score = _weighted_average(weighted_scores)
        leadership_score = _plain_average(review.leadership_dimensions)
        culture_score = _plain_average(review.culture_dimensions)
        coefficient = (
            _culture_coefficient(culture_score) if culture_score is not None else None
        )
        final_score: float | None = None
        if business_score is None:
            status = "缺少可计权业务 OKR"
        elif coefficient is None:
            status = "文化维度待补充"
        elif review.role_level in {"经理", "总监", "VP", "CXO"}:
            if leadership_score is None:
                status = "领导力维度待补充"
            else:
                final_score = (
                    business_score * 0.7 + leadership_score * 0.3
                ) * coefficient
                status = "已形成最终分"
        elif review.role_level == "专业贡献者":
            final_score = business_score * coefficient
            status = "已形成最终分（专业贡献者公式）"
        else:
            status = "职级待确认，暂不形成最终分"
        cards[review.name] = ManagerScorecard(
            business_score=_rounded(business_score),
            leadership_score=_rounded(leadership_score),
            culture_score=_rounded(culture_score),
            culture_coefficient=coefficient,
            final_score=_rounded(final_score),
            final_status=status,
        )
    return cards


def _live_kr_rows(
    manager_payloads: list[dict[str, Any]],
    manager_name: str,
) -> dict[str, dict[str, Any]]:
    for item in manager_payloads:
        if item.get("manager", {}).get("name") != manager_name:
            continue
        rows = item.get("liveOkr", {}).get("processed", {}).get("okrRows", [])
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("level") or "").upper() != "KR":
                continue
            kr_id = str(row.get("krId") or "").strip()
            if not kr_id:
                raise ValueError(f"live OKR KR row for {manager_name} is missing krId")
            if kr_id in result:
                raise ValueError(f"live OKR contains duplicate KR {kr_id} for {manager_name}")
            result[kr_id] = row
        return result
    raise ValueError(f"missing live OKR payload for {manager_name}")


def refresh_company_okr_archive(
    *,
    gateway: WeeklyOkrGateway,
    source,
    workspace: Path,
    now: datetime,
    group_name: str = DEFAULT_GROUP_NAME,
    period_label: str = "",
) -> CompanyOkrArchiveResult:
    local_now = now.astimezone()
    roster = gateway.resolve_group_roster(group_name)
    if not roster.managers:
        raise RuntimeError("CEO-2 manager roster is empty")

    resolved_period = period_label.strip() or current_quarter_period(
        local_now.date().isoformat()
    ).period_label
    period_slug = _period_slug(resolved_period)
    archive_dir = workspace / DEFAULT_ARCHIVE_DIR_NAME / period_slug
    archive_dir.mkdir(parents=True, exist_ok=True)
    raw_path = archive_dir / f"company_okr_{period_slug}_raw.json"
    index_path = archive_dir / f"company_okr_{period_slug}_index.md"
    latest_index_path = workspace / DEFAULT_ARCHIVE_DIR_NAME / LATEST_ARCHIVE_INDEX_NAME
    latest_raw_path = workspace / DEFAULT_ARCHIVE_DIR_NAME / LATEST_ARCHIVE_RAW_NAME

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
                    "openDingtalkId": manager.open_dingtalk_id,
                },
                "liveOkr": payload,
            }
        )

    archive_payload = {
        "generatedAt": local_now.isoformat(),
        "periodLabel": resolved_period,
        "group": roster.name,
        "groupConversationId": roster.conversation_id,
        "managers": manager_payloads,
    }
    raw_text = json.dumps(archive_payload, ensure_ascii=False, indent=2)
    raw_path.write_text(raw_text, encoding="utf-8")
    latest_raw_path.write_text(raw_text, encoding="utf-8")

    index_text = render_company_okr_archive_index(archive_payload)
    index_path.write_text(index_text, encoding="utf-8")
    latest_index_path.write_text(index_text, encoding="utf-8")
    return CompanyOkrArchiveResult(
        status="archived",
        generated_at=local_now.isoformat(),
        period_label=resolved_period,
        manager_count=len(roster.managers),
        kr_count=_archive_kr_count(manager_payloads),
        raw_path=str(raw_path),
        index_path=str(index_path),
    )


def render_company_okr_archive_index(payload: dict[str, Any]) -> str:
    managers = payload.get("managers")
    if not isinstance(managers, list):
        raise ValueError("company OKR archive has no managers")
    lines = [
        f"# 公司 OKR 索引（{payload.get('periodLabel', '')}）",
        "",
        f"- 生成时间：{payload.get('generatedAt', '')}",
        f"- 来源群：{payload.get('group', '')}",
        f"- 管理者数：{len(managers)}",
        f"- KR 数：{_archive_kr_count(managers)}",
        "",
        "用途：供 task agent 判断事项是否关联公司目标、OKR/KR、关键项目或管理风险；这不是 TODO 完成证据。",
        "",
    ]
    for item in managers:
        manager = item.get("manager", {}) if isinstance(item, dict) else {}
        live_okr = item.get("liveOkr", {}) if isinstance(item, dict) else {}
        rows = live_okr.get("processed", {}).get("okrRows", [])
        lines.extend(
            [
                f"## {manager.get('name', '')}｜{manager.get('title', '')}",
                "",
            ]
        )
        current_objective = ""
        manager_kr_count = 0
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            objective = str(row.get("objectiveTitle") or row.get("objective") or "").strip()
            if objective and objective != current_objective:
                current_objective = objective
                lines.append(f"- O：{objective}")
            if str(row.get("level") or "").upper() != "KR":
                continue
            kr_title = str(row.get("krTitle") or row.get("kr") or "").strip()
            if not kr_title:
                continue
            manager_kr_count += 1
            progress = row.get("krProgress")
            weight = row.get("krWeight")
            suffix_parts = []
            if progress not in (None, ""):
                suffix_parts.append(f"进度 {progress}%")
            if weight not in (None, ""):
                suffix_parts.append(f"权重 {weight}")
            suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
            lines.append(f"  - KR：{kr_title}{suffix}")
        if manager_kr_count == 0:
            lines.append("- 暂无可用 KR")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _archive_kr_count(manager_payloads: list[dict[str, Any]]) -> int:
    count = 0
    for item in manager_payloads:
        rows = item.get("liveOkr", {}).get("processed", {}).get("okrRows", [])
        if not isinstance(rows, list):
            continue
        count += sum(
            1
            for row in rows
            if isinstance(row, dict) and str(row.get("level") or "").upper() == "KR"
        )
    return count


def _period_slug(period_label: str) -> str:
    normalized = period_label.strip().lower().replace(" ", "")
    normalized = normalized.replace("年", "").replace("季度", "")
    normalized = normalized.replace("第", "").replace("q", "q")
    replacements = {
        "一": "1",
        "二": "2",
        "三": "3",
        "四": "4",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"[^0-9a-z]+", "", normalized) or "current"


def _weighted_average(items: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in items)
    if total_weight <= 0:
        return None
    return sum(score * weight for score, weight in items) / total_weight


def _plain_average(items: list[DimensionScoreReview]) -> float | None:
    if not items:
        return None
    return sum(item.score for item in items) / len(items)


def _culture_coefficient(score: float) -> float:
    if score >= 100:
        return 1.2
    if score >= 90:
        return 1.1
    if score >= 80:
        return 1.05
    if score >= 70:
        return 1.0
    if score >= 50:
        return 0.95
    return 0.8


def _weight_number(value: object) -> float:
    number = _progress_number(value)
    return 0.0 if number is None else number


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


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


def _dimension_row(item: DimensionScoreReview) -> str:
    return (
        f"| {_md_cell(item.dimension)} | {_md_cell(item.required_behavior)} | "
        f"{_md_cell(item.positive_evidence)} | "
        f"{_md_cell(item.missing_or_contrary_evidence)} | {item.score:.1f} | "
        f"{_md_cell(item.next_band_evidence)} |"
    )


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _brief_cell(items: list[str], *, limit: int = 120) -> str:
    if not items:
        return "无"
    text = str(items[0]).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return _md_cell(text)


def _score_text(value: float | None) -> str:
    return "暂不形成" if value is None else f"{value:.1f}"


def _coefficient_text(value: float | None) -> str:
    return "暂不形成" if value is None else f"{value:.2f}"


def _unique_strings(items) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if str(item).strip()))


def _analysis_source_hash(live_okr: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            live_okr.get("processed"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
