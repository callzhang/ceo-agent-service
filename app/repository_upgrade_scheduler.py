from __future__ import annotations

import time
from typing import Callable


def run_repository_upgrade_check_loop(
    *,
    service_factory: Callable[[], object],
    interval_seconds: int,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("repository upgrade interval must be positive")
    while True:
        try:
            service_factory().check()
        except Exception:
            # Discovery failures are persisted by the service when possible and
            # must never terminate the supervisor's other components.
            pass
        sleep(interval_seconds)
