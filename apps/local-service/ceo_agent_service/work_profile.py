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
    payload = dws.list_doc_nodes(workspace_id=workspace_id, folder_id=folder_id)
    for node in _doc_nodes_from_payload(payload):
        if len(records) >= limit:
            break
        node_id = str(node.get("nodeId") or node.get("dentryUuid") or "")
        if not node_id:
            continue
        extension = str(node.get("extension") or "").lower()
        content_type = str(node.get("contentType") or "").upper()
        if extension != "adoc" and content_type != "ALIDOC":
            continue
        info = dws.doc_info(node_id)
        markdown = _doc_markdown_from_payload(dws.read_doc(node_id)).strip()
        if not markdown:
            continue
        cache_path = cache_dir / f"{node_id}.md"
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
                sensitivity="general",
                excerpt=safe_excerpt(markdown),
                usable_for_profile=True,
            )
        )
    return records


def write_jsonl(path: Path, records: list[EvidenceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
