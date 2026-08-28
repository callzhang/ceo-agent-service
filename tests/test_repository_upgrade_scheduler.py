from pathlib import Path

import pytest

from app.repository_upgrade_scheduler import run_repository_upgrade_check_loop


class StopLoop(Exception):
    pass


class FakeService:
    def __init__(self):
        self.checks = 0

    def check(self):
        self.checks += 1


def test_check_loop_runs_check_then_waits_and_isolated_failures_continue():
    service = FakeService()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise StopLoop()

    with pytest.raises(StopLoop):
        run_repository_upgrade_check_loop(
            service_factory=lambda: service,
            interval_seconds=30,
            sleep=sleep,
        )

    assert service.checks == 2
    assert sleeps == [30, 30]
