from app.action_storage import REPLY_ATTEMPT_COMMON_SQL, update_action
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.agent_cron.commands import (
    SERVICE_COMMAND_OPTIONS,
    ServiceCommandConsumerContext,
    ServiceCommandRegistry,
    ServiceCommandSkillMaterial,
)
from app.dws_client import DwsOaApprovalCandidate
from app.audit_web import _queue_attention_rows
from app.skill_features import FeatureRegistry
from app.store import AutoReplyStore
from app.task_scanners import (
    _pending_oa_task_ids_for_current_user,
    reconcile_failed_oa_reply_tasks,
    reconcile_unbound_failed_oa_attempts,
    scan_ai_minutes,
    scan_meeting_todos,
    scan_pending_oa_approvals,
)


def test_unbound_failed_oa_attempt_is_skipped_only_after_exact_terminal_readback():
    attempt = SimpleNamespace(
        id=4594,
        channel="dingtalk",
        conversation_id="conversation-1",
        trigger_message_id="message-1",
        send_status="failed",
        oa_process_instance_id="process-1",
        oa_task_id="task-1",
    )
    older_attempt = SimpleNamespace(**{
        **vars(attempt), "id": 4593, "trigger_message_id": "older-message"
    })

    class Store:
        def list_unbound_failed_oa_attempts(self, *, limit):
            assert limit == 20
            return [attempt, older_attempt]

        def skip_unbound_failed_oa_attempt(
            self, attempt_id, *, process_instance_id, oa_task_id, terminal_evidence
        ):
            assert process_instance_id == "process-1"
            assert oa_task_id == "task-1"
            self.evidence = terminal_evidence
            return attempt_id in {4593, 4594}

    class Dws:
        calls = 0

        def read_oa_process_instance_openapi(self, process_id):
            self.calls += 1
            assert process_id == "process-1"
            return {"result": {
                "status": "COMPLETED",
                "processInstanceResult": "agree",
                "tasks": [{"taskId": "task-1", "status": "COMPLETED"}],
            }}

    store = Store()
    dws = Dws()
    assert reconcile_unbound_failed_oa_attempts(store, dws) == [4594, 4593]
    assert dws.calls == 1
    assert "process-1" in store.evidence
    assert "task-1" in store.evidence
    assert "COMPLETED" in store.evidence


def _returned_but_running_process():
    return {"result": {
        "status": "RUNNING",
        "tasks": [
            {"taskId": "task-1", "userId": "principal-1", "taskStatus": "CANCELED"},
            {"taskId": "task-applicant", "userId": "applicant-1", "taskStatus": "RUNNING"},
        ],
        "operationRecords": [{
            "date": 1791889619840, "type": "REDIRECT_PROCESS",
            "userId": "principal-1", "result": "NONE",
        }],
    }}


def test_unbound_failed_oa_attempt_closes_when_principal_return_canceled_the_exact_task():
    attempt = SimpleNamespace(
        id=15880, channel="dingtalk", conversation_id="c", trigger_message_id="m",
        send_status="failed", oa_process_instance_id="process-1", oa_task_id="task-1",
    )

    class Store:
        def list_unbound_failed_oa_attempts(self, *, limit):
            return [attempt]

        def skip_unbound_failed_oa_attempt(self, attempt_id, *, terminal_evidence, **_):
            self.evidence = terminal_evidence
            return True

    class Dws:
        def read_oa_process_instance_openapi(self, process_id):
            return _returned_but_running_process()

    store = Store()
    assert reconcile_unbound_failed_oa_attempts(
        store, Dws(), current_user_id="principal-1"
    ) == [15880]
    assert "OA_RETURNED" in store.evidence
    assert "CANCELED" in store.evidence
    # Without knowing who the principal is, a running process proves nothing.
    assert reconcile_unbound_failed_oa_attempts(store, Dws()) == []


def test_unbound_failed_oa_attempt_stays_open_without_exact_terminal_evidence():
    attempt = SimpleNamespace(
        id=4595,
        channel="dingtalk",
        conversation_id="conversation-2",
        trigger_message_id="message-2",
        send_status="failed",
        oa_process_instance_id="process-2",
        oa_task_id="task-2",
    )

    class Store:
        def list_unbound_failed_oa_attempts(self, *, limit):
            return [attempt]

        def skip_unbound_failed_oa_attempt(self, *args, **kwargs):
            raise AssertionError("unverified OA state must not close the Attempt")

    class Dws:
        def read_oa_process_instance_openapi(self, process_id):
            return {"result": {
                "status": "RUNNING",
                "tasks": [{"taskId": "other-task", "status": "COMPLETED"}],
            }}

    assert reconcile_unbound_failed_oa_attempts(Store(), Dws()) == []


def test_failed_reply_task_closes_when_its_exact_oa_candidate_is_terminal():
    task = SimpleNamespace(
        id=44, channel="dingtalk", error="audit_revision_exhausted",
        execution_generation="gen-4", status="failed",
    )
    run = SimpleNamespace(
        id=91,
        role="consumer",
        status="completed",
        proposal_revision=3,
        turn_attempt=1,
        adopted_result_json=json.dumps({
            "outcome": "proposal",
            "proposal": {"actions": [{
                "capability": "dingtalk-oa",
                "operation": "revert_task",
                "target": {
                    "process_instance_id": "process-44",
                    "task_id": "node-44",
                },
            }]},
        }),
    )

    class Store:
        def list_reply_tasks(self, *, statuses, limit):
            assert statuses == ("failed", "needs_human")
            return [task]

        def list_agent_runs_for_task_generation(self, task_id, generation, *, load_events):
            assert (task_id, generation, load_events) == (44, "gen-4", False)
            return [run]

        def close_unresolved_reply_task_already_settled(
            self, task_id, *, expected_execution_generation, settled_evidence
        ):
            assert (task_id, expected_execution_generation) == (44, "gen-4")
            self.evidence = settled_evidence
            return True

    class Dws:
        def read_oa_process_instance_openapi(self, process_id):
            assert process_id == "process-44"
            return {
                "result": {
                    "status": "COMPLETED",
                    "processInstanceResult": "agree",
                    "tasks": [{"taskId": "node-44", "status": "COMPLETED"}],
                }
            }

    store = Store()
    assert reconcile_failed_oa_reply_tasks(store, Dws()) == [44]
    assert "process-44" in store.evidence
    assert "node-44" in store.evidence
    assert "COMPLETED" in store.evidence


def test_uncertain_reply_is_skipped_without_attention_when_exact_oa_task_is_terminal():
    task = SimpleNamespace(
        id=46, channel="dingtalk", error="external_action_uncertain",
        execution_generation="gen-6", status="failed",
        business_object_key="oa:process-46:node-46",
    )
    reviewed_run = SimpleNamespace(
        id=93, role="consumer", status="completed", proposal_revision=1,
        turn_attempt=0, adopted_result_json=json.dumps({
            "outcome": "proposal",
            "proposal": {"actions": [{
                "capability": "dingtalk-oa", "operation": "approve",
                "target": {"process_instance_id": "process-46", "task_id": "node-46"},
            }]},
        }),
    )
    latest_no_action = SimpleNamespace(
        id=94, role="consumer", status="completed", proposal_revision=2,
        turn_attempt=0, adopted_result_json=json.dumps({
            "outcome": "no_action", "proposal": None,
        }),
    )

    class Store:
        def list_reply_tasks(self, *, statuses, limit):
            return [task]

        def list_agent_runs_for_task_generation(self, *args, **kwargs):
            return [reviewed_run, latest_no_action]

        def skip_unresolved_reply_task_for_terminal_oa_source(
            self, task_id, *, expected_execution_generation,
            process_instance_id, oa_task_id, settled_evidence,
        ):
            assert (task_id, expected_execution_generation) == (46, "gen-6")
            assert (process_instance_id, oa_task_id) == ("process-46", "node-46")
            self.evidence = settled_evidence
            return True

        def close_unresolved_reply_task_already_settled(self, *args, **kwargs):
            raise AssertionError("uncertain delivery is skipped, not marked delivered")

    class Dws:
        def read_oa_process_instance_openapi(self, process_id):
            return {"result": {
                "status": "TERMINATED", "processInstanceResult": "agree",
                "tasks": [{"taskId": "node-46", "status": "CANCELED"}],
            }}

    store = Store()
    assert reconcile_failed_oa_reply_tasks(store, Dws()) == [46]
    assert "process-46" in store.evidence
    assert "node-46" in store.evidence
    assert "TERMINATED" in store.evidence


def test_failed_reply_task_does_not_close_for_nonterminal_or_mismatched_oa():
    task = SimpleNamespace(
        id=45, channel="dingtalk", error="audit_revision_exhausted",
        execution_generation="gen-5", status="failed",
    )
    candidate = {
        "outcome": "proposal",
        "proposal": {"actions": [{
            "capability": "dingtalk-oa",
            "operation": "revert_task",
            "target": {"process_instance_id": "process-45", "task_id": "node-45"},
        }]},
    }
    run = SimpleNamespace(
        id=92, role="consumer", status="completed", proposal_revision=1,
        turn_attempt=0, adopted_result_json=json.dumps(candidate),
    )

    class Store:
        def list_reply_tasks(self, *, statuses, limit):
            return [task]

        def list_agent_runs_for_task_generation(self, *args, **kwargs):
            return [run]

        def close_unresolved_reply_task_already_settled(self, *args, **kwargs):
            raise AssertionError("nonterminal OA must remain actionable")

    class Dws:
        def read_oa_process_instance_openapi(self, process_id):
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [{"taskId": "different-node", "status": "COMPLETED"}],
                }
            }

    assert reconcile_failed_oa_reply_tasks(Store(), Dws()) == []


def test_pending_oa_task_resolution_refuses_ambiguous_current_nodes():
    payload = {
        "result": {
            "tasks": [
                {"taskId": "task-1", "status": "RUNNING", "userId": "me"},
                {"taskId": "task-2", "status": "RUNNING", "userId": "me"},
            ]
        }
    }

    assert _pending_oa_task_ids_for_current_user(
        payload, current_user_id="me"
    ) == ["task-1", "task-2"]


def test_pending_oa_tasks_include_nonterminal_statuses_for_exact_principal():
    payload = {"result": {"tasks": [
        {"taskId": "waiting", "status": "PENDING", "userId": "me"},
        {"taskId": "in-progress", "status": "IN_PROGRESS", "userId": "me"},
        {"taskId": "completed", "status": "COMPLETED", "userId": "me"},
        {"taskId": "other-person", "status": "PENDING", "userId": "other"},
    ]}}
    assert _pending_oa_task_ids_for_current_user(
        payload, current_user_id="me"
    ) == ["waiting", "in-progress"]


def test_disabled_work_tracking_does_not_scan_or_enqueue_ai_minutes(tmp_path):
    store = AutoReplyStore(tmp_path / "scanner.sqlite3")
    registry = FeatureRegistry(state_path=tmp_path / "skill-state.json")
    registry.set_enabled("work_tracking", False)

    class Dws:
        def list_minutes(self):
            raise AssertionError("disabled scanner must not read AI minutes")

    assert scan_ai_minutes(store, Dws(), feature_registry=registry) == 0
    assert store.claim_work_summary_inputs(limit=1) == []


def test_scan_meeting_todos_carries_scheduled_consumer_context(tmp_path):
    class FakeDws:
        def __init__(self):
            self.list_limits = []

        def list_minutes(self, *, limit):
            self.list_limits.append(limit)
            return [{"taskUuid": "minutes-1", "title": "产品会"}]

        def get_minutes_todos(self, task_uuid):
            return {"result": {"actions": ['{"value":"确认发布范围"}']}}

        def get_all_minutes_transcription(self, task_uuid):
            return {"paragraphs": []}

        def get_minutes_summary(self, task_uuid):
            return {"result": {"fullSummary": ""}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    context = ServiceCommandConsumerContext(
        scheduled_task_id=7,
        scheduled_task_run_id=11,
        prompt="使用 $ceo-work-tracking 处理真实工作来源。",
        skill_names=("ceo-work-tracking",),
        skill_protocol="# Targeted Work Tracking Skill",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="ceo-work-tracking", content="large frozen material " * 1000
            ),
        ),
    )
    implementations = {option.name: lambda: "unused" for option in SERVICE_COMMAND_OPTIONS}
    dws = FakeDws()
    implementations["scan-meeting-todos-once"] = lambda: str(
        scan_meeting_todos(store, dws)
    )

    ServiceCommandRegistry(implementations).run(
        "scan-meeting-todos-once",
        consumer_context=context,
    )

    [claimed] = store.claim_work_summary_inputs(limit=10)
    payload = json.loads(claimed.payload_json)
    expected_context = context.to_payload()
    expected_context.pop("skill_materials")
    assert payload["scheduled_consumer"] == expected_context
    assert context.skill_materials[0].content == "large frozen material " * 1000
    assert dws.list_limits == [50]


def test_scan_ai_minutes_enqueues_adapter_items(tmp_path):
    class FakeDws:
        def list_minutes(self):
            return [
                {
                    "taskUuid": "minutes-1",
                    "title": "售前知识库周会",
                    "createdAt": "2026-06-07 09:00:00",
                    "summary": "需要补齐来源链接",
                }
            ]

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, FakeDws(), enqueue_existing_on_first_scan=True)

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_type == "ai_minutes"
    assert claimed[0].source_ref == "minutes-1"
    assert "售前知识库周会" in claimed[0].payload_json


def test_scan_ai_minutes_accepts_live_dws_row_shape(tmp_path):
    class FakeDws:
        def list_minutes(self):
            return [
                {
                    "uuid": "minutes-1",
                    "title": "吴柯欣 - 招聘专员-2026050701 - 三面",
                    "startTimeISO": "2026-06-07T13:24:06+08:00",
                }
            ]

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, FakeDws(), enqueue_existing_on_first_scan=True)

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_ref == "minutes-1"
    assert "2026-06-07T13:24:06+08:00" in claimed[0].payload_json


def test_scan_ai_minutes_walks_paginated_adapter(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            if not cursor:
                return {
                    "items": [
                        {
                            "taskUuid": "minutes-1",
                            "title": "第一页",
                            "createdAt": "2026-08-10T12:00:00+00:00",
                        }
                    ],
                    "has_more": True,
                    "next_token": "token-2",
                }
            return {
                "items": [
                    {
                        "taskUuid": "minutes-2",
                        "title": "第二页",
                        "createdAt": "2026-08-10T10:00:00+00:00",
                    }
                ],
                "has_more": False,
                "next_token": "",
            }

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps(
            {
                "seen_ids": ["minutes-2"],
                "oldest_seen_at": "2026-08-10T10:00:00+00:00",
            }
        ),
    )
    count = scan_ai_minutes(store, dws, enqueue_existing_on_first_scan=True)

    assert count == 1
    assert dws.tokens == [(50, ""), (50, "token-2")]
    claimed = store.claim_work_summary_inputs(limit=10)
    assert {row.source_ref for row in claimed} == {"minutes-1"}


def test_scan_ai_minutes_migrates_id_only_boundary_without_stale_pagination(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            return {
                "items": [
                    {
                        "taskUuid": "minutes-new",
                        "title": "恢复窗口的新纪要",
                        "createdAt": "2026-08-10T12:00:00+00:00",
                    },
                    {
                        "taskUuid": "minutes-known",
                        "title": "已记录纪要",
                        "createdAt": "2026-08-10T11:00:00+00:00",
                    },
                ],
                "has_more": True,
                "next_token": "stale-cursor",
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps({"seen_ids": ["minutes-known"]}),
    )

    assert scan_ai_minutes(store, dws, enqueue_existing_on_first_scan=True) == 1
    assert dws.tokens == [(50, "")]
    assert [row.source_ref for row in store.claim_work_summary_inputs(limit=10)] == [
        "minutes-new"
    ]
    state = json.loads(store.get_daily_scan_state("ai_minutes")["cursor_json"])
    assert state["oldest_seen_at"] == "2026-08-10T11:00:00+00:00"


def test_scan_ai_minutes_baselines_only_latest_page_on_first_scan(tmp_path):
    class FakeDws:
        def __init__(self):
            self.tokens = []

        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            self.tokens.append((limit, cursor))
            return {
                "items": [
                    {
                        "taskUuid": "minutes-1",
                        "title": "最新会议",
                        "createdAt": "2026-08-10T12:00:00+00:00",
                    }
                ],
                "has_more": True,
                "next_token": "older-page",
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, dws) == 0
    assert dws.tokens == [(50, "")]
    assert json.loads(store.get_daily_scan_state("ai_minutes")["cursor_json"]) == {
        "oldest_seen_at": "2026-08-10T12:00:00+00:00",
        "seen_ids": ["minutes-1"],
    }


def test_scan_ai_minutes_preserves_seen_cursor_when_pagination_fails(tmp_path):
    class FakeDws:
        def list_minutes(self):
            raise AssertionError("paginated adapter should be used")

        def list_minutes_page(self, *, limit, cursor):
            raise RuntimeError("page request failed")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    state = {"seen_ids": ["minutes-1"]}
    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at="2026-08-10T00:00:00+00:00",
        cursor_json=json.dumps(state),
    )

    assert scan_ai_minutes(store, FakeDws()) == 0
    saved = store.get_daily_scan_state("ai_minutes")
    assert json.loads(saved["cursor_json"]) == state
    assert saved["last_success_at"] == "2026-08-10T00:00:00+00:00"
    assert saved["last_error"] == "page request failed"


def test_scan_ai_minutes_keeps_completed_pages_when_a_later_page_fails(tmp_path):
    class PartialDws:
        def list_minutes_page(self, *, limit, cursor):
            if not cursor:
                return {
                    "items": [{"taskUuid": "minutes-1", "title": "第一页"}],
                    "has_more": True,
                    "next_token": "token-2",
                }
            raise RuntimeError("cursor expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(
        store,
        PartialDws(),
        enqueue_existing_on_first_scan=True,
    )

    assert count == 1
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["pagination_deferred"] is True


def test_scan_ai_minutes_records_empty_completed_page_before_cursor_failure(tmp_path):
    class EmptyFirstPageDws:
        def list_minutes_page(self, *, limit, cursor):
            if not cursor:
                return {
                    "items": [],
                    "has_more": True,
                    "next_token": "token-2",
                }
            raise RuntimeError("cursor expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, EmptyFirstPageDws()) == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["pagination_deferred"] is True


def test_scan_ai_minutes_baselines_existing_items_on_first_scan(tmp_path):
    class FakeDws:
        def __init__(self):
            self.items = [
                {"taskUuid": "minutes-1", "title": "历史会议"},
            ]

        def list_minutes(self):
            return self.items

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_ai_minutes(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.items = [
        {"taskUuid": "minutes-1", "title": "历史会议"},
        {"taskUuid": "minutes-2", "title": "新增会议"},
    ]

    assert scan_ai_minutes(store, dws) == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_ref == "minutes-2"


def test_scan_ai_minutes_limit_preserves_unqueued_new_items(tmp_path):
    class FakeDws:
        def __init__(self):
            self.items = [
                {"taskUuid": "minutes-1", "title": "历史会议"},
            ]

        def list_minutes(self):
            return self.items

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    assert scan_ai_minutes(store, dws) == 0

    dws.items = [
        {"taskUuid": "minutes-1", "title": "历史会议"},
        {"taskUuid": "minutes-2", "title": "新增会议 2"},
        {"taskUuid": "minutes-3", "title": "新增会议 3"},
    ]

    assert scan_ai_minutes(store, dws, max_new_items=1) == 1
    first_claimed = store.claim_work_summary_inputs(limit=10)
    assert [row.source_ref for row in first_claimed] == ["minutes-2"]

    assert scan_ai_minutes(store, dws, max_new_items=1) == 1
    second_claimed = store.claim_work_summary_inputs(limit=10)
    assert [row.source_ref for row in second_claimed] == ["minutes-3"]


def test_scan_ai_minutes_records_unavailable_adapter(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, object())

    assert count == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == "dws list_minutes unavailable"


def test_scan_ai_minutes_records_adapter_errors(tmp_path):
    class BrokenDws:
        def list_minutes(self):
            raise RuntimeError("auth expired")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_ai_minutes(store, BrokenDws())

    assert count == 0
    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_error"] == "auth expired"


def test_scan_meeting_todos_enqueues_only_meetings_with_action_items(tmp_path):
    class FakeDws:
        def list_minutes(self, *, limit):
            assert limit == 50
            return [
                {
                    "taskUuid": "minutes-1",
                    "title": "产品周会",
                    "createdAt": "2026-09-14T09:00:00+08:00",
                },
                {
                    "taskUuid": "minutes-2",
                    "title": "无行动项会议",
                    "createdAt": "2026-09-14T10:00:00+08:00",
                },
            ]

        def get_minutes_todos(self, task_uuid):
            if task_uuid == "minutes-1":
                return {
                    "result": {
                        "actions": [
                            '{"value":"Alex 在周五前确认上线范围"}'
                        ]
                    }
                }
            return {"result": {"actions": []}}

        def get_all_minutes_transcription(self, task_uuid):
            return {"paragraphs": []}

        def get_minutes_summary(self, task_uuid):
            return {"result": {"fullSummary": ""}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, FakeDws()) == 1

    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert claimed[0].source_type == "ai_minutes"
    assert claimed[0].source_ref.startswith("minutes-1#todos-sha256=")
    payload = json.loads(claimed[0].payload_json)
    assert payload["source"]["title"] == "产品周会行动项"
    assert "Alex 在周五前确认上线范围" in payload["summary"]
    assert payload["context"]["source_conversation_kind"] == "minutes"


def test_scan_meeting_todos_requeues_only_when_todos_change(tmp_path):
    class FakeDws:
        def __init__(self):
            self.actions = ['{"value":"第一版行动项"}']
            self.title = "经营会"
            self.request_id = "request-1"

        def list_minutes(self, *, limit):
            assert limit == 50
            return [{"taskUuid": "minutes-1", "title": self.title}]

        def get_all_minutes_transcription(self, task_uuid):
            return {"paragraphs": []}

        def get_minutes_summary(self, task_uuid):
            return {"result": {"fullSummary": ""}}

        def get_minutes_todos(self, task_uuid):
            assert task_uuid == "minutes-1"
            return {
                "request_id": self.request_id,
                "result": {"actions": self.actions},
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, dws) == 1
    first = store.claim_work_summary_inputs(limit=10)
    assert len(first) == 1
    store.mark_work_summary_input_done(first[0].id)

    assert scan_meeting_todos(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.title = "经营会（标题已修订）"
    dws.request_id = "request-2"
    assert scan_meeting_todos(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []

    dws.actions = ['{"value":"第二版行动项"}']
    assert scan_meeting_todos(store, dws) == 1
    second = store.claim_work_summary_inputs(limit=10)
    assert len(second) == 1
    assert second[0].source_ref != first[0].source_ref
    assert "第二版行动项" in second[0].payload_json


def test_scan_meeting_todos_does_not_advance_failed_or_deferred_items(tmp_path):
    class FakeDws:
        def __init__(self):
            self.fail_first = True

        def list_minutes(self, *, limit):
            assert limit == 50
            return [
                {"taskUuid": "minutes-1", "title": "读取失败"},
                {"taskUuid": "minutes-2", "title": "超过本轮上限"},
            ]

        def get_all_minutes_transcription(self, task_uuid):
            return {"paragraphs": []}

        def get_minutes_summary(self, task_uuid):
            return {"result": {"fullSummary": ""}}

        def get_minutes_todos(self, task_uuid):
            if task_uuid == "minutes-1" and self.fail_first:
                raise RuntimeError("todo read failed")
            return {"result": {"actions": [f'{{"value":"{task_uuid}"}}']}}

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, dws, max_new_items=1) == 1
    state = store.get_daily_scan_state("meeting_todos")
    assert state is not None
    assert "minutes-1: todo read failed" in state["last_error"]

    first = store.claim_work_summary_inputs(limit=10)
    assert len(first) == 1
    assert first[0].source_ref.startswith("minutes-2#todos-sha256=")
    store.mark_work_summary_input_done(first[0].id)

    dws.fail_first = False
    assert scan_meeting_todos(store, dws, max_new_items=1) == 1
    second = store.claim_work_summary_inputs(limit=10)
    assert len(second) == 1
    assert second[0].source_ref.startswith("minutes-1#todos-sha256=")


def test_scan_pending_oa_approvals_enqueues_daily_review_task(tmp_path):
    class FakeDws:
        def __init__(self):
            self.pages = []
            self.task_reads = []

        def read_oa_approval_detail(self, process_instance_id):
            raise AssertionError("the broken DWS detail adapter must not be used")

        def list_pending_oa_approvals(self, *, page, size, start, end):
            self.pages.append((page, size, start, end))
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            self.task_reads.append(process_instance_id)
            return {
                "result": {
                    "taskIdList": [
                        {"taskId": 102648882814},
                        {"taskId": 102648910080},
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": 102648882814,
                            "userId": "other-user",
                            "status": "COMPLETED",
                        },
                        {
                            "taskId": 102648910080,
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    from app.agent_cron.models import ScheduledTaskSkillRef

    skill_names = ("dingtalk-oa-approval", "stardust-oa-finance-review")
    scheduled_task = store.create_scheduled_task(
        migration_key="oa-review-skill-refresh-test",
        name="OA review",
        command="scan-oa-approvals",
        prompt="Use $dingtalk-oa-approval and $stardust-oa-finance-review.",
        cron_expression="0 * * * * *",
        timezone_name="UTC",
        skill_refs=tuple(
            ScheduledTaskSkillRef(
                skill_source="operation", skill_name=name, position=position
            )
            for position, name in enumerate(skill_names)
        ),
    )
    context = ServiceCommandConsumerContext(
        scheduled_task_id=scheduled_task.id,
        scheduled_task_run_id=11,
        prompt=(
            "Use $dingtalk-oa-approval and $stardust-oa-finance-review."
        ),
        skill_names=("dingtalk-oa-approval", "stardust-oa-finance-review"),
        skill_protocol="# OA review skills",
        skill_materials=(
            ServiceCommandSkillMaterial(
                name="dingtalk-oa-approval", content="approval material"
            ),
            ServiceCommandSkillMaterial(
                name="stardust-oa-finance-review", content="finance material"
            ),
        ),
    )
    implementations = {
        option.name: (lambda: "unused") for option in SERVICE_COMMAND_OPTIONS
    }
    skill_revision = 1
    class CurrentSkillOptions:
        def list_managed_skill_options(self):
            return ()

        def resolve_operation_skill(self, name):
            return SimpleNamespace(
                path=Path(f"/current/{name}/SKILL.md"),
                sha256=f"current-{name}-{skill_revision}",
                content=f"current body for {name}, revision {skill_revision}",
            )

    implementations["scan-oa-approvals"] = lambda: str(
        scan_pending_oa_approvals(
            store,
            dws,
            now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
            skill_options=CurrentSkillOptions(),
        )
    )

    queued = int(
        ServiceCommandRegistry(implementations).run(
            "scan-oa-approvals",
            consumer_context=context,
        )
    )

    assert queued == 1
    # DingTalk rejects list-pending pages above 20 items (400002 参数错误).
    assert dws.pages == [
        (
            1,
            20,
            "2025-07-27T09:30:00+08:00",
            "2026-07-27T09:30:00+08:00",
        )
    ]
    assert dws.task_reads == ["proc-1"]
    task = store.claim_reply_tasks(limit=1)[0]
    unchanged_skill_scan = int(
        ServiceCommandRegistry(implementations).run(
            "scan-oa-approvals",
            consumer_context=context,
        )
    )
    assert unchanged_skill_scan == 0

    skill_revision = 2
    changed_skill_scan = int(
        ServiceCommandRegistry(implementations).run(
            "scan-oa-approvals",
            consumer_context=context,
        )
    )
    assert changed_skill_scan == 0
    assert len(store.list_reply_tasks()) == 1
    # One conversation per approval: a shared session let a later
    # approval inherit an earlier one's transcript and skip its work.
    assert task.conversation_id == "oa_pending_scan:proc-1"
    assert task.conversation_title == "审批待办"
    assert task.trigger_message_id.startswith("oa-pending:proc-1:")
    assert "张三提交的录用申请" in task.trigger_text
    assert "申请人的最新明确陈述是其申请事实的权威来源" not in task.trigger_text
    assert "审批判断与动作一律以 dingtalk-oa-approval 为准" not in task.trigger_text
    assert "判断标准由本次所选的星尘业务 Skill 提供" in task.trigger_text
    # Derek, 2026-09-23: actions come from the generic decision table; only
    # finance templates use rule cards. Requiring a processCode-matched card
    # for every approval stopped every non-finance one at needs_human.
    assert "动作一律按通用 Skill 的完整决策表" in task.trigger_text
    assert "其他审批类型没有规则卡不是规则缺口" in task.trigger_text
    assert "业务规则卡" not in task.trigger_text
    assert "procInstId=proc-1&taskId=102648910080" in task.oa_url
    assert '"source":"oa_pending_scan"' in task.trigger_message_json
    payload = json.loads(task.trigger_message_json)
    bound_context = payload["raw_payload"]["scheduled_consumer"]
    assert bound_context["scheduled_task_id"] == scheduled_task.id
    assert bound_context["scheduled_task_run_id"] == context.scheduled_task_run_id
    assert [item["name"] for item in bound_context["skill_materials"]] == list(skill_names)
    assert [item["content"] for item in bound_context["skill_materials"]] == [
        f"## Operation Skill: {name}\nsource: /current/{name}/SKILL.md\n"
            f"sha256: current-{name}-1\n\ncurrent body for {name}, revision 1"
        for name in skill_names
    ]
    assert context.skill_materials[0].content == "approval material"
    from app.agent_cli import build_role_server

    for role in ("consumer", "audit"):
        server = build_role_server(
            role, task_id=task.id, db_path=tmp_path / "task.sqlite3",
            execution_generation=task.execution_generation,
        )
        read = server._tool_manager.get_tool("read_task_skill").fn
        for material in ServiceCommandConsumerContext.from_payload(
            bound_context
        ).skill_materials:
            assert read(material.name)["content"] == material.content

    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )
    changed_skill_scan = int(
        ServiceCommandRegistry(implementations).run(
            "scan-oa-approvals",
            consumer_context=context,
        )
    )
    assert changed_skill_scan == 1
    refreshed_task = next(
        row for row in store.list_reply_tasks()
        if row.trigger_message_id != task.trigger_message_id
    )
    assert refreshed_task.trigger_message_id != task.trigger_message_id
    assert "revision 2" in refreshed_task.trigger_message_json


def test_scan_pending_oa_approvals_closes_completed_oa_needs_human_attempt(tmp_path):
    class FakeDws:
        detail_reads: list[str] = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("a completed OA instance has no pending task to read")

        def read_oa_process_instance_openapi(self, process_instance_id):
            self.detail_reads.append(process_instance_id)
            return {
                "result": {
                    "status": "COMPLETED",
                    "processInstanceResult": "agree",
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    trigger_message_id = "oa-pending:proc-completed:revision-1"
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-09-10T12:00:00+00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        oa_url=(
            "https://aflow.dingtalk.com/detail?"
            "procInstId=proc-completed&taskId=task-completed"
        ),
    )
    task = store.get_reply_task_by_trigger_message_id(trigger_message_id)
    assert task is not None
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.complete_reply_task(
        claimed.id,
        expected_execution_generation=claimed.execution_generation,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id=trigger_message_id,
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
    )
    dws = FakeDws()
    assert scan_pending_oa_approvals(store, dws) == 0
    assert dws.detail_reads == ["proc-completed"]
    assert store.get_reply_attempt(attempt_id).send_status == "skipped"
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert json.loads(state["cursor_json"])["reconciled_completed_attempt_ids"] == [
        attempt_id
    ]


def test_scan_pending_oa_approvals_keeps_running_oa_needs_human_attempt(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": []}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"status": "RUNNING"}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        send_status="needs_human",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"


def test_scan_pending_oa_approvals_keeps_running_process_task_no_longer_pending(
    tmp_path,
):
    class FakeDws:
        def get_current_user_id(self):
            return "principal-user-1"

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("the process is no longer pending for the principal")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"status": "RUNNING"}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-2",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        oa_task_id="task-old",
        send_status="needs_human",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    with store._connect() as db:
        resolution = db.execute(
            f"select resolution from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (attempt_id,),
        ).fetchone()["resolution"]
    assert resolution == ""


def test_scan_pending_oa_approvals_closes_running_process_after_explicit_return(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("returned process is no longer pending for principal")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskId": "task-old",
                            "userId": "principal-user-1",
                            "taskStatus": "CANCELED",
                        },
                        {
                            "taskId": "task-applicant",
                            "userId": "applicant-user-1",
                            "taskStatus": "RUNNING",
                        },
                    ],
                    "operationRecords": [
                        {
                            "date": 1791889619840,
                            "type": "REDIRECT_PROCESS",
                            "userId": "principal-user-1",
                            "result": "NONE",
                        }
                    ],
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-returned:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-returned",
        oa_task_id="task-old",
        send_status="needs_human",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    with store._connect() as db:
        resolution = db.execute(
            f"select resolution from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (attempt_id,),
        ).fetchone()["resolution"]
    assert "verified return receipt" in resolution


def test_scan_clears_attention_when_process_moves_to_another_approver(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("the principal has no current node")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskId": "task-principal",
                            "userId": "principal-user-1",
                            "taskStatus": "COMPLETED",
                        },
                        {
                            "taskId": "task-next-approver",
                            "userId": "next-approver",
                            "taskStatus": "RUNNING",
                        },
                    ],
                    "operationRecords": [
                        {
                            "date": 1791889619840,
                            "type": "EXECUTE_TASK_NORMAL",
                            "userId": "principal-user-1",
                            "result": "AGREE",
                        }
                    ],
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    key = "oa:proc-next-approver:task-principal"
    store.set_daily_scan_state(
        "oa_pending",
        last_success_at="2026-10-12 00:00:00",
        cursor_json=json.dumps({"pending_business_object_keys": [key]}),
        last_error="",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert key not in json.loads(state["cursor_json"])["pending_business_object_keys"]
    assert not any(row["category"] == "OA approval" and row["id"] == key
                   for row in _queue_attention_rows(store))


def test_scan_does_not_restore_attention_for_legacy_task_owned_by_another_approver(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("the principal has no current node")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskId": "task-principal",
                            "userId": "principal-user-1",
                            "taskStatus": "COMPLETED",
                        },
                        {
                            "taskId": "task-next-approver",
                            "userId": "next-approver",
                            "taskStatus": "RUNNING",
                        },
                    ],
                    "operationRecords": [
                        {
                            "date": 1791889619840,
                            "type": "EXECUTE_TASK_NORMAL",
                            "userId": "principal-user-1",
                            "result": "AGREE",
                        }
                    ],
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan:proc-legacy",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-legacy:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-legacy",
        oa_task_id="task-principal",
        send_status="skipped",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "resolved_at": db.execute("select current_timestamp").fetchone()[0],
            "resolution": "Live DingTalk OA task reached terminal state OA_TASK_NOT_PENDING.",
        })

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    key = "oa:proc-legacy:task-principal"
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert key not in json.loads(state["cursor_json"])["pending_business_object_keys"]
    assert not any(row["category"] == "OA approval" and row["id"] == key
                   for row in _queue_attention_rows(store))
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert "OA_TASK_NOT_PENDING" in attempt.resolution


def test_scan_clears_legacy_attention_for_provider_snake_case_return_receipt(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return []

        def get_current_user_id(self):
            return "035504180730922"

        def read_oa_approval_tasks(self, process_instance_id):
            raise AssertionError("Derek's original node is no longer pending")

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "errcode": 0,
                "process_instance": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskid": 104248980945,
                            "userid": "V00_1400815737560",
                            "task_status": "CANCELED",
                        },
                        {
                            "taskid": 104220105271,
                            "userid": "next-approver",
                            "task_status": "RUNNING",
                        },
                    ],
                    "operation_records": [
                        {
                            "date": "2026-10-09 15:46:01",
                            "operation_type": "REDIRECT_PROCESS",
                            "userid": "035504180730922",
                            "operation_result": "NONE",
                        }
                    ],
                },
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    key = "oa:5aD5eemcSVO3kI2H7qL4rg03641790612331:104248980945"
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan:legacy-return",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:legacy-return:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="5aD5eemcSVO3kI2H7qL4rg03641790612331",
        oa_task_id="104248980945",
        send_status="skipped",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "resolved_at": db.execute("select current_timestamp").fetchone()[0],
            "resolution": "Live DingTalk OA task reached terminal state OA_TASK_NOT_PENDING.",
        })
    store.set_daily_scan_state(
        "oa_pending",
        last_success_at="2026-10-12 00:00:00",
        cursor_json=json.dumps({
            "pending_business_object_keys": [key],
            "reconciled_legacy_oa_attempt_ids": [attempt_id],
        }),
        last_error="",
    )

    assert scan_pending_oa_approvals(store, FakeDws()) == 0
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert key not in json.loads(state["cursor_json"])["pending_business_object_keys"]
    assert not any(
        row["category"] == "OA approval" and row["id"] == key
        for row in _queue_attention_rows(store)
    )
    corrected = store.get_reply_attempt(attempt_id)
    assert corrected is not None
    assert "verified return receipt" in corrected.resolution
    assert "Previous resolution retained: Live DingTalk OA task reached terminal state OA_TASK_NOT_PENDING." in corrected.resolution
    assert not store.correct_legacy_oa_task_not_pending_resolution(
        attempt_id,
        process_instance_id="5aD5eemcSVO3kI2H7qL4rg03641790612331",
        task_id="104248980945",
        process_result="OA_RETURNED",
    )
    assert store.get_reply_attempt(attempt_id).resolution == corrected.resolution


def test_scan_pending_oa_approvals_does_not_close_absent_task_on_partial_scan(
    tmp_path,
):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="other-process",
                    title="其他审批",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "other-task"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            if process_instance_id == "proc-running":
                return {"result": {"status": "RUNNING"}}
            return {
                "result": {
                    "status": "RUNNING",
                    "tasks": [
                        {
                            "taskId": "other-task",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ],
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {"result": {"operationRecords": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-running:revision-3",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-running",
        oa_task_id="task-old",
        send_status="needs_human",
    )

    scan_pending_oa_approvals(
        store,
        FakeDws(),
        page_size=1,
        max_pages=1,
    )

    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"


def test_scan_pending_oa_approvals_caches_applicant_open_dingtalk_id(tmp_path):
    class Profile:
        user_id = "applicant-user-1"
        name = "张三"
        title = ""
        open_dingtalk_id = "open-applicant-1"
        manager_user_id = None
        manager_name = ""
        department_ids = set()
        department_names = set()
        org_labels = []
        has_subordinate = None

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的申请",
                    process_name="申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "originatorUserid": "applicant-user-1",
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ],
                }
            }

        def get_user_profiles(self, user_ids):
            assert user_ids == ["applicant-user-1"]
            return [Profile()]

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    assert scan_pending_oa_approvals(store, FakeDws(), now=datetime.fromisoformat(
        "2026-07-27T09:30:00+08:00"
    )) == 1

    task = store.claim_reply_tasks(limit=1)[0]
    assert '"originatorOpenDingTalkId":"open-applicant-1"' in task.trigger_message_json
    profile = store.get_org_user_profile("applicant-user-1")
    assert profile is not None
    assert profile.open_dingtalk_id == "open-applicant-1"


def test_scan_pending_oa_approvals_reuses_cached_applicant_mapping(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(
                process_instance_id="proc-1", title="张三提交的申请", process_name="申请"
            )]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"originatorUserid": "applicant-user-1", "tasks": [
                {"taskId": "task-1", "userId": "principal-user-1", "status": "RUNNING"}
            ]}}

        def get_user_profiles(self, user_ids):
            return []

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.upsert_org_user_profile(
        user_id="applicant-user-1", name="张三", open_dingtalk_id="open-applicant-1",
        manager_user_id=None, department_ids=set(),
    )
    assert scan_pending_oa_approvals(store, FakeDws(), now=datetime.fromisoformat(
        "2026-07-27T09:30:00+08:00"
    )) == 1
    task = store.claim_reply_tasks(limit=1)[0]
    assert '"originatorOpenDingTalkId":"open-applicant-1"' in task.trigger_message_json


def test_scan_pending_oa_approvals_covers_an_old_process_that_reaches_me_now(tmp_path):
    class FakeDws:
        def __init__(self):
            self.pages = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            self.pages.append((page, size, start, end))
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="old-proc",
                    title="刘紫煜提交的供应商付款申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {"result": {"operationRecords": []}}

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_pending_oa_approvals(
        store,
        dws,
        now=datetime.fromisoformat("2026-07-31T19:00:00+08:00"),
    ) == 1

    assert dws.pages[0][2] == "2025-07-31T19:00:00+08:00"


def test_scan_pending_oa_approvals_requeues_when_a_new_remark_arrives(tmp_path):
    class FakeDws:
        latest_operation_time = 1

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": self.latest_operation_time,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1
    dws.latest_operation_time = 2
    assert scan_pending_oa_approvals(store, dws, now=now) == 1


def test_scan_pending_oa_approvals_does_not_requeue_after_own_decision(tmp_path):
    """A decision by the reviewer closes the approval for this scan.

    This used to be asserted with an `ADD_REMARK`, which is a comment rather
    than a decision; production showed approvals disappearing after the agent
    commented on them, so only a real decision counts here now.
    """

    class FakeDws:
        latest_operation_time = 1
        latest_operation_user_id = "requester"
        latest_operation_type = "ADD_REMARK"

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        {
                            "operationType": self.latest_operation_type,
                            "operationTime": self.latest_operation_time,
                            "userId": self.latest_operation_user_id,
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    # The current reviewer has already decided after the applicant's latest
    # message. A pending-list hit is not new work and must not enter the queue.
    dws.latest_operation_time = 2
    dws.latest_operation_user_id = "principal-user-1"
    dws.latest_operation_type = "EXECUTE_TASK_NORMAL"
    assert scan_pending_oa_approvals(store, dws, now=now) == 0


def test_scan_pending_oa_approvals_skips_when_current_task_id_is_missing(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": []}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"tasks": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    queued = scan_pending_oa_approvals(
        store,
        FakeDws(),
        now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
    )

    assert queued == 0
    assert store.claim_reply_tasks(limit=1) == []


def test_scan_pending_oa_approvals_does_not_guess_unowned_task_ids(tmp_path):
    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {
                "result": {
                    "taskIdList": [
                        {"taskId": 102648882814},
                        {"taskId": 102648910080},
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {"result": {"tasks": []}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    queued = scan_pending_oa_approvals(
        store,
        FakeDws(),
        now=datetime.fromisoformat("2026-07-27T09:30:00+08:00"),
    )

    assert queued == 0
    assert store.claim_reply_tasks(limit=1) == []
    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])[
        "skipped_missing_task_id_process_instance_ids"
    ] == ["proc-1"]


def test_scan_pending_oa_approvals_records_task_read_failures(tmp_path):
    class BrokenDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1",
                    title="张三提交的录用申请",
                    process_name="录用申请",
                )
            ]

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            raise RuntimeError("DingTalk OA detail unavailable")

    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_pending_oa_approvals(store, BrokenDws()) == 0

    state = store.get_daily_scan_state("oa_pending")
    assert state is not None
    assert state["last_error"] == ""
    assert json.loads(state["cursor_json"])["read_failure_process_instance_ids"] == [
        "proc-1"
    ]


def test_scan_pending_oa_approvals_revisits_an_untouched_approval_the_next_day(tmp_path):
    """Production 2026-09-17: four approvals sat in Derek's list for weeks.

    A turn had ended without a decision, so nothing changed their revision and
    the scanner never offered them again, while DingTalk still showed them
    waiting on him. Same day is still deduplicated; a new day is not.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = datetime.fromisoformat("2026-07-31T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=first) == 1
    assert scan_pending_oa_approvals(store, dws, now=first) == 0
    assert (
        scan_pending_oa_approvals(
            store, dws, now=first + timedelta(days=1)
        )
        == 1
    )


def test_scan_pending_oa_approvals_does_not_reset_failed_retry_budget_for_same_revision(
    tmp_path,
):
    class FakeDws:
        record_time = 1

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [{
                        "taskId": "task-1",
                        "userId": "principal-user-1",
                        "status": "RUNNING",
                    }]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [{
                        "operationType": "ADD_REMARK",
                        "operationTime": self.record_time,
                        "userId": "requester",
                    }]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = datetime.fromisoformat("2026-07-31T19:00:00+08:00")
    assert scan_pending_oa_approvals(store, dws, now=first) == 1

    [task] = store.list_reply_tasks(channel="dingtalk")
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.fail_reply_task(
        claimed.id,
        "consumer_retry_exhausted",
        expected_execution_generation=claimed.execution_generation,
    )

    # The same pending evidence cannot start a new execution generation and
    # refund the exhausted Consumer/Audit retry budget on the next daily scan.
    assert scan_pending_oa_approvals(store, dws, now=first + timedelta(days=1)) == 0

    # A new source record changes the evidence revision and is eligible for a
    # fresh review budget.
    dws.record_time = 2
    assert scan_pending_oa_approvals(store, dws, now=first + timedelta(days=2)) == 1


def test_scan_pending_oa_approvals_keeps_an_approval_the_agent_only_commented_on(
    tmp_path,
):
    """Production 2026-09-17: a comment is not a decision.

    The agent comments as the principal whenever it reviews without deciding.
    That comment became the newest operation record for the principal, the
    revision came back empty, and the approval fell out of every later scan
    while DingTalk still showed it waiting on him -- the outer cause of the
    four-approval backlog, ahead of the same-day deduplication.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(
                    process_instance_id="proc-1", title="软件项目立项全流程"
                )
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 2,
                            "userId": "principal-user-1",
                        },
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1


def test_scan_pending_oa_approvals_gives_each_approval_its_own_conversation(tmp_path):
    """Production 2026-09-17: four approvals, one Agent session, no work done.

    The Agent session is keyed by conversation id.  With every approval sharing
    `oa_pending_scan`, each run after the first resumed the previous approval's
    transcript and returned "已在当前会话中完成处理" after zero tool calls -- runs
    20015 and 20016 made none at all, and 20016 reported an approval it had
    never executed while DingTalk still showed the item waiting.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [
                DwsOaApprovalCandidate(process_instance_id="proc-1", title="立项"),
                DwsOaApprovalCandidate(process_instance_id="proc-2", title="合同"),
            ]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {"taskId": f"task-{process_instance_id}", "status": "RUNNING"}
                    ]
                }
            }

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": f"task-{process_instance_id}",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, FakeDws(), now=now) == 2

    conversations = {task.conversation_id for task in store.claim_reply_tasks(limit=10)}
    assert conversations == {"oa_pending_scan:proc-1", "oa_pending_scan:proc-2"}


def test_scan_pending_oa_approvals_waits_for_a_new_comment_once_reviewed(tmp_path):
    """Derek 2026-09-17: once we have commented, wake on a new comment, not a timer.

    A daily reminder for an approval whose ball is in someone else's court is
    noise.  An approval we have NOT commented on is different -- that one can
    have lost its turn silently, so it keeps the daily revisit.
    """

    class FakeDws:
        extra_records: list = []

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="付款申请")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        },
                        *self.extra_records,
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    day_one = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    # Not reviewed yet: the daily revisit still protects it.
    assert scan_pending_oa_approvals(store, dws, now=day_one) == 1
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=1)) == 1

    # We commented. No timer wake-up, however many days pass: our own record
    # is excluded from the revision, so nothing about the approval has moved.
    dws.extra_records = [
        {"operationType": "ADD_REMARK", "operationTime": 2, "userId": "principal-user-1"}
    ]
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=2)) == 0
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=3)) == 0
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=9)) == 0

    # Someone else comments: that is the wake-up signal.
    dws.extra_records = [
        *dws.extra_records,
        {"operationType": "ADD_REMARK", "operationTime": 3, "userId": "requester"},
    ]
    assert scan_pending_oa_approvals(store, dws, now=day_one + timedelta(days=10)) == 1


def test_scan_pending_oa_approvals_starts_each_review_from_a_clean_session(tmp_path):
    """A new scan is a fresh look at DingTalk, not a continuation.

    The session is bound to the conversation, so a requeued approval resumed
    the previous turn's transcript: runs 20017, 20021, 20023 and 20032 on the
    same approval all carried session 01a0b122 and the last three returned
    "已在此前一次运行中完成实时审阅" verbatim with zero tool calls.
    """

    class FakeDws:
        revision_seed = 1

        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="续签")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": self.revision_seed,
                            "userId": "requester",
                        }
                    ]
                }
            }

    dws = FakeDws()
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    conversation = "oa_pending_scan:proc-1"
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, dws, now=now) == 1
    store.upsert_conversation_runtime_session(
        conversation, "codex_oauth", "session-from-the-previous-turn"
    )
    assert (
        store.get_conversation_runtime_session(conversation, "codex_oauth") is not None
    )

    # The applicant replies, so the next scan is a genuinely new review.
    dws.revision_seed = 2
    assert scan_pending_oa_approvals(store, dws, now=now + timedelta(days=1)) == 1

    assert store.get_conversation_runtime_session(conversation, "codex_oauth") is None


def test_scan_pending_oa_approvals_points_the_turn_at_our_own_skill(tmp_path):
    """The prompt named the vendor reference, so our Skill was never read.

    Run 20020 read `dingtalk-oa-approval` zero times and `dingtalk-misc` seven,
    which is why eight revisions of our OA rules -- revert-first, the scoring
    rubric, the no-hand-copied-URL rule -- had no effect on the pipeline. The
    vendor Skills are also overwritten by `dws upgrade`, so rules cannot live
    there.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="立项")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "requester",
                        }
                    ]
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-17T19:00:00+08:00")

    assert scan_pending_oa_approvals(store, FakeDws(), now=now) == 1

    [task] = store.claim_reply_tasks(limit=1)
    assert "dingtalk-oa-approval/SKILL.md" in task.trigger_text
    # The vendor reference may still be mentioned, but only as command usage.
    assert "dingtalk-misc 的 references/oa.md 只作为 dws 命令用法参考" in task.trigger_text
    assert "审批判断与动作一律以 dingtalk-oa-approval 为准" not in task.trigger_text


def test_scan_pending_oa_approvals_names_the_principal_to_the_turn(tmp_path):
    """The turn was only handed the applicant's id and guessed Derek's from it.

    On 李明's contract approval it took her userId 100000000000000001 for
    Derek's, concluded that Derek's own task belonged to someone else, and
    skipped it twice. The scanner already knows the principal -- it used that id
    to decide the task was his -- so it must say so outright.
    """

    class FakeDws:
        def list_pending_oa_approvals(self, *, page, size, start, end):
            return [DwsOaApprovalCandidate(process_instance_id="proc-1", title="合同")]

        def get_current_user_id(self):
            return "principal-user-1"

        def read_oa_approval_tasks(self, process_instance_id):
            return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

        def read_oa_process_instance_openapi(self, process_instance_id):
            return {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "userId": "principal-user-1",
                            "status": "RUNNING",
                        }
                    ]
                }
            }

        def read_oa_approval_records(self, process_instance_id):
            return {
                "result": {
                    "operationRecords": [
                        {
                            "operationType": "ADD_REMARK",
                            "operationTime": 1,
                            "userId": "applicant-user-1",
                        }
                    ]
                }
            }

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    now = datetime.fromisoformat("2026-09-23T09:00:00+08:00")

    assert scan_pending_oa_approvals(store, FakeDws(), now=now) == 1

    [task] = store.claim_reply_tasks(limit=1)
    assert "principal-user-1" in task.trigger_text
    assert "task-1" in task.trigger_text
    assert "originatorUserid 是申请人" in task.trigger_text
    payload = json.loads(task.trigger_message_json)["raw_payload"]
    assert payload["principalUserid"] == "principal-user-1"


FRIDAY_TODOS = {"result": {"actions": ['{"value":"整理访谈问题清单并发给磊哥确认"}'], "dingtalkTodoList": [
    {"title": "整理访谈问题清单并发给磊哥确认", "createdTime": 738480, "executorList": []},
]}}
FRIDAY_PARAGRAPHS = [
    {"nickName": "磊哥", "paragraph": "呃，框架啊。", "startTime": "736000", "endTime": "738000"},
    {"nickName": "Zoey", "paragraph": "那这个我们可以先列一个list吧，给你看一下。", "startTime": "738500", "endTime": "748400"},
    {"nickName": "磊哥", "paragraph": "行啊。你可以让AI辅助你一下。", "startTime": "750300", "endTime": "756500"},
]


def _friday_dws(paragraphs=None, *, transcript_error=None, summary="", summary_error=None):
    class FakeDws:
        def list_minutes(self, *, limit):
            return [{"taskUuid": "minutes-1", "title": "每周内容同步"}]

        def get_minutes_todos(self, task_uuid):
            return FRIDAY_TODOS

        def get_all_minutes_transcription(self, task_uuid):
            if transcript_error:
                raise transcript_error
            return {"paragraphs": FRIDAY_PARAGRAPHS if paragraphs is None else paragraphs}

        def get_minutes_summary(self, task_uuid):
            if summary_error:
                raise summary_error
            return {"result": {"fullSummary": summary}}

    return FakeDws()


def test_scan_meeting_todos_hands_over_the_conversation_around_each_action_item(tmp_path):
    """DingTalk leaves executorList empty; the owner is in what was said when the item was extracted."""
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, _friday_dws()) == 1

    [claimed] = store.claim_work_summary_inputs(limit=10)
    summary = json.loads(json.loads(claimed.payload_json)["summary"])
    [excerpt] = summary["transcript_excerpts"]
    assert excerpt["todo"] == "整理访谈问题清单并发给磊哥确认"
    assert "Zoey：那这个我们可以先列一个list吧，给你看一下。" in excerpt["lines"]
    assert excerpt["lines"][0].startswith("磊哥：")


def test_scan_meeting_todos_queues_a_meeting_without_a_transcript_and_keeps_its_digest(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, _friday_dws(paragraphs=[])) == 1
    [claimed] = store.claim_work_summary_inputs(limit=10)
    assert "transcript_excerpts" not in json.loads(json.loads(claimed.payload_json)["summary"])
    # the digest covers the action items only: reading a transcript later does not queue it again
    assert scan_meeting_todos(store, _friday_dws()) == 0


def test_scan_meeting_todos_uses_the_meeting_summary_when_transcript_cannot_be_read(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(
        store,
        _friday_dws(
            transcript_error=RuntimeError("transcript unavailable"),
            summary="会议确认由磊哥负责整理访谈问题清单。",
        ),
    ) == 1

    [claimed] = store.claim_work_summary_inputs(limit=10)
    payload = json.loads(claimed.payload_json)
    summary = json.loads(payload["summary"])
    assert summary["meeting_summary"] == "会议确认由磊哥负责整理访谈问题清单。"
    assert payload["context"]["source_transcript_unavailable"] is True

    from app.task_scanners import MEETING_TODO_SCANNER

    state = store.get_daily_scan_state(MEETING_TODO_SCANNER)
    assert state["last_error"] == ""
    from app.quality_gate import scan_hourly_quality

    report = scan_hourly_quality(store.path)
    assert not any(issue.source == MEETING_TODO_SCANNER for issue in report.violations)


def test_scan_meeting_todos_also_hands_over_the_meetings_own_summary(tmp_path):
    """Derek 2026-09-28: a narrow transcript window can miss a sentence the meeting's own summary states plainly."""
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    full_summary = "行动项：**磊哥**与**周俊杰**负责代码 Review。"

    assert scan_meeting_todos(store, _friday_dws(summary=full_summary)) == 1

    [claimed] = store.claim_work_summary_inputs(limit=10)
    summary = json.loads(json.loads(claimed.payload_json)["summary"])
    assert summary["meeting_summary"] == full_summary


def test_scan_meeting_todos_omits_an_empty_summary(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert scan_meeting_todos(store, _friday_dws()) == 1
    [claimed] = store.claim_work_summary_inputs(limit=10)
    assert "meeting_summary" not in json.loads(json.loads(claimed.payload_json)["summary"])


def test_scan_meeting_todos_retries_when_the_summary_cannot_be_read(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    dws = _friday_dws(summary_error=RuntimeError("summary unavailable"))
    assert scan_meeting_todos(store, dws) == 0
    assert store.claim_work_summary_inputs(limit=10) == []
    from app.task_scanners import MEETING_TODO_SCANNER

    state = store.get_daily_scan_state(MEETING_TODO_SCANNER)
    assert "summary unavailable" in state["last_error"]
    # nothing was recorded as seen, so the next scan reads it again
    assert scan_meeting_todos(store, _friday_dws()) == 1
