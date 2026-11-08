from app.automatic_repair import AutomaticRepairCoordinator
from app.automatic_repair_store import get_repair, save_repair
from tests.test_automatic_repair_gate import coordinator, finish, store as store


def snapshot(*, head="abc", checks=None, threads=None, reviews=None, state="OPEN"):
    return dict(
        url="https://github.com/example/service/pull/9",
        headRefOid=head,
        headRefName="codex/repair-errors-1",
        state=state,
        reviewDecision="",
        checks=checks or [],
        threads=threads or [],
        reviews=reviews or [],
        latest_reviews=reviews or [],
    )


def failed_check(name="tests", run="run1"):
    return dict(
        __typename="CheckRun",
        id=run,
        name=name,
        status="COMPLETED",
        conclusion="FAILURE",
        detailsUrl="https://github.com/example/service/actions/runs/1/job/2",
    )


def attached(store, tmp_path):
    store.record_error(None, None, "failure", "real failure")
    identity = store.list_errors(limit=1)[0].id
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, "errors", identity)
    finish(store, entry["turn_ids"][0])
    entry.update(status="pr_created", pr_url=snapshot()["url"], pr_head="abc")
    save_repair(store, entry)
    return identity, entry


def poll(store, tmp_path, data):
    AutomaticRepairCoordinator(
        store,
        repository=tmp_path,
        pr_reader=lambda branch: [],
        feedback_reader=lambda url: data,
        interval_seconds=0,
        pr_poll_seconds=0,
    ).run_once()


def test_ci_failure_resumes_same_conversation_and_deduplicates_restart(store, tmp_path):
    identity, before = attached(store, tmp_path)
    data = snapshot(checks=[failed_check()])
    poll(store, tmp_path, data)
    entry = get_repair(store, "errors", identity)
    assert entry["task_id"] == before["task_id"]
    assert len(entry["turn_ids"]) == 2
    turn = store.get_turn(entry["turn_ids"][-1])
    assert "FAILURE" in turn.user_text and "run1" in turn.user_text
    assert before["pr_url"] in turn.user_text and "abc" in turn.user_text
    poll(store, tmp_path, data)
    assert get_repair(store, "errors", identity)["turn_ids"] == entry["turn_ids"]


def test_pending_waits_then_failure_and_new_commit_are_observed(store, tmp_path):
    identity, before = attached(store, tmp_path)
    poll(
        store,
        tmp_path,
        snapshot(checks=[dict(failed_check(), status="IN_PROGRESS", conclusion="")]),
    )
    assert get_repair(store, "errors", identity)["status"] == "awaiting_checks"
    assert get_repair(store, "errors", identity)["turn_ids"] == before["turn_ids"]
    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    entry = get_repair(store, "errors", identity)
    finish(store, entry["turn_ids"][-1])
    poll(
        store,
        tmp_path,
        snapshot(
            head="def", checks=[dict(failed_check(), status="QUEUED", conclusion="")]
        ),
    )
    assert get_repair(store, "errors", identity)["status"] == "awaiting_checks"
    poll(
        store,
        tmp_path,
        snapshot(head="def", checks=[dict(failed_check(), conclusion="SUCCESS")]),
    )
    assert get_repair(store, "errors", identity)["status"] == "checks_passed"
    assert len(get_repair(store, "errors", identity)["turn_ids"]) == 2
    assert not store.get_error(identity).resolved_at


def test_unresolved_review_with_green_ci_resumes_and_resolved_review_passes(
    store, tmp_path
):
    identity, _ = attached(store, tmp_path)
    thread = dict(
        id="thread1",
        isResolved=False,
        isOutdated=True,
        path="app/store.py",
        line=9,
        comments=[
            dict(
                id="comment1",
                body="Fix the remaining consumers",
                url="https://github.com/review",
                updatedAt="today",
            )
        ],
    )
    good = dict(failed_check(), conclusion="SUCCESS")
    poll(store, tmp_path, snapshot(checks=[good], threads=[thread]))
    entry = get_repair(store, "errors", identity)
    assert (
        "Fix the remaining consumers" in store.get_turn(entry["turn_ids"][-1]).user_text
    )
    assert entry["status"] == "running"
    finish(store, entry["turn_ids"][-1])
    poll(
        store,
        tmp_path,
        snapshot(checks=[good], threads=[dict(thread, isResolved=True)]),
    )
    assert get_repair(store, "errors", identity)["status"] == "checks_passed"


def test_green_pr_remains_watched_for_new_review(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    good = dict(failed_check(), conclusion="SUCCESS")
    poll(store, tmp_path, snapshot(checks=[good]))
    assert get_repair(store, "errors", identity)["status"] == "checks_passed"
    review = dict(
        id="review1",
        state="CHANGES_REQUESTED",
        body="Add a regression",
        submittedAt="now",
    )
    poll(store, tmp_path, snapshot(checks=[good], reviews=[review]))
    entry = get_repair(store, "errors", identity)
    assert entry["status"] == "running"
    assert "Add a regression" in store.get_turn(entry["turn_ids"][-1]).user_text


def test_no_checks_is_not_green_and_closed_pr_does_not_resume(store, tmp_path):
    identity, before = attached(store, tmp_path)
    poll(store, tmp_path, snapshot())
    assert get_repair(store, "errors", identity)["status"] == "awaiting_checks"
    poll(store, tmp_path, snapshot(state="CLOSED", checks=[failed_check()]))
    assert get_repair(store, "errors", identity)["status"] == "pr_closed"
    assert get_repair(store, "errors", identity)["turn_ids"] == before["turn_ids"]


def test_identical_feedback_is_bounded_but_new_evidence_continues(store, tmp_path):
    from app.external_retry import MAX_AUTOMATIC_ATTEMPTS

    identity, _ = attached(store, tmp_path)
    data = snapshot(checks=[failed_check()])
    for _ in range(MAX_AUTOMATIC_ATTEMPTS):
        poll(store, tmp_path, data)
        entry = get_repair(store, "errors", identity)
        finish(store, entry["turn_ids"][-1])
    poll(store, tmp_path, data)
    blocked = get_repair(store, "errors", identity)
    assert blocked["status"] == "feedback_blocked"
    assert len(blocked["turn_ids"]) == 1 + MAX_AUTOMATIC_ATTEMPTS
    poll(store, tmp_path, snapshot(head="def", checks=[failed_check(run="run2")]))
    assert (
        len(get_repair(store, "errors", identity)["turn_ids"])
        == len(blocked["turn_ids"]) + 1
    )


def test_feedback_checkpoint_interruption_reuses_turn(store, tmp_path, monkeypatch):
    import app.automatic_repair as module
    import pytest

    identity, _ = attached(store, tmp_path)
    real_save = module.save_repair
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("crash")
        return real_save(*args)

    monkeypatch.setattr(module, "save_repair", interrupted)
    with pytest.raises(RuntimeError, match="crash"):
        poll(store, tmp_path, snapshot(checks=[failed_check()]))
    monkeypatch.setattr(module, "save_repair", real_save)
    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    with store._connect() as db:
        assert db.execute("select count(*) from workbench_turns").fetchone()[0] == 2
    assert len(get_repair(store, "errors", identity)["turn_ids"]) == 2


def test_explicit_stop_is_not_resumed_by_pr_failure(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    entry = get_repair(store, "errors", identity)
    store.request_stop(entry["turn_ids"][-1])
    poll(store, tmp_path, snapshot(checks=[failed_check()]))
    assert get_repair(store, "errors", identity)["status"] == "stopped"
    assert len(get_repair(store, "errors", identity)["turn_ids"]) == 2


def test_waiting_checks_do_not_starve_new_error(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    watcher = AutomaticRepairCoordinator(
        store,
        repository=tmp_path,
        interval_seconds=0,
        pr_reader=lambda branch: [],
        feedback_reader=lambda url: snapshot(),
    )
    watcher.run_once()
    store.record_error(None, None, "second_failure", "failed")
    second = store.list_errors(limit=1)[0].id
    watcher.run_once()
    assert get_repair(store, "errors", identity)["status"] == "awaiting_checks"
    assert get_repair(store, "errors", second)["status"] == "running"


def test_status_context_and_incomplete_review_do_not_report_green():
    from app.repair_pull_request import assess_feedback

    good = dict(
        __typename="StatusContext",
        id="deployment",
        context="deployment",
        state="SUCCESS",
    )
    assert assess_feedback(snapshot(checks=[good]))[3]
    assert not assess_feedback(snapshot(checks=[dict(good, state="PENDING")]))[3]
    assert assess_feedback(snapshot(checks=[dict(good, state="ERROR")]))[2]
    assert not assess_feedback(
        dict(snapshot(checks=[good]), reviewDecision="CHANGES_REQUESTED")
    )[3]


def test_reader_paginates_every_connection_and_detects_push_during_read(
    monkeypatch, tmp_path
):
    from app.repair_pull_request import PullRequestReader

    reader = PullRequestReader(tmp_path)
    calls = []

    def gh(args, payload=None):
        if args[:2] == ["pr", "view"]:
            if args[-1] == "headRefOid":
                return {"headRefOid": "abc"}
            return dict(snapshot(), id="pr-node")
        query = payload["query"]
        cursor = payload["variables"].get("cursor")
        calls.append((query, cursor))
        page = dict(hasNextPage=cursor is None, endCursor="next")
        if "commits(last:1)" in query:
            node = {"commits": {"nodes": [{"commit": {"id": "commit-node"}}]}}
        elif "statusCheckRollup" in query:
            node = {
                "statusCheckRollup": {
                    "contexts": dict(
                        pageInfo=page, nodes=[dict(failed_check(), id=str(cursor))]
                    )
                }
            }
        elif "reviewThreads" in query:
            node = {
                "reviewThreads": dict(
                    pageInfo=page, nodes=[dict(id=str(cursor), isResolved=False)]
                )
            }
        elif "comments(" in query:
            node = {
                "comments": dict(
                    pageInfo=page, nodes=[dict(id=str(cursor), body="feedback")]
                )
            }
        else:
            field = "latestReviews" if "latestReviews" in query else "reviews"
            node = {
                field: dict(
                    pageInfo=page,
                    nodes=[dict(id=str(cursor), state="CHANGES_REQUESTED")],
                )
            }
        return {"data": {"node": node}}

    monkeypatch.setattr(reader, "_gh", gh)
    result = reader._read(snapshot()["url"])
    assert (
        len(result["checks"]) == len(result["threads"]) == len(result["reviews"]) == 2
    )
    assert all(len(thread["comments"]) == 2 for thread in result["threads"])
    assert any(cursor == "next" for _, cursor in calls)
    original = reader._gh
    monkeypatch.setattr(
        reader,
        "_gh",
        lambda args, payload=None: (
            {"headRefOid": "new-head"}
            if args[-1] == "headRefOid"
            else original(args, payload)
        ),
    )
    import pytest

    with pytest.raises(ValueError, match="head changed"):
        reader._read(snapshot()["url"])


def test_missing_checks_rollup_is_pending(monkeypatch, tmp_path):
    from app.repair_pull_request import PullRequestReader

    reader = PullRequestReader(tmp_path)
    monkeypatch.setattr(
        reader, "_gh", lambda *args: {"data": {"node": {"statusCheckRollup": None}}}
    )
    assert (
        reader._connection("commit", "Commit", "", ["statusCheckRollup", "contexts"])
        == []
    )


def test_feedback_change_after_checkpoint_crash_does_not_orphan_turn(
    store, tmp_path, monkeypatch
):
    import app.automatic_repair as module
    import pytest

    identity, _ = attached(store, tmp_path)
    real_save = module.save_repair
    calls = 0

    def fail_checkpoint(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("crash after turn")
        return real_save(*args)

    monkeypatch.setattr(module, "save_repair", fail_checkpoint)
    with pytest.raises(RuntimeError, match="crash after turn"):
        poll(store, tmp_path, snapshot(checks=[failed_check()]))
    monkeypatch.setattr(module, "save_repair", real_save)
    poll(store, tmp_path, snapshot(head="new-head", checks=[failed_check(run="run2")]))
    entry = get_repair(store, "errors", identity)
    assert len(entry["turn_ids"]) == 2
    with store._connect() as db:
        assert db.execute("select count(*) from workbench_turns").fetchone()[0] == 2


def test_feedback_a_b_a_does_not_reuse_a_completed_turn(store, tmp_path):
    identity, _ = attached(store, tmp_path)
    all_turns = []
    for head in ["abc", "def", "abc"]:
        poll(store, tmp_path, snapshot(head=head, checks=[failed_check()]))
        turn_id = get_repair(store, "errors", identity)["turn_ids"][-1]
        assert turn_id not in all_turns
        all_turns.append(turn_id)
        finish(store, turn_id)


def test_commented_review_body_requires_explicit_handling_receipt(store, tmp_path):
    import json

    identity, _ = attached(store, tmp_path)
    good = dict(failed_check(), conclusion="SUCCESS")
    review = dict(
        id="review-body",
        state="COMMENTED",
        body="Please cover the missing consumer",
        updatedAt="today",
    )
    data = snapshot(checks=[good], reviews=[review])
    poll(store, tmp_path, data)
    entry = get_repair(store, "errors", identity)
    assert len(entry["turn_ids"]) == 2
    assert review["body"] in store.get_turn(entry["turn_ids"][-1]).user_text
    finish(
        store,
        entry["turn_ids"][-1],
        json.dumps(
            dict(
                outcome="pr_updated",
                summary="Verified and covered",
                remaining_work=[],
                addressed_review_ids=["review-body"],
            )
        ),
    )
    poll(store, tmp_path, data)
    assert get_repair(store, "errors", identity)["status"] == "checks_passed"
    poll(
        store,
        tmp_path,
        snapshot(
            checks=[good],
            reviews=[dict(review, body="Additional finding", updatedAt="tomorrow")],
        ),
    )
    assert len(get_repair(store, "errors", identity)["turn_ids"]) == 3


def test_earlier_commented_body_is_not_hidden_by_later_approval():
    from app.repair_pull_request import assess_feedback

    first = dict(
        id="old-comment",
        state="COMMENTED",
        body="Please cover a missing path",
        author={"login": "reviewer"},
    )
    approved = dict(
        id="approval", state="APPROVED", body="", author={"login": "reviewer"}
    )
    pr = snapshot(
        checks=[dict(failed_check(), conclusion="SUCCESS")], reviews=[first, approved]
    )
    pr["latest_reviews"] = [approved]
    feedback, _, actionable, passed, _ = assess_feedback(pr)
    assert actionable and not passed
    assert feedback["commented_reviews"] == [first]
    old_request = dict(first, state="CHANGES_REQUESTED")
    pr["reviews"] = [old_request, approved]
    assert not assess_feedback(pr)[2]


def test_retry_carries_previous_result_and_remaining_goal(store, tmp_path):
    import json

    identity, _ = attached(store, tmp_path)
    data = snapshot(checks=[failed_check()])
    poll(store, tmp_path, data)
    entry = get_repair(store, "errors", identity)
    finish(
        store,
        entry["turn_ids"][-1],
        json.dumps(
            {
                "outcome": "blocked",
                "summary": "These tests also fail on main",
                "remaining_work": ["Repair stale test callers"],
                "addressed_review_ids": [],
            }
        ),
    )
    poll(store, tmp_path, data)
    latest = get_repair(store, "errors", identity)
    prompt = store.get_turn(latest["turn_ids"][-1]).user_text
    assert "These tests also fail on main" in prompt
    assert "Repair stale test callers" in prompt
    assert "Goal:" in prompt
    assert latest["last_feedback_result"]["outcome"] == "blocked"


def test_invalid_result_gets_format_feedback_in_same_conversation(store, tmp_path):
    identity, original = attached(store, tmp_path)
    data = snapshot(checks=[failed_check()])
    poll(store, tmp_path, data)
    entry = get_repair(store, "errors", identity)
    finish(
        store, entry["turn_ids"][-1], 'An explanation followed by {"outcome":"blocked"}'
    )
    poll(store, tmp_path, data)
    latest = get_repair(store, "errors", identity)
    assert latest["task_id"] == original["task_id"]
    assert latest["last_feedback_result"]["error"] == "invalid_result"
    prompt = store.get_turn(latest["turn_ids"][-1]).user_text
    assert "json_invalid" in prompt
    assert "remaining_work" in prompt
