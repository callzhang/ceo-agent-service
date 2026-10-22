"""Read migration-affected Console APIs against an operator-supplied copy.

No application lifespan, workers, native model calls or outbound execution.
Email source reads use a substitute; Python network connections are blocked.
The report contains counts and public identities, never message bodies.
"""
import argparse
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fingerprint(path):
    """Compare every persisted SQL value, including BLOBs, after app startup."""
    result = {}
    with closing(sqlite3.connect(path)) as db:
        for (name,) in db.execute("select name from sqlite_schema where type='table' order by name"):
            digest = hashlib.sha256()
            count = 0
            for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
                for value in row:
                    if isinstance(value, bytes):
                        data = value
                    else:
                        data = repr(value).encode()
                    digest.update(type(value).__name__.encode() + b":" + str(len(data)).encode() + b":" + data)
                count += 1
            result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def validate(path):
    from fastapi.testclient import TestClient
    from app.audit_web import create_audit_app
    from app.logical_run_storage import logical_run_projection

    source_reads = []

    class UnavailableSource:
        def read_views(self, account_id, identities):
            source_reads.append(len(identities))
            raise LookupError("validation substitute: exact original unavailable")

    original_connect = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("copy validation forbids provider/network connections")
        return original_connect(sock, address)

    results = []
    with ExitStack() as stack:
        stack.enter_context(patch("socket.socket.connect", connect))
        stack.enter_context(patch("app.email_source_reader.build_email_source_reader", return_value=UnavailableSource()))
        app = create_audit_app(path, workbench_workspace=path.parent, spa_enabled=True)
        # Do not enter the client context: it starts the app's background lifespan.
        client = TestClient(app)
        stack.callback(client.close)
        before = fingerprint(path)

        def get(url, expected=200):
            response = client.get(url)
            assert response.status_code == expected, (url, response.status_code, response.text[:200])
            value = response.json()
            results.append({"url": url, "http": response.status_code, "items": len(value.get("items", []))})
            return value

        # History combines queue/action/specialized Run projections. Both pages
        # and a filtered older population must be readable under the same schema.
        history = get("/api/console/history?page=1&page_size=20&include_chart=false")
        older = get("/api/console/history?page=2&page_size=20&include_chart=false")
        assert len({row["id"] for row in history["items"] + older["items"]}) == 40
        get("/api/console/history?object_type=meeting&page_size=10&include_chart=false")
        get("/api/console/attention")
        get("/api/console/dashboard/queues")
        get("/api/console/tasks/all?page_size=5")
        get("/api/console/tasks/projects?page_size=5")
        legacy = get("/api/console/tasks/legacy-projects/54")
        item = legacy["item"]
        assert item["project"]["id"] == 54
        assert item["todos"] and item["updates"]
        retired = {"priority", "risk_level", "needs_derek_attention", "next_follow_up_at", "follow_up_mode", "confidence", "merge_reason"}
        for row in [item["project"], *item["todos"], *item["updates"]]:
            assert not retired.intersection(row), retired.intersection(row)
        get("/api/console/tasks/legacy-projects/9007199254740991", 404)
        get("/api/console/tasks/sent-todos?page_size=10")
        with closing(sqlite3.connect(path)) as db:
            task = db.execute("select public_id from business where record_type='task' order by id desc limit 1").fetchone()
            meeting = db.execute(
                f"select id, common_run_id from ({logical_run_projection('meeting_alignment_runs')}) h "
                "where id != common_run_id and exists(select 1 from tasks p where p.id=h.task_id) "
                "order by id desc limit 1"
            ).fetchone()
            attempt = db.execute("select legacy_key from actions where record_type='reply_attempts' order by id desc limit 1").fetchone()
        if task:
            detail = get(f"/api/console/tasks/items/{task[0]}")
            assert detail["item"]["summary"]["id"] == task[0]
        assert meeting, "source must contain a preserved public/physical Run distinction"
        detail = get(f"/api/console/meeting-attempts/{meeting[0]}")
        assert detail["item"]["id"] == meeting[0] and meeting[0] != meeting[1]
        if attempt:
            get(f"/api/console/history/{attempt[0]}")
        email = get("/api/console/email/classifications?status=all&page_size=10")
        assert email["items"]
        assert source_reads == [], "preview list must not fetch original"
        for row in email["items"]:
            assert len(row["preview"]) <= 1000
            assert "mailbox_actions" in row and "provider_classification" in row
        missing = get(f"/api/console/email/classifications/{email['items'][0]['id']}", 502)
        assert missing["code"] == "email_original_unavailable"
        assert source_reads
        after = fingerprint(path)
        assert before == after, "GET traversal modified persisted facts"
        assert history["items"]
    return {"checks": results, "tables": before, "persisted_values_unchanged": True,
            "provider_substitute_reads": len(source_reads), "real_sends": 0,
            "lifespan_started": False, "legacy_todos": len(item["todos"]), "legacy_updates": len(item["updates"]),
            "meeting_public_id": meeting[0], "meeting_physical_id": meeting[1]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.database.resolve())
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "tables"}))
