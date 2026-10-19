from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo
from app.dws_client import DwsClient, DwsError
from app.okr_review import (
    DwsLiveOkrSource,
    OkrLiveSourceError,
    _explicit_okr_quarter,
    current_quarter_period,
)

DEFAULT_GROUP_NAME = "CEO-2 管理群"
DEFAULT_ARCHIVE_DIR_NAME = "OKR档案"
LATEST_ARCHIVE_INDEX_NAME = "latest_company_okr_index.md"
LATEST_ARCHIVE_RAW_NAME = "latest_company_okr_raw.json"
WORK_TIME_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MemberGap:
    user_id: str
    stage: Literal["collection", "analysis"]
    kind: Literal["goals_missing", "source_failed", "analysis_failed"]
    evidence: str
    diagnostic: str = ""


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
class CompanyOkrArchiveResult:
    status: str
    generated_at: str
    period_label: str
    manager_count: int
    kr_count: int
    raw_path: str
    index_path: str


class WeeklyOkrGateway(Protocol):
    def resolve_group_roster(self, group_name: str) -> GroupRoster: ...


class DwsWeeklyOkrGateway:
    def __init__(self, dws: DwsClient):
        self.dws = dws

    def resolve_group_roster(self, group_name: str) -> GroupRoster:
        groups: list[dict[str, Any]] = []
        cursor = "0"
        while True:
            command = [
                self.dws.dws_bin,
                "chat",
                "+chat-search",
                "--query",
                group_name,
                "--limit",
                "100",
                "--format",
                "json",
            ]
            if cursor != "0":
                command[3:3] = ["--cursor", cursor]
            payload = self.dws.run_json(command)
            # DWS chat-search currently returns `chats` at the top level;
            # older clients returned the same records under `groups`.
            groups.extend(_nested_list(payload, "chats"))
            groups.extend(_nested_list(payload, "groups"))
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict) or not result.get("hasMore"):
                break
            cursor = str(result.get("nextCursor") or "").strip()
            if not cursor:
                raise DwsError("DingTalk group search is incomplete without nextCursor")
        exact_groups = [
            conversation
            for conversation in groups
            if (
                conversation.get("conversationName")
                or conversation.get("title")
                or conversation.get("name")
            )
            == group_name
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
                raise DwsError(
                    "DingTalk group member is missing name or openDingTalkId"
                )
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
    if not objectives and not rows and payload.get("objectiveList"):
        raise ValueError("empty processed OKR contradicts verified raw objectiveList")
    kr_ids: set[str] = set()
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
        kr_id = str(row.get("krId") or "").strip()
        if not kr_id or kr_id in kr_ids:
            raise ValueError(
                f"live OKR contains blank or duplicate KR ID for {manager.name}"
            )
        kr_ids.add(kr_id)


def _verified_missing_period_gap(
    payload: dict[str, Any], *, manager: ManagerIdentity, period_label: str
) -> MemberGap:
    """Accept only the producer's complete personal-period absence receipt."""
    if set(payload) != {"source", "userId", "periodLabel", "availability", "periods"}:
        raise ValueError("invalid personal-period absence envelope fields")
    if payload["userId"] != manager.user_id or payload["periodLabel"] != period_label:
        raise ValueError(
            "personal-period absence receipt does not match requested identity"
        )
    source = payload["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"system", "capturedAt"}
        or source["system"] != "Dingteam personal-period API"
        or not isinstance(source["capturedAt"], str)
    ):
        raise ValueError("invalid personal-period absence source authority")
    if datetime.fromisoformat(source["capturedAt"]).utcoffset() != timedelta(0):
        raise ValueError("personal-period absence capture timestamp must be UTC")
    availability = payload["availability"]
    if (
        not isinstance(availability, dict)
        or set(availability) != {"status", "providerCode", "periodsComplete"}
        or availability["status"] != "goals_not_established"
        or type(availability["providerCode"]) is not int
        or availability["providerCode"] != 0
        or availability["periodsComplete"] is not True
    ):
        raise ValueError(
            "personal-period absence is not a verified complete provider result"
        )
    periods = payload["periods"]
    if not isinstance(periods, list):
        raise ValueError("personal-period absence receipt has no period list")
    for period in periods:
        if (
            not isinstance(period, dict)
            or not isinstance(period.get("name"), str)
            or not period["name"].strip()
            or not isinstance(period.get("okrId"), (str, int))
            or isinstance(period["okrId"], bool)
            or not period["okrId"]
            or not str(period["okrId"]).strip()
        ):
            raise ValueError(
                "personal-period absence contains an invalid period identity"
            )
        if _matches_requested_period(period["name"], period_label):
            raise ValueError(
                "personal-period absence contains the requested period or an unresolved name"
            )
    return MemberGap(
        manager.user_id,
        "collection",
        "goals_missing",
        f"{period_label} 个人周期尚未建立；尚无法读取该周期目标。",
    )


def _validate_live_okr_receipt(
    payload: object, *, manager: ManagerIdentity, period_label: str
) -> None:
    """Verify the existing Dingteam live-source receipt before using its data."""
    if not isinstance(payload, dict):
        raise ValueError("live OKR receipt is not an object")
    if (
        payload.get("userId") != manager.user_id
        or payload.get("periodLabel") != period_label
    ):
        raise ValueError(
            "live OKR receipt does not match requested userId / periodLabel"
        )
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("system") != "叮当OKR Dingteam Web (direct API)"
    ):
        raise ValueError("live OKR receipt is missing source authority")
    captured_at = source.get("capturedAt")
    if not isinstance(captured_at, str) or not captured_at:
        raise ValueError("live OKR receipt is missing source.capturedAt")
    if datetime.fromisoformat(captured_at).tzinfo is None:
        raise ValueError("live OKR receipt source.capturedAt has no timezone")
    period = payload.get("period")
    periods = payload.get("periods")
    if (
        not isinstance(period, dict)
        or not isinstance(period.get("name"), str)
        or not period.get("okrId")
        or not isinstance(periods, list)
        or period not in periods
        or not _matches_requested_period(period["name"], period_label)
    ):
        raise ValueError("live OKR receipt has no verified requested period")
    objectives = payload.get("objectiveList")
    if not isinstance(objectives, list):
        raise ValueError("live OKR receipt is missing raw objectiveList")
    objective_receipt = source.get("objectiveListReceipt")
    if (
        not isinstance(objective_receipt, dict)
        or set(objective_receipt) != {"providerCode", "complete", "count"}
        or type(objective_receipt["providerCode"]) is not int
        or objective_receipt["providerCode"] != 0
        or objective_receipt["complete"] is not True
        or type(objective_receipt["count"]) is not int
        or objective_receipt["count"] != len(objectives)
    ):
        raise ValueError(
            "live OKR objective list lacks verified complete provider receipt"
        )


def _public_source_failure_code(exc: Exception) -> str:
    if (
        isinstance(exc, OkrLiveSourceError)
        and exc.code.isascii()
        and exc.code.isdecimal()
        and len(exc.code) <= 6
    ):
        return exc.code
    return "source_failed"


def _matches_requested_period(name: str, period_label: str) -> bool:
    requested = _explicit_okr_quarter(period_label)
    observed = _explicit_okr_quarter(name)
    if requested is None or requested[0] is None or observed is None:
        raise ValueError("unresolved OKR period identity")
    year, quarter = observed
    return (year or requested[0], quarter) == requested


def _validate_roster_ids(managers: list[ManagerIdentity]) -> None:
    ids = [manager.user_id for manager in managers]
    if any(not user_id.strip() for user_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("weekly OKR roster has missing or duplicate user IDs")


def refresh_company_okr_archive(
    *,
    gateway: WeeklyOkrGateway,
    source,
    workspace: Path,
    now: datetime,
    group_name: str = DEFAULT_GROUP_NAME,
    period_label: str = "",
) -> CompanyOkrArchiveResult:
    local_now = now.astimezone(WORK_TIME_ZONE)
    roster = gateway.resolve_group_roster(group_name)
    if not roster.managers:
        raise RuntimeError("CEO-2 manager roster is empty")

    resolved_period = (
        period_label.strip()
        or current_quarter_period(local_now.date().isoformat()).period_label
    )
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
            objective = str(
                row.get("objectiveTitle") or row.get("objective") or ""
            ).strip()
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


def refresh_company_okr_archive_command(
    settings,
    *,
    period_label: str = "",
    group_name: str = DEFAULT_GROUP_NAME,
    now: datetime | None = None,
) -> CompanyOkrArchiveResult:
    current = now or datetime.now(WORK_TIME_ZONE)
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


def collect_weekly_okr_member(
    *, source, manager: ManagerIdentity, period_label: str
) -> dict[str, Any]:
    """Read one member and retain the existing provider authority/outcome contract.

    A call performs source IO only. The scheduled Agent owns the report workflow,
    evidence judgment and publication. Keeping this per member bounds each read.
    """
    _validate_roster_ids([manager])
    item: dict[str, Any] = {
        "manager": {
            "name": manager.name,
            "title": manager.title,
            "userId": manager.user_id,
            "openDingtalkId": manager.open_dingtalk_id,
        }
    }
    failure_scope = "shared"
    validation_phase = "fetch"
    try:
        payload = source.fetch_user_okr(
            user_id=manager.user_id, period_label=period_label
        )
        validation_phase = "receipt"
        gap = None
        if isinstance(payload, dict) and "availability" in payload:
            gap = _verified_missing_period_gap(
                payload, manager=manager, period_label=period_label
            )
            item["availabilityReceipt"] = payload
        else:
            _validate_live_okr_receipt(
                payload, manager=manager, period_label=period_label
            )
            failure_scope = "member"
            validation_phase = "member_data"
            _validate_live_okr_payload(payload, manager=manager)
            item["liveOkr"] = payload
            if (
                not payload["processed"]["objectives"]
                and not payload["processed"]["okrRows"]
            ):
                gap = MemberGap(
                    manager.user_id,
                    "collection",
                    "goals_missing",
                    f"{period_label} 周期已建立；完整实时目标列表为空，当前尚无目标。",
                )
    except (RuntimeError, ValueError, OSError) as exc:
        if isinstance(exc, OkrLiveSourceError):
            if exc.user_id == manager.user_id and exc.period_label == period_label:
                failure_scope = exc.scope
        gap = MemberGap(
            manager.user_id,
            "collection",
            "source_failed",
            f"{period_label} 数据采集未完成，暂不形成评分（代码 {_public_source_failure_code(exc)}）。",
            f"{type(exc).__name__}: {exc}",
        )
    if gap is None:
        item["sourceOutcome"] = {
            "user_id": manager.user_id,
            "stage": "collection",
            "kind": "collected",
            "evidence": "Validated live payload",
        }
    else:
        item["sourceOutcome"] = asdict(gap)
        if gap.kind == "source_failed":
            item["sourceOutcome"].update(
                scope=failure_scope, validationPhase=validation_phase
            )
    return item


def _configured_dws(settings) -> DwsClient:
    return DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )


def weekly_okr_roster(settings, *, group_name: str) -> dict[str, Any]:
    """Resolve an explicitly selected group's current identity and contact roster."""
    roster = DwsWeeklyOkrGateway(_configured_dws(settings)).resolve_group_roster(
        group_name
    )
    _validate_roster_ids(roster.managers)
    if not roster.managers:
        raise RuntimeError("OKR source roster is empty")
    return asdict(roster)


def weekly_okr_member(
    settings,
    *,
    user_id: str,
    name: str,
    period_label: str,
    title: str = "",
    open_dingtalk_id: str = "",
) -> dict[str, Any]:
    """Fetch one explicit roster member using the configured raw live source."""
    if not user_id.strip() or not name.strip() or not period_label.strip():
        raise ValueError("OKR member user ID, name and period are required")
    command_template = shlex.split(os.getenv("CEO_OKR_LIVE_SOURCE_COMMAND", ""))
    if not command_template:
        raise RuntimeError("CEO_OKR_LIVE_SOURCE_COMMAND is required")
    source = DwsLiveOkrSource(
        dws=_configured_dws(settings),
        command_template=command_template,
        max_attempts=1,
        timeout_seconds=min(settings.task_codex_timeout_seconds, 240),
    )
    return collect_weekly_okr_member(
        source=source,
        manager=ManagerIdentity(name, title, user_id, open_dingtalk_id),
        period_label=period_label,
    )
