from __future__ import annotations

import json
from pathlib import Path

from app.email_classifier_scan import advance_scan_progress
from app.email_store import EmailStore
from app.store import AutoReplyStore

AT = "2026-09-25T09:00:00+00:00"


def test_the_first_batch_sets_the_peak_to_what_was_waiting() -> None:
    state = advance_scan_progress(None, batch_size=50, remaining_after=950, at=AT)

    assert state == {"remaining": 950, "peak": 1000, "last_batch": 50, "at": AT}


def test_the_peak_holds_while_the_backlog_is_worked_off() -> None:
    first = advance_scan_progress(None, batch_size=50, remaining_after=950, at=AT)

    second = advance_scan_progress(first, batch_size=50, remaining_after=900, at=AT)

    assert second["remaining"] == 900
    assert second["peak"] == 1000


def test_mail_arriving_mid_sweep_raises_the_peak_instead_of_moving_the_bar_back() -> None:
    first = advance_scan_progress(None, batch_size=50, remaining_after=100, at=AT)

    second = advance_scan_progress(first, batch_size=50, remaining_after=200, at=AT)

    assert second["peak"] == 250


def test_a_finished_sweep_starts_the_next_one_from_its_own_backlog() -> None:
    finished = advance_scan_progress(None, batch_size=20, remaining_after=0, at=AT)
    assert finished["remaining"] == 0

    next_sweep = advance_scan_progress(finished, batch_size=3, remaining_after=0, at=AT)

    assert next_sweep["peak"] == 3


def _store_with_progress(tmp_path: Path, *rows: tuple[str, dict[str, object]]) -> EmailStore:
    path = tmp_path / "progress.sqlite3"
    tasks = AutoReplyStore(path)
    for key, value in rows:
        tasks.set_service_state(key, json.dumps(value))
    return EmailStore(path)


def test_progress_is_summed_over_every_folder(tmp_path: Path) -> None:
    store = _store_with_progress(
        tmp_path,
        ("email_model_scan_progress:a:INBOX", {"remaining": 30, "peak": 100, "at": AT}),
        ("email_model_scan_progress:b:INBOX", {"remaining": 10, "peak": 50, "at": AT}),
    )

    assert store.model_scan_progress() == {
        "remaining": 40,
        "total": 150,
        "done": 110,
        "updated_at": AT,
    }


def test_no_progress_is_none_rather_than_a_full_bar(tmp_path: Path) -> None:
    assert _store_with_progress(tmp_path).model_scan_progress() is None


def test_a_malformed_progress_row_is_skipped(tmp_path: Path) -> None:
    store = _store_with_progress(
        tmp_path,
        ("email_model_scan_progress:a:INBOX", {"remaining": "many", "peak": 5}),
        ("email_model_scan_progress:b:INBOX", {"remaining": 1, "peak": 4, "at": AT}),
    )

    progress = store.model_scan_progress()

    assert progress is not None and progress["total"] == 4


def test_the_live_rate_counts_only_recent_samples_and_times_only_the_model(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    store = EmailStore(tmp_path / "rate.sqlite3")
    for total_ms in (100.0, 200.0, 300.0):
        store.record_classifier_runtime_sample(
            model_id="m1", outcome="success", fallback_code="", cache_hit=False,
            runtime_warm=True, queue_ms=0, http_ms=0, embedding_ms=0, head_ms=1, total_ms=total_ms,
        )
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with store._connect() as db:
        db.execute(
            "update email_classifier_runtime_samples set recorded_at=? where id=1", (old,)
        )

    rate = store.classifier_runtime_rate(window_seconds=600)

    assert rate["evaluated"] == 2
    assert rate["per_minute"] == 0.2
    # The two recent samples only: the old 100 ms one is out of the window.
    assert rate["latency_ms"] == {"p50": 300.0, "p95": 300.0}


def test_a_quiet_window_reports_the_last_batch_and_says_so(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    store = EmailStore(tmp_path / "quiet-window.sqlite3")
    for total_ms in (100.0, 200.0, 300.0):
        store.record_classifier_runtime_sample(
            model_id="m1", outcome="success", fallback_code="", cache_hit=False,
            runtime_warm=True, queue_ms=0, http_ms=0, embedding_ms=0, head_ms=1, total_ms=total_ms,
        )
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with store._connect() as db:
        db.execute("update email_classifier_runtime_samples set recorded_at=?", (old,))

    rate = store.classifier_runtime_rate(window_seconds=600)

    # Nothing was judged in the window, but the model's speed is still known.
    assert rate["evaluated"] == 0
    assert rate["latency_ms"] == {"p50": 200.0, "p95": 300.0}
    assert rate["latency_from_last_batch"] is True
    assert rate["latest_at"] == old


def test_a_model_that_never_judged_anything_has_no_latency(tmp_path: Path) -> None:
    rate = EmailStore(tmp_path / "never.sqlite3").classifier_runtime_rate()

    assert rate["evaluated"] == 0
    assert rate["latency_ms"] is None
    assert rate["latency_from_last_batch"] is False
