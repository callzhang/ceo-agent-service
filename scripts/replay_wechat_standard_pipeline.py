#!/usr/bin/env python3
"""Replay recent real WeChat tasks through the standard Consumer -> Audit flow.

Offline and send-free: it works on a copy of the service database, replaces the
``wechat / send_message`` handler with one that runs the real delivery gate but
records the text instead of sending, reads each conversation as it stood when
the legacy path decided, and runs with a shadow HOME that carries the draft
``ceo-wechat`` Skill and has Memory writes switched off. The live database,
installed Skills, Codex config and WeChat conversations are only read.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIVE_DB = Path.home() / "Library/Application Support/ceo-agent-service/auto-reply.sqlite3"
SERVICE_ROOT = Path.home() / "Services/ceo-agent-service"
ENV_FILE = SERVICE_ROOT / ".env"
DRAFT_SKILL = ROOT / "ci/shared-skills/ceo-wechat"
NOT_FROM_ENV = {"CEO_WORKER_DB", "CEO_WECHAT_SENDER_ENABLED", "CEO_WECHAT_SEND_MODE", "CEO_WORKSPACE"}


def backup_database(destination: Path) -> None:
    source = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=60)
    target = sqlite3.connect(destination)
    with target:
        source.backup(target)
    source.close()
    target.close()


def build_shadow_home(shadow: Path) -> None:
    """HOME where everything is the real thing except the Skill tree and Memory writes."""
    real = Path.home()
    shadow.mkdir(parents=True, exist_ok=True)
    for entry in real.iterdir():
        if entry.name in {".agents", ".codex", "Library"} or entry.name.startswith(".claude.json.tmp"):
            continue
        link = shadow / entry.name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(entry)
    # Real directories down to the Reader socket, so the proxy can listen there
    # without ever touching the real socket; everything else is linked.
    chain = [Path("Library"), Path("Library/Application Support"), Path("Library/Application Support/CEO Agent")]
    for rel in chain:
        (shadow / rel).mkdir(exist_ok=True)
        for entry in (real / rel).iterdir():
            if any(entry == real / deeper for deeper in chain) or entry.name == "WeChatReader" and rel == chain[-1]:
                continue
            link = shadow / rel / entry.name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(entry)
    (shadow / chain[-1] / "WeChatReader").mkdir(exist_ok=True)
    agents = shadow / ".agents"
    agents.mkdir(exist_ok=True)
    for entry in (real / ".agents").iterdir():
        if entry.name == "skills":
            continue
        if not (agents / entry.name).exists():
            (agents / entry.name).symlink_to(entry)
    skills = agents / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    shutil.copytree(real / ".agents/skills", skills, symlinks=True)
    target = skills / "ceo-wechat"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DRAFT_SKILL, target)
    codex = shadow / ".codex"
    codex.mkdir(exist_ok=True)
    for entry in (real / ".codex").iterdir():
        if entry.name == "config.toml":
            continue
        if not (codex / entry.name).exists():
            (codex / entry.name).symlink_to(entry)
    config = (real / ".codex/config.toml").read_text(encoding="utf-8")
    marker = '[mcp_servers.memory_connector]\n'
    if marker not in config:
        raise SystemExit("memory_connector entry not found in Codex config")
    config = config.replace(marker, marker + 'disabled_tools = ["memory_write", "document_upload"]\n', 1)
    (codex / "config.toml").write_text(config, encoding="utf-8")


def load_service_env(shadow: Path, scratch_db: Path, workspace: Path) -> None:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if key and key not in NOT_FROM_ENV:
            value = value.strip().strip("'\"")
            # The service runs from its own checkout, so relative paths in its .env
            # are relative to that checkout, not to this one.
            if value and not value.startswith("/") and (SERVICE_ROOT / value).exists():
                value = str(SERVICE_ROOT / value)
            os.environ.setdefault(key, value)
    os.environ.update({
        "HOME": str(shadow), "CODEX_HOME": str(shadow / ".codex"),
        "CEO_WORKER_DB": str(scratch_db), "CEO_WORKSPACE": str(workspace),
        "CEO_WECHAT_SENDER_ENABLED": "0", "CEO_WECHAT_SEND_MODE": "off",
        "CEO_WECHAT_READER_ENABLED": "1", "PYTHONPATH": str(ROOT),
        # Reads go through the as-of proxy at the default path under the shadow HOME;
        # the Sender socket deliberately points nowhere.
        "CEO_WECHAT_SENDER_SOCKET": "/tmp/replay-no-sender.sock",
    })


def sample(count: int) -> list[dict]:
    from app.action_storage import task_delivery_projection

    db = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        f"select id, reply_task_id, account_id, target_type, target_id, status, reply_text, created_at, error "
        f"from ({task_delivery_projection('wechat_deliveries')}) order by id desc limit 200"
    ).fetchall()
    db.close()
    seen: dict[int, dict] = {}
    for row in rows:
        seen.setdefault(int(row["reply_task_id"]), dict(row))
    by_status: dict[str, list[dict]] = {}
    for item in seen.values():
        by_status.setdefault(item["status"], []).append(item)
    quota = {"superseded": count * 2 // 5, "skipped": count // 5}
    chosen: list[dict] = []
    for status, limit in quota.items():
        chosen += by_status.get(status, [])[:limit]
    others = [i for s, items in by_status.items() if s not in quota for i in items]
    chosen += others[: count - len(chosen)]
    return sorted(chosen, key=lambda item: item["id"])[:count]


def as_of(created_at: str) -> str:
    stamp = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (stamp + timedelta(seconds=1)).isoformat()


class ProxyReader:
    """Reader facade that only ever shows a conversation as of one moment."""

    def __init__(self, reader):
        self.reader, self.until = reader, ""

    def read_messages(self, account, **kwargs):
        kwargs["until"] = self.until
        return self.reader.read_messages(account, **kwargs)

    def __getattr__(self, name):
        return getattr(self.reader, name)


def serve_proxy(shadow: Path, reader: ProxyReader, real_reader):
    import threading

    from app.wechat.reader_ipc import WechatReaderRpcService, WechatReaderUnixServer

    socket_path = shadow / "Library/Application Support/CEO Agent/WeChatReader/reader.sock"
    server = WechatReaderUnixServer(
        socket_path, WechatReaderRpcService(reader, real_reader.discover_accounts))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def freeze_clock(moment: datetime):
    """The Consumer's 'current time' is the moment the legacy path decided."""
    import app.agent_context as agent_context
    import app.consumer_agent as consumer_agent

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.astimezone()

    for module in (agent_context, consumer_agent):
        module.datetime = Frozen


def recording_handler(store, sent: list[dict]):
    """The real gate up to the send; the text is recorded instead of sent."""
    from app.agent_contracts import ProposedAction
    from app.system_action_handlers import ActionOutcome, WechatSendMessageHandler

    class Recorder(WechatSendMessageHandler):
        def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict) -> ActionOutcome:
            task, prepared = self._task_and_prepared(action, candidate)
            if task is None or prepared is None:
                return ActionOutcome("failed", {"reason": "replay_prepared_mismatch"})
            delivery_id = self.store.create_standard_wechat_delivery(
                candidate_id=candidate["candidate_id"], review_id=candidate["review_id"],
                action_identity=action.action_identity,
            )
            approved = self.store.wechat_delivery_review_approved(delivery_id)
            sent.append({"text": action.payload["text"], "gate_approved": bool(approved)})
            return ActionOutcome("verified", {"delivery_id": delivery_id, "replay": True})

    return Recorder(store)


def describe(db_path: Path, task_id: int) -> dict:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    out: dict = {"consumer": [], "audit": []}
    for run in db.execute("select id, role, status, structured_error_json from runs where task_id=? order by id", (task_id,)):
        if run["status"] == "failed":
            out.setdefault("failed_runs", []).append((run["role"], (run["structured_error_json"] or "")[:300]))
    for row in db.execute(
        "select r.consumer_run_id, r.candidate_json from run_adopted_results r "
        "join runs u on u.id=r.consumer_run_id where u.task_id=? order by r.consumer_run_id", (task_id,)):
        cand = json.loads(row["candidate_json"])
        po = cand.get("purpose_output") or {}
        actions = [(a.get("capability"), a.get("operation"), (a.get("payload") or {}).get("text"))
                   for a in ((cand.get("proposal") or {}).get("actions") or [])]
        out["consumer"].append({"outcome": cand.get("outcome"), "action": po.get("action"),
                                "reason": po.get("reason"), "actions": actions,
                                "confidence": cand.get("confidence")})
    for row in db.execute(
        "select a.decision, a.result_json from run_adopted_reviews a "
        "join runs u on u.id=a.consumer_run_id where u.task_id=? order by a.id", (task_id,)):
        res = json.loads(row["result_json"])
        out["audit"].append({"decision": row["decision"], "summary": res.get("summary"),
                             "feedback": res.get("feedback")})
    db.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--home", type=Path, default=Path("/private/tmp/wxh"),
                        help="short on purpose: the Reader socket path must fit a unix socket")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--only", type=int, nargs="*", help="legacy reply_task ids to replay")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    scratch = args.scratch
    scratch.mkdir(parents=True, exist_ok=True)
    db_path, shadow, workspace = scratch / "replay.sqlite3", args.home, scratch / "workspace"
    workspace.mkdir(exist_ok=True)
    if not db_path.exists():
        backup_database(db_path)
    if not (shadow / ".codex").exists():
        build_shadow_home(shadow)
    picked = sample(args.count)
    if args.only:
        picked = [item for item in picked if item["reply_task_id"] in set(args.only)]
    (scratch / "sample.json").write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.prepare_only:
        print(f"prepared {len(picked)} tasks in {scratch}")
        return 0

    from app.wechat.reader_ipc import WechatReaderClient

    real_reader = WechatReaderClient(
        Path.home() / "Library/Application Support/CEO Agent/WeChatReader/reader.sock", timeout_seconds=120)
    load_service_env(shadow, db_path, workspace)
    from app.cli import WorkerSettings, create_worker, initialize_agent_runtime_routes, _resolve_service_runtime_skills
    from app.store import AutoReplyStore
    from app.wechat import service as wechat_service

    proxy = ProxyReader(real_reader)
    server = serve_proxy(shadow, proxy, real_reader)
    settings = WorkerSettings(workspace=workspace, db_path=db_path)
    snapshot = _resolve_service_runtime_skills(settings)
    refresher = initialize_agent_runtime_routes(settings)
    worker = create_worker(settings, runtime_refresher=refresher, runtime_skill_snapshot=snapshot)
    store = worker.store
    sent: list[dict] = []
    orchestrator = worker._agent_orchestrator()
    orchestrator.system_executor.handlers[("wechat", "send_message")] = recording_handler(store, sent)
    account = wechat_service.account_from_state(wechat_service.ready_account_state(store))

    results = []
    try:
        for legacy in picked:
            src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=60)
            src.row_factory = sqlite3.Row
            task_row = AutoReplyStore._native_wechat_task(src, int(legacy["reply_task_id"]))
            src.close()
            if not task_row["trigger_text"] or not task_row["trigger_message_json"]:
                # Older tasks no longer carry their trigger; the conversation still does.
                found = [m for m in real_reader.read_messages(
                    account, conversation_id=task_row["conversation_id"],
                    conversation_type="direct" if task_row["single_chat"] else "group",
                    since=task_row["trigger_create_time"], until=task_row["trigger_create_time"],
                    limit=100, order="oldest") if m.message_id == task_row["trigger_message_id"]]
                if not found:
                    results.append({"legacy": legacy, "replay_status": "trigger_unrecoverable"})
                    continue
                task_row = dict(task_row, trigger_text=found[0].text,
                                trigger_message_json=found[0].model_dump_json())
            replay_id = f"{task_row['trigger_message_id']}@replay-{int(time.time())}"
            store.enqueue_reply_task(
                channel="wechat", conversation_id=task_row["conversation_id"],
                conversation_title=task_row["conversation_title"], single_chat=bool(task_row["single_chat"]),
                trigger_message_id=replay_id, trigger_create_time=task_row["trigger_create_time"],
                trigger_sender=task_row["trigger_sender"], trigger_text=task_row["trigger_text"],
                trigger_message_json=task_row["trigger_message_json"],
            )
            with store._connect() as db:
                new_id = int(db.execute(
                    "select task_id from task_inputs where channel='wechat' and conversation_id=? and trigger_message_id=?",
                    (task_row["conversation_id"], replay_id)).fetchone()[0])
            # Each replay starts a fresh Consumer session: the conversation's session
            # would otherwise carry earlier replays (and older Skill text) into this one.
            store.clear_conversation_runtime_sessions(task_row["conversation_id"])
            task = store.claim_reply_task(new_id)
            sent.clear()
            proxy.until = as_of(legacy["created_at"])
            freeze_clock(datetime.fromisoformat(proxy.until))
            entry = {"legacy": legacy, "trigger_text": task_row["trigger_text"], "replay_task_id": new_id}
            try:
                worker.process_claimed_wechat_task(task, reader=proxy, account=account)
            except Exception as exc:  # noqa: BLE001 - a replay failure is itself a result
                entry["exception"] = f"{type(exc).__name__}: {exc}"[:500]
            final = store.get_reply_task(new_id)
            entry.update(replay_status=getattr(final, "status", "?"), replay_error=getattr(final, "error", ""),
                         replay_sent=list(sent), **describe(db_path, new_id))
            results.append(entry)
            (scratch / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            print(f"[{len(results)}/{len(picked)}] legacy={legacy['status']} replay={entry['replay_status']} sent={len(sent)}", flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
