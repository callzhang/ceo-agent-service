import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser

from app.external_retry import run_external
from app.okr_models import OkrReviewItem, OkrReviewPayload


class _HtmlTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


@dataclass(frozen=True)
class OkrPeriod:
    period_label: str
    period_start: str
    period_end: str


class DwsLiveOkrSource:
    def __init__(
        self,
        *,
        dws,
        command_template: list[str],
        max_attempts: int = 3,
        timeout_seconds: int = 120,
    ):
        if not command_template:
            raise ValueError("missing OKR live source command template")
        self.dws = dws
        self.command_template = command_template
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        if not user_id.strip():
            raise ValueError("missing OKR user_id")
        command = [
            part.replace("{user_id}", user_id).replace("{period_label}", period_label)
            for part in self.command_template
        ]
        payload = run_external(
            "dws okr live source",
            lambda: self.dws.run_json(
                command,
                timeout_seconds=self.timeout_seconds,
            ),
            max_attempts=self.max_attempts,
        )
        if not isinstance(payload, dict):
            raise ValueError("invalid OKR live source payload")
        return payload


class DwsAgoalApiOkrSource:
    def __init__(
        self,
        *,
        dws,
        objective_rule_id: str | None = None,
        page_size: int = 100,
        max_attempts: int = 3,
    ):
        self.dws = dws
        self.objective_rule_id = (objective_rule_id or "").strip()
        self.page_size = page_size
        self.max_attempts = max_attempts

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        if not user_id.strip():
            raise ValueError("missing OKR user_id")
        objective_rule_id = self._resolve_objective_rule_id()
        period_payload = run_external(
            "dingtalk agoal objective rule periods",
            lambda: self.dws.read_agoal_objective_rule_period_list(objective_rule_id),
            max_attempts=self.max_attempts,
        )
        period = self._select_period(period_payload, period_label)
        period_id = self._required_string(period, "periodId")
        objectives_payload = run_external(
            "dingtalk agoal user objectives",
            lambda: self.dws.read_agoal_user_objective_list(
                ding_user_id=user_id,
                objective_rule_id=objective_rule_id,
                period_ids=[period_id],
            ),
            max_attempts=self.max_attempts,
        )
        objectives = self._extract_list_payload(objectives_payload)
        objective_details = []
        objective_progresses = []
        for objective in objectives:
            objective_id = self._objective_id(objective)
            detail = run_external(
                "dingtalk agoal objective detail",
                lambda objective_id=objective_id: self.dws.read_agoal_objective_detail(
                    objective_id
                ),
                max_attempts=self.max_attempts,
            )
            progress = run_external(
                "dingtalk agoal objective progresses",
                lambda objective_id=objective_id: self.dws.read_agoal_objective_progress_list(
                    objective_id,
                    page_size=self.page_size,
                ),
                max_attempts=self.max_attempts,
            )
            objective_details.append(
                {"objectiveId": objective_id, "payload": self._unwrap_content(detail)}
            )
            objective_progresses.append(
                {
                    "objectiveId": objective_id,
                    "payload": self._unwrap_content(progress),
                }
            )
        processed = self._build_processed_payload(
            objectives=objectives,
            objective_details=objective_details,
            objective_progresses=objective_progresses,
        )
        return {
            "source": {
                "system": "叮当OKR Agoal OpenAPI",
                "api": "agoal_1.0",
                "objectiveRuleId": objective_rule_id,
            },
            "userId": user_id,
            "periodLabel": period_label,
            "period": period,
            "objectives": objectives,
            "objectiveDetails": objective_details,
            "objectiveProgresses": objective_progresses,
            "processed": processed,
        }

    def _build_processed_payload(
        self,
        *,
        objectives: list[dict],
        objective_details: list[dict],
        objective_progresses: list[dict],
    ) -> dict:
        details_by_id = {
            row["objectiveId"]: row["payload"]
            for row in objective_details
            if isinstance(row.get("payload"), dict)
        }
        progress_by_id = {
            row["objectiveId"]: self._progress_items(row["payload"])
            for row in objective_progresses
            if isinstance(row.get("payload"), dict)
        }
        processed_objectives = []
        okr_rows = []
        for objective in objectives:
            objective_id = self._objective_id(objective)
            detail = details_by_id.get(objective_id, objective)
            objective_payload = detail if isinstance(detail, dict) else objective
            objective_title = self._required_string(objective_payload, "title")
            objective_weight = self._first_present(
                objective_payload.get("weight"),
                objective.get("weight"),
            )
            objective_progress = self._first_present(
                objective_payload.get("progress"),
                objective.get("progress"),
            )
            objective_row = {
                "objectiveId": objective_id,
                "title": objective_title,
                "weight": objective_weight,
                "progress": objective_progress,
                "latestProgressText": self._latest_progress_text(objective_payload),
                "keyResults": [],
                "unscopedProgressUpdates": [],
            }
            okr_rows.append(
                {
                    "level": "O",
                    "objectiveId": objective_id,
                    "objectiveTitle": objective_title,
                    "objectiveWeight": objective_weight,
                    "objectiveProgress": objective_progress,
                    "krId": "",
                    "krTitle": "",
                    "krWeight": "",
                    "krProgress": "",
                    "krDetailsUpdatesAggregated": "",
                }
            )
            key_results = self._dict_list(objective_payload.get("keyResults"))
            progress_items = progress_by_id.get(objective_id, [])
            for key_result in key_results:
                key_result_id = self._key_result_id(key_result)
                updates, unscoped = self._updates_for_key_result(
                    progress_items=progress_items,
                    key_result_id=key_result_id,
                )
                objective_row["unscopedProgressUpdates"].extend(unscoped)
                aggregated = self._aggregate_progress_updates(updates)
                key_result_row = {
                    "keyResultId": key_result_id,
                    "title": self._required_string(key_result, "title"),
                    "weight": key_result.get("weight"),
                    "progress": key_result.get("progress"),
                    "status": key_result.get("status"),
                    "progressUpdates": updates,
                    "progressUpdatesAggregated": aggregated,
                }
                objective_row["keyResults"].append(key_result_row)
                okr_rows.append(
                    {
                        "level": "KR",
                        "objectiveId": objective_id,
                        "objectiveTitle": objective_title,
                        "objectiveWeight": objective_weight,
                        "objectiveProgress": objective_progress,
                        "krId": key_result_id,
                        "krTitle": key_result_row["title"],
                        "krWeight": key_result.get("weight"),
                        "krProgress": key_result.get("progress"),
                        "krDetailsUpdatesAggregated": aggregated,
                    }
                )
            processed_objectives.append(objective_row)
        return {"objectives": processed_objectives, "okrRows": okr_rows}

    def _progress_items(self, payload) -> list[dict]:
        if isinstance(payload, list):
            return self._require_dict_items(payload)
        if isinstance(payload, dict):
            for key in ("result", "items", "records", "list"):
                value = payload.get(key)
                if isinstance(value, list):
                    return self._require_dict_items(value)
        raise ValueError("invalid Agoal objective progress payload")

    def _updates_for_key_result(
        self,
        *,
        progress_items: list[dict],
        key_result_id: str,
    ) -> tuple[list[dict], list[dict]]:
        updates = []
        unscoped = []
        for item in progress_items:
            progress_key_results = self._dict_list(item.get("keyResults"))
            matched = [
                row
                for row in progress_key_results
                if self._key_result_id(row) == key_result_id
            ]
            if not progress_key_results:
                unscoped.append(self._progress_update_row(item, None))
                continue
            for key_result in matched:
                updates.append(self._progress_update_row(item, key_result))
        return updates, unscoped

    def _progress_update_row(
        self,
        item: dict,
        key_result: dict | None,
    ) -> dict:
        return {
            "progressId": self._required_string(item, "progressId"),
            "createdAt": self._format_timestamp(item.get("created")),
            "updatedAt": self._format_timestamp(item.get("updated")),
            "objectiveProgress": item.get("progress"),
            "htmlContent": self._clean_html(self._required_string(item, "htmlContent")),
            "keyResultProgress": key_result.get("progress") if key_result else None,
            "keyResultStatus": key_result.get("status") if key_result else None,
            "keyResultTitle": self._required_string(key_result or {}, "title"),
        }

    def _aggregate_progress_updates(self, updates: list[dict]) -> str:
        if not updates:
            return "[未撰写进度]"
        parts = []
        for item in updates:
            timestamp = item.get("updatedAt") or item.get("createdAt") or "时间未知"
            progress = item.get("keyResultProgress")
            content = item.get("htmlContent") or "未填写说明"
            parts.append(f"{timestamp} | KR进度={progress} | {content}")
        return "\n".join(parts)

    def _latest_progress_text(self, objective: dict) -> str:
        latest_progress = objective.get("latestProgress")
        if not isinstance(latest_progress, dict):
            return ""
        return self._clean_html(self._required_string(latest_progress, "htmldescription"))

    @staticmethod
    def _clean_html(value: str) -> str:
        if not value:
            return ""
        parser = _HtmlTextExtractor()
        parser.feed(value)
        text = parser.text() or value
        return " ".join(unescape(text).split())

    @staticmethod
    def _format_timestamp(value) -> str:
        if not isinstance(value, int | float):
            return ""
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()

    @staticmethod
    def _first_present(*values):
        for value in values:
            if value is not None:
                return value
        return None

    def _resolve_objective_rule_id(self) -> str:
        if self.objective_rule_id:
            return self.objective_rule_id
        rules_payload = run_external(
            "dingtalk agoal objective rules",
            self.dws.read_agoal_objective_rule_list,
            max_attempts=self.max_attempts,
        )
        rules = self._extract_list_payload(rules_payload)
        candidates = [
            rule for rule in rules if self._required_string(rule, "objectiveRuleId")
        ]
        if len(candidates) != 1:
            names = [
                f"{rule.get('objectiveRuleName', '')}:{rule.get('objectiveRuleId', '')}"
                for rule in candidates
            ]
            raise RuntimeError(
                "unable to resolve Agoal objective rule; "
                "set CEO_OKR_OBJECTIVE_RULE_ID or verify Agoal.ObjectiveRule.Read; "
                f"candidates={names}"
            )
        return self._required_string(candidates[0], "objectiveRuleId")

    def _select_period(self, payload: dict, period_label: str) -> dict:
        periods = self._extract_list_payload(payload)
        expected = self._period_key(period_label)
        matches = [
            period
            for period in periods
            if self._period_key(str(period.get("name") or "")) == expected
        ]
        if len(matches) != 1:
            names = [str(period.get("name") or "") for period in periods]
            raise RuntimeError(
                "unable to resolve Agoal OKR period; "
                f"period_label={period_label}; available={names}"
            )
        return matches[0]

    @staticmethod
    def _period_key(value: str) -> str:
        compact = value.casefold().replace(" ", "").replace("年", "").replace("第", "")
        for marker in ("季度", "季"):
            marker_index = compact.find(marker)
            if marker_index <= 0:
                continue
            quarter_text = compact[marker_index - 1]
            quarter = DwsAgoalApiOkrSource._quarter_number(quarter_text)
            if quarter:
                suffix_start = marker_index + len(marker)
                return f"{compact[:marker_index - 1]}q{quarter}{compact[suffix_start:]}"
        return compact

    @staticmethod
    def _quarter_number(value: str) -> str:
        if value.isdigit():
            return value
        return {"一": "1", "二": "2", "三": "3", "四": "4"}.get(value, "")

    @classmethod
    def _extract_list_payload(cls, payload: dict) -> list[dict]:
        content = cls._unwrap_content(payload)
        if isinstance(content, list):
            return cls._require_dict_items(content)
        if isinstance(content, dict):
            for key in ("result", "items", "records", "list"):
                value = content.get(key)
                if isinstance(value, list):
                    return cls._require_dict_items(value)
        raise ValueError("invalid Agoal API list payload")

    @staticmethod
    def _require_dict_items(items: list) -> list[dict]:
        if not all(isinstance(item, dict) for item in items):
            raise ValueError("invalid Agoal API item payload")
        return items

    @classmethod
    def _unwrap_content(cls, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("invalid Agoal API payload")
        body = payload.get("body")
        if isinstance(body, dict):
            payload = body
        response = payload.get("response")
        if isinstance(response, dict):
            response_content = response.get("content")
            if isinstance(response_content, dict):
                payload = response_content
        return payload.get("content", payload)

    @classmethod
    def _objective_id(cls, objective: dict) -> str:
        for key in ("objectiveId", "objective_id", "id"):
            value = cls._required_string(objective, key)
            if value:
                return value
        raise ValueError("Agoal objective is missing objectiveId")

    @classmethod
    def _key_result_id(cls, key_result: dict) -> str:
        for key in ("keyResultId", "key_result_id", "id"):
            value = cls._required_string(key_result, key)
            if value:
                return value
        raise ValueError("Agoal key result is missing keyResultId")

    @classmethod
    def _dict_list(cls, value) -> list[dict]:
        if value is None:
            return []
        if isinstance(value, list):
            return cls._require_dict_items(value)
        raise ValueError("invalid Agoal nested list payload")

    @staticmethod
    def _required_string(payload: dict, key: str) -> str:
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
        return ""


class UnconfiguredOkrLiveSource:
    def __init__(self, env_name: str):
        self.env_name = env_name

    def fetch_user_okr(self, *, user_id: str, period_label: str) -> dict:
        raise RuntimeError(
            "missing Dingteam OKR live source command template; "
            f"set {self.env_name} to the configured Dingteam Web/OpenAPI command"
        )


def is_okr_review_request(text: str) -> bool:
    tokens = text.strip().split()
    non_url_text = " ".join(
        token
        for token in tokens
        if not token.casefold().startswith(("http://", "https://"))
    )
    normalized = " ".join(non_url_text.split()).casefold()
    confirmation_markers = ("目标确认", "修改项目", "需要你确认")
    if any(marker in normalized for marker in confirmation_markers):
        return False
    review_markers = ("审核", "review", "看看", "打分", "评价")
    okr_markers = {"okr", "kr"}
    return any(marker in normalized for marker in review_markers) and any(
        term in okr_markers for term in _ascii_terms(normalized)
    )


def _ascii_terms(text: str) -> list[str]:
    terms: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isascii() and char.isalnum():
            current.append(char)
            continue
        if current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return terms


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
    compact_okr_source_json = compact_okr_source_for_review_prompt(okr_source_json)
    return f"""你是 CEO Agent OKR review task。

request_id: {request_id}
person_name: {person_name}
period_label: {period_label}
trigger_text: {trigger_text}

实时叮当 OKR JSON:
{compact_okr_source_json}

任务:
- 优先使用实时 JSON 中的 `processed.okrRows` 和 `processed.objectives`；这是 worker 在触发 runner 前整理好的 O/KR 层级结构。
- 逐 KR 阅读 `krDetailsUpdatesAggregated` 和 KR progress updates。
- 从 KR进度更新中抽取员工主张、完成时间、产出和指标。
- 给出员工主张信息打分。
- 使用本地文件、memory_recall、DWS 搜索和读取进行事实核实。
- 给出事实核实后打分。
- 两套分数都必须考虑超期、时差、业务影响和表述是否可衡量。
- 只输出当前 AgentEnvelope JSON，不要输出旧格式 `request_id/status/result`。
- 顶层必须包含 `kind`、`user_response`、`system_actions`、`domain_payload`、`audit`。
- `kind` 必须是 "okr_review"。
- `user_response` 使用 {{"mode":"send_reply","text":"OKR review completed.","sensitivity_kind":"internal_personnel"}}。
- `system_actions` 必须包含 {{"type":"persist_okr_review","request_id":{request_id}}}。
- `domain_payload` 必须符合 OkrReviewPayload：包含 `person_name`、`period_label`、`summary`、`items`。
- `domain_payload.items` 必须是数组。每个 item 必须对应一个 KR，不要把 O 行当作单独评分项。
- 每个 item 必须包含这些 snake_case 字段：
  `objective_title`, `objective_weight`, `kr_title`, `kr_weight`,
  `self_progress`, `kr_progress_update`, `claim_text`,
  `claim_completion_time`, `deadline`, `claim_base_score`,
  `claim_discount_factor`, `claim_discount_reason`, `claim_score`,
  `verified_completion_time`, `verified_base_score`,
  `verified_discount_factor`, `verified_discount_reason`, `verified_score`,
  `evidence_used`, `evidence_gap`, `review_comment`, `suggested_follow_up`。
- `objective_weight` 和 `kr_weight` 用 0-1 小数；如果实时 JSON 是 30，输出 0.3。
- `evidence_used` 是数组，每项包含 `source` 和 `summary`。
"""


def compact_okr_source_for_review_prompt(okr_source_json: str) -> str:
    payload = json.loads(okr_source_json)
    if not isinstance(payload, dict):
        raise ValueError("OKR source JSON must be an object")
    processed = payload.get("processed")
    if not isinstance(processed, dict):
        raise ValueError("OKR source JSON must contain processed")
    if not isinstance(processed.get("objectives"), list):
        raise ValueError("OKR source JSON must contain processed.objectives")
    if not isinstance(processed.get("okrRows"), list):
        raise ValueError("OKR source JSON must contain processed.okrRows")
    compact = {
        "source": payload.get("source", {}),
        "userId": payload.get("userId", ""),
        "periodLabel": payload.get("periodLabel", ""),
        "period": payload.get("period", {}),
        "processed": processed,
    }
    return json.dumps(compact, ensure_ascii=False)


def render_okr_review_reply(payload: OkrReviewPayload) -> str:
    lines = [f"{payload.person_name} {payload.period_label} OKR 审核", payload.summary]
    for index, item in enumerate(payload.items, start=1):
        lines.extend(
            [
                "",
                f"KR {index}: {item.kr_title}",
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
    payload = OkrReviewPayload.model_validate(
        normalize_okr_review_domain_payload(run.envelope.domain_payload)
    )
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


def normalize_okr_review_domain_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    summary = normalized.get("summary")
    if isinstance(summary, dict):
        text = str(
            summary.get("overall_comment")
            or summary.get("overall_assessment")
            or summary.get("summary")
            or ""
        ).strip()
        gaps = summary.get("key_gaps")
        if isinstance(gaps, list) and gaps:
            text = (
                text + "\n\n关键差距：\n" + "\n".join(f"- {gap}" for gap in gaps)
            ).strip()
        normalized["summary"] = text or json.dumps(summary, ensure_ascii=False)
    items = normalized.get("items")
    if isinstance(items, dict):
        items = _flatten_okr_review_items(items)
    if isinstance(items, list):
        normalized["items"] = [_normalize_okr_review_item(item) for item in items]
    return normalized


def _flatten_okr_review_items(items: dict) -> list:
    flattened = []
    for value in items.values():
        if isinstance(value, list):
            flattened.extend(value)
            continue
        if isinstance(value, dict):
            if _looks_like_okr_review_item(value):
                flattened.append(value)
            else:
                flattened.extend(_flatten_okr_review_items(value))
    return flattened


def _looks_like_okr_review_item(value: dict) -> bool:
    item_keys = {
        "objective",
        "objective_title",
        "objectiveTitle",
        "kr",
        "kr_title",
        "krTitle",
        "claim_score",
        "claimScore",
        "verified_score",
        "verifiedScore",
        "final_score",
        "finalScore",
    }
    return any(key in value for key in item_keys)


def _normalize_okr_review_item(item: object) -> object:
    if not isinstance(item, dict):
        return item
    if set(OkrReviewItem.model_fields).issubset(item):
        return item
    verified_base_score = _number(
        _first_present_value(
            item,
            (
                "fact_checked_base_score",
                "factCheckedBaseScore",
                "verified_base_score",
                "verifiedBaseScore",
                "base_score",
                "baseScore",
            ),
        )
    )
    verified_score = _number(
        _first_present_value(
            item,
            (
                "fact_checked_final_score",
                "factCheckedFinalScore",
                "fact_checked_score",
                "factCheckedScore",
                "verified_score",
                "verifiedScore",
                "final_score",
                "finalScore",
            ),
        )
    )
    # Some agent runs emit only the final verified score and omit the base; treat
    # the final as the base (no time discount) so it does not render as "基础 0".
    if verified_base_score <= 0 and verified_score > 0:
        verified_base_score = verified_score
    verified_discount_factor = 1.0
    if verified_base_score > 0 and verified_score <= verified_base_score:
        verified_discount_factor = max(0.3, min(1.0, verified_score / verified_base_score))
    evidence_used = _first_present_value(
        item,
        ("evidence_used", "evidenceUsed", "verification_evidence", "verificationEvidence"),
    )
    if isinstance(evidence_used, list):
        normalized_evidence = [
            _normalize_okr_evidence(evidence)
            for evidence in evidence_used
        ]
    else:
        normalized_evidence = []
    employee_claims = _first_present_value(
        item,
        ("employee_claims", "employeeClaims", "claim_text", "claimText"),
    )
    if isinstance(employee_claims, list):
        claim_text = "；".join(str(claim) for claim in employee_claims)
    else:
        claim_text = str(employee_claims or "")
    claim_score = _number(
        _first_present_value(
            item,
            (
                "employee_claim_score",
                "employeeClaimScore",
                "claim_base_score",
                "claimBaseScore",
                "claim_score",
                "claimScore",
            ),
        )
    )
    kr_progress_update = str(
        _first_present_value(
            item,
            (
                "kr_progress_update",
                "krProgressUpdate",
                "kr_progress_notes",
                "krProgressNotes",
                "krDetailsUpdatesAggregated",
                "progressUpdatesAggregated",
            ),
        )
        or ""
    )
    if not claim_text:
        claim_text = kr_progress_update
    return {
        **item,
        "objective_title": str(
            _first_present_value(
                item,
                ("objective", "objective_title", "objectiveTitle", "objective_name", "objectiveName"),
            )
            or ""
        ),
        "objective_weight": _normalize_weight(
            _first_present_value(item, ("objective_weight", "objectiveWeight")),
            default=1.0,
        ),
        "kr_title": str(
            _first_present_value(item, ("kr", "kr_title", "krTitle", "key_result", "keyResult"))
            or ""
        ),
        "kr_weight": _normalize_weight(
            _first_present_value(item, ("kr_weight", "krWeight", "key_result_weight", "keyResultWeight")),
            default=1.0,
        ),
        "self_progress": str(
            _first_present_value(item, ("self_progress", "selfProgress", "krProgress", "progress"))
            or ""
        ),
        "kr_progress_update": kr_progress_update,
        "claim_text": claim_text,
        "claim_completion_time": str(
            _first_present_value(
                item,
                (
                    "claim_completion_time",
                    "claimCompletionTime",
                    "actual_completion_time",
                    "actualCompletionTime",
                    "completed_at",
                    "completedAt",
                ),
            )
            or ""
        ),
        "deadline": str(
            _first_present_value(item, ("deadline", "krDeadline", "due_date", "dueDate"))
            or ""
        ),
        "claim_base_score": claim_score,
        "claim_discount_factor": _normalize_discount(
            _first_present_value(item, ("claim_discount_factor", "claimDiscountFactor")),
            default=1.0,
        ),
        "claim_discount_reason": str(
            _first_present_value(item, ("claim_discount_reason", "claimDiscountReason"))
            or "按员工自述原始评分。"
        ),
        "claim_score": claim_score,
        "verified_completion_time": str(
            _first_present_value(
                item,
                (
                    "verified_completion_time",
                    "verifiedCompletionTime",
                    "actual_completion_time",
                    "actualCompletionTime",
                ),
            )
            or ""
        ),
        "verified_base_score": verified_base_score,
        "verified_discount_factor": _normalize_discount(
            _first_present_value(
                item,
                ("verified_discount_factor", "verifiedDiscountFactor"),
            ),
            default=verified_discount_factor,
        ),
        "verified_discount_reason": str(
            _first_present_value(
                item,
                ("verified_discount_reason", "verifiedDiscountReason", "time_discount", "timeDiscount"),
            )
            or "按事实核实结果计分。"
        ),
        "verified_score": verified_score,
        "evidence_used": normalized_evidence,
        "evidence_gap": str(
            _first_present_value(item, ("evidence_gap", "evidenceGap", "evidence_gaps", "evidenceGaps"))
            or ""
        ),
        "review_comment": str(
            _first_present_value(item, ("review_comment", "reviewComment", "ceo_comment", "ceoComment"))
            or ""
        ),
        "suggested_follow_up": str(
            _first_present_value(item, ("suggested_follow_up", "suggestedFollowUp"))
            or ""
        ),
    }


def _normalize_okr_evidence(evidence: object) -> dict:
    if not isinstance(evidence, dict):
        return {"source": "agent evidence", "summary": str(evidence)}
    source = _first_present_value(evidence, ("source", "title", "name", "url"))
    summary = _first_present_value(evidence, ("summary", "text", "content", "description"))
    return {
        "source": str(source or "agent evidence"),
        "summary": str(summary or source or "evidence provided"),
    }


def _normalize_discount(value: object, *, default: float) -> float:
    number = _number(value, default=default)
    return max(0.3, min(1.0, number))


def _normalize_weight(value: object, *, default: float) -> float:
    number = _number(value, default=default)
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))


def _first_present_value(payload: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return default
    return default
