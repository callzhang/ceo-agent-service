from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ExternalAttempt:
    operation: str
    attempt: int
    max_attempts: int
    error: str


def run_external(
    operation: str,
    call: Callable[[], T],
    *,
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    on_failure: Callable[[ExternalAttempt], None] | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            failure = ExternalAttempt(
                operation=operation,
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(exc),
            )
            if on_failure is not None:
                on_failure(failure)
            if attempt == max_attempts:
                raise
            sleep(delay_seconds)
    raise AssertionError("unreachable retry loop exit")
