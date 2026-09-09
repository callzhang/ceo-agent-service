from app.codex_capacity import (
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    CODEX_PROVIDER_UNAVAILABLE,
    is_codex_provider_recovery_code,
)
from app.codex_failure import CODEX_PROVIDER_OVERLOADED


def test_provider_recovery_code_accepts_persisted_detail() -> None:
    assert is_codex_provider_recovery_code(CODEX_PROVIDER_UNAVAILABLE)
    assert is_codex_provider_recovery_code(
        f"{CODEX_PROVIDER_UNAVAILABLE}: request disconnected"
    )
    assert is_codex_provider_recovery_code(
        f"{CODEX_PROVIDER_CAPACITY_EXHAUSTED}: retry later"
    )
    assert not is_codex_provider_recovery_code("codex_process_failed")


def test_provider_overloaded_is_a_recovery_code() -> None:
    assert is_codex_provider_recovery_code(CODEX_PROVIDER_OVERLOADED)
    assert is_codex_provider_recovery_code(f"{CODEX_PROVIDER_OVERLOADED}: retry later")
