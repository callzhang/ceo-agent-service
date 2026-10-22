from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


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
    model_config = ConfigDict(extra="forbid")
    _user_id: str = PrivateAttr(default="")

    @property
    def user_id(self) -> str:
        return self._user_id

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


@dataclass(frozen=True)
class MemberGap:
    user_id: str
    stage: Literal["collection", "analysis"]
    kind: Literal["goals_missing", "source_failed", "analysis_failed"]
    evidence: str
    diagnostic: str = ""


class WeeklyOkrAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: str
    company_progress: list[str] = Field(default_factory=list)
    ceo_attention_items: list[CeoAttentionItem] = Field(default_factory=list)
    manager_reviews: list[ManagerReportAnalysis]
    source_coverage: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Only orchestration creates gaps; model output and cached reviews cannot.
    _member_gaps: tuple[MemberGap, ...] = PrivateAttr(default=())

    @property
    def member_gaps(self) -> tuple[MemberGap, ...]:
        return self._member_gaps


@dataclass(frozen=True)
class ManagerIdentity:
    name: str
    title: str
    user_id: str
    open_dingtalk_id: str


@dataclass(frozen=True)
class PublishedDocument:
    node_id: str
    url: str


@dataclass(frozen=True)
class ManagerScorecard:
    business_score: float | None
    leadership_score: float | None
    culture_score: float | None
    culture_coefficient: float | None
    final_score: float | None
    final_status: str


def _validate_roster_ids(managers: list[ManagerIdentity]) -> None:
    ids = [manager.user_id for manager in managers]
    if any(not user_id.strip() for user_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("weekly OKR roster has missing or duplicate user IDs")


def _validate_manager_coverage(
    analysis: WeeklyOkrAnalysis,
    managers: list[ManagerIdentity],
) -> None:
    """Reject rows the model invented, duplicated, or dropped."""
    _validate_roster_ids(managers)
    expected = {manager.user_id for manager in managers}
    names_by_id = {manager.user_id: manager.name for manager in managers}
    for review in analysis.manager_reviews:
        if not review.user_id:
            candidates = [
                member.user_id for member in managers if member.name == review.name
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "weekly OKR analysis manager coverage mismatch: unbound review identity"
                )
            review._user_id = candidates[0]
        if names_by_id.get(review.user_id) != review.name:
            raise ValueError(
                "weekly OKR analysis manager coverage mismatch: review identity"
            )
    actual = [review.user_id for review in analysis.manager_reviews]
    gap_ids = [gap.user_id for gap in analysis.member_gaps]
    if len(gap_ids) != len(set(gap_ids)) or set(gap_ids) - set(names_by_id):
        raise ValueError("weekly OKR analysis has duplicate or unknown member gaps")
    if any(
        not gap.evidence
        or (gap.stage, gap.kind)
        not in {
            ("collection", "goals_missing"),
            ("collection", "source_failed"),
            ("analysis", "analysis_failed"),
        }
        for gap in analysis.member_gaps
    ):
        raise ValueError("weekly OKR analysis has invalid member gap evidence")
    actual.extend(gap_ids)
    if len(actual) != len(set(actual)):
        raise ValueError("weekly OKR analysis contains duplicate manager rows")
    extra = sorted(set(actual) - expected)
    missing = sorted(expected - set(actual))
    if extra or missing:
        raise ValueError(
            f"weekly OKR analysis manager coverage mismatch: missing={missing}, extra={extra}"
        )


def _validate_kr_coverage(
    analysis: WeeklyOkrAnalysis,
    manager_payloads: list[dict[str, Any]],
) -> None:
    payloads = {item["manager"]["userId"]: item for item in manager_payloads}
    for review in analysis.manager_reviews:
        expected_rows = _live_kr_rows(
            manager_payloads, _review_user_id(review, manager_payloads)
        )
        _bind_kr_reviews_to_live_rows(review, expected_rows)
        actual_ids = [item.kr_id for item in review.kr_reviews]
        if len(actual_ids) != len(set(actual_ids)):
            raise ValueError(
                f"weekly OKR analysis contains duplicate KRs for {review.name}"
            )
        if set(actual_ids) != set(expected_rows):
            missing = sorted(set(expected_rows) - set(actual_ids))
            extra = sorted(set(actual_ids) - set(expected_rows))
            raise ValueError(
                f"weekly OKR KR coverage mismatch for {review.name}: "
                f"missing={missing}, extra={extra}"
            )
        for item in review.kr_reviews:
            if item.score > item.base_score:
                raise ValueError(
                    f"time-discounted score exceeds base score for KR {item.kr_id}"
                )
        if len(review.culture_dimensions) != 3:
            raise ValueError(
                f"culture scoring must contain three dimensions for {review.name}"
            )
        if review.role_level in {"经理", "总监", "VP", "CXO"}:
            if len(review.leadership_dimensions) != 4:
                raise ValueError(
                    f"leadership scoring must contain four dimensions for {review.name}"
                )
        elif len(review.leadership_dimensions) not in {0, 4}:
            raise ValueError(
                f"candidate leadership scoring must contain zero or four dimensions for {review.name}"
            )
        if review.user_id not in payloads:
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
        if item.kr_id in expected_rows and item.kr_id not in resolved_ids.values():
            resolved_ids[index] = item.kr_id
            continue
        key = (
            _normalized_title(item.objective_title),
            _normalized_title(item.kr_title),
        )
        matches = ids_by_titles.get(key, [])
        if len(matches) == 1:
            resolved_ids[index] = matches[0]
    if len(set(resolved_ids.values())) != len(resolved_ids):
        raise ValueError(
            f"weekly OKR analysis contains duplicate KR titles for {review.name}"
        )
    if len(expected_ids) > 1 and not resolved_ids:
        # The prompt requires the model to preserve the live KR order, but
        # models sometimes paraphrase every title and omit the opaque IDs.
        # With an exact row count and no usable anchor, bind by that required
        # source order instead of retrying the same semantically valid review
        # forever. Any count, duplicate, or order violation is still rejected
        # by the checks above and below.
        if all(_normalized_title(item.kr_title) for item in review.kr_reviews):
            resolved_ids = {
                index: expected_ids[index] for index in range(len(expected_ids))
            }
        else:
            raise ValueError(
                f"weekly OKR analysis has no title anchors for {review.name}"
            )
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
        live_rows = _live_kr_rows(
            manager_payloads, _review_user_id(review, manager_payloads)
        )
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
        cards[review.user_id] = ManagerScorecard(
            business_score=_rounded(business_score),
            leadership_score=_rounded(leadership_score),
            culture_score=_rounded(culture_score),
            culture_coefficient=coefficient,
            final_score=_rounded(final_score),
            final_status=status,
        )
    return cards


def _review_user_id(
    review: ManagerReportAnalysis, manager_payloads: list[dict[str, Any]]
) -> str:
    identities = [item["manager"] for item in manager_payloads]
    if review.user_id:
        matches = [
            identity for identity in identities if identity["userId"] == review.user_id
        ]
    else:
        matches = [
            identity for identity in identities if identity["name"] == review.name
        ]
    if len(matches) != 1 or matches[0]["name"] != review.name:
        raise ValueError(f"unresolved live OKR identity for {review.name}")
    review._user_id = matches[0]["userId"]
    return review.user_id


def _live_kr_rows(
    manager_payloads: list[dict[str, Any]],
    manager_user_id: str,
) -> dict[str, dict[str, Any]]:
    for item in manager_payloads:
        if item.get("manager", {}).get("userId") != manager_user_id:
            continue
        rows = item.get("liveOkr", {}).get("processed", {}).get("okrRows", [])
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("level") or "").upper() != "KR":
                continue
            kr_id = str(row.get("krId") or "").strip()
            if not kr_id:
                raise ValueError(
                    f"live OKR KR row for {manager_user_id} is missing krId"
                )
            if kr_id in result:
                raise ValueError(
                    f"live OKR contains duplicate KR {kr_id} for {manager_user_id}"
                )
            result[kr_id] = row
        return result
    raise ValueError(f"missing live OKR payload for {manager_user_id}")


def render_weekly_okr_report(
    *,
    title: str,
    period_label: str,
    analysis: WeeklyOkrAnalysis,
    managers: list[ManagerIdentity],
    manager_payloads: list[dict[str, Any]],
) -> str:
    _validate_manager_coverage(analysis, managers)
    stats = {
        item["manager"]["userId"]: _okr_stats(item["liveOkr"])
        for item in manager_payloads
        if "liveOkr" in item
    }
    reviews = {review.user_id: review for review in analysis.manager_reviews}
    gaps = {gap.user_id: gap for gap in analysis.member_gaps}
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
            f"- **{item.topic}**（{'、'.join(item.owner_names) or '责任人待明确'}）："
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
        label = _member_label(manager, managers)
        if manager.user_id in gaps:
            gap = gaps[manager.user_id]
            lines.append(
                f"| {_md_cell(label)} | 暂不形成 / {gap.kind} | — | "
                f"{_md_cell(gap.evidence)} | — |"
            )
            continue
        review = reviews[manager.user_id]
        scorecard = scorecards[manager.user_id]
        lines.append(
            f"| {_md_cell(label)} | "
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
        label = _member_label(manager, managers)
        if manager.user_id in gaps:
            lines.append(
                f"| {_md_cell(label)} | {_md_cell(manager.title)} | — | — | — | — | — | "
                f"{gaps[manager.user_id].kind} |"
            )
            continue
        review = reviews[manager.user_id]
        scorecard = scorecards[manager.user_id]
        lines.append(
            f"| {_md_cell(label)} | {_md_cell(review.role_level)} | "
            f"{_score_text(scorecard.business_score)} | "
            f"{_score_text(scorecard.leadership_score)} | "
            f"{_score_text(scorecard.culture_score)} | "
            f"{_coefficient_text(scorecard.culture_coefficient)} | "
            f"{_score_text(scorecard.final_score)} | {_md_cell(scorecard.final_status)} |"
        )

    lines.extend(["", "## 附录：逐人证据与逐 KR 评分", ""])
    for manager in managers:
        label = _member_label(manager, managers)
        if manager.user_id in gaps:
            gap = gaps[manager.user_id]
            lines.extend(
                [
                    f"### {label}｜{manager.title}",
                    "",
                    f"- 成员 ID：{manager.user_id}",
                    f"- 状态：{gap.kind}；暂不形成评分，不按零分处理。",
                    f"- 阶段：{gap.stage}",
                    f"- 证据：{gap.evidence}",
                    "",
                ]
            )
            continue
        review = reviews[manager.user_id]
        manager_stats = stats[manager.user_id]
        scorecard = scorecards[manager.user_id]
        lines.extend(
            [
                f"### {label}｜{manager.title}",
                "",
                f"- 成员 ID：{manager.user_id}",
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
        row_by_id = _live_kr_rows(manager_payloads, manager.user_id)
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
    finalized = [
        item.final_score for item in scorecards.values() if item.final_score is not None
    ]
    pending = manager_count - len(finalized)
    lines = [
        f"## {title}",
        "",
        "已按综合证据口径更新：系统进度仅作线索，结合评论/进展和独立证据逐 KR 评分。",
        f"评分概览：{manager_count} 人中 {len(finalized)} 人形成最终分，{pending} 人暂不形成最终分"
        + (
            f"；已形成最终分的平均值为 {sum(finalized) / len(finalized):.1f}。"
            if finalized
            else "。"
        ),
    ]
    names = {item["manager"]["userId"]: item["manager"]["name"] for item in manager_payloads}
    pending_reasons = [
        f"- {names[gap.user_id]} [{gap.user_id}]：{gap.kind}；{gap.evidence}"
        for gap in analysis.member_gaps
    ]
    pending_reasons.extend(
        f"- {names[user_id]} [{user_id}]：{card.final_status}"
        for user_id, card in scorecards.items() if card.final_score is None
    )
    if pending_reasons:
        lines.extend(["", "暂不形成评分的具体原因：", *pending_reasons])
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
    lines.extend(["", f"完整周报：[打开完整周报]({document_url})"])
    return "\n".join(lines)


def _member_label(manager: ManagerIdentity, managers: list[ManagerIdentity]) -> str:
    if sum(member.name == manager.name for member in managers) == 1:
        return manager.name
    return f"{manager.name} [{manager.user_id}]"


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
        label = _member_label(manager, managers)
        start_marker = f"### {label}｜"
        start = manager_details.find(start_marker)
        if start < 0:
            raise ValueError(
                f"weekly OKR report is missing appendix for {manager.name}"
            )
        if index + 1 < len(managers):
            next_marker = f"### {_member_label(managers[index + 1], managers)}｜"
            end = manager_details.find(next_marker, start + len(start_marker))
            if end < 0:
                raise ValueError(
                    f"weekly OKR report is missing appendix boundary for {manager.name}"
                )
        else:
            end = len(manager_details)
        section = manager_details[start:end].strip()
        appendices[manager.user_id] = (
            f"# {title}｜评分附录｜{label}\n\n"
            f"- OKR 周期：{period_label}\n"
            "- 评分口径：综合系统评论/进展、独立证据、实际效果和完成时间；系统进度仅作为线索\n\n"
            f"{section}\n\n"
            f"## 附录校验：{label}\n"
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
        label = f"{_member_label(manager, managers)}｜{manager.title}"
        document = appendix_documents.get(manager.user_id)
        if document is None:
            lines.append(f"- {label}（发布时生成线上链接）")
        else:
            lines.append(f"- [{label}]({document.url})")
    lines.extend(["", limits_section.rstrip(), ""])
    return "\n".join(lines)


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
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


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


def _member_key(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _roster_members(roster: dict[str, Any]) -> list[ManagerIdentity]:
    managers = [ManagerIdentity(**item) for item in roster["managers"]]
    _validate_roster_ids(managers)
    if not managers:
        raise ValueError("OKR report roster is empty")
    return managers


def _member_schema(kr_count: int, subject_label: str, subject_name: str) -> dict[str, Any]:
    schema = _read_json(Path(__file__).with_name("analysis.schema.json"))
    managers = schema["properties"]["manager_reviews"]
    managers.update(minItems=1, maxItems=1)
    properties = managers["items"]["properties"]
    properties["name"]["const"] = subject_name
    schema["properties"]["ceo_attention_items"]["items"]["properties"]["owner_names"]["description"] = (
        f"This analysis concerns {subject_label}. When this subject owns an item, "
        f"use the exact label {subject_label!r}; do not drop its stable identity. "
        "Other owners require independently sourced, unambiguous identities."
    )
    properties["kr_reviews"].update(minItems=kr_count, maxItems=kr_count)
    culture = json.loads(json.dumps(schema["$defs"]["dimensions"]))
    culture.update(minItems=3, maxItems=3)
    properties["culture_dimensions"] = culture
    return schema


def _member_source_digest(item, period_label, score_end):
    raw = json.dumps({"member": item, "period_label": period_label, "score_end": score_end},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def prepare(
    *,
    roster_file: Path,
    members_dir: Path,
    period_label: str,
    report_date: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Build exact analysis inputs from separately completed realtime reads."""
    from datetime import date, timedelta

    roster = _read_json(roster_file)
    managers = _roster_members(roster)
    year_text, quarter_text = period_label.split()
    year, quarter = int(year_text), int(quarter_text.removeprefix("Q"))
    if quarter_text != f"Q{quarter}" or quarter not in range(1, 5):
        raise ValueError("period-label must be YYYY Q1, Q2, Q3 or Q4")
    end = date.fromisoformat(report_date)
    start = date(year, 3 * quarter - 2, 1)
    if end < start:
        raise ValueError("report date precedes the scoring period")
    expected = {manager.user_id: manager for manager in managers}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(members_dir.glob("okr-member-*.json")):
        item = _read_json(path)
        if (
            not isinstance(item, dict)
            or "manager" not in item
            or "sourceOutcome" not in item
        ):
            continue
        identity = item["manager"]
        user_id = identity.get("userId")
        if user_id not in expected:
            continue
        if user_id in found:
            raise ValueError(f"duplicate source artifacts for member {user_id}")
        member = expected[user_id]
        if identity.get("name") != member.name or identity.get("title") != member.title:
            raise ValueError(f"source artifact identity mismatch for {user_id}")
        outcome = item["sourceOutcome"]
        if outcome.get("user_id") != user_id or outcome.get("stage") != "collection":
            raise ValueError(f"source outcome identity mismatch for {user_id}")
        if outcome.get("kind") == "source_failed" and outcome.get("scope") != "member":
            raise ValueError(
                f"shared source collection failed for {user_id}: {outcome.get('diagnostic', '')}"
            )
        if outcome.get("kind") not in {"collected", "goals_missing", "source_failed"}:
            raise ValueError(f"invalid source outcome for {user_id}")
        receipt = item.get("liveOkr", item.get("availabilityReceipt", {}))
        if receipt and (
            receipt.get("userId") != user_id
            or receipt.get("periodLabel") != period_label
        ):
            raise ValueError(f"source period or user identity mismatch for {user_id}")
        if outcome["kind"] == "collected" and "liveOkr" not in item:
            raise ValueError(f"collected source has no live payload for {user_id}")
        found[user_id] = item
    missing = set(expected) - set(found)
    if missing:
        raise ValueError(f"missing member source artifacts: {sorted(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "periodLabel": period_label,
        "scoreStart": start.isoformat(),
        "scoreEnd": end.isoformat(),
        "currentWeekStart": (end - timedelta(days=6)).isoformat(),
        "currentWeekEnd": end.isoformat(),
        "group": roster["name"],
        "groupConversationId": roster["conversation_id"],
        "roster": roster["managers"],
        "managers": [found[member.user_id] for member in managers],
    }
    source_file = output_dir / "source.json"
    _write_json(source_file, payload)
    plan = []
    for member in managers:
        item = found[member.user_id]
        key = _member_key(member.user_id)
        entry = {
            "user_id": member.user_id,
            "name": member.name,
            "subject_label": f"{member.name} [{member.user_id}]",
            "key": key,
            "outcome": item["sourceOutcome"]["kind"],
            "source_sha256": _member_source_digest(item, period_label, end.isoformat()),
        }
        if entry["outcome"] == "collected":
            manager_source = output_dir / f"source-{key}.json"
            schema_file = output_dir / f"schema-{key}.json"
            _write_json(
                manager_source,
                {**payload, "managers": [item], "roster": [asdict(member)]},
            )
            _write_json(
                schema_file, _member_schema(len(_live_kr_rows([item], member.user_id)),
                                            entry["subject_label"], member.name)
            )
            entry.update(
                source_file=str(manager_source.resolve()),
                schema_file=str(schema_file.resolve()),
                analysis_file=f"analysis-{key}.json",
            )
            analysis_file = output_dir / entry["analysis_file"]
            existing_analysis = (
                _read_json(analysis_file) if analysis_file.is_file() else None
            )
            entry["analysis_reusable"] = (
                isinstance(existing_analysis, dict)
                and "analysis_failure" not in existing_analysis
                and existing_analysis.get("_source_sha256") == entry["source_sha256"]
            )
        plan.append(entry)
    result = {
        "source_file": str(source_file.resolve()),
        "period_label": period_label,
        "score_start": start.isoformat(),
        "score_end": end.isoformat(),
        "managers": plan,
    }
    _write_json(output_dir / "plan.json", result)
    return result


def _analysis_from_files(
    source: dict[str, Any], analysis_dir: Path
) -> tuple[WeeklyOkrAnalysis, list[ManagerIdentity]]:
    managers = _roster_members({"managers": source["roster"]})
    _validate_roster_ids(managers)
    payloads = source["managers"]
    ids = [item["manager"]["userId"] for item in payloads]
    if len(set(ids)) != len(ids) or set(ids) != {member.user_id for member in managers}:
        raise ValueError("source roster coverage mismatch")
    by_id = {item["manager"]["userId"]: item for item in payloads}
    results: list[WeeklyOkrAnalysis] = []
    gaps: list[MemberGap] = []
    for member in managers:
        item = by_id[member.user_id]
        if item["manager"]["name"] != member.name:
            raise ValueError("source roster name mismatch")
        outcome = item["sourceOutcome"]
        if outcome["user_id"] != member.user_id:
            raise ValueError("source outcome identity mismatch")
        if outcome["kind"] != "collected":
            if outcome["kind"] == "source_failed" and outcome.get("scope") != "member":
                raise ValueError("shared source collection failed")
            gaps.append(
                MemberGap(
                    **{
                        key: outcome.get(key, "")
                        for key in (
                            "user_id",
                            "stage",
                            "kind",
                            "evidence",
                            "diagnostic",
                        )
                    }
                )
            )
            continue
        analysis_file = analysis_dir / f"analysis-{_member_key(member.user_id)}.json"
        raw = _read_json(analysis_file)
        expected_digest = _member_source_digest(item, source["periodLabel"], source["scoreEnd"])
        if not isinstance(raw, dict) or raw.pop("_source_sha256", None) != expected_digest:
            raise ValueError(f"analysis source changed for {member.user_id}; reanalyze current source")
        if set(raw) == {"analysis_failure"}:
            failure = raw["analysis_failure"]
            if (
                not isinstance(failure, dict)
                or set(failure) != {"diagnostic"}
                or not isinstance(failure["diagnostic"], str)
                or not failure["diagnostic"].strip()
            ):
                raise ValueError("analysis failure requires a concrete diagnostic")
            gaps.append(
                MemberGap(
                    member.user_id,
                    "analysis",
                    "analysis_failed",
                    f"{source['periodLabel']} 逐 KR 分析未完成，暂不形成评分（代码 analysis_failed）。",
                    failure["diagnostic"],
                )
            )
            continue
        result = WeeklyOkrAnalysis.model_validate(raw)
        if (
            len(result.manager_reviews) != 1
            or result.manager_reviews[0].name != member.name
        ):
            raise ValueError(f"exactly one review for {member.name} is required")
        result.manager_reviews[0]._user_id = member.user_id
        _validate_manager_coverage(result, [member])
        _validate_kr_coverage(result, [item])
        results.append(result)
    analysis = WeeklyOkrAnalysis(
        executive_summary=(
            f"已完成 {len(results)} / {len(managers)} 位 CEO-2 成员的逐 KR 综合证据评分；系统进度仅作为线索，最终判断以评论/进展、独立证据、实际效果和完成时间为准；未完成评分的成员保留缺口区块，不形成分数。"
        ),
        company_progress=_unique_strings(
            text for result in results for text in result.company_progress[:1]
        ),
        ceo_attention_items=[
            item for result in results for item in result.ceo_attention_items[:1]
        ],
        manager_reviews=[result.manager_reviews[0] for result in results],
        source_coverage=_unique_strings(
            text for result in results for text in result.source_coverage
        ),
        warnings=_unique_strings(
            text for result in results for text in result.warnings
        ),
    )
    analysis._member_gaps = tuple(gaps)
    _validate_manager_coverage(analysis, managers)
    _validate_kr_coverage(analysis, payloads)
    return analysis, managers


def render(
    *,
    source_file: Path,
    analysis_dir: Path,
    output_dir: Path,
    appendix_links_file: Path | None = None,
    document_url: str = "",
) -> dict[str, Any]:
    source = _read_json(source_file)
    analysis, managers = _analysis_from_files(source, analysis_dir)
    title = f"CEO-2 管理者 OKR 进度周报（{source['periodLabel']} 至今：{source['scoreStart']}—{source['scoreEnd']}）"
    full = render_weekly_okr_report(
        title=title,
        period_label=source["periodLabel"],
        analysis=analysis,
        managers=managers,
        manager_payloads=source["managers"],
    )
    prefix, limits, appendices = _report_publication_parts(
        full, title=title, period_label=source["periodLabel"], managers=managers
    )
    links = _read_json(appendix_links_file) if appendix_links_file else {}
    if appendix_links_file and set(links) != {member.user_id for member in managers}:
        raise ValueError("appendix links must cover the exact roster")
    documents = {user_id: PublishedDocument("", url) for user_id, url in links.items()}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "full.md").write_text(full, encoding="utf-8")
    (output_dir / "main.md").write_text(
        _render_management_report(
            summary_prefix=prefix,
            limits_section=limits,
            managers=managers,
            appendix_documents=documents,
        ),
        encoding="utf-8",
    )
    paths = []
    for member in managers:
        path = output_dir / f"appendix-{_member_key(member.user_id)}.md"
        path.write_text(appendices[member.user_id], encoding="utf-8")
        paths.append(
            {
                "user_id": member.user_id,
                "title": f"{title}｜评分附录｜{_member_label(member, managers)}",
                "path": str(path.resolve()),
                "artifact_name": path.name,
            }
        )
    cards = _manager_scorecards(analysis, source["managers"])
    result = {
        "title": title,
        "group_conversation_id": source["groupConversationId"],
        "manager_count": len(managers),
        "scored_count": sum(card.final_score is not None for card in cards.values()),
        "main_file": str((output_dir / "main.md").resolve()),
        "artifact_name": "main.md",
        "full_file": str((output_dir / "full.md").resolve()),
        "appendices": paths,
    }
    if document_url:
        summary = render_group_summary(
            title=title,
            analysis=analysis,
            document_url=document_url,
            manager_count=len(managers),
            manager_payloads=source["managers"],
        )
        (output_dir / "summary.md").write_text(summary, encoding="utf-8")
        result["summary_file"] = str((output_dir / "summary.md").resolve())
    _write_json(output_dir / "manifest.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and render the CEO-2 OKR report locally"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--roster-file", required=True, type=Path)
    prep.add_argument("--members-dir", required=True, type=Path)
    prep.add_argument("--period-label", required=True)
    prep.add_argument("--report-date", required=True)
    prep.add_argument("--output-dir", required=True, type=Path)
    report = commands.add_parser("render")
    report.add_argument("--source", required=True, type=Path)
    report.add_argument("--analysis-dir", required=True, type=Path)
    report.add_argument("--output-dir", required=True, type=Path)
    report.add_argument("--appendix-links-file", type=Path)
    report.add_argument("--document-url", default="")
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "prepare":
        result = prepare(**args)
    else:
        args["source_file"] = args.pop("source")
        result = render(**args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
