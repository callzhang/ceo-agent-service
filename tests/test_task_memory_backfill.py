import json
from types import SimpleNamespace

import pytest

from app.task_memory_backfill import (
    ProjectMemoryContextCodexRunner,
    parse_project_memory_context,
    validate_project_memory_context,
)
from app.task_models import (
    FollowUpMode,
    ProjectCategory,
    ProjectMemoryContext,
    ProjectPriority,
    ProjectStatus,
    RiskLevel,
    WorkProject,
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


def test_project_memory_backfill_uses_persisted_project_identity_and_read_only_route(
    tmp_path,
):
    calls = []

    class Routed:
        def execute(self, **kwargs):
            calls.append(kwargs)
            value = kwargs["parser"](
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "mcp_tool_call",
                                    "tool": "memory_recall",
                                    "arguments": {"query": "项目历史"},
                                    "result": {"memories": []},
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "query": "项目历史",
                                "summary": "已查询 memory_recall。",
                                "memories": [],
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
            )
            return SimpleNamespace(value=value, session_id="session-1")

    project = WorkProject(
        id=41,
        title="项目",
        category=ProjectCategory.PROJECTS,
        status=ProjectStatus.ACTIVE,
        priority=ProjectPriority.P1,
        risk_level=RiskLevel.LOW,
        follow_up_mode=FollowUpMode.NONE,
        created_at="2026-08-20 10:00:00",
        updated_at="2026-08-20 10:00:00",
    )
    runner = ProjectMemoryContextCodexRunner(
        tmp_path,
        routed_execution=Routed(),
    )

    result = runner.build(project=project, todos=[], updates=[])

    assert result.query == "项目历史"
    assert runner.last_session_id == "session-1"
    assert calls[0]["workload_kind"] == "task"
    assert calls[0]["workload_key"] == "41:memory_backfill"
    assert calls[0]["conversation_id"] is None
    assert calls[0]["required_capabilities"] == frozenset(
        {"structured_output", "memory_connector_read"}
    )
    assert calls[0]["command_factory"]._approved_policy.effect_mode == "read_only"
