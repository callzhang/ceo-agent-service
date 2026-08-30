from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import TypeVar


T = TypeVar("T")
DEFAULT_MAX_RETRY_DELAY_SECONDS = 15 * 60


class ExternalDependencyError(RuntimeError):
    """An external operation stayed unavailable after its immediate retries."""

    def __init__(
        self,
        operation: str,
        cause: Exception,
        *,
        dependency: str = "",
    ):
        self.operation = operation
        self.original_error = cause
        self.dependency = dependency
        super().__init__(str(cause) or cause.__class__.__name__)


def is_external_dependency_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, ExternalDependencyError) or bool(
            getattr(current, "retryable_external_dependency", False)
        ):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


@dataclass(frozen=True)
class ExternalAttempt:
    operation: str
    attempt: int
    max_attempts: int
    error: str


def retry_delay_seconds(
    delay_seconds: float,
    retry_index: int,
    *,
    backoff_multiplier: float = 2.0,
    max_delay_seconds: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
) -> float:
    """Return the shared capped exponential delay before a retry."""
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    if retry_index < 0:
        raise ValueError("retry_index must be non-negative")
    if backoff_multiplier < 1:
        raise ValueError("backoff_multiplier must be at least 1")
    if max_delay_seconds < 0:
        raise ValueError("max_delay_seconds must be non-negative")
    return min(
        delay_seconds * (backoff_multiplier**retry_index),
        max_delay_seconds,
    )


def run_external(
    operation: str,
    call: Callable[[], T],
    *,
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff_multiplier: float = 2.0,
    max_delay_seconds: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
    dependency: str = "",
    sleep: Callable[[float], None] = time.sleep,
    on_failure: Callable[[ExternalAttempt], None] | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    retry_delay_seconds(
        delay_seconds,
        0,
        backoff_multiplier=backoff_multiplier,
        max_delay_seconds=max_delay_seconds,
    )
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
                raise ExternalDependencyError(
                    operation,
                    exc,
                    dependency=dependency,
                ) from exc
            sleep(
                retry_delay_seconds(
                    delay_seconds,
                    attempt - 1,
                    backoff_multiplier=backoff_multiplier,
                    max_delay_seconds=max_delay_seconds,
                )
            )
    raise AssertionError("unreachable retry loop exit")
