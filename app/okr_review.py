import json
from dataclasses import dataclass
from datetime import date

from app.external_retry import run_external
from app.okr_models import OkrReviewPayload


@dataclass(frozen=True)
class OkrPeriod:
    period_label: str
    period_start: str
    period_end: str


class DwsLiveOkrSource:
    def __init__(self, *, dws, command_template: list[str], max_attempts: int = 3):
        if not command_template:
            raise ValueError("missing OKR live source command template")
        self.dws = dws
        self.command_template = command_template
        self.max_attempts = max_attempts

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        if not user_id.strip():
            raise ValueError("missing OKR user_id")
        command = [
            part.replace("{user_id}", user_id).replace("{period_label}", period_label)
            for part in self.command_template
        ]
        payload = run_external(
            "dws okr live source",
            lambda: self.dws.run_json(command),
            max_attempts=self.max_attempts,
        )
        if not isinstance(payload, dict):
            raise ValueError("invalid OKR live source payload")
        return payload


class UnconfiguredOkrLiveSource:
    def __init__(self, env_name: str):
        self.env_name = env_name

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        raise RuntimeError(
            "missing OKR live source command template; "
            f"set {self.env_name} to the configured DWS/OpenAPI command"
        )


def is_okr_review_request(text: str) -> bool:
    normalized = " ".join(text.strip().split()).casefold()
    review_markers = ("审核", "review", "看看", "打分", "评价")
    okr_markers = ("okr", "kr", "目标")
    return any(marker in normalized for marker in review_markers) and any(
        marker in normalized for marker in okr_markers
    )


def current_quarter_period(today: str | None = None) -> OkrPeriod:
    current = date.fromisoformat(today) if today else date.today()
    quarter = (current.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(current.year, start_month, 1)
    if end_month == 12:
        end = date(current.year, 12, 31)
    else:
        end = date(current.year, end_month + 1, 1).replace(day=1)
        end = date.fromordinal(end.toordinal() - 1)
    return OkrPeriod(
        period_label=f"{current.year} Q{quarter}",
        period_start=start.isoformat(),
        period_end=end.isoformat(),
    )


def build_okr_review_prompt(
    *,
    request_id: int,
    person_name: str,
    period_label: str,
    okr_source_json: str,
    trigger_text: str,
) -> str:
    json.loads(okr_source_json)
    return f"""你是 CEO Agent OKR review task。

request_id: {request_id}
person_name: {person_name}
period_label: {period_label}
trigger_text: {trigger_text}

实时叮当 OKR JSON:
{okr_source_json}

任务:
- 逐 KR 阅读 KR进度更新。
- 从 KR进度更新中抽取员工主张、完成时间、产出和指标。
- 给出员工主张信息打分。
- 使用本地文件、memory_recall、DWS 搜索和读取进行事实核实。
- 给出事实核实后打分。
- 两套分数都必须考虑超期、时差、业务影响和表述是否可衡量。
- 只输出 AgentEnvelope JSON，kind=okr_review，domain_payload 必须符合 OkrReviewPayload。
"""


def render_okr_review_reply(payload: OkrReviewPayload) -> str:
    lines = [f"{payload.person_name} {payload.period_label} OKR 审核", payload.summary]
    for index, item in enumerate(payload.items, start=1):
        lines.extend(
            [
                "",
                f"{index}. {item.kr_title}",
                f"- 员工主张分: {item.claim_score:g}（基础 {item.claim_base_score:g}，折扣 {item.claim_discount_factor:g}）",
                f"- 事实核实分: {item.verified_score:g}（基础 {item.verified_base_score:g}，折扣 {item.verified_discount_factor:g}）",
                f"- 依据: {'；'.join(e.summary for e in item.evidence_used) or '无独立证据'}",
                f"- 证据缺口: {item.evidence_gap}",
                f"- 建议: {item.suggested_follow_up}",
            ]
        )
    return "\n".join(lines)


def process_okr_review_request(*, store, runner, request, single_chat: bool) -> str:
    prompt = build_okr_review_prompt(
        request_id=request.id,
        person_name=request.trigger_sender,
        period_label=request.period_label,
        okr_source_json=request.okr_source_json,
        trigger_text=request.trigger_text,
    )
    run = runner.run(
        request.conversation_id,
        request.conversation_title,
        single_chat,
        prompt,
        owner=f"okr_review:{request.id}",
    )
    payload = OkrReviewPayload.model_validate(run.envelope.domain_payload)
    store.record_okr_review_run(
        request_id=request.id,
        codex_session_id=run.codex_session_id,
        codex_transcript_start_line=run.transcript_start_line,
        codex_transcript_end_line=run.transcript_end_line,
        envelope_json=run.envelope.model_dump_json(),
        audit_tool_events_json=json.dumps(run.audit_tool_events, ensure_ascii=False),
        audit_summary=run.envelope.audit.summary,
    )
    for item in payload.items:
        store.record_okr_review_item(
            request_id=request.id,
            objective_title=item.objective_title,
            objective_weight=item.objective_weight,
            kr_title=item.kr_title,
            kr_weight=item.kr_weight,
            item_json=item.model_dump_json(),
        )
    store.mark_okr_review_request_done(request.id, codex_session_id=run.codex_session_id)
    return render_okr_review_reply(payload)
