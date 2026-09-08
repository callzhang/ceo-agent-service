from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-minutes-sync" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter() -> dict[str, object]:
    text = _skill_text()
    assert text.startswith("---\n")
    raw = text.split("---\n", 2)[1]
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_minutes_sync_skill_frontmatter_is_skill_metadata_not_a_schedule() -> None:
    frontmatter = _frontmatter()

    assert set(frontmatter) == {"name", "description", "metadata"}
    assert frontmatter["name"] == "ceo-minutes-sync"
    assert str(frontmatter["description"]).startswith("Use when ")
    assert frontmatter["metadata"] == {"managed_by": "ceo-agent-service"}
    assert "cron" not in frontmatter
    assert "trigger" not in frontmatter


def test_minutes_sync_skill_requires_the_read_skill_and_conditions_access_requests() -> None:
    text = _skill_text()

    assert "**REQUIRED SUB-SKILL:** Use `dingtalk-minutes`" in text
    assert "**CONDITIONAL SUB-SKILL:** Use `dingtalk-minutes-access-request`" in text
    assert "explicitly authorizes" in text
    assert "Do not send an access request" in text


def test_minutes_sync_skill_requires_complete_listing_and_content_pagination() -> None:
    text = _skill_text()

    for required in (
        "--page-all",
        "data.complete=true",
        "meta.pagination.next_token",
        "cursor repeats",
        "complete transcript",
        "basic metadata, summary, and complete transcript",
    ):
        assert required in text


def test_minutes_sync_skill_persists_content_cursor_and_permission_backlog() -> None:
    text = _skill_text()

    for required in (
        "content-cursor.json",
        "taskUuid",
        "permission_pending",
        "last_verified_source_version",
        "artifact_sha256",
        "Retry every permission-pending item",
        "clear its permission-pending state only after",
    ):
        assert required in text


def test_minutes_sync_skill_requires_source_freshness_evidence() -> None:
    text = _skill_text()

    for required in (
        "source_updated_at",
        "source_version",
        "fetched_at",
        "artifact_sha256",
        "archived manifest",
        "stale",
    ):
        assert required in text


def test_minutes_sync_skill_fails_closed_when_source_freshness_is_unknown() -> None:
    text = _skill_text()

    for required in (
        "freshness_unknown",
        "explicitly belongs to that artifact",
        "cannot prove freshness",
        "must not be `synced` or `skipped`",
        "makes the overall run non-successful",
    ):
        assert required in text
    assert "derive a stable version record" not in text


def test_minutes_sync_skill_triages_restricted_items_before_requesting_access() -> None:
    text = _skill_text()

    for required in (
        "trusted visible metadata",
        "CEO-relevant",
        "clearly out-of-scope",
        "cannot determine relevance",
        "content is necessary",
        "explicitly authorizes",
        "audit evidence",
        "needs_review",
        "prevents overall success",
        "A submitted access request remains `permission_pending`",
        "or trusted metadata proves it clearly out-of-scope",
    ):
        assert required in text


def test_minutes_sync_skill_defines_exclusive_counts_and_real_success() -> None:
    text = _skill_text()

    for required in (
        "synced + skipped + permission_pending + failed = discovered",
        "exactly one outcome",
        "discovered",
        "synced",
        "skipped",
        "permission_pending",
        "failed",
        "Exit code 0",
        "list request",
        "does not prove synchronization",
        "Run status is successful only when",
    ):
        assert required in text
