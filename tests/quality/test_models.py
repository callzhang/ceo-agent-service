from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.quality.models import QualityCase, QualitySnapshot


def test_quality_case_rejects_direct_identifiers_and_paths() -> None:
    with pytest.raises(ValidationError):
        QualityCase.model_validate(
            {
                "id": "unsafe",
                "suite": "safety",
                "input_context": {"user_id": "real-user", "path": "/Users/alice/db"},
                "recorded_output": {},
            }
        )

    with pytest.raises(ValidationError, match="non-redacted text"):
        QualityCase.model_validate(
            {
                "id": "unsafe-text",
                "suite": "safety",
                "input_context": {"context": "Bearer synthetic-value"},
                "recorded_output": {},
            }
        )


def test_quality_snapshot_serialization_contains_no_sensitive_fields() -> None:
    snapshot = QualitySnapshot(
        commit="abc123",
        pid=42,
        schema_version=1,
        components=[],
        backlog={"failed": 0, "processing": 0, "unknown": 0},
        oldest_queue_age_seconds=0,
        failed_actions=0,
        unknown_actions=0,
        slo_status="pass",
        ready=True,
    )

    payload = json.loads(snapshot.model_dump_json())

    assert "path" not in payload
    assert "message" not in payload
    assert "user" not in payload
