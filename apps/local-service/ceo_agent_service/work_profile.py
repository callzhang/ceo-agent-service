import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


WHITESPACE_RE = re.compile(r"\s+")


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


def write_jsonl(path: Path, records: list[EvidenceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
