from datetime import datetime
from datetime import timedelta
from dataclasses import dataclass
from enum import StrEnum
import importlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, Field

from app.agent_context import AgentTaskContext
from app.agent_contracts import AuditAgentResult
from app.agent_orchestrator import OrchestrationResult
from app.agent_envelope import AgentEnvelope
from app.agent_result import (
    AgentError,
    SideEffectState,
)
from app.agent_effects import LEASE_SECONDS
import app.worker as worker_module
from app.codex_decision import (
    CodexDecisionRunner,
)
from app.channel_gate import (
    ChannelGateResult,
    ChannelGateState,
    DwsChannelGate,
    LarkChannelGate,
    default_channel_gates,
)
from app.corpus import CorpusRecord
from app.dingtalk_models import (
    CodexAction,
    CodexDecision,
    DingTalkConversation,
    DingTalkMessage,
    SensitivityKind,
)
from app.dws_client import (
    DwsCalendarEvent,
    DwsClient,
    DwsDocumentSearchResult,
    DwsError,
    DwsMinutesPermissionRequest,
    DwsOaApprovalCandidate,
    DwsUserProfile,
)
from app.store import AgentRole, AutoReplyStore
from app.worker import (
    DWS_AUTH_LOGIN_STATE_KEY,
    HANDOFF_NOTIFICATION_PREFIX,
    PROCESSING_ACK,
    DingTalkAutoReplyWorker,
)


CONTEXT_HEADER = "上下文消息（自上次回复后的新信息，最多 20 条）:"


class ScriptOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class ScriptOaActionReceipt(BaseModel):
    process_instance_id: str
    task_id: str = ""
    action: str
    remark: str = ""
    result: dict[str, object] = Field(default_factory=dict)


class ScriptResult(BaseModel):
    outcome: ScriptOutcome
    summary: str
    error: AgentError = Field(default_factory=AgentError)
    oa_action_receipt: ScriptOaActionReceipt | None = None


def _get_audit_run(store, task_id: int, execution_generation: str):
    return store.get_agent_run_for_turn(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


def _claim_audit_run(
    store,
    task_id: int,
    execution_generation: str,
    *,
    owner: str,
    **kwargs,
):
    return store.claim_agent_run(
        task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"audit-agent:{task_id}:{execution_generation}",
        owner=owner,
        **kwargs,
    )


class FakeAuthLoginProcess:
    def __init__(self, pid: int = 1234, returncode: int | None = None):
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FixedGate:
    def __init__(self, channel: str, state: ChannelGateState):
        self.channel_name = channel
        self.state = state
        self.calls = 0

    def check(self) -> ChannelGateResult:
        self.calls += 1
        return ChannelGateResult(
            channel=self.channel_name,
            state=self.state,
            reason_code=self.state.value,
        )


@dataclass(frozen=True)
class ScriptedRunResult:
    run_id: int
    result: ScriptResult
    transcript_start_line: int
    transcript_end_line: int
    events: tuple[dict[str, object], ...]


class FakeAgentResultRunner:
    """Persist explicit result scripts behind the orchestrator test seam."""

    def __init__(
        self,
        store: AutoReplyStore,
        scripts: list[
            tuple[
                ScriptResult,
                tuple[dict[str, object], ...],
                str,
                tuple[tuple[str, str, str], ...],
            ]
            | tuple[ScriptResult, tuple[dict[str, object], ...], str]
        ]
        | None = None,
    ) -> None:
        self.store = store
        self.scripts = list(scripts or [])
        self.calls: list[tuple[int, str, AgentTaskContext, str]] = []
        self.owner = "worker-result-agent"

    def run(self, task, context, **_kwargs) -> ScriptedRunResult:
        claim = _claim_audit_run(
            self.store,
            task.id,
            task.execution_generation,
            owner=self.owner,
        )
        assert claim.claimed
        run = claim.run
        if self.scripts:
            script = self.scripts.pop(0)
            result, events, session_id = script[:3]
            receipts = script[3] if len(script) == 4 else ()
        else:
            raise AssertionError(
                "Agent invocation was not explicitly scripted: "
                f"task={task.id} generation={task.execution_generation}"
            )
        self.calls.append(
            (task.id, task.execution_generation, context, run.codex_session_id)
        )
        if not run.codex_session_id:
            run = self.store.set_agent_run_session(
                run.id,
                session_id,
                owner=self.owner,
            )
        for event in events:
            run = self.store.append_agent_run_event(
                run.id,
                event,
                owner=self.owner,
            )
        for operation_id, command_path, command_digest in receipts:
            self.store.record_agent_execution_receipt(
                run.id,
                receipt_id=f"native:{operation_id}:{command_digest}",
                operation_id=operation_id,
                cli="dws",
                command_path=command_path,
                command_digest=command_digest,
                exit_code=0,
                owner=self.owner,
            )
        if result.error.side_effect_state is SideEffectState.UNKNOWN:
            run = self.store.mark_agent_run_unknown(
                run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=len(events),
            )
        elif result.outcome is ScriptOutcome.FAILED:
            run = self.store.fail_agent_run(
                run.id,
                result.error.model_dump(mode="json"),
                owner=self.owner,
                transcript_end_line=len(events),
            )
        else:
            run = self.store.complete_agent_run(
                run.id,
                result.model_dump(mode="json"),
                owner=self.owner,
                side_effect_state=("confirmed" if receipts else "none"),
                transcript_end_line=len(events),
            )
        return ScriptedRunResult(
            run_id=run.id,
            result=result,
            transcript_start_line=run.transcript_start_line,
            transcript_end_line=run.transcript_end_line,
            events=events,
        )


class FailingTaskRunner:
    def __init__(self, error: str) -> None:
        self.error = error
        self.calls = 0

    def run(self, _task, _context, **_kwargs):
        self.calls += 1
        raise RuntimeError(self.error)


class FakeAgentOrchestrator:
    """Adapt existing explicit runner fixtures to the new worker seam."""

    def __init__(self, worker: "DingTalkAutoReplyWorker") -> None:
        self.worker = worker

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        runner = self.worker._test_agent_runner
        assert runner is not None
        run_result = runner.run(task, context)
        result = run_result.result
        if result.error.side_effect_state is SideEffectState.UNKNOWN:
            status = "unknown"
        elif result.outcome is ScriptOutcome.COMPLETED:
            status = "executed"
        elif result.outcome is ScriptOutcome.NO_ACTION:
            status = "no_action"
        elif result.outcome is ScriptOutcome.NEEDS_HUMAN:
            status = "needs_human"
        else:
            status = "failed_retryable" if result.error.retryable else "failed_terminal"

        audit_result = None
        if result.oa_action_receipt is not None:
            receipt = result.oa_action_receipt
            run = self.worker.store.get_agent_run(run_result.run_id)
            assert run is not None
            audit_result = AuditAgentResult.model_validate(
                {
                    "outcome": "executed",
                    "summary": result.summary,
                    "proposal_revision": 0,
                    "side_effect_state": "confirmed",
                    "feedback": None,
                    "external_result": {
                        "operation_id": run.operation_id,
                        "verification_summary": result.summary,
                        "live_result_reference": {
                            "process_instance_id": receipt.process_instance_id,
                            "task_id": receipt.task_id,
                            "action": receipt.action,
                            "remark": receipt.remark,
                            "result": receipt.result,
                        },
                    },
                    "error": {
                        "code": "",
                        "retryable": False,
                        "authorization_required": False,
                    },
                }
            )
        return OrchestrationResult(
            status=status,
            final_run_id=run_result.run_id,
            final_role=AgentRole.AUDIT,
            summary=result.summary,
            error=result.error,
            feedback_cycles=0,
            audit_result=audit_result,
        )


class ScriptedAgentOrchestrator:
    def __init__(self, *results: OrchestrationResult) -> None:
        self.results = list(results)
        self.calls = []

    def process(self, task, context, *, refresh_context) -> OrchestrationResult:
        self.calls.append((task, context))
        return self.results.pop(0)


def explicit_agent_result(
    outcome: ScriptOutcome,
    summary: str,
    *,
    code: str = "",
    retryable: bool = False,
    authorization_required: bool = False,
    side_effect_state: SideEffectState = SideEffectState.NONE,
) -> ScriptResult:
    return ScriptResult(
        outcome=outcome,
        summary=summary,
        error=AgentError(
            code=code,
            retryable=retryable,
            authorization_required=authorization_required,
            side_effect_state=side_effect_state,
        ),
    )


def effect_events(
    call_id: str,
    result: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    started = {
        "type": "item.started",
        "item": {
            "id": call_id,
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    return started, {**started, "type": "item.completed", "result": result}


def agent_runner(worker: DingTalkAutoReplyWorker) -> FakeAgentResultRunner:
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    return runner


def agent_prompt(worker: DingTalkAutoReplyWorker) -> str:
    return agent_runner(worker).calls[0][2].render()


def script_no_action(worker: DingTalkAutoReplyWorker, *, count: int = 1) -> None:
    runner = agent_runner(worker)
    for index in range(count):
        runner.scripts.append(
            (
                explicit_agent_result(
                    ScriptOutcome.NO_ACTION,
                    "测试明确指定无需外部动作。",
                ),
                (),
                f"worker-test-session-{len(runner.scripts) + index + 1}",
            )
        )


def assert_calendar_agent_contract(
    worker: DingTalkAutoReplyWorker,
    dws: "FakeDws",
) -> str:
    context = agent_runner(worker).calls[0][2]
    calendar_materials = [
        material
        for material in context.materials
        if material.kind == "dingtalk_calendar"
    ]
    assert calendar_materials
    assert all(material.read_commands for material in calendar_materials)
    assert all(
        command.startswith("dws calendar event ")
        for material in calendar_materials
        for command in material.read_commands
    )
    assert dws.calendar_event_detail_calls == []
    assert dws.calendar_responses == []
    return context.render()


def script_agent_result(
    worker: DingTalkAutoReplyWorker,
    result: ScriptResult,
    *,
    events: tuple[dict[str, object], ...] = (),
    receipts: tuple[tuple[str, str, str], ...] = (),
    session_id: str = "worker-test-session-explicit",
) -> FakeAgentResultRunner:
    runner = FakeAgentResultRunner(
        worker.store,
        [(result, events, session_id, receipts)],
    )
    worker._test_agent_runner = runner
    return runner


def execution_receipt(
    operation_id: str = "worker-write",
    command_path: str = "chat message send",
    command_digest: str = "a" * 64,
) -> tuple[str, str, str]:
    return (
        operation_id,
        command_path,
        command_digest,
    )


def script_completed_result(
    worker: DingTalkAutoReplyWorker,
    summary: str = "Agent completed and verified the requested action.",
    *,
    operation_id: str = "worker-write",
) -> FakeAgentResultRunner:
    return script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.COMPLETED,
            summary,
            side_effect_state=SideEffectState.CONFIRMED,
        ),
        receipts=(execution_receipt(operation_id),),
    )


def script_calendar_result(
    worker: DingTalkAutoReplyWorker,
    outcome: ScriptOutcome,
    scenario: str,
) -> FakeAgentResultRunner:
    if outcome is ScriptOutcome.COMPLETED:
        return script_completed_result(
            worker,
            summary=scenario,
            operation_id="calendar-action",
        )
    return script_agent_result(
        worker,
        explicit_agent_result(
            outcome,
            scenario,
            code=(
                "calendar_needs_human" if outcome is ScriptOutcome.NEEDS_HUMAN else ""
            ),
        ),
    )


def fixed_channel_gates(
    dingtalk: ChannelGateState = ChannelGateState.READY,
    lark: ChannelGateState = ChannelGateState.READY,
) -> dict[str, FixedGate]:
    return {
        "dingtalk": FixedGate("dingtalk", dingtalk),
        "lark": FixedGate("lark", lark),
    }


def fixed_worker_now() -> datetime:
    return datetime(2026, 5, 13, 10, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


def test_worker_recovery_runtime_config_reads_environment(monkeypatch):
    monkeypatch.setenv("MESSAGE_RECOVERY_INTERVAL", "15m")
    monkeypatch.setenv("FAST_PATH_UNREAD_BACKOFF", "2m")
    monkeypatch.setenv("SINGLE_CHAT_READ_RECOVERY_WINDOW", "6h")
    monkeypatch.setenv("SINGLE_CHAT_READ_RECOVERY_LIMIT", "11")

    importlib.reload(worker_module)

    assert worker_module.MESSAGE_RECOVERY_INTERVAL == timedelta(minutes=15)
    assert worker_module.FAST_PATH_UNREAD_BACKOFF == timedelta(minutes=2)
    assert worker_module.SINGLE_CHAT_READ_RECOVERY_WINDOW == timedelta(hours=6)
    assert worker_module.SINGLE_CHAT_READ_RECOVERY_LIMIT == 11
    for name in (
        "MESSAGE_RECOVERY_INTERVAL",
        "FAST_PATH_UNREAD_BACKOFF",
        "SINGLE_CHAT_READ_RECOVERY_WINDOW",
        "SINGLE_CHAT_READ_RECOVERY_LIMIT",
    ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("FAST_PATH_UNREAD_BACKOFF", "0s")
    importlib.reload(worker_module)


class FakeDws:
    def __init__(
        self,
        conversations: list[DingTalkConversation],
        messages: dict[str, list[DingTalkMessage]],
        unread_messages: dict[str, list[DingTalkMessage]] | None = None,
        read_errors: dict[str, Exception] | None = None,
        unread_errors: dict[str, Exception] | None = None,
        list_error: Exception | None = None,
        mentioned_error: Exception | None = None,
        send_error: Exception | None = None,
        ding_error: Exception | None = None,
        current_user_error: Exception | None = None,
        send_result: dict | None = None,
        client_cids: dict[str, str] | None = None,
    ):
        self.conversations = conversations
        self.messages = self._messages_by_conversation(messages)
        self.unread_messages = self._messages_by_conversation(
            unread_messages or messages
        )
        self.read_errors = read_errors or {}
        self.unread_errors = unread_errors or {}
        self.list_error = list_error
        self.mentioned_error = mentioned_error
        self.send_error = send_error
        self.ding_error = ding_error
        self.text_emotion_error: Exception | None = None
        self.current_user_error = current_user_error
        self.send_result = send_result
        self.docs: dict[str, dict] = {}
        self.doc_infos: dict[str, dict] = {}
        self.aitable_bases: dict[str, dict] = {}
        self.aitable_tables: dict[tuple[str, tuple[str, ...]], dict] = {}
        self.aitable_records: dict[tuple[str, str], dict] = {}
        self.document_search_results: dict[str, list[DwsDocumentSearchResult]] = {}
        self.download_docs: dict[str, dict | Exception] = {}
        self.drive_file_downloads: dict[str, bytes | Exception] = {}
        self.resource_download_urls: dict[
            tuple[str, str, str, str],
            dict | Exception,
        ] = {}
        self.robot_message_file_downloads: dict[str, dict | Exception] = {}
        self.doc_info_calls: list[str] = []
        self.read_doc_calls: list[str] = []
        self.get_aitable_base_calls: list[str] = []
        self.get_aitable_tables_calls: list[tuple[str, tuple[str, ...] | None]] = []
        self.query_aitable_record_calls: list[tuple[str, str, int]] = []
        self.search_document_calls: list[tuple[str, int]] = []
        self.download_doc_calls: list[str] = []
        self.drive_file_download_calls: list[tuple[str, str, str]] = []
        self.resource_download_url_calls: list[tuple[str, str, str, str]] = []
        self.robot_message_file_download_calls: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.reply_messages: list[tuple[str, str, str, str]] = []
        self.mail_replies: list[tuple[str, str, str, str]] = []
        self.mail_reply_error: Exception | None = None
        self.created_markdown_docs: list[tuple[str, str]] = []
        self.doc_editor_permissions: list[tuple[str, list[str]]] = []
        self.doc_editor_permission_error: Exception | None = None
        self.send_visible = True
        self.reply_visible = True
        self.message_emojis: list[tuple[str, str, str]] = []
        self.message_text_emotions: list[tuple[str, str, str, str, str, str]] = []
        self.created_text_emotions: list[tuple[str, str, str]] = []
        self.sent_at_users: list[list[str]] = []
        self.direct_user_ids: list[str | None] = []
        self.direct_open_dingtalk_ids: list[str | None] = []
        self.send_attempt_count = 0
        self.dings: list[str] = []
        self.mentioned_messages: dict[str, list[DingTalkMessage]] = {
            conversation_id: [
                message
                for message in messages
                if "@Alex Chen" in message.content
                or "@所有人" in message.content
                or "@All" in message.content
            ]
            for conversation_id, messages in self.unread_messages.items()
        }
        self.broadcast_messages: dict[str, list[DingTalkMessage]] = {
            conversation_id: [
                message
                for message in messages
                if "@所有人" in message.content or "@All" in message.content
            ]
            for conversation_id, messages in self.unread_messages.items()
        }
        self.robot_direct_messages: dict[str, list[DingTalkMessage]] = {}
        self.robot_direct_message_reads = 0
        self.bot_direct_messages: list[tuple[str, str]] = []
        self.user_departments: dict[str, set[str]] = {}
        self.user_profiles: dict[str, DwsUserProfile] = {}
        self.user_profile_calls: list[str] = []
        self.recent_message_reads: list[str] = []
        self.unread_message_reads: list[str] = []
        self.messages_by_id_reads: list[list[str]] = []
        self.hr_users: set[str] = set()
        self.hr_user_calls: list[str] = []
        self.manager_chains: dict[str, list[str]] = {}
        self.manager_chain_calls: list[tuple[str, str]] = []
        self.user_department_calls: list[str] = []
        self.resolved_senders: dict[str, str] = {}
        self.current_user_id = "principal-user-1"
        self.current_user_checks: list[str] = []
        self.calendar_invites: dict[str, DwsCalendarEvent | None] = {}
        self.calendar_events: dict[str, list[DwsCalendarEvent]] = {}
        self.calendar_event_details: dict[str, DwsCalendarEvent | None] = {}
        self.calendar_event_detail_calls: list[str] = []
        self.calendar_responses: list[tuple[str, str]] = []
        self.calendar_response_error: Exception | None = None
        self.calendar_response_updates_details = True
        self.minutes_permission_requests: dict[
            str, DwsMinutesPermissionRequest | None
        ] = {}
        self.added_minutes_permissions: list[DwsMinutesPermissionRequest] = []
        self.minutes_infos: dict[str, dict | Exception] = {}
        self.minutes_summaries: dict[str, dict] = {}
        self.minutes_todos: dict[str, dict] = {}
        self.minutes_transcriptions: dict[str, dict] = {}
        self.minutes_info_calls: list[str] = []
        self.minutes_summary_calls: list[str] = []
        self.minutes_todo_calls: list[str] = []
        self.minutes_transcription_calls: list[tuple[str, str]] = []
        self.doc_comments: list[tuple[str, str]] = []
        self.doc_comment_result: dict = {"result": {"commentKey": "comment-1"}}
        self.doc_comment_error: Exception | None = None
        self.oa_approval_actions: list[tuple[str, str, str, str]] = []
        self.oa_approval_action_result: dict = {"errcode": 0, "errmsg": "ok"}
        self.oa_approval_action_error: Exception | None = None
        self.oa_approval_comments: list[tuple[str, str]] = []
        self.oa_approval_comment_result: dict = {"errcode": 0, "errmsg": "ok"}
        self.oa_approval_comment_error: Exception | None = None
        self.pending_oa_approvals: list[DwsOaApprovalCandidate] = []
        self.oa_approval_details: dict[str, dict | Exception] = {}
        self.oa_approval_records: dict[str, dict | Exception] = {}
        self.oa_approval_tasks: dict[str, dict | Exception] = {
            "proc-1": {
                "result": {
                    "tasks": [
                        {
                            "taskId": "task-1",
                            "status": "RUNNING",
                            "userId": "principal-user-1",
                        }
                    ]
                }
            }
        }
        self.openapi_oa_details: dict[str, dict | Exception] = {
            "proc-1": {
                "process_instance": {
                    "originator_userid": "applicant-user-1",
                    "tasks": [
                        {
                            "taskid": "task-1",
                            "task_status": "RUNNING",
                            "userid": "principal-user-1",
                        }
                    ],
                }
            }
        }
        self.oa_attachment_downloads: dict[tuple[str, str], bytes | Exception] = {}
        self.download_oa_attachment_calls: list[tuple[str, str]] = []
        self.upgrade_check_response: dict = {"needs_upgrade": False}
        self.upgrade_error: Exception | None = None
        self.upgrade_check_error: Exception | None = None
        self.upgrade_install_error: Exception | None = None
        self.upgrade_check_calls = 0
        self.upgrade_calls = 0
        self.list_unread_calls = 0
        self.auth_status_response: dict = {
            "authenticated": True,
            "token_valid": True,
            "refresh_token_valid": True,
        }
        self.auth_status_error: Exception | None = None
        self.auth_status_calls = 0
        self.auth_login_processes: list[FakeAuthLoginProcess] = [FakeAuthLoginProcess()]
        self.auth_login_starts = 0
        self.pat_authorization_processes: list[FakeAuthLoginProcess] = [
            FakeAuthLoginProcess(pid=2234)
        ]
        self.pat_authorization_scopes: list[list[str]] = []
        self.client_cids = client_cids or {}
        self.client_cid_calls: list[str] = []

    @staticmethod
    def _messages_by_conversation(
        messages: dict[str, list[DingTalkMessage]],
    ) -> dict[str, list[DingTalkMessage]]:
        return {
            conversation_id: [
                message.model_copy(update={"open_conversation_id": conversation_id})
                for message in conversation_messages
            ]
            for conversation_id, conversation_messages in messages.items()
        }

    def list_unread_conversations(self, count: int) -> list[DingTalkConversation]:
        self.list_unread_calls += 1
        assert count == 50
        if self.list_error:
            raise self.list_error
        return self.conversations

    def auth_status(self) -> dict:
        self.auth_status_calls += 1
        if self.auth_status_error:
            raise self.auth_status_error
        return self.auth_status_response

    def check_upgrade(self) -> dict:
        self.upgrade_check_calls += 1
        error = self.upgrade_check_error or self.upgrade_error
        if error:
            raise error
        return self.upgrade_check_response

    def upgrade(self) -> str:
        self.upgrade_calls += 1
        error = self.upgrade_install_error or self.upgrade_error
        if error:
            raise error
        return "upgraded"

    def start_auth_login(self) -> FakeAuthLoginProcess:
        self.auth_login_starts += 1
        return self.auth_login_processes.pop(0)

    def start_pat_authorization(self, scopes: list[str]) -> FakeAuthLoginProcess:
        self.pat_authorization_scopes.append(list(scopes))
        return self.pat_authorization_processes.pop(0)

    def get_current_user_id(self) -> str:
        return self.current_user_id

    def search_department_ids(self, query: str) -> set[str]:
        del query
        return {"hr-dept"}

    def client_conversation_id(self, open_conversation_id: str) -> str:
        self.client_cid_calls.append(open_conversation_id)
        return self.client_cids.get(open_conversation_id, "")

    def list_department_member_profiles(
        self, department_ids: list[str]
    ) -> list[DwsUserProfile]:
        del department_ids
        return [
            profile
            for profile in self.user_profiles.values()
            if "hr-dept" in profile.department_ids
        ]

    def get_user_profiles(self, user_ids: list[str]) -> list[DwsUserProfile]:
        return [
            self.user_profiles.get(
                user_id,
                DwsUserProfile(
                    user_id=user_id,
                    name=user_id,
                    department_ids={"dept-1"},
                ),
            )
            for user_id in user_ids
        ]

    def read_recent_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        self.recent_message_reads.append(conversation.open_conversation_id)
        if conversation.open_conversation_id in self.read_errors:
            raise self.read_errors[conversation.open_conversation_id]
        return self.messages.get(conversation.open_conversation_id, [])

    def read_unread_messages(
        self, conversation: DingTalkConversation
    ) -> list[DingTalkMessage]:
        self.unread_message_reads.append(conversation.open_conversation_id)
        if conversation.open_conversation_id in self.unread_errors:
            raise self.unread_errors[conversation.open_conversation_id]
        return self.unread_messages.get(conversation.open_conversation_id, [])

    def list_messages_by_ids(self, message_ids: list[str]) -> list[DingTalkMessage]:
        self.messages_by_id_reads.append(list(message_ids))
        wanted = set(message_ids)
        seen: set[str] = set()
        result: list[DingTalkMessage] = []
        sources = (
            self.messages,
            self.unread_messages,
            self.mentioned_messages,
            self.broadcast_messages,
            self.robot_direct_messages,
        )
        for source in sources:
            for messages in source.values():
                for message in messages:
                    if (
                        message.open_message_id in wanted
                        and message.open_message_id not in seen
                    ):
                        result.append(message)
                        seen.add(message.open_message_id)
        return [
            message
            for message_id in message_ids
            for message in result
            if message.open_message_id == message_id
        ]

    def read_mentioned_messages(
        self,
        conversation: DingTalkConversation | None = None,
        limit: int = 50,
        cursor: str = "0",
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        if self.mentioned_error:
            raise self.mentioned_error
        if conversation is None:
            return [
                message
                for messages in self.mentioned_messages.values()
                for message in messages
            ]
        return self.mentioned_messages.get(conversation.open_conversation_id, [])

    def read_broadcast_messages(
        self,
        aliases: tuple[str, ...],
        limit: int = 100,
        lookback_hours: int = 24,
    ) -> list[DingTalkMessage]:
        del aliases, limit, lookback_hours
        return [
            message
            for messages in self.broadcast_messages.values()
            for message in messages
        ]

    def read_robot_direct_messages(
        self,
        *,
        lookback_minutes: int = 30,
        limit: int = 100,
    ) -> list[DingTalkMessage]:
        del lookback_minutes, limit
        self.robot_direct_message_reads += 1
        return [
            message
            for messages in self.robot_direct_messages.values()
            for message in messages
        ]

    def read_doc(self, node: str) -> dict:
        self.read_doc_calls.append(node)
        if node not in self.docs:
            raise DwsError(f"doc not found: {node}")
        return self.docs[node]

    def doc_info(self, node: str) -> dict:
        self.doc_info_calls.append(node)
        if node in self.doc_infos:
            result = self.doc_infos[node]
            if isinstance(result, DwsError):
                raise result
            return result
        if node in self.docs:
            return {
                "contentType": "ALIDOC",
                "extension": "adoc",
                "name": self.docs[node].get("title", "钉钉文档"),
                "nodeId": node.rsplit("/", 1)[-1],
            }
        raise DwsError(f"doc info not found: {node}")

    def get_aitable_base(self, base_id: str) -> dict:
        self.get_aitable_base_calls.append(base_id)
        if base_id not in self.aitable_bases:
            raise DwsError(f"aitable base not found: {base_id}")
        return self.aitable_bases[base_id]

    def get_aitable_tables(
        self, base_id: str, table_ids: list[str] | None = None
    ) -> dict:
        key = (base_id, tuple(table_ids or ()))
        self.get_aitable_tables_calls.append(
            (base_id, tuple(table_ids) if table_ids else None)
        )
        if key not in self.aitable_tables:
            raise DwsError(f"aitable table not found: {base_id}")
        return self.aitable_tables[key]

    def query_aitable_records(
        self, base_id: str, table_id: str, limit: int = 10
    ) -> dict:
        self.query_aitable_record_calls.append((base_id, table_id, limit))
        return self.aitable_records.get((base_id, table_id), {"data": {"records": []}})

    def search_documents(
        self, query: str, page_size: int = 5
    ) -> list[DwsDocumentSearchResult]:
        self.search_document_calls.append((query, page_size))
        return self.document_search_results.get(query, [])

    def download_doc(self, node: str) -> dict:
        self.download_doc_calls.append(node)
        result = self.download_docs.get(node)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def download_drive_file(
        self,
        node: str,
        *,
        file_name: str = "download",
        space_id: str = "",
    ) -> bytes:
        self.drive_file_download_calls.append((node, file_name, space_id))
        result = self.drive_file_downloads.get(node)
        if isinstance(result, Exception):
            raise result
        return result or b""

    def get_resource_download_url(
        self,
        open_conversation_id: str,
        open_message_id: str,
        resource_id: str,
        resource_type: str,
    ) -> dict:
        key = (
            open_conversation_id,
            open_message_id,
            resource_id,
            resource_type,
        )
        self.resource_download_url_calls.append(key)
        result = self.resource_download_urls.get(key)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def download_robot_message_file(self, download_code: str) -> dict:
        self.robot_message_file_download_calls.append(download_code)
        result = self.robot_message_file_downloads.get(download_code)
        if isinstance(result, Exception):
            raise result
        return result or {}

    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
        user_id: str | None = None,
        open_dingtalk_id: str | None = None,
    ) -> None:
        del at_open_dingtalk_names
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.sent.append((conversation_id or "", text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(user_id)
        self.direct_open_dingtalk_ids.append(open_dingtalk_id)
        if conversation_id and self.send_visible:
            self._append_visible_message(conversation_id, text)
        return self.send_result

    def reply_message(
        self,
        conversation_id: str,
        ref_message_id: str,
        ref_sender_open_dingtalk_id: str,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> None:
        del at_open_dingtalk_names
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.reply_messages.append(
            (conversation_id, ref_message_id, ref_sender_open_dingtalk_id, text)
        )
        self.sent.append((conversation_id, text))
        self.sent_at_users.append(at_users or [])
        self.direct_user_ids.append(None)
        self.direct_open_dingtalk_ids.append(None)
        if self.reply_visible:
            self._append_visible_message(conversation_id, text)
        return self.send_result

    def create_markdown_doc(self, name: str, content: str) -> dict:
        self.created_markdown_docs.append((name, content))
        index = len(self.created_markdown_docs)
        return {
            "result": {
                "nodeId": f"doc-{index}",
                "url": f"https://alidocs.dingtalk.com/i/nodes/doc-{index}",
                "name": name,
            }
        }

    def reply_mail(
        self, mailbox: str, message_id: str, subject: str, content: str
    ) -> dict:
        if self.mail_reply_error:
            raise self.mail_reply_error
        self.mail_replies.append((mailbox, message_id, subject, content))
        return {"success": True, "messageId": "reply-mail-1"}

    def build_mail_reply_command(
        self, *, mailbox: str, message_id: str, subject: str, content: str
    ) -> list[str]:
        return [
            "dws",
            "mail",
            "message",
            "reply",
            "--from",
            mailbox,
            "--id",
            message_id,
            "--subject",
            subject,
            "--content",
            content,
            "--format",
            "json",
            "--yes",
        ]

    def add_doc_editor_permission(self, node: str, user_ids: list[str]) -> dict:
        if self.doc_editor_permission_error:
            raise self.doc_editor_permission_error
        self.doc_editor_permissions.append((node, user_ids))
        return {"success": True, "nodeId": node, "userIds": user_ids}

    def _append_visible_message(self, conversation_id: str, text: str) -> None:
        visible = DingTalkMessage(
            open_conversation_id=conversation_id,
            open_message_id=f"sent-{len(self.sent)}",
            conversation_title="CEO-2 管理群",
            single_chat=False,
            sender_name="磊哥",
            sender_open_dingtalk_id="principal-open-1",
            create_time="2026-05-13 18:00:00",
            content=text,
        )
        self.messages.setdefault(conversation_id, []).insert(0, visible)

    def send_reply_to_trigger(
        self,
        conversation,
        trigger,
        text: str,
        at_users: list[str] | None = None,
        at_open_dingtalk_ids: list[str] | None = None,
        at_open_dingtalk_names: list[str] | None = None,
    ) -> None:
        return self.reply_message(
            conversation.open_conversation_id,
            trigger.open_message_id,
            trigger.sender_open_dingtalk_id,
            text,
            at_users=at_users,
            at_open_dingtalk_ids=at_open_dingtalk_ids,
            at_open_dingtalk_names=at_open_dingtalk_names,
        )

    def send_direct_message_by_bot(self, user_id: str, text: str) -> dict:
        self.send_attempt_count += 1
        if self.send_error:
            raise self.send_error
        self.bot_direct_messages.append((user_id, text))
        self.sent.append(("", text))
        self.sent_at_users.append([])
        self.direct_user_ids.append(user_id)
        self.direct_open_dingtalk_ids.append(None)
        return self.send_result or {"success": True}

    def add_message_emoji(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str,
    ) -> dict:
        self.message_emojis.append((conversation_id, message_id, emoji))
        return {"success": True}

    def add_message_text_emotion(
        self,
        conversation_id: str,
        message_id: str,
        *,
        text: str,
        emotion_id: str,
        emotion_name: str,
        background_id: str,
    ) -> dict:
        if self.text_emotion_error:
            raise self.text_emotion_error
        self.message_text_emotions.append(
            (conversation_id, message_id, text, emotion_id, emotion_name, background_id)
        )
        return {"success": True}

    def create_message_text_emotion(
        self,
        *,
        text: str,
        emotion_name: str,
        background_id: str = "",
    ) -> dict:
        if self.text_emotion_error:
            raise self.text_emotion_error
        self.created_text_emotions.append((text, emotion_name, background_id))
        return {
            "emotionId": f"created-{len(self.created_text_emotions)}",
            "backgroundId": "created-bg",
        }

    def ding_self(self, text: str) -> None:
        if self.ding_error:
            raise self.ding_error
        self.dings.append(text)

    def resolve_message_sender(self, message: DingTalkMessage) -> str:
        if message.sender_user_id:
            return message.sender_user_id
        if message.sender_open_dingtalk_id in self.resolved_senders:
            return self.resolved_senders[message.sender_open_dingtalk_id]
        raise RuntimeError("sender not resolved")

    def get_user_profile(self, user_id: str) -> DwsUserProfile:
        self.user_profile_calls.append(user_id)
        if user_id not in self.user_profiles:
            raise DwsError(f"user profile not found: {user_id}")
        return self.user_profiles[user_id]

    def is_hr_user(self, user_id: str) -> bool:
        self.hr_user_calls.append(user_id)
        return user_id in self.hr_users

    def user_in_manager_chain(self, manager_user_id: str, subject_user_id: str) -> bool:
        self.manager_chain_calls.append((manager_user_id, subject_user_id))
        return manager_user_id in self.manager_chains.get(subject_user_id, [])

    def get_user_department_ids(self, user_id: str) -> set[str]:
        self.user_department_calls.append(user_id)
        if user_id not in self.user_departments:
            raise RuntimeError("department not resolved")
        return self.user_departments[user_id]

    def is_current_user_message(self, message: DingTalkMessage) -> bool:
        self.current_user_checks.append(message.sender_name)
        if self.current_user_error:
            raise self.current_user_error
        return message.sender_user_id == self.current_user_id

    def calendar_invite_from_message(
        self, message: DingTalkMessage
    ) -> DwsCalendarEvent | None:
        if message.raw_payload:
            event = DwsClient._find_calendar_event_in_payload(message.raw_payload)
            if event is not None:
                return event
        return self.calendar_invites.get(message.open_message_id)

    def list_calendar_events(self, start: str, end: str) -> list[DwsCalendarEvent]:
        return self.calendar_events.get(f"{start}|{end}", [])

    def get_calendar_event(self, event_id: str) -> DwsCalendarEvent | None:
        self.calendar_event_detail_calls.append(event_id)
        return self.calendar_event_details.get(event_id)

    def respond_calendar_event(self, event_id: str, response_status: str) -> dict:
        self.calendar_responses.append((event_id, response_status))
        if self.calendar_response_error:
            raise self.calendar_response_error
        if self.calendar_response_updates_details:
            existing = self.calendar_event_details.get(event_id)
            if existing is not None:
                self.calendar_event_details[event_id] = existing.model_copy(
                    update={"self_response_status": response_status}
                )
        return {"success": True}

    def minutes_permission_request_from_message(
        self, message: DingTalkMessage
    ) -> DwsMinutesPermissionRequest | None:
        return self.minutes_permission_requests.get(message.open_message_id)

    def add_minutes_member_permission(
        self, request: DwsMinutesPermissionRequest
    ) -> dict:
        self.added_minutes_permissions.append(request)
        return {"success": True}

    def get_minutes_info(self, task_uuid: str) -> dict:
        self.minutes_info_calls.append(task_uuid)
        result = self.minutes_infos.get(task_uuid)
        if isinstance(result, Exception):
            raise result
        if result is not None:
            return result
        return self.minutes_infos.get(
            task_uuid,
            {"result": {"taskUuid": task_uuid, "title": "静默会"}},
        )

    def get_minutes_summary(self, task_uuid: str) -> dict:
        self.minutes_summary_calls.append(task_uuid)
        return self.minutes_summaries.get(task_uuid, {"result": {}})

    def get_minutes_todos(self, task_uuid: str) -> dict:
        self.minutes_todo_calls.append(task_uuid)
        return self.minutes_todos.get(task_uuid, {"result": {}})

    def get_minutes_transcription(
        self,
        task_uuid: str,
        *,
        next_token: str = "",
    ) -> dict:
        self.minutes_transcription_calls.append((task_uuid, next_token))
        return self.minutes_transcriptions.get(task_uuid, {"result": {}})

    def create_doc_comment(self, node_id: str, content: str) -> dict:
        self.doc_comments.append((node_id, content))
        if self.doc_comment_error:
            raise self.doc_comment_error
        return self.doc_comment_result

    def execute_oa_approval_action(
        self,
        process_instance_id: str,
        task_id: str,
        action: str,
        remark: str,
    ) -> dict:
        self.oa_approval_actions.append((process_instance_id, task_id, action, remark))
        if self.oa_approval_action_error:
            raise self.oa_approval_action_error
        return self.oa_approval_action_result

    def comment_oa_approval(
        self,
        process_instance_id: str,
        text: str,
    ) -> dict:
        self.oa_approval_comments.append((process_instance_id, text))
        if self.oa_approval_comment_error:
            raise self.oa_approval_comment_error
        return self.oa_approval_comment_result

    def list_pending_oa_approvals(
        self, page: int = 1, size: int = 30
    ) -> list[DwsOaApprovalCandidate]:
        del page, size
        return self.pending_oa_approvals

    def read_oa_approval_detail(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_details.get(
            process_instance_id,
            {"result": {"formValueVOS": [{"details": []}]}},
        )
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_approval_records(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_records.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_approval_tasks(self, process_instance_id: str) -> dict:
        payload = self.oa_approval_tasks.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def read_oa_process_instance_openapi(self, process_instance_id: str) -> dict:
        payload = self.openapi_oa_details.get(process_instance_id, {})
        if isinstance(payload, Exception):
            raise payload
        return payload

    def download_oa_process_attachment(
        self,
        process_instance_id: str,
        file_id: str,
    ) -> bytes:
        self.download_oa_attachment_calls.append((process_instance_id, file_id))
        payload = self.oa_attachment_downloads.get((process_instance_id, file_id), b"")
        if isinstance(payload, Exception):
            raise payload
        return payload


class FakeCodex:
    def __init__(
        self,
        decision: CodexDecision,
        last_session_id: str | None = None,
        next_session_id: str | None = None,
        audit_tool_events: list[dict[str, str]] | None = None,
        transcript_start_line: int = 0,
        transcript_end_line: int = 0,
        before_decide=None,
    ):
        self.decision = decision
        self.last_session_id = last_session_id
        self.next_session_id = next_session_id
        self.last_audit_tool_events = audit_tool_events or []
        self.last_transcript_start_line = transcript_start_line
        self.last_transcript_end_line = transcript_end_line
        self.before_decide = before_decide
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.image_bytes_calls: list[list[bytes]] = []

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ) -> CodexDecision:
        if self.before_decide is not None:
            self.before_decide(prompt, session_id)
        paths = image_paths or []
        self.image_bytes_calls.append([path.read_bytes() for path in paths])
        self.calls.append((prompt, session_id, paths))
        if self.next_session_id is not None:
            self.last_session_id = self.next_session_id
        return self.decision


class FakeEnvelopeCodex:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.last_session_id = "session-envelope"
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ):
        self.calls.append((prompt, session_id, image_paths or []))
        return self.envelope


class SequencedFakeCodex:
    def __init__(self, decisions: list[CodexDecision]):
        self.decisions = decisions
        self.calls: list[tuple[str, str | None, list[Path]]] = []
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ) -> CodexDecision:
        self.calls.append((prompt, session_id, image_paths or []))
        self.last_session_id = session_id or self.last_session_id or "session-1"
        return self.decisions[len(self.calls) - 1]


def final_sent(dws: FakeDws) -> list[tuple[str, str]]:
    return [sent for sent in dws.sent if sent[1] != PROCESSING_ACK]


def final_sent_at_users(dws: FakeDws) -> list[list[str]]:
    return [
        at_users
        for sent, at_users in zip(dws.sent, dws.sent_at_users)
        if sent[1] != PROCESSING_ACK
    ]


def final_direct_user_ids(dws: FakeDws) -> list[str | None]:
    return [
        user_id
        for sent, user_id in zip(dws.sent, dws.direct_user_ids)
        if sent[1] != PROCESSING_ACK
    ]


def final_direct_open_dingtalk_ids(dws: FakeDws) -> list[str | None]:
    return [
        open_dingtalk_id
        for sent, open_dingtalk_id in zip(dws.sent, dws.direct_open_dingtalk_ids)
        if sent[1] != PROCESSING_ACK
    ]


def conversation(single_chat: bool = False) -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="Friday",
        single_chat=single_chat,
        unread_point=1,
    )


def message(
    content: str,
    message_id: str = "msg-1",
    single_chat: bool = False,
    quoted_content: str | None = None,
    sender_user_id: str | None = "sender-user-1",
    message_type: str | None = None,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="Friday",
        single_chat=single_chat,
        sender_name="周俊杰",
        sender_open_dingtalk_id="sender-1",
        sender_user_id=sender_user_id,
        message_type=message_type,
        create_time="2026-05-13 18:00:00",
        content=content,
        quoted_message_id="quoted-1" if quoted_content else None,
        quoted_content=quoted_content,
    )


def principal_message(
    content: str,
    message_id: str = "principal-msg-1",
    create_time: str = "2026-05-13 18:00:01",
) -> DingTalkMessage:
    msg = message(
        content=content,
        message_id=message_id,
        sender_user_id="principal-user-1",
    )
    msg.create_time = create_time
    return msg


def make_worker(
    tmp_path: Path,
    dws: FakeDws,
    codex: FakeCodex,
    monkeypatch,
    style_profile: str = "",
    style_records: list[CorpusRecord] | None = None,
    dry_run: bool = False,
    max_task_attempts: int = 3,
    fast_path_unread_backoff: timedelta = timedelta(0),
    channel_gates=None,
    agent_orchestrator=None,
    scripted_runner=None,
) -> DingTalkAutoReplyWorker:
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    monkeypatch.setattr(
        "app.worker.FAST_PATH_UNREAD_BACKOFF",
        fast_path_unread_backoff,
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user-1")
    scripted_runner = scripted_runner or FakeAgentResultRunner(store)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=dry_run,
        style_profile=style_profile,
        style_records=style_records,
        now_provider=fixed_worker_now,
        max_task_attempts=max_task_attempts,
        channel_gates=channel_gates or fixed_channel_gates(),
        agent_orchestrator=agent_orchestrator,
    )
    worker._test_agent_runner = scripted_runner
    if agent_orchestrator is None:
        worker.agent_orchestrator = FakeAgentOrchestrator(worker)
    return worker


def test_worker_defaults_to_real_channel_gates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    monkeypatch.setattr(worker_module, "default_channel_gates", default_channel_gates)
    worker = worker_module.DingTalkAutoReplyWorker(
        store=AutoReplyStore(tmp_path / "worker.sqlite3"),
        dws=FakeDws([], {}),
        codex=FakeCodex([]),
    )

    assert isinstance(worker.channel_gates["dingtalk"], DwsChannelGate)
    assert isinstance(worker.channel_gates["lark"], LarkChannelGate)


def test_notification_url_includes_attempt_id(tmp_path, monkeypatch):
    dws = FakeDws(
        conversations=[conversation(single_chat=True)],
        messages={},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    url = worker._notification_url(conversation(single_chat=True), attempt_id=123)

    assert url == (
        "http://127.0.0.1:8765/open-dingtalk?conversation_id=cid-1&attempt_id=123"
    )


def test_run_once_with_zero_batches_is_noop(tmp_path, monkeypatch):
    dws = FakeDws(
        conversations=[conversation(single_chat=True)],
        messages={},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    worker.run_once(max_batches=0)

    assert dws.upgrade_check_calls == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.list_errors() == []


def developer_instructions_from_command(command: list[str]) -> str:
    for index, item in enumerate(command):
        if item != "-c":
            continue
        value = command[index + 1]
        if value.startswith("developer_instructions="):
            return json.loads(value.split("=", 1)[1])
    raise AssertionError("developer_instructions config missing")


def write_profile_for_consumer_test(tmp_path: Path, monkeypatch) -> str:
    profile = tmp_path / "profiles" / "work_profile.md"
    content = """# Alex Work Profile

## 核心心智模型

### 模型1: 结果闭环高于动作勤奋

**一句话**：不要基于一句话拍板，先看材料是否完整、结果是否可验证。

## 决策启发式

1. **材料不完整时先追问，不拍板**：审批、候选人、客户、方案、PPT、预算缺正文或附件时，不给最终判断。
   - 应用场景：审批、招聘、客户材料、文档 review、最终版确认。
   - 案例：需要本人确认最终版或审批时，分身只 handoff，不代替承诺。

## 表达DNA

- 节奏：先给结论，再给原因和下一步；材料不足时直接收敛到一个追问。

## 诚实边界

- 不替 Alex 做最终人事、审批、财务、法律或客户关键承诺。
- 不声称 Alex 已经做了现实动作。
- 材料不足时不编造结论。
"""
    profile.parent.mkdir(parents=True)
    profile.write_text(content, encoding="utf-8")
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    return content


def test_consumer_codex_command_injects_work_profile_content(
    tmp_path: Path, monkeypatch
):
    profile_content = write_profile_for_consumer_test(tmp_path, monkeypatch)
    seen_instructions = []

    def executor(command: list[str], prompt: str) -> str:
        seen_instructions.append(developer_instructions_from_command(command))
        return AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "ask_clarifying_question",
                    "text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                    "sensitivity_kind": "external_candidate",
                },
                "system_actions": [],
                "domain_payload": {
                    "candidate_context_known": True,
                    "candidate_department_ids": ["dept-candidate"],
                },
                "audit": {
                    "summary": "仅根据当前消息判断，材料不足，需要追问。",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        ).model_dump_json()

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人可以推进吗？")]},
    )
    dws.user_departments["sender-user-1"] = {"dept-candidate"}
    codex = CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        codex_home=tmp_path,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "缺少岗位要求和候选人简历，需补充材料。",
            code="candidate_material_missing",
        ),
    )

    worker.run_once()

    assert seen_instructions == []
    assert len(runner.calls) == 1
    context = runner.calls[0][2]
    assert "这个候选人可以推进吗" in context.trigger_text
    assert profile_content not in context.render()
    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None and attempt.send_status == "needs_human"


def test_consumer_uses_profile_to_ask_for_missing_candidate_materials(
    tmp_path: Path, monkeypatch
):
    write_profile_for_consumer_test(tmp_path, monkeypatch)

    def executor(command: list[str], prompt: str) -> str:
        instructions = developer_instructions_from_command(command)
        assert "Profile 内容:" in instructions
        assert "材料不完整时先追问，不拍板" in instructions
        assert "这个候选人可以推进吗" in prompt
        return AgentEnvelope.model_validate(
            {
                "kind": "reply",
                "user_response": {
                    "mode": "ask_clarifying_question",
                    "text": "先把岗位要求和候选人简历补齐，我再判断是否推进。",
                    "sensitivity_kind": "external_candidate",
                },
                "system_actions": [],
                "domain_payload": {
                    "candidate_context_known": True,
                    "candidate_department_ids": ["dept-candidate"],
                },
                "audit": {
                    "summary": "仅根据当前消息判断，缺少岗位要求和简历内容，按 profile 先追问材料。",
                    "documents": [],
                    "confidence": 0.8,
                },
            }
        ).model_dump_json()

    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人可以推进吗？")]},
    )
    dws.user_departments["sender-user-1"] = {"dept-candidate"}
    codex = CodexDecisionRunner(
        workspace=tmp_path,
        executor=executor,
        codex_home=tmp_path,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "缺少岗位要求和候选人简历，需补充材料。",
            code="candidate_material_missing",
        ),
    )

    worker.run_once()

    assert len(runner.calls) == 1
    assert "这个候选人可以推进吗" in runner.calls[0][2].trigger_text
    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        "cid-1",
        "msg-1",
    )
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "candidate_material_missing"


def test_group_without_principal_mention_does_not_call_codex_or_send(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([conversation()], {"cid-1": [message("同步一下进展")]})
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_producer_does_not_call_dws_when_gate_is_not_ready(tmp_path, monkeypatch):
    dws = FakeDws([], {})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        channel_gates={
            "dingtalk": FixedGate("dingtalk", ChannelGateState.NEEDS_LOGIN),
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )

    assert worker.produce_once() == 0
    assert dws.list_unread_calls == 0
    assert dws.upgrade_check_calls == 0
    assert dws.auth_login_starts == 1


def test_required_channels_for_task_detects_referenced_channel_capabilities(
    tmp_path, monkeypatch
):
    worker = make_worker(tmp_path, FakeDws([], {}), FakeCodex([]), monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Derek",
        trigger_text="read the referenced docs",
        trigger_message_json=json.dumps(
            {
                "content": "https://example.feishu.cn/docx/abc",
                "quoted_content": "https://alidocs.dingtalk.com/i/nodes/xyz",
            }
        ),
        channel="custom",
    )
    task = worker.store.peek_reply_tasks(limit=1, channel="custom")[0]

    assert worker.required_channels_for_task(task) == {
        "custom",
        "dingtalk",
        "lark",
    }


def test_produce_once_records_list_unread_failure_without_crashing(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.count_errors() == 0
    assert notifications == []
    assert codex.calls == []


def test_produce_once_suppresses_transient_list_unread_notification(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("transient discovery timeout", code="6"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0
    assert notifications == []
    assert worker.store.count_errors() == 0

    assert worker.produce_once() == 0

    assert notifications == []
    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state(
            "dws_transient_error_count:list_unread_conversations"
        )
        or "{}"
    )
    assert state["count"] == 3


def test_produce_once_clears_transient_list_unread_error_after_success(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("transient discovery timeout", code="6"))
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    dws.list_error = None
    assert worker.produce_once() == 0

    assert notifications == []
    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state(
            "dws_transient_error_count:list_unread_conversations"
        )
        or "{}"
    )
    assert state["count"] == 0
    assert state["last_error"] == ""


def test_read_conversation_messages_suppresses_transient_errors(
    tmp_path: Path, monkeypatch
):
    transient_error = DwsError("transient discovery timeout", code="6")
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": []},
        read_errors={"cid-1": transient_error},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    conv = conversation(single_chat=True)

    for _ in range(2):
        assert (
            worker._read_conversation_messages(
                "read_recent_messages",
                conv,
                lambda: dws.read_recent_messages(conv),
                default=[],
            )
            == []
        )

    assert worker.store.count_errors() == 0

    assert (
        worker._read_conversation_messages(
            "read_recent_messages",
            conv,
            lambda: dws.read_recent_messages(conv),
            default=[],
        )
        == []
    )

    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state("dws_transient_error_count:read_recent_messages")
        or "{}"
    )
    assert state["count"] == 3


def test_read_conversation_messages_suppresses_token_verified_errors_until_threshold(
    tmp_path: Path, monkeypatch
):
    token_error = DwsError("token verified failed", code="TOKEN_VERIFIED_FAILED")
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": []},
        read_errors={"cid-1": token_error},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    conv = conversation(single_chat=True)

    for _ in range(2):
        assert (
            worker._read_conversation_messages(
                "read_recent_messages",
                conv,
                lambda: dws.read_recent_messages(conv),
                default=[],
            )
            == []
        )

    assert worker.store.count_errors() == 0

    assert (
        worker._read_conversation_messages(
            "read_recent_messages",
            conv,
            lambda: dws.read_recent_messages(conv),
            default=[],
        )
        == []
    )

    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state("dws_transient_error_count:read_recent_messages")
        or "{}"
    )
    assert state["count"] == 3


def test_call_dws_suppresses_message_read_system_errors(tmp_path: Path, monkeypatch):
    notifications = []
    system_error = DwsError(
        "dws command failed with exit code 1; "
        "command=dws contact user search --query 于海龙 --format json; "
        'stderr={"error":{"actions":["Check network, proxy, and DNS settings"],'
        '"cause":"net/http: TLS handshake timeout"}}',
        code="1",
    )
    dws = FakeDws([], {})
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    def fail_read():
        raise system_error

    for _ in range(2):
        assert (
            worker._call_dws(
                "read_mentioned_messages",
                fail_read,
                notify_title="CEO read mentioned messages failed",
                default=[],
            )
            == []
        )

    assert notifications == []
    assert worker.store.count_errors() == 0

    assert (
        worker._call_dws(
            "read_mentioned_messages",
            fail_read,
            notify_title="CEO read mentioned messages failed",
            default=[],
        )
        == []
    )

    assert notifications == []
    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state(
            "dws_transient_error_count:read_mentioned_messages"
        )
        or "{}"
    )
    assert state["count"] == 3


def test_read_recent_messages_missing_direct_chat_target_is_empty_context(
    tmp_path: Path, monkeypatch
):
    missing_target_error = DwsError(
        "expected one direct chat user for '张静', got 0",
        code=DwsError.DIRECT_CHAT_TARGET_NOT_FOUND_CODE,
    )
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": []},
        read_errors={"cid-1": missing_target_error},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state(
        "dws_transient_error_count:read_recent_messages",
        json.dumps({"count": 2, "last_error": "previous", "updated_at": "old"}),
    )
    conv = conversation(single_chat=True)

    assert (
        worker._read_conversation_messages(
            "read_recent_messages",
            conv,
            lambda: dws.read_recent_messages(conv),
            default=[],
        )
        == []
    )

    assert worker.store.count_errors() == 0
    state = json.loads(
        worker.store.get_service_state("dws_transient_error_count:read_recent_messages")
        or "{}"
    )
    assert state["count"] == 0
    assert state["last_error"] == ""


def test_queued_single_chat_context_missing_direct_target_is_not_an_error(
    tmp_path: Path, monkeypatch
):
    missing_target_error = DwsError(
        "expected one direct chat user for '张静', got 0",
        code=DwsError.DIRECT_CHAT_TARGET_NOT_FOUND_CODE,
    )
    trigger = message("请确认一下")
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": []},
        read_errors={"cid-1": missing_target_error},
        unread_errors={"cid-1": missing_target_error},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="unused")),
        monkeypatch,
    )

    context, prompt_context = worker._queued_task_prompt_context_messages(
        conversation(single_chat=True),
        trigger,
    )

    assert context == []
    assert prompt_context == []
    assert worker.store.count_errors() == 0


def test_queued_task_starts_pat_authorization_when_context_read_needs_authorization(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    auth_error = DwsError(
        "dws command failed with exit code 4; code=PAT_MEDIUM_RISK_NO_PERMISSION",
        code="PAT_MEDIUM_RISK_NO_PERMISSION",
        required_scopes=["chat.message:list"],
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        read_errors={"cid-1": auth_error},
        unread_errors={"cid-1": auth_error},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="broadcast only")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert codex.calls == []
    assert dws.pat_authorization_scopes == [["chat.message:list"]]
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    pat_state = json.loads(
        worker.store.get_service_state("dws_pat_authorization") or "{}"
    )
    assert pat_state["status"] == "requested"
    assert pat_state["pid"] == 2234
    assert pat_state["scopes"] == ["chat.message:list"]
    assert any(
        notification["title"] == "CEO DWS PAT authorization required"
        for notification in notifications
    )


def test_consume_manual_rerun_task_forces_new_decision(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen 重新判断一下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="unused")),
        monkeypatch,
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    rerun = worker.store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        attempt_id=attempt_id,
    )
    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1
    assert rerun.execution_generation
    assert worker._test_agent_runner.calls[0][0:2] == (
        rerun.id,
        rerun.execution_generation,
    )


def test_produce_once_starts_dws_auth_login_once_for_non_ready_gate(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    auth_notifications = [
        notification
        for notification in notifications
        if notification["title"] == "CEO DWS auth login required"
    ]
    assert auth_notifications == []
    assert codex.calls == []


def test_produce_once_uses_non_ready_gate_for_unclassified_dws_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws(
        [],
        {},
        list_error=DwsError(
            "dws command failed while resolving the access token",
            code="5",
        ),
    )
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert dws.auth_status_calls == 0
    assert dws.auth_login_starts == 1
    assert worker.store.count_errors() == 0
    assert notifications == []


def test_produce_once_restarts_stale_persisted_dws_auth_login(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": "running", "pid": 99999999}),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    assert notifications == []


def test_produce_once_does_not_start_second_dws_auth_login_for_recent_request(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps(
            {
                "status": "running",
                "pid": 99999999,
                "started_at": "2026-05-13T16:45:00+00:00",
            }
        ),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 0
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 99999999
    assert notifications == []


@pytest.mark.parametrize("previous_status", ["completed", "failed", "authenticated"])
def test_produce_once_restarts_dws_auth_login_after_previous_terminal_state(
    tmp_path: Path, monkeypatch, previous_status: str
):
    notifications = []
    dws = FakeDws([], {}, list_error=DwsError("not authenticated", code="2"))
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到")),
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": previous_status, "pid": 1234}),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0

    assert dws.auth_login_starts == 1
    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "running"
    assert state["pid"] == 1234
    assert notifications == []


def test_produce_once_marks_dws_auth_healthy_after_success(tmp_path: Path, monkeypatch):
    dws = FakeDws([], {})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps({"status": "completed", "pid": 1234}),
    )

    assert worker.produce_once() == 0

    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "healthy"


def test_mark_dws_auth_healthy_records_safe_coordinator_state(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.set_service_state(
        DWS_AUTH_LOGIN_STATE_KEY,
        json.dumps(
            {
                "status": "running",
                "pid": 1234,
                "started_at": "2026-05-13T16:00:00+00:00",
            }
        ),
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **kwargs: None)

    worker._mark_dws_auth_healthy()

    state = json.loads(worker.store.get_service_state(DWS_AUTH_LOGIN_STATE_KEY))
    assert state["status"] == "healthy"
    assert state["started_at"] == "2026-05-13T16:00:00+00:00"


def test_produce_once_continues_when_mention_recovery_fails(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        mentioned_error=DwsError("list mentions failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_errors() == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert dws.unread_message_reads[0] == "cid-1"
    assert notifications == [
        {
            "title": "CEO read mentioned messages failed",
            "message": "list mentions failed",
            "url": None,
        }
    ]
    assert codex.calls == []


def test_produce_once_enqueues_candidate_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_does_not_send_processing_ack_for_new_reply_task(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert codex.calls == []
    assert dws.sent == []


def test_produce_once_fast_path_reads_only_unread_messages_without_recent_context(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("历史上下文", message_id="msg-context")]},
    )
    dws.unread_messages = {"cid-1": [trigger]}
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert "cid-1" in dws.unread_message_reads
    assert dws.recent_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_fast_path_enqueues_pending_before_backoff(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert queued == 1
    assert "cid-1" in dws.unread_message_reads
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-unread"
    assert tasks[0].available_at == "2026-05-13 17:05:00"
    assert tasks[0].error == "waiting_fast_path_unread_backoff"
    assert worker.consume_once() == 0
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_produce_once_fast_path_skips_bare_minutes_link_before_backoff(
    tmp_path: Path, monkeypatch, caplog
):
    caplog.set_level("INFO", logger="app.worker")
    minutes_id = "76327569643331373139373932355f313131333531383337385f30"
    trigger = message(
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8&creator=1113518378]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8&creator=1113518378)\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        message_id="msg-minutes-only",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.unread_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert queued == 0
    assert worker.store.count_reply_tasks() == 0
    assert attempts == []
    assert worker.store.has_seen("msg-minutes-only") is True
    assert "producer skipped message" in caplog.text
    assert "system_or_notification_message" in caplog.text
    assert codex.calls == []


def test_produce_once_fast_path_task_is_claimable_after_backoff(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )
    assert worker.produce_once() == 1

    claimed_before_backoff = worker.store.claim_reply_tasks(
        limit=1,
        now="2026-05-13 17:04:59",
    )
    claimed_after_backoff = worker.store.claim_reply_tasks(
        limit=1,
        now="2026-05-13 17:05:00",
    )

    assert claimed_before_backoff == []
    assert len(claimed_after_backoff) == 1
    assert claimed_after_backoff[0].status == "processing"
    assert claimed_after_backoff[0].error == "waiting_fast_path_unread_backoff"
    assert claimed_after_backoff[0].available_at == ""


def test_calendar_card_task_is_enriched_with_matching_pending_invite(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=True,
        message_type="calendar",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和组织者足以判断需要参加。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Vivian Memorial Park",
        start_time="2026-05-13T15:00:00-07:00",
        end_time="2026-05-13T16:00:00-07:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        attendees=["Alex Chen(明哥)", trigger.sender_name],
        status="confirmed",
    )
    search_start, search_end = worker._calendar_pending_invite_search_window(trigger)
    dws.calendar_events[f"{search_start}|{search_end}"] = [invite]

    queued = worker.produce_once()

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert queued == 1
    assert [task.trigger_message_id for task in tasks] == ["msg-calendar-card"]
    assert tasks[0].trigger_text == "[日程]"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.sender_open_dingtalk_id == "sender-1"
    assert merged.raw_payload == {}


def test_producer_enriches_bare_calendar_card_task_with_invite_details(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=True,
        message_type="calendar",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="测试开发岗位人选画像圆桌",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="讨论测试开发岗位画像和候选人结论。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        attendees=["Alex Chen(明哥)", trigger.sender_name],
        comments=["Alan: 请先看第一位弱不推荐候选人的材料。"],
        status="confirmed",
    )
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 1

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert [task.trigger_message_id for task in tasks] == ["msg-calendar-card"]
    assert tasks[0].trigger_text == "[日程]"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.raw_payload == {}


def test_single_chat_calendar_reply_is_not_suppressed_by_approximate_topic_match(
    tmp_path: Path,
    monkeypatch,
):
    second = message(
        "[日程] 【线上】CTO面试 - 邢继风",
        message_id="msg-calendar-second",
        single_chat=True,
        message_type="calendar",
    )
    worker = make_worker(
        tmp_path,
        FakeDws([conversation(single_chat=True)], {"cid-1": [second]}),
        FakeCodex([]),
        monkeypatch,
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-calendar-first",
        trigger_sender="周俊杰",
        trigger_text="[日程] 【线下】CTO面试 - 张振庭",
        action="send_reply",
        sensitivity_kind="calendar",
        send_status="sent",
    )
    with sqlite3.connect(worker.store.path) as db:
        db.execute(
            "update reply_attempts set created_at=?, updated_at=? where id=?",
            ("2026-05-13 09:59:00", "2026-05-13 09:59:00", attempt_id),
        )

    queued = worker._enqueue_reply_task(
        conversation(single_chat=True),
        second,
    )

    attempts = worker.store.list_reply_attempts(limit=10)
    assert queued is True
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert all(
        attempt.trigger_message_id != "msg-calendar-second" for attempt in attempts
    )
    assert worker.store.has_seen("msg-calendar-second") is False


def test_group_calendar_card_without_explicit_mention_is_ignored(
    tmp_path: Path, monkeypatch
):
    intro = message(
        "静默会，请大家先认真阅读会议描述，谢谢",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    intro.sender_name = "Claire"
    trigger.sender_name = "Claire"
    dws = FakeDws([conversation(single_chat=False)], {"cid-1": [intro, trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="官网反馈静默会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="请阅读官网反馈材料并给出修改建议。",
        organizer="Claire",
        self_response_status="needsAction",
        status="confirmed",
    )
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert tasks == []
    assert worker.store.has_seen("msg-calendar-intro") is False


def test_group_calendar_card_without_explicit_mention_does_not_use_context(
    tmp_path: Path, monkeypatch
):
    older_noise = message(
        "官网 review 需要关注客户表达、产品定位、agent 体验和上线风险。",
        message_id="msg-older-noise",
        single_chat=False,
    )
    older_noise.create_time = "2026-05-13 17:30:00"
    intro = message(
        "欢迎有兴趣的同学参与周二 09:00-10:00 的领先性讨论周会，"
        "下周二议题是产品部和售前团队分享。",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    intro.create_time = "2026-05-13 17:59:33"
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    intro.sender_name = "Robin"
    trigger.sender_name = "Robin"
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [older_noise, intro, trigger]},
        unread_messages={"cid-1": [trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.mark_seen("msg-older-noise", "cid-1")
    worker.store.mark_seen("msg-calendar-intro", "cid-1")
    unrelated = DwsCalendarEvent(
        event_id="invite-unrelated",
        title="官网 review",
        start_time="2026-05-14T12:00:00+08:00",
        end_time="2026-05-14T12:30:00+08:00",
        description="客户表达、产品定位、agent 体验和上线风险。",
        organizer="Claire",
        self_response_status="accepted",
        status="confirmed",
    )
    similar_accepted = DwsCalendarEvent(
        event_id="invite-similar-accepted",
        title="产品前瞻性和领先性讨论",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品领先性讨论。",
        organizer="Principal",
        self_response_status="accepted",
        status="confirmed",
    )
    sender_owned_unrelated = DwsCalendarEvent(
        event_id="invite-sender-unrelated",
        title="Friday memory MCP 安装",
        start_time="2026-05-15T18:00:00+08:00",
        end_time="2026-05-15T19:00:00+08:00",
        description="讨论 MCP 安装路径。",
        organizer="Robin",
        self_response_status="accepted",
        status="confirmed",
    )
    invite = DwsCalendarEvent(
        event_id="invite-context",
        title="领先性讨论周会（每周一收集、每周二讨论）",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品部和售前团队分享新的市场、客户需求和技术落地路径。",
        organizer="Alex Chen",
        self_response_status="tentative",
        status="confirmed",
    )
    later_recurrence = invite.model_copy(
        update={
            "event_id": "invite-context-next",
            "start_time": "2026-05-23T09:00:00+08:00",
            "end_time": "2026-05-23T10:00:00+08:00",
        }
    )
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        unrelated,
        similar_accepted,
        sender_owned_unrelated,
        invite,
        later_recurrence,
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert tasks == []
    assert dws.recent_message_reads == []


def test_group_calendar_card_without_explicit_mention_does_not_refresh_pending_task(
    tmp_path: Path, monkeypatch
):
    intro = message(
        "欢迎参与周二 09:00-10:00 的领先性讨论周会，下周二议题是产品部分享。",
        message_id="msg-calendar-intro",
        single_chat=False,
    )
    intro.create_time = "2026-05-13 17:59:33"
    trigger = message(
        "[日程]",
        message_id="msg-calendar-card",
        single_chat=False,
        message_type="calendar",
    )
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [intro, trigger]},
        unread_messages={"cid-1": [trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-calendar-card",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text="[日程]",
        trigger_message_json=trigger.model_dump_json(),
    )
    invite = DwsCalendarEvent(
        event_id="invite-context",
        title="领先性讨论周会（每周一收集、每周二讨论）",
        start_time="2026-05-19T09:00:00+08:00",
        end_time="2026-05-19T10:00:00+08:00",
        description="产品部分享新的市场和技术落地路径。",
        organizer="Alex Chen",
        self_response_status="tentative",
        status="confirmed",
    )
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]

    assert worker.produce_once() == 0

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(tasks) == 1
    assert tasks[0].trigger_text == "[日程]"
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.raw_payload == {}


def test_fast_path_backoff_processes_trigger_when_unread_clears_without_user_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，先推进")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    runner = script_completed_result(worker, operation_id="fast-path-reply")
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    assert worker.run_once() is None

    attempts = worker.store.list_reply_attempts(limit=10)
    assert "cid-1" in dws.unread_message_reads
    assert worker.store.count_reply_tasks(status="done") == 1
    assert len(attempts) == 1
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "completed"
    assert len(runner.calls) == 1
    assert "msg-unread" == runner.calls[0][2].trigger_message_id
    assert codex.calls == []
    assert final_sent(dws) == []
    assert dws.reply_messages == []


def test_reply_agent_envelope_send_reply_is_delivered(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 帮我看下", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "可以，我看一下。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {},
            "audit": {"summary": "普通回复。", "documents": [], "confidence": 0.8},
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    runner = script_completed_result(worker, operation_id="reply-envelope-send")

    worker.run_once()

    assert len(runner.calls) == 1
    assert "帮我看下" in agent_prompt(worker)
    assert codex.calls == []
    assert final_sent(dws) == []


def test_robot_direct_message_triggers_bot_reply(tmp_path: Path, monkeypatch):
    trigger = message(
        "hi",
        message_id="msg-robot-direct",
        single_chat=True,
        sender_user_id=None,
    ).model_copy(
        update={
            "open_conversation_id": "cid-bot",
            "conversation_title": "磊哥",
            "sender_name": "磊哥",
            "sender_open_dingtalk_id": "user-open-1",
            "raw_payload": {"ceo_agent_source": "robot_direct"},
        }
    )
    dws = FakeDws([], {"cid-bot": [trigger]})
    dws.robot_direct_messages = {"cid-bot": [trigger]}
    dws.resolved_senders["user-open-1"] = "principal-user-1"
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="你好，我在。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    runner = script_completed_result(worker, operation_id="robot-direct-reply")

    worker.run_once()

    assert dws.robot_direct_message_reads == 1
    assert len(runner.calls) == 1
    assert runner.calls[0][2].trigger_message_id == "msg-robot-direct"
    assert dws.bot_direct_messages == []
    assert final_sent(dws) == []
    task_rows = worker.store.list_reply_tasks(statuses=("done",), limit=10)
    assert task_rows[0].conversation_title == "磊哥"


def test_robot_direct_message_is_prioritized_when_task_limit_is_small(
    tmp_path: Path, monkeypatch
):
    group_trigger = message("@Alex Chen(明哥) 帮看一下", message_id="msg-group")
    robot_trigger = message(
        "hi",
        message_id="msg-robot-direct",
        single_chat=True,
    ).model_copy(
        update={
            "open_conversation_id": "cid-bot",
            "conversation_title": "磊哥",
            "raw_payload": {"ceo_agent_source": "robot_direct"},
        }
    )
    dws = FakeDws([conversation()], {"cid-1": [group_trigger]})
    dws.robot_direct_messages = {"cid-bot": [robot_trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker.produce_once(max_tasks=1) == 1

    pending_tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(pending_tasks) == 1
    assert pending_tasks[0].trigger_message_id == "msg-robot-direct"


def test_robot_direct_message_still_queues_when_unread_listing_fails(
    tmp_path: Path, monkeypatch
):
    robot_trigger = message(
        "hi",
        message_id="msg-robot-direct",
        single_chat=True,
    ).model_copy(
        update={
            "open_conversation_id": "cid-bot",
            "conversation_title": "磊哥",
            "raw_payload": {"ceo_agent_source": "robot_direct"},
        }
    )
    dws = FakeDws(
        [],
        {},
        list_error=DwsError("transient discovery timeout", code="6"),
    )
    dws.robot_direct_messages = {"cid-bot": [robot_trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker.produce_once(max_tasks=1) == 1

    pending_tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(pending_tasks) == 1
    assert pending_tasks[0].trigger_message_id == "msg-robot-direct"
    assert dws.robot_direct_message_reads == 1


def test_robot_direct_current_user_message_still_triggers_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "hi",
        message_id="msg-robot-direct",
        single_chat=True,
        sender_user_id=None,
    ).model_copy(
        update={
            "open_conversation_id": "cid-bot",
            "conversation_title": "磊哥",
            "sender_open_dingtalk_id": "current-open-id",
            "raw_payload": {"ceo_agent_source": "robot_direct"},
        }
    )
    dws = FakeDws([], {"cid-bot": [trigger]})
    dws.robot_direct_messages = {"cid-bot": [trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    worker.store.set_current_user_id("principal-user-1")
    worker.store.upsert_org_user_profile(
        user_id="principal-user-1",
        name="Derek",
        open_dingtalk_id="current-open-id",
        manager_user_id=None,
        department_ids=set(),
    )

    assert worker.produce_once(max_tasks=1) == 1

    pending_tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(pending_tasks) == 1
    assert pending_tasks[0].trigger_message_id == "msg-robot-direct"


def test_no_reply_agent_envelope_reaction_adds_emoji_without_text_reply(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message(
        "[群公告]群公告@所有人 咱们大问题都改的差不多了，日清并重新打包。",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "emoji",
                    "emoji": "👍",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "群公告无需正式回复，但适合用表情表示支持。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="reaction-add")

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert dws.message_emojis == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_no_reply_agent_envelope_reaction_strips_square_brackets(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message(
        "[群公告]群公告@所有人 咱们大问题都改的差不多了，日清并重新打包。",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "emoji",
                    "emoji": "[👍]",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "群公告无需正式回复，但适合用表情表示支持。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="reaction-normalize")

    worker.run_once()

    assert dws.message_emojis == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_no_reply_agent_envelope_text_emotion_creates_and_adds_reaction(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message(
        "@Alex Chen(明哥) Hello磊哥，有后端开发工程师面试，我们线上等您哈"
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "no_action",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {
                    "type": "dws_message_reaction",
                    "reaction_type": "text_emotion",
                    "text": "我去摇人",
                }
            ],
            "domain_payload": {},
            "audit": {
                "summary": "只是呼叫本人进入会议，用文字表情轻量承接。",
                "documents": [],
                "confidence": 0.9,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="reaction-create")

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""
    assert dws.created_text_emotions == []
    assert dws.message_text_emotions == []
    assert final_sent(dws) == []


def test_worker_creates_markdown_doc_for_long_reply_before_sending(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 帮我看下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="long-reply")

    worker.run_once()

    sent = final_sent(dws)
    sent_at_users = final_sent_at_users(dws)
    assert dws.created_markdown_docs == []
    assert dws.doc_editor_permissions == []
    assert sent == []
    assert sent_at_users == []
    assert "帮我看下" in agent_prompt(worker)


def test_worker_falls_back_to_chunked_reply_when_automatic_long_reply_doc_fails(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 帮我看下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    monkeypatch.setattr(
        dws,
        "create_markdown_doc",
        lambda name, content: {"result": {"name": name}},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="long-reply-no-service-fallback")

    worker.run_once()

    sent = final_sent(dws)
    assert sent == []
    assert dws.created_markdown_docs == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""


def test_worker_creates_markdown_doc_when_decision_requests_document_reply(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="# 方案\n\n先按 A 路径推进。",
            sensitivity_kind=SensitivityKind.GENERAL,
            system_actions=[
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
                {
                    "type": "dws_markdown_document_reply",
                    "reply_text_ref": "user_response.text",
                    "title": "方案建议",
                },
            ],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="document-reply")

    worker.run_once()

    sent = final_sent(dws)
    assert dws.created_markdown_docs == []
    assert dws.doc_editor_permissions == []
    assert sent == []
    assert "写一版方案" in agent_prompt(worker)


def test_worker_falls_back_when_explicit_document_create_has_no_url(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    def create_doc_without_url(name: str, content: str) -> dict:
        dws.created_markdown_docs.append((name, content))
        return {"result": {"name": name}}

    monkeypatch.setattr(dws, "create_markdown_doc", create_doc_without_url)
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="# 方案\n\n先按 A 路径推进。",
            sensitivity_kind=SensitivityKind.GENERAL,
            system_actions=[
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
                {
                    "type": "dws_markdown_document_reply",
                    "reply_text_ref": "user_response.text",
                    "title": "方案建议",
                },
            ],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "document creation returned no URL",
            code="document_creation_no_url",
        ),
    )

    worker.run_once()

    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "document_creation_no_url"
    assert dws.created_markdown_docs == []


def test_worker_falls_back_to_chunked_reply_when_automatic_doc_permission_fails(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_editor_permission_error = DwsError("doc permission add failed")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="long-reply-permission")

    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert dws.created_markdown_docs == []
    assert dws.doc_editor_permissions == []
    sent = final_sent(dws)
    assert sent == []
    assert attempts[-1].send_status == "completed"
    assert attempts[-1].send_error == ""


def test_worker_keeps_explicit_document_reply_failed_when_permission_fails(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 写一版方案")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_editor_permission_error = DwsError("doc permission add failed")
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="# 方案\n\n先按 A 路径推进。",
            sensitivity_kind=SensitivityKind.GENERAL,
            system_actions=[
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
                {
                    "type": "dws_markdown_document_reply",
                    "reply_text_ref": "user_response.text",
                    "title": "方案建议",
                },
            ],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "doc permission add failed",
            code="doc_permission_failed",
        ),
    )

    worker.run_once()

    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "doc_permission_failed"
    assert dws.doc_editor_permissions == []


def test_worker_does_not_fallback_group_send_when_native_reply_visibility_unconfirmed(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("CEO_REPLY_VISIBILITY_RECHECK_SECONDS", "0")
    trigger = message("@Alex Chen(明哥) 帮我看下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.reply_visible = False
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="A" * 6000,
            sensitivity_kind=SensitivityKind.GENERAL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=False)
    script_completed_result(worker, operation_id="native-visible-reply")

    worker.run_once()

    assert dws.created_markdown_docs == []
    assert dws.reply_messages == []
    sent = final_sent(dws)
    assert sent == []
    assert final_sent_at_users(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "completed"
    sent_reply = worker.store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is None


def test_queued_task_falls_back_to_trigger_when_context_read_fails(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 这是新的工作流，效率可以提升很多",
        message_id="msg-context-error",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    dws.read_errors["cid-1"] = DwsError("forbidden request", code="1001")
    dws.unread_errors["cid-1"] = DwsError("forbidden request", code="1001")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="这个方向可以")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="context-read-failed")
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="MKT core",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once() == 1

    assert len(runner.calls) == 1
    assert "这是新的工作流" in runner.calls[0][2].trigger_text
    assert codex.calls == []
    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        "cid-1",
        "msg-context-error",
    )
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    errors = worker.store.list_errors(limit=10)
    assert errors == []
    assert dws.recent_message_reads[0] == "cid-1"
    assert dws.recent_message_reads.count("cid-1") == 1
    assert dws.unread_message_reads == []


def test_fast_path_backoff_skips_when_current_user_replied_after_trigger(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    manual_reply = principal_message(
        "我已经处理了",
        message_id="msg-principal-after",
        create_time="2026-05-13 18:01:00",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            audit_summary="本人已经处理，无需再次回复。",
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    dws.messages = {"cid-1": [trigger, manual_reply]}
    dws.unread_messages = {"cid-1": []}
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    script_no_action(worker)
    assert worker.run_once() is None

    assert worker.store.count_reply_tasks(status="done") == 1
    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(attempts) == 1
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "skipped"
    assert len(agent_runner(worker).calls) == 1
    assert "我已经处理了" in agent_prompt(worker)
    assert codex.calls == []
    assert final_sent(dws) == []


def test_fast_path_backoff_skips_when_trigger_was_recalled_after_wait(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？", message_id="msg-unread")
    recalled_trigger = trigger.model_copy(
        update={"raw_payload": {"messageStatus": "recalled"}}
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY, audit_summary="消息已撤回，无需回复。"
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    dws.conversations = []
    dws.messages = {"cid-1": [recalled_trigger]}
    dws.unread_messages = {"cid-1": []}
    worker.now_provider = lambda: fixed_worker_now() + timedelta(minutes=6)
    script_no_action(worker)
    assert worker.run_once() is None

    assert dws.messages_by_id_reads == []
    assert worker.store.count_reply_tasks(status="done") == 1
    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(attempts) == 1
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "skipped"
    assert len(agent_runner(worker).calls) == 1
    assert agent_runner(worker).calls[0][2].trigger_message_id == "msg-unread"
    assert codex.calls == []
    assert final_sent(dws) == []


def test_produce_once_fast_path_skips_unread_conversations_unchanged_since_last_check(
    tmp_path: Path, monkeypatch
):
    old_conversation = DingTalkConversation(
        open_conversation_id="cid-old",
        title="旧未读",
        single_chat=False,
        unread_point=2,
        last_message_create_at=1778662800000,
    )
    new_conversation = DingTalkConversation(
        open_conversation_id="cid-new",
        title="新未读",
        single_chat=False,
        unread_point=1,
        last_message_create_at=1778666400000,
    )
    new_trigger = message(
        "@Alex Chen(明哥) 新问题",
        message_id="msg-new",
    )
    new_trigger.open_conversation_id = "cid-new"
    dws = FakeDws(
        [old_conversation, new_conversation],
        {
            "cid-old": [message("@Alex Chen(明哥) 旧问题", message_id="msg-old")],
            "cid-new": [new_trigger],
        },
        unread_messages={"cid-new": [new_trigger]},
    )
    dws.mentioned_messages = {"cid-new": [new_trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )
    worker.store.set_service_state(
        "message_fast_path_checked_at",
        "2026-05-13T09:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert dws.unread_message_reads == ["cid-new"]
    assert dws.recent_message_reads == []


def test_produce_once_skips_recent_conversation_recovery_between_hourly_fallbacks(
    tmp_path: Path, monkeypatch
):
    recovered_conversation = DingTalkConversation(
        open_conversation_id="cid-recovered",
        title="最近处理过的单聊",
        single_chat=True,
        unread_point=0,
    )
    dws = FakeDws([], {"cid-recovered": [message("补充一下", message_id="msg-new")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id=recovered_conversation.open_conversation_id,
        title=recovered_conversation.title,
        single_chat=True,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen", recovered_conversation.open_conversation_id)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_produce_once_runs_recent_conversation_recovery_once_per_hour(
    tmp_path: Path, monkeypatch
):
    recovered_conversation = DingTalkConversation(
        open_conversation_id="cid-recovered",
        title="最近处理过的单聊",
        single_chat=True,
        unread_point=0,
    )
    old_message = message("之前处理过", message_id="msg-seen", single_chat=True)
    new_message = message("补充一下", message_id="msg-new", single_chat=True)
    new_message.create_time = "2026-05-13 18:05:00"
    dws = FakeDws([], {"cid-recovered": [old_message, new_message]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id=recovered_conversation.open_conversation_id,
        title=recovered_conversation.title,
        single_chat=True,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen", recovered_conversation.open_conversation_id)
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 1
    assert dws.recent_message_reads == ["cid-recovered"]
    assert dws.unread_message_reads == []
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert (
        worker.store.get_service_state("message_recovery_checked_at")
        == "2026-05-13T17:00:00+00:00"
    )


def test_produce_once_does_not_recover_recent_group_conversations(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {"cid-group": [message("群里补充一下")]})
    dws.read_errors["cid-group"] = DwsError("forbidden request", code="1001")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        conversation_id="cid-group",
        title="最近处理过的群聊",
        single_chat=False,
        codex_session_id=None,
    )
    worker.store.mark_seen("msg-seen-group", "cid-group")
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    queued = worker.produce_once()

    assert queued == 0
    assert dws.recent_message_reads == []
    assert dws.unread_message_reads == []
    assert worker.store.list_errors() == []
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_current_user_candidate_filter_uses_only_local_identity_cache(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {}, current_user_error=RuntimeError("remote lookup"))
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    current_user_message = message(
        "我自己发的",
        sender_user_id="principal-user-1",
    )
    unknown_sender_message = message(
        "未知 sender",
        sender_user_id=None,
    )

    assert worker._is_current_user_message_for_candidate_filter(current_user_message)
    assert (
        worker._is_current_user_message_for_candidate_filter(unknown_sender_message)
        is False
    )
    assert dws.current_user_checks == []


def test_produce_once_checks_dws_upgrade_once_per_local_day(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws([], {})
    dws.upgrade_check_response = {
        "current_version": "v1.0.26",
        "latest_version": "v1.0.32",
        "needs_upgrade": True,
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert dws.upgrade_check_calls == 1
    assert dws.upgrade_calls == 1
    assert worker.store.get_service_state("dws_upgrade_checked_date") == "2026-05-13"


def test_produce_once_records_dws_upgrade_check_failure_in_service_state(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.upgrade_check_error = RuntimeError("upgrade service unavailable")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert dws.upgrade_check_calls == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    errors = worker.store.list_errors()
    assert errors == []
    state = json.loads(worker.store.get_service_state("dws_upgrade_check_result"))
    assert state["status"] == "check_failed"
    assert "upgrade service unavailable" in state["detail"]
    assert worker.store.get_service_state("dws_upgrade_checked_date") == "2026-05-13"


def test_produce_once_records_dws_upgrade_install_failure_without_blocking_messages(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.upgrade_check_response = {
        "current_version": "v1.0.26",
        "latest_version": "v1.0.32",
        "needs_upgrade": True,
    }
    dws.upgrade_install_error = RuntimeError("upgrade install unavailable")
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert dws.upgrade_check_calls == 1
    assert dws.upgrade_calls == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    errors = worker.store.list_errors()
    assert len(errors) == 1
    assert errors[0].kind == "dws_upgrade"
    assert "upgrade install unavailable" in errors[0].detail
    assert worker.store.get_service_state("dws_upgrade_checked_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_once_per_seven_days(
    tmp_path: Path, monkeypatch
):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert calls[0][1] is dws
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_after_seven_days(tmp_path: Path, monkeypatch):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state("org_cache_refreshed_date", "2026-05-06")

    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_refreshes_org_cache_when_refresh_date_is_invalid(
    tmp_path: Path, monkeypatch
):
    calls = []

    def fake_refresh_org_cache(store, dws):
        calls.append((store, dws))
        return 3

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    dws = FakeDws([], {})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.set_service_state("org_cache_refreshed_date", "invalid")

    assert worker.produce_once() == 0

    assert len(calls) == 1
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_records_org_cache_refresh_failure_without_blocking_messages(
    tmp_path: Path, monkeypatch
):
    def fake_refresh_org_cache(store, dws):
        raise RuntimeError("contact service unavailable")

    monkeypatch.setattr(worker_module, "refresh_org_cache", fake_refresh_org_cache)
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    errors = worker.store.list_errors()
    assert len(errors) == 1
    assert errors[0].kind == "org_cache_refresh"
    assert "contact service unavailable" in errors[0].detail
    assert worker.store.get_service_state("org_cache_refreshed_date") == "2026-05-13"


def test_produce_once_skips_messages_older_than_local_24_hour_window(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个旧消息不用处理？")
    trigger.create_time = "2026-05-13 00:59:59"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 0
    assert codex.calls == []
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.has_seen("msg-1") is True


def test_produce_once_uses_beijing_message_time_against_local_24_hour_window(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个消息还在24小时内？")
    trigger.create_time = "2026-05-13 01:00:00"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1


def test_repeated_produce_once_does_not_send_processing_ack(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert dws.sent == []


def test_consume_once_does_not_send_processing_ack(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    def before_decide(prompt, _session_id):
        assert PROCESSING_ACK not in prompt
        assert dws.sent == []

    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走"),
        before_decide=before_decide,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="processing-ack-reply")
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert len(runner.calls) == 1
    assert PROCESSING_ACK not in runner.calls[0][2].render()
    assert dws.sent == []


def test_repeated_produce_once_does_not_duplicate_pending_task(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    assert worker.produce_once() == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert codex.calls == []


def test_produce_once_treats_configured_agent_name_mention_like_principal_mention(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_NAMES", "磊哥")
    trigger = message("@磊哥 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 1
    task = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    assert task.trigger_message_id == "msg-1"
    assert task.trigger_text == "@磊哥 这个怎么处理？"
    assert codex.calls == []


def test_produce_once_uses_recent_context_when_unread_read_fails_for_group_mention(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        unread_messages={"cid-1": []},
        unread_errors={
            "cid-1": DwsError(
                "business error: SECURITY_CHECK_INVOKE_FAILED",
                code="SECURITY_CHECK_INVOKE_FAILED",
            )
        },
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_errors() == 0
    transient_state = json.loads(
        worker.store.get_service_state("dws_transient_error_count:read_unread_messages")
        or "{}"
    )
    assert transient_state["count"] == 1
    assert notifications == []
    assert codex.calls == []


def test_produce_once_suppresses_repeated_forbidden_unread_reads(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_errors={"cid-1": DwsError("forbidden request", code="1001")},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.get_service_state("dws_forbidden_conversations")

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_produce_once_suppresses_repeated_permission_denied_unread_reads(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_errors={
            "cid-1": DwsError(
                "[AUTH_PERMISSION_DENIED] Permission denied "
                "(operation: chat/list_conversation_message_v2)",
                code="1001",
            )
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.get_service_state("dws_forbidden_conversations")

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 0
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_produce_once_does_not_cache_authorization_errors_as_forbidden_reads(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_errors={
            "cid-1": DwsError(
                "dws command failed with exit code 4; code=AGENT_CODE_NOT_EXISTS",
                code="AGENT_CODE_NOT_EXISTS",
            )
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert worker.store.count_errors() == 1
    assert not worker.store.get_service_state("dws_forbidden_conversations")

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1", "cid-1"]
    assert worker.store.count_errors() == 2
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_produce_once_starts_pat_authorization_without_forbidden_read_cache(
    tmp_path: Path, monkeypatch
):
    notifications = []
    pat_error = DwsError(
        "dws command failed with exit code 4; code=PAT_MEDIUM_RISK_NO_PERMISSION",
        code="PAT_MEDIUM_RISK_NO_PERMISSION",
        required_scopes=["chat.message:list"],
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_errors={"cid-1": pat_error},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1"]
    assert dws.pat_authorization_scopes == [["chat.message:list"]]
    assert worker.store.count_errors() == 0
    assert not worker.store.get_service_state("dws_forbidden_conversations")
    assert any(
        notification["title"] == "CEO DWS PAT authorization required"
        for notification in notifications
    )

    assert worker.produce_once() == 0
    assert dws.unread_message_reads == ["cid-1", "cid-1"]
    assert dws.pat_authorization_scopes == [["chat.message:list"]]
    assert worker.store.count_errors() == 0
    assert worker.store.count_reply_tasks(status="pending") == 0


def test_forbidden_read_cache_only_suppresses_during_short_cooldown(
    tmp_path: Path, monkeypatch
):
    trigger = message("窗口打开时也要能恢复", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    forbidden_until = (
        fixed_worker_now().astimezone(ZoneInfo("UTC"))
        + worker_module.DWS_FORBIDDEN_CONVERSATION_COOLDOWN
        - timedelta(seconds=1)
    ).isoformat()
    worker.store.set_service_state(
        "dws_forbidden_conversations",
        json.dumps({"cid-1": forbidden_until}),
    )

    messages = worker._read_conversation_messages(
        "read_recent_messages",
        conversation(single_chat=True),
        lambda: dws.read_recent_messages(conversation(single_chat=True)),
        default=[],
    )

    assert messages == []
    assert dws.recent_message_reads == []


def test_stale_forbidden_read_cache_does_not_block_recovered_single_chat(
    tmp_path: Path, monkeypatch
):
    trigger = message("窗口打开时也要能恢复", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    forbidden_until = (
        fixed_worker_now().astimezone(ZoneInfo("UTC"))
        + worker_module.DWS_FORBIDDEN_CONVERSATION_COOLDOWN
        + timedelta(hours=1)
    ).isoformat()
    worker.store.set_service_state(
        "dws_forbidden_conversations",
        json.dumps({"cid-1": forbidden_until}),
    )

    messages = worker._read_conversation_messages(
        "read_recent_messages",
        conversation(single_chat=True),
        lambda: dws.read_recent_messages(conversation(single_chat=True)),
        default=[],
    )

    assert [item.open_message_id for item in messages] == [trigger.open_message_id]
    assert dws.recent_message_reads == ["cid-1"]
    assert (
        json.loads(
            worker.store.get_service_state("dws_forbidden_conversations") or "{}"
        )
        == {}
    )


def test_produce_once_does_not_notify_when_only_recent_context_read_fails(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_messages={"cid-1": [trigger]},
        read_errors={"cid-1": DwsError("temporary SYSTEM_ERROR", code="SYSTEM_ERROR")},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    queued = worker.produce_once()

    assert queued == 1
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_errors() == 0
    assert notifications == []
    assert codex.calls == []


def test_consume_once_processes_queued_task(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", raising=False)
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="queued-task-reply")
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert worker.store.count_reply_tasks(status="done") == 1
    assert len(runner.calls) == 1
    assert runner.calls[0][2].trigger_message_id == trigger.open_message_id
    assert final_sent(dws) == []


def test_consumer_does_not_claim_task_when_required_gate_is_not_ready(
    tmp_path, monkeypatch
):
    worker = make_worker(
        tmp_path,
        FakeDws([], {}),
        FakeCodex([]),
        monkeypatch,
        channel_gates={
            "dingtalk": FixedGate("dingtalk", ChannelGateState.UNAVAILABLE),
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )
    trigger = message("@Alex Chen 看一下")
    worker.store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task_id = worker.store.peek_reply_tasks(limit=1)[0].id

    assert worker.consume_once() == 0

    task = worker.store.get_reply_task(task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 0


def test_channel_gate_preserves_last_success_when_later_check_fails(
    tmp_path, monkeypatch
):
    gate = FixedGate("dingtalk", ChannelGateState.READY)
    worker = make_worker(
        tmp_path,
        FakeDws([], {}),
        FakeCodex([]),
        monkeypatch,
        channel_gates={"dingtalk": gate},
    )

    worker._channel_result("dingtalk")
    last_success = worker.store.get_service_state("channel_gate_last_success:dingtalk")

    gate.state = ChannelGateState.UNAVAILABLE
    worker._pass_channel_results.clear()
    worker._channel_result("dingtalk")

    assert last_success == "2026-05-13T17:00:00+00:00"
    assert (
        worker.store.get_service_state("channel_gate_last_success:dingtalk")
        == last_success
    )
    gate_state = json.loads(worker.store.get_service_state("channel_gate:dingtalk"))
    assert gate_state["status"] == "unavailable"


def test_lark_non_ready_keeps_referencing_task_pending_and_checks_each_gate_once(
    tmp_path, monkeypatch
):
    trigger = message("https://example.feishu.cn/docx/abc 这份文档怎么处理？")
    dingtalk_gate = FixedGate("dingtalk", ChannelGateState.READY)
    lark_gate = FixedGate("lark", ChannelGateState.UNAVAILABLE)
    worker = make_worker(
        tmp_path,
        FakeDws([], {}),
        FakeCodex([]),
        monkeypatch,
        channel_gates={"dingtalk": dingtalk_gate, "lark": lark_gate},
    )
    worker.store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task_id = worker.store.peek_reply_tasks(limit=1)[0].id

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.get_reply_task(task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 0
    assert dingtalk_gate.calls == 1
    assert lark_gate.calls == 1


def test_lark_blocked_task_does_not_block_later_dingtalk_task(tmp_path, monkeypatch):
    dingtalk_gate = FixedGate("dingtalk", ChannelGateState.READY)
    lark_gate = FixedGate("lark", ChannelGateState.UNAVAILABLE)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    runner = FakeAgentResultRunner(store)
    worker = worker_module.DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws([], {}),
        codex=FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        now_provider=fixed_worker_now,
        channel_gates={"dingtalk": dingtalk_gate, "lark": lark_gate},
    )
    worker._test_agent_runner = runner
    worker.agent_orchestrator = FakeAgentOrchestrator(worker)
    lark_triggers = [
        message(
            f"https://example.feishu.cn/docx/{index} 这份文档怎么处理？",
            message_id=f"msg-lark-{index}",
        )
        for index in range(201)
    ]
    dingtalk_trigger = message(
        "系统通知",
        message_id="msg-dingtalk",
        message_type="system",
    )
    for trigger in (*lark_triggers, dingtalk_trigger):
        store.enqueue_reply_task(
            conversation_id=trigger.open_conversation_id,
            conversation_title=trigger.conversation_title,
            single_chat=trigger.single_chat,
            trigger_message_id=trigger.open_message_id,
            trigger_create_time=trigger.create_time,
            trigger_sender=trigger.sender_name,
            trigger_text=trigger.content,
            trigger_message_json=trigger.model_dump_json(),
        )
    all_tasks = store.peek_reply_tasks(limit=202)
    blocked_ids = [task.id for task in all_tasks[:-1]]
    processable_id = all_tasks[-1].id

    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1

    processable = store.get_reply_task(processable_id)
    blocked = [store.get_reply_task(task_id) for task_id in blocked_ids]
    assert all(task is not None for task in blocked)
    assert all(task.status == "pending" for task in blocked if task is not None)
    assert all(task.attempts == 0 for task in blocked if task is not None)
    assert processable is not None
    assert processable.status == "done"
    assert processable.attempts == 1
    assert dingtalk_gate.calls == 1
    assert lark_gate.calls == 1


def test_consume_once_does_not_scan_tasks_inserted_after_pass_snapshot(
    tmp_path, monkeypatch
):
    dingtalk_gate = FixedGate("dingtalk", ChannelGateState.READY)
    lark_gate = FixedGate("lark", ChannelGateState.UNAVAILABLE)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    worker = worker_module.DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws([], {}),
        codex=FakeCodex([]),
        now_provider=fixed_worker_now,
        channel_gates={"dingtalk": dingtalk_gate, "lark": lark_gate},
    )
    initial = message(
        "https://example.feishu.cn/docx/initial",
        message_id="msg-initial",
    )
    store.enqueue_reply_task(
        conversation_id=initial.open_conversation_id,
        conversation_title=initial.conversation_title,
        single_chat=initial.single_chat,
        trigger_message_id=initial.open_message_id,
        trigger_create_time=initial.create_time,
        trigger_sender=initial.sender_name,
        trigger_text=initial.content,
        trigger_message_json=initial.model_dump_json(),
    )
    original_peek = store.peek_reply_tasks
    peek_calls = 0

    def peek_and_insert(*args, **kwargs):
        nonlocal peek_calls
        peek_calls += 1
        if peek_calls > 2:
            raise AssertionError(
                "consume pass followed tasks inserted after its snapshot"
            )
        page = original_peek(*args, **kwargs)
        inserted = message(
            f"https://example.feishu.cn/docx/inserted-{peek_calls}",
            message_id=f"msg-inserted-{peek_calls}",
        )
        store.enqueue_reply_task(
            conversation_id=inserted.open_conversation_id,
            conversation_title=inserted.conversation_title,
            single_chat=inserted.single_chat,
            trigger_message_id=inserted.open_message_id,
            trigger_create_time=inserted.create_time,
            trigger_sender=inserted.sender_name,
            trigger_text=inserted.content,
            trigger_message_json=inserted.model_dump_json(),
        )
        return page

    monkeypatch.setattr(store, "peek_reply_tasks", peek_and_insert)

    assert worker.consume_once(max_tasks=1) == 0
    assert peek_calls == 2
    assert store.count_reply_tasks(status="pending") == 3


def test_lark_non_ready_is_not_checked_for_dingtalk_only_task(tmp_path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dingtalk_gate = FixedGate("dingtalk", ChannelGateState.READY)
    lark_gate = FixedGate("lark", ChannelGateState.UNAVAILABLE)
    worker = make_worker(
        tmp_path,
        FakeDws([], {}),
        FakeCodex([]),
        monkeypatch,
        channel_gates={"dingtalk": dingtalk_gate, "lark": lark_gate},
    )
    worker.store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task_id = worker.store.peek_reply_tasks(limit=1)[0].id

    worker.consume_once(max_tasks=1)

    task = worker.store.get_reply_task(task_id)
    assert task is not None
    assert task.attempts == 1
    assert dingtalk_gate.calls == 1
    assert lark_gate.calls == 0


def test_consume_once_claims_one_reply_task_at_a_time(tmp_path: Path, monkeypatch):
    class WorkerInterrupted(BaseException):
        pass

    class InterruptingRunner:
        def run(self, _task, _context, **_kwargs):
            raise WorkerInterrupted()

    first = message(
        "@Alex Chen(明哥) 第一条怎么处理？",
        message_id="msg-1",
    )
    second = message(
        "@Alex Chen(明哥) 第二条怎么处理？",
        message_id="msg-2",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [second, first]},
    )

    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        scripted_runner=InterruptingRunner(),
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=first.open_message_id,
        trigger_create_time=first.create_time,
        trigger_sender=first.sender_name,
        trigger_text=first.content,
        trigger_message_json=first.model_dump_json(),
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=second.open_message_id,
        trigger_create_time=second.create_time,
        trigger_sender=second.sender_name,
        trigger_text=second.content,
        trigger_message_json=second.model_dump_json(),
    )

    with pytest.raises(WorkerInterrupted):
        worker.consume_once(max_tasks=2)

    tasks = {
        task.trigger_message_id: task
        for task in worker.store.list_reply_tasks(statuses=("pending", "processing"))
    }
    assert tasks["msg-1"].status == "processing"
    assert tasks["msg-2"].status == "pending"


def test_consume_once_appends_feedback_links_when_configured(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="feedback-configured-reply")
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert len(runner.calls) == 1
    assert "feedback.example.com" not in runner.calls[0][2].render()
    assert final_sent(dws) == []
    assert worker.store.get_sent_reply("cid-1", "msg-1") is None
    attempt = worker.store.list_reply_attempts(limit=1)[0]
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_consume_once_uses_required_feedback_prefix_after_unanswered_week(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="feedback-week-reply")
    worker.store.record_sent_reply(
        "cid-1",
        "old-msg-1",
        "旧回复",
        feedback_token="token-old",
    )
    with sqlite3.connect(worker.store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-05 18:00:00", "old-msg-1"),
        )
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert len(runner.calls) == 1
    assert "【反馈】" not in runner.calls[0][2].render()
    assert final_sent(dws) == []
    assert worker.store.get_sent_reply("cid-1", "msg-1") is None


def test_consume_once_keeps_reply_after_unanswered_feedback_deadline(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = script_completed_result(worker, operation_id="feedback-deadline-reply")
    worker.store.record_sent_reply(
        "cid-1",
        "old-msg-1",
        "旧回复",
        feedback_token="token-old",
    )
    with sqlite3.connect(worker.store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-02 18:00:00", "old-msg-1"),
        )
    worker.produce_once()

    processed = worker.consume_once(max_tasks=1)

    assert processed == 1
    assert len(runner.calls) == 1
    prompt = runner.calls[0][2].render()
    assert "【反馈】" not in prompt
    assert "请对我提供反馈后再提问" not in prompt
    assert "/api/dingtalk-feedback-spike" not in prompt
    assert final_sent(dws) == []
    assert worker.store.get_sent_reply("cid-1", "msg-1") is None


def test_consume_once_stops_retryable_orchestration_at_limit(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    retry_result = explicit_agent_result(
        ScriptOutcome.FAILED,
        "temporary agent failure",
        code="temporary_agent_failure",
        retryable=True,
    )
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (retry_result, (), "retry-session"),
            (retry_result, (), "unused-session"),
        ],
    )
    worker._test_agent_runner = runner
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    pending = worker.store.list_reply_tasks(limit=1, statuses=["pending"])[0]
    generation = pending.execution_generation
    with worker.store._connect() as db:
        db.execute("update reply_tasks set available_at='' where id=?", (pending.id,))
    assert worker.consume_once(max_tasks=1) == 0
    retried = worker.store.list_reply_tasks(limit=1, statuses=["failed"])[0]
    assert retried.execution_generation == generation
    assert len(runner.calls) == 2
    assert runner.calls[1][3] == "retry-session"
    assert notifications == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "temporary_agent_failure"


def test_orchestration_finalize_is_atomic_after_generation_switch(
    tmp_path: Path,
    monkeypatch,
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    result = explicit_agent_result(ScriptOutcome.COMPLETED, "处理完成")

    class TerminalThenRotateRunner(FakeAgentResultRunner):
        def run(self, task, context, **kwargs):
            run_result = super().run(task, context, **kwargs)
            self.new_generation = self.store.rotate_reply_task_execution_generation(
                task.id
            )
            return run_result

    runner = TerminalThenRotateRunner(
        worker.store,
        [(result, (), "old-session")],
    )
    worker._test_agent_runner = runner
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.get_reply_task(1)
    assert task is not None
    assert task.execution_generation == runner.new_generation
    assert task.status == "pending"
    assert task.error == "execution_generation_rotated"
    assert worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1") is None


def test_consume_once_retries_execution_generation_mismatch(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    worker.produce_once()

    def raise_execution_generation_mismatch(*_args, **_kwargs):
        raise ValueError("execution generation mismatch")

    monkeypatch.setattr(
        worker,
        "_process_queued_task",
        raise_execution_generation_mismatch,
    )

    assert worker.consume_once(max_tasks=1) == 0

    task = worker.store.list_reply_tasks(statuses=["pending"])[0]
    assert task.attempts == 1
    assert task.error == "execution generation mismatch"
    assert task.force_new_decision is False
    assert task.execution_generation == "initial"
    assert "reply_task_retry" in [
        error.kind for error in worker.store.list_errors(limit=10)
    ]


def test_pre_run_exception_does_not_link_an_older_failed_agent_run(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex([]),
        monkeypatch,
    )
    worker.produce_once()
    task = worker.store.claim_reply_task(1)
    assert task is not None
    old_run = _claim_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
        owner="old-failed-run",
    ).run
    worker.store.fail_agent_run(
        old_run.id,
        {"code": "old_failure", "retryable": True},
        owner="old-failed-run",
    )
    worker.store.requeue_reply_task(
        task.id,
        "old_failure",
        expected_execution_generation=task.execution_generation,
    )

    def fail_before_agent_run(*_args, **_kwargs):
        raise RuntimeError("context_build_failed")

    monkeypatch.setattr(worker, "_process_queued_task", fail_before_agent_run)

    assert worker.consume_once(max_tasks=1) == 0

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.agent_run_id is None
    assert attempt.audit_summary == "context_build_failed"
    assert attempt.send_error == "context_build_failed"


@pytest.mark.parametrize("authorization", [False, True])
def test_old_worker_cannot_write_context_or_authorization_failure_after_rotation(
    tmp_path: Path, monkeypatch, authorization: bool
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex([]),
        monkeypatch,
    )
    worker.produce_once()

    class AuthorizationFailure(RuntimeError):
        needs_authorization = True

    def rotate_then_fail(_conversation, task):
        new_generation = worker.store.rotate_reply_task_execution_generation(task.id)
        worker._test_new_generation = new_generation
        error_type = AuthorizationFailure if authorization else RuntimeError
        raise error_type(
            "authorization required" if authorization else "context failed"
        )

    monkeypatch.setattr(worker, "_process_queued_task", rotate_then_fail)

    assert worker.consume_once(max_tasks=1) == 0
    task = worker.store.get_reply_task(1)
    assert task is not None
    assert task.execution_generation == worker._test_new_generation
    assert task.status == "pending"
    assert task.error == "execution_generation_rotated"
    assert worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1") is None


def test_active_run_defer_cannot_overwrite_rotated_generation(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex([]),
        monkeypatch,
    )
    worker.produce_once()
    pending = worker.store.get_reply_task(1)
    assert pending is not None
    worker.agent_orchestrator = ScriptedAgentOrchestrator(
        OrchestrationResult(
            status="failed_retryable",
            final_run_id=0,
            final_role=AgentRole.CONSUMER,
            summary="agent_run_active",
            error=AgentError(code="agent_run_active", retryable=True),
            feedback_cycles=0,
        )
    )
    original_defer = worker.store.defer_reply_task

    def rotate_then_defer(task_id, error, **kwargs):
        worker._test_new_generation = (
            worker.store.rotate_reply_task_execution_generation(task_id)
        )
        return original_defer(task_id, error, **kwargs)

    monkeypatch.setattr(worker.store, "defer_reply_task", rotate_then_defer)

    assert worker.consume_once(max_tasks=1) == 0
    task = worker.store.get_reply_task(1)
    assert task is not None
    assert task.execution_generation == worker._test_new_generation
    assert task.status == "pending"
    assert task.error == "execution_generation_rotated"


def test_consume_once_completes_generation_mismatch_after_terminal_at_max_attempts(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        max_task_attempts=1,
    )
    worker.produce_once()
    task = worker.store.list_reply_tasks(statuses=["pending"])[0]
    worker.store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="oa_approval",
        sensitivity_kind="general",
        send_status="commented",
    )

    def raise_execution_generation_mismatch(*_args, **_kwargs):
        raise ValueError("execution generation mismatch")

    monkeypatch.setattr(
        worker,
        "_process_queued_task",
        raise_execution_generation_mismatch,
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert "reply_task" in [error.kind for error in worker.store.list_errors(limit=10)]


def test_consume_once_records_stale_processing_tasks_before_requeue(
    tmp_path: Path, monkeypatch
):
    notifications = []
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-29 11:26:41",
        trigger_sender="ET",
        trigger_text="@Alex Chen 这个怎么处理？",
    )
    claimed = store.claim_reply_tasks(limit=1)
    assert claimed[0].status == "processing"
    agent_claim = _claim_audit_run(
        store,
        claimed[0].id,
        claimed[0].execution_generation,
        owner="crashed-worker",
    )
    store.set_agent_run_session(
        agent_claim.run.id,
        "session-stale-1",
        owner="crashed-worker",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed[0].id,),
        )
        db.execute(
            "update agent_runs set lease_expires_at=datetime('now', '-1 minute') where id=?",
            (agent_claim.run.id,),
        )
    dws = FakeDws([conversation()], {"cid-1": []})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, audit_summary="无需回复。")
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.consume_once(max_tasks=1)

    errors = store.list_errors()
    assert any(error.kind == "reply_task_stale" for error in errors)
    stale_error = next(error for error in errors if error.kind == "reply_task_stale")
    assert f"task={claimed[0].id}" in stale_error.detail
    assert f"generation={claimed[0].execution_generation}" in stale_error.detail
    assert notifications[0]["title"] == "CEO task retrying stale tasks"
    run = _get_audit_run(
        store,
        claimed[0].id,
        claimed[0].execution_generation,
    )
    assert run is not None
    assert run.codex_session_id == "session-stale-1"


def test_stale_wechat_read_only_decision_requeues_with_precise_reason(
    tmp_path: Path, monkeypatch
):
    notifications = []
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="wechat-cid",
        conversation_title="Wechat contact",
        single_chat=True,
        trigger_message_id="wechat-msg",
        trigger_create_time="2026-08-07 01:00:00",
        trigger_sender="contact",
        trigger_text="Can you reply?",
    )
    [task] = store.claim_reply_tasks(limit=1, channel="wechat")
    store.mark_wechat_read_only_decision_started(
        task.id,
        expected_execution_generation=task.execution_generation,
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (task.id,),
        )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws([], {}),
        codex=FakeCodex(
            CodexDecision(action=CodexAction.NO_REPLY, audit_summary="unused")
        ),
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker._recover_stale_agent_reply_tasks()

    recovered = store.get_reply_task(task.id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.error == "interrupted_read_only_decision"
    assert notifications[0]["title"] == "CEO task retrying stale tasks"


def test_consume_once_does_not_requeue_stale_task_with_live_agent_lease(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-29 11:26:41",
        trigger_sender="ET",
        trigger_text="@Alex Chen 这个怎么处理？",
    )
    claimed = store.claim_reply_tasks(limit=1)
    agent_claim = _claim_audit_run(
        store,
        claimed[0].id,
        claimed[0].execution_generation,
        owner="active-worker",
        lease_seconds=3600,
    )
    store.set_agent_run_session(
        agent_claim.run.id,
        "session-active-1",
        owner="active-worker",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed[0].id,),
        )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws([conversation()], {"cid-1": []}),
        codex=FakeCodex([]),
        now_provider=lambda: datetime.now().astimezone(),
        channel_gates=fixed_channel_gates(),
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_kwargs: None)

    worker.consume_once(max_tasks=1)

    task = store.get_reply_task(claimed[0].id)
    assert task is not None
    assert task.status == "processing"
    assert not any(error.kind == "reply_task_stale" for error in store.list_errors())


def test_agent_run_lease_outlives_stale_task_recovery_window():
    assert LEASE_SECONDS > worker_module.STALE_PROCESSING_TASK_SECONDS


def test_consumer_cycle_does_not_requeue_task_claimed_by_another_worker(
    tmp_path: Path,
    monkeypatch,
):
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": []}),
        FakeCodex([]),
        monkeypatch,
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-orphan",
        conversation_title="Orphan",
        single_chat=False,
        trigger_message_id="msg-orphan",
        trigger_create_time="2026-07-30 09:00:00",
        trigger_sender="Derek",
        trigger_text="handle this",
    )
    orphan = worker.store.claim_reply_tasks(1)[0]

    worker.consume_once(max_tasks=1)

    current = worker.store.get_reply_task(orphan.id)
    assert current is not None
    assert current.status == "processing"
    assert current.execution_generation == orphan.execution_generation


def test_consume_once_does_not_recover_older_single_chat_claim(
    tmp_path: Path,
    monkeypatch,
):
    old_message = message("我先补充第一点", message_id="msg-single-1", single_chat=True)
    old_message.create_time = "2026-05-13 18:00:00"
    new_message = message(
        "我已经算出来了，按这个回复", message_id="msg-single-2", single_chat=True
    )
    new_message.create_time = "2026-05-13 18:01:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [new_message, old_message]},
        unread_messages={"cid-1": [new_message, old_message]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到，按第二条处理。")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id=old_message.open_message_id,
        trigger_create_time=old_message.create_time,
        trigger_sender=old_message.sender_name,
        trigger_text=old_message.content,
        trigger_message_json=old_message.model_dump_json(),
    )
    old_task = worker.store.claim_reply_tasks(limit=1)[0]
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id=new_message.open_message_id,
        trigger_create_time=new_message.create_time,
        trigger_sender=new_message.sender_name,
        trigger_text=new_message.content,
        trigger_message_json=new_message.model_dump_json(),
    )

    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1

    tasks = {
        task.trigger_message_id: task
        for task in worker.store.list_reply_tasks(
            statuses=("done", "pending", "processing")
        )
    }
    assert tasks["msg-single-1"].id == old_task.id
    assert tasks["msg-single-1"].status == "processing"
    assert tasks["msg-single-1"].locked_at is not None
    assert tasks["msg-single-2"].status == "done"
    assert worker.store.count_reply_tasks(status="processing") == 1
    assert not any(
        error.kind == "reply_task_superseded" for error in worker.store.list_errors()
    )


def test_consume_once_authorization_failure_waits_without_final_failure(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    gates = fixed_channel_gates(dingtalk=ChannelGateState.NEEDS_LOGIN)
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
        channel_gates=gates,
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    task = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    assert task.attempts == 0
    assert task.available_at == ""
    assert worker.store.count_reply_attempts() == 0
    assert gates["dingtalk"].calls == 2


def test_consume_once_codex_provider_auth_failure_waits_for_authorization(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    failure = (
        "unexpected status 401 Unauthorized: invalid api key, "
        "url: https://api.example.invalid/v1/responses"
    )

    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
        scripted_runner=FailingTaskRunner(failure),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        attempts, error, available_at = db.execute(
            "select attempts, error, available_at from reply_tasks"
        ).fetchone()
    assert attempts == 0
    assert error.startswith("codex_provider_auth_failed:")
    assert "configured Codex model provider rejected its API key" in error
    assert available_at == "2026-05-13 17:15:00"
    error_kinds = [error.kind for error in worker.store.list_errors(limit=10)]
    assert "reply_task_authorization" in error_kinds
    assert "reply_task_retry" not in error_kinds
    assert any(
        notification["title"] == "CEO task waiting for authorization: Friday"
        for notification in notifications
    )


def test_consume_once_native_codex_missing_auth_header_waits_for_provider_recovery(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    failure = (
        "unexpected status 401 Unauthorized: Missing bearer or basic "
        "authentication in header, url: "
        "https://api.openai.com/v1/responses"
    )

    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
        scripted_runner=FailingTaskRunner(failure),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        attempts, error, available_at = db.execute(
            "select attempts, error, available_at from reply_tasks"
        ).fetchone()
    assert attempts == 0
    assert error.startswith("codex_provider_unavailable:")
    assert "omitted the authenticated request header" in error
    assert available_at == "2026-05-13 17:01:00"
    error_kinds = [error.kind for error in worker.store.list_errors(limit=10)]
    assert "reply_task_provider_recovery" in error_kinds
    assert "reply_task_authorization" not in error_kinds
    assert any(
        notification["title"] == "CEO task waiting for Codex provider recovery: Friday"
        for notification in notifications
    )


def test_explicit_codex_provider_missing_auth_header_still_requires_authorization(
    monkeypatch,
):
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "custom-responses")

    normalized = worker_module._normalize_codex_stop_error_reason(
        "unexpected status 401 Unauthorized: Missing bearer or basic "
        "authentication in header, url: https://api.example.invalid/v1/responses"
    )

    assert normalized.startswith("codex_provider_auth_failed:")


def test_consume_once_chatgpt_codex_forbidden_waits_for_authorization(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    failure = (
        "unexpected status 403 Forbidden: <html>blocked</html>, "
        "url: https://chatgpt.com/backend-api/codex/responses, "
        "cf-ray: a17c9a26aeb585e3-HKG"
    )

    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
        scripted_runner=FailingTaskRunner(failure),
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        attempts, error, available_at = db.execute(
            "select attempts, error, available_at from reply_tasks"
        ).fetchone()
    assert attempts == 0
    assert error.startswith("codex_provider_auth_failed:")
    assert "ChatGPT Codex backend rejected the service session" in error
    assert available_at == "2026-05-13 17:15:00"
    error_kinds = [error.kind for error in worker.store.list_errors(limit=10)]
    assert "reply_task_authorization" in error_kinds
    assert "reply_task_retry" not in error_kinds


def test_consume_once_codex_provider_transport_failure_waits_for_recovery(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    failure = (
        "stream disconnected before completion: error sending request "
        "for url (https://api.openai.com/v1/responses)"
    )

    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
        scripted_runner=FailingTaskRunner(failure),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        attempts, error, available_at = db.execute(
            "select attempts, error, available_at from reply_tasks"
        ).fetchone()
    assert attempts == 0
    assert error.startswith("codex_provider_unavailable:")
    assert "disconnected before completion" in error
    assert available_at == "2026-05-13 17:01:00"
    error_kinds = [error.kind for error in worker.store.list_errors(limit=10)]
    assert "reply_task_provider_recovery" in error_kinds
    assert "reply_task_authorization" not in error_kinds
    assert any(
        notification["title"] == "CEO task waiting for Codex provider recovery: Friday"
        for notification in notifications
    )


def test_consume_once_external_dependency_honors_attempt_limit(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    runner = FakeAgentResultRunner(
        AutoReplyStore(tmp_path / "worker.sqlite3"),
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    "remote context compaction unavailable",
                    code="codex_dependency_unavailable",
                    retryable=True,
                ),
                (),
                "dependency-session",
            )
        ],
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
        scripted_runner=runner,
    )
    worker.store.upsert_conversation(
        "cid-1",
        "Friday",
        False,
        "full-codex-session",
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    task = worker.store.list_reply_tasks(statuses=("failed",), limit=1)[0]
    assert task.attempts == 1
    assert task.error == "codex_dependency_unavailable"
    assert worker.store.get_codex_session_id("cid-1") == "full-codex-session"
    run = _get_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
    )
    assert run is not None
    assert run.codex_session_id == "dependency-session"
    assert worker.store.count_reply_attempts() == 1


def test_consume_once_native_codex_transport_fallback_auth_failure_waits_for_recovery(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})

    failure = (
        "stream disconnected before completion: native codex exec transport "
        "fallback ended with unexpected status 401 Unauthorized: Missing "
        "bearer or basic authentication in header, url: "
        "https://api.openai.com/v1/responses"
    )

    codex = FakeCodex([])
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
        scripted_runner=FailingTaskRunner(failure),
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        error = db.execute("select error from reply_tasks").fetchone()[0]
    assert error.startswith("codex_provider_unavailable:")
    assert "omitted the authenticated request header" in error


def test_unresolvable_non_candidate_sender_does_not_block_conversation(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("OA审批通知", sender_user_id=None)]},
        current_user_error=RuntimeError("sender not resolved"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_errors() == 0


def test_single_chat_rendered_schedule_asks_for_readable_calendar_detail(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN, reason="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_single_chat_rendered_schedule_asks_for_readable_calendar_detail",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert '"kind": "dingtalk_calendar"' in agent_prompt(worker)
    assert "dws calendar event list --start" in agent_prompt(worker)
    assert final_sent(dws) == []
    assert dws.dings == []
    assert worker.store.has_seen("msg-1") is False
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_non_text_calendar_without_detail_asks_for_readable_calendar_detail(
    tmp_path: Path, monkeypatch
):
    trigger = message("日程卡片", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN, reason="日程详情仍不可读")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_non_text_calendar_without_detail_asks_for_readable_calendar_detail",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws calendar event list --start" in agent_prompt(worker)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_calendar_link_message_is_handled_as_calendar_invite(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "好的明哥 dingtalk://dingtalkclient/action/open_mini_app?"
        "page=pages%2Fdetail%2Findex%3FuniqueId%3Dinvite-1%26recurrenceId%3D",
        single_chat=True,
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="国寿Demo思路",
        start_time="2026-05-30T14:00:00+08:00",
        end_time="2026-05-30T15:00:00+08:00",
        description="",
        organizer="韩露",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_link_message_is_handled_as_calendar_invite",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "invite-1" in prompt
    assert "dws calendar event get --id invite-1 --format json" in prompt
    assert "国寿Demo思路" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_calendar_link_message_is_handled_as_calendar_invite"
    )
    assert attempt.send_status == "completed"


def test_calendar_invite_still_injects_calendar_context_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "明哥看下这个日程 dingtalk://dingtalkclient/action/open_mini_app?"
        "page=pages%2Fdetail%2Findex%3FuniqueId%3Dinvite-1%26recurrenceId%3D",
        single_chat=True,
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Hyperion 客户复盘会",
        start_time="2026-05-30T14:00:00+08:00",
        end_time="2026-05-30T15:00:00+08:00",
        description="复盘 Hyperion 客户反馈，并确认下周跟进材料。",
        organizer="韩露",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="日程上下文足够判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event get --id invite-1 --format json" in prompt
    assert "Hyperion 客户复盘会" not in prompt
    assert dws.read_doc_calls == []
    assert dws.download_doc_calls == []
    assert dws.search_document_calls == []


def test_bare_calendar_card_uses_unique_pending_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Preseen x Walmart",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和组织者足以判断需要参加客户会议。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程；标题和组织者足以判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_unique_pending_invite_from_sender",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "Preseen x Walmart" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_bare_calendar_card_uses_unique_pending_invite_from_sender"
    )
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_calendar_response_organizer_error_is_terminal_noop(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="项目组反馈讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    dws.calendar_response_error = DwsError(
        "code: 300000, developerMessage: Cannot change response status of event organizer.",
        code="300000",
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="组织者本人不需要文字回复。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_calendar_response_organizer_error_is_terminal_noop",
    )
    worker.run_once()

    assert final_sent(dws) == []
    assert_calendar_agent_contract(worker, dws)
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert worker.store.list_agent_execution_receipts(1) == []


def test_calendar_response_missing_event_error_is_terminal_noop(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户会议",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer="客户",
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    dws.calendar_response_error = DwsError(
        "DWS calendar/respond returned Event does not exist",
        code="business_error",
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="日程已不存在，不需要文字回复。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_calendar_response_missing_event_error_is_terminal_noop",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert worker.store.count_reply_tasks(status="failed") == 0
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert worker.store.list_agent_execution_receipts(1) == []


def test_send_reply_calendar_response_failure_does_not_send_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户续约方案讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_response_error = DwsError("calendar accept failed", code="500")
    dws.calendar_events["agent-live-read"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="我先参加，重点看续约方案。",
            reason="客户续约会议有明确业务价值，应该参加。",
            calendar_response_status="accepted",
            audit_summary="已读取待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_send_reply_calendar_response_failure_does_not_send_reply",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "calendar_needs_human"
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    material = next(
        item for item in context.materials if item.kind == "dingtalk_calendar"
    )
    assert material.read_commands[0].startswith("dws calendar event list ")


def test_rendered_calendar_card_without_message_type_uses_unique_pending_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="MB 营销proposal 终版确认",
        start_time="2026-06-04T10:00:00+08:00",
        end_time="2026-06-04T11:00:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题足以判断先暂定。",
            calendar_response_status="tentative",
            audit_summary="已按唯一待响应日程匹配裸日程卡片。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_rendered_calendar_card_without_message_type_uses_unique_pending_invite_without_change_time",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "MB 营销proposal 终版确认" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.codex_reason.startswith(
        "test_rendered_calendar_card_without_message_type"
    )
    assert json.loads(attempt.audit_tool_events_json) == []


def test_bare_calendar_card_enriches_sender_pending_invites_to_match_recent_create_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="吴柯欣 - 招聘专员 - 三面",
        start_time="2026-06-07T13:30:00+08:00",
        end_time="2026-06-07T14:30:00+08:00",
        description="",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    enriched_invite = invite.model_copy(
        update={
            "description": "候选人：吴柯欣\n岗位：招聘专员\n轮次：三面",
            "created_ms": int(
                datetime(
                    2026,
                    5,
                    13,
                    18,
                    0,
                    0,
                    tzinfo=ZoneInfo("Asia/Shanghai"),
                ).timestamp()
                * 1000
            ),
        }
    )
    older_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="HR 周例会",
        start_time="2026-06-08T13:30:00+08:00",
        end_time="2026-06-08T14:45:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite,
        older_invite,
    ]
    dws.calendar_event_details["invite-1"] = enriched_invite
    dws.calendar_event_details["invite-2"] = older_invite
    dws.calendar_events[f"{enriched_invite.start_time}|{enriched_invite.end_time}"] = [
        enriched_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="已读取候选人面试日程并接受。",
            calendar_response_status="accepted",
            audit_summary="已通过详情接口读取刚创建的待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_enriches_sender_pending_invites_to_match_recent_create_time",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "候选人：吴柯欣" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_existing_dry_run_calendar_response_is_executed_without_rerunning_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不应该重新生成",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="标题足以判断需要接受。",
        calendar_event_id="invite-1",
        calendar_response_status="accepted",
        send_status="dry_run",
    )

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_existing_dry_run_calendar_response_is_executed_without_rerunning_codex",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "dry_run"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest.id != attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"


def test_retry_existing_calendar_response_missing_event_is_terminal_noop(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_response_error = DwsError(
        "DWS calendar/respond returned Event does not exist",
        code="business_error",
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="标题足以判断需要接受。",
        calendar_event_id="invite-1",
        calendar_response_status="accepted",
        send_status="dry_run",
    )
    script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_retry_existing_calendar_response_missing_event_is_terminal_noop",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    updated = worker.store.get_reply_attempt(attempt_id)
    assert updated.send_status == "dry_run"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest.id != attempt_id
    assert latest.send_status == "skipped"


def test_calendar_response_respects_worker_dry_run(tmp_path: Path, monkeypatch):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户方案确认",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题足以判断需要接受。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    script_no_action(worker)
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_bare_calendar_card_uses_already_accepted_invite_as_context(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="主持会议",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="主持人需要参加。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
        created_ms=message_time_ms,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个日程已经接受，后面按会议主题准备。",
            reason="日程已经接受，标题和描述足够判断。",
            audit_summary="已按消息时间匹配同一发送人刚创建的日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_already_accepted_invite_as_context",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "主持会议" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_bare_calendar_card_uses_already_accepted_invite_as_context"
    )
    assert attempt.send_status == "completed"


def test_bare_calendar_card_prefers_pending_attendee_invite_over_resolved_sender_event(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    resolved_sender_event = DwsCalendarEvent(
        event_id="resolved-1",
        title="售前材料和商机周会",
        start_time="2026-05-16T10:00:00+08:00",
        end_time="2026-05-16T11:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
        created_ms=int(
            datetime(
                2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp()
            * 1000
        ),
    )
    pending_attendee_event = DwsCalendarEvent(
        event_id="pending-1",
        title="融资开发关键demo review和风险卡点讨论",
        start_time="2026-05-16T18:30:00+08:00",
        end_time="2026-05-16T20:30:00+08:00",
        organizer="张毅倜(ET)",
        attendees=[trigger.sender_name],
        self_response_status="needsAction",
        status="confirmed",
    )
    accepted_conflict = DwsCalendarEvent(
        event_id="conflict-1",
        title="融资对齐交流",
        start_time="2026-05-16T18:30:00+08:00",
        end_time="2026-05-16T19:00:00+08:00",
        organizer="Lily",
        self_response_status="accepted",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        resolved_sender_event,
        pending_attendee_event,
    ]
    dws.calendar_events[
        f"{pending_attendee_event.start_time}|{pending_attendee_event.end_time}"
    ] = [pending_attendee_event, accepted_conflict]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="融资对齐交流已经占用同一时间，先不接受。",
            calendar_response_status="declined",
            audit_summary="应优先处理待本人响应的 18:30 日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_prefers_pending_attendee_invite_over_resolved_sender_event",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "融资开发关键demo review和风险卡点讨论" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_already_accepted_calendar_response_is_noop_without_forced_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="商机周会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="这个会我接受。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_already_accepted_calendar_response_is_noop_without_forced_reply",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert attempt.calendar_response_result_json == ""


def test_send_reply_with_already_accepted_calendar_status_does_not_call_response_api(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="商机周会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="已看到，按商机清单和客户优先级准备。",
            reason="日程已接受，只同步会前准备重点。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_send_reply_with_already_accepted_calendar_status_does_not_call_response_api",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "completed"
    assert attempt.calendar_response_result_json == ""


def test_calendar_response_verifies_result_before_sending_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    still_pending = invite.model_copy()
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    dws.calendar_event_details["invite-1"] = still_pending
    dws.calendar_response_updates_details = False
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个会我接受。",
            reason="需要参加客户复盘。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_calendar_response_verifies_result_before_sending_reply",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "calendar_needs_human"
    assert worker.store.has_seen("msg-1") is False


def test_bare_calendar_card_uses_unique_future_accepted_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【圆桌讨论】测试开发岗位人选画像",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="讨论测试开发岗位画像和候选人结论。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        comments=["Alan: 第一位候选人弱不推荐，需要会上定取舍。"],
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="已看到圆桌会，按测试岗位画像和候选人结论来准备。",
            reason="同发送人的唯一未来日程已经匹配。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_unique_future_accepted_invite_without_change_time",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "【圆桌讨论】测试开发岗位人选画像" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_bare_calendar_card_uses_closest_recent_pending_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    matched_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="售前候选人二面",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
    )
    nearby_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="管理工作讨论",
        start_time="2026-05-17T09:00:00+08:00",
        end_time="2026-05-17T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms + 2 * 60 * 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        matched_invite,
        nearby_invite,
    ]
    dws.calendar_events[f"{matched_invite.start_time}|{matched_invite.end_time}"] = [
        matched_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="候选人二面需要参加。",
            calendar_response_status="accepted",
            audit_summary="已按消息时间匹配最近创建的待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_closest_recent_pending_invite_from_sender",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "售前候选人二面" not in prompt


def test_bare_calendar_card_uses_single_chat_sender_attendee_invite(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="管理工作讨论",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        description="会议主要结论",
        organizer="系统日历",
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms,
        attendees=[trigger.sender_name],
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和描述足以判断需要参加。",
            calendar_response_status="accepted",
            audit_summary="已按消息时间匹配刚创建的本人待响应日程。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_single_chat_sender_attendee_invite",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "管理工作讨论" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_bare_calendar_card_ignores_sender_pending_invite_changed_too_early(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="过早创建的会议",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 6 * 60 * 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="日程创建时间与触发消息不一致，需要人工确认目标。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_bare_calendar_card_ignores_sender_pending_invite_changed_too_early",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws calendar event list --start" in agent_prompt(worker)
    assert final_sent(dws) == []
    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_bare_calendar_card_does_not_guess_multiple_pending_invites(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        DwsCalendarEvent(
            event_id="invite-1",
            title="客户会 A",
            start_time="2026-05-16T09:00:00+08:00",
            end_time="2026-05-16T10:00:00+08:00",
            organizer=trigger.sender_name,
            self_response_status="needsAction",
            status="confirmed",
        ),
        DwsCalendarEvent(
            event_id="invite-2",
            title="客户会 B",
            start_time="2026-05-17T09:00:00+08:00",
            end_time="2026-05-17T10:00:00+08:00",
            organizer=trigger.sender_name,
            self_response_status="needsAction",
            status="confirmed",
        ),
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="存在多个待响应日程，无法唯一确定目标。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_bare_calendar_card_does_not_guess_multiple_pending_invites",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws calendar event list --start" in agent_prompt(worker)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_bare_calendar_card_uses_near_upcoming_invite_without_change_time(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    near_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】审工资",
        start_time="2026-05-14T14:30:00+08:00",
        end_time="2026-05-14T15:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    later_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="管理周会",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        near_invite,
        later_invite,
    ]
    dws.calendar_events[f"{near_invite.start_time}|{near_invite.end_time}"] = [
        near_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题和时间足以判断需要接受这次静默会。",
            calendar_response_status="accepted",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_bare_calendar_card_uses_near_upcoming_invite_without_change_time",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "【静默会】审工资" not in prompt
    assert final_sent(dws) == []


def test_bare_calendar_card_uses_pending_invite_created_near_message(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    message_time_ms = int(
        datetime(2026, 5, 13, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        * 1000
    )
    older_invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户会 A",
        start_time="2026-05-16T09:00:00+08:00",
        end_time="2026-05-16T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 2 * 24 * 60 * 60 * 1000,
    )
    matched_invite = DwsCalendarEvent(
        event_id="invite-2",
        title="Mike项目结项会",
        start_time="2026-05-17T09:00:00+08:00",
        end_time="2026-05-17T10:00:00+08:00",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
        created_ms=message_time_ms - 1000,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_events["2026-05-13T17:00:00+08:00|2026-05-27T17:00:00+08:00"] = [
        older_invite,
        matched_invite,
    ]
    dws.calendar_events[f"{matched_invite.start_time}|{matched_invite.end_time}"] = [
        matched_invite
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_bare_calendar_card_uses_pending_invite_created_near_message",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "Mike项目结项会" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert (
        attempt.codex_reason
        == "test_bare_calendar_card_uses_pending_invite_created_near_message"
    )


def test_calendar_retry_ignores_old_system_notification_skip(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    old_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Chat",
        trigger_message_id="msg-1",
        trigger_sender="sender",
        trigger_text="[日程]",
        action=CodexAction.NO_REPLY.value,
        sensitivity_kind="general",
        codex_reason="system_or_notification_message",
        send_status="skipped",
    )
    worker.store.update_reply_attempt(old_attempt_id, send_error="no_reply")

    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Chat",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_calendar_retry_ignores_old_system_notification_skip",
    )
    worker.consume_once()

    assert len(agent_runner(worker).calls) == 1
    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != old_attempt_id
    assert latest.action == "agent_run"
    assert (
        latest.codex_reason
        == "test_calendar_retry_ignores_old_system_notification_skip"
    )
    assert latest.send_status == "needs_human"
    assert worker.store.count_reply_attempts() == 2


def test_calendar_invite_without_description_asks_for_attendance_reason(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="",
        organizer="Mina",
    )
    existing = DwsCalendarEvent(
        event_id="event-1",
        title="产品周会",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="固定例会",
        self_response_status="accepted",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite, existing]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="这场和产品周会冲突，请补充为什么需要优先于现有日程。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_calendar_invite_without_description_asks_for_attendance_reason",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "客户复盘" not in prompt
    assert final_sent(dws) == []
    assert worker.store.has_seen("msg-1") is False
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_calendar_invite_without_description_asks_for_attendance_reason"
    )
    assert attempt.send_status == "needs_human"


def test_calendar_invite_ignores_declined_overlapping_event(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目结项会",
        start_time="2026-06-05T10:30:00+08:00",
        end_time="2026-06-05T11:30:00+08:00",
        description="",
        organizer="王天浩",
    )
    declined_existing = DwsCalendarEvent(
        event_id="event-1",
        title="销售周会",
        start_time="2026-06-05T10:00:00+08:00",
        end_time="2026-06-05T12:00:00+08:00",
        description="到期续约",
        status="confirmed",
        self_response_status="declined",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [
        invite,
        declined_existing,
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="已拒绝过重叠会议，标题足以判断新会议可接受。",
            calendar_response_status="accepted",
            audit_summary="已读取日程；重叠会议是 declined，不构成冲突。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_ignores_declined_overlapping_event",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "Mike项目结项会" not in prompt
    assert final_sent(dws) == []


def test_calendar_invite_ignores_pending_overlapping_event(tmp_path: Path, monkeypatch):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="",
        organizer="Mina",
    )
    pending_existing = DwsCalendarEvent(
        event_id="event-1",
        title="待确认会议",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="待确认",
        status="confirmed",
        self_response_status="needsAction",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [
        invite,
        pending_existing,
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="请补充这场会议希望我决策或输入的内容。",
            reason="calendar_agent_needs_more_context",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_calendar_invite_ignores_pending_overlapping_event",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "客户复盘" not in prompt
    assert final_sent(dws) == []


def test_calendar_invite_without_description_can_be_tentative_without_conflict(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户复盘",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="标题看起来相关但价值不够明确，先暂定。",
            calendar_response_status="tentative",
            audit_summary="已读取日程；标题足以判断先暂定，不需要聊天追问。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_without_description_can_be_tentative_without_conflict",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "客户复盘" not in prompt
    assert "dws calendar event list --start" in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.codex_reason.startswith(
        "test_calendar_invite_without_description_can_be_tentative"
    )


def test_calendar_invite_with_description_asks_codex_to_evaluate_conflict(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="客户升级问题决策",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="需要 Alex 判断是否承诺本周交付，客户 CEO 会参加。",
        organizer="Mina",
    )
    existing = DwsCalendarEvent(
        event_id="event-1",
        title="产品周会",
        start_time="2026-05-14T10:30:00+08:00",
        end_time="2026-05-14T11:30:00+08:00",
        description="固定例会",
        self_response_status="accepted",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite, existing]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个会议和产品周会冲突。按描述看客户升级问题优先级更高，建议接受这场并请产品周会另约。",
            reason="calendar_conflict_evaluated",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_with_description_asks_codex_to_evaluate_conflict",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "客户升级问题决策" not in prompt
    assert "dws calendar event list --start" in prompt
    assert final_sent(dws) == []
    assert worker.store.has_seen("msg-1") is False
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.codex_reason.startswith(
        "test_calendar_invite_with_description_asks_codex"
    )


def test_calendar_prompt_includes_current_response_status(tmp_path: Path, monkeypatch):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="商机周会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="过商机清单。",
        organizer="韩露",
        self_response_status="accepted",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="已看到，按商机清单准备。",
            reason="日程已接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_calendar_prompt_includes_current_response_status",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert '"self_response_status"' not in prompt
    assert "dws calendar event list --start" in prompt


def test_calendar_invite_for_document_review_replies_to_use_document_comment(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="官网文档批阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="请 Alex 批阅官网文档并反馈修改意见。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="请直接@我文档让我批阅即可，只有存疑再约会。",
            reason="calendar_document_review_redirect",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_for_document_review_replies_to_use_document_comment",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "请 Alex 批阅官网文档并反馈修改意见" not in prompt
    assert final_sent(dws) == []


def test_calendar_static_review_description_must_process_task_before_document_redirect(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】官网反馈",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=(
            "请根据官网反馈截图和评论直接给处理结论："
            "上线前必须改、后续可优化，并具体到把 A 改成 B。"
        ),
        organizer="Mina",
        comments=["Mina: 重点看首屏定位和客户案例模块，处理完请评论会议。"],
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text=(
                "可以，这个静默会我直接处理：上线前先收敛首屏 CTA 和表单跳转；"
                "后续再优化客户案例的排序。"
            ),
            reason="calendar_static_review_task_processed",
            calendar_response_status="accepted",
            audit_summary="根据静默会描述直接处理官网反馈任务。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_static_review_description_must_process_task_before_document_redirect",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "上线前必须改、后续可优化" not in prompt
    assert "dws calendar event list --start" in prompt
    assert final_sent(dws) == []


def test_calendar_response_accepts_agent_envelope_domain_payload(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="产品周会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="讨论客户升级问题。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    envelope = AgentEnvelope.model_validate(
        {
            "kind": "reply",
            "user_response": {
                "mode": "send_reply",
                "text": "这个会议需要参加，我会按客户升级问题准备。",
                "sensitivity_kind": "general",
            },
            "system_actions": [
                {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"}
            ],
            "domain_payload": {"calendar_response_status": "accepted"},
            "audit": {
                "summary": "根据日程标题和描述判断需要参加。",
                "documents": [],
                "confidence": 0.8,
            },
        }
    )
    codex = FakeEnvelopeCodex(envelope)
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_response_accepts_agent_envelope_domain_payload",
    )
    worker.run_once()

    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    assert codex.calls == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_calendar_static_review_exposes_minutes_reference_to_agent(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    target_url = (
        "https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true"
    )
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】测试开发工程师 - 候选人A 作业题审阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=f"请阅读听记和作业材料后给处理结论：{target_url}",
        organizer=trigger.sender_name,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.minutes_infos[minutes_id] = {
        "result": {
            "taskUuid": minutes_id,
            "title": "候选人A测试开发技术面记录",
            "url": f"https://shanji.dingtalk.com/app/transcribes/{minutes_id}",
        }
    }
    dws.minutes_summaries[minutes_id] = {
        "result": {"fullSummary": "候选人测试开发基础较完整，但 Agent 工程化偏浅。"}
    }
    dws.minutes_todos[minutes_id] = {
        "result": {"actions": ['{"value":"Alex 给出是否推进录用的处理结论"}']}
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不建议直接推进，建议补充作业后再判断。",
            reason="静默会材料已处理，并接受日程。",
            calendar_response_status="accepted",
            audit_summary="读取静默会听记后给出处理结论。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_static_review_exposes_minutes_reference_to_agent",
    )
    worker.run_once()

    assert dws.minutes_info_calls == []
    assert dws.minutes_summary_calls == []
    assert dws.minutes_todo_calls == []
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "Raw material references and exact read commands" in prompt
    assert minutes_id not in prompt
    assert "dws calendar event list --start" in prompt
    assert dws.doc_comments == []
    assert dws.reply_messages == []
    assert final_sent_at_users(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_calendar_document_reference_is_exposed_to_agent_for_reading(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/no-access"
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="【静默会】材料审阅",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description=f"请阅读材料后给处理结论：{doc_url}",
        organizer=trigger.sender_name,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    dws.doc_infos[doc_url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在没有权限读取这份材料，麻烦补充正文或开权限。",
            reason="calendar_material_unreadable",
            calendar_response_status="accepted",
            audit_summary="静默会材料读取失败，已说明不能判断正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NEEDS_HUMAN,
        "test_calendar_document_reference_is_exposed_to_agent_for_reading",
    )
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "Raw material references and exact read commands" in prompt
    assert '"kind": "dingtalk_doc"' not in prompt
    assert doc_url not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert (
        attempt.codex_reason
        == "test_calendar_document_reference_is_exposed_to_agent_for_reading"
    )


def test_calendar_invite_with_clear_value_auto_accepts_without_chat_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="关键客户交付决策",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="客户 CEO 参加，需要 Alex 判断本周交付承诺。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="Alex 参与有明确业务价值",
            calendar_response_status="accepted",
            audit_summary="日程描述明确，且需要 Alex 做关键客户交付判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_with_clear_value_auto_accepts_without_chat_reply",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    assert worker.store.has_seen("msg-1") is False
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_calendar_invite_with_clear_value_auto_accepts_without_chat_reply"
    )
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_rerun_calendar_card_recovers_event_from_existing_attempt(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目同步",
        start_time="2026-06-08T12:30:00+08:00",
        end_time="2026-06-08T13:00:00+08:00",
        description="客户拜访前同步当前项目情况和后续计划。",
        organizer=trigger.sender_name,
        self_response_status="needsAction",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_event_details["invite-1"] = invite
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="客户拜访前需要同步项目情况和后续计划，有必要参加。",
            calendar_response_status="accepted",
            audit_summary="已从既有 attempt 恢复日历详情并判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="no_reply",
        sensitivity_kind="general",
        calendar_event_id="invite-1",
        calendar_response_status="accepted",
        send_status="calendar",
    )

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_rerun_calendar_card_recovers_event_from_existing_attempt",
    )
    processed_message_id = worker.rerun_message(
        conversation(single_chat=True),
        trigger.open_message_id,
        force_new_decision=True,
    )

    assert processed_message_id == trigger.open_message_id
    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "Mike项目同步" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_rerun_interactive_card_recovers_richer_trigger_from_existing_attempt(
    tmp_path: Path, monkeypatch
):
    live_trigger = message("[互动卡片]", single_chat=True)
    rich_trigger_text = (
        "这是什么意思来着？\n"
        "https://n.dingtalk.com/dingding/dd-todo/detail/index.html?taskId=todo-1"
    )
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [live_trigger]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    captured_tasks = []
    monkeypatch.setattr(
        worker,
        "_process_queued_task",
        lambda _conversation, task: captured_tasks.append(task) or True,
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=live_trigger.open_message_id,
        trigger_sender=live_trigger.sender_name,
        trigger_text=rich_trigger_text,
        action="ask_clarifying_question",
        sensitivity_kind="general",
        send_status="sent",
    )

    worker.rerun_message(
        conversation(single_chat=True),
        live_trigger.open_message_id,
        force_new_decision=True,
    )

    task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    assert task.trigger_text == rich_trigger_text
    assert captured_tasks[0].trigger_text == rich_trigger_text


def test_rerun_calendar_card_matches_already_accepted_invite_from_sender(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="Mike项目同步",
        start_time="2026-05-14T12:30:00+08:00",
        end_time="2026-05-14T13:00:00+08:00",
        description="客户拜访前同步当前项目情况和后续计划。",
        organizer=trigger.sender_name,
        self_response_status="accepted",
        status="confirmed",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="客户拜访前需要同步项目情况和后续计划，有必要参加。",
            calendar_response_status="accepted",
            audit_summary="已从同发送人的已接受日程恢复详情并判断需要接受。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    search_start, search_end = worker._calendar_pending_invite_search_window(trigger)
    dws.calendar_events[f"{search_start}|{search_end}"] = [invite]
    dws.calendar_events[f"{invite.start_time}|{invite.end_time}"] = [invite]
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        action="ask_clarifying_question",
        sensitivity_kind="general",
        codex_reason="calendar_detail_unreadable",
        send_status="sent",
    )

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_rerun_calendar_card_matches_already_accepted_invite_from_sender",
    )
    processed_message_id = worker.rerun_message(
        conversation(single_chat=True),
        trigger.open_message_id,
        force_new_decision=True,
    )

    assert processed_message_id == trigger.open_message_id
    assert len(agent_runner(worker).calls) == 1
    prompt = assert_calendar_agent_contract(worker, dws)
    assert "dws calendar event list --start" in prompt
    assert "Mike项目同步" not in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_calendar_invite_no_reply_without_auto_accept_reason_does_not_accept(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="not relevant",
            audit_summary="不需要处理。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.NO_ACTION,
        "test_calendar_invite_no_reply_without_auto_accept_reason_does_not_accept",
    )
    worker.run_once()

    assert dws.calendar_responses == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert attempt.send_error == ""


def test_calendar_invite_agent_can_decline_without_chat_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="状态同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息，不需要 Alex 输入。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="会议只是状态同步，不需要本人参加。",
            calendar_response_status="declined",
            audit_summary="已读取日程；描述显示只是同步信息，不需要本人输入。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    _calendar_runner = script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_calendar_invite_agent_can_decline_without_chat_reply",
    )
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert_calendar_agent_contract(worker, dws)
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert (
        attempt.codex_reason
        == "test_calendar_invite_agent_can_decline_without_chat_reply"
    )
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""


def test_queued_calendar_response_completes_task_with_terminal_attempt_update(
    tmp_path: Path, monkeypatch
):
    trigger = message("[日程]", single_chat=True, message_type="calendar")
    invite = DwsCalendarEvent(
        event_id="invite-1",
        title="状态同步会",
        start_time="2026-05-14T10:00:00+08:00",
        end_time="2026-05-14T11:00:00+08:00",
        description="同步信息，不需要 Alex 输入。",
        organizer="Mina",
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.calendar_invites["msg-1"] = invite
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="会议只是状态同步，不需要本人参加。",
            calendar_response_status="declined",
            audit_summary="已读取日程；描述显示只是同步信息，不需要本人输入。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_calendar_result(
        worker,
        ScriptOutcome.COMPLETED,
        "test_queued_calendar_response_completes_task_with_terminal_attempt_update",
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 1

    assert_calendar_agent_contract(worker, dws)
    assert worker.store.count_reply_tasks(status="done") == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_structured_link_card_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "\n".join(
            [
                "表单标题",
                "字段一: A",
                "字段二: B",
                "字段三: C",
                "字段四: D",
                "[dingtalk://dingtalkclient/action/open_platform_link?x=1](dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_single_chat_alidocs_card_reaches_codex_as_material_reference(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/weekly123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/weekly123"
    trigger = message(
        "\n".join(
            [
                "总裁办每周讨论-20260531",
                "![image](https://gw.alicdn.com/imgextra/i4/example.png)",
                "字段一: A",
                "字段二: B",
                f"[{doc_url}]({doc_url})",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这份周会材料需要先读材料再判断。",
            audit_summary="私聊文档卡片已进入 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert canonical_doc_url in prompt
    assert "dws doc read --node" in prompt
    assert "本周重点：处理项目 owner" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert final_sent(dws) == []


def test_structured_approval_card_is_processed_by_audit_agent(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "\n".join(
            [
                "闫成成提交的项目立项全流程（第一曲线）",
                "项目经理: 闫成成",
                "销售经理: 曹宇航",
                "项目类型: 点云;图片;视频",
                "总预估数据量: 2546573",
                "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
                "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
                "%2Fpchomepage.htm%3Fswfrom%3Doa%26dinghash%3Dapproval]"
                "(dingtalk://dingtalkclient/action/open_platform_link?x=1)",
            ]
        ),
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="闫成成提交的项目立项全流程（第一曲线）",
            process_name="项目立项全流程",
        )
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="审批需要本人处理",
            audit_summary="结构化 OA 卡片需要按审批审阅原则处理。",
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "审批需要本人处理",
            code="oa_review_required",
        ),
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws oa +list-pending --format json" not in agent_prompt(worker)
    assert '"read_commands": []' in agent_prompt(worker)
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert dws.oa_approval_actions == []


def test_audit_agent_oa_receipt_is_persisted_with_approval_history(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]审批待办", single_chat=True)
    trigger.raw_payload = {
        "processInstanceId": "proc-1",
        "taskId": "task-1",
    }
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    result = ScriptResult(
        outcome=ScriptOutcome.NEEDS_HUMAN,
        summary="已评论要求补充复评标准。",
        error=AgentError(code="OA_MATERIAL_INCOMPLETE"),
        oa_action_receipt=ScriptOaActionReceipt(
            process_instance_id="proc-1",
            task_id="task-1",
            action="comment",
            remark="请补充延期时长、量化目标和复评标准。",
            result={"success": True},
        ),
    )
    script_agent_result(worker, result)

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_action == "comment"
    assert attempt.oa_remark == "请补充延期时长、量化目标和复评标准。"
    assert json.loads(attempt.oa_action_result_json) == {"success": True}


def test_existing_commented_oa_attempt_is_terminal(tmp_path: Path, monkeypatch):
    trigger = message(
        "[Ding]张静提醒您审批他的录用申请 https://aflow.dingtalk.com/dingtalk/pc/query"
        "/pchomepage.htm?procInstId=proc-1&taskId=task-1&swfrom=oa",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="oa_approval",
        sensitivity_kind="internal_finance",
        codex_reason="退回",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补充预算来源。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "Safe prior execution receipts" in agent_prompt(worker)
    assert "退回" in agent_prompt(worker)
    assert "dws oa approval detail --instance-id proc-1" in agent_prompt(worker)
    assert "dws oa approval tasks --instance-id proc-1" in agent_prompt(worker)
    assert dws.oa_approval_actions == []
    assert dws.oa_approval_comments == []
    assert worker.store.count_reply_attempts() == 2
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.action == "agent_run"
    assert latest.send_status == "skipped"


def test_single_chat_oa_follow_up_reuses_recent_review_target(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "已经补充了二面结论、录用理由、薪资依据、实习目标",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
    )
    previous_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-old-oa",
        trigger_sender="周俊杰",
        trigger_text="[Ding]周俊杰提醒您审批他的录用申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url=("https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"),
        oa_action="退回",
        oa_remark="请补充二面结论、录用理由、薪资依据、实习目标。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="commented",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_attempts set created_at=?, updated_at=? where id=?",
            ("2026-05-13 09:30:00", "2026-05-13 09:30:00", previous_attempt_id),
        )

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "Safe prior execution receipts" in agent_prompt(worker)
    assert dws.oa_approval_actions == []
    attempts = worker.store.list_reply_attempts(limit=2)
    assert attempts[0].trigger_message_id == "msg-1"
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "skipped"


def test_automatic_sync_notification_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("AI 自动同步成功：董事会筹备组纪要", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_file_state_notification_is_skipped_before_codex(tmp_path: Path, monkeypatch):
    trigger = message("文档已更新：董事会材料", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_project_status_notification_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("项目立项已提交", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_status_like_message_with_followup_request_is_processed_by_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("文件已更新，帮忙看一下", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="带请求的文件状态消息需要交给 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1


def test_question_with_link_still_goes_to_codex(tmp_path: Path, monkeypatch):
    trigger = message(
        "这个链接里的方案怎么看？ https://example.com/a", single_chat=True
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1


def test_bare_external_link_is_processed_by_codex(tmp_path: Path, monkeypatch):
    trigger = message("@明哥 https://example.com/a", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="test",
            audit_summary="普通外链需要交给 agent 判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).action == "agent_run"


def test_bare_dingtalk_internal_link_is_skipped_before_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@明哥 [dingtalk://dingtalkclient/page/flash_minutes_detail?x=1]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?x=1)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 0
    assert worker.store.has_seen("msg-1") is True


def test_ai_minutes_permission_request_is_auto_approved_without_codex_or_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId=minutes-1&from=8]",
        single_chat=True,
    )
    request = DwsMinutesPermissionRequest(
        uuids=["minutes-1"],
        member_uids=[451416406],
        policy_id=3,
        role_sub_resource_ids=["OrigContent", "Summary"],
        cover_permission=False,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_permission_requests["msg-1"] = request
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert '"kind": "dingtalk_minutes"' in agent_prompt(worker)
    assert "dws minutes get info --id minutes-1 --format json" in agent_prompt(worker)
    assert final_sent(dws) == []
    assert dws.added_minutes_permissions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_ding_approval_reminder_is_processed_by_audit_agent(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]张静提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="张静提交的录用申请",
            process_name="录用申请",
        )
    ]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="审批需要本人处理",
            audit_summary="审批催办需要按 OA 审阅原则处理。",
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "审批需要本人处理",
            code="oa_review_required",
        ),
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws oa +list-pending --format json" not in agent_prompt(worker)
    assert '"read_commands": []' in agent_prompt(worker)
    assert dws.oa_approval_actions == []
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_oa_approval_missing_applicant_records_failed_delivery(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.NEEDS_HUMAN,
                    "审批申请人缺失，无法发送退回通知。",
                    code="missing_oa_applicant_user_id",
                ),
                (),
                "oa-missing-applicant",
            )
        ],
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "missing_oa_applicant_user_id"


def test_oa_reject_action_still_requires_task_id(tmp_path: Path, monkeypatch):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的合同审批 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.NEEDS_HUMAN,
                    "审批实例缺少当前任务 ID。",
                    code="missing_oa_approval_target",
                ),
                (),
                "oa-missing-task",
            )
        ],
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    assert dws.oa_approval_comments == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "missing_oa_approval_target"
    assert "proc-1" in worker._test_agent_runner.calls[0][2].materials[0].reference


def test_oa_reject_action_requires_parseable_current_user_ownership(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的合同审批 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.NEEDS_HUMAN,
                    "无法确认审批任务归属。",
                    code="oa_ownership_unverified",
                ),
                (),
                "oa-ownership-unverified",
            )
        ],
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    assert dws.oa_approval_comments == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "oa_ownership_unverified"
    assert dws.oa_approval_actions == []


def test_oa_approval_does_not_execute_task_that_is_not_current_user(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "CANCELED",
                    "userid": "principal-user-1",
                },
                {"taskid": "task-2", "task_status": "RUNNING", "userid": "other-user"},
            ]
        }
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=False,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.NO_ACTION,
                    "审批任务不属于当前用户。",
                    code="oa_task_not_current_user",
                ),
                (),
                "oa-not-current-user",
            )
        ],
    )

    worker.run_once()

    assert dws.oa_approval_actions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "oa_task_not_current_user"


def test_ding_approval_reminder_injects_openapi_detail_when_dws_form_is_empty(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.pending_oa_approvals = [
        DwsOaApprovalCandidate(
            process_instance_id="proc-1",
            title="刘瑞安提交的录用申请",
            process_name="录用申请",
        )
    ]
    dws.oa_approval_details["proc-1"] = {"result": {"formValueVOS": [{"details": []}]}}
    dws.oa_approval_records["proc-1"] = {"result": {"operationRecords": []}}
    dws.oa_approval_tasks["proc-1"] = {"result": {"taskIdList": [{"taskId": 1}]}}
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "刘瑞安提交的录用申请",
            "form_component_values": [
                {
                    "name": "试用期工作内容和转正要求",
                    "value": "3个月内完成 Friday 场景闭环",
                }
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )

    script_no_action(worker)
    worker.run_once()
    assert len(agent_runner(worker).calls) == 1
    assert "dws oa +list-pending --format json" not in agent_prompt(worker)
    assert '"read_commands": []' in agent_prompt(worker)
    assert "试用期工作内容和转正要求" not in agent_prompt(worker)


def test_oa_approval_detail_always_includes_openapi_comments(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]郑威格提醒您审批他的项目立项 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = {
        "result": {"formValueVOS": [{"name": "项目名称", "value": "奥迪第三曲线项目"}]}
    }
    dws.oa_approval_records["proc-1"] = {
        "result": {
            "operationRecords": [
                {"operationType": "ADD_REMARK", "userId": "principal-user-1"}
            ]
        }
    }
    dws.oa_approval_tasks["proc-1"] = {"result": {"taskIdList": [{"taskId": "task-1"}]}}
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "郑威格提交的项目立项全流程（第三曲线）",
            "form_component_values": [
                {"name": "项目名称", "value": "奥迪第三曲线项目"}
            ],
            "operation_records": [
                {
                    "operation_type": "ADD_REMARK",
                    "userid": "principal-user-1",
                    "remark": "证据不严谨，需要补充模型对比结论。",
                }
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )

    script_no_action(worker)
    worker.run_once()
    prompt = agent_prompt(worker)
    assert "dws oa approval detail --instance-id proc-1 --format json" in prompt
    assert "dws oa approval tasks --instance-id proc-1 --format json" in prompt
    assert "证据不严谨，需要补充模型对比结论。" not in prompt


def test_oa_approval_detail_param_error_is_recovered_by_openapi(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]郑威格提醒您审批他的项目立项 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError(
        "dws command failed: server_error_code=PARAM_ERROR",
        code="1",
    )
    dws.oa_approval_records["proc-1"] = {
        "result": {"operationRecords": [{"operationType": "START_PROCESS_INSTANCE"}]}
    }
    dws.oa_approval_tasks["proc-1"] = {"result": {"taskIdList": [{"taskId": "task-1"}]}}
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "郑威格提交的项目立项全流程（第三曲线）",
            "form_component_values": [
                {"name": "项目名称", "value": "奥迪第三曲线项目"}
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )

    script_no_action(worker)
    worker.run_once()
    prompt = agent_prompt(worker)
    assert "dws oa approval detail --instance-id proc-1 --format json" in prompt
    assert "dws oa approval tasks --instance-id proc-1 --format json" in prompt
    assert "recovered_by_openapi" not in prompt
    assert "奥迪第三曲线项目" not in prompt


def test_oa_approval_is_not_discovered_when_dws_gate_needs_login(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message(
        "[Ding]刘瑞安提醒您审批他的录用申请 "
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.oa_approval_details["proc-1"] = DwsError("not authenticated", code="2")
    dws.oa_approval_records["proc-1"] = DwsError("not authenticated", code="2")
    dws.oa_approval_tasks["proc-1"] = DwsError("not authenticated", code="2")
    dws.openapi_oa_details["proc-1"] = DwsError("not authenticated", code="2")
    dws.auth_status_response = {
        "authenticated": False,
        "token_valid": False,
        "refresh_token_valid": False,
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates=fixed_channel_gates(ChannelGateState.NEEDS_LOGIN),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert dws.list_unread_calls == 0
    assert dws.auth_login_starts == 1
    assert notifications == []


def test_oa_approval_dry_run_uses_review_only_mode_and_keeps_live_retry_open(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[Ding]张静提醒您审批他的录用申请 https://aflow.dingtalk.com/dingtalk/pc/query"
        "/pchomepage.htm?procInstId=proc-1&taskId=task-1&swfrom=oa",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该走聊天回复")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=True,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "dry-run approval requires live execution",
            code="oa_dry_run_not_executed",
        ),
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert dws.oa_approval_actions == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.count_reply_attempts() == 1


def test_bare_dingtalk_approval_wrapper_reaches_audit_agent(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "[dingtalk://dingtalkclient/action/open_platform_link?pcLink="
        "https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
        "%2Fpchomepage.htm%3Fswfrom%3Doa%26dinghash%3Dapproval]"
        "(dingtalk://dingtalkclient/action/open_platform_link?x=1)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "dws oa +list-pending --format json" not in agent_prompt(worker)
    assert '"read_commands": []' in agent_prompt(worker)
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_group_mention_sends_signed_reply(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Alex Chen(明哥) @晓民 这个怎么处理？",
        quoted_content="这个ACL表看一下",
    )
    trigger.mentioned_user_ids = ["principal-user-1", "mentioned-user-1"]
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                trigger,
                message("前面上下文", message_id="msg-0"),
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="group-reply")

    worker.run_once()

    assert final_sent(dws) == []
    assert final_sent_at_users(dws) == []
    assert dws.reply_messages == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Original trigger" in prompt
    assert "Recent conversation context" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Alex 的钉钉自动回复分身" not in prompt
    assert '"conversation_title": "Friday"' in prompt
    assert "@Alex Chen(明哥) @晓民 这个怎么处理？" in prompt
    assert '"sender": "quoted_message"' in prompt
    assert "这个ACL表看一下" in prompt
    assert "前面上下文" in prompt


def test_group_reply_replaces_leading_name_with_structured_at(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 帮忙看一下")
    trigger.sender_name = "ET"
    trigger.sender_open_dingtalk_id = "open-et"
    group = conversation()
    dws = FakeDws([group], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="@ET 你要再往下收一层",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="group-structured-at")

    worker.run_once()

    assert dws.reply_messages == []
    assert final_sent(dws) == []
    assert final_sent_at_users(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_success_notification_keeps_full_reply_text(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reply_body = "我倾向于按这个方向收敛：" + "先看行业经验和交付闭环，" * 12
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text=reply_body)
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="long-notification-reply")
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert notifications == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_success_notification_prepares_dingtalk_open_conversation_url(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 请给一下你的看法")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="收到"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="notification-open-url")
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert dws.client_cid_calls == []
    assert notifications == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert json.loads(attempt.audit_tool_events_json) == []


def test_leak_check_feedback_regenerates_reply_before_blocking(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    trigger.mentioned_user_ids = ["principal-user-1"]
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="参考 [1]，先按A方案推进",
                audit_summary="只需上下文判断，当前消息已足够确认。",
            ),
            AgentEnvelope.model_validate(
                {
                    "kind": "reply",
                    "user_response": {
                        "mode": "send_reply",
                        "text": "先按A方案推进",
                        "sensitivity_kind": "general",
                    },
                    "system_actions": [
                        {
                            "type": "send_dingtalk_reply",
                            "reply_text_ref": "user_response.text",
                        }
                    ],
                    "domain_payload": {},
                    "audit": {
                        "summary": "收到安全反馈后，改写为不带来源引用的回复。",
                        "documents": [],
                        "confidence": 0.8,
                    },
                }
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    script_completed_result(worker, operation_id="safe-direct-agent-output")

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert worker.store.count_errors() == 0
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""
    assert json.loads(attempt.audit_tool_events_json) == []


def test_dingtalk_material_links_are_passed_to_codex_without_worker_reading(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    minutes_id = "7632756964333134343836383736303334325f3435313431363430365f35"
    trigger = message(
        "\n".join(
            [
                f"文档: {doc_url}",
                f"听记: dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId={minutes_id}&from=8",
                "@Alex Chen(明哥) 判断这个材料是否能推进",
            ]
        )
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先读材料再判断")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.minutes_info_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert canonical_doc_url in prompt
    assert minutes_id in prompt
    assert "dws doc read --node" in prompt
    assert "dws minutes get info --id" in prompt


def test_lark_doc_link_is_passed_to_codex_as_material_reference(
    tmp_path: Path, monkeypatch
):
    doc_url = (
        "https://zhipu-ai.feishu.cn/wiki/MvIOwPyfCiJHo2ku5rZcx3vpnVh?from=from_copylink"
    )
    canonical_doc_url = "https://zhipu-ai.feishu.cn/wiki/MvIOwPyfCiJHo2ku5rZcx3vpnVh"
    trigger = message(f"{doc_url}\n@Alex Chen(明哥) 看下真实需求")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="已按文档判断")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert '"kind": "lark_doc"' in prompt
    assert canonical_doc_url in prompt
    assert f"lark-cli docs +fetch --doc {canonical_doc_url}" in prompt
    assert "--doc-format markdown --format json --as bot" in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_dingtalk_doc_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc123"
    trigger = message(f"{doc_url} @Alex Chen(明哥) 看下根因和解法")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="按协作方式拆分")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert final_sent(dws) == []
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert canonical_doc_url in prompt
    assert "dws doc read --node" in prompt
    assert "根因是协作方式不对" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_single_chat_doc_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private"
    trigger = message(
        f"{doc_url}\n帮我看下这个方案",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读材料再判断方案。",
                audit_summary="私聊材料引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(agent_runner(worker).calls) == 1
    first_prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in first_prompt
    assert canonical_doc_url in first_prompt
    assert "已获取的钉钉材料:" not in first_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_single_chat_file_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "帮我看下这个文件",
        quoted_content="[文件] 02_下一步推进建议.md",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读取文件再判断。",
                audit_summary="私聊文件材料引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    assert len(agent_runner(worker).calls) == 1
    first_prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in first_prompt
    assert '"kind": "dingtalk_file"' in first_prompt
    assert "02_下一步推进建议.md" in first_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_single_chat_mixed_minutes_and_doc_material_retries_for_doc(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-private"
    trigger = message(
        "听记和方案一起看：\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)\n"
        f"{doc_url}",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为听记单独场景。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会结合方案材料判断。",
                audit_summary="文档材料触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.minutes_info_calls == []
    assert dws.doc_info_calls == []
    assert len(agent_runner(worker).calls) == 1
    first_prompt = agent_prompt(worker)
    assert '"kind": "dingtalk_minutes"' in first_prompt
    assert '"kind": "dingtalk_doc"' in first_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_dingtalk_doc_permission_setup_is_irrelevant_to_worker_material_references(
    tmp_path: Path, monkeypatch
):
    blocked_url = "https://alidocs.dingtalk.com/i/nodes/blocked123"
    readable_url = "https://alidocs.dingtalk.com/i/nodes/readable456?utm_source=im"
    canonical_blocked_url = "https://alidocs.dingtalk.com/i/nodes/blocked123"
    canonical_readable_url = "https://alidocs.dingtalk.com/i/nodes/readable456"
    trigger = message(
        "\n".join(
            [
                f"第一份材料：{blocked_url}",
                f"第二份材料：{readable_url}",
                "@Derek Zen(磊哥) 按第二份材料判断主叙事",
            ]
        )
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[canonical_blocked_url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    dws.docs[canonical_readable_url] = {
        "title": "OpenAI 合作建议补充版",
        "markdown": "核心结论：Stardust 应主打 Expert Signal Flywheel。",
    }
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
        dry_run=True,
    )
    references = worker._material_references([trigger], [trigger])

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert [(reference.kind, reference.reference) for reference in references] == [
        ("dingtalk_doc", canonical_blocked_url),
        ("dingtalk_doc", canonical_readable_url),
    ]


def test_dingtalk_aitable_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    aitable_url = "https://alidocs.dingtalk.com/i/nodes/base123?utm_source=im"
    canonical_url = "https://alidocs.dingtalk.com/i/nodes/base123"
    trigger = message(f"{aitable_url} @Derek Zen(磊哥) 看下进展")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
        dry_run=True,
    )
    references = worker._material_references([trigger], [trigger])

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.get_aitable_base_calls == []
    assert dws.get_aitable_tables_calls == []
    assert dws.query_aitable_record_calls == []
    assert len(references) == 1
    assert references[0].kind == "dingtalk_doc"
    assert references[0].reference == canonical_url


def test_docs_dingtalk_aitable_material_no_reply_retries_without_worker_read(
    tmp_path: Path, monkeypatch
):
    aitable_url = "https://docs.dingtalk.com/i/nodes/base-private?utm_source=im"
    canonical_url = "https://docs.dingtalk.com/i/nodes/base-private"
    trigger = message(
        f"{aitable_url}\n帮我看下这个表格",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="误判为无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="我会先读表格材料再判断。",
                audit_summary="私聊 AI 表格引用触发重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert dws.get_aitable_base_calls == []
    assert len(agent_runner(worker).calls) == 1
    first_prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in first_prompt
    assert canonical_url in first_prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_dingtalk_doc_link_in_context_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context?utm_source=im"
    canonical_doc_url = "https://alidocs.dingtalk.com/i/nodes/doc-in-context"
    context_doc = message(
        f"[文档] 方案: {doc_url}",
        message_id="doc-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        message_id="msg-2",
        quoted_content=f"[文档] 方案: {doc_url}",
    )
    dws = FakeDws([conversation()], {"cid-1": [context_doc, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先收敛需求")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert canonical_doc_url in prompt
    assert "下一步建议：先做客户需求收敛" not in prompt


def test_referenced_file_message_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 明哥comments一下",
        message_id="msg-2",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    trigger.quoted_message_id = "file-msg-1"
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert "02_下一步推进建议.md" in prompt
    assert '"kind": "dingtalk_file"' in prompt
    assert '"read_commands": []' in prompt
    assert "建议正文：先明确客户边界" not in prompt


def test_referenced_file_message_includes_drive_download_command(
    tmp_path: Path, monkeypatch
):
    file_message = message(
        "[文件] HSW 平台业务流程、规则与自动化测试规格.md "
        "fileId: Exel2BLV5z6a2P64hPj2OwkzJgk9rpMq url: url "
        "注意：如需下载使用dws drive download命令下载",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Alex Chen(明哥) 这个md是HSW要补充的用户流程材料，新的平台设计要能覆盖其中的内容",
        message_id="msg-2",
        quoted_content="[文件] HSW 平台业务流程、规则与自动化测试规格.md",
    )
    trigger.quoted_message_id = "file-msg-1"
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="我会先读材料再合并规则"
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    prompt = agent_prompt(worker)
    assert '"reference": "HSW 平台业务流程、规则与自动化测试规格.md"' in prompt
    assert (
        '"dws drive download --node Exel2BLV5z6a2P64hPj2OwkzJgk9rpMq '
        "--output <local-path> --format json"
    ) in prompt


def test_referenced_file_context_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    file_message = message(
        "[文件] 02_下一步推进建议.md",
        message_id="file-msg-1",
    )
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        message_id="msg-2",
    )
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="建议补边界和owner")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    references = worker._material_references([trigger], [file_message, trigger])

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    assert len(references) == 1
    assert references[0].kind == "dingtalk_file"
    assert references[0].reference == "02_下一步推进建议.md"
    assert references[0].source_message_id == "file-msg-1"


def test_referenced_file_with_file_id_is_passed_as_read_command_without_download(
    tmp_path: Path, monkeypatch
):
    file_name = "Stardust_Company_Brief_IMDA_English_20260722152953.pdf"
    file_id = "Exel2BLV5pOEPex5IPjqQX5gWgk9rpMq"
    file_message = message(
        f"[文件] {file_name} fileId: {file_id} url: hidden",
        message_id="file-msg-1",
    )
    trigger = message(
        "最底下的customers部分 可以多写一些大公司，"
        "请 @Derek Zen(磊哥) 直接给我一个改好的pdf版本",
        message_id="msg-2",
    )
    dws = FakeDws([conversation()], {"cid-1": [file_message, trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    references = worker._material_references([trigger], [file_message, trigger])

    assert dws.drive_file_download_calls == []
    assert dws.search_document_calls == []
    assert len(references) == 1
    assert references[0].kind == "dingtalk_file"
    assert references[0].reference == file_name
    assert references[0].read_command == (
        f"dws drive download --node {file_id} --output <local-path> --format json"
    )


def test_referenced_file_reference_does_not_download_or_expose_credentials(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Derek Zen(磊哥) 磊哥comments一下",
        quoted_content="[文件] 02_下一步推进建议.md",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我现在只能看到文件名，麻烦贴一下正文。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    references = worker._material_references([trigger], [trigger])

    assert dws.search_document_calls == []
    assert dws.download_doc_calls == []
    assert len(references) == 1
    assert references[0].kind == "dingtalk_file"
    assert references[0].reference == "02_下一步推进建议.md"
    assert references[0].read_command == ""


def test_minutes_link_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    trigger = message(
        "[https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true]"
        "(https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true)\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
    )
    references = worker._material_references([trigger], [trigger])

    assert dws.minutes_info_calls == []
    assert dws.minutes_summary_calls == []
    assert dws.minutes_todo_calls == []
    assert dws.minutes_transcription_calls == []
    assert len(references) == 1
    assert references[0].kind == "dingtalk_minutes"
    assert references[0].reference == minutes_id


def test_single_chat_minutes_no_reply_does_not_trigger_material_retry(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    trigger = message(
        "这是一条听记链接\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = SequencedFakeCodex(
        [
            CodexDecision(
                action=CodexAction.NO_REPLY,
                audit_summary="单独听记链接按上下文判断无需回复。",
            ),
            CodexDecision(
                action=CodexAction.SEND_REPLY,
                reply_text="这次不应该被调用。",
                audit_summary="听记不应触发普通材料重试。",
            ),
        ]
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert dws.minutes_info_calls == []
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert minutes_id in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"


def test_minutes_comment_failure_falls_back_to_original_message_reply(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643331323035353732315f3233333438363436305f30"
    target_url = (
        "https://alidocs.dingtalk.com/i/u/dingdocSelectorV4/save?"
        f"resourceId={minutes_id}&resourceType=SHANJI&createLink=true"
    )
    trigger = message(
        f"[{target_url}]({target_url})\n"
        "[dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8]"
        "(dingtalk://dingtalkclient/page/flash_minutes_detail?"
        f"minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_infos[minutes_id] = {
        "result": {
            "taskUuid": minutes_id,
            "title": "测试开发三面",
            "url": f"https://shanji.dingtalk.com/app/transcribes/{minutes_id}",
        }
    }
    dws.minutes_summaries[minutes_id] = {"result": {"fullSummary": "候选人风险偏高。"}}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="不建议直接推进，建议补充作业后再判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="minutes-result-reply")

    worker.run_once()

    assert dws.doc_comments == []
    assert dws.reply_messages == []
    assert final_sent_at_users(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""
    assert worker.store.get_sent_reply("cid-1", "msg-1") is None
    assert worker.store.list_errors() == []


def test_plain_shanji_transcribe_link_replies_without_doc_comment(
    tmp_path: Path, monkeypatch
):
    minutes_id = "76327569643334323330373439365f3337343933323031365f39"
    transcribe_url = f"https://shanji.dingtalk.com/app/transcribes/{minutes_id}"
    trigger = message(
        f"@Alex Chen(明哥) [{transcribe_url}]({transcribe_url}) 看下这个听记",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_infos[minutes_id] = {
        "result": {
            "taskUuid": minutes_id,
            "title": "招聘站会",
            "url": transcribe_url,
        }
    }
    dws.minutes_summaries[minutes_id] = {"result": {"fullSummary": "候选人优先级。"}}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先推进刁必颂、代东，其他人放第二梯队。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="minutes-direct-reply")

    worker.run_once()

    assert dws.doc_comments == []
    assert dws.reply_messages == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert worker.store.list_errors() == []


def test_media_id_image_uses_dws_local_download_path(tmp_path: Path, monkeypatch):
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws_local_path = tmp_path / "dws-downloaded-image.png"
    dws_local_path.write_bytes(b"\x89PNG\r\n\x1a\nlocal-image")
    dws.resource_download_urls[("cid-1", "msg-image-1", "@img-token-1", "mediaId")] = {
        "localPath": str(dws_local_path),
        "response": {
            "content": {
                "result": {"downloadUrl": "https://signed.example/message-image.png"}
            }
        },
    }
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="image reviewed",
            audit_summary="只需上下文判断，不需要回复。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert codex.calls == []
    assert dws.resource_download_url_calls == []
    assert dws_local_path.exists() is True
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    assert any(item.kind == "dingtalk_image" for item in runner.calls[0][2].materials)


def test_image_download_failure_is_passed_to_codex_prompt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.notification.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "unit tests must not call the live browser notification bridge"
        ),
    )
    trigger = message(
        "@Alex Chen(明哥) 看下这个图[图片消息](mediaId=@img-token-1)",
        message_id="msg-image-1",
    )
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.resource_download_urls[("cid-1", "msg-image-1", "@img-token-1", "mediaId")] = (
        DwsError("resource download unavailable")
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我这边图片读取失败，你发一个可查看版本我再看。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "image must be read by the agent",
            code="image_read_required",
        ),
    )

    worker.run_once()

    assert dws.resource_download_url_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert codex.calls == []
    assert '"kind": "dingtalk_image"' in prompt
    assert "msg-image-1" in prompt
    assert "dws chat message download-media --type mediaId" in prompt
    assert "resource download unavailable" not in prompt
    attempts = worker.store.list_reply_attempts()
    assert len(attempts) == 1
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "needs_human"
    assert worker.store.list_errors() == []


def test_dingtalk_doc_read_failure_setup_does_not_block_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "https://alidocs.dingtalk.com/i/nodes/missing @Alex Chen(明哥) 看下"
    )
    canonical_url = "https://alidocs.dingtalk.com/i/nodes/missing"
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[canonical_url] = {
        "contentType": "ALIDOC",
        "extension": "adoc",
        "name": "缺失文档",
        "nodeId": "missing",
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我先读材料")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="document-live-read")

    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert canonical_url in prompt
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""


def test_minutes_permission_setup_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    minutes_id = "7632756964333134343836383736303334325f3435313431363430365f35"
    trigger = message(
        "这些初筛的数据，尤其是没有通过的，我就不给你开放读取了。\n"
        f"[听记](dingtalk://dingtalkclient/page/flash_minutes_detail?minutesId={minutes_id}&from=8)",
        single_chat=True,
    )
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.minutes_infos[minutes_id] = DwsError(
        "B_PERMISSION_NoPermission",
        code="B_PERMISSION_NoPermission",
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert dws.minutes_info_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert minutes_id in prompt
    assert "B_PERMISSION_NoPermission" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert worker.store.list_errors() == []


def test_alidocs_permission_setup_is_passed_to_codex_without_worker_read(
    tmp_path: Path, monkeypatch
):
    url = "https://alidocs.dingtalk.com/i/nodes/XPwkYGxZV3BqnwQ0I3dbwZDlWAgozOKL"
    trigger = message(f"@Alex Chen(明哥) 看下这个材料包：{url}")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.doc_infos[url] = DwsError(
        "forbidden.accessDenied: 你没有权限进行此操作",
        code="forbidden.accessDenied",
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert dws.doc_info_calls == []
    assert dws.read_doc_calls == []
    assert len(agent_runner(worker).calls) == 1
    prompt = agent_prompt(worker)
    assert "Raw material references and exact read commands" in prompt
    assert url in prompt
    assert "forbidden.accessDenied" not in prompt
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert worker.store.list_errors() == []


def test_codex_stop_with_error_notifies_only_after_task_retries_are_exhausted(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason="codex exec failed",
            macos_notify=False,
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        dry_run=True,
        max_task_attempts=1,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "codex exec failed",
            code="codex_exec_failed",
        ),
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert notifications == []


def test_codex_auth_required_stop_with_error_is_failed(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    gate = FixedGate("dingtalk", ChannelGateState.NEEDS_LOGIN)
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates={
            "dingtalk": gate,
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert worker.store.count_reply_attempts() == 0
    assert codex.calls == []
    assert gate.calls == 1


def test_codex_invalid_refresh_token_waits_for_authorization(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    gate = FixedGate("dingtalk", ChannelGateState.NEEDS_LOGIN)
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates={
            "dingtalk": gate,
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert worker.store.count_reply_attempts() == 0
    assert codex.calls == []
    assert gate.calls == 1


def test_codex_invalid_refresh_token_retries_without_duplicate_notification(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    gate = FixedGate("dingtalk", ChannelGateState.NEEDS_LOGIN)
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates={
            "dingtalk": gate,
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert worker.store.count_reply_attempts() == 0
    assert _get_audit_run(worker.store, 1, "initial") is None
    assert codex.calls == []
    assert gate.calls == 2


@pytest.mark.parametrize(
    "reason",
    [
        "unexpected status 401 Unauthorized: Missing bearer or basic "
        "authentication in header, url: https://api.openai.com/v1/responses, "
        "cf-ray: abc, request id: req-1",
        "unexpected status 401 Unauthorized: invalid api key (2049), "
        "url: https://api.minimaxi.com/v1/responses",
    ],
)
def test_codex_provider_stop_with_error_records_clear_sanitized_failure(
    tmp_path: Path,
    monkeypatch,
    reason: str,
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason=reason,
            macos_notify=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            reason,
            code="codex_provider_auth_failed",
        ),
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == "codex_provider_auth_failed"
    assert attempt.codex_reason == reason
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert notifications == []


def test_codex_stop_with_error_keeps_queued_task_retryable(tmp_path: Path, monkeypatch):
    notifications: list[dict[str, str | None]] = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = (
        "failed to refresh available models: timeout waiting for child process to exit"
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="model_refresh_timeout",
                    retryable=True,
                ),
                (),
                "session-retry-1",
            ),
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="model_refresh_timeout",
                    retryable=True,
                ),
                (),
                "session-retry-1",
            ),
        ],
    )
    worker._test_agent_runner = runner
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_attempts() == 1
    failed_attempt = worker.store.get_reply_attempt(1)
    assert failed_attempt is not None
    assert failed_attempt.send_status == "failed"
    assert [
        notification
        for notification in notifications
        if "error" in notification["title"] or "failed" in notification["title"]
    ] == []

    pending = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='2026-05-13 17:00:00' where id=?",
            (pending.id,),
        )

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_attempts() == 2
    assert len(runner.calls) == 2
    assert runner.calls[0][1] == runner.calls[1][1]
    assert codex.calls == []
    assert [
        notification
        for notification in notifications
        if "error" in notification["title"] or "failed" in notification["title"]
    ] == []


def test_dws_transient_dependency_stop_requeues_task_without_sending(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 帮我整理这份听记")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = (
        "dws_transient_dependency_unavailable: "
        "dws minutes list all failed with exit code 6"
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=3)
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="dws_transient_dependency_unavailable",
                    retryable=True,
                ),
                (),
                "session-dws-retry",
            )
        ],
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.produce_once()
    assert worker.consume_once(max_tasks=1) == 0

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == "dws_transient_dependency_unavailable"
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert final_sent(dws) == []
    assert notifications == []


def test_retryable_codex_timeout_does_not_notify_before_final_failure(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = "process produced no output for 180 seconds"
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="codex_timeout",
                    retryable=True,
                ),
                (),
                "session-timeout",
            )
        ],
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert notifications == []


def test_codex_process_failure_is_terminal_at_attempt_limit(
    tmp_path: Path, monkeypatch
):
    browser_notifications = []
    dismissed_notification_ids = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )

    class ProcessFailureThenSuccessRunner(FakeAgentResultRunner):
        def run(self, task, context, **kwargs):
            if not self.calls:
                claim = _claim_audit_run(
                    self.store,
                    task.id,
                    task.execution_generation,
                    owner=self.owner,
                )
                assert claim.claimed
                self.calls.append(
                    (
                        task.id,
                        task.execution_generation,
                        context,
                        claim.run.codex_session_id,
                    )
                )
                self.store.fail_agent_run(
                    claim.run.id,
                    {"code": "codex_process_failed", "retryable": True},
                    owner=self.owner,
                )
                raise RuntimeError("codex_process_failed")
            return super().run(task, context, **kwargs)

    worker._test_agent_runner = ProcessFailureThenSuccessRunner(
        worker.store,
        [
            (
                explicit_agent_result(ScriptOutcome.NO_ACTION, "无需回复"),
                (),
                "session-recovered",
            )
        ],
    )
    monkeypatch.setattr(
        "app.worker.send_browser_notification",
        lambda **kwargs: browser_notifications.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "app.worker.dismiss_browser_notification",
        lambda notification_id: dismissed_notification_ids.append(notification_id) or True,
    )

    worker.produce_once()
    assert worker.consume_once(max_tasks=1) == 0
    failed = worker.store.list_reply_tasks(statuses=("failed",), limit=1)[0]
    assert failed.attempts == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    assert len(browser_notifications) == 1
    assert browser_notifications[0]["notification_id"] == worker._problem_notification_id(failed)
    assert browser_notifications[0]["detail_url"] == "/attempts/1"
    worker._dismiss_problem_notification(failed)
    assert dismissed_notification_ids == [worker._problem_notification_id(failed)]


def test_invalid_agent_result_gets_one_clean_session_retry_at_attempt_limit(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
        max_task_attempts=1,
    )
    worker.store.upsert_conversation("cid-1", "Friday", False, "bad-session")

    class InvalidResultRunner(FakeAgentResultRunner):
        def run(self, task, context, **kwargs):
            claim = _claim_audit_run(
                self.store,
                task.id,
                task.execution_generation,
                owner=self.owner,
            )
            assert claim.claimed
            self.store.set_agent_run_session(
                claim.run.id,
                "bad-session",
                owner=self.owner,
            )
            self.store.fail_agent_run(
                claim.run.id,
                {"code": "codex_result_invalid", "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError("codex_result_invalid")

    worker._test_agent_runner = InvalidResultRunner(worker.store, [])

    worker.run_once()

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert worker.store.get_codex_session_id("cid-1") is None


@pytest.mark.parametrize(
    "failure_code",
    ("codex_process_failed", "codex_result_invalid", "codex_result_missing"),
)
def test_recoverable_agent_runtime_failure_rotates_stuck_conversation_session_before_retry(
    tmp_path: Path, monkeypatch, failure_code: str
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
    )
    worker.store.upsert_conversation("cid-1", "Friday", False, "stuck-session")

    class ProcessFailureRunner(FakeAgentResultRunner):
        def run(self, task, context, **kwargs):
            claim = _claim_audit_run(
                self.store,
                task.id,
                task.execution_generation,
                owner=self.owner,
            )
            assert claim.claimed
            self.store.set_agent_run_session(
                claim.run.id,
                "stuck-session",
                owner=self.owner,
            )
            self.store.fail_agent_run(
                claim.run.id,
                {"code": failure_code, "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError(failure_code)

    worker._test_agent_runner = ProcessFailureRunner(worker.store, [])

    worker.run_once()

    assert worker.store.get_codex_session_id("cid-1") is None
    failed_run = _get_audit_run(worker.store, 1, "initial")
    assert failed_run is not None
    assert failed_run.codex_session_id == ""


def test_repeated_codex_process_failure_does_not_get_an_extra_claim_at_limit(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )

    class PersistentProcessFailureRunner(FakeAgentResultRunner):
        def run(self, task, context, **kwargs):
            claim = _claim_audit_run(
                self.store,
                task.id,
                task.execution_generation,
                owner=self.owner,
            )
            assert claim.claimed
            self.calls.append(
                (
                    task.id,
                    task.execution_generation,
                    context,
                    claim.run.codex_session_id,
                )
            )
            self.store.fail_agent_run(
                claim.run.id,
                {"code": "codex_process_failed", "retryable": True},
                owner=self.owner,
            )
            raise RuntimeError("codex_process_failed")

    worker._test_agent_runner = PersistentProcessFailureRunner(worker.store, [])
    monkeypatch.setattr(
        "app.worker.send_browser_notification",
        lambda **kwargs: notifications.append(kwargs) or True,
    )

    worker.produce_once()
    assert worker.consume_once(max_tasks=1) == 0
    failed = worker.store.list_reply_tasks(statuses=("failed",), limit=1)[0]
    assert failed.attempts == 1
    assert failed.error == "codex_process_failed"
    assert len(notifications) == 1
    assert notifications[0] == {
        "title": "CEO 待处理：@Alex Chen(明哥) 这个怎么处理？",
        "message": (
            "事项：@Alex Chen(明哥) 这个怎么处理？\n"
            "状态：failed\n"
            "原因：自动处理_process_failed\n"
            "操作：打开审计页查看原因并继续处理。"
        ),
        "url": worker._notification_url(conversation(), attempt_id=1),
        "notification_id": worker._problem_notification_id(failed),
        "detail_url": "/attempts/1",
    }


def test_codex_stop_with_error_retry_waits_for_backoff(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = "codex exec timed out after 300 seconds"
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="codex_timeout",
                    retryable=True,
                ),
                (),
                "session-timeout",
            )
        ],
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **_: None,
    )

    worker.run_once()

    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    retried = worker.store.claim_reply_tasks(
        limit=1,
        now="2026-05-13 17:00:59",
    )
    assert retried == []
    pending = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    assert pending.available_at == "2026-05-13 17:01:00"
    assert pending.error == "codex_timeout"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"


def test_stale_processing_task_with_terminal_attempt_is_requeued_not_completed(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    trigger = message("[日程] 晚饭", message_id="msg-calendar", message_type="calendar")
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重跑")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Melody",
        single_chat=True,
        trigger_message_id="msg-calendar",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Melody",
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    claimed = worker.store.claim_reply_tasks(limit=1)[0]
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Melody",
        trigger_message_id="msg-calendar",
        trigger_sender="Melody",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="calendar",
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') where id=?",
            (claimed.id,),
        )
    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1

    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.count_reply_tasks(status="processing") == 0
    assert worker.store.count_errors() == 0
    assert len(agent_runner(worker).calls) == 1
    attempts = worker.store.list_reply_attempts(limit=10)
    assert [attempt.action for attempt in attempts] == ["agent_run", "send_reply"]


def test_critical_info_unavailable_stop_with_error_fails_queued_task(
    tmp_path: Path, monkeypatch
):
    notifications = []
    trigger = message("@Alex Chen(明哥) 帮忙看一下这个审批材料")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = (
        "critical_info_unavailable: dws oa approval detail failed and "
        "required approval material is unavailable"
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=3,
    )
    worker._test_agent_runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="critical_info_unavailable",
                ),
                (),
                "session-critical-info",
            )
        ],
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0

    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    task = worker.store.list_reply_tasks(statuses=("failed",), limit=1)[0]
    assert task.error == "critical_info_unavailable"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == "critical_info_unavailable"
    assert final_sent(dws) == []
    assert notifications == []


def test_xiaoqing_unavailable_without_mcp_call_forces_retry(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 请看一下候选人冯学震的录用申请")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = "小青面试系统结构化读取能力暂时不可用。"
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="xiaoqing_interview_unavailable",
                    retryable=True,
                ),
                (),
                "session-xiaoqing",
            )
        ],
    )
    worker._test_agent_runner = runner
    worker.produce_once()

    worker.consume_once(max_tasks=1)

    assert len(runner.calls) == 1
    assert codex.calls == []
    task = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    assert task.error == "xiaoqing_interview_unavailable"
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.codex_reason == reason
    assert attempt.send_status == "failed"


def test_queued_stop_with_error_becomes_terminal_at_retry_limit(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    reason = "codex exec timed out after 300 seconds"
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="codex_timeout",
                    retryable=True,
                ),
                (),
                "session-timeout",
            ),
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    reason,
                    code="codex_timeout",
                    retryable=True,
                ),
                (),
                "session-timeout",
            ),
        ],
    )
    worker._test_agent_runner = runner
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **_: None,
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_attempts() == 1
    pending = worker.store.list_reply_tasks(limit=1, statuses=["pending"])[0]
    assert pending.available_at == "2026-05-13 17:01:00"

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.count_reply_attempts() == 1
    assert len(runner.calls) == 1

    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set available_at='2026-05-13 17:00:00' where id=?",
            (pending.id,),
        )

    assert worker.consume_once(max_tasks=1) == 0
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_attempts() == 2
    assert len(runner.calls) == 2
    assert runner.calls[0][1] == runner.calls[1][1]


def test_queued_failed_non_send_attempt_does_not_create_duplicate_attempt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="handoff_to_human",
        sensitivity_kind="general",
        send_status="failed",
        codex_reason="handoff delivery failed",
    )

    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1

    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.count_reply_attempts() == 2
    assert len(agent_runner(worker).calls) == 1
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.action == "agent_run"
    assert latest.send_status == "skipped"


def test_worker_has_no_service_side_reply_body_rewriter():
    assert not hasattr(DingTalkAutoReplyWorker, "_native_reply_body")


def test_resume_prompt_only_includes_turn_message_without_repeating_thread_prompt(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    script_no_action(worker)
    worker.run_once()

    prompt = agent_prompt(worker)
    assert agent_runner(worker).calls[0][3] == ""
    assert codex.calls == []
    assert "Consumer Agent A responsibilities" in prompt
    assert "CEO Agent Prompt" not in prompt
    assert "你是 Alex 的钉钉自动回复分身" not in prompt
    assert "回答任何问题前，先检索本地 workspace" not in prompt
    assert "graphify query" not in prompt
    assert "@Alex Chen(明哥) 这个怎么处理？" in prompt


def test_stale_codex_resume_retries_same_thread_before_opening_new_thread(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    assert worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    claimed_task = worker.store.claim_reply_task(
        task.id,
        now="2026-05-13 17:00:00",
    )
    assert claimed_task is not None
    claim = _claim_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-05-13 17:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-1",
        owner="dead-worker",
        now="2026-05-13 17:00:00",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') "
            "where id=?",
            (task.id,),
        )
        db.execute(
            "update agent_runs set lease_expires_at=datetime('now', '-1 minute') "
            "where id=?",
            (claim.run.id,),
        )
    worker.now_provider = lambda: (
        datetime.now(tz=ZoneInfo("Asia/Shanghai")) + timedelta(minutes=1)
    )

    script_no_action(worker)
    assert worker.consume_once(max_tasks=1) == 1

    assert agent_runner(worker).calls[0][3] == "session-1"
    assert codex.calls == []
    run = _get_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
    )
    assert run is not None
    assert run.codex_session_id == "session-1"
    assert run.status == "completed"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert [error.kind for error in worker.store.list_errors()] == ["reply_task_stale"]


@pytest.mark.parametrize(
    "stale_reason",
    [
        "thread/resume failed: no rollout found for thread id session-1 (code -32600)",
        (
            "2026-05-27T02:03:54.663595Z ERROR codex_rollout::list: "
            "state db returned stale rollout path for thread session-1: "
            "/Users/principal/.codex/sessions/2026/05/18/rollout-session-1.jsonl"
        ),
    ],
)
def test_stale_codex_resume_clears_session_and_retries_with_new_user_message(
    tmp_path: Path, monkeypatch, stale_reason: str
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    stale_reason,
                    code=stale_reason,
                ),
                (),
                "unused-session",
            )
        ],
    )
    worker._test_agent_runner = runner
    assert worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert task is not None
    claimed_task = worker.store.claim_reply_task(
        task.id,
        now="2026-05-13 17:00:00",
    )
    assert claimed_task is not None
    claim = _claim_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
        owner="dead-worker",
        lease_seconds=60,
        now="2026-05-13 17:00:00",
    )
    worker.store.set_agent_run_session(
        claim.run.id,
        "session-1",
        owner="dead-worker",
        now="2026-05-13 17:00:00",
    )
    with worker.store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') "
            "where id=?",
            (task.id,),
        )
        db.execute(
            "update agent_runs set lease_expires_at=datetime('now', '-1 minute') "
            "where id=?",
            (claim.run.id,),
        )
    worker.now_provider = lambda: (
        datetime.now(tz=ZoneInfo("Asia/Shanghai")) + timedelta(minutes=1)
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert runner.calls[0][3] == "session-1"
    run = _get_audit_run(
        worker.store,
        task.id,
        task.execution_generation,
    )
    assert run is not None
    assert run.codex_session_id == "session-1"
    assert run.status == "failed"
    assert worker.store.count_reply_attempts() == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == stale_reason
    assert [error.kind for error in worker.store.list_errors()] == ["reply_task_stale"]


def test_sent_reply_records_recall_key_from_send_result(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="reply-with-receipt")

    worker.run_once()

    assert worker.store.get_sent_reply("cid-1", "msg-1") is None
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    run = _get_audit_run(worker.store, 1, "initial")
    assert run is not None
    receipts = worker.store.list_agent_execution_receipts(run.id)
    assert [receipt.operation_id for receipt in receipts] == ["reply-with-receipt"]


def test_existing_dry_run_attempt_does_not_call_codex_again(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    script_completed_result(worker, operation_id="dry-run-rerun")
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="dry_run",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text="> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）",
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 2
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"


def test_failed_send_retries_existing_final_reply_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_result={"result": {"processQueryKey": "key-1"}},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="failed-send-rerun")
    final_reply = (
        "> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按A方案走",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert final_sent_at_users(dws) == []
    assert dws.reply_messages == []
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "failed"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"
    assert worker.store.get_sent_reply("cid-1", "msg-1") is None


def test_sent_reply_prevents_retry_when_latest_attempt_failed(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NO_ACTION, "existing sent receipt is current"
        ),
    )
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )
    failed_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="stop_with_error",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        failed_attempt_id,
        send_error="linked document read failed",
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.count_reply_attempts() == 2
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.action == "agent_run"
    assert latest.send_status == "skipped"
    assert "Before proposing a repeated external action" in agent_prompt(worker)


def test_rerun_message_retries_existing_failed_attempt_without_calling_codex(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="manual-failed-rerun")
    final_reply = (
        "> 周俊杰: @Alex Chen(明哥) 这个怎么处理？\n\n"
        "<@sender-user-1> 先按A方案走（by明哥分身）"
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(attempt_id).send_status == "failed"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"


def test_rerun_message_cleans_legacy_group_reply_wrappers(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该重新生成")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="legacy-wrapper-rerun")
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    worker.store.update_reply_attempt(
        attempt_id,
        final_reply_text=(
            "> 周俊杰: 这个怎么处理？\n\n<@sender-user-1> 先按A方案走（by明哥分身）"
        ),
        send_error="network",
    )

    processed = worker.rerun_message(conversation(), "msg-1")

    assert processed == "msg-1"
    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.final_reply_text.startswith("> 周俊杰:")
    assert attempt.send_status == "failed"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"


def test_rerun_message_can_force_new_codex_decision(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="corrected-action")
    assert worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    original_task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert original_task is not None
    claimed_original = worker.store.claim_reply_task(original_task.id)
    assert claimed_original is not None
    worker.store.complete_reply_task(
        original_task.id,
        expected_execution_generation=claimed_original.execution_generation,
    )
    old_attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert len(agent_runner(worker).calls) == 1
    assert worker.store.count_reply_attempts() == 2
    attempt = worker.store.get_reply_attempt(old_attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != old_attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "completed"
    rerun_task = worker.store.get_reply_task_for_message("cid-1", "msg-1")
    assert rerun_task is not None
    assert rerun_task.execution_generation != original_task.execution_generation
    assert final_sent(dws) == []


def test_rerun_message_looks_up_trigger_by_id_when_recent_context_expired(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": []},
        unread_messages={"cid-1": []},
    )
    dws.mentioned_messages["cid-1"] = [trigger]
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="expired-context-rerun")

    processed = worker.rerun_message(
        conversation(),
        "msg-1",
        force_new_decision=True,
    )

    assert processed == "msg-1"
    assert dws.recent_message_reads[0] == "cid-1"
    assert dws.recent_message_reads.count("cid-1") == 2
    assert dws.unread_message_reads == ["cid-1", "cid-1"]
    assert dws.messages_by_id_reads == [["msg-1"]]
    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []


def test_rerun_message_does_not_resend_when_trigger_already_has_sent_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NO_ACTION, "existing sent receipt is current"
        ),
    )
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )

    worker.rerun_message(conversation(), "msg-1")

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "sent"
    latest = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert latest is not None
    assert latest.id != attempt_id
    assert latest.action == "agent_run"
    assert latest.send_status == "skipped"
    assert worker.store.count_sent_replies() == 1


def test_force_new_rerun_can_resend_when_trigger_already_has_sent_reply(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="改走B方案")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, operation_id="corrected-resend")
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="周俊杰",
        trigger_text=trigger.content,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    worker.store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已经发过的回复",
        send_result_json='{"ok": true}',
    )

    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    old_attempt = worker.store.get_reply_attempt(attempt_id)
    assert old_attempt is not None
    assert old_attempt.send_status == "sent"
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.id != attempt_id
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json) == []
    assert attempt.send_error == ""
    assert worker.store.count_sent_replies() == 1


def test_force_new_rerun_starts_fresh_codex_session(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY),
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", False, "old-session")

    script_no_action(worker)
    worker.rerun_message(conversation(), "msg-1", force_new_decision=True)

    assert codex.calls == []
    run = _get_audit_run(
        worker.store, 1, worker.store.get_reply_task(1).execution_generation
    )
    assert run is not None
    assert run.codex_session_id != "old-session"
    assert "Consumer Agent A responsibilities" in agent_prompt(worker)
    assert "你是 Alex 的钉钉自动回复分身" not in agent_prompt(worker)


def test_rerun_message_uses_explicit_oa_url_when_trigger_has_no_link(
    tmp_path: Path, monkeypatch
):
    trigger = message("[Ding]刘瑞安提醒您审批他的录用申请", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    dws.openapi_oa_details["proc-1"] = {
        "process_instance": {
            "title": "刘瑞安提交的录用申请",
            "form_component_values": [
                {"name": "试用期工作内容和转正要求", "value": "完成 PM 关键项目交付"}
            ],
            "tasks": [
                {
                    "taskid": "task-1",
                    "task_status": "RUNNING",
                    "userid": "principal-user-1",
                }
            ],
        }
    }
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
    )

    script_no_action(worker)
    worker.rerun_message(
        conversation(single_chat=True),
        "msg-1",
        force_new_decision=True,
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    material = next(item for item in context.materials if item.kind == "dingtalk_oa")
    assert '"process_instance_id": "proc-1"' in material.reference
    assert '"task_id": "task-1"' in material.reference
    assert material.read_commands == (
        "dws oa approval detail --instance-id proc-1 --format json",
        "dws oa approval tasks --instance-id proc-1 --format json",
    )


def test_reply_attempt_records_codex_audit_fields(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人是否推进？")]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先补岗位画像和简历再判断",
            audit_documents=[
                {
                    "path": "面试/项目经理/岗位画像.md",
                    "title": "项目经理岗位画像",
                    "relevance": "判断候选人是否匹配",
                }
            ],
            audit_summary="缺少简历内容，因此要求补齐材料后再判断。",
        ),
        audit_tool_events=[
            {
                "tool": "exec_command",
                "command": "rg -n 岗位 /Users/principal/Documents/memory/面试",
            }
        ],
        next_session_id="session-1",
        transcript_start_line=4,
        transcript_end_line=12,
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    safe_events = (
        {
            "type": "item.started",
            "item": {
                "id": "evidence-read",
                "type": "command_execution",
                "metadata": {"effect": "read_only"},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "evidence-read",
                "type": "command_execution",
                "metadata": {"effect": "read_only"},
            },
            "result": {"status": "completed"},
        },
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.COMPLETED,
            "缺少简历内容，因此要求补齐材料后再判断。",
            side_effect_state=SideEffectState.CONFIRMED,
        ),
        events=safe_events,
        receipts=(execution_receipt("candidate-follow-up"),),
        session_id="session-1",
    )

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.audit_documents_json == "[]"
    events = json.loads(attempt.audit_tool_events_json)
    assert [event["type"] for event in events] == [
        "item.started",
        "item.completed",
    ]
    assert events[-1]["item"]["id"] == "evidence-read"
    assert attempt.audit_summary == "缺少简历内容，因此要求补齐材料后再判断。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 0
    assert attempt.codex_transcript_end_line == 2


def test_prompt_includes_dynamic_similar_corpus_examples_without_static_style_profile(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个项目排期怎么处理？")]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    style_records = [
        CorpusRecord(
            source_type="dingtalk",
            source_title="Friday",
            timestamp="2026-05-13",
            context="项目排期要不要改",
            principal_reply="先定优先级，再确认谁负责、什么时候交付、怎么验收。",
            message_id="style-1",
            conversation_id="cid-style-1",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="HR",
            timestamp="2026-05-13",
            context="候选人怎么样",
            principal_reply="先看岗位匹配，再看负责范围和是否真正承担过结果。",
            message_id="style-2",
            conversation_id="cid-style-2",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="技术部",
            timestamp="2026-05-13",
            context="项目排期风险",
            principal_reply="先把风险拆成产品、算法和交付三类，每类只留一个负责人和一个截止时间。",
            message_id="style-3",
            conversation_id="cid-style-3",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="项目群",
            timestamp="2026-05-13",
            context="项目排期延期怎么拆",
            principal_reply="先判断延期是不是影响客户承诺，再决定砍范围、加资源还是换里程碑。",
            message_id="style-4",
            conversation_id="cid-style-4",
            speaker_name="明哥",
            metadata_json="{}",
        ),
        CorpusRecord(
            source_type="dingtalk",
            source_title="研发群",
            timestamp="2026-05-13",
            context="项目排期和负责人不清楚",
            principal_reply="先把负责人写到任务上，再把验收口径写清楚，否则排期没有意义。",
            message_id="style-5",
            conversation_id="cid-style-5",
            speaker_name="明哥",
            metadata_json="{}",
        ),
    ]
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        style_profile="# Alex Style Profile\n- 先结论，再解释原因。",
        style_records=style_records,
    )

    script_no_action(worker)
    worker.run_once()

    prompt = agent_prompt(worker)
    assert "Alex 语气规则:" not in prompt
    assert "- 先结论，再解释原因。" not in prompt
    assert "相似历史回复风格例子" not in prompt
    assert "先定优先级，再确认谁负责、什么时候交付、怎么验收。" not in prompt
    assert "先看岗位匹配" not in prompt
    assert "cid-style-1" not in prompt
    assert '"conversation_title": "Friday"' in prompt
    assert "Consumer Agent A responsibilities" in prompt


def test_prompt_includes_similar_human_feedback_examples(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {
            "cid-1": [
                message(
                    "明哥，这个本地工具我跑通过，你先安装试试。",
                    single_chat=True,
                )
            ]
        },
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="dry run"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    attempt_id = worker.store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Mina 邹",
        trigger_message_id="msg-old",
        trigger_sender="Mina 邹",
        trigger_text="你先安装试试这个本地工具",
        action="handoff_to_human",
        sensitivity_kind="general",
        codex_reason="要求 Alex 安装本地工具，应交给本人。",
    )
    worker.store.record_reply_feedback(
        attempt_id,
        feedback=(
            "这类请求不要直接交给本人；先推动可交接动作，要求对方先提交代码或整理材料。"
        ),
        corrected_reply_text="你把代码提交一下，然后代码提交了，就可以让别人帮你看了",
    )

    script_no_action(worker)
    worker.run_once()

    prompt = agent_prompt(worker)
    assert "相似人工纠偏样本" not in prompt
    assert "不要直接交给本人" not in prompt
    assert "你把代码提交一下" not in prompt
    assert "msg-old" not in prompt
    assert "cid-old" not in prompt
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    assert runner.calls[0][2].prior_receipts == ()


def test_group_name_reference_without_direct_at_does_not_queue(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@张晓民(Xiaomin张晓民) 这个和明哥预期一致")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_algorithm_owner_multi_mention_is_framed_as_principal_responsibility(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                message(
                    "@ET(张毅倜(ET)) @Alex Chen(明哥) "
                    "aijam是否可以把算法大神们纳入进来？",
                    message_id="msg-algo-owner",
                )
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="可以，算法这边应该参与"
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "算法 owner 已处理请求。")

    worker.run_once()

    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).send_status == "completed"
    prompt = agent_prompt(worker)
    assert "aijam是否可以把算法大神们纳入进来？" in prompt
    assert "Original trigger" in prompt


def test_group_direct_mention_found_in_recent_context_is_queued(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
        message_id="msg-old",
    )
    old_direct_mention.create_time = "2026-05-15 18:34:47"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    dws.mentioned_messages = {"cid-1": [old_direct_mention]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "已处理上下文中的直接 mention。")

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.get_reply_attempt(1).send_status == "completed"


def test_group_seen_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    old_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
        message_id="msg-old",
    )
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                old_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.mark_seen("msg-old", "cid-1")

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_prompt_context_limits_after_sorting_reverse_chronological_history():
    messages = []
    for index in range(25):
        item = message(f"history {index}", message_id=f"msg-{index}")
        item.create_time = f"2026-05-13 18:{index:02d}:00"
        messages.append(item)
    reverse_chronological = list(reversed(messages))

    context = DingTalkAutoReplyWorker._prompt_context_messages(
        reverse_chronological,
        [],
        previous_limit=20,
    )

    assert [item.open_message_id for item in context] == [
        f"msg-{index}" for index in range(5, 25)
    ]


def test_group_stale_direct_mention_found_in_recent_context_does_not_queue(
    tmp_path: Path, monkeypatch
):
    stale_direct_mention = message(
        "@Alex Chen(明哥) 旧消息看一下",
        message_id="msg-old",
    )
    stale_direct_mention.create_time = "2026-04-30 17:34:59"
    latest_unread = message("最新未读只是同步进展", message_id="msg-new")
    latest_unread.create_time = "2026-05-15 18:35:47"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                stale_direct_mention,
                latest_unread,
            ]
        },
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_okr_review_request_is_enqueued_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.COMPLETED,
                    "OKR review completed by the audit executor",
                    side_effect_state=SideEffectState.CONFIRMED,
                ),
                effect_events("okr-review", {"success": True}),
                "okr-review-session",
            )
        ],
    )
    worker._test_agent_runner = runner

    worker.run_once()

    assert codex.calls == []
    assert runner.calls[0][2].trigger_text == "帮我审核 OKR"
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert json.loads(attempt.audit_tool_events_json)[-1]["result"]["success"] is True


def test_okr_review_request_uses_explicit_quarter_from_trigger(
    tmp_path: Path, monkeypatch
):
    trigger = message("请帮我 review Q2 OKR", single_chat=True)
    trigger.create_time = "2026-07-03 10:00:00"
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 Q2 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.COMPLETED,
                    "2026 Q2 OKR review completed by the audit executor",
                    side_effect_state=SideEffectState.CONFIRMED,
                ),
                effect_events(
                    "okr-review-q2",
                    {"success": True, "period_label": "2026 Q2"},
                ),
                "okr-q2-session",
            )
        ],
    )
    worker._test_agent_runner = runner

    worker.run_once()

    assert codex.calls == []
    assert runner.calls[0][2].trigger_text == "请帮我 review Q2 OKR"
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_reply_attempt(1)
    events = json.loads(attempt.audit_tool_events_json)
    assert events[-1]["result"]["period_label"] == "2026 Q2"


def test_okr_mentions_without_agent_queue_action_do_not_fetch_okr_source(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) Q3 OKR 季度会请大家准备，AI 打分只是材料同步。",
        single_chat=False,
    )
    dws = FakeDws([conversation(single_chat=False)], {"cid-1": [trigger]})
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="通知同步"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.okr_live_source = type(
        "LiveSource",
        (),
        {
            "fetch_user_okr": lambda self, user_id, period_label: (_ for _ in ()).throw(
                AssertionError("OKR source should not be called")
            )
        },
    )()

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert final_sent(dws) == []


def test_okr_review_missing_live_source_fails_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=1)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    "OKR live source is not configured",
                    code="okr_live_source_not_configured",
                ),
                (),
                "okr-missing-source-session",
            )
        ],
    )
    worker._test_agent_runner = runner

    worker.run_once()

    assert codex.calls == []
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == "okr_live_source_not_configured"
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.list_errors(limit=10) == []
    assert final_sent(dws) == []


def test_okr_review_live_source_error_fails_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=1)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    "Dingteam OKR live source failed: page API module missing",
                    code="okr_live_source_module_missing",
                ),
                (),
                "okr-source-error-session",
            )
        ],
    )
    worker._test_agent_runner = runner

    worker.run_once()

    assert codex.calls == []
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert attempt.send_error == "okr_live_source_module_missing"
    assert worker.store.count_reply_tasks(status="failed") == 1
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.list_errors(limit=10) == []
    assert final_sent(dws) == []


def test_okr_review_dingteam_auth_error_blocks_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws([conversation(single_chat=True)], {"cid-1": [trigger]})
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, max_task_attempts=1)
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.NEEDS_HUMAN,
                    "Dingteam OKR requires login",
                    code="okr_authorization_required",
                    authorization_required=True,
                ),
                (),
                "okr-auth-session",
            )
        ],
    )
    worker._test_agent_runner = runner

    worker.run_once()

    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "okr_authorization_required"
    assert worker.store.count_reply_tasks(status="failed") == 0
    assert worker.store.count_reply_tasks(status="done") == 1
    assert worker.store.claim_okr_review_requests(1) == []
    assert final_sent(dws) == []


def test_queued_okr_review_ack_delivery_failure_requeues_after_agent_queue_action(
    tmp_path: Path, monkeypatch
):
    trigger = message("帮我审核 OKR", single_chat=True)
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [trigger]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.NO_REPLY,
            reason="用户明确请求审核 OKR，交给 OKR handler 处理。",
            system_actions=[{"type": "queue_okr_review"}],
        )
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=2,
    )
    started, _completed = effect_events("okr-review-ack", {"success": True})
    runner = FakeAgentResultRunner(
        worker.store,
        [
            (
                explicit_agent_result(
                    ScriptOutcome.FAILED,
                    "OKR review acknowledgement result is unknown",
                    code="okr_ack_side_effect_unknown",
                    side_effect_state=SideEffectState.UNKNOWN,
                ),
                (started,),
                "okr-unknown-session",
            )
        ],
    )
    worker._test_agent_runner = runner
    worker.store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert codex.calls == []
    assert worker.store.count_reply_tasks(status="done") == 0
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert worker.store.claim_okr_review_requests(1) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt.action == "agent_run"
    assert attempt.send_status == "pending_reconciliation"
    assert attempt.send_error == "okr_ack_side_effect_unknown"
    assert worker.store.get_agent_run(1).status == "unknown"


def test_single_chat_old_candidate_context_does_not_become_new_question(
    tmp_path: Path, monkeypatch
):
    old_candidate_context = message(
        "这个候选人怎么样？",
        message_id="msg-old-candidate",
        single_chat=True,
    )
    old_candidate_context.create_time = "2026-05-13 17:00:00"
    latest_unread = message("好的", message_id="msg-new-ok", single_chat=True)
    latest_unread.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [old_candidate_context, latest_unread]},
        unread_messages={"cid-1": [latest_unread]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="ack only"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert final_sent(dws) == []
    assert len(agent_runner(worker).calls) == 1
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    assert context.trigger_text == "好的"
    assert "这个候选人怎么样？" in [item.text for item in context.messages]


def test_single_chat_recent_context_after_seen_is_processed_when_unread_empty(
    tmp_path: Path, monkeypatch
):
    handled = message(
        "paper是不是也要开始准备了？", message_id="msg-handled", single_chat=True
    )
    handled.create_time = "2026-05-13 17:44:34"
    sent_reply = principal_message(
        "对，paper不要等所有数据都齐了再启动。",
        message_id="msg-principal-reply",
        create_time="2026-05-13 17:45:31",
    )
    new_peer_message = message(
        "我比较想先把hsw弄出来，目前的novelty更强一点",
        message_id="msg-new-peer-1",
        single_chat=True,
    )
    new_peer_message.create_time = "2026-05-13 17:47:44"
    latest_peer_message = message(
        "如果他们确实比较感兴趣的话能拉他们弄点合作或者挂个名之类的就更好一些",
        message_id="msg-new-peer-2",
        single_chat=True,
    )
    latest_peer_message.create_time = "2026-05-13 17:50:01"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                latest_peer_message,
                new_peer_message,
                sent_reply,
                handled,
            ]
        },
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我倾向先推 HSW。")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "Friday", True, None)
    worker.store.mark_seen("msg-handled", "cid-1")
    script_completed_result(worker, "已处理恢复窗口中的最新私聊。")

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    assert "拉他们弄点合作或者挂个名" in context.trigger_text
    assert any("我比较想先把hsw弄出来" in item.text for item in context.messages)
    assert final_sent(dws) == []
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-new-peer-2"


def test_single_chat_recovery_processes_unseen_gap_before_later_seen_anchor(
    tmp_path: Path, monkeypatch
):
    handled = message("前面已经处理过", message_id="msg-seen-old", single_chat=True)
    handled.create_time = "2026-05-13 16:50:00"
    missed = message(
        "这条如果窗口开着也要处理",
        message_id="msg-missed-gap",
        single_chat=True,
    )
    missed.create_time = "2026-05-13 17:10:00"
    manual_context = principal_message(
        "后面我手动说了另一件事",
        message_id="msg-principal-after-gap",
        create_time="2026-05-13 17:20:00",
    )
    later_seen = message(
        "后面这条已经处理", message_id="msg-seen-new", single_chat=True
    )
    later_seen.create_time = "2026-05-13 17:30:00"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                later_seen,
                manual_context,
                missed,
                handled,
            ]
        },
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我会处理这条。")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation("cid-1", "韩露", True, None)
    worker.store.mark_seen("msg-seen-old", "cid-1")
    worker.store.mark_seen("msg-seen-new", "cid-1")

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    assert context.trigger_text == "这条如果窗口开着也要处理"
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-missed-gap"


def test_single_chat_recovery_does_not_coalesce_across_current_user_context(
    tmp_path: Path, monkeypatch
):
    seen_anchor = message("已经处理过", message_id="msg-seen-anchor", single_chat=True)
    seen_anchor.create_time = "2026-05-13 16:50:00"
    first_missed = message(
        "第一段要处理", message_id="msg-first-missed", single_chat=True
    )
    first_missed.create_time = "2026-05-13 17:10:00"
    current_user = principal_message(
        "中间我说了另一件事",
        message_id="msg-current-user-between",
        create_time="2026-05-13 17:20:00",
    )
    second_missed = message(
        "第二段也要处理", message_id="msg-second-missed", single_chat=True
    )
    second_missed.create_time = "2026-05-13 17:30:00"
    dws = FakeDws(
        [],
        {
            "cid-1": [
                second_missed,
                current_user,
                first_missed,
                seen_anchor,
            ]
        },
        unread_messages={"cid-1": []},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="test")),
        monkeypatch,
    )
    worker.store.upsert_conversation("cid-1", "韩露", True, None)
    worker.store.mark_seen("msg-seen-anchor", "cid-1")

    assert worker.produce_once() == 2

    tasks = sorted(worker.store.list_reply_tasks(limit=10), key=lambda task: task.id)
    assert [task.trigger_message_id for task in tasks] == [
        "msg-first-missed",
        "msg-second-missed",
    ]


def test_single_chat_empty_unread_without_seen_anchor_does_not_process_old_context(
    tmp_path: Path, monkeypatch
):
    old_message = message("这个候选人怎么样？", message_id="msg-old", single_chat=True)
    old_message.create_time = "2026-05-13 17:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [old_message]},
        unread_messages={"cid-1": []},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_initial_prompt_context_includes_previous_20_plus_unread_tail(
    tmp_path: Path, monkeypatch
):
    old_messages = [
        message(f"历史上下文 {index:02d}", message_id=f"old-{index:02d}")
        for index in range(25)
    ]
    for index, old_message in enumerate(old_messages):
        old_message.create_time = f"2026-05-13 17:{index:02d}:00"
    trigger = message("@Alex Chen(明哥) 这个需要你看一下", message_id="trigger-msg")
    trigger.create_time = "2026-05-13 18:00:00"
    downstream = message("我已经处理好了", message_id="downstream-msg")
    downstream.create_time = "2026-05-13 18:01:00"
    dws = FakeDws(
        [conversation()],
        {"cid-1": old_messages},
        unread_messages={"cid-1": [trigger, downstream]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert final_sent(dws) == []
    assert len(agent_runner(worker).calls) == 1
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    context_texts = [item.text for item in context.messages]
    assert "历史上下文 04" not in context_texts
    assert "历史上下文 05" in context_texts
    assert "历史上下文 24" in context_texts
    assert context.trigger_text == "@Alex Chen(明哥) 这个需要你看一下"
    assert "我已经处理好了" in context_texts


def test_resumed_prompt_context_only_includes_messages_after_last_seen(
    tmp_path: Path, monkeypatch
):
    before_seen = message("旧上下文，不应重复", message_id="old-before")
    before_seen.create_time = "2026-05-13 17:00:00"
    last_seen = message("上次已经处理到这里", message_id="old-seen")
    last_seen.create_time = "2026-05-13 17:10:00"
    after_seen = message("上次回复后的补充信息", message_id="after-seen")
    after_seen.create_time = "2026-05-13 17:20:00"
    trigger = message(
        "@Alex Chen(明哥) 结合上面的补充再看一下", message_id="trigger-msg"
    )
    trigger.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [before_seen, last_seen, after_seen]},
        unread_messages={"cid-1": [trigger]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    worker.store.upsert_conversation(
        "cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )
    worker.store.mark_seen("old-seen", "cid-1")

    script_no_action(worker)
    worker.run_once()

    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    context_texts = [item.text for item in context.messages]
    run = worker.store.get_agent_run(1)
    assert run is not None
    assert run.codex_session_id == "worker-test-session-1"
    assert "旧上下文，不应重复" in context_texts
    assert "上次已经处理到这里" in context_texts
    assert "上次回复后的补充信息" in context_texts
    assert context.trigger_text == "@Alex Chen(明哥) 结合上面的补充再看一下"


def test_no_reply_action_does_not_send(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) cc一下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(ScriptOutcome.NO_ACTION, "cc only"),
    )
    store = worker.store
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.sent == []
    assert store.has_seen("msg-1") is False
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "cc only"


def test_handoff_adds_text_emotion_dings_self_and_records_reaction(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 不要分身，真人看一下")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "需要本人处理。",
            code="needs_human",
        ),
    )
    store = worker.store
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.reply_messages == []
    assert dws.created_text_emotions == []
    assert dws.message_text_emotions == []
    assert dws.dings == []
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.final_reply_text == ""
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "needs_human"
    sent_reply = store.get_sent_reply("cid-1", "msg-1")
    assert sent_reply is None
    assert store.count_errors() == 0


def test_service_handoff_notification_is_not_enqueued_from_self_chat(
    tmp_path: Path, monkeypatch
):
    handoff = message(
        (
            f"{HANDOFF_NOTIFICATION_PREFIX}\n"
            "[会议群]晋升答辩 张静: @所有人 请评委填写评分\n"
            "previous split-person reply: none"
        ),
        message_id="handoff-notification",
        single_chat=True,
        sender_user_id=None,
    )
    handoff.sender_name = "磊哥"
    handoff.sender_open_dingtalk_id = "ding-robot-open-id"
    direct = conversation(single_chat=True)
    direct.title = "磊哥"
    dws = FakeDws(
        [direct],
        {"cid-1": [handoff]},
        unread_messages={"cid-1": [handoff]},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
    )

    queued = worker.produce_once()

    assert queued == 0
    assert worker.store.has_seen("handoff-notification") is True
    assert worker.store.count_reply_tasks() == 0


def test_new_principal_mention_is_processed(tmp_path: Path, monkeypatch):
    latest = message(
        "@Melody Xu（Melody） @Alex Chen（明哥）请明哥看一下2026年的战略主线这样写是否合适？[图片消息]",
        message_id="msg-after-handoff",
    )
    latest.create_time = "2026-05-13 18:10:00"
    latest.sender_name = "Melody"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "26年董事会筹备组"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="战略主线建议这样调整")
    )
    notifications: list[dict[str, str | None]] = []
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_completed_result(worker, "已处理新的 principal mention。")
    store.upsert_conversation("cid-1", "26年董事会筹备组", False, None)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert store.has_seen("msg-after-handoff") is False
    assert notifications == []
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"


def test_group_unread_without_principal_mention_is_ignored(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "MKT core", False, None)
    latest = message(
        "［文件】星尘数据B轮融资 BP_20260526.pptx-2.pptx",
        message_id="file-after-handoff",
        message_type="file",
    )
    latest.create_time = "2026-05-13 18:10:00"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "MKT core"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert store.has_seen("file-after-handoff") is False
    assert notifications == []


def test_group_unread_without_principal_mention_reads_unread_tail_but_does_not_queue(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    latest = message(
        "无关同步",
        message_id="msg-unmentioned",
    )
    latest.create_time = "2026-05-13 18:10:00"
    group = conversation()
    group.title = "无关群"
    group.single_chat = False
    group.unread_point = 1
    dws = FakeDws(
        [group],
        {"cid-1": [latest]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    worker.run_once()

    assert dws.unread_message_reads[0] == "cid-1"
    assert store.list_errors() == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_recovery_due_group_unread_without_principal_mention_reads_unread_tail_but_does_not_queue(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    latest = message(
        "无关同步",
        message_id="msg-unmentioned",
    )
    latest.create_time = "2026-05-13 18:10:00"
    group = conversation()
    group.title = "无关群"
    group.single_chat = False
    group.unread_point = 1
    dws = FakeDws(
        [group],
        {"cid-1": [latest]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T15:30:00+00:00",
    )

    worker.run_once()

    assert dws.unread_message_reads == ["cid-1"]
    assert store.list_errors() == []
    assert codex.calls == []
    assert final_sent(dws) == []


def test_dry_run_group_unread_without_principal_mention_is_ignored(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "26年董事会筹备组", False, None)
    latest = message(
        "可以东风集团（京东云渠道）",
        message_id="msg-after-handoff",
    )
    latest.create_time = "2026-05-13 18:10:00"
    dws = FakeDws([conversation()], {"cid-1": [latest]})
    dws.conversations[0].title = "26年董事会筹备组"
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    notifications: list[dict[str, str | None]] = []
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=True,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []
    assert store.has_seen("msg-after-handoff") is False
    assert notifications == []


def test_single_chat_unread_is_processed_without_mention(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个今天能拍吗？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="可以，先推进")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "已直接完成私聊请求。")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.reply_messages == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "completed"
    assert attempt.direct_user_id == ""
    assert attempt.direct_open_dingtalk_id == ""
    assert attempt.final_reply_text == ""
    assert worker.store.list_agent_execution_receipts(1)


def test_user_runtime_term_in_trigger_does_not_block_safe_reply(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {
            "cid-1": [
                message(
                    "明哥，你是怎么解决codex上下文压缩失败的问题的？", single_chat=True
                )
            ]
        },
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="我会把长任务拆小，每一步都留清楚验收口径。",
            audit_summary="只需上下文判断。",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "已处理关于运行时的安全回复。")

    worker.run_once()

    assert final_sent(dws) == []
    assert "codex上下文压缩失败" in agent_prompt(worker)
    assert worker.store.get_reply_attempt(1).send_status == "completed"


def test_single_chat_current_user_message_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [principal_message("AI自动抓取，用于会议纪要整理")]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "只处理首个 batch。")

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_single_chat_peer_message_before_current_user_unread_is_processed(
    tmp_path: Path, monkeypatch
):
    peer = message(
        "这个会议链接需要你看一下",
        message_id="msg-peer-before-self",
        single_chat=True,
    )
    peer.create_time = "2026-05-13 18:00:00"
    own_followup = principal_message(
        "基于项目的未完成事项：请确认流程状态",
        message_id="msg-self-after-peer",
        create_time="2026-05-13 18:03:00",
    )
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [peer, own_followup]},
        unread_messages={"cid-1": [peer, own_followup]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    assert worker.produce_once() == 1

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-peer-before-self"
    assert tasks[0].trigger_text == "这个会议链接需要你看一下"


def test_run_once_max_batches_stops_after_limit(tmp_path: Path, monkeypatch):
    conv_1 = DingTalkConversation(
        open_conversation_id="cid-1",
        title="技术部",
        single_chat=False,
        unread_point=1,
    )
    conv_2 = DingTalkConversation(
        open_conversation_id="cid-2",
        title="产品部",
        single_chat=False,
        unread_point=1,
    )
    dws = FakeDws(
        [conv_1, conv_2],
        {
            "cid-1": [message("@Alex Chen(明哥) 第一个问题", message_id="msg-1")],
            "cid-2": [message("@Alex Chen(明哥) 第二个问题", message_id="msg-2")],
        },
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先推进"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once(max_batches=1)

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []
    assert worker.store.has_seen("msg-1") is False
    assert worker.store.has_seen("msg-2") is False
    assert worker.store.count_reply_tasks(status="done") == 1


def test_single_chat_same_display_name_without_current_user_id_still_calls_codex(
    tmp_path: Path, monkeypatch
):
    same_name_message = message(
        "这个事情你怎么看？",
        single_chat=True,
        sender_user_id=None,
    )
    same_name_message.sender_name = "明哥"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [same_name_message]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert final_sent(dws) == []


def test_message_before_current_user_reply_does_not_call_codex(
    tmp_path: Path, monkeypatch
):
    requester = message(
        "@Alex Chen(明哥) push了",
        message_id="msg-before-self",
    )
    requester.create_time = "2026-05-13 08:45:50"
    manual_reply = principal_message(
        "@周俊杰(周俊杰) 我merge了",
        message_id="msg-self-after",
        create_time="2026-05-13 11:00:03",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [requester, manual_reply]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该回复")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "已处理可读取的会话。")

    worker.run_once()

    assert codex.calls == []
    assert final_sent(dws) == []


def test_message_after_current_user_reply_still_calls_codex(
    tmp_path: Path, monkeypatch
):
    manual_reply = principal_message(
        "这个ACL表@张晓民(Xiaomin张晓民) 看一下",
        message_id="msg-self-before",
        create_time="2026-05-13 15:15:14",
    )
    requester = message(
        "@Alex Chen(明哥) 我和俊杰聊下",
        message_id="msg-after-self",
    )
    requester.create_time = "2026-05-13 15:16:49"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [manual_reply, requester]},
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="handled"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert len(agent_runner(worker).calls) == 1
    assert "@Alex Chen(明哥) 我和俊杰聊下" in agent_prompt(worker)
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    context = runner.calls[0][2]
    assert context.trigger_text == "@Alex Chen(明哥) 我和俊杰聊下"
    assert "这个ACL表@张晓民(Xiaomin张晓民) 看一下" in [
        item.text for item in context.messages
    ]


def test_read_failure_records_error_and_continues_next_conversation(
    tmp_path: Path, monkeypatch
):
    bad_conversation = DingTalkConversation(
        open_conversation_id="cid-bad",
        title="bad",
        single_chat=False,
        unread_point=1,
    )
    good_conversation = DingTalkConversation(
        open_conversation_id="cid-good",
        title="good",
        single_chat=False,
        unread_point=1,
    )
    good_message = message(
        "@Alex Chen(明哥) 这个怎么处理？",
        message_id="msg-good",
    )
    good_message.open_conversation_id = "cid-good"
    dws = FakeDws(
        [bad_conversation, good_conversation],
        {"cid-good": [good_message]},
        read_errors={"cid-bad": RuntimeError("forbidden request")},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)

    script_no_action(worker)
    worker.run_once()

    assert final_sent(dws) == []
    assert len(agent_runner(worker).calls) == 1
    assert agent_runner(worker).calls[0][2].conversation_id == "cid-good"


def test_group_mention_from_unread_conversation_is_processed_when_unread_tail_misses_it(
    tmp_path: Path, monkeypatch
):
    unread_tail = message("后续同步进展", message_id="msg-tail")
    unread_tail.create_time = "2026-05-25 17:53:12"
    missed_mention = message(
        "@Alex Chen(明哥) 要不现在对一下",
        message_id="msg-mentioned",
    )
    missed_mention.create_time = "2026-05-25 16:20:14"
    conv = conversation()
    conv.unread_point = 6
    dws = FakeDws(
        [conv],
        {"cid-1": [unread_tail]},
        unread_messages={"cid-1": [unread_tail]},
    )
    dws.mentioned_messages = {"cid-1": [missed_mention]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="现在可以对")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-mentioned"
    assert attempts[0].send_status == "skipped"


def test_group_agent_name_mention_from_search_is_processed_when_mentions_miss_it(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_NAMES", "磊哥")
    agent_mention = message(
        "@磊哥 要不现在对一下",
        message_id="msg-agent-mentioned",
    )
    agent_mention.create_time = "2026-05-25 16:20:14"
    dws = FakeDws([], {})
    dws.mentioned_messages = {}
    dws.broadcast_messages = {"cid-1": [agent_mention]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="现在可以对")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-agent-mentioned"
    assert attempts[0].send_status == "skipped"


def test_group_mention_from_unread_payload_is_processed_when_mention_lookup_misses_it(
    tmp_path: Path, monkeypatch
):
    unread_mention = message(
        "@Alex Chen(明哥) 官网反馈这条帮忙看一下",
        message_id="msg-unread-mention",
    )
    unread_mention.create_time = "2026-05-25 17:53:12"
    conv = conversation()
    conv.unread_point = 4
    dws = FakeDws(
        [conv],
        {"cid-1": [unread_mention]},
        unread_messages={"cid-1": [unread_mention]},
    )
    dws.mentioned_messages = {}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="这条我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert dws.unread_message_reads[0] == "cid-1"
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-unread-mention"
    assert attempts[0].send_status == "skipped"


def test_produce_once_triggers_only_latest_consecutive_group_mention_from_same_sender(
    tmp_path: Path, monkeypatch
):
    first = message(
        "@Alex Chen(明哥) 先看第一点",
        message_id="msg-mentioned-1",
    )
    first.create_time = "2026-05-28 13:21:54"
    second = message(
        "@曹宇航(Yuhang Cao) @Alex Chen(明哥) 再看第二点",
        message_id="msg-mentioned-2",
    )
    second.create_time = "2026-05-28 13:24:02"
    third = message(
        "@Alex Chen(明哥) @曹宇航(Yuhang Cao) 最后总结一下",
        message_id="msg-mentioned-3",
    )
    third.create_time = "2026-05-28 13:27:41"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [first, second, third]},
        unread_messages={"cid-1": [first, second, third]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-mentioned-3"
    assert tasks[0].trigger_text == "@Alex Chen(明哥) @曹宇航(Yuhang Cao) 最后总结一下"
    assert codex.calls == []


def test_produce_once_triggers_only_latest_single_chat_message(
    tmp_path: Path, monkeypatch
):
    first = message("先看第一点", message_id="msg-single-1", single_chat=True)
    first.create_time = "2026-05-28 13:21:54"
    second = message("再看第二点", message_id="msg-single-2", single_chat=True)
    second.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [first, second]},
        unread_messages={"cid-1": [first, second]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-single-2"
    assert tasks[0].trigger_text == "再看第二点"


def test_produce_once_replaces_pending_single_chat_task_with_latest_message(
    tmp_path: Path, monkeypatch
):
    first = message("先看第一点", message_id="msg-single-1", single_chat=True)
    first.create_time = "2026-05-28 13:21:54"
    second = message("再看第二点", message_id="msg-single-2", single_chat=True)
    second.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [first]},
        unread_messages={"cid-1": [first]},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex([]),
        monkeypatch,
        fast_path_unread_backoff=timedelta(minutes=5),
    )

    assert worker.produce_once() == 1

    dws.messages = {"cid-1": [first, second]}
    dws.unread_messages = {"cid-1": [first, second]}
    assert worker.produce_once() == 1

    tasks = worker.store.list_reply_tasks(statuses=("pending",), limit=10)
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-single-2"
    assert tasks[0].trigger_text == "再看第二点"


def test_produce_once_triggers_only_latest_group_thread_reply(
    tmp_path: Path, monkeypatch
):
    first_thread_reply = message(
        "@Alex Chen(明哥) 这个 thread 先看第一点",
        message_id="msg-thread-1",
        quoted_content="同一个 thread",
        sender_user_id="sender-user-1",
    )
    first_thread_reply.create_time = "2026-05-28 13:21:54"
    other_topic = message(
        "@Alex Chen(明哥) 另一个话题",
        message_id="msg-other-topic",
        sender_user_id="sender-user-2",
    )
    other_topic.create_time = "2026-05-28 13:22:54"
    latest_thread_reply = message(
        "@Alex Chen(明哥) 这个 thread 最后看这里",
        message_id="msg-thread-2",
        quoted_content="同一个 thread",
        sender_user_id="sender-user-3",
    )
    latest_thread_reply.create_time = "2026-05-28 13:24:02"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [first_thread_reply, other_topic, latest_thread_reply]},
        unread_messages={
            "cid-1": [first_thread_reply, other_topic, latest_thread_reply]
        },
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = sorted(
        worker.store.claim_reply_tasks(limit=10),
        key=lambda task: task.trigger_create_time,
    )
    assert queued == 2
    assert [task.trigger_message_id for task in tasks] == [
        "msg-other-topic",
        "msg-thread-2",
    ]
    assert tasks[1].trigger_text == "@Alex Chen(明哥) 这个 thread 最后看这里"


def test_single_chat_oa_card_followup_triggers_followup_only(
    tmp_path: Path, monkeypatch
):
    oa_card = message(
        "Roy Han's 招聘需求申请\n"
        "申请人: Roy Han\n"
        "招聘岗位: 大模型数据项目实习生\n"
        "[dingtalk://dingtalkclient/action/open_platform_link?"
        "pcLink=https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
        "%3FprocInstId%3Dproc-1%26taskId%3Dtask-1%26swfrom%3Doa"
        "%26dinghash%3Dapproval](dingtalk://dingtalkclient/action/open_platform_link)",
        message_id="msg-oa-card",
        single_chat=True,
    )
    oa_card.create_time = "2026-06-08 18:36:39"
    followup = message(
        "磊哥请你的分身审核一遍，并判断这个需求是否必要，以及是否有其他建议",
        message_id="msg-followup",
        single_chat=True,
    )
    followup.create_time = "2026-06-08 18:36:57"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [oa_card, followup]},
        unread_messages={"cid-1": [oa_card, followup]},
    )
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-followup"
    assert (
        tasks[0].trigger_text
        == "磊哥请你的分身审核一遍，并判断这个需求是否必要，以及是否有其他建议"
    )
    merged = DingTalkMessage.model_validate_json(tasks[0].trigger_message_json)
    assert merged.open_message_id == "msg-followup"


def test_fast_path_followup_does_not_inherit_oa_target_from_recent_context(
    tmp_path: Path, monkeypatch
):
    oa_card = message(
        "贾金鹏提交的项目立项全流程（第一曲线）\n"
        "项目经理: 贾金鹏\n"
        "销售经理: 曹宇航\n"
        "[dingtalk://dingtalkclient/action/open_platform_link?"
        "pcLink=https%3A%2F%2Faflow.dingtalk.com%2Fdingtalk%2Fpc%2Fquery"
        "%2Fpchomepage.htm%3FprocInstId%3Dproc-1%26taskId%3Dtask-1"
        "%26swfrom%3Doa%26dinghash%3Dapproval]"
        "(dingtalk://dingtalkclient/action/open_platform_link)",
        message_id="msg-oa-card",
        single_chat=True,
    )
    oa_card.create_time = "2026-05-13 17:59:00"
    followup = message(
        "这个是审批链接，您看下有问题吗",
        message_id="msg-followup",
        single_chat=True,
    )
    followup.create_time = "2026-05-13 18:00:00"
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [oa_card, followup]},
        unread_messages={"cid-1": [followup]},
    )
    worker = make_worker(
        tmp_path,
        dws,
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="missing route")),
        monkeypatch,
    )
    worker.store.set_service_state(
        "message_recovery_checked_at",
        "2026-05-13T16:30:00+00:00",
    )

    assert worker.produce_once() == 1
    task = worker.store.list_reply_tasks(statuses=("pending",), limit=1)[0]
    assert "coalesced_message_ids" not in task.trigger_message_json
    dws.conversations = []
    script_no_action(worker)
    assert worker.consume_once() == 1
    runner = worker._test_agent_runner
    assert isinstance(runner, FakeAgentResultRunner)
    assert all(item.kind != "dingtalk_oa" for item in runner.calls[0][2].materials)
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "skipped"
    assert worker.store.count_reply_tasks(status="done") == 1


def test_mark_seen_tracks_all_latest_trigger_message_ids(tmp_path: Path, monkeypatch):
    first = message("@Alex Chen(明哥) 先看第一点", message_id="msg-mentioned-1")
    second = message("@Alex Chen(明哥) 再看第二点", message_id="msg-mentioned-2")
    third = message("@Alex Chen(明哥) 最后总结一下", message_id="msg-mentioned-3")
    dws = FakeDws([conversation()], {"cid-1": [first, second, third]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="no action needed")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    trigger = DingTalkAutoReplyWorker._latest_trigger_message([first, second, third])

    worker._mark_seen([trigger])

    assert worker.store.has_seen("msg-mentioned-1") is True
    assert worker.store.has_seen("msg-mentioned-2") is True
    assert worker.store.has_seen("msg-mentioned-3") is True


def test_latest_trigger_message_uses_input_order_for_equal_timestamps():
    older = message("first", message_id="msg-older", single_chat=True)
    newer = message("second", message_id="msg-newer", single_chat=True)
    older.create_time = newer.create_time = "2026-05-13 20:26:00"

    trigger = DingTalkAutoReplyWorker._latest_trigger_message([older, newer])

    assert trigger.open_message_id == "msg-newer"
    assert trigger.raw_payload["coalesced_message_ids"] == [
        "msg-older",
        "msg-newer",
    ]


def test_group_all_mention_from_unread_conversation_is_processed(
    tmp_path: Path, monkeypatch
):
    all_mention = message("@所有人 今天需要同步一下项目风险", message_id="msg-all")
    all_mention.create_time = "2026-05-25 17:53:12"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [all_mention]},
        unread_messages={"cid-1": [all_mention]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下风险点")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-all"
    assert attempts[0].send_status == "skipped"


def test_group_all_mention_is_case_insensitive_for_ascii_alias(
    tmp_path: Path, monkeypatch
):
    all_mention = message("@All 请大家看一下官网更新内容", message_id="msg-all-case")
    all_mention.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [conversation()],
        {"cid-1": [all_mention]},
        unread_messages={"cid-1": [all_mention]},
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-all-case"
    assert attempts[0].send_status == "skipped"


def test_group_mention_from_read_conversation_is_processed_from_mentions(
    tmp_path: Path, monkeypatch
):
    mentioned = message(
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-mkt-mention",
    )
    mentioned.open_conversation_id = "cid-mkt"
    mentioned.conversation_title = "MKT core"
    mentioned.create_time = "2026-05-25 19:21:56"
    dws = FakeDws(
        [],
        {"cid-mkt": [mentioned]},
        unread_messages={"cid-mkt": []},
    )
    dws.mentioned_messages = {"cid-mkt": [mentioned]}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="会，但只处理需要回复的消息"
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].conversation_title == "MKT core"
    assert attempts[0].trigger_message_id == "msg-mkt-mention"
    assert attempts[0].send_status == "skipped"


def test_group_all_mention_from_read_conversation_is_processed_from_broadcast_search(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@All 新的官网更新一共16页，请大家打开每一个html文档",
        message_id="msg-website-all",
    )
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    broadcast.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [],
        {"cid-website": [broadcast]},
        unread_messages={"cid-website": []},
    )
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下官网内容")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].conversation_title == "官网迭代群"
    assert attempts[0].trigger_message_id == "msg-website-all"
    assert attempts[0].send_status == "skipped"


def test_current_user_all_mention_is_filtered_from_broadcast_search(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@所有人 我已经更新完了",
        message_id="msg-self-all",
        sender_user_id="principal-user-1",
    )
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    broadcast.create_time = "2026-05-28 04:04:53"
    dws = FakeDws(
        [],
        {"cid-website": [broadcast]},
        unread_messages={"cid-website": []},
    )
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker._broadcast_messages_by_conversation() == {}


def test_broadcast_filter_does_not_resolve_sender_without_stable_identity(
    tmp_path: Path, monkeypatch
):
    broadcast = message(
        "@所有人 系统通知",
        message_id="msg-system-all",
    )
    broadcast.sender_name = "数据小蜜"
    broadcast.sender_user_id = None
    broadcast.sender_open_dingtalk_id = None
    broadcast.open_conversation_id = "cid-website"
    broadcast.conversation_title = "官网迭代群"
    dws = FakeDws([], {"cid-website": [broadcast]})
    dws.broadcast_messages = {"cid-website": [broadcast]}
    codex = FakeCodex(CodexDecision(action=CodexAction.NO_REPLY, reason="not relevant"))
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    assert worker._broadcast_messages_by_conversation() == {"cid-website": [broadcast]}
    assert dws.current_user_checks == []


def test_read_group_mention_is_skipped_when_later_current_user_text_replied(
    tmp_path: Path, monkeypatch
):
    mentioned = message(
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-mkt-mention",
    )
    mentioned.open_conversation_id = "cid-mkt"
    mentioned.conversation_title = "MKT core"
    mentioned.create_time = "2026-05-25 19:21:56"
    manual_reply = principal_message(
        "会的，晚上也会处理需要回复的消息",
        message_id="msg-principal-text",
        create_time="2026-05-25 19:24:00",
    )
    manual_reply.open_conversation_id = "cid-mkt"
    manual_reply.conversation_title = "MKT core"

    class ContextAwareFakeDws(FakeDws):
        def read_recent_messages(self, conversation: DingTalkConversation):
            if conversation.open_conversation_id == "cid-mkt":
                if conversation.last_message_create_at is None:
                    return [manual_reply, mentioned]
                return [mentioned]
            return super().read_recent_messages(conversation)

    dws = ContextAwareFakeDws(
        [],
        {"cid-mkt": [manual_reply, mentioned]},
        unread_messages={"cid-mkt": []},
    )
    dws.mentioned_messages = {"cid-mkt": [mentioned]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    worker.run_once()

    assert codex.calls == []
    assert worker.store.list_reply_attempts(limit=10) == []


def test_read_group_mention_after_seen_message_is_processed_from_mentions(
    tmp_path: Path, monkeypatch
):
    handled = message(
        "@Alex Chen(明哥) 客户问 Hyperion 怎么讲？",
        message_id="msg-handled",
    )
    handled.open_conversation_id = "cid-hyperion"
    handled.conversation_title = "奔驰北美-Hyperion需求"
    handled.create_time = "2026-05-29 14:19:12"
    bot_reply = principal_message(
        "不能讲成 persona 报告，要讲成 marketing 决策盲区。",
        message_id="msg-bot-reply",
        create_time="2026-05-29 14:32:48",
    )
    bot_reply.open_conversation_id = "cid-hyperion"
    bot_reply.conversation_title = "奔驰北美-Hyperion需求"
    follow_up = message(
        "@Alex Chen(明哥) 这个好。@何耘光(Jack He(Yunguang He)) 我喜欢明哥分身的答案，更抓客户胃口",
        message_id="msg-follow-up",
    )
    follow_up.open_conversation_id = "cid-hyperion"
    follow_up.conversation_title = "奔驰北美-Hyperion需求"
    follow_up.create_time = "2026-05-29 14:35:51"
    conversation_record = conversation()
    conversation_record.open_conversation_id = "cid-hyperion"
    conversation_record.title = "奔驰北美-Hyperion需求"
    conversation_record.unread_point = 0

    dws = FakeDws(
        [],
        {"cid-hyperion": [handled, bot_reply, follow_up]},
        unread_messages={"cid-hyperion": []},
    )
    dws.mentioned_messages = {"cid-hyperion": [follow_up]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    worker.store.upsert_conversation(
        "cid-hyperion",
        "奔驰北美-Hyperion需求",
        False,
        None,
    )
    worker.store.mark_seen("msg-handled", "cid-hyperion")

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-follow-up"
    assert "我喜欢明哥分身的答案" in tasks[0].trigger_text
    assert dws.recent_message_reads == ["cid-hyperion"]


def test_split_person_auto_reply_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    handled = message(
        "@Alex Chen(明哥) 和我迭代一下材料",
        message_id="msg-handled",
    )
    handled.open_conversation_id = "cid-iter"
    handled.conversation_title = "迭代群"
    handled.create_time = "2026-05-29 21:53:36"
    missed = message(
        "@Alex Chen(明哥) 这个分身能读群历史和群文件吗？",
        message_id="msg-missed",
    )
    missed.open_conversation_id = "cid-iter"
    missed.conversation_title = "迭代群"
    missed.create_time = "2026-05-29 21:55:10"
    auto_reply = principal_message(
        "可以，别先把我屏蔽了。（by明哥分身）",
        message_id="msg-auto-reply",
        create_time="2026-05-29 21:55:41",
    )
    auto_reply.open_conversation_id = "cid-iter"
    auto_reply.conversation_title = "迭代群"
    conversation_record = conversation()
    conversation_record.open_conversation_id = "cid-iter"
    conversation_record.title = "迭代群"
    conversation_record.unread_point = 0

    dws = FakeDws(
        [],
        {"cid-iter": [handled, missed, auto_reply]},
        unread_messages={"cid-iter": []},
    )
    dws.mentioned_messages = {"cid-iter": [missed]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="不应该调用")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)
    worker.store.upsert_conversation("cid-iter", "迭代群", False, None)
    worker.store.mark_seen("msg-handled", "cid-iter")

    queued = worker.produce_once()

    tasks = worker.store.claim_reply_tasks(limit=10)
    assert queued == 1
    assert len(tasks) == 1
    assert tasks[0].trigger_message_id == "msg-missed"
    assert "能读群历史和群文件吗" in tasks[0].trigger_text
    assert dws.recent_message_reads == ["cid-iter"]


def test_group_mentions_are_processed_by_message_time_not_fetch_order(
    tmp_path: Path, monkeypatch
):
    older_mention = message(
        "@Alex Chen(明哥) 怎么规避客户拿给别的 vendor 比价？",
        message_id="msg-older-mention",
    )
    older_mention.create_time = "2026-05-26 07:54:36"
    newer_mention = message(
        "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见",
        message_id="msg-newer-mention",
    )
    newer_mention.create_time = "2026-05-26 08:34:57"
    latest_file = message("[文件] 新版文档.docx", message_id="msg-latest-file")
    latest_file.create_time = "2026-05-26 08:57:46"
    dws = FakeDws(
        [conversation()],
        {
            "cid-1": [
                latest_file,
                newer_mention,
                older_mention,
            ]
        },
        unread_messages={"cid-1": [latest_file]},
    )
    dws.mentioned_messages = {
        "cid-1": [
            older_mention,
            newer_mention,
        ]
    }
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert len(attempts) == 1
    assert attempts[0].trigger_message_id == "msg-newer-mention"
    assert (
        attempts[0].trigger_text == "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见"
    )


def test_current_user_file_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 明哥，你的数字分身在你睡着的时候还会运作吗？",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-25 19:21:56"
    self_file = principal_message(
        "[文件] 北京星尘_B轮融资BP_图片版_19页.pdf",
        message_id="msg-self-file",
        create_time="2026-05-26 03:49:28",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [self_file, trigger]},
        unread_messages={"cid-1": [self_file]},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY, reply_text="会，但只处理需要回复的消息"
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    attempts = worker.store.list_reply_attempts(limit=10)
    assert len(agent_runner(worker).calls) == 1
    assert attempts[0].trigger_message_id == "msg-trigger"


def test_processing_ack_does_not_hide_unanswered_group_mention(
    tmp_path: Path, monkeypatch
):
    trigger = message(
        "@Alex Chen(明哥) 明哥请审一下这个文档，给一下意见",
        message_id="msg-trigger",
    )
    trigger.create_time = "2026-05-26 08:34:57"
    ack = principal_message(
        PROCESSING_ACK,
        message_id="msg-processing-ack",
        create_time="2026-05-26 09:05:36",
    )
    dws = FakeDws(
        [conversation()],
        {"cid-1": [ack, trigger]},
        unread_messages={"cid-1": [ack]},
    )
    dws.mentioned_messages = {"cid-1": [trigger]}
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="我看一下")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch, dry_run=True)

    script_no_action(worker)
    worker.run_once()

    prompt = agent_prompt(worker)
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].trigger_message_id == "msg-trigger"
    assert PROCESSING_ACK not in prompt
    assert "请审一下这个文档" in prompt


def test_internal_personnel_question_missing_subject_blocks_without_sending(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个人后续怎么处理？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.HANDOFF_TO_HUMAN,
            reason="missing personnel subject",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "missing personnel subject",
            code="needs_human",
        ),
    )

    worker.run_once()

    assert final_sent(dws) == []
    attempts = worker.store.list_reply_attempts(limit=10)
    assert attempts[0].action == "agent_run"
    assert attempts[0].send_status == "needs_human"
    assert attempts[0].send_error == "needs_human"
    assert attempts[0].codex_reason == "missing personnel subject"
    assert attempts[0].final_reply_text == ""
    assert attempts[0].draft_reply_text == ""


def test_internal_personnel_question_allows_private_self_subject(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("我转正怎么看？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="你这次转正材料看起来可以，但后续要补齐闭环。",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="sender-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent verified the requester is the subject.")

    worker.run_once()

    assert final_sent(dws) == []
    assert "我转正怎么看" in agent_prompt(worker)


def test_internal_personnel_question_allows_private_hr_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.hr_users.add("sender-user-1")
    dws.manager_chains["subject-user-1"] = ["sender-user-1"]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent handled the HR personnel request.")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.manager_chain_calls == []


def test_internal_personnel_question_does_not_auto_allow_manager(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.user_profiles["subject-user-1"] = DwsUserProfile(
        user_id="subject-user-1",
        name="张三",
        department_ids={"dept-1"},
    )
    dws.manager_chains["subject-user-1"] = ["sender-user-1"]
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个涉及其他人的人事信息，我不能直接回答。",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent refused disclosure after live checks.")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.user_profile_calls == []


def test_internal_personnel_question_refuses_unrelated_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True)]},
    )
    dws.user_profiles["subject-user-1"] = DwsUserProfile(
        user_id="subject-user-1",
        name="张三",
        department_ids={"dept-1"},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个涉及其他人的人事信息，我不能直接回答。",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(
        worker, "Agent refused disclosure to an unrelated requester."
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.user_profile_calls == []


def test_internal_personnel_question_allows_agent_reply_in_group(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [message("@Alex Chen(明哥) 我绩效怎么定？", single_chat=False)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="你这次可以按高绩效处理",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="sender-user-1",
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent handled a group personnel request.")

    worker.run_once()

    assert final_sent(dws) == []
    assert "我绩效怎么定" in agent_prompt(worker)


def test_candidate_question_missing_context_uses_agent_clarifying_question(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.ASK_CLARIFYING_QUESTION,
            reply_text="我这边没找到这个候选人的面试记录和岗位信息，你把简历或面试听记发我一下。",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=False,
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent asked for the genuinely missing evidence.")

    worker.run_once()

    assert final_sent(dws) == []
    assert "这个候选人怎么样" in agent_prompt(worker)


def test_candidate_question_allows_related_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-sales"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent handled the candidate request.")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.user_department_calls == []


def test_candidate_question_refuses_unrelated_department_requester(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("这个候选人怎么样？", single_chat=True)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-product"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="这个候选人信息只回答相关部门的人。",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent refused candidate disclosure.")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.user_department_calls == []


def test_candidate_question_allows_group_reply_without_sender_department_check(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation(single_chat=False)],
        {"cid-1": [message("@Alex Chen(明哥) 这个候选人怎么样？", single_chat=False)]},
    )
    dws.user_departments["sender-user-1"] = {"dept-product"}
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="可以推进",
            sensitivity_kind=SensitivityKind.EXTERNAL_CANDIDATE,
            candidate_context_known=True,
            candidate_department_ids=["dept-sales"],
        )
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    script_completed_result(worker, "Agent handled the group candidate request.")

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.user_department_calls == []


def test_permission_lookup_failure_records_error_and_does_not_send(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation(single_chat=True)],
        {"cid-1": [message("张三绩效怎么定？", single_chat=True, sender_user_id=None)]},
    )
    codex = FakeCodex(
        CodexDecision(
            action=CodexAction.SEND_REPLY,
            reply_text="先按事实反馈",
            sensitivity_kind=SensitivityKind.INTERNAL_PERSONNEL,
            personnel_subject_user_id="subject-user-1",
        )
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert store.count_errors() == 1
    assert store.has_seen("msg-1") is False


def test_dry_run_does_not_mutate_terminal_state(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws(
        [conversation()], {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]}
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        dry_run=True,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )

    worker.run_once()

    assert final_sent(dws) == []
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0


def test_send_failure_records_error_and_does_not_mark_seen(tmp_path: Path, monkeypatch):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "The native send outcome is unknown.",
            code="send_result_unknown",
            side_effect_state=SideEffectState.UNKNOWN,
        ),
    )

    worker.run_once()

    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_errors() == 0
    assert store.count_reply_tasks(status="pending") == 1
    assert dws.send_attempt_count == 0
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "pending_reconciliation"
    assert attempt.retry_count == 0
    assert attempt.send_error == "send_result_unknown"
    run = store.get_agent_run(1)
    assert run is not None
    assert run.status == "unknown"


def test_send_failure_requeues_reply_task_for_consumer_retry(
    tmp_path: Path, monkeypatch
):
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "The native send failed before any side effect.",
            code="send_failed_before_effect",
            retryable=True,
        ),
    )

    worker.run_once()

    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_reply_tasks(status="pending") == 1
    assert store.count_reply_tasks(status="done") == 0
    retried = store.claim_reply_tasks(limit=1)
    assert len(retried) == 1
    assert (
        retried[0].execution_generation == store.get_reply_task(1).execution_generation
    )
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "send_failed_before_effect"


def test_retryable_consumer_send_failure_at_limit_emits_one_terminal_notification(
    tmp_path: Path, monkeypatch
):
    notifications = []
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 这个怎么处理？")]},
        send_error=RuntimeError("send failed"),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "The native send failed before any side effect.",
            code="send_failed_before_effect",
            retryable=True,
        ),
    )
    monkeypatch.setattr(
        "app.worker.send_browser_notification",
        lambda **kwargs: notifications.append(kwargs) or True,
    )
    worker.produce_once()

    worker.consume_once(max_tasks=1)

    assert len(notifications) == 1
    assert worker.store.count_reply_tasks(status="pending") == 0
    assert worker.store.count_reply_tasks(status="failed") == 1
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "failed"
    assert dws.send_attempt_count == 0


def test_pat_authorization_error_is_recorded_as_failed_without_retry_or_url(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [trigger]},
        send_error=DwsError(
            "dws command failed with exit code 4: PAT_HIGH_RISK_NO_PERMISSION",
            code="PAT_HIGH_RISK_NO_PERMISSION",
        ),
    )
    codex = FakeCodex(
        CodexDecision(action=CodexAction.SEND_REPLY, reply_text="先按A方案走")
    )
    gate = FixedGate("dingtalk", ChannelGateState.NEEDS_LOGIN)
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        channel_gates={
            "dingtalk": gate,
            "lark": FixedGate("lark", ChannelGateState.READY),
        },
    )
    store = worker.store
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )

    assert worker.consume_once(max_tasks=1) == 0

    assert dws.send_attempt_count == 0
    assert store.has_seen("msg-1") is False
    assert store.count_sent_replies() == 0
    assert store.count_reply_tasks(status="pending") == 1
    assert store.count_reply_attempts() == 0
    assert gate.calls == 1
    assert codex.calls == []


def test_handoff_ding_failure_does_not_block_ack(tmp_path: Path, monkeypatch):
    notifications: list[dict[str, str | None]] = []
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 不要分身，真人看一下")]},
        ding_error=RuntimeError("ding failed"),
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "Agent requested principal review.",
            code="needs_human",
        ),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    monkeypatch.setattr("app.worker.send_browser_notification", lambda **_: False)

    worker.run_once()

    assert final_sent(dws) == []
    assert dws.bot_direct_messages == []
    assert dws.message_text_emotions == []
    assert store.has_seen("msg-1") is False
    assert store.count_errors() == 0
    assert store.count_reply_tasks(status="done") == 1
    assert notifications == [
        {
            "title": "CEO 需要确认：@Alex Chen(明哥) 不要分身，真人看一下",
            "message": (
            "需要你确认：系统不会代为作出这项管理决定。\n"
            "事项：@Alex Chen(明哥) 不要分身，真人看一下\n"
            "已核验：Agent requested principal review.\n"
            "操作：打开审计页阅读已核验事实，并提交具体处理指令。"
            ),
        }
    ]
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "needs_human"


def test_needs_human_agent_attempt_publishes_browser_notification(
    tmp_path: Path, monkeypatch
):
    browser_notifications: list[dict[str, str | None]] = []
    trigger = message("@Alex Chen(明哥) 需要本人确认")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "需要本人确认。",
            code="principal_confirmation_required",
        ),
    )
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **kwargs: browser_notifications.append(kwargs) or True,
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert browser_notifications == [
        {
            "title": "CEO 需要确认：@Alex Chen(明哥) 需要本人确认",
            "message": (
                "需要你确认：系统不会代为作出这项管理决定。\n"
                "事项：@Alex Chen(明哥) 需要本人确认\n"
                "已核验：需要本人确认。\n"
                "操作：打开审计页阅读已核验事实，并提交具体处理指令。"
            ),
            "url": worker._notification_url(conversation(), attempt_id=attempt.id),
            "notification_id": worker._problem_notification_id(
                worker.store.get_reply_task(1)
            ),
            "detail_url": f"/attempts/{attempt.id}",
        }
    ]


def test_needs_human_agent_attempt_falls_back_to_macos_notification(
    tmp_path: Path, monkeypatch
):
    notifications: list[dict[str, str | None]] = []
    trigger = message("@Alex Chen(明哥) 需要本人确认")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "需要本人确认。",
            code="principal_confirmation_required",
        ),
    )
    monkeypatch.setattr("app.worker.send_browser_notification", lambda **_: False)
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )

    worker.run_once()

    assert notifications == [
        {
            "title": "CEO 需要确认：@Alex Chen(明哥) 需要本人确认",
            "message": (
                "需要你确认：系统不会代为作出这项管理决定。\n"
                "事项：@Alex Chen(明哥) 需要本人确认\n"
                "已核验：需要本人确认。\n"
                "操作：打开审计页阅读已核验事实，并提交具体处理指令。"
            ),
        }
    ]


def test_retryable_failed_agent_attempt_does_not_notify_before_limit(
    tmp_path: Path, monkeypatch
):
    browser_notifications: list[dict[str, str | None]] = []
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
        max_task_attempts=3,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "provider temporarily unavailable",
            code="provider_unavailable",
            retryable=True,
        ),
    )
    monkeypatch.setattr(
        "app.notification._send_browser_notification",
        lambda **kwargs: browser_notifications.append(kwargs) or True,
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)

    worker.run_once()

    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert worker.store.count_reply_tasks(status="pending") == 1
    assert browser_notifications == []


def test_provider_capacity_failure_stays_pending_after_retry_limit(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 这个怎么处理？")
    worker = make_worker(
        tmp_path,
        FakeDws([conversation()], {"cid-1": [trigger]}),
        FakeCodex(CodexDecision(action=CodexAction.NO_REPLY)),
        monkeypatch,
        max_task_attempts=1,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "Codex provider capacity is temporarily unavailable.",
            code="codex_provider_unavailable",
            retryable=True,
        ),
    )
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)

    worker.run_once()

    task = worker.store.get_reply_task(1)
    assert task is not None
    assert task.status == "pending"
    assert task.error == "codex_provider_unavailable"
    assert task.available_at > ""
    assert task.attempts == 0


def test_handoff_records_one_error_when_external_delivery_falls_back_to_local(
    tmp_path: Path, monkeypatch
):
    notifications: list[dict[str, str | None]] = []
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 不要分身，真人看一下")]},
        ding_error=RuntimeError("ding failed"),
        send_error=RuntimeError("bot failed"),
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "Agent requested principal review.",
            code="needs_human",
        ),
    )
    monkeypatch.setattr(
        "app.worker.send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
    )
    monkeypatch.setattr("app.worker.send_browser_notification", lambda **_: False)

    worker.run_once()

    assert store.list_errors() == []
    assert dws.dings == []
    assert dws.bot_direct_messages == []
    assert notifications == [
        {
            "title": "CEO 需要确认：@Alex Chen(明哥) 不要分身，真人看一下",
            "message": (
            "需要你确认：系统不会代为作出这项管理决定。\n"
            "事项：@Alex Chen(明哥) 不要分身，真人看一下\n"
            "已核验：Agent requested principal review.\n"
            "操作：打开审计页阅读已核验事实，并提交具体处理指令。"
            ),
        }
    ]
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"


def test_handoff_text_emotion_failure_still_notifies_and_marks_seen(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    dws = FakeDws(
        [conversation()],
        {"cid-1": [message("@Alex Chen(明哥) 不要分身，真人看一下")]},
    )
    dws.text_emotion_error = DwsError(
        "token verified failed",
        code="TOKEN_VERIFIED_FAILED",
    )
    codex = FakeCodex(CodexDecision(action=CodexAction.HANDOFF_TO_HUMAN))
    worker = make_worker(
        tmp_path,
        dws,
        codex,
        monkeypatch,
        max_task_attempts=1,
    )
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.NEEDS_HUMAN,
            "Agent requested principal review.",
            code="needs_human",
        ),
    )
    worker.produce_once()

    assert worker.consume_once(max_tasks=1) == 1

    assert final_sent(dws) == []
    assert dws.message_text_emotions == []
    assert dws.dings == []
    assert store.has_seen("msg-1") is False
    assert store.count_reply_tasks(status="done") == 1
    attempt = store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "needs_human"


def test_persists_codex_last_session_id_after_decision(tmp_path: Path, monkeypatch):
    dws = FakeDws([conversation()], {"cid-1": [message("@Alex Chen(明哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        next_session_id="session-1",
    )
    worker = make_worker(tmp_path, dws, codex, monkeypatch)
    store = worker.store
    script_agent_result(
        worker,
        explicit_agent_result(ScriptOutcome.NO_ACTION, "cc only"),
        session_id="session-1",
    )

    worker.run_once()

    assert store.get_codex_session_id("cid-1") is None
    run = store.get_agent_run(1)
    assert run is not None
    assert run.codex_session_id == "session-1"


def test_stale_codex_last_session_id_is_not_persisted(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    monkeypatch.setattr("app.worker.send_macos_notification", lambda **_: None)
    dws = FakeDws([conversation()], {"cid-1": [message("@Alex Chen(明哥) cc一下")]})
    codex = FakeCodex(
        CodexDecision(action=CodexAction.NO_REPLY, reason="cc only"),
        last_session_id="stale-session",
    )
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=codex,
        now_provider=fixed_worker_now,
        channel_gates=fixed_channel_gates(),
    )

    worker.run_once()

    assert store.get_codex_session_id("cid-1") is None


def test_mail_reply_action_executes_before_chat_and_persists_result(
    tmp_path: Path, monkeypatch
):
    trigger = message("@Alex Chen(明哥) 审批并回复这封邮件")
    dws = FakeDws([conversation()], {"cid-1": [trigger]})
    decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="邮件已审阅并回复。",
        system_actions=[
            {
                "type": "dws_mail_reply",
                "mailbox": "derek@example.com",
                "message_id": "mail-1",
                "subject": "Re: 评奖结果",
                "content": "确认无误，可以发布。",
            },
            {"type": "send_dingtalk_reply", "reply_text_ref": "user_response.text"},
        ],
    )
    worker = make_worker(tmp_path, dws, FakeCodex(decision), monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.COMPLETED,
            "Agent replied to the mail and then acknowledged the chat.",
            side_effect_state=SideEffectState.CONFIRMED,
        ),
        receipts=(
            execution_receipt("mail-1", "mail message reply", "b" * 64),
            execution_receipt("dingtalk-reply", "chat message reply", "c" * 64),
        ),
    )

    worker.run_once()

    assert dws.mail_replies == []
    assert final_sent(dws) == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    receipts = worker.store.list_agent_execution_receipts(1)
    assert [receipt.operation_id for receipt in receipts] == [
        "mail-1",
        "dingtalk-reply",
    ]


def test_retry_after_chat_failure_does_not_send_mail_twice(tmp_path: Path, monkeypatch):
    trigger = message("@Alex Chen(明哥) 审批并回复这封邮件")
    dws = FakeDws(
        [conversation()], {"cid-1": [trigger]}, send_error=DwsError("chat down")
    )
    decision = CodexDecision(
        action=CodexAction.SEND_REPLY,
        reply_text="邮件已审阅并回复。",
        system_actions=[
            {
                "type": "dws_mail_reply",
                "mailbox": "derek@example.com",
                "message_id": "mail-1",
                "subject": "Re: 评奖结果",
                "content": "确认无误，可以发布。",
            }
        ],
    )
    worker = make_worker(tmp_path, dws, FakeCodex(decision), monkeypatch)
    script_agent_result(
        worker,
        explicit_agent_result(
            ScriptOutcome.FAILED,
            "Mail completed, but the chat reply outcome is unknown.",
            code="chat_result_unknown",
            side_effect_state=SideEffectState.UNKNOWN,
        ),
        receipts=(execution_receipt("mail-1", "mail message reply", "d" * 64),),
    )

    worker.run_once()
    assert dws.mail_replies == []
    attempt = worker.store.get_reply_attempt(1)
    assert attempt is not None
    assert attempt.action == "agent_run"
    assert attempt.send_status == "pending_reconciliation"
    assert attempt.send_error == "chat_result_unknown"
    run = worker.store.get_agent_run(1)
    assert run is not None
    assert run.status == "unknown"

    assert worker.store.count_reply_attempts() == 1
    persisted = worker.store.get_agent_run(run.id)
    assert persisted is not None and persisted.status == "unknown"
    task = worker.store.get_reply_task(run.reply_task_id)
    assert task is not None and task.status == "pending"
    assert (
        _get_audit_run(
            worker.store,
            run.reply_task_id,
            run.execution_generation,
        ).id
        == run.id
    )
