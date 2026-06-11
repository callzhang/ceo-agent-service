import pytest
from pydantic import ValidationError

from app.task_models import (
    FollowUpDraftStatus,
    ProjectCategory,
    ProjectPriority,
    ProjectStatus,
    TaskAgentDecision,
    TodoStatus,
    WorkItem,
)


def _memory_context():
    return {
        "query": "售前知识库",
        "summary": "售前知识库历史背景来自 memory_recall。",
        "memories": [
            {
                "source": "memory_recall",
                "uuid": "mem-1",
                "text": "售前知识库历史背景：材料沉淀在 business/售前知识库。",
                "summary": "材料沉淀在 business/售前知识库。",
                "created_at": "2026-06-05",
            }
        ],
    }


def test_work_item_keeps_input_small():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "42",
                "title": "项目进展",
                "conversation_id": "cid-1",
                "conversation_title": "项目群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "客户交付项目今天确认 P0 风险，需要 owner 给 ETA。",
            "project_name": "客户交付项目",
            "context": {
                "sender": "Mina",
                "participants": ["Mina", "Derek"],
                "source_conversation_kind": "group",
                "source_conversation_title": "项目群",
            },
        }
    )

    payload = item.model_dump()
    assert "project_candidates" not in payload
    assert "todo_candidates" not in payload
    assert "facts" not in payload
    assert item.project_name == "客户交付项目"


def test_project_category_is_fixed_enum():
    assert ProjectCategory.MANAGEMENT.value == "management"
    assert ProjectCategory.HR.value == "HR"
    assert ProjectCategory.OTHER.value == "other"

    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "action": "create_project",
                "project": {
                    "title": "x",
                    "category": "random",
                    "status": "active",
                },
                "todo_changes": [],
                "follow_up_drafts": [],
                "update_summary": "x",
                "merge_reason": "",
                "memory_recall_used": False,
                "confidence": 0.8,
            }
        )


def test_task_agent_decision_accepts_project_todo_and_follow_up():
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {
                "id": 7,
                "title": "售前知识库建设",
                "category": "sales",
                "tags": ["售前", "知识库"],
                "status": "active",
                "priority": "P1",
                "risk_level": "medium",
                "needs_derek_attention": True,
                "owner_user_id": "owner-1",
                "owner_name": "Alex",
                "related_people": [],
                "goal": "沉淀可复用售前材料",
                "background": "这是销售支持项目。",
                "memory_context": _memory_context(),
                "facts": [
                    {
                        "description": "已确认放在 business/售前知识库。",
                        "source": "memory_recall",
                        "created": "2026-06-05",
                        "updated": "2026-06-07",
                    }
                ],
                "current_state": "正在整理来源材料。",
                "blocker": "",
                "next_step": "确认可复用摘要边界。",
                "next_follow_up_at": "2026-06-10 09:00:00",
                "follow_up_mode": "draft",
                "source_conversations": [],
            },
            "todo_changes": [
                {
                    "action": "create",
                    "todo_id": None,
                    "title": "补齐售前材料来源链接",
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "status": "open",
                    "priority": "P1",
                    "deadline_at": "2026-06-10 18:00:00",
                    "next_follow_up_at": "2026-06-10 09:00:00",
                    "follow_up_question": "现在来源链接补齐到哪一步了？",
                    "completion_evidence": None,
                    "blocker": "",
                }
            ],
            "follow_up_drafts": [
                {
                    "todo_id": None,
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "target_conversation_id": "cid-1",
                    "target_kind": "group",
                    "question_text": "售前材料来源链接现在补齐到哪一步了？",
                    "scheduled_at": "2026-06-10 09:00:00",
                    "risk_check": {"owner_in_group": True, "sensitive": False},
                }
            ],
            "update_summary": "新增 P1 跟进项。",
            "merge_reason": "项目名称、owner 和售前知识库事实一致。",
            "memory_recall_used": True,
            "confidence": 0.86,
        }
    )

    assert decision.project.category == ProjectCategory.SALES
    assert decision.project.priority == ProjectPriority.P1
    assert decision.project.status == ProjectStatus.ACTIVE
    assert decision.project.memory_context.memories[0].uuid == "mem-1"
    assert decision.todo_changes[0].status == TodoStatus.OPEN
    assert decision.follow_up_drafts[0].status == FollowUpDraftStatus.DRAFT
