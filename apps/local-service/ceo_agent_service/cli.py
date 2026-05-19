import argparse
import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, PositiveInt

from ceo_agent_service.codex_decision import contains_forbidden_leak
from ceo_agent_service.codex_decision import CodexDecisionRunner
from ceo_agent_service.corpus import (
    append_records,
    build_dingtalk_records_from_sender_payload,
    build_style_profile,
    extract_minutes_records,
    load_corpus_records,
    write_records,
)
from ceo_agent_service.audit_web import run_audit_web
from ceo_agent_service.dws_client import DwsClient, DwsError, local_time_zone_name
from ceo_agent_service.dingtalk_models import CodexAction, DingTalkConversation
from ceo_agent_service.org_cache import (
    CachedDwsClient,
    CachedOrgDirectory,
    refresh_org_cache,
)
from ceo_agent_service.store import AutoReplyStore
from ceo_agent_service.worker import DingTalkAutoReplyWorker

LIVE_SEND_BLOCKERS = (
    "deterministic personnel/candidate permission gates",
    "handoff-clear detection",
    "batching semantics",
)
LIVE_SEND_GUARD_ENV = "CEO_LIVE_SEND_BLOCKERS_ACCEPTED"
DEFAULT_DING_ROBOT_NAME = None
DEFAULT_WORKSPACE = Path.home() / "Documents" / "memory"
SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT = 500


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_data_dir() -> Path:
    return _repo_root() / "data"


def _default_corpus_dir() -> Path:
    return _repo_root() / "corpus"


class WorkerSettings(BaseModel):
    workspace: Path = DEFAULT_WORKSPACE
    db_path: Path = _default_data_dir() / "auto-reply.sqlite3"
    corpus_dir: Path = _default_corpus_dir()
    dry_run: bool = True
    poll_interval_seconds: PositiveInt = 30
    batch_seconds: PositiveInt = 120
    ding_robot_code: str | None = None
    ding_robot_name: str | None = DEFAULT_DING_ROBOT_NAME
    ding_receiver_user_id: str | None = None
    dws_transient_retry_attempts: PositiveInt = 3
    dws_transient_retry_delay_seconds: float = 1.0
    max_batches: PositiveInt | None = None


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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _optional_positive_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return _positive_int(value)


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
        "build-corpus",
        "collect-corpus",
        "refresh-org-cache",
        "feedback",
        "audit-web",
        "export-feedback",
        "test-ding",
        "rerun-message",
        "send-attempt",
        "reset-codex-sessions",
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--db", default=os.getenv("CEO_WORKER_DB", str(defaults.db_path)))
        subparser.add_argument("--workspace", default=os.getenv("CEO_WORKSPACE", str(defaults.workspace)))
        subparser.add_argument("--corpus-dir", default=os.getenv("CEO_CORPUS_DIR", str(defaults.corpus_dir)))
        subparser.add_argument("--dry-run", action="store_true", default=_env_bool("CEO_DRY_RUN", defaults.dry_run))
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
            type=_positive_int,
            default=_optional_positive_int_env("CEO_MAX_BATCHES"),
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
        if command == "refresh-org-cache":
            subparser.add_argument("--user-id", action="append", default=[])
        if command == "feedback":
            subparser.add_argument("--attempt-id", type=int, required=True)
            subparser.add_argument("--feedback", required=True)
            subparser.add_argument("--corrected-reply", default="")
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
                "--force-new-decision",
                action="store_true",
                help="run Codex again even if this message already has an attempt",
            )
        if command == "send-attempt":
            subparser.add_argument("--attempt-id", type=int, required=True)

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
    codex = CodexDecisionRunner(workspace=settings.workspace)
    style_profile = _load_style_profile(settings.corpus_dir)
    style_records = load_corpus_records(settings.corpus_dir / "derek_style_corpus.csv")
    return DingTalkAutoReplyWorker(
        store=store,
        dws=cached_dws,
        codex=codex,
        dry_run=settings.dry_run,
        style_profile=style_profile,
        style_records=style_records,
    )


def ensure_live_send_allowed(settings: WorkerSettings) -> None:
    if settings.dry_run:
        return
    if _env_bool(LIVE_SEND_GUARD_ENV, False):
        return

    blockers = "\n".join(f"- {blocker}" for blocker in LIVE_SEND_BLOCKERS)
    raise SystemExit(
        "CEO_DRY_RUN=0 is blocked until unresolved live-send blockers are "
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


def rerun_message_command(
    settings: WorkerSettings,
    conversation_id: str,
    message_id: str,
    *,
    force_new_decision: bool = False,
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
            ),
            message_id,
            force_new_decision=force_new_decision,
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
    if attempt.send_status != "dry_run":
        raise SystemExit(
            f"reply attempt {attempt_id} is not a dry_run attempt: {attempt.send_status}"
        )
    if attempt.action not in {
        CodexAction.SEND_REPLY.value,
        CodexAction.ASK_CLARIFYING_QUESTION.value,
    }:
        raise SystemExit(
            f"reply attempt {attempt_id} is not sendable: action={attempt.action}"
        )
    if not attempt.final_reply_text.strip():
        raise SystemExit(f"reply attempt {attempt_id} has empty final_reply_text")
    if contains_forbidden_leak(attempt.final_reply_text):
        store.update_reply_attempt(
            attempt.id,
            send_status="blocked",
            send_error="leak_check",
        )
        store.record_error(
            attempt.conversation_id,
            attempt.trigger_message_id,
            "leak_check",
            attempt.final_reply_text,
        )
        raise SystemExit(f"reply attempt {attempt_id} blocked by leak_check")

    conversation = store.get_conversation(attempt.conversation_id)
    if conversation is None:
        raise SystemExit(f"conversation not found: {attempt.conversation_id}")

    at_users = _at_user_ids_from_reply(attempt.final_reply_text)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
        transient_retry_attempts=settings.dws_transient_retry_attempts,
        transient_retry_delay_seconds=settings.dws_transient_retry_delay_seconds,
    )
    direct_user_id, direct_open_dingtalk_id = _direct_send_target_for_attempt(
        dws=dws,
        conversation=conversation,
        attempt=attempt,
        store=store,
    )
    try:
        send_result = dws.send_message(
            None if conversation.single_chat else attempt.conversation_id,
            attempt.final_reply_text,
            at_users=[] if conversation.single_chat else at_users,
            user_id=direct_user_id,
            open_dingtalk_id=direct_open_dingtalk_id,
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
        attempt.final_reply_text,
        send_result_json=json.dumps(send_result or {}, ensure_ascii=False),
        recall_key=DwsClient.extract_recall_key(send_result),
    )
    result = {
        "attempt_id": attempt.id,
        "conversation_title": attempt.conversation_title,
        "trigger_sender": attempt.trigger_sender,
        "trigger_text_excerpt": _excerpt(attempt.trigger_text),
        "send_status": "sent",
        "reply_text_excerpt": _excerpt(attempt.final_reply_text),
        "send_result_excerpt": _excerpt(json.dumps(send_result or {}, ensure_ascii=False)),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def _direct_send_target_for_attempt(
    *,
    dws: DwsClient,
    conversation,
    attempt,
    store: AutoReplyStore,
) -> tuple[str | None, str | None]:
    if not conversation.single_chat:
        return None, None
    if attempt.direct_user_id.strip():
        return attempt.direct_user_id.strip(), None
    if attempt.direct_open_dingtalk_id.strip():
        return None, attempt.direct_open_dingtalk_id.strip()

    dingtalk_conversation = DingTalkConversation(
        open_conversation_id=conversation.conversation_id,
        title=conversation.title,
        single_chat=True,
        unread_point=0,
    )
    candidate_conversations = [dingtalk_conversation]
    attempt_created_at_ms = _attempt_created_at_ms(attempt.created_at)
    if attempt_created_at_ms is not None:
        candidate_conversations.append(
            dingtalk_conversation.model_copy(
                update={"last_message_create_at": attempt_created_at_ms}
            )
        )
    for candidate_conversation in candidate_conversations:
        for message in dws.read_recent_messages(
            candidate_conversation,
            limit=SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT,
        ):
            if message.open_message_id != attempt.trigger_message_id:
                continue
            if message.sender_user_id:
                store.update_reply_attempt(
                    attempt.id,
                    direct_user_id=message.sender_user_id,
                    direct_open_dingtalk_id=getattr(
                        message, "sender_open_dingtalk_id", None
                    )
                    or "",
                )
                return message.sender_user_id, None
            sender_open_dingtalk_id = (
                getattr(message, "sender_open_dingtalk_id", None) or ""
            )
            if sender_open_dingtalk_id:
                store.update_reply_attempt(
                    attempt.id,
                    direct_open_dingtalk_id=sender_open_dingtalk_id,
                )
                return None, sender_open_dingtalk_id
            try:
                resolved_sender_user_id = dws.resolve_message_sender(message)
            except Exception:
                continue
            if resolved_sender_user_id:
                store.update_reply_attempt(
                    attempt.id,
                    direct_user_id=resolved_sender_user_id,
                )
                return resolved_sender_user_id, None
            break
    raise SystemExit(
        f"reply attempt {attempt.id} cannot resolve direct user id for single-chat send"
    )


def _attempt_created_at_ms(created_at: str) -> int | None:
    try:
        parsed = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _at_user_ids_from_reply(reply_text: str) -> list[str]:
    user_ids: list[str] = []
    for match in re.finditer(r"<@([^>]+)>", reply_text):
        user_id = match.group(1).strip()
        if user_id and user_id not in user_ids:
            user_ids.append(user_id)
    return user_ids


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


def run_audit_web_command(
    settings: WorkerSettings,
    host: str,
    port: int,
    reload: bool = False,
    reload_interval_seconds: int = 1,
) -> None:
    run_audit_web(
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


def build_style_corpus(workspace: Path, corpus_dir: Path) -> int:
    minutes_dir = workspace / "AI听记"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus_csv = corpus_dir / "derek_style_corpus.csv"
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

    corpus_csv = settings.corpus_dir / "derek_style_corpus.csv"
    append_records(corpus_csv, collected_records)
    print(
        f"collect-corpus sender_user_id={sender_user_id} records={len(collected_records)} "
        f"csv={corpus_csv}",
        flush=True,
    )
    return len(collected_records)


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
    elif args.command == "build-corpus":
        build_style_corpus(settings.workspace, settings.corpus_dir)
    elif args.command == "collect-corpus":
        collect_corpus(settings)
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
        )
    elif args.command == "send-attempt":
        ensure_live_send_allowed(settings)
        send_attempt_command(settings, attempt_id=args.attempt_id)
    elif args.command == "reset-codex-sessions":
        reset_codex_sessions_command(settings)


if __name__ == "__main__":
    main()
