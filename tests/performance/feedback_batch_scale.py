"""Measure the batch detail endpoint against 10,001 newer synthetic rows."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from tests.feedback_processing_fixtures import (
    registered_feedback_route_client,
    seed_feedback_batch_lookup,
)


def main() -> None:
    with TemporaryDirectory(prefix="ceo-feedback-scale-") as directory:
        client, store = registered_feedback_route_client(
            Path(directory), feedback_backlog_factory=lambda: {"processing": 0, "failed": 0, "retryable": 0},
        )
        noise_count = 10_001
        started = perf_counter()
        key, summary = seed_feedback_batch_lookup(store, noise_count=noise_count)
        seed_seconds = perf_counter() - started
        general_page = store.list_user_feedback_items(limit=10_000)
        assert len(general_page) == 10_000
        assert key not in {item.key for item in general_page}

        with client:
            started = perf_counter()
            response = client.get("/api/console/feedback/batches/batch-outside-limit")
            lookup_seconds = perf_counter() - started

        response.raise_for_status()
        batch = response.json()["item"]
        assert batch["requested_count"] == 1
        [item] = batch["items"]
        assert item["feedback_key"] == key
        assert item["summary"] == summary
        print(f"newer_rows={noise_count} seed_seconds={seed_seconds:.3f} lookup_seconds={lookup_seconds:.3f}")


if __name__ == "__main__":
    main()
