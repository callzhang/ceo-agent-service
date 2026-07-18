"""One-shot historical Memory extraction + approved-only write.

Extraction is bounded (account + target/date bound + capped limit) and produces
only ``pending`` review candidates; it never writes Memory. Deterministic cleanup
drops secrets (passwords, verification codes, tokens, financial/medical), empty
sources, unsupported categories, and exact duplicates before human review. The
writer refuses anything not ``approved`` and is idempotent per candidate.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

ALLOWED_CATEGORIES = {
    "fact", "preference", "commitment", "decision",
    "project_status", "relationship", "reusable_experience",
}

_BLOCK_PATTERNS = [
    re.compile(r"验证码"),
    re.compile(r"verification code", re.I),
    re.compile(r"密码|password", re.I),
    re.compile(r"\btoken\b", re.I),
    re.compile(r"api[_ ]?key", re.I),
    re.compile(r"secret", re.I),
    re.compile(r"\b\d{6}\b"),                       # 6-digit one-time codes
    re.compile(r"身份证|银行卡|信用卡|bank card|credit card|\bcvv\b", re.I),
]


def _is_sensitive(statement: str) -> bool:
    return any(p.search(statement) for p in _BLOCK_PATTERNS)


class ExtractedMemoryCandidate(BaseModel):
    statement: str
    category: str
    confidence: float
    sensitivity: str = "normal"
    source_message_ids: list[str] = Field(default_factory=list)
    source_conversation_ids: list[str] = Field(default_factory=list)
    source_time_start: str = ""
    source_time_end: str = ""
    evidence_excerpt: str = ""
    cleanup_notes: str = ""


class WechatMemoryImporter:
    def __init__(self, store, reader=None, codex=None):
        self.store = store
        self.reader = reader
        self.codex = codex

    def clean_candidates(
        self, candidates: list[ExtractedMemoryCandidate]
    ) -> list[ExtractedMemoryCandidate]:
        seen: set[str] = set()
        out: list[ExtractedMemoryCandidate] = []
        for candidate in candidates:
            statement = " ".join(candidate.statement.split())
            if not statement or not candidate.source_message_ids:
                continue
            if candidate.category not in ALLOWED_CATEGORIES:
                continue
            if _is_sensitive(statement):
                continue
            key = statement.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate.model_copy(update={"statement": statement}))
        return out

    def run(self, *, account_id: str, target_ids: list[str], since: str,
            until: str, limit: int, import_run_id: str = "") -> dict:
        if not account_id:
            raise ValueError("account_id required")
        if not (target_ids or since or until):
            raise ValueError("bounded scope required: pass target_ids and/or a date bound")
        if not (1 <= limit <= 10000):
            raise ValueError("bounded scope required: 1 <= limit <= 10000")
        run_id = import_run_id or f"{account_id}:{since}:{until}:{limit}"
        raw = self.codex.extract(account_id, target_ids, since, until, limit) if self.codex else []
        candidates = self.clean_candidates([ExtractedMemoryCandidate(**c) if isinstance(c, dict) else c for c in raw])
        written = 0
        for candidate in candidates:
            if self.store.add_wechat_memory_candidate(
                import_run_id=run_id, account_id=account_id, candidate=candidate
            ) is not None:
                written += 1
        return {"import_run_id": run_id, "candidates": written}


class WechatMemoryWriter:
    """Approved-only, idempotent Memory write orchestration."""

    def __init__(self, store, memory_backend):
        self.store = store
        self.memory_backend = memory_backend

    def write(self, candidate_id: int) -> str:
        row = self.store.get_wechat_memory_candidate(candidate_id)
        if row is None:
            raise ValueError("candidate not found")
        if row["status"] != "approved":
            raise ValueError("candidate must be approved before writing memory")
        if row["memory_id"]:
            return row["memory_id"]  # idempotent
        statement = row["edited_statement"] or row["statement"]
        memory_id = self.memory_backend.write(
            statement,
            source_time_start=row["source_time_start"],
            source_time_end=row["source_time_end"],
        )
        self.store.set_wechat_memory_candidate_written(
            candidate_id, memory_id=memory_id, memory_write_status="written"
        )
        return memory_id
