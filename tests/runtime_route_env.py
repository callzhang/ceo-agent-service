"""Environment for an API runtime route in tests.

Since 2026-09-24 a route that signs in with its own API key is an added route
described by CEO_RUNTIME_<NAME>_* settings; `codex_api` and `claude_api` are
ordinary names for such routes.
"""

from __future__ import annotations


def codex_api_env(
    api_key: str,
    *,
    name: str = "codex_api",
    model: str = "gpt-5.5",
    base_url: str = "https://api.openai.com/v1",
) -> dict[str, str]:
    prefix = f"CEO_RUNTIME_{name.upper()}_"
    return {
        f"{prefix}KIND": "codex_api",
        f"{prefix}MODEL": model,
        f"{prefix}BASE_URL": base_url,
        f"{prefix}API_KEY": api_key,
    }


def claude_api_env(
    api_key: str,
    *,
    name: str = "claude_api",
    model: str = "sonnet",
) -> dict[str, str]:
    prefix = f"CEO_RUNTIME_{name.upper()}_"
    return {
        f"{prefix}KIND": "claude_api",
        f"{prefix}MODEL": model,
        f"{prefix}API_KEY": api_key,
    }


def set_env(monkeypatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
