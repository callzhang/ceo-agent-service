import argparse
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, NonNegativeInt, PositiveInt

from app.codex_decision import CodexDecisionRunner, append_signature
from app.config import (
    consumer_poll_interval_seconds,
    feedback_spike_vercel_base_url,
    principal_display_name,
    producer_interval_seconds,
    profile_evidence_dir,
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
from app.dws_client import (
    DINGTALK_MESSAGE_TIME_ZONE,
    DwsClient,
    DwsError,
    local_time_zone_name,
    native_reply_delivery_payload,
)
from app.feedback_spike import (
    append_feedback_links,
    build_events_url,
    send_feedback_spike_links,
)
from app.leak_check import contains_forbidden_leak
from app.dingtalk_models import CodexAction, DingTalkConversation, DingTalkMessage
from app.notification import send_macos_notification
from app.oa_approval import OaApprovalCodexRunner
from app.org_cache import (
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from app.store import AutoReplyStore
from app.task_agent import TaskAgentCodexRunner, TaskAgentRunner, process_work_item
from app.work_profile import (
    build_initial_profile,
    collect_dingtalk_kb_evidence,
    collect_existing_corpus_evidence,
    collect_local_doc_evidence,
    render_markdown_profile,
    write_jsonl,
)
from app.worker import CALENDAR_ACTION_SEND_STATUS, DingTalkAutoReplyWorker

LIVE_SEND_BLOCKERS = (
    "deterministic personnel/candidate permission gates",
    "handoff-clear detection",
    "batching semantics",
)
LIVE_SEND_GUARD_ENV = "CEO_LIVE_SEND_BLOCKERS_ACCEPTED"
DEFAULT_DING_ROBOT_NAME = None
DEFAULT_WORKSPACE = Path.home() / "Documents" / "memory"
SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT = 500
run_audit_web = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    return _repo_root() / "data"


def _default_corpus_dir() -> Path:
    return _repo_root() / "corpus"


class WorkerSettings(BaseModel):
    workspace: Path = DEFAULT_WORKSPACE
    db_path: Path = _default_data_dir() / "auto-reply.sqlite3"
    corpus_dir: Path = _default_corpus_dir()
    dry_run: bool = False
    poll_interval_seconds: PositiveInt = 300
    batch_seconds: PositiveInt = 120
    ding_robot_code: str | None = None
    ding_robot_name: str | None = DEFAULT_DING_ROBOT_NAME
    ding_receiver_user_id: str | None = None
    dws_transient_retry_attempts: PositiveInt = 3
    dws_transient_retry_delay_seconds: float = 1.0
    codex_timeout_seconds: PositiveInt = 420
    codex_idle_timeout_seconds: PositiveInt = 180
    max_batches: NonNegativeInt | None = None


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
        "run-once",
        "run",
        "service",
        "produce-once",
        "produce",
        "consume-once",
        "consume",
        "process-work-items",
        "scan-task-sources",
        "process-follow-ups",
        "daily-task-maintenance",
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
        "reset-codex-sessions",
        "build-work-profile",
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
        if command == "refresh-org-cache":
            subparser.add_argument("--user-id", action="append", default=[])
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
                help="Vercel deployment root URL, for example https://example.vercel.app",
            )
            subparser.add_argument("--conversation-id", default="")
            subparser.add_argument("--user-id", default="")
            subparser.add_argument("--open-dingtalk-id", default="")
            subparser.add_argument(
                "--reply-text",
                default="这是一条 CEO agent 反馈链接 spike 测试消息。",
            )
            subparser.add_argument("--original-text", default="")
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

    return parser


def settings_from_args(args: argparse.Namespace) -> WorkerSettings:
    return WorkerSettings(
        workspace=Path(args.workspace),
        db_path=Path(args.db),
        corpus_dir=Path(args.corpus_dir),
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
        max_batches=args.max_batches,
    )


def create_worker(settings: WorkerSettings) -> DingTalkAutoReplyWorker:
    store = AutoReplyStore(settings.db_path)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    cached_dws = CachedDwsClient(dws=dws, org_directory=CachedOrgDirectory(store))
    codex = CodexDecisionRunner(
        workspace=settings.workspace,
        timeout_seconds=settings.codex_timeout_seconds,
        idle_timeout_seconds=settings.codex_idle_timeout_seconds,
    )
    oa_approval_runner = OaApprovalCodexRunner(
        workspace=settings.workspace,
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
    )
    worker.oa_approval_runner = oa_approval_runner
    return worker


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
    store = AutoReplyStore(settings.db_path)
    limit = 20 if settings.max_batches is None else settings.max_batches
    runner = TaskAgentRunner(
        TaskAgentCodexRunner(
            workspace=settings.workspace,
            timeout_seconds=settings.codex_timeout_seconds,
            idle_timeout_seconds=settings.codex_idle_timeout_seconds,
        )
    )
    processed = 0
    for work_input in store.claim_work_summary_inputs(limit=limit):
        try:
            process_work_item(store, runner, work_input)
            processed += 1
        except Exception as exc:
            store.mark_work_summary_input_failed(work_input.id, str(exc))
            store.record_error(None, None, "task_agent", str(exc))
    print(f"process-work-items processed={processed}", flush=True)
    return processed


def scan_task_sources_command(settings: WorkerSettings) -> int:
    from app.task_scanners import scan_ai_minutes, scan_local_workspace_files

    store = AutoReplyStore(settings.db_path)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    local_count = scan_local_workspace_files(store, workspace=settings.workspace)
    minutes_count = scan_ai_minutes(store, dws)
    total = local_count + minutes_count
    print(
        "scan-task-sources "
        f"local_files={local_count} ai_minutes={minutes_count} total={total}",
        flush=True,
    )
    return total


def process_follow_ups_command(settings: WorkerSettings) -> int:
    from app.follow_up import process_due_follow_ups

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
    )
    print(f"process-follow-ups sent={sent}", flush=True)
    return sent


def daily_task_maintenance_command(settings: WorkerSettings) -> dict[str, int]:
    sources = scan_task_sources_command(settings)
    work_items = process_work_items_command(settings)
    follow_ups = process_follow_ups_command(settings)
    result = {
        "sources": sources,
        "work_items": work_items,
        "follow_ups": follow_ups,
    }
    print(
        "daily-task-maintenance "
        f"sources={sources} work_items={work_items} follow_ups={follow_ups}",
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
        store.complete_reply_task_for_message(conversation_id, processed_message_id)
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
    if attempt.send_status != "dry_run":
        raise SystemExit(
            f"reply attempt {attempt_id} is not a dry_run attempt: {attempt.send_status}"
        )
    if attempt.calendar_event_id.strip() and attempt.calendar_response_status.strip():
        return _send_calendar_attempt(settings, store, attempt)
    if attempt.action not in {
        CodexAction.SEND_REPLY.value,
        CodexAction.ASK_CLARIFYING_QUESTION.value,
    }:
        raise SystemExit(
            f"reply attempt {attempt_id} is not sendable: action={attempt.action}"
        )
    reply_text = DingTalkAutoReplyWorker._native_reply_body(attempt.final_reply_text)
    if not reply_text.strip():
        raise SystemExit(f"reply attempt {attempt_id} has empty final_reply_text")
    if contains_forbidden_leak(reply_text):
        regenerated_reply_text = _regenerate_send_attempt_after_leak_check(
            settings,
            blocked_reply_text=reply_text,
            session_id=attempt.codex_session_id or None,
        )
        if regenerated_reply_text:
            reply_text = append_signature(regenerated_reply_text)
        if contains_forbidden_leak(reply_text):
            store.update_reply_attempt(
                attempt.id,
                send_status="blocked",
                send_error="leak_check",
            )
            store.record_error(
                attempt.conversation_id,
                attempt.trigger_message_id,
                "leak_check",
                reply_text,
            )
            raise SystemExit(f"reply attempt {attempt_id} blocked by leak_check")

    conversation = store.get_conversation(attempt.conversation_id)
    if conversation is None:
        raise SystemExit(f"conversation not found: {attempt.conversation_id}")

    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    dingtalk_conversation = DingTalkConversation(
        open_conversation_id=conversation.conversation_id,
        title=conversation.title,
        single_chat=conversation.single_chat,
        unread_point=0,
    )
    trigger = _trigger_message_for_attempt(
        dws=dws,
        conversation=dingtalk_conversation,
        attempt=attempt,
        store=store,
    )
    feedback_token = ""
    feedback_base_url = feedback_spike_vercel_base_url()
    if feedback_base_url:
        feedback_reply = append_feedback_links(
            vercel_base_url=feedback_base_url,
            reply_text=reply_text,
            original_text=attempt.trigger_text,
        )
        reply_text = feedback_reply.text
        feedback_token = feedback_reply.feedback_token
        store.update_reply_attempt(attempt.id, final_reply_text=reply_text)
        if contains_forbidden_leak(reply_text):
            regenerated_reply_text = _regenerate_send_attempt_after_leak_check(
                settings,
                blocked_reply_text=reply_text,
                session_id=attempt.codex_session_id or None,
            )
            if regenerated_reply_text:
                clean_reply_text = append_signature(regenerated_reply_text)
                feedback_reply = append_feedback_links(
                    vercel_base_url=feedback_base_url,
                    reply_text=clean_reply_text,
                    original_text=attempt.trigger_text,
                )
                reply_text = feedback_reply.text
                feedback_token = feedback_reply.feedback_token
                store.update_reply_attempt(attempt.id, final_reply_text=reply_text)
        if contains_forbidden_leak(reply_text):
            store.update_reply_attempt(
                attempt.id,
                send_status="blocked",
                send_error="leak_check",
            )
            store.record_error(
                attempt.conversation_id,
                attempt.trigger_message_id,
                "leak_check",
                reply_text,
            )
            raise SystemExit(f"reply attempt {attempt_id} blocked by leak_check")
    store.update_reply_attempt(attempt.id, final_reply_text=reply_text)
    try:
        send_result = dws.send_reply_to_trigger(
            dingtalk_conversation,
            trigger,
            reply_text,
        )
    except Exception as exc:
        store.update_reply_attempt(
            attempt.id,
            send_status="failed",
            send_error=str(exc),
        )
        store.record_error(
            attempt.conversation_id,
            attempt.trigger_message_id,
            "send",
            str(exc),
        )
        raise

    store.update_reply_attempt(attempt.id, send_status="sent", retry_count=0)
    store.record_sent_reply(
        attempt.conversation_id,
        attempt.trigger_message_id,
        reply_text,
        send_result_json=json.dumps(
            native_reply_delivery_payload(
                dingtalk_conversation,
                trigger,
                send_result,
            ),
            ensure_ascii=False,
        ),
        recall_key=DwsClient.extract_recall_key(send_result),
        feedback_token=feedback_token,
    )
    result = {
        "attempt_id": attempt.id,
        "conversation_title": attempt.conversation_title,
        "trigger_sender": attempt.trigger_sender,
        "trigger_text_excerpt": _excerpt(attempt.trigger_text),
        "send_status": "sent",
        "reply_text_excerpt": _excerpt(reply_text),
        "send_result_excerpt": _excerpt(json.dumps(send_result or {}, ensure_ascii=False)),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def _regenerate_send_attempt_after_leak_check(
    settings: WorkerSettings,
    *,
    blocked_reply_text: str,
    session_id: str | None,
) -> str:
    codex = CodexDecisionRunner(
        workspace=settings.workspace,
        timeout_seconds=settings.codex_timeout_seconds,
        idle_timeout_seconds=settings.codex_idle_timeout_seconds,
    )
    decision = codex.decide(
        prompt=DingTalkAutoReplyWorker._leak_check_feedback_prompt(
            blocked_reply_text
        ),
        session_id=session_id,
    )
    if decision.action not in {
        CodexAction.SEND_REPLY,
        CodexAction.ASK_CLARIFYING_QUESTION,
    }:
        return ""
    return decision.reply_text.strip()


def _send_calendar_attempt(
    settings: WorkerSettings,
    store: AutoReplyStore,
    attempt,
) -> dict[str, object]:
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    event_id = attempt.calendar_event_id.strip()
    response_status = attempt.calendar_response_status.strip()
    try:
        action_result = dws.respond_calendar_event(event_id, response_status)
    except Exception as exc:
        store.update_reply_attempt(
            attempt.id,
            send_status="failed",
            send_error=str(exc),
        )
        store.record_error(
            attempt.conversation_id,
            attempt.trigger_message_id,
            "calendar_response",
            str(exc),
        )
        raise

    store.update_reply_attempt(
        attempt.id,
        calendar_response_result_json=json.dumps(
            action_result,
            ensure_ascii=False,
            sort_keys=True,
        ),
        send_status=CALENDAR_ACTION_SEND_STATUS,
        send_error="",
        retry_count=0,
    )
    if attempt.codex_reason:
        store.record_error(
            attempt.conversation_id,
            attempt.trigger_message_id,
            "calendar_response",
            f"{response_status}: {attempt.codex_reason}",
        )
    result = {
        "attempt_id": attempt.id,
        "conversation_title": attempt.conversation_title,
        "trigger_sender": attempt.trigger_sender,
        "trigger_text_excerpt": _excerpt(attempt.trigger_text),
        "send_status": CALENDAR_ACTION_SEND_STATUS,
        "calendar_event_id": event_id,
        "calendar_response_status": response_status,
        "calendar_response_result_excerpt": _excerpt(
            json.dumps(action_result or {}, ensure_ascii=False)
        ),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def _trigger_message_for_attempt(
    *,
    dws: DwsClient,
    conversation: DingTalkConversation,
    attempt,
    store: AutoReplyStore,
) -> DingTalkMessage:
    task = store.get_reply_task_for_message(
        attempt.conversation_id,
        attempt.trigger_message_id,
    )
    if task is not None:
        try:
            raw_payload = json.loads(task.trigger_message_json)
        except json.JSONDecodeError:
            raw_payload = {}
        if isinstance(raw_payload, dict):
            message = _trigger_message_from_payload(
                raw_payload,
                conversation=conversation,
            )
            if (
                message.open_message_id == attempt.trigger_message_id
                and message.sender_open_dingtalk_id
            ):
                return message

    candidate_conversations = [conversation]
    attempt_created_at_ms = _attempt_created_at_ms(attempt.created_at)
    if attempt_created_at_ms is not None:
        candidate_conversations.append(
            conversation.model_copy(update={"last_message_create_at": attempt_created_at_ms})
        )
    for candidate_conversation in candidate_conversations:
        for message in _send_attempt_target_lookup_messages(dws, candidate_conversation):
            if message.open_message_id != attempt.trigger_message_id:
                continue
            if getattr(message, "sender_open_dingtalk_id", ""):
                return message
            break
    raise SystemExit(
        f"reply attempt {attempt.id} cannot resolve trigger senderOpenDingTalkId for native reply"
    )


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


def _send_attempt_target_lookup_messages(dws: DwsClient, conversation):
    yield from dws.read_recent_messages(
        conversation,
        limit=SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT,
    )
    if conversation.last_message_create_at is None:
        return
    payload = dws.run_json(
        dws.build_message_list_command(
            conversation,
            limit=SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT,
            forward=True,
        )
    )
    yield from dws.parse_messages(
        payload,
        conversation_title=conversation.title,
        single_chat=conversation.single_chat,
    )


def _attempt_created_at_ms(created_at: str) -> int | None:
    try:
        parsed = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000)


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


def run_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_batches: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
) -> None:
    while True:
        worker.run_once(max_batches=max_batches)
        sleep(poll_interval_seconds)


def run_producer_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_tasks: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
) -> None:
    while True:
        worker.produce_once(max_tasks=max_tasks)
        sleep(poll_interval_seconds)


def run_consumer_loop(
    worker: DingTalkAutoReplyWorker,
    poll_interval_seconds: int,
    max_tasks: int | None = None,
    sleep: Callable[[int], None] = time.sleep,
) -> None:
    while True:
        worker.consume_once(max_tasks=max_tasks)
        sleep(poll_interval_seconds)


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
) -> None:
    components = (
        (
            "producer",
            lambda: run_producer_loop(
                create_worker(settings),
                producer_interval_seconds,
                max_tasks=settings.max_batches,
            ),
        ),
        (
            "consumer",
            lambda: run_consumer_loop(
                create_worker(settings),
                consumer_poll_interval_seconds,
                max_tasks=settings.max_batches,
            ),
        ),
        (
            "audit-web",
            lambda: run_audit_web_command(settings, host=host, port=port, reload=False),
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = settings_from_args(args)

    if args.command == "run-once":
        ensure_live_send_allowed(settings)
        run_once(settings)
    elif args.command == "run":
        ensure_live_send_allowed(settings)
        run_loop(
            create_worker(settings),
            settings.poll_interval_seconds,
            max_batches=settings.max_batches,
        )
    elif args.command == "service":
        ensure_live_send_allowed(settings)
        run_service(
            settings,
            host=args.host,
            port=args.port,
            producer_interval_seconds=args.producer_interval_seconds,
            consumer_poll_interval_seconds=args.consumer_poll_interval_seconds,
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
        consume_once(settings)
    elif args.command == "consume":
        ensure_live_send_allowed(settings)
        run_consumer_loop(
            create_worker(settings),
            settings.poll_interval_seconds,
            max_tasks=settings.max_batches,
        )
    elif args.command == "process-work-items":
        process_work_items_command(settings)
    elif args.command == "scan-task-sources":
        scan_task_sources_command(settings)
    elif args.command == "process-follow-ups":
        process_follow_ups_command(settings)
    elif args.command == "daily-task-maintenance":
        daily_task_maintenance_command(settings)
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
    elif args.command == "reset-codex-sessions":
        reset_codex_sessions_command(settings)


if __name__ == "__main__":
    main()
