"""Save the console's Agent Runtime settings into the service `.env`.

The React Settings page submits every field under its `.env` key name. This
module validates that submission and writes it; nothing else writes these keys.
"""

from __future__ import annotations

from collections.abc import Mapping

from app import config as app_config
from app.agent_runtime_config import (
    ADDED_ROUTE_KINDS,
    ADDED_ROUTE_KINDS_WITH_KEY,
    DEFAULT_CEO_CLAUDE_MODEL,
    DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT,
    DEFAULT_FRIDAY_RUNTIME_BASE_URL,
    ROUTE_NAME_PATTERN,
    SUPPORTED_CODEX_RUNTIME_MODELS,
    SUPPORTED_OPENAI_COMPATIBLE_MODELS,
    SUPPORTED_RUNTIME_REASONING_EFFORTS,
    SUPPORTED_RUNTIME_ROUTES,
    added_route_settings_prefix,
    load_runtime_config,
    normalize_codex_api_base_url,
    normalize_friday_runtime_base_url,
    normalize_optional_provider_base_url,
)


ADDED_ROUTE_SETTING_SUFFIXES = ("KIND", "BASE_URL", "MODEL", "API_KEY")


class AgentRuntimeSettingsError(ValueError):
    """A submission the service refuses, with the reason shown to the operator."""


def save_agent_runtime_settings(fields: Mapping[str, object]) -> None:
    """Validate a console submission and write it to `.env`."""

    persisted = app_config.read_env_file()

    def submitted(key: str) -> str:
        return str(fields.get(key) or "").strip()

    def current(key: str, default: str = "") -> str:
        """The submitted value, or the configured one when the field is blank."""

        return submitted(key) or persisted.get(key, default).strip()

    if fields.get("CEO_AGENT_RUNTIME_ROUTES") is None:
        # A payload that names no route at all used to disable every optional
        # route and still answer 已保存. Saving one field must never turn off a
        # route the caller never mentioned.
        raise AgentRuntimeSettingsError(
            "保存 Agent Runtime 必须带上启用的线路，否则未列出的线路会被关闭。"
        )
    route_order = _names(submitted("CEO_AGENT_RUNTIME_ROUTES"))
    selected = set(route_order)
    hidden_routes = [
        name
        for name in _names(submitted("CEO_AGENT_RUNTIME_HIDDEN_ROUTES"))
        if name in SUPPORTED_RUNTIME_ROUTES and name != "codex_oauth"
    ]
    model = submitted("CEO_CODEX_MODEL")
    reasoning_effort = submitted("CEO_CODEX_MODEL_REASONING_EFFORT")
    api_enabled = "codex_api" in selected
    api_model = submitted("CEO_CODEX_API_MODEL")
    api_token = submitted("CEO_CODEX_API_KEY")
    claude_enabled = "claude_oauth" in selected
    claude_model = current("CEO_CLAUDE_MODEL", DEFAULT_CEO_CLAUDE_MODEL)
    claude_reasoning_effort = current(
        "CEO_CLAUDE_MODEL_REASONING_EFFORT", DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT
    )
    claude_api_enabled = "claude_api" in selected
    claude_api_token = submitted("CEO_CLAUDE_API_KEY")
    claude_api_model = current("CEO_CLAUDE_API_MODEL")
    friday_enabled = "friday_runtime" in selected
    friday_project_id = submitted("CEO_FRIDAY_RUNTIME_PROJECT_ID")
    friday_provider_model = submitted("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL")
    friday_provider_api_key = submitted("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY")
    friday_auth_disabled = current("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "0") == "1"
    if model not in SUPPORTED_CODEX_RUNTIME_MODELS:
        raise AgentRuntimeSettingsError("Model must be selected from this page.")
    if reasoning_effort not in SUPPORTED_RUNTIME_REASONING_EFFORTS:
        raise AgentRuntimeSettingsError(
            "Thinking strength must be selected from this page."
        )
    if api_model not in SUPPORTED_OPENAI_COMPATIBLE_MODELS:
        raise AgentRuntimeSettingsError(
            "Fallback model must be selected from this page."
        )
    if claude_reasoning_effort not in SUPPORTED_RUNTIME_REASONING_EFFORTS:
        raise AgentRuntimeSettingsError(
            "Claude thinking strength must be selected from this page."
        )
    added_routes = _added_routes(
        [name for name in route_order if name not in SUPPORTED_RUNTIME_ROUTES],
        fields,
        persisted,
    )
    try:
        api_base_url = normalize_codex_api_base_url(submitted("CEO_CODEX_API_BASE_URL"))
        friday_base_url = normalize_friday_runtime_base_url(
            current("CEO_FRIDAY_RUNTIME_BASE_URL", DEFAULT_FRIDAY_RUNTIME_BASE_URL)
        )
        friday_provider_base_url = normalize_optional_provider_base_url(
            submitted("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL")
        )
    except ValueError as exc:
        raise AgentRuntimeSettingsError(str(exc)) from exc
    if api_enabled and not (api_token or persisted.get("CEO_CODEX_API_KEY", "").strip()):
        raise AgentRuntimeSettingsError(
            "API Token is required before API fallback can be enabled."
        )
    if claude_api_enabled and not (
        claude_api_token or persisted.get("CEO_CLAUDE_API_KEY", "").strip()
    ):
        raise AgentRuntimeSettingsError(
            "API Token is required before Claude API can be enabled."
        )
    provider_values = (
        friday_provider_base_url,
        friday_provider_model,
        friday_provider_api_key
        or persisted.get("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY", "").strip(),
    )
    if any(provider_values) and not all(provider_values):
        raise AgentRuntimeSettingsError(
            "Friday provider requires Base URL, model, and API Token together."
        )
    if friday_enabled:
        friday_base_url, friday_project_id = _desktop_friday(
            persisted, friday_project_id
        )
    enabled = {
        "codex_oauth": True,
        "codex_api": api_enabled and "codex_api" not in hidden_routes,
        "claude_oauth": claude_enabled and "claude_oauth" not in hidden_routes,
        "claude_api": claude_api_enabled and "claude_api" not in hidden_routes,
        "friday_runtime": friday_enabled and "friday_runtime" not in hidden_routes,
    }
    updates = {
        "CEO_CODEX_MODEL": model,
        "CEO_CODEX_MODEL_REASONING_EFFORT": reasoning_effort,
        "CEO_CLAUDE_MODEL": claude_model,
        "CEO_CLAUDE_MODEL_REASONING_EFFORT": claude_reasoning_effort,
        "CEO_CLAUDE_API_MODEL": claude_api_model,
        "CEO_AGENT_RUNTIME_HIDDEN_ROUTES": ",".join(hidden_routes),
        "CEO_AGENT_RUNTIME_ROUTES": _composed_route_order(
            enabled=enabled,
            added=[route["name"] for route in added_routes],
            submitted_order=route_order,
        ),
        "CEO_CODEX_API_BASE_URL": api_base_url,
        "CEO_CODEX_API_MODEL": api_model,
        "CEO_FRIDAY_RUNTIME_BASE_URL": friday_base_url,
        "CEO_FRIDAY_RUNTIME_PROJECT_ID": friday_project_id,
        "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL": friday_provider_base_url,
        "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL": friday_provider_model,
        "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1" if friday_auth_disabled else "0",
    }
    if api_token:
        updates["CEO_CODEX_API_KEY"] = api_token
    if claude_api_token:
        updates["CEO_CLAUDE_API_KEY"] = claude_api_token
    updates.update(_added_route_updates(added_routes, persisted))
    # Deleting a card drops the credential that belongs to that route only.
    # Shared settings stay: CEO_CODEX_API_BASE_URL also feeds the email
    # classifier, and the Claude model is shared with Claude OAuth.
    own_credentials = {
        "codex_api": ("CEO_CODEX_API_KEY",),
        "claude_api": ("CEO_CLAUDE_API_KEY",),
        "friday_runtime": (
            "CEO_FRIDAY_RUNTIME_TICKET",
            "CEO_FRIDAY_SESSION_TOKEN",
            "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY",
        ),
    }
    for route_name in hidden_routes:
        for key in own_credentials.get(route_name, ()):
            updates[key] = ""
    if friday_enabled:
        # The CLI signs the runtime's own ticket, so no credential is stored.
        updates["CEO_FRIDAY_RUNTIME_TICKET"] = ""
        updates["CEO_FRIDAY_SESSION_TOKEN"] = ""
    if friday_provider_api_key:
        updates["CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY"] = friday_provider_api_key
    app_config.write_env_values(updates)


def _names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def _desktop_friday(
    persisted: Mapping[str, str], project_id: str
) -> tuple[str, str]:
    """Return the address the desktop Friday CLI serves and its project id."""

    from app.friday_runtime_adapter import (
        FridayRuntimeError,
        bundled_friday_cli,
        check_desktop_friday,
        ensure_friday_project,
    )

    if not bundled_friday_cli():
        raise AgentRuntimeSettingsError(
            "Friday Runtime needs the Friday desktop app: it ships the CLI "
            "this service runs. Install Friday.app, then enable the route."
        )
    # Friday runs through its own CLI, so a save stores the address that CLI
    # is serving now rather than one typed into the page.
    try:
        base_url = check_desktop_friday()
    except (FridayRuntimeError, OSError) as exc:
        raise AgentRuntimeSettingsError(
            f"Friday CLI could not serve a runtime: {exc}"
        ) from exc
    if project_id:
        return base_url, project_id
    # Friday owns its project ids, so provision one instead of asking an
    # operator to invent a value Friday would reject.
    try:
        project_id = ensure_friday_project(
            load_runtime_config(
                {
                    **persisted,
                    "CEO_FRIDAY_RUNTIME_BASE_URL": base_url,
                    "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth",
                    "CEO_FRIDAY_RUNTIME_PROJECT_ID": "",
                }
            )
        )
    except (FridayRuntimeError, OSError, ValueError) as exc:
        raise AgentRuntimeSettingsError(
            f"Friday Runtime could not provide a project: {exc}"
        ) from exc
    return base_url, project_id


def _added_routes(
    names: list[str],
    fields: Mapping[str, object],
    persisted: Mapping[str, str],
) -> list[dict[str, str]]:
    """Read and validate the routes the operator added under their own names."""

    routes: list[dict[str, str]] = []
    for name in names:
        if not ROUTE_NAME_PATTERN.match(name):
            raise AgentRuntimeSettingsError(
                "A runtime name must be lowercase letters, digits or _, "
                "and must not reuse a built-in route name."
            )
        prefix = added_route_settings_prefix(name)

        def submitted(suffix: str) -> str:
            return str(fields.get(f"{prefix}{suffix}") or "").strip()

        kind = submitted("KIND")
        if kind not in ADDED_ROUTE_KINDS:
            raise AgentRuntimeSettingsError(
                f"Runtime {name} must select one of: {', '.join(ADDED_ROUTE_KINDS)}."
            )
        model = submitted("MODEL")
        if not model:
            raise AgentRuntimeSettingsError(f"Runtime {name} requires a model.")
        api_key = ""
        if kind in ADDED_ROUTE_KINDS_WITH_KEY:
            api_key = submitted("API_KEY") or persisted.get(f"{prefix}API_KEY", "").strip()
            if not api_key:
                raise AgentRuntimeSettingsError(f"Runtime {name} requires an API token.")
        base_url = ""
        if kind == "codex_api":
            try:
                base_url = normalize_codex_api_base_url(submitted("BASE_URL"))
            except ValueError as exc:
                raise AgentRuntimeSettingsError(f"Runtime {name}: {exc}") from exc
        routes.append(
            {
                "name": name,
                "kind": kind,
                "base_url": base_url,
                "model": model,
                "api_key": api_key,
            }
        )
    return routes


def _added_route_updates(
    added_routes: list[dict[str, str]], persisted: Mapping[str, str]
) -> dict[str, str]:
    """Write each added route's settings and clear the ones just removed."""

    updates: dict[str, str] = {}
    for route in added_routes:
        prefix = added_route_settings_prefix(route["name"])
        updates[f"{prefix}KIND"] = route["kind"]
        updates[f"{prefix}BASE_URL"] = route["base_url"]
        updates[f"{prefix}MODEL"] = route["model"]
        updates[f"{prefix}API_KEY"] = route["api_key"]
    kept = {route["name"] for route in added_routes}
    previous = set(_names(persisted.get("CEO_AGENT_RUNTIME_ROUTES", "")))
    for name in previous - SUPPORTED_RUNTIME_ROUTES - kept:
        prefix = added_route_settings_prefix(name)
        for suffix in ADDED_ROUTE_SETTING_SUFFIXES:
            updates[f"{prefix}{suffix}"] = ""
    return updates


def _composed_route_order(
    *,
    enabled: dict[str, bool],
    added: list[str],
    submitted_order: list[str],
) -> str:
    """Keep the failover order the console submitted, dropping disabled routes."""

    remaining = [name for name, is_on in enabled.items() if is_on] + added
    ordered = []
    for name in submitted_order:
        if name in remaining:
            ordered.append(name)
            remaining.remove(name)
    # A route the caller enabled without placing it keeps its default position.
    return ",".join(ordered + remaining)
