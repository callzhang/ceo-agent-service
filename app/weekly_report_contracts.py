"""Existing weekly-report JSON shapes exposed through native tool parameters.

The installed Skill retains business validation. Optional keys leave missing
section checks there; extra fields and values pass through without projection.
"""

from typing import Any

from pydantic import ConfigDict, with_config
from typing_extensions import TypedDict


@with_config(ConfigDict(strict=True, extra="allow"))
class ReportObject(TypedDict, total=False):
    pass


class ReportEvidence(ReportObject, total=False):
    source_kind: Any
    source_id: Any
    speaker_is_derek: Any


class ReportClaim(ReportObject, total=False):
    type: Any
    evidence: list[ReportEvidence] | None
    counterevidence: Any


class ReportMetric(ReportObject, total=False):
    key: Any
    current: Any
    target: Any
    previous: Any
    change: Any
    status: Any
    source: Any
    data_date: Any
    definition: Any
    decision_critical: Any
    responsible_user_id: Any
    responsible_name: Any
    next_checkpoint: Any


class ReportIssue(ReportObject, total=False):
    id: str
    status: Any
    deadline: Any
    as_of: Any
    severity: Any
    closure_evidence: Any
    weeks_without_gate_movement: Any
    weeks_without_evidence: Any
    management_decision: Any


class ReportJudgment(ReportObject, total=False):
    status: str
    text: str


class ReportRootCause(ReportObject, total=False):
    text: str


class BusinessLineReport(ReportObject, total=False):
    summary: Any
    core_metrics: Any
    milestone_deviation: Any
    largest_risk: Any
    management_decision: Any
    metrics: list[dict[str, Any]]
    milestones: list[dict[str, Any]]
    full_report_jsonml: list[list[Any]]


class WeeklyReport(ReportObject, total=False):
    as_of: Any
    ceo_judgment: list[ReportJudgment]
    company_metrics: list[ReportMetric]
    claims: list[ReportClaim]
    issues: list[ReportIssue]
    cockpit: list[str]
    business_lines: dict[str, BusinessLineReport]
    root_causes: list[ReportRootCause]
    discussion: list[str]
    private: dict[str, Any]


class ReportDocumentIdentity(ReportObject, total=False):
    title: Any
    node_id: Any
    revision: Any


class MinutesCoverage(ReportObject, total=False):
    inventory_complete: Any
    relevant_ids: list[Any]
    full_transcript_ids: list[Any]


class MessagesCoverage(ReportObject, total=False):
    inventory_complete: Any
    selected_ids: list[Any]
    context_read_ids: list[Any]


class WeeklyReportManifest(ReportObject, total=False):
    target: ReportDocumentIdentity
    previous: ReportDocumentIdentity
    minutes: MinutesCoverage
    messages: MessagesCoverage
    business_reports: dict[str, Any]
