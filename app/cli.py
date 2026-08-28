import argparse
import errno
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, NonNegativeInt, PositiveInt

from app.codex_decision import CodexDecisionRunner
from app.database_backup import (
    BACKUP_CHECK_INTERVAL_SECONDS,
    backup_database_if_due,
)
from app.config import (
    codex_capacity_retry_duration,
    consumer_poll_interval_seconds,
    consumer_worker_count,
    embedding_api_key,
    embedding_base_url,
    embedding_enabled,
    embedding_model,
    embedding_timeout_seconds,
    feedback_spike_vercel_base_url,
    meeting_consumer_poll_interval_seconds,
    meeting_producer_interval_seconds,
    meeting_settle_seconds,
    principal_display_name,
    producer_interval_seconds,
    repository_upgrade_branch,
    repository_upgrade_check_interval_seconds,
    repository_upgrade_enabled,
    repository_upgrade_remote,
    profile_evidence_dir,
    task_daily_interval_seconds,
    task_follow_up_interval_seconds,
    task_work_item_interval_seconds,
    worker_db_path,
    work_profile_path,
)
from app.corpus import (
    append_records,
    build_dingtalk_records_from_sender_payload,
    build_style_profile,
    extract_minutes_records,
    load_corpus_records,
    write_records,
)
from app.embedding import EmbeddingClient
from app.dws_client import (
    DINGTALK_MESSAGE_TIME_ZONE,
    DwsClient,
    DwsError,
    extract_recall_key_from_send_result,
    local_time_zone_name,
    native_reply_delivery_payload,
)
from app.feedback_spike import (
    build_events_url,
    send_feedback_spike_links,
)
from app.external_retry import is_external_dependency_error
from app.message_split import split_dingtalk_text
from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.notification import send_macos_notification
from app.meeting_alignment import (
    MEETING_DISCOVERY_ACTIVATED_AT_STATE_KEY,
    consume_meeting_alignment_jobs,
    produce_meeting_alignment_jobs,
    queue_recent_meeting_alignment_replay,
    recover_meeting_alignment_jobs,
)
from app.meeting_alignment_agent import MeetingAlignmentCodexRunner
from app.org_cache import (
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from app.store import AutoReplyStore
from app.task_agent import TaskAgentCodexRunner, TaskAgentRunner, process_work_item
from app.task_memory_backfill import (
    ProjectMemoryContextCodexRunner,
    validate_project_memory_context,
)
from app.task_models import ProjectMemoryContext
from app.task_noise_backfill import (
    RoutineProcessBackfillResult,
    backfill_routine_process_todos,
)
from app.todo_completion import enqueue_follow_up_completion_checks
from app.todo_sync import (
    dispatch_task_todo_sync_outbox,
    pull_dingtalk_todo_statuses,
    retry_failed_dingtalk_todo_links,
)
from app.work_profile import (
    build_initial_profile,
    collect_dingtalk_kb_evidence,
    collect_existing_corpus_evidence,
    collect_local_doc_evidence,
    render_markdown_profile,
    write_jsonl,
)
from app.worker import (
    DingTalkAutoReplyWorker,
    RECOVERABLE_AGENT_RUNTIME_ERRORS,
    _is_codex_provider_recovery_wait_reason,
    _is_terminal_codex_auth_failure,
    _normalize_codex_stop_error_reason,
)
from app.codex_capacity import (
    CODEX_CAPACITY_EXHAUSTED_MESSAGE,
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    is_codex_capacity_exhausted,
)
from app.weekly_okr_report import (
    DEFAULT_SCHEDULE_HOUR,
    refresh_company_okr_archive_command,
    weekly_okr_report_command,
    weekly_okr_report_window_open,
)

WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS = 3
WORK_SUMMARY_RETRY_BASE_DELAY_SECONDS = 60
WORK_SUMMARY_RETRY_MAX_DELAY_SECONDS = 15 * 60
WORK_SUMMARY_TRANSIENT_ERROR_MARKERS = (
    "stream disconnected before completion",
    "timeout awaiting response headers",
    "http2: timeout",
    "i/o timeout",
    "temporary failure",
    "failed to resolve",
    "connection reset",
    "connection refused",
    "codex exec timed out",
    "task agent codex timed out",
    "non-skip task decision requires memory_recall tool event",
    "unexpected status 401 unauthorized",
    "missing bearer or basic authentication",
)

WORK_SUMMARY_DISCARDABLE_ERROR_MARKERS = (
    (
        "follow_up_draft.todo_id",
        "does not belong to project",
    ),
)

LIVE_SEND_BLOCKERS = (
    "deterministic personnel/candidate permission gates",
    "handoff-clear detection",
    "batching semantics",
)
LIVE_SEND_GUARD_ENV = "CEO_LIVE_SEND_BLOCKERS_ACCEPTED"
DEFAULT_DING_ROBOT_NAME = None
DEFAULT_WORKSPACE = Path.home() / "Documents" / "memory"
OKR_LIVE_SOURCE_COMMAND_ENV = "CEO_OKR_LIVE_SOURCE_COMMAND"
OKR_SOURCE_KIND_ENV = "CEO_OKR_SOURCE_KIND"
OKR_OBJECTIVE_RULE_ID_ENV = "CEO_OKR_OBJECTIVE_RULE_ID"
OKR_REVIEW_CODEX_TIMEOUT_SECONDS = 1200
OKR_REVIEW_CODEX_IDLE_TIMEOUT_SECONDS = 900
WORK_SUMMARY_INPUT_STALE_GRACE_SECONDS = 60
run_audit_web = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    return _repo_root() / "data"


def _default_corpus_dir() -> Path:
    return _repo_root() / "data" / "corpus"


class WorkerSettings(BaseModel):
    workspace: Path = DEFAULT_WORKSPACE
    db_path: Path = worker_db_path()
    corpus_dir: Path = _default_corpus_dir()
    dry_run: bool = False
    poll_interval_seconds: PositiveInt = 300
    batch_seconds: PositiveInt = 120
    ding_robot_code: str | None = None
    ding_robot_name: str | None = DEFAULT_DING_ROBOT_NAME
    ding_receiver_user_id: str | None = None
    dws_transient_retry_attempts: PositiveInt = 3
    dws_transient_retry_delay_seconds: float = 1.0
    codex_timeout_seconds: PositiveInt = 1200
    codex_idle_timeout_seconds: PositiveInt = 900
    task_codex_timeout_seconds: PositiveInt = 1200
    task_codex_idle_timeout_seconds: PositiveInt = 900
    task_work_item_interval_seconds: PositiveInt = 60
    task_daily_interval_seconds: PositiveInt = 86_400
    task_follow_up_interval_seconds: PositiveInt = 60
    oa_pending_scan_enabled: bool = True
    oa_pending_scan_interval_seconds: PositiveInt = 3_600
    oa_pending_scan_lookback_days: PositiveInt = 365
    meeting_producer_interval_seconds: PositiveInt = 60
    meeting_consumer_poll_interval_seconds: PositiveInt = 10
    meeting_settle_seconds: PositiveInt = 600
    consumer_workers: PositiveInt = 2
    max_batches: NonNegativeInt | None = None
    repository_upgrade_enabled: bool = repository_upgrade_enabled()
    repository_upgrade_remote: str = repository_upgrade_remote()
    repository_upgrade_branch: str = repository_upgrade_branch()
    repository_upgrade_check_interval_seconds: PositiveInt = repository_upgrade_check_interval_seconds()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value: 1/0, true/false, yes/no, or on/off")


def _not_send_message_default(default: bool) -> bool:
    if os.getenv("CEO_DRY_RUN") is not None:
        _env_bool("CEO_DRY_RUN", default)
    if os.getenv("CEO_NOT_SEND_MESSAGE") is not None:
        return _env_bool("CEO_NOT_SEND_MESSAGE", default)
    return _env_bool("CEO_DRY_RUN", default)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _optional_non_negative_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return _non_negative_int(value)


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    defaults = WorkerSettings()
    parser = argparse.ArgumentParser(prog="ceo-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in (
        "probe-dws",
        "probe-agent-runtimes",
        "run-once",
        "run",
        "service",
        "produce-once",
        "produce",
        "consume-once",
        "consume",
        "process-work-items",
        "retry-work-summary-input",
        "backfill-task-memory-context",
        "backfill-routine-process-todos",
        "process-okr-reviews",
        "weekly-okr-report",
        "refresh-okr-archive",
        "scan-task-sources",
        "scan-oa-approvals",
        "read-oa-approval-detail",
        "process-follow-ups",
        "check-follow-up-completions",
        "daily-task-maintenance",
        "quality-check",
        "channel-doctor",
        "doctor-mcp",
        "setup-memory-connector",
        "build-corpus",
        "collect-corpus",
        "refresh-org-cache",
        "feedback",
        "feedback-spike",
        "audit-web",
        "export-feedback",
        "test-ding",
        "rerun-message",
        "send-attempt",
        "resolve-agent-run",
        "reset-codex-sessions",
        "build-work-profile",
        "replay-recent-meetings",
        "repository-updater",
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--db", default=os.getenv("CEO_WORKER_DB", str(defaults.db_path)))
        subparser.add_argument("--workspace", default=os.getenv("CEO_WORKSPACE", str(defaults.workspace)))
        subparser.add_argument("--corpus-dir", default=os.getenv("CEO_CORPUS_DIR", str(defaults.corpus_dir)))
        subparser.add_argument(
            "--not-send-message",
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=_not_send_message_default(defaults.dry_run),
            help=(
                "record decisions without sending DingTalk messages; "
                "--dry-run is kept as a compatibility alias"
            ),
        )
        subparser.add_argument(
            "--poll-interval-seconds",
            type=_positive_int,
            default=_positive_int(os.getenv("CEO_POLL_INTERVAL_SECONDS", str(defaults.poll_interval_seconds))),
        )
        subparser.add_argument(
            "--batch-seconds",
            type=_positive_int,
            default=_positive_int(os.getenv("CEO_BATCH_SECONDS", str(defaults.batch_seconds))),
        )
        subparser.add_argument(
            "--max-batches",
            type=_non_negative_int,
            default=_optional_non_negative_int_env("CEO_MAX_BATCHES"),
            help="maximum candidate batches to process before exiting this pass",
        )
        subparser.add_argument(
            "--oa-pending-scan-enabled",
            action=argparse.BooleanOptionalAction,
            default=_env_bool(
                "CEO_OA_PENDING_SCAN_ENABLED",
                defaults.oa_pending_scan_enabled,
            ),
            help="enable the DingTalk OA pending approval scanner",
        )
        subparser.add_argument(
            "--oa-pending-scan-interval-seconds",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_OA_PENDING_SCAN_INTERVAL_SECONDS",
                    str(defaults.oa_pending_scan_interval_seconds),
                )
            ),
            help="seconds between scheduled DingTalk OA pending approval scans",
        )
        subparser.add_argument(
            "--oa-pending-scan-lookback-days",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_OA_PENDING_SCAN_LOOKBACK_DAYS",
                    str(defaults.oa_pending_scan_lookback_days),
                )
            ),
            help="days of DingTalk OA pending approvals to query on each scan",
        )
        subparser.add_argument(
            "--dws-transient-retry-attempts",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_DWS_TRANSIENT_RETRY_ATTEMPTS",
                    str(defaults.dws_transient_retry_attempts),
                )
            ),
            help="number of retries for transient dws discovery/network errors",
        )
        subparser.add_argument(
            "--dws-transient-retry-delay-seconds",
            type=_non_negative_float,
            default=_non_negative_float(
                os.getenv(
                    "CEO_DWS_TRANSIENT_RETRY_DELAY_SECONDS",
                    str(defaults.dws_transient_retry_delay_seconds),
                )
            ),
            help="base delay before retrying transient dws errors; each retry multiplies this by the attempt number",
        )
        subparser.add_argument(
            "--codex-timeout-seconds",
            type=_positive_int,
            default=_positive_int(
                os.getenv("CEO_CODEX_TIMEOUT_SECONDS", str(defaults.codex_timeout_seconds))
            ),
            help="maximum seconds to wait for one Codex decision",
        )
        subparser.add_argument(
            "--codex-idle-timeout-seconds",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_CODEX_IDLE_TIMEOUT_SECONDS",
                    str(defaults.codex_idle_timeout_seconds),
                )
            ),
            help="maximum seconds to wait without Codex stdout/stderr output",
        )
        subparser.add_argument(
            "--task-codex-timeout-seconds",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_TASK_CODEX_TIMEOUT_SECONDS",
                    str(defaults.task_codex_timeout_seconds),
                )
            ),
            help="maximum seconds to wait for one task-agent Codex decision",
        )
        subparser.add_argument(
            "--task-codex-idle-timeout-seconds",
            type=_positive_int,
            default=_positive_int(
                os.getenv(
                    "CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS",
                    str(defaults.task_codex_idle_timeout_seconds),
                )
            ),
            help="maximum seconds to wait without task-agent Codex stdout/stderr output",
        )
        if command == "refresh-org-cache":
            subparser.add_argument("--user-id", action="append", default=[])
        if command == "probe-agent-runtimes":
            subparser.add_argument(
                "--route",
                action="append",
                choices=("codex_oauth", "codex_api"),
                default=[],
                help="probe only this configured route; repeat to select both",
            )
        if command == "read-oa-approval-detail":
            subparser.add_argument("--instance-id", required=True)
        if command == "repository-updater":
            subparser.add_argument("--operation-id", required=True)
        if command == "retry-work-summary-input":
            subparser.add_argument("--input-id", type=_positive_int, required=True)
        if command == "replay-recent-meetings":
            subparser.add_argument("--limit", type=_positive_int, required=True)
            subparser.add_argument(
                "--offset", type=_non_negative_int, default=0
            )
        if command == "backfill-routine-process-todos":
            subparser.add_argument(
                "--todo-id",
                action="append",
                type=int,
                required=True,
                help="Work TODO id to cancel. Repeat for multiple reviewed IDs.",
            )
            subparser.add_argument(
                "--reason",
                required=True,
                help="Audit reason explaining why these TODOs are routine process noise.",
            )
            subparser.add_argument(
                "--apply",
                action="store_true",
                help="Apply changes. Omit for dry-run.",
            )
        if command == "setup-memory-connector":
            subparser.add_argument(
                "--memory-url",
                default=os.getenv("MEMORY_CONNECTOR_URL", ""),
                help="memory connector MCP URL",
            )
            subparser.add_argument(
                "--codex-config",
                default=str(
                    Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()
                    / "config.toml"
                ),
                help="Codex config.toml path",
            )
            subparser.add_argument(
                "--claude-config",
                default=str(
                    Path.home()
                    / "Library"
                    / "Application Support"
                    / "Claude"
                    / "claude_desktop_config.json"
                ),
                help="Claude Desktop config JSON path",
            )
        if command == "doctor-mcp":
            subparser.add_argument(
                "--service-mcp-config",
                default=os.getenv("CEO_SERVICE_MCP_CONFIG_PATH", ""),
                help="service-owned MCP manifest JSON path",
            )
            subparser.add_argument(
                "--verify-live",
                action="store_true",
                help="also verify live reachability for services that support it",
            )
            subparser.add_argument(
                "--notify",
                action="store_true",
                help="record non-ready auth states and notify once",
            )
        if command == "quality-check":
            subparser.add_argument(
                "--state-file",
                default=os.getenv(
                    "CEO_HOURLY_QUALITY_GATE_PATH",
                    str(_default_data_dir() / "hourly-quality-gate.json"),
                ),
                help="path for the fail-closed queue coverage result",
            )
            subparser.add_argument(
                "--verify-channels",
                action=argparse.BooleanOptionalAction,
                default=_env_bool("CEO_QUALITY_CHECK_VERIFY_CHANNELS", True),
                help="run live DingTalk and Lark channel health checks",
            )
        if command == "feedback":
            subparser.add_argument("--attempt-id", type=int, required=True)
            subparser.add_argument("--feedback", required=True)
            subparser.add_argument("--corrected-reply", default="")
        if command == "feedback-spike":
            subparser.add_argument(
                "spike_action",
                choices=("send-links", "events-url"),
            )
            subparser.add_argument(
                "--vercel-base-url",
                default=os.getenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", ""),
                help=(
                    "Root URL of your own feedback Vercel deployment; leave empty "
                    "to disable feedback links"
                ),
            )
            subparser.add_argument("--conversation-id", default="")
            subparser.add_argument("--user-id", default="")
            subparser.add_argument("--open-dingtalk-id", default="")
            subparser.add_argument(
                "--reply-text",
                default="这是一条 CEO agent 反馈链接 spike 测试消息。",
            )
            subparser.add_argument("--original-text", default="")
            subparser.add_argument("--attempt-id", default="")
            subparser.add_argument("--dws-bin", default=os.getenv("DWS_BIN", "dws"))
            subparser.add_argument(
                "--preview",
                action="store_true",
                help="print the generated DingTalk text message without sending",
            )
            subparser.add_argument(
                "--secret",
                default=os.getenv("FEEDBACK_SPIKE_SECRET", ""),
                help="shared secret for the Vercel diagnostic events endpoint",
            )
            subparser.add_argument("--limit", type=_positive_int, default=20)
        if command == "audit-web":
            subparser.add_argument("--host", default="127.0.0.1")
            subparser.add_argument("--port", type=_positive_int, default=8765)
            subparser.add_argument(
                "--reload",
                action="store_true",
                default=_env_bool("CEO_AUDIT_WEB_RELOAD", False),
                help="restart the audit web child process when local service source files change",
            )
            subparser.add_argument(
                "--reload-interval-seconds",
                type=_positive_int,
                default=_positive_int(os.getenv("CEO_AUDIT_WEB_RELOAD_INTERVAL_SECONDS", "1")),
            )
        if command == "service":
            subparser.add_argument("--host", default=os.getenv("CEO_AUDIT_WEB_HOST", "127.0.0.1"))
            subparser.add_argument(
                "--port",
                type=_positive_int,
                default=_positive_int(os.getenv("CEO_AUDIT_WEB_PORT", "8765")),
            )
            subparser.add_argument(
                "--producer-interval-seconds",
                type=_positive_int,
                default=producer_interval_seconds(),
            )
            subparser.add_argument(
                "--consumer-poll-interval-seconds",
                type=_positive_int,
                default=consumer_poll_interval_seconds(),
            )
            subparser.add_argument(
                "--consumer-workers",
                type=_positive_int,
                default=consumer_worker_count(),
                help="bounded in-process reply consumer threads; the same conversation remains session-locked",
            )
            subparser.add_argument(
                "--task-work-item-interval-seconds",
                type=_positive_int,
                default=task_work_item_interval_seconds(),
            )
            subparser.add_argument(
                "--task-daily-interval-seconds",
                type=_positive_int,
                default=task_daily_interval_seconds(),
            )
            subparser.add_argument(
                "--task-follow-up-interval-seconds",
                type=_positive_int,
                default=task_follow_up_interval_seconds(),
            )
        if command == "export-feedback":
            subparser.add_argument(
                "--output",
                default=os.getenv(
                    "CEO_FEEDBACK_EXPORT",
                    str(_default_data_dir() / "feedback.jsonl"),
                ),
            )
            subparser.add_argument("--limit", type=_positive_int)
        if command == "rerun-message":
            subparser.add_argument("--conversation-id", required=True)
            subparser.add_argument("--message-id", required=True)
            subparser.add_argument(
                "--oa-url",
                default="",
                help=(
                    "explicit DingTalk OA approval URL for rerunning approval "
                    "reminders that do not include an instance id"
                ),
            )
            subparser.add_argument(
                "--context-time",
                help=(
                    "anchor time for historical message lookup; accepts "
                    "YYYY-MM-DD HH:MM:SS or ISO datetime"
                ),
            )
            subparser.add_argument(
                "--force-new-decision",
                action="store_true",
                help="run Codex again even if this message already has an attempt",
            )
        if command == "send-attempt":
            subparser.add_argument("--attempt-id", type=int, required=True)
        if command == "weekly-okr-report":
            subparser.add_argument(
                "--force",
                action="store_true",
                help="run immediately even when today is not the scheduled Sunday",
            )
            subparser.add_argument(
                "--period-label",
                default="",
                help="override the current-quarter OKR period label",
            )
        if command == "refresh-okr-archive":
            subparser.add_argument(
                "--period-label",
                default="",
                help="override the current-quarter OKR period label",
            )
            subparser.add_argument(
                "--group-name",
                default="CEO-2 管理群",
                help="DingTalk group whose members define the company OKR archive roster",
            )
        if command == "resolve-agent-run":
            subparser.add_argument("--run-id", type=int, required=True)
            subparser.add_argument("--execution-generation", required=True)
            subparser.add_argument(
                "--resolution",
                required=True,
                choices=(
                    "confirmed_occurred",
                    "confirmed_not_occurred",
                    "terminate_unrecoverable",
                ),
            )
            subparser.add_argument("--reason", required=True)
            subparser.add_argument("--actor", required=True)
        if command == "build-work-profile":
            include_dingtalk_messages_default = not _env_bool(
                "CEO_PROFILE_SKIP_DINGTALK_MESSAGES", False
            )
            include_dingtalk_kb_default = not _env_bool(
                "CEO_PROFILE_SKIP_DINGTALK_KB", False
            )
            subparser.set_defaults(
                include_dingtalk_messages=include_dingtalk_messages_default,
                include_dingtalk_kb=include_dingtalk_kb_default,
            )
            subparser.add_argument(
                "--skip-minutes-corpus",
                action="store_true",
                default=_env_bool("CEO_PROFILE_SKIP_MINUTES_CORPUS", False),
                help="skip rebuilding local AI minutes corpus before profile generation",
            )
            subparser.add_argument(
                "--include-dingtalk-messages",
                dest="include_dingtalk_messages",
                action="store_true",
                help=(
                    "read recent messages sent by "
                    f"{principal_display_name()} through dws in read-only mode"
                ),
            )
            subparser.add_argument(
                "--skip-dingtalk-messages",
                dest="include_dingtalk_messages",
                action="store_false",
                help="skip DingTalk sent-message collection",
            )
            subparser.add_argument(
                "--dingtalk-message-target-count",
                type=_positive_int,
                default=_positive_int(
                    os.getenv("CEO_PROFILE_DINGTALK_MESSAGE_TARGET_COUNT", "1000")
                ),
                help="maximum DingTalk sent-message records to collect for profile evidence",
            )
            subparser.add_argument(
                "--include-dingtalk-kb",
                dest="include_dingtalk_kb",
                action="store_true",
                help="read online DingTalk knowledge base docs in read-only mode",
            )
            subparser.add_argument(
                "--skip-dingtalk-kb",
                dest="include_dingtalk_kb",
                action="store_false",
                help="skip online DingTalk knowledge base collection",
            )
            subparser.add_argument(
                "--dingtalk-kb-workspace",
                default=os.getenv("CEO_DINGTALK_KB_WORKSPACE", ""),
                help=(
                    "DingTalk knowledge base workspace id or URL for read-only "
                    "profile evidence"
                ),
            )

    # WeChat channel commands pass through to the self-contained app.wechat.cli
    # (status / read-recent / produce-once / consume-once). REMAINDER keeps its
    # own flags out of the shared DingTalk arg set.
    wechat_parser = subparsers.add_parser(
        "wechat",
        help="WeChat channel diagnostics (status/read-recent/produce-once/consume-once)",
    )
    wechat_parser.add_argument("wechat_args", nargs=argparse.REMAINDER)

    return parser


def settings_from_args(args: argparse.Namespace) -> WorkerSettings:
    return WorkerSettings(
        workspace=_expand_path_arg(args.workspace),
        db_path=_expand_path_arg(args.db),
        corpus_dir=_expand_path_arg(args.corpus_dir),
        dry_run=bool(args.dry_run),
        poll_interval_seconds=args.poll_interval_seconds,
        batch_seconds=args.batch_seconds,
        ding_robot_code=os.getenv("CEO_DING_ROBOT_CODE")
        or os.getenv("DINGTALK_DING_ROBOT_CODE"),
        ding_robot_name=os.getenv("CEO_DING_ROBOT_NAME", DEFAULT_DING_ROBOT_NAME),
        ding_receiver_user_id=os.getenv("CEO_DING_RECEIVER_USER_ID"),
        dws_transient_retry_attempts=args.dws_transient_retry_attempts,
        dws_transient_retry_delay_seconds=args.dws_transient_retry_delay_seconds,
        codex_timeout_seconds=args.codex_timeout_seconds,
        codex_idle_timeout_seconds=args.codex_idle_timeout_seconds,
        task_codex_timeout_seconds=args.task_codex_timeout_seconds,
        task_codex_idle_timeout_seconds=args.task_codex_idle_timeout_seconds,
        task_work_item_interval_seconds=getattr(
            args,
            "task_work_item_interval_seconds",
            WorkerSettings().task_work_item_interval_seconds,
        ),
        task_daily_interval_seconds=getattr(
            args,
            "task_daily_interval_seconds",
            WorkerSettings().task_daily_interval_seconds,
        ),
        task_follow_up_interval_seconds=getattr(
            args,
            "task_follow_up_interval_seconds",
            WorkerSettings().task_follow_up_interval_seconds,
        ),
        oa_pending_scan_enabled=args.oa_pending_scan_enabled,
        oa_pending_scan_interval_seconds=args.oa_pending_scan_interval_seconds,
        oa_pending_scan_lookback_days=args.oa_pending_scan_lookback_days,
        meeting_producer_interval_seconds=meeting_producer_interval_seconds(),
        meeting_consumer_poll_interval_seconds=meeting_consumer_poll_interval_seconds(),
        meeting_settle_seconds=meeting_settle_seconds(),
        consumer_workers=getattr(args, "consumer_workers", consumer_worker_count()),
        max_batches=args.max_batches,
        repository_upgrade_enabled=repository_upgrade_enabled(),
        repository_upgrade_remote=repository_upgrade_remote(),
        repository_upgrade_branch=repository_upgrade_branch(),
        repository_upgrade_check_interval_seconds=repository_upgrade_check_interval_seconds(),
    )


def _expand_path_arg(value: str | Path) -> Path:
    return Path(value).expanduser()


def create_worker(
    settings: WorkerSettings,
    *,
    runtime_refresher=None,
) -> DingTalkAutoReplyWorker:
    from app.okr_review import (
        DwsAgoalApiOkrSource,
        DwsLiveOkrSource,
        UnconfiguredOkrLiveSource,
    )

    # The CLI/service host owns the refresher. Workers receive only its bound
    # refresh operation so every concrete Agent turn can renew a stale health
    # snapshot before route selection.
    store = AutoReplyStore(settings.db_path)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    cached_dws = CachedDwsClient(dws=dws, org_directory=CachedOrgDirectory(store))
    from app.agent_runtime_production import build_production_agent_runtime

    agent_runtime = build_production_agent_runtime(
        store=store,
        workspace=settings.workspace,
        refresh_runtime_capabilities=(
            runtime_refresher.refresh_expired
            if runtime_refresher is not None
            else None
        ),
    )
    codex = CodexDecisionRunner(
        workspace=settings.workspace,
        store=store,
        timeout_seconds=settings.codex_timeout_seconds,
        idle_timeout_seconds=settings.codex_idle_timeout_seconds,
    )
    style_profile = _load_style_profile(settings.corpus_dir)
    style_records = load_corpus_records(settings.corpus_dir / "style_corpus.csv")
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=cached_dws,
        codex=codex,
        dry_run=settings.dry_run,
        style_profile=style_profile,
        style_records=style_records,
        agent_runtime=agent_runtime,
    )
    okr_source_kind = _okr_source_kind()
    if okr_source_kind == "agoal":
        worker.okr_live_source = DwsAgoalApiOkrSource(
            dws=dws,
            objective_rule_id=os.getenv(OKR_OBJECTIVE_RULE_ID_ENV, ""),
        )
    elif okr_source_kind == "dingteam_web":
        worker.okr_live_source = DwsLiveOkrSource(
            dws=dws,
            command_template=_okr_live_source_command_template(),
        )
    else:
        worker.okr_live_source = UnconfiguredOkrLiveSource(OKR_SOURCE_KIND_ENV)
    return worker


def _create_service_worker(settings: WorkerSettings, runtime_refresher):
    return create_worker(settings, runtime_refresher=runtime_refresher)

def _okr_source_kind() -> str:
    value = os.getenv(OKR_SOURCE_KIND_ENV, "dingteam_web").strip().casefold()
    if value not in {"dingteam_web", "agoal"}:
        raise ValueError(
            f"{OKR_SOURCE_KIND_ENV} must be dingteam_web or agoal, got {value!r}"
        )
    return value


def _okr_live_source_command_template() -> list[str]:
    value = os.getenv(OKR_LIVE_SOURCE_COMMAND_ENV, "").strip()
    if not value:
        return []
    return shlex.split(value)


def ensure_live_send_allowed(settings: WorkerSettings) -> None:
    if settings.dry_run:
        return
    if _env_bool(LIVE_SEND_GUARD_ENV, False):
        return

    blockers = "\n".join(f"- {blocker}" for blocker in LIVE_SEND_BLOCKERS)
    raise SystemExit(
        "CEO_NOT_SEND_MESSAGE=0 is blocked until unresolved live-send blockers are "
        f"explicitly accepted with {LIVE_SEND_GUARD_ENV}=1:\n{blockers}"
    )


def _excerpt(value: str | None, limit: int = 180) -> str:
    if not value:
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _run_once_summary(
    store: AutoReplyStore,
    *,
    after_attempt_id: int,
    after_sent_reply_id: int,
    after_error_id: int,
) -> dict[str, object]:
    attempts = store.list_reply_attempts_after(after_attempt_id)
    sent_replies = store.list_sent_replies_after(after_sent_reply_id)
    errors = store.list_errors_after(after_error_id)
    return {
        "agent_local_timezone": local_time_zone_name(),
        "counts": {
            "reply_attempts": len(attempts),
            "sent_replies": len(sent_replies),
            "errors": len(errors),
        },
        "reply_attempts": [
            {
                "id": attempt.id,
                "conversation_title": attempt.conversation_title,
                "trigger_sender": attempt.trigger_sender,
                "trigger_text_excerpt": _excerpt(attempt.trigger_text),
                "action": attempt.action,
                "send_status": attempt.send_status,
                "send_error_excerpt": _excerpt(attempt.send_error),
                "final_reply_text_excerpt": _excerpt(attempt.final_reply_text),
                "codex_session_id": attempt.codex_session_id,
            }
            for attempt in attempts
        ],
        "sent_replies": [
            {
                "id": sent_reply.id,
                "conversation_id": sent_reply.conversation_id,
                "trigger_message_id": sent_reply.trigger_message_id,
                "reply_text_excerpt": _excerpt(sent_reply.reply_text),
                "send_result_excerpt": _excerpt(sent_reply.send_result_json),
                "sent_at": sent_reply.sent_at,
            }
            for sent_reply in sent_replies
        ],
        "errors": [
            {
                "id": error.id,
                "conversation_id": error.conversation_id,
                "message_id": error.message_id,
                "kind": error.kind,
                "detail_excerpt": _excerpt(error.detail, limit=320),
                "created_at": error.created_at,
            }
            for error in errors
        ],
    }


def run_once(settings: WorkerSettings) -> None:
    store = AutoReplyStore(settings.db_path)
    after_attempt_id = store.max_reply_attempt_id()
    after_sent_reply_id = store.max_sent_reply_id()
    after_error_id = store.max_error_id()
    worker = create_worker(settings)
    worker.run_once(max_batches=settings.max_batches)
    summary = _run_once_summary(
        AutoReplyStore(settings.db_path),
        after_attempt_id=after_attempt_id,
        after_sent_reply_id=after_sent_reply_id,
        after_error_id=after_error_id,
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def produce_once(settings: WorkerSettings) -> int:
    try:
        queued = create_worker(settings).produce_once(max_tasks=settings.max_batches)
    except Exception as exc:
        _record_service_failure(settings, "producer", exc)
        raise
    print(f"produce-once queued={queued}", flush=True)
    return queued


def consume_once(settings: WorkerSettings) -> int:
    try:
        processed = create_worker(settings).consume_once(max_tasks=settings.max_batches)
    except Exception as exc:
        _record_service_failure(settings, "consumer", exc)
        raise
    print(f"consume-once processed={processed}", flush=True)
    return processed


def process_work_items_command(settings: WorkerSettings) -> int:
    from app.agent_runtime_production import (
        build_production_routed_codex_execution,
    )
    from app.task_agent import (
        TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS,
        TASK_AGENT_MAX_TIMEOUT_SECONDS,
    )

    store = AutoReplyStore(settings.db_path)
    limit = 20 if settings.max_batches is None else settings.max_batches
    if limit <= 0:
        print("process-work-items processed=0", flush=True)
        return 0
    store.recover_orphaned_task_agent_runs()
    store.recover_expired_terminal_task_runtime_attempts()
    store.reset_stale_processing_work_summary_inputs(
        _work_summary_processing_stale_seconds(settings)
    )
    routed_execution = build_production_routed_codex_execution(
        store=store,
        workspace=settings.workspace,
        total_timeout_seconds=min(
            settings.task_codex_timeout_seconds,
            TASK_AGENT_MAX_TIMEOUT_SECONDS,
        ),
        idle_timeout_seconds=min(
            settings.task_codex_idle_timeout_seconds,
            TASK_AGENT_MAX_IDLE_TIMEOUT_SECONDS,
        ),
    )
    runner = TaskAgentRunner(
        TaskAgentCodexRunner(routed_execution=routed_execution)
    )
    dws = None
    if not settings.dry_run:
        dws = DwsClient(
            ding_robot_code=settings.ding_robot_code,
            ding_robot_name=settings.ding_robot_name,
            ding_receiver_user_id=settings.ding_receiver_user_id,
            transient_retry_attempts=settings.dws_transient_retry_attempts,
            transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
        )
        dispatch_task_todo_sync_outbox(
            store,
            dws,
            owner="process-work-items-recovery",
            now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            limit=min(limit, 20),
        )
    processed = 0
    for _ in range(limit):
        if store.active_codex_capacity_pause(now=datetime.now(timezone.utc)):
            break
        claimed = store.claim_work_summary_inputs(limit=1)
        if not claimed:
            break
        work_input = claimed[0]
        capacity_recovery_active = store.codex_capacity_failure_count() > 0
        try:
            process_work_item(store, runner, work_input, dws=dws)
            store.clear_codex_capacity_pause()
            processed += 1
        except Exception as exc:
            raw_error = str(exc)
            error = _normalize_codex_stop_error_reason(raw_error)
            if capacity_recovery_active and error in RECOVERABLE_AGENT_RUNTIME_ERRORS:
                error = CODEX_PROVIDER_CAPACITY_EXHAUSTED
            capacity_exhausted = is_codex_capacity_exhausted(error)
            opened_capacity_pause = False
            if capacity_exhausted:
                now = datetime.now(timezone.utc)
                opened_capacity_pause = store.open_codex_capacity_pause(
                    retry_at=(
                        now
                        + codex_capacity_retry_duration(
                            store.codex_capacity_failure_count()
                        )
                    ).isoformat(),
                    now=now,
                )
            if capacity_exhausted:
                store.defer_work_summary_input_for_capacity(
                    work_input.id,
                    error,
                    available_at=_work_summary_retry_available_at(
                        work_input.attempts,
                        capacity_exhausted=True,
                        capacity_failure_count=max(
                            store.codex_capacity_failure_count() - 1,
                            0,
                        ),
                    ),
                )
            elif _should_retry_work_summary_input(exc, work_input.attempts):
                store.schedule_work_summary_input_retry(
                    work_input.id,
                    error,
                    available_at=_work_summary_retry_available_at(
                        work_input.attempts,
                    ),
                )
            elif _should_skip_work_summary_input(error):
                store.mark_work_summary_input_skipped(work_input.id, error)
            else:
                store.mark_work_summary_input_failed(work_input.id, error)
            if not capacity_exhausted or opened_capacity_pause:
                store.record_error(
                    "work_summary_input",
                    str(work_input.id),
                    "codex_capacity_pause" if capacity_exhausted else "task_agent",
                    CODEX_CAPACITY_EXHAUSTED_MESSAGE if capacity_exhausted else error,
                )
    print(f"process-work-items processed={processed}", flush=True)
    return processed


def retry_work_summary_input_command(
    settings: WorkerSettings,
    *,
    input_id: int,
) -> None:
    store = AutoReplyStore(settings.db_path)
    requeued = store.requeue_failed_work_summary_input(
        input_id,
        "retry_after_reviewed_root_cause_fix",
    )
    if not requeued:
        raise SystemExit(
            f"work summary input is not a retryable failed record: {input_id}"
        )
    print(f"work-summary-input requeued={input_id}", flush=True)


def _should_retry_work_summary_input(error: Exception | str, attempts: int) -> bool:
    error_text = str(error)
    normalized_error = _normalize_codex_stop_error_reason(error_text)
    if _is_terminal_codex_auth_failure(normalized_error):
        return False
    if attempts >= WORK_SUMMARY_TRANSIENT_RETRY_ATTEMPTS:
        return False
    if isinstance(error, Exception) and is_external_dependency_error(error):
        return True
    if _is_codex_provider_recovery_wait_reason(normalized_error):
        return True
    normalized = error_text.lower()
    return any(marker in normalized for marker in WORK_SUMMARY_TRANSIENT_ERROR_MARKERS)


def _should_skip_work_summary_input(error: str) -> bool:
    normalized = error.lower()
    return any(
        all(marker in normalized for marker in markers)
        for markers in WORK_SUMMARY_DISCARDABLE_ERROR_MARKERS
    )


def _work_summary_retry_available_at(
    attempts: int,
    *,
    capacity_exhausted: bool = False,
    capacity_failure_count: int = 0,
) -> str:
    if capacity_exhausted:
        return (
            datetime.now(timezone.utc)
            + codex_capacity_retry_duration(capacity_failure_count)
        ).strftime("%Y-%m-%d %H:%M:%S")
    delay_seconds = min(
        WORK_SUMMARY_RETRY_BASE_DELAY_SECONDS * (2 ** max(attempts - 1, 0)),
        WORK_SUMMARY_RETRY_MAX_DELAY_SECONDS,
    )
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def backfill_task_memory_context_command(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    limit = 20 if settings.max_batches is None else settings.max_batches
    runner = ProjectMemoryContextCodexRunner(
        workspace=settings.workspace,
        timeout_seconds=settings.codex_timeout_seconds,
        idle_timeout_seconds=settings.codex_idle_timeout_seconds,
        store=store,
    )
    updated = 0
    failed = 0
    for project in store.list_work_projects_missing_memory_context(limit=limit):
        try:
            context = ProjectMemoryContext.model_validate(
                runner.build(
                    project=project,
                    todos=store.list_work_todos(project_id=project.id),
                    updates=store.list_work_updates(project.id),
                )
            )
            validate_project_memory_context(
                context,
                getattr(runner, "last_audit_tool_events", None),
            )
            store.update_work_project_memory_context(
                project.id,
                json.dumps(
                    context.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            updated += 1
        except Exception as exc:
            failed += 1
            store.record_error(
                None,
                None,
                "task_memory_backfill",
                f"project_id={project.id}: {exc}",
            )
    print(
        f"backfill-task-memory-context updated={updated} failed={failed}",
        flush=True,
    )
    return updated


def _print_routine_process_backfill_result(
    result: RoutineProcessBackfillResult,
) -> None:
    print(
        "backfill-routine-process-todos "
        f"dry_run={result.dry_run} planned={result.planned} changed={result.changed}"
    )
    for item in result.items:
        status = "skip" if item.skipped_reason else "plan"
        print(
            f"- {status} todo_id={item.todo_id} project_id={item.project_id} "
            f"before={item.before_status} after={item.after_status} "
            f"follow_ups={item.suppressed_follow_up_ids} "
            f"dingtalk_links={item.dingtalk_link_ids} "
            f"reason={item.skipped_reason or item.reason} title={item.title}"
        )


def backfill_routine_process_todos_command(
    settings: WorkerSettings,
    *,
    todo_ids: list[int],
    reason: str,
    apply: bool = False,
    now: str = "",
) -> RoutineProcessBackfillResult:
    store = AutoReplyStore(settings.db_path)
    result = backfill_routine_process_todos(
        store,
        todo_ids=todo_ids,
        reason=reason,
        dry_run=not apply,
        now=now,
    )
    _print_routine_process_backfill_result(result)
    return result


def process_okr_reviews_command(settings: WorkerSettings) -> int:
    from app.agent_runtime_production import (
        build_production_routed_codex_execution,
    )
    from app.okr_review import process_okr_review_request
    from app.structured_agent import AgentSpec, StructuredCodexRunner

    store = AutoReplyStore(settings.db_path)
    recovered_requests = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=_okr_review_processing_stale_seconds(settings)
    )
    for request in recovered_requests:
        store.record_error(
            request.conversation_id,
            request.trigger_message_id,
            "okr_review_stale_requeue",
            (
                "requeued recoverable OKR review request: "
                f"request={request.id} "
                f"status={request.status} "
                f"conversation={request.conversation_title} "
                f"updated_at={request.updated_at} "
                f"error={request.error}"
            ),
        )
    spec = AgentSpec(
        name="okr_review",
        schema_path=_repo_root() / "app" / "schemas" / "agent_envelope.schema.json",
        primary_skill_paths=[
            Path.home() / ".agents" / "skills" / "dingtang-okr-review" / "SKILL.md"
        ],
        reply_visible_skill_paths=[],
        developer_preamble=(
            "You are the local CEO Agent OKR review runner. "
            "Return only AgentEnvelope JSON."
        ),
    )
    routed_execution = build_production_routed_codex_execution(
        store=store,
        workspace=settings.workspace,
        total_timeout_seconds=max(
            settings.codex_timeout_seconds, OKR_REVIEW_CODEX_TIMEOUT_SECONDS
        ),
        idle_timeout_seconds=max(
            settings.codex_idle_timeout_seconds,
            OKR_REVIEW_CODEX_IDLE_TIMEOUT_SECONDS,
        ),
    )
    runner = StructuredCodexRunner(
        routed_execution=routed_execution,
        spec=spec,
    )
    dws = None
    if not settings.dry_run:
        dws = DwsClient(
            ding_robot_code=settings.ding_robot_code,
            ding_robot_name=settings.ding_robot_name,
            ding_receiver_user_id=settings.ding_receiver_user_id,
            transient_retry_attempts=settings.dws_transient_retry_attempts,
            transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
        )
    processed = 0
    limit = 20 if settings.max_batches is None else settings.max_batches
    for request in store.claim_okr_review_requests(limit):
        try:
            conversation = DingTalkConversation(
                open_conversation_id=request.conversation_id,
                title=request.conversation_title,
                single_chat=_conversation_single_chat_for_okr_request(store, request),
                unread_point=0,
            )
            trigger = _trigger_message_for_okr_request(
                store=store,
                conversation=conversation,
                request=request,
            )
            reply = process_okr_review_request(
                store=store,
                runner=runner,
                request=request,
                single_chat=conversation.single_chat,
            )
        except Exception as exc:
            store.mark_okr_review_request_failed(request.id, str(exc))
            store.record_error(
                request.conversation_id,
                request.trigger_message_id,
                "okr_review_process",
                str(exc),
            )
            raise
        if settings.dry_run:
            processed += 1
            continue
        try:
            if dws is None:
                raise RuntimeError("DWS client is not configured for OKR review send")
            send_result = _send_reply_to_trigger_chunks(
                dws, conversation, trigger, reply
            )
        except Exception as exc:
            store.mark_okr_review_request_failed(request.id, str(exc))
            store.record_error(
                request.conversation_id,
                request.trigger_message_id,
                "okr_review_send",
                str(exc),
            )
            raise
        store.record_sent_reply(
            request.conversation_id,
            request.trigger_message_id,
            reply,
            send_result_json=json.dumps(
                native_reply_delivery_payload(conversation, trigger, send_result),
                ensure_ascii=False,
            ),
            recall_key=extract_recall_key_from_send_result(send_result),
        )
        processed += 1
    print(f"process-okr-reviews processed={processed}", flush=True)
    return processed


def _conversation_single_chat_for_okr_request(
    store: AutoReplyStore,
    request,
) -> bool:
    task = store.get_reply_task_for_message(
        request.conversation_id,
        request.trigger_message_id,
    )
    if task is not None:
        return task.single_chat
    record = store.get_conversation(request.conversation_id)
    if record is None:
        raise RuntimeError(
            f"conversation not found for OKR review request: {request.conversation_id}"
        )
    return record.single_chat


def _trigger_message_for_okr_request(
    *,
    store: AutoReplyStore,
    conversation: DingTalkConversation,
    request,
) -> DingTalkMessage:
    task = store.get_reply_task_for_message(
        request.conversation_id,
        request.trigger_message_id,
    )
    if task is None:
        raise RuntimeError(
            f"reply task not found for OKR review trigger: {request.trigger_message_id}"
        )
    raw_payload = json.loads(task.trigger_message_json)
    if not isinstance(raw_payload, dict):
        raise RuntimeError(
            f"invalid OKR review trigger payload: {request.trigger_message_id}"
        )
    trigger = _trigger_message_from_payload(raw_payload, conversation=conversation)
    if trigger.open_message_id != request.trigger_message_id:
        raise RuntimeError(
            f"OKR review trigger payload message mismatch: {request.trigger_message_id}"
        )
    if not trigger.sender_open_dingtalk_id:
        raise RuntimeError(
            f"OKR review trigger missing senderOpenDingTalkId: {request.trigger_message_id}"
        )
    return trigger


def scan_task_sources_command(
    settings: WorkerSettings,
    *,
    max_new_items: int | None = None,
) -> int:
    from app.task_scanners import scan_ai_minutes, scan_local_workspace_files

    store = AutoReplyStore(settings.db_path)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    local_count = scan_local_workspace_files(
        store,
        workspace=settings.workspace,
        max_new_items=max_new_items,
    )
    remaining_minutes_items = (
        None
        if max_new_items is None
        else max(0, max_new_items - local_count)
    )
    minutes_count = scan_ai_minutes(
        store,
        dws,
        max_new_items=remaining_minutes_items,
    )
    total = local_count + minutes_count
    print(
        "scan-task-sources "
        f"local_files={local_count} ai_minutes={minutes_count} total={total}",
        flush=True,
    )
    return total


def scan_oa_approvals_command(
    settings: WorkerSettings,
    *,
    max_new_items: int | None = None,
) -> int:
    from app.task_scanners import scan_pending_oa_approvals

    store = AutoReplyStore(settings.db_path)
    if not settings.oa_pending_scan_enabled:
        print("scan-oa-approvals disabled", flush=True)
        return 0
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    queued = scan_pending_oa_approvals(
        store,
        dws,
        lookback_days=settings.oa_pending_scan_lookback_days,
        max_new_items=max_new_items,
    )
    print(f"scan-oa-approvals queued={queued}", flush=True)
    return queued


def read_oa_approval_detail_command(
    settings: WorkerSettings,
    *,
    process_instance_id: str,
) -> dict[str, object]:
    """Read one OA process instance through the service-owned DingTalk adapter."""
    process_id = process_instance_id.strip()
    if not process_id:
        raise ValueError("OA process instance id is required")
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    payload = dws.read_oa_process_instance_openapi(process_id)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return payload


def process_follow_ups_command(
    settings: WorkerSettings,
    *,
    refresh_evidence: bool = True,
    limit: int = 50,
) -> int:
    from app.follow_up import process_due_follow_ups

    if refresh_evidence:
        scan_task_sources_command(settings, max_new_items=settings.max_batches)
        process_work_items_command(settings)

    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    sent = process_due_follow_ups(
        AutoReplyStore(settings.db_path),
        dws,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        auto_send=not settings.dry_run,
        feedback_base_url=feedback_spike_vercel_base_url(),
        limit=limit,
    )
    print(f"process-follow-ups sent={sent}", flush=True)
    return sent


def check_follow_up_completions_command(
    settings: WorkerSettings,
    *,
    limit: int = 1,
) -> int:
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    checked = enqueue_follow_up_completion_checks(
        AutoReplyStore(settings.db_path),
        dws,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        limit=limit,
    )
    print(f"check-follow-up-completions checked={checked}", flush=True)
    return checked


def daily_task_maintenance_command(settings: WorkerSettings) -> dict[str, int]:
    sources = scan_task_sources_command(settings)
    oa_approvals = scan_oa_approvals_command(settings)
    work_items = process_work_items_command(settings)
    okr_reviews = process_okr_reviews_command(settings)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    dingtalk_todos_closed = pull_dingtalk_todo_statuses(
        AutoReplyStore(settings.db_path),
        dws,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    dingtalk_todos_recovered = retry_failed_dingtalk_todo_links(
        AutoReplyStore(settings.db_path),
        dws,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    follow_up_completions_checked = check_follow_up_completions_command(
        settings,
        limit=1,
    )
    follow_ups = process_follow_ups_command(settings, refresh_evidence=False)
    result = {
        "sources": sources,
        "oa_approvals": oa_approvals,
        "work_items": work_items,
        "okr_reviews": okr_reviews,
        "dingtalk_todos_closed": dingtalk_todos_closed,
        "dingtalk_todos_recovered": dingtalk_todos_recovered,
        "follow_up_completions_checked": follow_up_completions_checked,
        "follow_ups": follow_ups,
    }
    print(
        "daily-task-maintenance "
        f"sources={sources} oa_approvals={oa_approvals} "
        f"work_items={work_items} "
        f"okr_reviews={okr_reviews} "
        f"dingtalk_todos_closed={dingtalk_todos_closed} "
        f"dingtalk_todos_recovered={dingtalk_todos_recovered} "
        f"follow_up_completions_checked={follow_up_completions_checked} "
        f"follow_ups={follow_ups}",
        flush=True,
    )
    return result


def setup_memory_connector_command(
    *,
    memory_url: str,
    codex_config: str,
    claude_config: str,
) -> dict[str, str]:
    from app.memory_setup import (
        claude_memory_connector_status,
        ensure_codex_memory_connector_config,
    )

    if not memory_url.strip():
        raise SystemExit(
            "setup-memory-connector requires --memory-url or MEMORY_CONNECTOR_URL"
        )

    url = memory_url.strip()
    codex_config_path = Path(codex_config).expanduser()
    claude_config_path = Path(claude_config).expanduser()
    codex_backup = ensure_codex_memory_connector_config(
        codex_config_path,
        url=url,
    )
    claude_status = claude_memory_connector_status(claude_config_path)
    result = {
        "codex_config": str(codex_config_path),
        "codex_backup": str(codex_backup),
        "claude_config": str(claude_config_path),
        "claude_status": claude_status["status"],
        "claude_manual_action": claude_status["manual_action"],
    }
    print(
        "setup-memory-connector "
        f"codex_config={result['codex_config']} "
        f"codex_backup={result['codex_backup']} "
        f"claude_config={result['claude_config']} "
        f"claude_status={result['claude_status']} "
        f"claude_manual_action={json.dumps(result['claude_manual_action'], ensure_ascii=False)}",
        flush=True,
    )
    return result


def doctor_mcp_command(
    settings: WorkerSettings,
    *,
    service_mcp_config: str = "",
    verify_live: bool = False,
    notify: bool = False,
) -> dict[str, object]:
    from app.mcp_doctor import mcp_doctor_report

    report = mcp_doctor_report(
        db_path=settings.db_path,
        service_config_path=(
            Path(service_mcp_config).expanduser()
            if service_mcp_config.strip()
            else None
        ),
        verify_live=verify_live,
        notify=notify,
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def channel_doctor_command() -> dict[str, object]:
    from app.channel_gate import default_channel_gates

    statuses = [
        gate.check().model_dump(mode="json")
        for gate in default_channel_gates().values()
    ]
    report: dict[str, object] = {"channels": statuses}
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def quality_check_command(
    settings: WorkerSettings,
    *,
    state_file: str | Path,
    verify_channels: bool = False,
) -> int:
    from app.quality_gate import (
        add_channel_health,
        required_live_channels,
        scan_hourly_quality,
        write_hourly_quality_state,
    )

    report = scan_hourly_quality(settings.db_path)
    if verify_channels:
        from app.channel_gate import default_channel_gates

        gates = default_channel_gates()
        channel_states = {
            name: gates[name].check().state.value
            for name in sorted(required_live_channels(settings.db_path))
            if name in gates
        }
        report = add_channel_health(report, channel_states)
    write_hourly_quality_state(report, state_file)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report.ok else 2


def _quality_required_channels(db_path: Path) -> set[str]:
    """Return channels whose readiness can block current recovery work.

    DingTalk is the primary control plane and is always checked. Optional
    channels are checked only while they have active work or a recent failed
    attempt that needs recovery. Channel Doctor still exposes all integrations.
    """
    import sqlite3

    channels = {"dingtalk"}
    with sqlite3.connect(str(db_path)) as db:
        rows = db.execute(
            """
            select distinct lower(channel) from reply_tasks
            where lower(status) in ('pending', 'processing') and trim(channel) != ''
            union
            select distinct lower(channel) from reply_attempts
            where lower(send_status) in ('failed', 'blocked')
              and datetime(updated_at) >= datetime('now', '-72 hours')
              and trim(channel) != ''
            """
        ).fetchall()
    channels.update(str(row[0]) for row in rows if row[0])
    return channels


def _record_service_failure(
    settings: WorkerSettings,
    component: str,
    exc: Exception,
) -> None:
    message = str(exc)
    AutoReplyStore(settings.db_path).record_error(None, None, component, message)
    send_macos_notification(
        title=f"CEO {component} failed",
        message=message[:120],
    )


def test_ding_command(settings: WorkerSettings) -> None:
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    try:
        dws.ding_self("CEO agent DING smoke test")
    except DwsError as exc:
        raise SystemExit(f"ding_self: BLOCKED {exc}") from exc
    print("ding_self: OK", flush=True)


def _context_time_to_epoch_ms(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        if "T" in normalized:
            parsed = datetime.fromisoformat(normalized)
        else:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise SystemExit(
            "invalid --context-time; expected YYYY-MM-DD HH:MM:SS or ISO datetime"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DINGTALK_MESSAGE_TIME_ZONE)
    return int(parsed.timestamp() * 1000)


def rerun_message_command(
    settings: WorkerSettings,
    conversation_id: str,
    message_id: str,
    *,
    force_new_decision: bool = False,
    context_time: str | None = None,
    oa_url: str = "",
) -> None:
    store = AutoReplyStore(settings.db_path)
    record = store.get_conversation(conversation_id)
    if record is None:
        raise SystemExit(f"conversation not found: {conversation_id}")
    worker = create_worker(settings)
    try:
        processed_message_id = worker.rerun_message(
            DingTalkConversation(
                open_conversation_id=record.conversation_id,
                title=record.title,
                single_chat=record.single_chat,
                unread_point=1,
                last_message_create_at=_context_time_to_epoch_ms(context_time),
            ),
            message_id,
            force_new_decision=force_new_decision,
            oa_url=oa_url,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"rerun-message processed conversation_id={conversation_id} "
        f"message_id={processed_message_id} force_new_decision={force_new_decision}",
        flush=True,
    )


def send_attempt_command(settings: WorkerSettings, attempt_id: int) -> dict[str, object]:
    store = AutoReplyStore(settings.db_path)
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        raise SystemExit(f"reply attempt not found: {attempt_id}")
    if attempt.send_status not in {"dry_run", "failed", "pending"}:
        raise SystemExit(
            f"reply attempt {attempt_id} is not an unsent attempt: "
            f"{attempt.send_status}"
        )
    task = store.get_reply_task_for_message(
        attempt.conversation_id,
        attempt.trigger_message_id,
        channel=attempt.channel,
    )
    conversation = store.get_conversation(attempt.conversation_id)
    if task is not None:
        trigger_message_json = task.trigger_message_json
        trigger_create_time = task.trigger_create_time
        conversation_title = task.conversation_title
        single_chat = task.single_chat
    else:
        if attempt.channel != "dingtalk" or conversation is None:
            raise SystemExit(
                f"original trigger is unavailable for reply attempt {attempt_id}"
            )
        trigger = DingTalkMessage(
            open_conversation_id=attempt.conversation_id,
            open_message_id=attempt.trigger_message_id,
            conversation_title=conversation.title,
            single_chat=conversation.single_chat,
            sender_name=attempt.trigger_sender,
            create_time=attempt.created_at,
            content=attempt.trigger_text,
        )
        trigger_message_json = trigger.model_dump_json()
        trigger_create_time = trigger.create_time
        conversation_title = conversation.title
        single_chat = conversation.single_chat
    queued_task = store.enqueue_manual_rerun_reply_task(
        conversation_id=attempt.conversation_id,
        conversation_title=conversation_title,
        single_chat=single_chat,
        trigger_message_id=attempt.trigger_message_id,
        trigger_create_time=trigger_create_time,
        trigger_sender=attempt.trigger_sender,
        trigger_text=attempt.trigger_text,
        trigger_message_json=trigger_message_json,
        oa_url=attempt.oa_url,
        attempt_id=attempt.id,
        channel=attempt.channel,
    )
    result = {
        "attempt_id": attempt.id,
        "conversation_title": attempt.conversation_title,
        "trigger_sender": attempt.trigger_sender,
        "trigger_text_excerpt": _excerpt(attempt.trigger_text),
        "send_status": "queued",
        "task_id": queued_task.id,
        "execution_generation": queued_task.execution_generation,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def resolve_agent_run_command(
    settings: WorkerSettings,
    *,
    run_id: int,
    execution_generation: str,
    resolution: str,
    reason: str,
    actor: str,
) -> dict[str, object]:
    resolved = AutoReplyStore(settings.db_path).resolve_agent_run_manually(
        run_id,
        expected_execution_generation=execution_generation,
        resolution=resolution,
        reason=reason,
        actor=actor,
    )
    result = {
        "run_id": resolved.run_id,
        "task_id": resolved.task_id,
        "attempt_id": resolved.attempt_id,
        "resolution": resolved.resolution,
        "execution_generation": resolved.execution_generation,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def _send_reply_to_trigger_chunks(dws, conversation, trigger, text: str) -> dict:
    chunks = split_dingtalk_text(text)
    if not chunks:
        raise RuntimeError("empty DingTalk reply text")
    return {
        "chunks": [
            {
                "index": index,
                "text": chunk,
                "send_result": dws.send_reply_to_trigger(conversation, trigger, chunk),
            }
            for index, chunk in enumerate(chunks, start=1)
        ]
    }


def _trigger_message_from_payload(
    payload: dict[str, object],
    *,
    conversation: DingTalkConversation,
) -> DingTalkMessage:
    raw_payload_value = payload.get("raw_payload")
    raw_payload = raw_payload_value if isinstance(raw_payload_value, dict) else {}

    def field(*names: str) -> object:
        for name in names:
            value = payload.get(name)
            if value not in (None, ""):
                return value
            value = raw_payload.get(name)
            if value not in (None, ""):
                return value
        return None

    quoted_message = field("quotedMessage", "quoted_message")
    quoted_payload = quoted_message if isinstance(quoted_message, dict) else {}
    return DingTalkMessage(
        open_conversation_id=str(
            field("openConversationId", "open_conversation_id")
            or conversation.open_conversation_id
        ),
        open_message_id=str(field("openMessageId", "open_message_id") or ""),
        conversation_title=conversation.title,
        single_chat=conversation.single_chat,
        sender_name=str(field("sender", "sender_name") or ""),
        sender_open_dingtalk_id=(
            str(field("senderOpenDingTalkId", "sender_open_dingtalk_id"))
            if field("senderOpenDingTalkId", "sender_open_dingtalk_id")
            else None
        ),
        sender_user_id=(
            str(field("senderUserId", "sender_user_id"))
            if field("senderUserId", "sender_user_id")
            else None
        ),
        message_type=str(field("messageType", "message_type") or ""),
        create_time=str(field("createTime", "create_time") or ""),
        content=str(field("content") or ""),
        mentioned_user_ids=[],
        quoted_message_id=(
            str(quoted_payload.get("openMessageId") or quoted_payload.get("open_message_id"))
            if quoted_payload.get("openMessageId") or quoted_payload.get("open_message_id")
            else None
        ),
        quoted_content=(
            str(quoted_payload.get("content"))
            if quoted_payload.get("content")
            else None
        ),
        raw_payload=payload,
    )


def _load_style_profile(corpus_dir: Path) -> str:
    path = corpus_dir / "style_profile.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def refresh_org_cache_command(settings: WorkerSettings, user_ids: set[str]) -> int:
    store = AutoReplyStore(settings.db_path)
    dws = DwsClient()
    count = refresh_org_cache(store=store, dws=dws, user_ids=user_ids)
    print(f"refresh-org-cache updated_profiles={count}", flush=True)
    return count


def record_feedback_command(
    settings: WorkerSettings,
    attempt_id: int,
    feedback: str,
    corrected_reply: str = "",
) -> None:
    store = AutoReplyStore(settings.db_path)
    updated = store.record_reply_feedback(
        attempt_id,
        feedback=feedback,
        corrected_reply_text=corrected_reply,
    )
    if not updated:
        raise SystemExit(f"reply attempt not found: {attempt_id}")
    print(f"feedback recorded attempt_id={attempt_id}", flush=True)


def feedback_spike_command(args: argparse.Namespace) -> dict[str, object]:
    if args.spike_action == "events-url":
        if not args.secret.strip():
            raise SystemExit("--secret or FEEDBACK_SPIKE_SECRET is required")
        url = build_events_url(
            args.vercel_base_url,
            secret=args.secret,
            limit=args.limit,
        )
        result = {"events_url": url}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    targets = {
        "--conversation-id": args.conversation_id.strip(),
        "--user-id": args.user_id.strip(),
        "--open-dingtalk-id": args.open_dingtalk_id.strip(),
    }
    selected_targets = [flag for flag, value in targets.items() if value]
    if len(selected_targets) != 1:
        raise SystemExit(
            "exactly one of --conversation-id, --user-id, --open-dingtalk-id "
            "is required for feedback-spike send-links"
        )
    result = send_feedback_spike_links(
        vercel_base_url=args.vercel_base_url,
        reply_text=args.reply_text,
        original_text=args.original_text,
        attempt_id=args.attempt_id,
        conversation_id=args.conversation_id.strip() or None,
        user_id=args.user_id.strip() or None,
        open_dingtalk_id=args.open_dingtalk_id.strip() or None,
        dws_bin=args.dws_bin,
        preview=args.preview,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def run_audit_web_command(
    settings: WorkerSettings,
    host: str,
    port: int,
    reload: bool = False,
    reload_interval_seconds: int = 1,
) -> None:
    audit_web_runner = run_audit_web
    if audit_web_runner is None:
        from app.audit_web import run_audit_web as audit_web_runner

    audit_web_runner(
        settings.db_path,
        host=host,
        port=port,
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        reload=reload,
        reload_delay_seconds=reload_interval_seconds,
        reload_dirs=[Path(__file__).resolve().parent],
    )


def export_feedback_command(
    settings: WorkerSettings, output: Path, limit: int | None = None
) -> int:
    store = AutoReplyStore(settings.db_path)
    attempts = store.list_reviewed_reply_attempts(limit=limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for attempt in attempts:
            payload = {
                "attempt_id": attempt.id,
                "conversation_id": attempt.conversation_id,
                "conversation_title": attempt.conversation_title,
                "trigger_message_id": attempt.trigger_message_id,
                "trigger_sender": attempt.trigger_sender,
                "trigger_text": attempt.trigger_text,
                "action": attempt.action,
                "sensitivity_kind": attempt.sensitivity_kind,
                "codex_reason": attempt.codex_reason,
                "draft_reply_text": attempt.draft_reply_text,
                "audit_documents_json": attempt.audit_documents_json,
                "audit_tool_events_json": attempt.audit_tool_events_json,
                "audit_summary": attempt.audit_summary,
                "final_reply_text": attempt.final_reply_text,
                "permission_action": attempt.permission_action,
                "permission_reason": attempt.permission_reason,
                "send_status": attempt.send_status,
                "send_error": attempt.send_error,
                "reviewer_feedback": attempt.reviewer_feedback,
                "corrected_reply_text": attempt.corrected_reply_text,
                "reviewed_at": attempt.reviewed_at,
                "created_at": attempt.created_at,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"feedback exported count={len(attempts)} output={output}", flush=True)
    return len(attempts)


def reset_codex_sessions_command(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    cleared = store.reset_codex_sessions()
    print(f"reset-codex-sessions cleared={cleared}", flush=True)
    return cleared


def _parse_macos_wifi_device(output: str) -> str:
    current_port = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Hardware Port:"):
            current_port = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Device:") and current_port in {"Wi-Fi", "AirPort"}:
            return line.split(":", 1)[1].strip()
    return ""


def _macos_wifi_connected(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if sys.platform != "darwin":
        return True
    try:
        ports = run(
            ["/usr/sbin/networksetup", "-listallhardwareports"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if ports.returncode != 0:
        return True
    device = _parse_macos_wifi_device(ports.stdout)
    if not device:
        return True
    if _macos_interface_has_default_reachable_network(device, run):
        return True
    try:
        status = run(
            ["/usr/sbin/networksetup", "-getairportnetwork", device],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if status.returncode != 0:
        return False
    output = status.stdout.strip()
    normalized = output.casefold()
    if any(
        marker in normalized
        for marker in (
            "not associated",
            "not connected",
            "wi-fi power is currently off",
            "airport power is off",
        )
    ):
        return False
    prefix = "current wi-fi network:"
    if normalized.startswith(prefix):
        return bool(output.split(":", 1)[1].strip())
    return True


def _macos_interface_has_default_reachable_network(
    device: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    try:
        route = run(
            ["/sbin/route", "-n", "get", "default"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        network = run(
            ["/usr/sbin/scutil", "--nwi"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if route.returncode != 0 or network.returncode != 0:
        return False
    route_interface = ""
    for raw_line in route.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("interface:"):
            route_interface = line.split(":", 1)[1].strip()
            break
    if route_interface != device:
        return False
    in_interface_block = False
    for raw_line in network.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith(f"{device} :"):
            in_interface_block = True
            if "Reachable" in line:
                return True
            continue
        if not in_interface_block:
            continue
        if "Reachable" in line:
            return True
        if line and not raw_line.startswith((" ", "\t")):
            in_interface_block = False
    return False


def _is_dws_transient_dependency_error(exc: Exception) -> bool:
    if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
        return True
    if not isinstance(exc, DwsError):
        return False
    if exc.code in (
        DwsClient.RETRYABLE_ERROR_CODES
        | DwsClient.MESSAGE_LIST_RETRYABLE_ERROR_CODES
        | DwsClient.TOKEN_VERIFIED_RETRYABLE_ERROR_CODES
    ):
        return True
    normalized = str(exc).casefold()
    return any(
        marker in normalized
        for marker in (
            "check network, proxy, and dns settings",
            "mcp service is reachable",
            "network_error",
            "timeout_error",
            "command timed out after",
            "exit code -9",
            "exit code -15",
        )
    )


class NetworkDependencyGate:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wifi_connected: Callable[[], bool] = _macos_wifi_connected,
        check_interval_seconds: int = 30,
    ):
        self.monotonic = monotonic
        self.wifi_connected = wifi_connected
        self.check_interval_seconds = check_interval_seconds
        self._last_checked_at: float | None = None
        self._last_ready = True

    def ready(self) -> bool:
        now = self.monotonic()
        if (
            self._last_checked_at is not None
            and now - self._last_checked_at < self.check_interval_seconds
        ):
            return self._last_ready
        self._last_checked_at = now
        self._last_ready = self.wifi_connected()
        return self._last_ready


def run_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_batches: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
    runtime_refresher=None,
) -> None:
    while True:
        if not network_ready():
            sleep(poll_interval_seconds)
            continue
        if runtime_refresher is not None:
            try:
                runtime_refresher.refresh_expired()
            except Exception:  # noqa: BLE001 - keep the sole long-lived owner alive
                sleep(poll_interval_seconds)
                continue
        worker.run_once(max_batches=max_batches)
        sleep(poll_interval_seconds)


def run_producer_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_tasks: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    while True:
        if not network_ready():
            sleep(poll_interval_seconds)
            continue
        try:
            worker.produce_once(max_tasks=max_tasks)
        except Exception as exc:
            worker.store.record_error("", "", "producer_loop_error", str(exc))
        sleep(poll_interval_seconds)


def run_consumer_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_tasks: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
    runtime_refresher=None,
) -> None:
    while True:
        if not network_ready():
            sleep(poll_interval_seconds)
            continue
        if runtime_refresher is not None:
            try:
                runtime_refresher.refresh_expired()
            except Exception:  # noqa: BLE001 - keep the sole long-lived owner alive
                # Route refresh is independent from local task recovery.  A
                # failed refresh must not prevent consume_once() from closing
                # stale ownership and requeueing resumable work.
                pass
        try:
            worker.consume_once(max_tasks=max_tasks)
        except Exception as exc:
            worker.store.record_error("", "", "consumer_loop_error", str(exc))
        sleep(poll_interval_seconds)


def run_database_backup_loop(
    db_path: Path,
    *,
    sleep: Callable[[int], None] = time.sleep,
) -> None:
    while True:
        backup_database_if_due(db_path)
        sleep(BACKUP_CHECK_INTERVAL_SECONDS)


def run_runtime_probe_loop(
    runtime_refresher,
    interval_seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Refresh expired route snapshots on one shared service cadence."""

    while True:
        sleep(interval_seconds)
        try:
            runtime_refresher.refresh_expired()
        except Exception:  # noqa: BLE001, S112 - persistent health loop
            # Probe failures are route health, never a reason to kill service
            # threads. The next interval retries through the same refresher.
            continue


def _create_meeting_dws(settings: WorkerSettings) -> DwsClient:
    return DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )


def run_meeting_producer_loop(
    settings: WorkerSettings,
    poll_interval_seconds: int,
    settle_seconds: int,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    store = AutoReplyStore(settings.db_path)
    dws = _create_meeting_dws(settings)
    while True:
        if not network_ready():
            sleep(poll_interval_seconds)
            continue
        try:
            produce_meeting_alignment_jobs(
                store,
                dws,
                now=datetime.now().astimezone(),
                settle_seconds=settle_seconds,
            )
        except Exception as exc:
            if not _is_dws_transient_dependency_error(exc):
                store.record_error(
                    "",
                    "",
                    "meeting_alignment_producer",
                    str(exc),
                )
        sleep(poll_interval_seconds)


def run_meeting_consumer_loop(
    settings: WorkerSettings,
    poll_interval_seconds: int,
    max_tasks: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    from app.agent_runtime_production import (
        build_production_routed_codex_execution,
    )

    store = AutoReplyStore(settings.db_path)
    dws = _create_meeting_dws(settings)
    routed_execution = build_production_routed_codex_execution(
        store=store,
        workspace=settings.workspace,
        total_timeout_seconds=settings.codex_timeout_seconds,
        idle_timeout_seconds=settings.codex_idle_timeout_seconds,
    )
    runner = MeetingAlignmentCodexRunner(
        routed_execution=routed_execution,
    )
    embedding_client = (
        EmbeddingClient(
            base_url=embedding_base_url(),
            model=embedding_model(),
            api_key=embedding_api_key(),
            timeout_seconds=embedding_timeout_seconds(),
        )
        if embedding_enabled()
        else None
    )
    consecutive_sqlite_lock_failures = 0
    while True:
        if not network_ready():
            sleep(poll_interval_seconds)
            continue
        try:
            consume_meeting_alignment_jobs(
                store,
                dws,
                runner,
                now=datetime.now().astimezone(),
                limit=1 if max_tasks is None else max_tasks,
                deliver=not settings.dry_run,
                embedding_client=embedding_client,
            )
            consecutive_sqlite_lock_failures = 0
        except Exception as exc:
            if isinstance(exc, sqlite3.OperationalError) and (
                "database is locked" in str(exc).casefold()
                or "database is busy" in str(exc).casefold()
            ):
                consecutive_sqlite_lock_failures += 1
                if consecutive_sqlite_lock_failures >= 3:
                    store.record_error(
                        "", "", "meeting_alignment_consumer", str(exc)
                    )
            elif not _is_dws_transient_dependency_error(exc):
                consecutive_sqlite_lock_failures = 0
                store.record_error(
                    "",
                    "",
                    "meeting_alignment_consumer",
                    str(exc),
                )
        sleep(poll_interval_seconds)


def replay_recent_meetings_command(
    settings: WorkerSettings,
    *,
    limit: int,
    offset: int = 0,
) -> list[dict[str, object]]:
    results = queue_recent_meeting_alignment_replay(
        AutoReplyStore(settings.db_path),
        _create_meeting_dws(settings),
        now=datetime.now().astimezone(),
        limit=limit,
        offset=offset,
        settle_seconds=settings.meeting_settle_seconds,
    )
    print(json.dumps(results, ensure_ascii=False), flush=True)
    return results


def run_task_maintenance_loop(
    settings: WorkerSettings,
    *,
    work_item_interval_seconds: int,
    daily_interval_seconds: int,
    sleep: Callable[[int], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    store = AutoReplyStore(settings.db_path)

    def run_step(kind: str, step: Callable[[], object]) -> None:
        try:
            step()
        except Exception as exc:
            store.record_error("", "", f"task_maintenance_{kind}", str(exc))

    now = monotonic()
    next_daily_run = now
    while True:
        if not network_ready():
            sleep(work_item_interval_seconds)
            continue
        run_step("process_work_items", lambda: process_work_items_command(settings))
        run_step("process_okr_reviews", lambda: process_okr_reviews_command(settings))
        run_step(
            "resolve_recovered_errors",
            lambda: (
                store.resolve_errors_recovered_by_reply_attempts()
                + store.resolve_errors_recovered_by_completed_reply_tasks()
                + store.resolve_errors_recovered_by_terminal_work_summary_inputs()
                + store.resolve_closed_blocked_reply_attempts()
                + store.resolve_unattributed_errors_after_quiet_period()
                + store.resolve_inactive_trigger_errors_after_quiet_period()
            ),
        )
        weekly_hour = int(
            os.getenv("CEO_WEEKLY_OKR_REPORT_HOUR", str(DEFAULT_SCHEDULE_HOUR))
        )
        if weekly_okr_report_window_open(
            wall_clock(),
            schedule_hour=weekly_hour,
        ):
            run_step(
                "weekly_okr_report",
                lambda: weekly_okr_report_command(settings, quiet_not_due=True),
            )
        now = monotonic()
        if now >= next_daily_run:
            run_step(
                "scan_task_sources",
                lambda: scan_task_sources_command(
                    settings,
                    max_new_items=settings.max_batches,
                ),
            )
            run_step("process_work_items", lambda: process_work_items_command(settings))
            run_step("process_okr_reviews", lambda: process_okr_reviews_command(settings))
            run_step(
                "check_follow_up_completions",
                lambda: check_follow_up_completions_command(settings, limit=1),
            )
            next_daily_run = now + daily_interval_seconds
        sleep(work_item_interval_seconds)


def run_follow_up_delivery_loop(
    settings: WorkerSettings,
    interval_seconds: int,
    *,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    store = AutoReplyStore(settings.db_path)
    consecutive_sqlite_lock_failures = 0
    while True:
        if network_ready():
            try:
                # max_batches bounds agent task discovery, not durable scheduled delivery.
                process_follow_ups_command(
                    settings,
                    refresh_evidence=False,
                    limit=50,
                )
                consecutive_sqlite_lock_failures = 0
            except Exception as exc:
                if isinstance(exc, sqlite3.OperationalError) and (
                    "database is locked" in str(exc).casefold()
                    or "database is busy" in str(exc).casefold()
                ):
                    consecutive_sqlite_lock_failures += 1
                    if consecutive_sqlite_lock_failures >= 3:
                        store.record_error("", "", "follow_up_delivery", str(exc))
                else:
                    consecutive_sqlite_lock_failures = 0
                    store.record_error("", "", "follow_up_delivery", str(exc))
        sleep(interval_seconds)


def run_oa_pending_scan_loop(
    settings: WorkerSettings,
    interval_seconds: int,
    *,
    max_new_items: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
    network_ready: Callable[[], bool] = _macos_wifi_connected,
) -> None:
    store = AutoReplyStore(settings.db_path)
    while True:
        if network_ready():
            try:
                scan_oa_approvals_command(settings, max_new_items=max_new_items)
            except Exception as exc:
                store.record_error("", "", "oa_pending_scan", str(exc))
        sleep(interval_seconds)


def _wechat_service_components(settings: WorkerSettings) -> tuple:
    """WeChat components when enabled; workers wait for a ready account.

    The account capability is written asynchronously by the dedicated Reader
    app, so gating component creation on a startup snapshot would lose the
    channel permanently after a launchd race. Disabled by default; auto-send
    remains separately gated.
    """
    from app import config as _cfg

    if not _cfg.wechat_reader_enabled():
        return ()
    from app.wechat import service as _wx

    components = [
        ("wechat-producer", lambda: _run_wechat_loop(settings, "producer")),
        ("wechat-consumer", lambda: _run_wechat_loop(settings, "consumer")),
    ]
    # The sender loop only auto-sends in 'auto' mode; in 'confirm' mode (default)
    # it holds ready_to_send deliveries for explicit approval. Only start it when
    # sending is enabled at all.
    if _cfg.wechat_sender_enabled():
        components.append(("wechat-sender", lambda: _run_wechat_loop(settings, "sender")))
    return tuple(components)


def _run_wechat_loop(settings: WorkerSettings, role: str) -> None:
    import time

    from app import config as _cfg
    from app.wechat import service as _wx
    from app.wechat.consumer import WechatTaskProcessingError
    from app.wechat.reader_ipc import ReaderIpcError

    store = AutoReplyStore(settings.db_path)
    state = None
    # Account capability is persisted by the dedicated Reader app and may
    # become ready just after launchd starts the main service. Keep the worker
    # alive and retry at the normal low-frequency cadence instead of selecting
    # an empty component set permanently at startup.
    while state is None:
        state = _wx.ready_account_state(store)
        if state is None:
            time.sleep(max(1, _cfg.wechat_poll_interval_seconds()))
    account = _wx.account_from_state(state)
    reader = _wx.build_reader()
    runner = None
    if role == "consumer":
        from app.wechat.decision_runner import WechatDecisionRunner

        runner = WechatDecisionRunner(
            workspace=settings.workspace,
            store=store,
            timeout_seconds=settings.codex_timeout_seconds,
            idle_timeout_seconds=settings.codex_idle_timeout_seconds,
        )
    wsender = None
    if role == "sender":
        from app.wechat.accessibility import WechatSender
        wsender = WechatSender(store, _wx.build_sender())
    configured_interval = _cfg.wechat_poll_interval_seconds()
    # Keep an optional/malformed provider value from reaching the loop's
    # integer boundary.  The config default is 15 seconds, so this preserves
    # the existing cadence when a launchd/.env value is absent or invalid.
    interval = (
        configured_interval
        if (
            isinstance(configured_interval, int)
            and not isinstance(configured_interval, bool)
            and configured_interval > 0
        )
        else 15
    )
    consecutive_sqlite_lock_failures = 0
    consecutive_reader_failures = 0
    reader_failure_reported = False
    while True:
        try:
            if role == "producer":
                _wx.run_produce_once(store, reader, account, self_user_id=account.self_user_id)
            elif role == "consumer":
                _wx.run_consume_once(store, runner, reader, account)
            else:  # sender: auto-sends only in 'auto' mode, else holds for approval
                _wx.process_ready_wechat_deliveries(
                    store, wsender,
                    mode=_cfg.wechat_send_mode(),
                    sender_enabled=_cfg.wechat_sender_enabled(),
                    reader=reader,
                    account=account,
                )
            consecutive_sqlite_lock_failures = 0
            consecutive_reader_failures = 0
            reader_failure_reported = False
        except Exception as exc:  # keep the loop alive; surface via error log
            if isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}:
                store.record_error(
                    "wechat",
                    "",
                    "wechat_data_permission_required",
                    "WeChat data access was denied; reader paused until service restart.",
                )
                _pause_wechat_loop_until_service_restart(time.sleep)
            elif isinstance(exc, ReaderIpcError):
                if exc.code == "permission_required":
                    store.record_error(
                        "wechat",
                        "",
                        "wechat_data_permission_required",
                        "CEO WeChat Reader App Data permission is required; "
                        f"{role} paused until service restart.",
                    )
                    _pause_wechat_loop_until_service_restart(time.sleep)
                else:
                    consecutive_reader_failures += 1
                    if consecutive_reader_failures >= 3 and not reader_failure_reported:
                        store.record_error(
                            "wechat",
                            "",
                            "wechat_reader_unavailable",
                            f"WeChat reader unavailable; {role} retrying automatically: {exc}",
                        )
                        reader_failure_reported = True
            elif isinstance(exc, WechatTaskProcessingError):
                store.record_error(
                    exc.conversation_id,
                    exc.trigger_message_id,
                    f"wechat_{role}_loop_error",
                    str(exc),
                )
            elif isinstance(exc, sqlite3.OperationalError) and (
                "database is locked" in str(exc).casefold()
                or "database is busy" in str(exc).casefold()
            ):
                consecutive_sqlite_lock_failures += 1
                if consecutive_sqlite_lock_failures >= 3:
                    store.record_error(
                        "wechat", "", f"wechat_{role}_loop_error", str(exc)
                    )
            else:
                consecutive_sqlite_lock_failures = 0
                store.record_error("wechat", "", f"wechat_{role}_loop_error", str(exc))
        time.sleep(interval)


def _pause_wechat_loop_until_service_restart(sleep: Callable[[float], None]) -> None:
    while True:
        sleep(3600)


def run_service(
    settings: WorkerSettings,
    *,
    host: str,
    port: int,
    producer_interval_seconds: int,
    consumer_poll_interval_seconds: int,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
    wait: Callable[[], None] | None = None,
    exit_process: Callable[[int], None] = os._exit,
    runtime_refresher=None,
) -> None:
    if runtime_refresher is not None:
        try:
            runtime_refresher.refresh_expired(force=True)
        except Exception:  # noqa: BLE001 - startup must degrade, not abort service
            AutoReplyStore(settings.db_path).record_error(
                "",
                "",
                "agent_runtime_probe_startup_failed",
                "Agent runtime startup probe failed; routes remain unavailable.",
            )
    _initialize_meeting_discovery_on_service_start(settings)
    _recover_orphaned_reply_tasks_on_service_start(settings)
    _recover_processing_work_summary_inputs_on_service_start(settings)
    _normalize_user_rejected_wechat_deliveries_on_service_start(settings)
    _recover_okr_review_requests_on_service_start(settings)
    _recover_meeting_alignment_jobs_on_service_start(settings)
    _resolve_recovered_errors_on_service_start(settings)
    doctor_mcp_command(
        settings,
        service_mcp_config=os.getenv("CEO_SERVICE_MCP_CONFIG_PATH", ""),
        notify=True,
    )
    dependency_gate = NetworkDependencyGate()
    components = (
        (
            "database-backup",
            lambda: run_database_backup_loop(settings.db_path),
        ),
        (
            "producer",
            lambda: run_producer_loop(
                _create_service_worker(settings, runtime_refresher),
                producer_interval_seconds,
                max_tasks=settings.max_batches,
                network_ready=dependency_gate.ready,
            ),
        ),
        (
            "meeting-producer",
            lambda: run_meeting_producer_loop(
                settings,
                settings.meeting_producer_interval_seconds,
                settings.meeting_settle_seconds,
                network_ready=dependency_gate.ready,
            ),
        ),
        (
            "meeting-consumer",
            lambda: run_meeting_consumer_loop(
                settings,
                settings.meeting_consumer_poll_interval_seconds,
                max_tasks=settings.max_batches,
                network_ready=dependency_gate.ready,
            ),
        ),
        (
            "task-maintenance",
            lambda: run_task_maintenance_loop(
                settings,
                work_item_interval_seconds=settings.task_work_item_interval_seconds,
                daily_interval_seconds=settings.task_daily_interval_seconds,
                network_ready=dependency_gate.ready,
            ),
        ),
        (
            "follow-up-delivery",
            lambda: run_follow_up_delivery_loop(
                settings,
                settings.task_follow_up_interval_seconds,
                network_ready=dependency_gate.ready,
            ),
        ),
    )
    consumer_components = tuple(
        (
            f"consumer-{index + 1}",
            lambda: run_consumer_loop(
                _create_service_worker(settings, runtime_refresher),
                consumer_poll_interval_seconds,
                max_tasks=settings.max_batches,
                network_ready=dependency_gate.ready,
            ),
        )
        for index in range(settings.consumer_workers)
    )
    components = components[:2] + consumer_components + components[2:]
    if runtime_refresher is not None:
        components = (
            (
                "runtime-probe",
                lambda: run_runtime_probe_loop(
                    runtime_refresher,
                    runtime_refresher.interval_seconds,
                ),
            ),
            *components,
        )
    if settings.oa_pending_scan_enabled:
        components += (
            (
                "oa-pending-scan",
                lambda: run_oa_pending_scan_loop(
                    settings,
                    settings.oa_pending_scan_interval_seconds,
                    max_new_items=settings.max_batches,
                    network_ready=dependency_gate.ready,
                ),
            ),
        )
    components = components + _wechat_service_components(settings)
    if settings.repository_upgrade_enabled:
        from app.repository_upgrade import GitRepository, RepositoryUpgradeService
        from app.repository_upgrade_scheduler import run_repository_upgrade_check_loop

        components += (
            (
                "repository-upgrade-check",
                lambda: run_repository_upgrade_check_loop(
                    service_factory=lambda: RepositoryUpgradeService(
                        repository=GitRepository(_repo_root()),
                        store=AutoReplyStore(settings.db_path),
                        remote=settings.repository_upgrade_remote,
                        branch=settings.repository_upgrade_branch,
                    ),
                    interval_seconds=settings.repository_upgrade_check_interval_seconds,
                ),
            ),
        )
    for component, target in components:
        thread = thread_factory(
            target=_service_component_target(
                settings=settings,
                component=component,
                target=target,
                exit_process=exit_process,
            ),
            name=f"ceo-agent-service-{component}",
            daemon=True,
        )
        thread.start()
    if wait is None:
        wait_event = threading.Event()
        wait_event.wait()
        return
    wait()


def _initialize_meeting_discovery_on_service_start(
    settings: WorkerSettings,
    *,
    now: datetime | None = None,
) -> str:
    store = AutoReplyStore(settings.db_path)
    existing = store.get_service_state(MEETING_DISCOVERY_ACTIVATED_AT_STATE_KEY)
    if existing:
        store.baseline_meeting_alignment_jobs_before(existing)
        return existing
    activated_at = (now or datetime.now().astimezone()).isoformat()
    store.set_service_state(
        MEETING_DISCOVERY_ACTIVATED_AT_STATE_KEY,
        activated_at,
    )
    store.baseline_meeting_alignment_jobs_before(activated_at)
    return activated_at


def _recover_meeting_alignment_jobs_on_service_start(
    settings: WorkerSettings,
) -> int:
    return recover_meeting_alignment_jobs(AutoReplyStore(settings.db_path))


def _recover_processing_work_summary_inputs_on_service_start(
    settings: WorkerSettings,
) -> int:
    store = AutoReplyStore(settings.db_path)
    recovered_runs = store.recover_orphaned_task_agent_runs()
    recovered_runtime_attempts = store.recover_stale_runtime_attempts(
        stale_after_seconds=_work_summary_processing_stale_seconds(settings),
    )
    recovered_attempts = store.recover_expired_terminal_task_runtime_attempts()
    recovered_inputs = store.reset_processing_work_summary_inputs()
    return (
        recovered_runs
        + recovered_runtime_attempts
        + recovered_attempts
        + len(recovered_inputs)
    )


def _normalize_user_rejected_wechat_deliveries_on_service_start(
    settings: WorkerSettings,
) -> int:
    return AutoReplyStore(settings.db_path).normalize_user_rejected_wechat_deliveries()


def _resolve_recovered_errors_on_service_start(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    return (
        store.resolve_errors_recovered_by_reply_attempts()
        + store.resolve_errors_recovered_by_completed_reply_tasks()
        + store.resolve_errors_recovered_by_terminal_work_summary_inputs()
        + store.resolve_closed_blocked_reply_attempts()
        + store.resolve_unattributed_errors_after_quiet_period()
        + store.resolve_inactive_trigger_errors_after_quiet_period()
    )


def _recover_orphaned_reply_tasks_on_service_start(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    recovered_tasks = (
        store.recover_orphaned_processing_reply_tasks()
        + store.recover_no_effect_agent_runs_after_service_restart()
        + store.retry_failed_service_restart_tasks()
        + store.recover_effectful_audit_runs_after_service_restart()
        + store.resume_completed_agent_turns_after_service_restart()
    )
    return len(recovered_tasks)


def _recover_okr_review_requests_on_service_start(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    recovered_requests = store.reset_recoverable_okr_review_requests()
    for request in recovered_requests:
        store.record_error(
            request.conversation_id,
            request.trigger_message_id,
            "okr_review_service_startup_requeue",
            (
                "requeued recoverable OKR review request on service startup: "
                f"request={request.id} "
                f"status={request.status} "
                f"conversation={request.conversation_title} "
                f"updated_at={request.updated_at} "
                f"error={request.error}"
            ),
        )
    return len(recovered_requests)


def _work_summary_processing_stale_seconds(settings: WorkerSettings) -> int:
    return (
        max(
            int(settings.task_codex_timeout_seconds),
            int(settings.task_codex_idle_timeout_seconds),
        )
        + WORK_SUMMARY_INPUT_STALE_GRACE_SECONDS
    )


def _okr_review_processing_stale_seconds(settings: WorkerSettings) -> int:
    return (
        max(
            int(settings.codex_timeout_seconds),
            int(settings.codex_idle_timeout_seconds),
            OKR_REVIEW_CODEX_TIMEOUT_SECONDS,
            OKR_REVIEW_CODEX_IDLE_TIMEOUT_SECONDS,
        )
        + WORK_SUMMARY_INPUT_STALE_GRACE_SECONDS
    )


def _service_component_target(
    *,
    settings: WorkerSettings,
    component: str,
    target: Callable[[], None],
    exit_process: Callable[[int], None],
) -> Callable[[], None]:
    def run_component() -> None:
        try:
            target()
        except Exception as exc:
            _record_service_failure(settings, component, exc)
            exit_process(1)
            return
        _record_service_failure(
            settings,
            component,
            RuntimeError(f"{component} stopped unexpectedly"),
        )
        exit_process(1)

    return run_component


def build_style_corpus(workspace: Path, corpus_dir: Path) -> int:
    minutes_dir = workspace / "AI听记"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus_csv = corpus_dir / "style_corpus.csv"
    style_profile = corpus_dir / "style_profile.md"

    records = []
    markdown_files = []
    if minutes_dir.exists():
        markdown_files = sorted(
            path for path in minutes_dir.rglob("*.md") if path.is_file()
        )
        for path in markdown_files:
            records.extend(
                extract_minutes_records(
                    path,
                    source_title=str(path.relative_to(minutes_dir)),
                )
            )

    written_count = write_records(corpus_csv, records)
    style_profile.write_text(build_style_profile(records), encoding="utf-8")
    print(
        f"build-corpus scanned={len(markdown_files)} records={written_count} "
        f"csv={corpus_csv} profile={style_profile}",
        flush=True,
    )
    return written_count


def collect_corpus(settings: WorkerSettings, target_count: int = 1000) -> int:
    dws = DwsClient()
    sender_user_id = dws.get_current_user_id()
    end_time = datetime.now().astimezone()
    start_time = end_time - timedelta(days=183)
    cursor = "0"
    collected_records = []

    while len(collected_records) < target_count:
        try:
            payload = dws.list_messages_by_sender(
                sender_user_id=sender_user_id,
                start=start_time.isoformat(timespec="seconds"),
                end=end_time.isoformat(timespec="seconds"),
                limit=100,
                cursor=cursor,
            )
        except DwsError as exc:
            if "TIMEOUT_ERROR" not in str(exc):
                raise
            payload = dws.list_messages_by_sender(
                sender_user_id=sender_user_id,
                start=start_time.isoformat(timespec="seconds"),
                end=end_time.isoformat(timespec="seconds"),
                limit=100,
                cursor=cursor,
            )
        records = build_dingtalk_records_from_sender_payload(
            payload,
            limit=target_count - len(collected_records),
        )
        collected_records.extend(records)

        result = payload.get("result", {})
        if not result.get("hasMore"):
            break
        next_cursor = result.get("nextCursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)

    corpus_csv = settings.corpus_dir / "style_corpus.csv"
    append_records(corpus_csv, collected_records)
    print(
        f"collect-corpus sender_user_id={sender_user_id} records={len(collected_records)} "
        f"csv={corpus_csv}",
        flush=True,
    )
    return len(collected_records)


def build_work_profile_command(
    settings: WorkerSettings,
    *,
    refresh_minutes_corpus: bool = True,
    include_dingtalk_messages: bool = True,
    dingtalk_message_target_count: int = 1000,
    include_dingtalk_kb: bool = True,
    dingtalk_kb_workspace: str = "",
) -> int:
    evidence_dir = profile_evidence_dir()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if refresh_minutes_corpus:
        build_style_corpus(settings.workspace, settings.corpus_dir)
    if include_dingtalk_messages:
        collect_corpus(settings, target_count=dingtalk_message_target_count)

    evidence = []
    evidence.extend(
        collect_existing_corpus_evidence(settings.corpus_dir / "style_corpus.csv")
    )
    evidence.extend(collect_local_doc_evidence(settings.workspace))
    if include_dingtalk_kb:
        evidence.extend(
            collect_dingtalk_kb_evidence(
                dws=DwsClient(),
                workspace_id=dingtalk_kb_workspace or None,
            )
        )

    write_jsonl(evidence_dir / "evidence_index.jsonl", evidence)
    profile = build_initial_profile(evidence)
    profile_path = work_profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(render_markdown_profile(profile), encoding="utf-8")
    print(
        f"build-work-profile evidence={len(evidence)} "
        f"profile={profile_path} evidence_index={evidence_dir / 'evidence_index.jsonl'}",
        flush=True,
    )
    return len(evidence)


def probe_dws() -> int:
    dws = DwsClient()
    blocked = False

    try:
        conversations = dws.list_unread_conversations(count=1)
        print(f"unread_conversations: OK count={len(conversations)}", flush=True)
    except DwsError as exc:
        blocked = True
        print(f"unread_conversations: BLOCKED {exc}", flush=True)

    try:
        dws.ding_self("CEO agent dws probe")
        print("ding_self: OK", flush=True)
    except DwsError as exc:
        blocked = True
        print(f"ding_self: BLOCKED {exc}", flush=True)

    return 1 if blocked else 0


def probe_agent_runtimes_command(
    settings: WorkerSettings,
    *,
    route_names: tuple[str, ...] = (),
    refresher=None,
) -> int:
    """Force synthetic route probes and print only safe capability evidence."""

    if refresher is None:
        from app.agent_runtime_production import build_production_runtime_refresher

        try:
            refresher = build_production_runtime_refresher(
                store=AutoReplyStore(settings.db_path),
            )
        except ValueError:
            configured = {
                item.strip()
                for item in os.getenv(
                    "CEO_AGENT_RUNTIME_ROUTES", "codex_oauth"
                ).split(",")
                if item.strip()
            }
            selected = route_names or tuple(sorted(configured))
            routes = [
                {
                    "route_name": route_name,
                    "healthy": False,
                    "capabilities": [],
                    "checked_at": "",
                    "expires_at": "",
                    "failure_code": (
                        "missing_secret"
                        if route_name == "codex_api"
                        and "codex_api" in configured
                        and not os.getenv("CEO_CODEX_API_KEY", "").strip()
                        else "runtime_configuration_invalid"
                    ),
                }
                for route_name in selected
            ]
            print(json.dumps({"routes": routes}, ensure_ascii=False), flush=True)
            return 1
    snapshots = refresher.refresh_expired(route_names=route_names, force=True)
    routes = [
        {
            "route_name": snapshot.route_name,
            "healthy": snapshot.healthy,
            "capabilities": sorted(snapshot.capabilities),
            "checked_at": snapshot.checked_at,
            "expires_at": snapshot.expires_at,
            "failure_code": snapshot.failure.code if snapshot.failure else "",
        }
        for snapshot in snapshots.values()
    ]
    print(json.dumps({"routes": routes}, ensure_ascii=False), flush=True)
    return 0 if routes and all(route["healthy"] for route in routes) else 1


def initialize_agent_runtime_routes(settings: WorkerSettings, *, refresher=None):
    """Publish static surfaces and run one explicit startup probe."""

    if refresher is None:
        from app.agent_runtime_production import build_production_runtime_refresher

        refresher = build_production_runtime_refresher(
            store=AutoReplyStore(settings.db_path),
            temporary_root=settings.workspace,
        )
    refresher.refresh_expired(force=True)
    return refresher


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "wechat":
        from app.wechat import cli as wechat_cli

        raise SystemExit(wechat_cli.main(args.wechat_args))

    settings = settings_from_args(args)

    if args.command == "run-once":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        run_once(settings)
    elif args.command == "run":
        ensure_live_send_allowed(settings)
        runtime_refresher = initialize_agent_runtime_routes(settings)
        run_loop(
            create_worker(settings),
            settings.poll_interval_seconds,
            max_batches=settings.max_batches,
            runtime_refresher=runtime_refresher,
        )
    elif args.command == "service":
        ensure_live_send_allowed(settings)
        from app.agent_runtime_production import build_production_runtime_refresher

        runtime_refresher = build_production_runtime_refresher(
            store=AutoReplyStore(settings.db_path),
        )
        run_service(
            settings,
            host=args.host,
            port=args.port,
            producer_interval_seconds=args.producer_interval_seconds,
            consumer_poll_interval_seconds=args.consumer_poll_interval_seconds,
            runtime_refresher=runtime_refresher,
        )
    elif args.command == "produce-once":
        produce_once(settings)
    elif args.command == "produce":
        run_producer_loop(
            create_worker(settings),
            settings.poll_interval_seconds,
            max_tasks=settings.max_batches,
        )
    elif args.command == "consume-once":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        consume_once(settings)
    elif args.command == "consume":
        ensure_live_send_allowed(settings)
        runtime_refresher = initialize_agent_runtime_routes(settings)
        run_consumer_loop(
            create_worker(settings),
            settings.poll_interval_seconds,
            max_tasks=settings.max_batches,
            runtime_refresher=runtime_refresher,
        )
    elif args.command == "process-work-items":
        initialize_agent_runtime_routes(settings)
        process_work_items_command(settings)
    elif args.command == "retry-work-summary-input":
        retry_work_summary_input_command(settings, input_id=args.input_id)
    elif args.command == "backfill-task-memory-context":
        initialize_agent_runtime_routes(settings)
        backfill_task_memory_context_command(settings)
    elif args.command == "backfill-routine-process-todos":
        backfill_routine_process_todos_command(
            settings,
            todo_ids=args.todo_id,
            reason=args.reason,
            apply=args.apply,
        )
    elif args.command == "process-okr-reviews":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        process_okr_reviews_command(settings)
    elif args.command == "weekly-okr-report":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        weekly_okr_report_command(
            settings,
            force=args.force,
            period_label=args.period_label,
        )
    elif args.command == "refresh-okr-archive":
        refresh_company_okr_archive_command(
            settings,
            period_label=args.period_label,
            group_name=args.group_name,
        )
    elif args.command == "scan-task-sources":
        scan_task_sources_command(settings)
    elif args.command == "scan-oa-approvals":
        scan_oa_approvals_command(settings)
    elif args.command == "read-oa-approval-detail":
        read_oa_approval_detail_command(settings, process_instance_id=args.instance_id)
    elif args.command == "process-follow-ups":
        ensure_live_send_allowed(settings)
        process_follow_ups_command(settings)
    elif args.command == "check-follow-up-completions":
        check_follow_up_completions_command(settings, limit=1)
    elif args.command == "daily-task-maintenance":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        daily_task_maintenance_command(settings)
    elif args.command == "quality-check":
        raise SystemExit(
            quality_check_command(
                settings,
                state_file=args.state_file,
                verify_channels=args.verify_channels,
            )
        )
    elif args.command == "repository-updater":
        from app.repository_updater import RepositoryUpdater, load_persisted_operation

        store = AutoReplyStore(settings.db_path)
        operation = load_persisted_operation(store, args.operation_id)
        result = RepositoryUpdater(
            _repo_root(),
            store,
            remote=settings.repository_upgrade_remote,
            branch=settings.repository_upgrade_branch,
            database_path=settings.db_path,
        ).execute(operation)
        print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    elif args.command == "doctor-mcp":
        doctor_mcp_command(
            settings,
            service_mcp_config=args.service_mcp_config,
            verify_live=args.verify_live,
            notify=args.notify,
        )
    elif args.command == "channel-doctor":
        channel_doctor_command()
    elif args.command == "setup-memory-connector":
        setup_memory_connector_command(
            memory_url=args.memory_url,
            codex_config=args.codex_config,
            claude_config=args.claude_config,
        )
    elif args.command == "build-corpus":
        build_style_corpus(settings.workspace, settings.corpus_dir)
    elif args.command == "collect-corpus":
        collect_corpus(settings)
    elif args.command == "build-work-profile":
        build_work_profile_command(
            settings,
            refresh_minutes_corpus=not args.skip_minutes_corpus,
            include_dingtalk_messages=args.include_dingtalk_messages,
            dingtalk_message_target_count=args.dingtalk_message_target_count,
            include_dingtalk_kb=args.include_dingtalk_kb,
            dingtalk_kb_workspace=args.dingtalk_kb_workspace,
        )
    elif args.command == "probe-dws":
        raise SystemExit(probe_dws())
    elif args.command == "probe-agent-runtimes":
        raise SystemExit(
            probe_agent_runtimes_command(settings, route_names=tuple(args.route))
        )
    elif args.command == "refresh-org-cache":
        refresh_org_cache_command(settings, set(args.user_id))
    elif args.command == "feedback":
        record_feedback_command(
            settings,
            attempt_id=args.attempt_id,
            feedback=args.feedback,
            corrected_reply=args.corrected_reply,
        )
    elif args.command == "feedback-spike":
        feedback_spike_command(args)
    elif args.command == "audit-web":
        run_audit_web_command(
            settings,
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_interval_seconds=args.reload_interval_seconds,
        )
    elif args.command == "export-feedback":
        export_feedback_command(
            settings,
            output=Path(args.output),
            limit=args.limit,
        )
    elif args.command == "test-ding":
        test_ding_command(settings)
    elif args.command == "rerun-message":
        ensure_live_send_allowed(settings)
        initialize_agent_runtime_routes(settings)
        rerun_message_command(
            settings,
            conversation_id=args.conversation_id,
            message_id=args.message_id,
            force_new_decision=args.force_new_decision,
            context_time=args.context_time,
            oa_url=args.oa_url,
        )
    elif args.command == "send-attempt":
        ensure_live_send_allowed(settings)
        send_attempt_command(settings, attempt_id=args.attempt_id)
    elif args.command == "resolve-agent-run":
        resolve_agent_run_command(
            settings,
            run_id=args.run_id,
            execution_generation=args.execution_generation,
            resolution=args.resolution,
            reason=args.reason,
            actor=args.actor,
        )
    elif args.command == "reset-codex-sessions":
        reset_codex_sessions_command(settings)
    elif args.command == "replay-recent-meetings":
        ensure_live_send_allowed(settings)
        replay_recent_meetings_command(
            settings,
            limit=args.limit,
            offset=args.offset,
        )


if __name__ == "__main__":
    main()
