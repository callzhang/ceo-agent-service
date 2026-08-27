"""Regression coverage for the result-only worker queue."""

from types import SimpleNamespace

from app.worker import DingTalkAutoReplyWorker


class _QueueStore:
    def __init__(self):
        self.pages = [[SimpleNamespace(id=1), SimpleNamespace(id=2)], []]
        self.calls = []

    def peek_pending_reconciliation_reply_tasks(self, *args, **kwargs):
        raise AssertionError("legacy reconciliation queue must not be scheduled")

    def peek_reply_tasks(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)


def test_pending_candidates_use_one_normal_queue():
    store = _QueueStore()
    worker = object.__new__(DingTalkAutoReplyWorker)
    worker.store = store

    candidates = list(
        worker._pending_reply_task_candidates(
            page_size=10,
            now="2026-08-27 12:00:00",
            max_id=10,
        )
    )

    assert [item.id for item in candidates] == [1, 2]
    assert len(store.calls) == 2
