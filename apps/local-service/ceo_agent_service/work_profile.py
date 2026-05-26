import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ceo_agent_service.corpus import load_corpus_records


WHITESPACE_RE = re.compile(r"\s+")
LOCAL_AUTHORED_DIRS = (
    Path("Thinking"),
    Path("management") / "strategy",
    Path("management"),
    Path("business"),
    Path("product"),
)
LOCAL_TEXT_SUFFIXES = {".md", ".txt"}
LOCAL_IGNORED_PARTS = {".smart-env", ".dws", ".obsidian", "AI听记"}
HIGH_CONFIDENCE_AUTHORED_DIRS = {Path("Thinking"), Path("management") / "strategy"}
LOCAL_SENSITIVITY_TERMS = (
    (
        "internal_personnel",
        (
            "HR",
            "招聘",
            "候选人",
            "面试",
            "人事",
            "绩效",
            "转正",
            "晋升",
            "staff management",
            "staff",
            "employee",
        ),
    ),
    (
        "approval",
        (
            "OA",
            "审批",
            "报销",
            "预算",
            "合同",
            "财务",
            "invoice",
            "finance",
        ),
    ),
    (
        "customer",
        (
            "客户",
            "customer",
            "partner",
            "合作",
            "商务",
        ),
    ),
)


def evidence_id(source_type: str, location: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{source_type}\n{location}\n{text}".encode("utf-8")
    ).hexdigest()[:16]
    return f"ev_{digest}"


def safe_excerpt(text: str, limit: int = 240) -> str:
    normalized = WHITESPACE_RE.sub(" ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}…"


def classify_local_doc_sensitivity(relative: str, text: str) -> str:
    haystack = f"{relative}\n{text}"
    haystack_lower = haystack.lower()
    for sensitivity, terms in LOCAL_SENSITIVITY_TERMS:
        for term in terms:
            if term.lower() in haystack_lower:
                return sensitivity
    return "general"


class EvidenceRecord(BaseModel):
    id: str
    source_type: str
    title: str = ""
    timestamp: str = ""
    location: str = ""
    scenario: str = "general"
    evidence_strength: str = "authored_assumed"
    sensitivity: str = "general"
    excerpt: str = ""
    usable_for_profile: bool = True


class WorkProfileRule(BaseModel):
    id: str
    title: str
    category: str
    scenarios: list[str] = Field(default_factory=list)
    trigger: str
    do: str
    dont: str
    confidence: str
    evidence_ids: list[str] = Field(min_length=1)


class WorkProfile(BaseModel):
    title: str
    summary: str
    rules: list[WorkProfileRule] = Field(default_factory=list)


def collect_existing_corpus_evidence(csv_path: Path) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for item in load_corpus_records(csv_path):
        location = f"{item.conversation_id}/{item.message_id}"
        records.append(
            EvidenceRecord(
                id=evidence_id(item.source_type, location, item.derek_reply),
                source_type=item.source_type,
                title=item.source_title,
                timestamp=item.timestamp,
                location=location,
                scenario="general",
                evidence_strength="behavior_high",
                sensitivity="general",
                excerpt=safe_excerpt(item.derek_reply),
                usable_for_profile=True,
            )
        )
    return records


def collect_local_doc_evidence(workspace: Path) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    seen_paths: set[Path] = set()
    for base in LOCAL_AUTHORED_DIRS:
        root = workspace / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(workspace)
            if relative_path in seen_paths:
                continue
            if path.suffix.lower() not in LOCAL_TEXT_SUFFIXES:
                continue
            if any(part in LOCAL_IGNORED_PARTS for part in relative_path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue

            seen_paths.add(relative_path)
            relative = str(relative_path)
            strength = (
                "authored_high"
                if base in HIGH_CONFIDENCE_AUTHORED_DIRS
                else "authored_assumed"
            )
            records.append(
                EvidenceRecord(
                    id=evidence_id("local_doc", relative, text[:1000]),
                    source_type="local_doc",
                    title=path.name,
                    timestamp="",
                    location=relative,
                    scenario="general",
                    evidence_strength=strength,
                    sensitivity=classify_local_doc_sensitivity(relative, text),
                    excerpt=safe_excerpt(text),
                    usable_for_profile=True,
                )
            )
    return records


def _doc_nodes_from_payload(payload: dict) -> list[dict]:
    result = payload.get("result", payload)
    if isinstance(result, dict):
        nodes = result.get("nodes") or result.get("items") or result.get("list") or []
        return [node for node in nodes if isinstance(node, dict)]
    return []


def _doc_next_page_token(payload: dict) -> str:
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return ""
    token = (
        result.get("nextPageToken")
        or result.get("nextToken")
        or result.get("next_page_token")
        or ""
    )
    return str(token) if token else ""


def _doc_markdown_from_payload(payload: dict) -> str:
    result = payload.get("result", payload)
    if isinstance(result, dict):
        markdown = (
            result.get("markdown")
            or result.get("content")
            or result.get("text")
            or ""
        )
        return str(markdown)
    return ""


def _dingtalk_kb_cache_path(cache_dir: Path, node_id: str) -> Path:
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"node_{digest}.md"


def collect_dingtalk_kb_evidence(
    *,
    dws,
    cache_dir: Path,
    workspace_id: str | None = None,
    folder_id: str | None = None,
    limit: int = 200,
) -> list[EvidenceRecord]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    records: list[EvidenceRecord] = []
    page_token = ""
    seen_page_tokens: set[str] = set()
    while len(records) < limit:
        payload = dws.list_doc_nodes(
            workspace_id=workspace_id,
            folder_id=folder_id,
            page_token=page_token,
        )
        for node in _doc_nodes_from_payload(payload):
            if len(records) >= limit:
                break
            node_id = str(node.get("nodeId") or node.get("dentryUuid") or "")
            if not node_id:
                continue
            extension = str(node.get("extension") or "").lower()
            content_type = str(node.get("contentType") or "").upper()
            if extension and extension != "adoc":
                continue
            if not extension and content_type != "ALIDOC":
                continue
            info = dws.doc_info(node_id)
            markdown = _doc_markdown_from_payload(dws.read_doc(node_id)).strip()
            if not markdown:
                continue
            cache_path = _dingtalk_kb_cache_path(cache_dir, node_id)
            cache_path.write_text(markdown, encoding="utf-8")
            info_result = info.get("result", info) if isinstance(info, dict) else {}
            title = str(info_result.get("name") or node.get("name") or node_id)
            location = f"dingtalk-kb:{node_id}"
            records.append(
                EvidenceRecord(
                    id=evidence_id("dingtalk_kb_live", location, markdown[:1000]),
                    source_type="dingtalk_kb_live",
                    title=title,
                    timestamp=str(
                        info_result.get("modifiedTime")
                        or info_result.get("createdTime")
                        or ""
                    ),
                    location=location,
                    scenario="general",
                    evidence_strength="kb_live_doc",
                    sensitivity=classify_local_doc_sensitivity(location, markdown),
                    excerpt=safe_excerpt(markdown),
                    usable_for_profile=True,
                )
            )
        next_page_token = _doc_next_page_token(payload)
        if not next_page_token or next_page_token in seen_page_tokens:
            break
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
    return records


def _rules_by_category(profile: WorkProfile, category: str) -> list[WorkProfileRule]:
    return [rule for rule in profile.rules if rule.category == category]


def _rule_lines(rule: WorkProfileRule) -> list[str]:
    scenarios = ", ".join(rule.scenarios) if rule.scenarios else "general"
    return [
        f"### {rule.title}",
        "",
        f"- Rule id: `{rule.id}`",
        f"- Scenarios: {scenarios}",
        f"- Trigger: {rule.trigger}",
        f"- Do: {rule.do}",
        f"- Do not: {rule.dont}",
        f"- Confidence: {rule.confidence}",
        "",
    ]


def render_markdown_profile(profile: WorkProfile) -> str:
    lines = [
        "# Derek Work Profile",
        "",
        profile.summary,
        "",
        "## Scope",
        "",
        (
            "Use this profile for DingTalk auto-reply judgment, business "
            "communication, product judgment, management coordination, "
            "recruiting triage, and approval pre-review. It is not Derek's "
            "final personal decision."
        ),
        "",
        "## Core Judgment Order",
        "",
        "1. Decide whether Derek needs to reply.",
        "2. Check whether the material is complete.",
        "3. Check hard boundaries before making any commitment.",
        "4. Reply with conclusion, reason, and next step when enough evidence exists.",
        "5. Ask a focused follow-up when evidence is missing.",
        "",
        "## Decision Framework",
        "",
    ]
    for rule in _rules_by_category(profile, "decision"):
        lines.extend(_rule_lines(rule))
    lines.extend(["## Expression Framework", ""])
    for rule in _rules_by_category(profile, "expression"):
        lines.extend(_rule_lines(rule))
    lines.extend(["## Follow-Up Framework", ""])
    for rule in _rules_by_category(profile, "follow_up"):
        lines.extend(_rule_lines(rule))
    lines.extend(
        [
            "## Scenario Playbooks",
            "",
            "- Approval: verify body, budget, owner, project context, and attachment before giving a view.",
            "- Candidate review: require role context, resume evidence, and interview material before judging fit.",
            "- Business or product judgment: identify customer value, boundary, owner, and next step.",
            "- Daily coordination: reply only when the next action is clear; hand off real-world actions to Derek.",
            "",
        ]
    )
    lines.extend(["## Boundary Framework", ""])
    for rule in _rules_by_category(profile, "boundary"):
        lines.extend(_rule_lines(rule))
    lines.extend(
        [
            "## Honest Boundaries",
            "",
            "- This profile is inferred from local work evidence and authored material.",
            "- It improves draft judgment but does not replace Derek's final decision.",
            "- It must not override the service's hard safety and privacy guardrails.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_skill(profile: WorkProfile) -> str:
    return f"""---
name: derek-perspective
description: Derek's work perspective for reviewing drafts, decisions, and business communication. Use when the user asks for Derek's angle, Derek work style, or Derek perspective.
---

# Derek Work Perspective

This skill represents Derek's work perspective based on local evidence. It is not Derek himself and does not authorize final real-world decisions.

Do not use this skill as the automated DingTalk runtime. The runtime reads `profiles/derek_work_profile.md` inside `ceo-agent-service`.

## Scope

{profile.summary}

## Hard Boundaries

- Do not claim Derek has joined a meeting, made a call, checked a message, approved a request, or completed a real-world action.
- Do not make final personnel, approval, finance, legal, or customer-critical decisions.
- When material is incomplete, ask for the missing material instead of inventing a conclusion.
"""


def _evidence_source_summary(evidence: list[EvidenceRecord]) -> str:
    source_types = sorted({record.source_type for record in evidence})
    return ", ".join(source_types) if source_types else "none"


def _pick_evidence_ids(
    evidence: list[EvidenceRecord],
    *,
    preferred_sensitivities: tuple[str, ...] = (),
    preferred_source_types: tuple[str, ...] = (),
    limit: int = 4,
) -> list[str]:
    selected: list[str] = []

    def append(record: EvidenceRecord) -> None:
        if record.usable_for_profile and record.id not in selected:
            selected.append(record.id)

    for sensitivity in preferred_sensitivities:
        for record in evidence:
            if record.sensitivity == sensitivity:
                append(record)
                break
    for source_type in preferred_source_types:
        for record in evidence:
            if record.source_type == source_type:
                append(record)
                break
    for record in evidence:
        append(record)
        if len(selected) >= limit:
            break

    return selected[:limit] or ["ev_manual_profile_seed"]


def build_initial_profile(evidence: list[EvidenceRecord]) -> WorkProfile:
    usable_evidence = [record for record in evidence if record.usable_for_profile]
    if usable_evidence:
        summary = (
            "A work-context profile for Derek's DingTalk auto-reply agent, "
            f"seeded from {len(usable_evidence)} usable records across "
            f"{len({record.source_type for record in usable_evidence})} source types "
            f"({_evidence_source_summary(usable_evidence)}) and ready for continued refinement."
        )
    else:
        summary = (
            "Initial deterministic seed for Derek's DingTalk auto-reply work "
            "profile. It defines the first runtime-safe judgment framework and "
            "will be replaced or refined as local evidence is collected."
        )
    decision_evidence_ids = _pick_evidence_ids(
        usable_evidence,
        preferred_sensitivities=("approval", "customer", "internal_personnel"),
        preferred_source_types=("dingtalk", "minutes", "dingtalk_kb_live", "local_doc"),
    )
    handoff_evidence_ids = _pick_evidence_ids(
        usable_evidence,
        preferred_source_types=("dingtalk", "minutes", "local_doc", "dingtalk_kb_live"),
    )
    expression_evidence_ids = _pick_evidence_ids(
        usable_evidence,
        preferred_source_types=("dingtalk", "minutes", "local_doc", "dingtalk_kb_live"),
    )
    follow_up_evidence_ids = _pick_evidence_ids(
        usable_evidence,
        preferred_sensitivities=("customer", "approval"),
        preferred_source_types=("dingtalk", "minutes", "local_doc", "dingtalk_kb_live"),
    )
    return WorkProfile(
        title="Derek Work Profile",
        summary=summary,
        rules=[
            WorkProfileRule(
                id="rule_materials_before_decision",
                title="材料不足不拍板",
                category="decision",
                scenarios=[
                    "approval",
                    "candidate_review",
                    "business",
                    "document_review",
                ],
                trigger=(
                    "A message asks for approval, judgment, confirmation, "
                    "comments, or finalization but lacks the body, background, "
                    "budget, owner, role context, resume, attachment, or "
                    "accessible link."
                ),
                do=(
                    "Ask for the specific missing material and say that a "
                    "judgment can be made after the material is complete."
                ),
                dont=(
                    "Do not approve, reject, advance, finalize, or evaluate "
                    "based only on a title or vague request."
                ),
                confidence="high",
                evidence_ids=decision_evidence_ids,
            ),
            WorkProfileRule(
                id="rule_real_world_actions_handoff",
                title="现实动作不代承诺",
                category="boundary",
                scenarios=["daily_coordination", "meeting", "handoff"],
                trigger=(
                    "A message asks whether Derek has joined, called, checked, "
                    "approved, gone onsite, or will immediately do a real-world "
                    "action."
                ),
                do="Hand off to Derek or state that Derek should personally handle it.",
                dont=(
                    "Do not claim Derek is doing, will do immediately, or has "
                    "done the action unless the conversation explicitly proves it."
                ),
                confidence="high",
                evidence_ids=handoff_evidence_ids,
            ),
            WorkProfileRule(
                id="rule_short_conclusion_next_step",
                title="先结论再下一步",
                category="expression",
                scenarios=[
                    "business",
                    "product",
                    "management",
                    "daily_coordination",
                ],
                trigger="The agent has enough evidence to reply.",
                do="Give a concise conclusion, one reason when useful, and the next action.",
                dont=(
                    "Do not write long background explanations, citations, "
                    "local paths, or tool details."
                ),
                confidence="medium",
                evidence_ids=expression_evidence_ids,
            ),
            WorkProfileRule(
                id="rule_focus_follow_up",
                title="追问要收敛问题",
                category="follow_up",
                scenarios=["business", "product", "approval", "candidate_review"],
                trigger="The user request is broad or missing the key decision variable.",
                do="Ask one focused question that unlocks the next decision.",
                dont=(
                    "Do not ask several broad questions or give generic advice "
                    "before the key missing fact is known."
                ),
                confidence="medium",
                evidence_ids=follow_up_evidence_ids,
            ),
        ],
    )


def write_jsonl(path: Path, records: list[EvidenceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
