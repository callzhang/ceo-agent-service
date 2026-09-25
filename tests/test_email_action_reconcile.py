"""Reconciling terminal-failed email move/trash actions against the mailbox.

The mailbox side is a fake IMAP session that fails the test if anything but
LIST, EXAMINE, UID SEARCH or UID FETCH ever reaches it.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from app.email_action_reconcile import (
    AMBIGUOUS,
    ELSEWHERE,
    ERROR,
    IN_SOURCE,
    MAX_CONSECUTIVE_ERRORS,
    NOT_FOUND,
    RECONCILED_OPERATION,
    RECONCILED_RESULT_PREFIX,
    VERIFIED,
    ReadOnlyImapSession,
    ReadOnlyViolation,
    read_only_provider,
    reconcile_email_actions,
)
from app.email_classifier_contracts import EmailAction, EmailClassificationStatus
from app.email_provider_actions import ImapDeterministicProvider
from app.email_store import EmailStore
from tests.test_email_provider_actions import FakeWritableImapSession
from tests.test_email_store import _classification, _persist_scan

ACCOUNT = "dingtalk-account"
FAILED_ERROR = "provider_apply_failed:ImapReadbackUnsupported"


class ReadOnlyFakeSession(FakeWritableImapSession):
    """The fake mailbox: any command that could change it is a test failure."""

    def __init__(
        self,
        *args: object,
        fail_fetch_uids: set[int] | None = None,
        hang_fetch_uids: set[int] | None = None,
        **kw,
    ):
        super().__init__(*args, **kw)
        self.fail_fetch_uids = set(fail_fetch_uids or ())
        self.hang_fetch_uids = set(hang_fetch_uids or ())
        self.released = threading.Event()
        self.fail_everything = False
        self.commands: list[str] = []

    def _no(self, name: str):
        raise AssertionError(f"mutating or unexpected IMAP command reached the server: {name}")

    def select(self, mailbox: str, readonly: bool = False):
        self.commands.append("EXAMINE" if readonly else "SELECT")
        if not readonly:
            self._no("SELECT (read-write)")
        return super().select(mailbox, readonly=readonly)

    def uid(self, command: str, *args: object):
        self.commands.append(f"UID {command}")
        if command not in {"FETCH", "SEARCH"}:
            self._no(f"UID {command}")
        if command == "FETCH" and int(args[0]) in self.hang_fetch_uids:
            # A read that never returns, until the connection is torn down.
            self.released.wait(30)
            raise OSError("connection was shut down while reading")
        if self.fail_everything or (
            command == "FETCH" and int(args[0]) in self.fail_fetch_uids
        ):
            raise TimeoutError("[THROTTLED] provider timed out")
        return super().uid(command, *args)

    def shutdown(self):
        self.released.set()
        super().shutdown()

    def create(self, mailbox: str):
        self._no("CREATE")

    def expunge(self):
        self._no("EXPUNGE")

    def store(self, *args: object):
        self._no("STORE")

    def copy(self, *args: object):
        self._no("COPY")

    def append(self, *args: object):
        self._no("APPEND")

    def delete(self, *args: object):
        self._no("DELETE")


def _seed_failed(
    store: EmailStore,
    message_id: str,
    *,
    uid: int,
    action: EmailAction = EmailAction.MOVE,
    error: str = FAILED_ERROR,
) -> str:
    parameters = (
        {EmailAction.MOVE: {"target_folder": "Projects"}}
        if action is EmailAction.MOVE
        else {EmailAction.TRASH: {}}
    )
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id=message_id,
        actions=(action,),
        action_parameters=parameters,
        uid=uid,
    )
    _persist_scan(store, classification)
    ids = store.direct_action_ids_for_plan(classification.action_plan.action_plan_id)
    assert len(ids) == 1
    claimed = store.claim_direct_action(
        action_id=ids[0], claimed_at="2026-09-25T09:10:00+00:00"
    )
    assert claimed is not None
    store.complete_direct_action_attempt(
        claimed,
        status="failed",
        provider_operation="MOVE" if action is EmailAction.MOVE else "move_to_trash",
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id="",
        error=error,
        finished_at="2026-09-25T09:10:05+00:00",
        retryable=False,
    )
    return ids[0]


def _snapshot(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as db:
        return [
            tuple(row)
            for table in ("email_actions", "email_action_attempts", "email_classifications", "email_messages")
            for row in db.execute(f"select * from {table} order by 1")
        ]


def _action_row(path: Path, action_id: str) -> sqlite3.Row:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "select * from email_actions where action_id=?", (action_id,)
        ).fetchone()


def _factory(session: ReadOnlyFakeSession, opened: list[object] | None = None):
    def connect():
        if opened is not None:
            opened.append(session)
        return read_only_provider(
            ImapDeterministicProvider(session, account_id=ACCOUNT)
        )

    return connect


def _run(store, session, *, apply=False, **options):
    return reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=_factory(session),
        apply=apply,
        sleep=lambda _seconds: None,
        now=lambda: "2026-09-25T10:00:00+00:00",
        **options,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "reconcile.sqlite3"


def test_message_verified_in_target_is_recorded_done_with_reconciliation_evidence(
    db_path: Path,
) -> None:
    store = EmailStore(db_path)
    action_id = _seed_failed(store, "moved", uid=7)
    session = ReadOnlyFakeSession(
        messages={"Projects": {31: ("<moved@example.com>", set())}}
    )

    report = _run(store, session, apply=True)

    assert report.counts == {"move": {VERIFIED: 1}}
    assert report.applied == 1
    row = _action_row(db_path, action_id)
    assert row["status"] == "done"
    assert row["provider_operation"] == RECONCILED_OPERATION
    assert row["provider_result_id"].startswith(RECONCILED_RESULT_PREFIX)
    assert row["error"] == ""
    attempts = store.list_action_attempts(action_id)
    assert [a["status"] for a in attempts] == ["failed", "done"]
    assert attempts[0]["error"] == FAILED_ERROR
    with sqlite3.connect(db_path) as db:
        folder, uidvalidity, uid = db.execute(
            "select folder, uidvalidity, uid from email_classifications"
        ).fetchone()
    assert (folder, uidvalidity, uid) == ("Projects", 126, 31)
    assert all(command in {"EXAMINE", "UID FETCH", "UID SEARCH"} for command in session.commands)


def test_trash_verified_in_the_trash_folder_is_recorded_done(db_path: Path) -> None:
    store = EmailStore(db_path)
    action_id = _seed_failed(store, "binned", uid=8, action=EmailAction.TRASH)
    session = ReadOnlyFakeSession(
        messages={"Trash": {5: ("<binned@example.com>", set())}}
    )

    report = _run(store, session, apply=True)

    assert report.counts == {"trash": {VERIFIED: 1}}
    assert _action_row(db_path, action_id)["status"] == "done"


def test_message_still_in_source_is_left_failed(db_path: Path) -> None:
    store = EmailStore(db_path)
    action_id = _seed_failed(store, "stuck", uid=9)
    session = ReadOnlyFakeSession(
        messages={"INBOX": {9: ("<stuck@example.com>", set())}}
    )
    before = _snapshot(db_path)

    report = _run(store, session, apply=True)

    assert report.counts == {"move": {IN_SOURCE: 1}}
    assert report.applied == 0
    assert _action_row(db_path, action_id)["status"] == "failed"
    assert _snapshot(db_path) == before


def test_message_found_nowhere_is_left_untouched_and_reported(db_path: Path) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "gone", uid=10)
    session = ReadOnlyFakeSession(messages={"INBOX": {}})
    before = _snapshot(db_path)

    report = _run(store, session, apply=True)

    assert report.counts == {"move": {NOT_FOUND: 1}}
    assert "not_found=1" in report.summary()
    assert _snapshot(db_path) == before


def test_message_found_only_in_another_folder_is_left_untouched(db_path: Path) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "wandered", uid=11)
    session = ReadOnlyFakeSession(
        messages={"Archive": {2: ("<wandered@example.com>", set())}}
    )
    before = _snapshot(db_path)

    report = _run(store, session, apply=True)

    assert report.counts == {"move": {ELSEWHERE: 1}}
    assert _snapshot(db_path) == before


def test_duplicate_message_id_in_target_is_ambiguous_and_left_untouched(
    db_path: Path,
) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "twin", uid=12)
    session = ReadOnlyFakeSession(
        messages={
            "Projects": {
                31: ("<twin@example.com>", set()),
                32: ("<twin@example.com>", set()),
            }
        }
    )
    before = _snapshot(db_path)

    report = _run(store, session, apply=True)

    assert report.counts == {"move": {AMBIGUOUS: 1}}
    assert _snapshot(db_path) == before


def test_a_timeout_on_one_message_does_not_abort_the_run(db_path: Path) -> None:
    store = EmailStore(db_path)
    uids = {"first": 21, "second": 22, "third": 23}
    ids = {name: _seed_failed(store, name, uid=uid) for name, uid in uids.items()}
    # Rows run in action_id order; make the first one the message that times out.
    throttled = min(ids, key=ids.get)
    session = ReadOnlyFakeSession(
        messages={
            "Projects": {
                31: ("<first@example.com>", set()),
                32: ("<second@example.com>", set()),
                33: ("<third@example.com>", set()),
            }
        },
        fail_fetch_uids={uids[throttled]},
    )
    opened: list[object] = []
    sleeps: list[float] = []

    report = reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=_factory(session, opened),
        apply=True,
        sleep=sleeps.append,
        now=lambda: "2026-09-25T10:00:00+00:00",
    )

    assert report.aborted is False
    assert report.counts == {"move": {VERIFIED: 2, ERROR: 1}}
    assert report.examined == 3
    statuses = {
        name: _action_row(db_path, action_id)["status"]
        for name, action_id in ids.items()
    }
    assert statuses.pop(throttled) == "failed"
    assert set(statuses.values()) == {"done"}
    assert len(opened) == 2, "the session is replaced after an error"
    assert any(delay >= 1.0 for delay in sleeps), "it backs off after an error"


def test_run_stops_kindly_after_consecutive_errors(db_path: Path) -> None:
    store = EmailStore(db_path)
    for index in range(MAX_CONSECUTIVE_ERRORS + 3):
        _seed_failed(store, f"m{index}", uid=40 + index)
    session = ReadOnlyFakeSession()
    session.fail_everything = True
    before = _snapshot(db_path)

    report = _run(store, session, apply=True)

    assert report.aborted is True
    assert report.examined == MAX_CONSECUTIVE_ERRORS
    assert report.counts == {"move": {ERROR: MAX_CONSECUTIVE_ERRORS}}
    assert _snapshot(db_path) == before


def test_dry_run_writes_nothing_but_reports_what_it_would_record(db_path: Path) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "moved", uid=7)
    session = ReadOnlyFakeSession(
        messages={"Projects": {31: ("<moved@example.com>", set())}}
    )
    before = _snapshot(db_path)

    report = _run(store, session)

    assert report.counts == {"move": {VERIFIED: 1}}
    assert report.applied == 0
    assert _snapshot(db_path) == before


def test_second_run_changes_nothing(db_path: Path) -> None:
    store = EmailStore(db_path)
    moved = _seed_failed(store, "moved", uid=7)
    _seed_failed(store, "stuck", uid=9)
    session = ReadOnlyFakeSession(
        messages={
            "Projects": {31: ("<moved@example.com>", set())},
            "INBOX": {9: ("<stuck@example.com>", set())},
        }
    )

    first = _run(store, session, apply=True)
    after_first = _snapshot(db_path)
    second = _run(store, session, apply=True)

    assert first.applied == 1
    assert second.applied == 0
    assert second.examined == 1
    assert second.counts == {"move": {IN_SOURCE: 1}}
    assert _snapshot(db_path) == after_first
    assert _action_row(db_path, moved)["attempt_count"] == 2


def test_store_reopens_cleanly_and_keeps_its_invariants_after_apply(
    db_path: Path,
) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "moved", uid=7)
    _seed_failed(store, "stuck", uid=9)
    _seed_failed(store, "gone", uid=10)
    session = ReadOnlyFakeSession(
        messages={
            "Projects": {31: ("<moved@example.com>", set())},
            "INBOX": {9: ("<stuck@example.com>", set())},
        }
    )

    _run(store, session, apply=True)
    EmailStore(db_path)  # its consistency check runs on open and would raise

    with sqlite3.connect(db_path) as db:
        pending_with_history = db.execute(
            """
            select count(*) from email_actions as a
            where a.status='pending' and (a.attempt_count != 0 or exists (
                select 1 from email_action_attempts t where t.action_id=a.action_id))
            """
        ).fetchone()[0]
        drift = db.execute(
            """
            select count(*) from email_actions as a
            where a.attempt_count != (
                select count(*) from email_action_attempts t where t.action_id=a.action_id)
            """
        ).fetchone()[0]
        statuses = sorted(r[0] for r in db.execute("select status from email_actions"))
    assert pending_with_history == 0
    assert drift == 0
    assert statuses == ["done", "failed", "failed"]


def test_a_recording_failure_does_not_leave_the_row_processing(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EmailStore(db_path)
    action_id = _seed_failed(store, "moved", uid=7)
    session = ReadOnlyFakeSession(
        messages={"Projects": {31: ("<moved@example.com>", set())}}
    )
    real = store.complete_direct_action_attempt
    calls: list[str] = []

    def flaky(action, **kwargs):
        calls.append(kwargs["status"])
        if kwargs["status"] == "done":
            raise RuntimeError("disk hiccup")
        return real(action, **kwargs)

    monkeypatch.setattr(store, "complete_direct_action_attempt", flaky)

    report = _run(store, session, apply=True)

    assert calls == ["done", "failed"]
    assert report.record_failed == 1
    assert _action_row(db_path, action_id)["status"] == "failed"
    EmailStore(db_path)


def test_limit_and_after_page_through_failed_rows(db_path: Path) -> None:
    store = EmailStore(db_path)
    for index in range(5):
        _seed_failed(store, f"page{index}", uid=60 + index)
    session = ReadOnlyFakeSession()
    seen: list[str] = []
    after = ""
    for _ in range(3):
        report = _run(store, session, limit=2, after_action_id=after)
        if not report.examined:
            break
        seen.append(report.next_after)
        after = report.next_after
    assert [_run(store, session, limit=2, after_action_id="").examined] == [2]
    assert len(seen) == 3
    assert seen == sorted(seen)
    assert _run(store, session, limit=2, after_action_id=seen[-1]).examined == 0


def test_only_the_current_plan_of_the_named_account_is_examined(db_path: Path) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "mine", uid=70)
    session = ReadOnlyFakeSession()

    other = reconcile_email_actions(
        store,
        account_id="another-account",
        provider_factory=_factory(session),
        sleep=lambda _s: None,
    )

    assert other.examined == 0


def test_claim_for_reconciliation_only_takes_the_same_failed_row(db_path: Path) -> None:
    store = EmailStore(db_path)
    action_id = _seed_failed(store, "moved", uid=7)

    assert (
        store.claim_failed_direct_action_for_reconciliation(
            action_id=action_id,
            expected_attempt_count=0,
            claimed_at="2026-09-25T10:00:00+00:00",
        )
        is None
    ), "a stale view of the attempt count is refused"
    claimed = store.claim_failed_direct_action_for_reconciliation(
        action_id=action_id,
        expected_attempt_count=1,
        claimed_at="2026-09-25T10:00:00+00:00",
    )
    assert claimed is not None and claimed.attempt_number == 2
    assert (
        store.claim_failed_direct_action_for_reconciliation(
            action_id=action_id,
            expected_attempt_count=1,
            claimed_at="2026-09-25T10:00:01+00:00",
        )
        is None
    ), "a row already processing is not claimed twice"


def test_pending_and_done_rows_are_not_listed_or_claimable(db_path: Path) -> None:
    store = EmailStore(db_path)
    classification = _classification(
        status=EmailClassificationStatus.PROCESSED,
        message_id="fresh",
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Projects"}},
        uid=80,
    )
    _persist_scan(store, classification)
    (pending_id,) = store.direct_action_ids_for_plan(
        classification.action_plan.action_plan_id
    )

    assert (
        store.list_failed_direct_actions(
            account_id=ACCOUNT, action_types=(EmailAction.MOVE,), limit=10
        )
        == []
    )
    assert (
        store.claim_failed_direct_action_for_reconciliation(
            action_id=pending_id,
            expected_attempt_count=0,
            claimed_at="2026-09-25T10:00:00+00:00",
        )
        is None
    )
    assert _action_row(db_path, pending_id)["status"] == "pending"


def test_read_only_session_refuses_every_mutating_command() -> None:
    fake = ReadOnlyFakeSession()
    session = ReadOnlyImapSession(fake)

    for command in ("STORE", "MOVE", "COPY", "EXPUNGE"):
        with pytest.raises(ReadOnlyViolation):
            session.uid(command, "1", "Projects")
    with pytest.raises(ReadOnlyViolation):
        session.select("INBOX")
    with pytest.raises(ReadOnlyViolation):
        session.select("INBOX", readonly=False)
    for name in ("store", "copy", "expunge", "append", "create", "delete", "rename"):
        with pytest.raises(ReadOnlyViolation):
            getattr(session, name)
    assert fake.commands == []

    assert session.list()[0] == "OK"
    assert session.select("INBOX", readonly=True)[0] == "OK"
    assert session.uid("SEARCH", None, "HEADER", "Message-ID", "<x>")[0] == "OK"
    assert fake.commands == ["EXAMINE", "UID SEARCH"]


def test_command_prints_counts_only(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import app.cli as cli

    store = EmailStore(db_path)
    _seed_failed(store, "moved", uid=7)
    session = ReadOnlyFakeSession(
        messages={"Projects": {31: ("<moved@example.com>", set())}}
    )
    monkeypatch.setattr(
        "app.email_action_reconcile.build_gmail_read_only_provider_factory",
        lambda *_args: _factory(session),
    )

    cli.reconcile_email_actions_command(
        SimpleNamespace(db_path=db_path),
        account_id=ACCOUNT,
        apply=False,
        limit=10,
        after_action_id="",
        pause_seconds=0,
    )

    out = capsys.readouterr().out
    assert "mode=dry-run" in out
    assert "move[verified=1" in out
    assert "example.com" not in out


def test_a_hung_read_on_one_message_is_cut_off_and_the_run_finishes(
    db_path: Path,
) -> None:
    store = EmailStore(db_path)
    uids = {"a": 21, "b": 22, "c": 23}
    ids = {name: _seed_failed(store, name, uid=uid) for name, uid in uids.items()}
    hung = min(ids, key=ids.get)
    messages = {
        "Projects": {
            31: ("<a@example.com>", set()),
            32: ("<b@example.com>", set()),
            33: ("<c@example.com>", set()),
        }
    }
    hanging = ReadOnlyFakeSession(messages=messages, hang_fetch_uids={uids[hung]})
    healthy = ReadOnlyFakeSession(messages=messages)
    sessions = [hanging, healthy]

    def connect():
        return read_only_provider(
            ImapDeterministicProvider(sessions.pop(0), account_id=ACCOUNT)
        )

    started = time.monotonic()
    report = reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=connect,
        apply=True,
        message_timeout_seconds=0.3,
        sleep=lambda _s: None,
        now=lambda: "2026-09-25T10:00:00+00:00",
    )

    assert time.monotonic() - started < 10, "a hung read must not block the run"
    assert report.aborted is False
    assert report.counts == {"move": {VERIFIED: 2, ERROR: 1}}
    assert hanging.released.is_set(), "the stuck connection was torn down"
    statuses = {n: _action_row(db_path, i)["status"] for n, i in ids.items()}
    assert statuses.pop(hung) == "failed"
    assert set(statuses.values()) == {"done"}


def test_a_hung_connect_counts_as_an_error_and_does_not_block(db_path: Path) -> None:
    store = EmailStore(db_path)
    _seed_failed(store, "a", uid=21)
    release = threading.Event()

    def connect():
        release.wait(30)
        raise OSError("connect torn down")

    started = time.monotonic()
    report = reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=connect,
        message_timeout_seconds=0.3,
        sleep=lambda _s: None,
    )
    release.set()

    assert time.monotonic() - started < 10
    assert report.counts == {"move": {ERROR: 1}}


def test_progress_is_reported_every_25_rows_and_ctrl_c_returns_partial_counts(
    db_path: Path,
) -> None:
    store = EmailStore(db_path)
    for index in range(26):
        _seed_failed(store, f"p{index}", uid=100 + index)
    session = ReadOnlyFakeSession()
    seen: list[str] = []

    report = reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=_factory(session),
        progress=lambda running: seen.append(running.summary()),
        sleep=lambda _s: None,
    )

    assert report.examined == 26
    assert len(seen) == 1 and "examined=25" in seen[0]

    interrupted = reconcile_email_actions(
        store,
        account_id=ACCOUNT,
        provider_factory=_factory(session),
        sleep=lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert interrupted.interrupted is True
    assert interrupted.examined == 1  # the pause before row two raised
