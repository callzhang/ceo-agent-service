import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import cli
from app.cli import (
    WorkerSettings,
    build_work_profile_command,
    build_parser,
    build_style_corpus,
    collect_corpus,
    create_worker,
    ensure_live_send_allowed,
    export_feedback_command,
    probe_dws,
    rerun_message_command,
    reset_codex_sessions_command,
    run_consumer_loop,
    record_feedback_command,
    refresh_org_cache_command,
    run_loop,
    run_producer_loop,
    run_service,
    process_work_items_command,
    send_attempt_command,
    settings_from_args,
    test_ding_command as run_test_ding_command,
    run_audit_web_command,
)
from app.corpus import CorpusRecord, append_records
from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.task_models import TaskAgentDecision, WorkItem


def enqueue_trigger_task(
    store,
    *,
    conversation_id: str = "cid-1",
    conversation_title: str = "Friday",
    single_chat: bool = False,
    trigger_message_id: str = "msg-1",
    trigger_sender: str = "Phina",
    trigger_text: str = "@Alex Chen 看一下",
    sender_open_dingtalk_id: str = "open-sender-1",
):
    store.enqueue_reply_task(
        conversation_id=conversation_id,
        conversation_title=conversation_title,
        single_chat=single_chat,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender=trigger_sender,
        trigger_text=trigger_text,
        trigger_message_json=json.dumps(
            {
                "openConversationId": conversation_id,
                "openMessageId": trigger_message_id,
                "sender": trigger_sender,
                "senderOpenDingTalkId": sender_open_dingtalk_id,
                "createTime": "2026-05-28 18:00:00",
                "content": trigger_text,
            },
            ensure_ascii=False,
        ),
    )


def test_parser_supports_worker_commands():
    parser = build_parser()

    args = parser.parse_args(
        ["run-once", "--not-send-message", "--db", "/tmp/worker.sqlite3"]
    )

    assert args.command == "run-once"
    assert args.dry_run is True
    assert args.db == "/tmp/worker.sqlite3"


def test_parser_supports_process_work_items():
    args = build_parser().parse_args(["process-work-items", "--max-batches", "3"])

    assert args.command == "process-work-items"
    assert args.max_batches == 3


def test_parser_supports_scan_task_sources():
    args = build_parser().parse_args(["scan-task-sources", "--workspace", "/tmp/w"])

    assert args.command == "scan-task-sources"
    assert args.workspace == "/tmp/w"


def test_parser_supports_setup_memory_connector():
    args = build_parser().parse_args(
        [
            "setup-memory-connector",
            "--memory-url",
            "https://memory.example/mcp/",
            "--codex-config",
            "/tmp/codex.toml",
            "--claude-config",
            "/tmp/claude.json",
        ]
    )

    assert args.command == "setup-memory-connector"
    assert args.memory_url == "https://memory.example/mcp/"
    assert args.codex_config == "/tmp/codex.toml"
    assert args.claude_config == "/tmp/claude.json"


def test_setup_memory_connector_command_updates_codex_and_claude(tmp_path, capsys):
    codex_config = tmp_path / "config.toml"
    claude_config = tmp_path / "claude.json"

    result = cli.setup_memory_connector_command(
        memory_url="https://memory.example/mcp/",
        codex_config=str(codex_config),
        claude_config=str(claude_config),
    )

    assert result["codex_config"] == str(codex_config)
    assert result["claude_config"] == str(claude_config)
    assert "[mcp_servers.memory_connector]" in codex_config.read_text(
        encoding="utf-8"
    )
    assert "memory_connector" in claude_config.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "setup-memory-connector codex_config=" in out
    assert "claude_config=" in out


def test_setup_memory_connector_command_requires_memory_url(tmp_path):
    with pytest.raises(SystemExit):
        cli.setup_memory_connector_command(
            memory_url="",
            codex_config=str(tmp_path / "config.toml"),
            claude_config=str(tmp_path / "claude.json"),
        )


def test_process_work_items_command_processes_claimed_input(tmp_path, monkeypatch, capsys):
    class FakeTaskAgentCodexRunner:
        last_session_id = "task-session-1"
        last_transcript_start_line = 0
        last_transcript_end_line = 0

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def decide(self, *, prompt, session_id=None):
            return TaskAgentDecision.model_validate(
                {
                    "action": "create_project",
                    "project": {
                        "title": "售前知识库建设",
                        "category": "sales",
                        "status": "active",
                    },
                    "todo_changes": [],
                    "follow_up_drafts": [],
                    "update_summary": "创建项目。",
                    "merge_reason": "事项名称稳定。",
                    "memory_recall_used": False,
                    "confidence": 0.8,
                }
            )

    monkeypatch.setattr(cli, "TaskAgentCodexRunner", FakeTaskAgentCodexRunner)
    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "售前推进",
                "conversation_id": "cid-1",
                "conversation_title": "售前群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "售前知识库需要补齐来源链接。",
            "project_name": "售前知识库",
            "context": {
                "sender": "Mina",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )

    processed = process_work_items_command(
        WorkerSettings(db_path=db_path, workspace=tmp_path, max_batches=5)
    )

    loaded = AutoReplyStore(db_path)
    assert processed == 1
    assert capsys.readouterr().out == "process-work-items processed=1\n"
    assert loaded.list_work_projects()[0].title == "售前知识库建设"
    assert loaded.claim_work_summary_inputs(limit=1) == []
    with loaded._connect() as db:
        status = db.execute(
            "select status from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()["status"]
    assert status == "done"


def test_process_work_items_command_respects_zero_max_batches(
    tmp_path,
    monkeypatch,
    capsys,
):
    class FakeTaskAgentCodexRunner:
        last_session_id = "task-session-1"
        last_transcript_start_line = 0
        last_transcript_end_line = 0

        def __init__(self, **kwargs):
            pass

        def decide(self, *, prompt, session_id=None):
            raise AssertionError("no inputs should be claimed")

    monkeypatch.setattr(cli, "TaskAgentCodexRunner", FakeTaskAgentCodexRunner)
    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    item = WorkItem.model_validate(
        {
            "source": {"type": "reply_attempt", "ref": "1"},
            "summary": "售前知识库需要补齐来源链接。",
            "project_name": "售前知识库",
            "context": {
                "sender": "Mina",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )
    store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )

    processed = process_work_items_command(
        WorkerSettings(db_path=db_path, workspace=tmp_path, max_batches=0)
    )

    assert processed == 0
    assert capsys.readouterr().out == "process-work-items processed=0\n"
    assert len(AutoReplyStore(db_path).claim_work_summary_inputs(limit=1)) == 1


def test_scan_task_sources_command_scans_local_and_minutes(
    tmp_path,
    monkeypatch,
    capsys,
):
    from app.cli import scan_task_sources_command

    calls = []

    def fake_local_scan(store, *, workspace):
        calls.append(("local", store.path, workspace))
        return 2

    def fake_minutes_scan(store, dws):
        calls.append(("minutes", store.path, type(dws).__name__))
        return 3

    class FakeDwsClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("app.task_scanners.scan_local_workspace_files", fake_local_scan)
    monkeypatch.setattr("app.task_scanners.scan_ai_minutes", fake_minutes_scan)
    monkeypatch.setattr(cli, "DwsClient", FakeDwsClient)
    db_path = tmp_path / "task.sqlite3"

    total = scan_task_sources_command(
        WorkerSettings(db_path=db_path, workspace=tmp_path)
    )

    assert total == 5
    assert calls == [
        ("local", db_path, tmp_path),
        ("minutes", db_path, "FakeDwsClient"),
    ]
    assert (
        capsys.readouterr().out
        == "scan-task-sources local_files=2 ai_minutes=3 total=5\n"
    )


def test_parser_supports_single_service_command(monkeypatch):
    monkeypatch.setenv("CEO_PRODUCER_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("CEO_CONSUMER_POLL_INTERVAL_SECONDS", "10")
    parser = build_parser()

    args = parser.parse_args(
        [
            "service",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--producer-interval-seconds",
            "61",
            "--consumer-poll-interval-seconds",
            "11",
        ]
    )

    assert args.command == "service"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.producer_interval_seconds == 61
    assert args.consumer_poll_interval_seconds == 11


def test_parser_keeps_dry_run_as_not_send_message_alias():
    parser = build_parser()

    args = parser.parse_args(["run-once", "--dry-run"])

    assert args.command == "run-once"
    assert args.dry_run is True


def test_parser_defaults_to_live_send_when_not_send_env_is_unset(monkeypatch):
    monkeypatch.delenv("CEO_DRY_RUN", raising=False)
    monkeypatch.delenv("CEO_NOT_SEND_MESSAGE", raising=False)
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.dry_run is False


def test_parser_supports_reset_codex_sessions_command():
    parser = build_parser()

    args = parser.parse_args(
        [
            "reset-codex-sessions",
            "--db",
            "/tmp/worker.sqlite3",
        ]
    )

    assert args.command == "reset-codex-sessions"
    assert args.db == "/tmp/worker.sqlite3"


def test_parser_supports_rerun_message_command():
    parser = build_parser()

    args = parser.parse_args(
        [
            "rerun-message",
            "--conversation-id",
            "cid-1",
            "--message-id",
            "msg-1",
            "--context-time",
            "2026-05-20 09:56:09",
            "--oa-url",
            "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
            "--force-new-decision",
        ]
    )

    assert args.command == "rerun-message"
    assert args.conversation_id == "cid-1"
    assert args.message_id == "msg-1"
    assert args.context_time == "2026-05-20 09:56:09"
    assert args.oa_url == "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    assert args.force_new_decision is True


def test_parser_supports_send_attempt_command():
    parser = build_parser()

    args = parser.parse_args(["send-attempt", "--attempt-id", "42"])

    assert args.command == "send-attempt"
    assert args.attempt_id == 42


def test_build_work_profile_command_is_registered():
    parser = build_parser()

    args = parser.parse_args(
        [
            "build-work-profile",
            "--workspace",
            "/tmp/memory",
            "--include-dingtalk-messages",
            "--include-dingtalk-kb",
            "--dingtalk-message-target-count",
            "25",
        ]
    )

    assert args.command == "build-work-profile"
    assert args.workspace == "/tmp/memory"
    assert args.include_dingtalk_messages is True
    assert args.include_dingtalk_kb is True
    assert args.dingtalk_message_target_count == 25


def test_build_work_profile_command_uses_all_sources_by_default():
    parser = build_parser()

    args = parser.parse_args(["build-work-profile"])

    assert args.skip_minutes_corpus is False
    assert args.include_dingtalk_messages is True
    assert args.include_dingtalk_kb is True


def test_build_work_profile_command_can_skip_live_sources_in_parser():
    parser = build_parser()

    args = parser.parse_args(
        ["build-work-profile", "--skip-dingtalk-messages", "--skip-dingtalk-kb"]
    )

    assert args.include_dingtalk_messages is False
    assert args.include_dingtalk_kb is False


def test_build_work_profile_command_writes_repo_assets(tmp_path, monkeypatch):
    from app.work_profile import EvidenceRecord

    workspace = tmp_path / "memory"
    corpus_dir = tmp_path / "corpus"
    evidence_dir = tmp_path / "data" / "profile-evidence"
    profile_path = tmp_path / "profiles" / "work_profile.md"

    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("CEO_PROFILE_EVIDENCE_DIR", str(evidence_dir))
    calls = []
    monkeypatch.setattr(
        cli,
        "build_style_corpus",
        lambda workspace, corpus_dir: calls.append(("minutes", workspace, corpus_dir)) or 2,
    )
    monkeypatch.setattr(
        cli,
        "collect_corpus",
        lambda settings, target_count=1000: calls.append(("dingtalk", target_count)) or 3,
    )
    monkeypatch.setattr(
        cli,
        "collect_existing_corpus_evidence",
        lambda path: [
            EvidenceRecord(
                id="ev_abc",
                source_type="dingtalk",
                title="客户群",
                timestamp="2026-05-26T10:00:00",
                location="cid/msg",
                scenario="business",
                evidence_strength="behavior_high",
                sensitivity="general",
                excerpt="先收敛目标和边界。",
                usable_for_profile=True,
            )
        ],
    )
    monkeypatch.setattr(cli, "collect_local_doc_evidence", lambda path: [])
    monkeypatch.setattr(
        cli,
        "collect_dingtalk_kb_evidence",
        lambda **kwargs: [
            EvidenceRecord(
                id="ev_kb",
                source_type="dingtalk_kb_live",
                title="知识库",
                location="dingtalk-kb:node-1",
                scenario="business",
                evidence_strength="kb_live_doc",
                sensitivity="general",
                excerpt="知识库材料。",
                usable_for_profile=True,
            )
        ],
    )

    settings = WorkerSettings(workspace=workspace, corpus_dir=corpus_dir)

    count = build_work_profile_command(
        settings,
        include_dingtalk_messages=True,
        include_dingtalk_kb=True,
    )

    assert count == 2
    assert calls == [
        ("minutes", workspace, corpus_dir),
        ("dingtalk", 1000),
    ]
    assert profile_path.exists()
    assert (evidence_dir / "evidence_index.jsonl").exists()
    assert not profile_path.with_suffix(".json").exists()
    assert not (profile_path.parent / "work-skill").exists()
    assert not (evidence_dir / "dingtalk_kb_cache").exists()


def test_build_work_profile_command_can_skip_live_sources(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv(
        "CEO_WORK_PROFILE_PATH",
        str(tmp_path / "profiles" / "work_profile.md"),
    )
    monkeypatch.setenv(
        "CEO_PROFILE_EVIDENCE_DIR",
        str(tmp_path / "data" / "profile-evidence"),
    )
    monkeypatch.setattr(cli, "build_style_corpus", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        cli,
        "collect_corpus",
        lambda *args, **kwargs: calls.append("collect_corpus") or 0,
    )
    monkeypatch.setattr(cli, "collect_existing_corpus_evidence", lambda path: [])
    monkeypatch.setattr(cli, "collect_local_doc_evidence", lambda path: [])
    monkeypatch.setattr(
        cli,
        "collect_dingtalk_kb_evidence",
        lambda **kwargs: calls.append("collect_dingtalk_kb_evidence") or [],
    )

    count = build_work_profile_command(
        WorkerSettings(workspace=tmp_path / "memory", corpus_dir=tmp_path / "corpus"),
        include_dingtalk_messages=False,
        include_dingtalk_kb=False,
    )

    assert count == 0
    assert calls == []


def test_settings_defaults_point_to_memory_home():
    parser = build_parser()
    args = parser.parse_args(["run-once"])

    settings = settings_from_args(args)
    repo_root = cli._repo_root()

    assert settings.workspace == Path.home() / "Documents" / "memory"
    assert settings.db_path == repo_root / "data" / "auto-reply.sqlite3"
    assert settings.corpus_dir == repo_root / "corpus"
    assert settings.batch_seconds == 120
    assert settings.poll_interval_seconds == 300
    assert settings.codex_timeout_seconds == 420
    assert settings.codex_idle_timeout_seconds == 180
    assert settings.max_batches is None


def test_reset_codex_sessions_command_only_clears_conversation_sessions(tmp_path):
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3")
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
    )

    cleared = reset_codex_sessions_command(settings)

    loaded = cli.AutoReplyStore(settings.db_path)
    attempt = loaded.get_reply_attempt(attempt_id)
    assert cleared == 1
    assert loaded.get_codex_session_id("cid-1") is None
    assert attempt is not None
    assert attempt.codex_session_id == "session-1"


def test_send_attempt_command_sends_existing_dry_run_without_rerunning_codex(
    monkeypatch, tmp_path, capsys
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(
        db_path=tmp_path / "worker.sqlite3",
        dry_run=False,
        dws_transient_retry_attempts=4,
        dws_transient_retry_delay_seconds=0,
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, None)
    enqueue_trigger_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Phina: 看一下\n\n<@user-1> 可以先这样处理。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    result = send_attempt_command(settings, attempt_id)

    assert sent["reply"] == (
        "cid-1",
        "msg-1",
        "open-sender-1",
        "可以先这样处理。（by明哥分身）",
    )
    assert result["send_status"] == "sent"
    updated = cli.AutoReplyStore(settings.db_path).get_reply_attempt(attempt_id)
    assert updated is not None
    assert updated.send_status == "sent"
    assert updated.final_reply_text == "可以先这样处理。（by明哥分身）"
    sent_reply = cli.AutoReplyStore(settings.db_path).get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.recall_key == "recall-1"
    assert '"kind": "native_reply"' in sent_reply.send_result_json
    assert '"ref_message_id": "msg-1"' in sent_reply.send_result_json
    assert '"send_status": "sent"' in capsys.readouterr().out


def test_send_attempt_command_executes_existing_dry_run_calendar_response(
    monkeypatch, tmp_path, capsys
):
    calls = {}

    class FakeDws:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def respond_calendar_event(self, event_id, response_status):
            calls["calendar"] = (event_id, response_status)
            return {"success": True}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(
        db_path=tmp_path / "worker.sqlite3",
        dry_run=False,
        dws_transient_retry_attempts=4,
        dws_transient_retry_delay_seconds=0,
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Calendar", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Calendar",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="[日程]",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="标题足以判断需要接受。",
        calendar_event_id="event-1",
        calendar_response_status="accepted",
        send_status="dry_run",
    )

    result = send_attempt_command(settings, attempt_id)

    assert calls["calendar"] == ("event-1", "accepted")
    assert result["send_status"] == "calendar"
    assert result["calendar_response_status"] == "accepted"
    updated = cli.AutoReplyStore(settings.db_path).get_reply_attempt(attempt_id)
    assert updated is not None
    assert updated.send_status == "calendar"
    assert updated.send_error == ""
    assert updated.calendar_response_result_json == '{"success": true}'
    assert '"calendar_response_status": "accepted"' in capsys.readouterr().out


def test_send_attempt_command_appends_feedback_links_when_configured(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, None)
    enqueue_trigger_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="可以先这样处理。（by明哥分身）",
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    sent_text = sent["reply"][3]
    assert "反馈：[👍](https://feedback.example.com/api/dingtalk-feedback-spike" in sent_text
    assert "source=" not in sent_text
    sent_reply = cli.AutoReplyStore(settings.db_path).get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None
    assert sent_reply.feedback_token.startswith("spike_")
    assert sent_reply.feedback_token in sent_text
    assert '"kind": "native_reply"' in sent_reply.send_result_json


def test_send_attempt_command_sends_single_chat_as_native_reply(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    enqueue_trigger_task(
        store,
        conversation_title="Claire",
        single_chat=True,
        trigger_sender="Claire",
        trigger_text="可以不参加",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
        direct_user_id="user-1",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["reply"] == (
        "cid-1",
        "msg-1",
        "open-sender-1",
        "收到。（by明哥分身）",
    )
    sent_reply = cli.AutoReplyStore(settings.db_path).get_sent_reply("cid-1", "msg-1")
    assert sent_reply is not None


def test_send_attempt_command_resolves_single_chat_trigger_sender_from_recent_message(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def read_recent_messages(self, conversation, limit=50):
            sent["read_recent"] = (conversation.open_conversation_id, limit)
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    sender_open_dingtalk_id="open-1",
                ),
            ]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["read_recent"] == ("cid-1", cli.SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT)
    assert sent["reply"] == ("cid-1", "msg-1", "open-1", "收到。（by明哥分身）")


def test_send_attempt_command_resolves_single_chat_trigger_sender_near_attempt_time(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs
            sent["read_recent"] = []

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def read_recent_messages(self, conversation, limit=50):
            sent["read_recent"].append((conversation.last_message_create_at, limit))
            if conversation.last_message_create_at is None:
                return []
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    sender_open_dingtalk_id="open-1",
                ),
            ]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["read_recent"][0] == (
        None,
        cli.SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT,
    )
    assert sent["read_recent"][1][0] is not None
    assert sent["read_recent"][1][1] == cli.SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT
    assert sent["reply"] == ("cid-1", "msg-1", "open-1", "收到。（by明哥分身）")


def test_send_attempt_command_uses_single_chat_open_dingtalk_id_when_user_id_absent(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def read_recent_messages(self, conversation, limit=50):
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    sender_user_id=None,
                    sender_open_dingtalk_id="open-1",
                ),
            ]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["reply"] == ("cid-1", "msg-1", "open-1", "收到。（by明哥分身）")


def test_send_attempt_command_uses_saved_snake_case_trigger_payload(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Claire",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-28 18:00:00",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        trigger_message_json=json.dumps(
            {
                "open_conversation_id": "cid-1",
                "open_message_id": "msg-1",
                "sender_name": "Claire",
                "sender_open_dingtalk_id": "open-snake-1",
                "create_time": "2026-05-28 18:00:00",
                "content": "可以不参加",
            },
            ensure_ascii=False,
        ),
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["reply"] == (
        "cid-1",
        "msg-1",
        "open-snake-1",
        "收到。（by明哥分身）",
    )


def test_send_attempt_command_requires_trigger_sender_for_native_reply(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def read_recent_messages(self, conversation, limit=50):
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    sender_user_id=None,
                    sender_open_dingtalk_id=None,
                    sender_name="Claire",
                ),
            ]

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    with pytest.raises(SystemExit, match="senderOpenDingTalkId"):
        send_attempt_command(settings, attempt_id)


def test_send_attempt_command_resolves_single_chat_target_forward_from_attempt_time(
    monkeypatch, tmp_path
):
    sent = {}

    class FakeDws:
        def __init__(self, **kwargs):
            sent["kwargs"] = kwargs
            sent["read_recent"] = []
            sent["forward"] = []

        @staticmethod
        def extract_recall_key(send_result):
            return send_result["result"]["processQueryKey"]

        def read_recent_messages(self, conversation, limit=50):
            sent["read_recent"].append((conversation.last_message_create_at, limit))
            return []

        def build_message_list_command(self, conversation, limit, forward):
            sent["forward"].append((conversation.last_message_create_at, limit, forward))
            return {"conversation": conversation, "limit": limit, "forward": forward}

        def run_json(self, command):
            return command

        def parse_messages(self, payload, conversation_title, single_chat):
            return [
                SimpleNamespace(
                    open_message_id="msg-1",
                    sender_user_id=None,
                    sender_open_dingtalk_id="open-1",
                ),
            ]

        def send_reply_to_trigger(self, conversation, trigger, text):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                trigger.sender_open_dingtalk_id,
                text,
            )
            return {"result": {"processQueryKey": "recall-1"}}

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Claire", True, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="可以不参加",
        action="send_reply",
        sensitivity_kind="general",
    )
    final_reply = "> Claire: 可以不参加\n\n收到。（by明哥分身）"
    store.update_reply_attempt(
        attempt_id,
        final_reply_text=final_reply,
        send_status="dry_run",
    )

    send_attempt_command(settings, attempt_id)

    assert sent["forward"][0][0] is not None
    assert sent["forward"][0][1] == cli.SEND_ATTEMPT_TARGET_LOOKBACK_LIMIT
    assert sent["forward"][0][2] is True
    assert sent["reply"] == ("cid-1", "msg-1", "open-1", "收到。（by明哥分身）")


def test_send_attempt_command_regenerates_runtime_leaks_before_sending(
    monkeypatch, tmp_path
):
    sent = {}
    codex_calls = []

    class FakeDws:
        def __init__(self, **kwargs):
            pass

        @staticmethod
        def extract_recall_key(send_result):
            return ""

        def read_recent_messages(self, conversation, limit=20):
            return [
                cli.DingTalkMessage(
                    open_conversation_id=conversation.open_conversation_id,
                    open_message_id="msg-1",
                    conversation_title=conversation.title,
                    single_chat=conversation.single_chat,
                    sender_name="Phina",
                    sender_open_dingtalk_id="sender-open-1",
                    sender_user_id="sender-user-1",
                    create_time="2026-05-13 18:00:00",
                    content="@Alex Chen 看一下",
                )
            ]

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
        ):
            sent["message"] = (
                conversation_id,
                text,
                at_users,
                user_id,
                open_dingtalk_id,
            )
            return {"result": {"processQueryKey": "key-1"}}

        def send_reply_to_trigger(self, conversation, trigger, text, at_users=None):
            sent["reply"] = (
                conversation.open_conversation_id,
                trigger.open_message_id,
                text,
                at_users,
            )
            return {"result": {"processQueryKey": "key-1"}}

    class FakeCodex:
        def __init__(self, workspace, timeout_seconds, idle_timeout_seconds):
            self.workspace = workspace
            self.timeout_seconds = timeout_seconds
            self.idle_timeout_seconds = idle_timeout_seconds

        def decide(self, prompt, session_id, image_paths=None):
            codex_calls.append((prompt, session_id, image_paths))
            return SimpleNamespace(
                action=cli.CodexAction.SEND_REPLY,
                reply_text="改写后可以发送",
            )

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    monkeypatch.setattr(cli, "CodexDecisionRunner", FakeCodex)
    settings = WorkerSettings(db_path=tmp_path / "worker.sqlite3", dry_run=False)
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, None)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="Codex 检索了本地 workspace 后认为可以。（by明哥分身）",
        send_status="dry_run",
    )
    with store._connect() as conn:
        conn.execute(
            "update reply_attempts set codex_session_id=? where id=?",
            ("session-1", attempt_id),
        )

    result = send_attempt_command(settings, attempt_id)

    updated = cli.AutoReplyStore(settings.db_path).get_reply_attempt(attempt_id)
    assert updated is not None
    assert updated.send_status == "sent"
    assert updated.send_error == ""
    assert updated.final_reply_text == "改写后可以发送（by明哥分身）"
    assert result["send_status"] == "sent"
    assert sent["reply"] == (
        "cid-1",
        "msg-1",
        "改写后可以发送（by明哥分身）",
        None,
    )
    assert len(codex_calls) == 1
    assert codex_calls[0][1] == "session-1"
    assert "发送安全检查拦截" in codex_calls[0][0]


def test_max_batches_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("CEO_MAX_BATCHES", "3")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.max_batches == 3


def test_max_batches_can_be_zero_for_empty_smoke_run(monkeypatch):
    monkeypatch.setenv("CEO_MAX_BATCHES", "0")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.max_batches == 0


def test_corpus_dir_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("CEO_CORPUS_DIR", "/tmp/ceo-corpus")
    parser = build_parser()

    args = parser.parse_args(["build-corpus"])
    settings = settings_from_args(args)

    assert str(settings.corpus_dir) == "/tmp/ceo-corpus"


def test_ding_config_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("CEO_DING_ROBOT_CODE", "robot-code")
    monkeypatch.setenv("CEO_DING_RECEIVER_USER_ID", "user-1")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.ding_robot_code == "robot-code"
    assert settings.ding_receiver_user_id == "user-1"


def test_ding_config_uses_dws_standard_env(monkeypatch):
    monkeypatch.delenv("CEO_DING_ROBOT_CODE", raising=False)
    monkeypatch.setenv("DINGTALK_DING_ROBOT_CODE", "dws-robot-code")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.ding_robot_code == "dws-robot-code"


def test_ding_robot_name_defaults_to_none(monkeypatch):
    monkeypatch.delenv("CEO_DING_ROBOT_NAME", raising=False)
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.ding_robot_name is None


def test_ding_robot_name_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("CEO_DING_ROBOT_NAME", "OpenClaw小钉")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.ding_robot_name == "OpenClaw小钉"


def test_parser_supports_refresh_org_cache_command():
    parser = build_parser()

    args = parser.parse_args(["refresh-org-cache", "--user-id", "user-1"])

    assert args.command == "refresh-org-cache"
    assert args.user_id == ["user-1"]


def test_parser_supports_feedback_command():
    parser = build_parser()

    args = parser.parse_args(
        [
            "feedback",
            "--attempt-id",
            "7",
            "--feedback",
            "太武断",
            "--corrected-reply",
            "需要先看材料",
        ]
    )

    assert args.command == "feedback"
    assert args.attempt_id == 7
    assert args.feedback == "太武断"
    assert args.corrected_reply == "需要先看材料"


def test_parser_supports_audit_web_command():
    parser = build_parser()

    args = parser.parse_args(
        [
            "audit-web",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--reload",
            "--reload-interval-seconds",
            "2",
        ]
    )

    assert args.command == "audit-web"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.reload is True
    assert args.reload_interval_seconds == 2


def test_cli_does_not_import_audit_web_until_command_needs_it():
    assert cli.run_audit_web is None


def test_parser_supports_export_feedback_command():
    parser = build_parser()

    args = parser.parse_args(
        ["export-feedback", "--output", "/tmp/feedback.jsonl", "--limit", "20"]
    )

    assert args.command == "export-feedback"
    assert args.output == "/tmp/feedback.jsonl"
    assert args.limit == 20


def test_invalid_dry_run_env_value_fails_fast(monkeypatch):
    monkeypatch.setenv("CEO_DRY_RUN", "treu")

    with pytest.raises(ValueError, match="CEO_DRY_RUN"):
        build_parser()


def test_dry_run_flag_overrides_disabled_dry_run_env(monkeypatch):
    monkeypatch.setenv("CEO_DRY_RUN", "0")
    parser = build_parser()

    args = parser.parse_args(["run-once", "--dry-run"])
    settings = settings_from_args(args)

    assert settings.dry_run is True


def test_not_send_message_env_replaces_dry_run_env(monkeypatch):
    monkeypatch.setenv("CEO_DRY_RUN", "0")
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "1")
    parser = build_parser()

    args = parser.parse_args(["run-once"])
    settings = settings_from_args(args)

    assert settings.dry_run is True


def test_invalid_not_send_message_env_value_fails_fast(monkeypatch):
    monkeypatch.setenv("CEO_NOT_SEND_MESSAGE", "treu")

    with pytest.raises(ValueError, match="CEO_NOT_SEND_MESSAGE"):
        build_parser()


def test_live_send_fails_fast_without_blocker_acceptance(monkeypatch, tmp_path):
    monkeypatch.delenv("CEO_LIVE_SEND_BLOCKERS_ACCEPTED", raising=False)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        dry_run=False,
    )

    with pytest.raises(SystemExit) as exc:
        ensure_live_send_allowed(settings)

    message = str(exc.value)
    assert "CEO_NOT_SEND_MESSAGE=0 is blocked" in message
    assert "deterministic personnel/candidate permission gates" in message
    assert "handoff-clear detection" in message
    assert "batching semantics" in message
    assert "DING handoff delivery" not in message


def test_live_send_allows_guarded_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CEO_LIVE_SEND_BLOCKERS_ACCEPTED", "1")
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        dry_run=False,
    )

    ensure_live_send_allowed(settings)


def test_poll_interval_seconds_must_be_positive():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run-once", "--poll-interval-seconds", "0"])


def test_batch_seconds_must_be_positive():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run-once", "--batch-seconds", "0"])


def test_parser_supports_dws_transient_retry_options():
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-once",
            "--dws-transient-retry-attempts",
            "5",
            "--dws-transient-retry-delay-seconds",
            "0.25",
        ]
    )
    settings = settings_from_args(args)

    assert settings.dws_transient_retry_attempts == 5
    assert settings.dws_transient_retry_delay_seconds == 0.25


def test_parser_supports_codex_timeout_option():
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-once",
            "--codex-timeout-seconds",
            "480",
            "--codex-idle-timeout-seconds",
            "180",
        ]
    )
    settings = settings_from_args(args)

    assert settings.codex_timeout_seconds == 480
    assert settings.codex_idle_timeout_seconds == 180


def test_create_worker_wires_store_dws_codex_and_dry_run(monkeypatch, tmp_path):
    constructed = {}

    class FakeStore:
        def __init__(self, path):
            constructed["store_path"] = path

    class FakeDws:
        def __init__(self, **kwargs):
            constructed["dws"] = self
            constructed["dws_kwargs"] = kwargs

    class FakeCachedOrgDirectory:
        def __init__(self, store):
            constructed["directory_store"] = store

    class FakeCachedDwsClient:
        def __init__(self, dws, org_directory):
            constructed["cached_dws_args"] = (dws, org_directory)

    class FakeCodex:
        def __init__(self, workspace, timeout_seconds, idle_timeout_seconds):
            constructed["codex_workspace"] = workspace
            constructed["codex_timeout_seconds"] = timeout_seconds
            constructed["codex_idle_timeout_seconds"] = idle_timeout_seconds

    class FakeWorker:
        def __init__(
            self,
            store,
            dws,
            codex,
            dry_run,
            style_profile="",
            style_records=None,
        ):
            constructed["worker"] = self
            constructed["worker_args"] = (store, dws, codex, dry_run)
            constructed["style_profile"] = style_profile
            constructed["style_records"] = style_records

    monkeypatch.setattr(cli, "AutoReplyStore", FakeStore)
    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    monkeypatch.setattr(cli, "CachedOrgDirectory", FakeCachedOrgDirectory)
    monkeypatch.setattr(cli, "CachedDwsClient", FakeCachedDwsClient)
    monkeypatch.setattr(cli, "CodexDecisionRunner", FakeCodex)
    monkeypatch.setattr(cli, "DingTalkAutoReplyWorker", FakeWorker)

    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        dry_run=True,
        codex_timeout_seconds=480,
        codex_idle_timeout_seconds=180,
    )
    settings.corpus_dir.mkdir()
    (settings.corpus_dir / "style_profile.md").write_text(
        "# Alex Style Profile\n- 先结论，再解释原因。\n",
        encoding="utf-8",
    )
    append_records(
        settings.corpus_dir / "style_corpus.csv",
        [
            CorpusRecord(
                source_type="dingtalk",
                source_title="Friday",
                timestamp="2026-05-13 18:00:00",
                context="项目排期怎么处理",
                principal_reply="先判断客户价值，再确认负责人、时间点和验收标准，不要只说继续推进。",
                message_id="style-msg-1",
                conversation_id="cid-1",
                speaker_name="明哥",
                metadata_json="{}",
            )
        ],
    )

    worker = create_worker(settings)

    assert worker is constructed["worker"]
    assert constructed["store_path"] == settings.db_path
    assert constructed["dws_kwargs"] == {
        "ding_robot_code": None,
        "ding_robot_name": None,
        "ding_receiver_user_id": None,
        "transient_retry_attempts": 3,
        "transient_retry_delay_seconds": 1.0,
    }
    assert constructed["cached_dws_args"][0] is constructed["dws"]
    assert constructed["codex_workspace"] == settings.workspace
    assert constructed["codex_timeout_seconds"] == 480
    assert constructed["codex_idle_timeout_seconds"] == 180
    assert constructed["worker_args"][3] is True
    assert "先结论" in constructed["style_profile"]
    assert len(constructed["style_records"]) == 1
    assert constructed["style_records"][0].message_id == "style-msg-1"


def test_run_once_command_calls_worker_once(monkeypatch, tmp_path):
    calls = []

    class FakeWorker:
        def run_once(self, max_batches=None):
            calls.append(max_batches)

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        max_batches=3,
    )

    cli.run_once(settings)

    assert calls == [3]


def test_produce_once_command_calls_worker_produce_once(monkeypatch, tmp_path):
    calls = []

    class FakeWorker:
        def produce_once(self, max_tasks=None):
            calls.append(max_tasks)
            return 2

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        max_batches=3,
    )

    queued = cli.produce_once(settings)

    assert queued == 2
    assert calls == [3]


def test_produce_once_records_and_notifies_top_level_failure(monkeypatch, tmp_path):
    notifications = []

    class FakeWorker:
        def produce_once(self, max_tasks=None):
            raise RuntimeError("dws not authenticated")

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())
    monkeypatch.setattr(
        cli,
        "send_macos_notification",
        lambda **kwargs: notifications.append(kwargs),
        raising=False,
    )
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        max_batches=3,
    )

    with pytest.raises(RuntimeError, match="dws not authenticated"):
        cli.produce_once(settings)

    errors = cli.AutoReplyStore(settings.db_path).list_errors(limit=1)
    assert errors[0].kind == "producer"
    assert "dws not authenticated" in errors[0].detail
    assert notifications == [
        {
            "title": "CEO producer failed",
            "message": "dws not authenticated",
        }
    ]


def test_consume_once_command_calls_worker_consume_once(monkeypatch, tmp_path):
    calls = []

    class FakeWorker:
        def consume_once(self, max_tasks=None):
            calls.append(max_tasks)
            return 2

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        max_batches=3,
    )

    processed = cli.consume_once(settings)

    assert processed == 2
    assert calls == [3]


def test_run_once_command_prints_attempt_sent_and_error_deltas(
    monkeypatch, tmp_path, capsys
):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    class FakeWorker:
        def run_once(self, max_batches=None):
            store = cli.AutoReplyStore(settings.db_path)
            store.record_reply_attempt(
                conversation_id="cid-1",
                conversation_title="BA",
                trigger_message_id="msg-1",
                trigger_sender="Phina",
                trigger_text="@Alex Chen 需要看一下吗？",
                action="send_reply",
                sensitivity_kind="general",
                send_status="pending",
            )
            store.record_sent_reply(
                "cid-1",
                "msg-1",
                "可以推进，后面同步一下。（by明哥分身）",
                send_result_json='{"ok": true}',
            )
            store.record_error("cid-2", None, "read_messages", "dws exit code 6")

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())
    monkeypatch.setattr(cli, "local_time_zone_name", lambda: "America/Los_Angeles")

    cli.run_once(settings)

    summary = json.loads(capsys.readouterr().out)
    assert summary["agent_local_timezone"] == "America/Los_Angeles"
    assert summary["counts"] == {
        "reply_attempts": 1,
        "sent_replies": 1,
        "errors": 1,
    }
    assert summary["reply_attempts"][0]["conversation_title"] == "BA"
    assert summary["reply_attempts"][0]["action"] == "send_reply"
    assert summary["sent_replies"][0]["trigger_message_id"] == "msg-1"
    assert summary["errors"][0]["kind"] == "read_messages"
    assert summary["errors"][0]["detail_excerpt"] == "dws exit code 6"


def test_test_ding_command_uses_dws_client(monkeypatch, tmp_path, capsys):
    calls = {}

    class FakeDws:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def ding_self(self, text):
            calls["ding_text"] = text

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        ding_robot_code="robot-code",
        ding_robot_name="极简云机器人",
        ding_receiver_user_id="user-1",
    )

    run_test_ding_command(settings)

    assert calls["kwargs"] == {
        "ding_robot_code": "robot-code",
        "ding_robot_name": "极简云机器人",
        "ding_receiver_user_id": "user-1",
    }
    assert calls["ding_text"] == "CEO agent DING smoke test"
    assert "ding_self: OK" in capsys.readouterr().out


def test_test_ding_command_reports_dws_error(monkeypatch, tmp_path):
    class FakeDws:
        def __init__(self, **kwargs):
            pass

        def ding_self(self, text):
            raise DwsError("robotCode is illegal")

    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    with pytest.raises(SystemExit, match="ding_self: BLOCKED robotCode is illegal"):
        run_test_ding_command(settings)


def test_rerun_message_command_loads_conversation_and_calls_worker(
    monkeypatch, tmp_path, capsys
):
    calls = {}
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, "session-1")

    class FakeWorker:
        def rerun_message(
            self,
            conversation,
            message_id,
            *,
            force_new_decision=False,
            oa_url=None,
        ):
            calls["conversation"] = conversation
            calls["message_id"] = message_id
            calls["force_new_decision"] = force_new_decision
            calls["oa_url"] = oa_url
            return message_id

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    rerun_message_command(
        settings,
        conversation_id="cid-1",
        message_id="msg-1",
        force_new_decision=True,
        context_time="2026-05-20T09:56:09+08:00",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )

    assert calls["conversation"].open_conversation_id == "cid-1"
    assert calls["conversation"].title == "Friday"
    assert calls["conversation"].last_message_create_at == int(
        datetime.fromisoformat("2026-05-20T09:56:09+08:00").timestamp() * 1000
    )
    assert calls["message_id"] == "msg-1"
    assert calls["force_new_decision"] is True
    assert calls["oa_url"] == "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    assert "rerun-message processed conversation_id=cid-1" in capsys.readouterr().out


def test_rerun_message_command_marks_matching_failed_task_done(
    monkeypatch, tmp_path, capsys
):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-20 09:56:09",
        trigger_sender="Claire",
        trigger_text="@Alex 这个怎么处理？",
    )
    task = store.claim_reply_tasks(1)[0]
    store.fail_reply_task(task.id, "old failure")

    class FakeWorker:
        def rerun_message(
            self,
            conversation,
            message_id,
            *,
            force_new_decision=False,
            oa_url=None,
        ):
            return message_id

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    rerun_message_command(
        settings,
        conversation_id="cid-1",
        message_id="msg-1",
        force_new_decision=True,
    )

    loaded = cli.AutoReplyStore(settings.db_path)
    tasks = loaded.list_reply_tasks(limit=1)
    assert tasks[0].status == "done"
    assert tasks[0].error == ""
    assert "rerun-message processed conversation_id=cid-1" in capsys.readouterr().out


def test_rerun_message_command_treats_naive_context_time_as_dingtalk_time(
    monkeypatch, tmp_path, capsys
):
    calls = {}
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, "session-1")

    class FakeWorker:
        def rerun_message(
            self,
            conversation,
            message_id,
            *,
            force_new_decision=False,
            oa_url=None,
        ):
            calls["conversation"] = conversation
            return message_id

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    rerun_message_command(
        settings,
        conversation_id="cid-1",
        message_id="msg-1",
        context_time="2026-05-20 09:56:09",
    )

    assert calls["conversation"].last_message_create_at == int(
        datetime.fromisoformat("2026-05-20T09:56:09+08:00").timestamp() * 1000
    )
    assert "rerun-message processed conversation_id=cid-1" in capsys.readouterr().out


def test_rerun_message_command_fails_for_unknown_conversation(tmp_path):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    with pytest.raises(SystemExit, match="conversation not found: cid-missing"):
        rerun_message_command(
            settings,
            conversation_id="cid-missing",
            message_id="msg-1",
        )


def test_rerun_message_command_reports_missing_message(monkeypatch, tmp_path):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    store.upsert_conversation("cid-1", "Friday", False, "session-1")

    class FakeWorker:
        def rerun_message(
            self,
            conversation,
            message_id,
            *,
            force_new_decision=False,
            oa_url=None,
        ):
            raise ValueError("message not found in recent DingTalk context: msg-1")

    monkeypatch.setattr(cli, "create_worker", lambda settings: FakeWorker())

    with pytest.raises(
        SystemExit, match="message not found in recent DingTalk context: msg-1"
    ):
        rerun_message_command(
            settings,
            conversation_id="cid-1",
            message_id="msg-1",
        )


def test_refresh_org_cache_command_uses_store_and_dws(monkeypatch, tmp_path):
    calls = {}

    class FakeStore:
        def __init__(self, path):
            calls["store_path"] = path

    class FakeDws:
        pass

    def fake_refresh(store, dws, user_ids):
        calls["refresh"] = (store, dws, user_ids)
        return 3

    monkeypatch.setattr(cli, "AutoReplyStore", FakeStore)
    monkeypatch.setattr(cli, "DwsClient", FakeDws)
    monkeypatch.setattr(cli, "refresh_org_cache", fake_refresh)

    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    count = refresh_org_cache_command(settings, {"user-1"})

    assert count == 3
    assert calls["store_path"] == settings.db_path
    assert calls["refresh"][2] == {"user-1"}


def test_record_feedback_command_updates_reply_attempt(tmp_path, capsys):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
    )

    record_feedback_command(
        settings,
        attempt_id=attempt_id,
        feedback="需要更严谨",
        corrected_reply="先看材料再判断。",
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.reviewer_feedback == "需要更严谨"
    assert attempt.corrected_reply_text == "先看材料再判断。"
    assert "feedback recorded attempt_id=1" in capsys.readouterr().out


def test_record_feedback_command_fails_when_attempt_is_missing(tmp_path):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    with pytest.raises(SystemExit, match="reply attempt not found: 99"):
        record_feedback_command(
            settings,
            attempt_id=99,
            feedback="没有这条记录",
        )


def test_run_audit_web_command_uses_db_host_and_port(monkeypatch, tmp_path):
    calls = {}

    def fake_run_audit_web(
        db_path,
        host,
        port,
        ding_robot_code=None,
        ding_robot_name=None,
        reload=False,
        reload_delay_seconds=1,
        reload_dirs=None,
    ):
        calls["args"] = (
            db_path,
            host,
            port,
            ding_robot_code,
            ding_robot_name,
            reload,
            reload_delay_seconds,
            reload_dirs,
        )

    monkeypatch.setattr(cli, "run_audit_web", fake_run_audit_web)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        ding_robot_code="robot-code",
        ding_robot_name="极简云机器人",
    )

    run_audit_web_command(settings, host="127.0.0.1", port=8765)

    assert calls["args"][:6] == (
        settings.db_path,
        "127.0.0.1",
        8765,
        "robot-code",
        "极简云机器人",
        False,
    )
    assert calls["args"][6] == 1
    assert calls["args"][7][0].name == "app"


def test_run_audit_web_command_forwards_uvicorn_reload(monkeypatch, tmp_path):
    calls = {}

    def fake_run_audit_web(
        db_path,
        host,
        port,
        ding_robot_code=None,
        ding_robot_name=None,
        reload=False,
        reload_delay_seconds=1,
        reload_dirs=None,
    ):
        calls["args"] = (
            db_path,
            host,
            port,
            ding_robot_code,
            ding_robot_name,
            reload,
            reload_delay_seconds,
            reload_dirs,
        )

    monkeypatch.setattr(cli, "run_audit_web", fake_run_audit_web)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )

    run_audit_web_command(
        settings,
        host="127.0.0.1",
        port=8765,
        reload=True,
        reload_interval_seconds=2,
    )

    assert calls["args"][:6] == (
        settings.db_path,
        "127.0.0.1",
        8765,
        None,
        None,
        True,
    )
    assert calls["args"][6] == 2
    assert calls["args"][7][0].name == "app"


def test_export_feedback_command_writes_reviewed_attempts_jsonl(tmp_path, capsys):
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
    )
    store = cli.AutoReplyStore(settings.db_path)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Claire",
        trigger_message_id="msg-1",
        trigger_sender="Claire",
        trigger_text="明哥上会啦",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="收到，我现在进会。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="收到，我现在进会。（by明哥分身）",
        send_status="sent",
    )
    store.record_reply_feedback(
        attempt_id,
        feedback="不能代 Alex 声称正在进会",
        corrected_reply_text="我让明哥本人看一下。（by明哥分身）",
    )
    output = tmp_path / "feedback.jsonl"

    written_count = export_feedback_command(settings, output=output, limit=None)

    text = output.read_text(encoding="utf-8")
    assert written_count == 1
    assert '"attempt_id": 1' in text
    assert '"trigger_text": "明哥上会啦"' in text
    assert '"reviewer_feedback": "不能代 Alex 声称正在进会"' in text
    assert '"corrected_reply_text": "我让明哥本人看一下。（by明哥分身）"' in text
    assert "feedback exported count=1" in capsys.readouterr().out


def test_run_loop_calls_run_once_and_sleeps_once():
    calls = []

    class StopLoop(Exception):
        pass

    class FakeWorker:
        def run_once(self, max_batches=None):
            calls.append(max_batches)

    def sleep(seconds):
        calls.append(f"sleep:{seconds}")
        raise StopLoop

    with pytest.raises(StopLoop):
        run_loop(FakeWorker(), poll_interval_seconds=7, max_batches=3, sleep=sleep)

    assert calls == [3, "sleep:7"]


def test_producer_and_consumer_loops_call_separate_methods_once():
    calls = []

    class StopLoop(Exception):
        pass

    class FakeWorker:
        def produce_once(self, max_tasks=None):
            calls.append(f"produce:{max_tasks}")

        def consume_once(self, max_tasks=None):
            calls.append(f"consume:{max_tasks}")

    def sleep(seconds):
        calls.append(f"sleep:{seconds}")
        raise StopLoop

    with pytest.raises(StopLoop):
        run_producer_loop(FakeWorker(), poll_interval_seconds=7, max_tasks=3, sleep=sleep)
    with pytest.raises(StopLoop):
        run_consumer_loop(FakeWorker(), poll_interval_seconds=11, max_tasks=5, sleep=sleep)

    assert calls == [
        "produce:3",
        "sleep:7",
        "consume:5",
        "sleep:11",
    ]


def test_run_service_starts_web_producer_and_consumer(monkeypatch, tmp_path):
    calls = []
    failures = []
    exits = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            calls.append(("start", self.name, self.daemon))
            self.target()

    def stop(component):
        raise RuntimeError(f"stop {component}")

    monkeypatch.setattr(cli, "create_worker", lambda settings: object())
    monkeypatch.setattr(
        cli,
        "run_producer_loop",
        lambda worker, poll_interval_seconds, max_tasks=None: calls.append(
            ("producer", poll_interval_seconds, max_tasks)
        )
        or stop("producer"),
    )
    monkeypatch.setattr(
        cli,
        "run_consumer_loop",
        lambda worker, poll_interval_seconds, max_tasks=None: calls.append(
            ("consumer", poll_interval_seconds, max_tasks)
        )
        or stop("consumer"),
    )
    monkeypatch.setattr(
        cli,
        "run_audit_web_command",
        lambda settings, host, port, reload=False: calls.append(
            ("audit-web", host, port, reload)
        )
        or stop("audit-web"),
    )
    monkeypatch.setattr(
        cli,
        "_record_service_failure",
        lambda settings, component, exc: failures.append((component, str(exc))),
    )

    run_service(
        WorkerSettings(db_path=tmp_path / "worker.sqlite3", max_batches=4),
        host="127.0.0.1",
        port=8765,
        producer_interval_seconds=60,
        consumer_poll_interval_seconds=10,
        thread_factory=FakeThread,
        wait=lambda: calls.append(("wait",)),
        exit_process=lambda status: exits.append(status),
    )

    assert calls == [
        ("start", "ceo-agent-service-producer", True),
        ("producer", 60, 4),
        ("start", "ceo-agent-service-consumer", True),
        ("consumer", 10, 4),
        ("start", "ceo-agent-service-audit-web", True),
        ("audit-web", "127.0.0.1", 8765, False),
        ("wait",),
    ]
    assert failures == [
        ("producer", "stop producer"),
        ("consumer", "stop consumer"),
        ("audit-web", "stop audit-web"),
    ]
    assert exits == [1, 1, 1]


def test_default_poll_interval_is_five_minutes():
    assert WorkerSettings().poll_interval_seconds == 300


def test_build_style_corpus_scans_minutes_and_writes_outputs(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    minutes_dir = workspace / "AI听记"
    nested_dir = minutes_dir / "team"
    nested_dir.mkdir(parents=True)
    (nested_dir / "meeting.md").write_text(
        """# Transcript
同事
00:01
这个怎么排？
明哥
00:02
先看客户价值，再决定投入优先级和负责人，不要只按谁声音大来排。
""",
        encoding="utf-8",
    )
    (minutes_dir / "ignore.txt").write_text("ignore", encoding="utf-8")
    corpus_dir = tmp_path / "corpus"

    count = build_style_corpus(workspace=workspace, corpus_dir=corpus_dir)

    csv_content = (corpus_dir / "style_corpus.csv").read_text(encoding="utf-8")
    profile = (corpus_dir / "style_profile.md").read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert count == 1
    assert "先看客户价值" in csv_content
    assert "先结论" in profile
    assert "build-corpus scanned=1 records=1" in output


def test_build_style_corpus_handles_missing_minutes_dir(tmp_path, capsys):
    corpus_dir = tmp_path / "corpus"

    count = build_style_corpus(workspace=tmp_path / "workspace", corpus_dir=corpus_dir)

    assert count == 0
    assert "build-corpus scanned=0 records=0" in capsys.readouterr().out
    assert (corpus_dir / "style_profile.md").exists()


def test_collect_corpus_fetches_current_user_sender_messages(monkeypatch, tmp_path, capsys):
    class FakeDws:
        def __init__(self):
            self.calls = []

        def get_current_user_id(self):
            return "principal-user-1"

        def list_messages_by_sender(self, sender_user_id, start, end, limit, cursor):
            self.calls.append((sender_user_id, start, end, limit, cursor))
            return {
                "result": {
                    "conversationMessagesList": [
                        {
                            "title": "技术部",
                            "openConversationId": "cid-1",
                            "singleChat": False,
                            "messages": [
                                {
                                    "content": "可以纳入，但主题要围绕业务落地、AI 提效和工程实践闭环，不做单纯算法理论分享。",
                                    "createTime": "2026-05-14 12:01:00",
                                    "openConversationId": "cid-1",
                                    "openMessageId": "msg-1",
                                    "quotedMessage": {"content": "是否可以让算法同学分享？"},
                                    "sender": "明哥",
                                },
                                {
                                    "content": "好的",
                                    "createTime": "2026-05-14 12:02:00",
                                    "openConversationId": "cid-1",
                                    "openMessageId": "msg-short",
                                    "sender": "明哥",
                                },
                            ],
                        }
                    ],
                    "hasMore": False,
                }
            }

    fake_dws = FakeDws()
    monkeypatch.setattr(cli, "DwsClient", lambda: fake_dws)
    settings = WorkerSettings(
        workspace=tmp_path / "workspace",
        db_path=tmp_path / "worker.sqlite3",
        corpus_dir=tmp_path / "corpus",
        dry_run=True,
    )

    count = collect_corpus(settings, target_count=1000)

    csv_content = (settings.corpus_dir / "style_corpus.csv").read_text(encoding="utf-8")
    assert count == 1
    assert fake_dws.calls[0][0] == "principal-user-1"
    assert fake_dws.calls[0][3] == 100
    assert "msg-1" in csv_content
    assert "msg-short" not in csv_content
    assert "collect-corpus sender_user_id=principal-user-1 records=1" in capsys.readouterr().out


def test_probe_dws_reports_unread_ok_and_ding_blocked(monkeypatch, capsys):
    class FakeDws:
        def list_unread_conversations(self, count):
            assert count == 1
            return [object(), object()]

        def ding_self(self, text):
            raise DwsError("DING to self is not configured")

    monkeypatch.setattr(cli, "DwsClient", FakeDws)

    exit_code = probe_dws()

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "unread_conversations: OK count=2" in output
    assert "ding_self: BLOCKED DING to self is not configured" in output


def test_probe_dws_reports_read_blocked_without_crashing(monkeypatch, capsys):
    class FakeDws:
        def list_unread_conversations(self, count):
            raise DwsError("not_authenticated")

        def ding_self(self, text):
            raise DwsError("DING to self is not configured")

    monkeypatch.setattr(cli, "DwsClient", FakeDws)

    exit_code = probe_dws()

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "unread_conversations: BLOCKED not_authenticated" in output
    assert "ding_self: BLOCKED DING to self is not configured" in output
