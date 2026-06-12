import json

import pytest

from app.task_models import ProjectMemoryContext
from app.task_memory_backfill import (
    parse_project_memory_context,
    validate_project_memory_context,
)


def test_parse_project_memory_context_reads_agent_message_text():
    raw = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(
                            {
                                "query": "候选人筛选项目",
                                "summary": "已查询 memory_recall。",
                                "memories": [],
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    context = parse_project_memory_context(raw)

    assert context.query == "候选人筛选项目"
    assert context.summary == "已查询 memory_recall。"


def test_parse_project_memory_context_rejects_unrelated_jsonl_events():
    raw = json.dumps({"type": "item.completed", "item": {"type": "reasoning"}})

    with pytest.raises(ValueError, match="No ProjectMemoryContext JSON found"):
        parse_project_memory_context(raw)


def test_validate_project_memory_context_rejects_memory_auth_failure():
    context = ProjectMemoryContext(
        query="候选人筛选项目",
        summary="已查询但需要重新认证。",
        memories=[],
    )

    with pytest.raises(ValueError, match="memory_recall authentication failed"):
        validate_project_memory_context(
            context,
            [
                {
                    "tool": "friday memory_memory_recall",
                    "output": '{"connector_auth_failure":{"auth_reason":"reauthentication_required"}}',
                }
            ],
        )
