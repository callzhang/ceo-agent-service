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
    ADDED_ROUTE_SETTING_SUFFIXES,
    DEFAULT_CEO_CLAUDE_MODEL,
    DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT,
    DEFAULT_FRIDAY_RUNTIME_BASE_URL,
    ROUTE_NAME_PATTERN,
    SUPPORTED_CODEX_RUNTIME_MODELS,
    SUPPORTED_RUNTIME_REASONING_EFFORTS,
    SUPPORTED_RUNTIME_ROUTES,
    added_route_settings_prefix,
    load_runtime_config,
    normalize_codex_api_base_url,
    normalize_friday_runtime_base_url,
    normalize_optional_provider_base_url,
)


class AgentRuntimeSettingsError(ValueError):
    """A submission the service refuses, with the reason shown to the operator."""


_SECRET_SUFFIXES = ("_API_KEY",)
_SECRET_KEYS = frozenset({"CEO_FRIDAY_RUNTIME_TICKET", "CEO_FRIDAY_SESSION_TOKEN"})


def is_secret_key(key: str) -> bool:
    return key in _SECRET_KEYS or key.endswith(_SECRET_SUFFIXES)


def mask_secret(value: str) -> str:
    """Show enough of a stored token to recognise it, never the token itself."""

    value = value.strip()
    if not value:
        return ""
    if len(value) < 12:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


def masked_settings(fields: Mapping[str, str]) -> dict[str, str]:
    """The settings as the console receives them: secrets partially masked."""

    return {
        key: mask_secret(value) if is_secret_key(key) else value
        for key, value in fields.items()
    }


def _unchanged(persisted: Mapping[str, str], key: str, value: str) -> bool:
    """A submitted secret that is just the mask the console was shown."""

    return bool(value) and value == mask_secret(persisted.get(key, ""))


def save_agent_runtime_settings(fields: Mapping[str, object]) -> None:
    """Validate a console submission and write it to `.env`."""

    persisted = app_config.read_env_file()

    def submitted(key: str) -> str:
        value = str(fields.get(key) or "").strip()
        # Echoing back the mask the console showed leaves the stored secret as is.
        return "" if is_secret_key(key) and _unchanged(persisted, key, value) else value

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
    claude_enabled = "claude_oauth" in selected
    claude_model = current("CEO_CLAUDE_MODEL", DEFAULT_CEO_CLAUDE_MODEL)
    claude_reasoning_effort = current(
        "CEO_CLAUDE_MODEL_REASONING_EFFORT", DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT
    )
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
        friday_base_url = normalize_friday_runtime_base_url(
            current("CEO_FRIDAY_RUNTIME_BASE_URL", DEFAULT_FRIDAY_RUNTIME_BASE_URL)
        )
        friday_provider_base_url = normalize_optional_provider_base_url(
            submitted("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL")
        )
    except ValueError as exc:
        raise AgentRuntimeSettingsError(str(exc)) from exc
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
        "claude_oauth": claude_enabled and "claude_oauth" not in hidden_routes,
        "friday_runtime": friday_enabled and "friday_runtime" not in hidden_routes,
    }
    updates = {
        "CEO_CODEX_MODEL": model,
        "CEO_CODEX_MODEL_REASONING_EFFORT": reasoning_effort,
        "CEO_CLAUDE_MODEL": claude_model,
        "CEO_CLAUDE_MODEL_REASONING_EFFORT": claude_reasoning_effort,
        "CEO_AGENT_RUNTIME_HIDDEN_ROUTES": ",".join(hidden_routes),
        "CEO_AGENT_RUNTIME_ROUTES": _composed_route_order(
            enabled=enabled,
            added=[route["name"] for route in added_routes],
            submitted_order=route_order,
        ),
        "CEO_FRIDAY_RUNTIME_BASE_URL": friday_base_url,
        "CEO_FRIDAY_RUNTIME_PROJECT_ID": friday_project_id,
        "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL": friday_provider_base_url,
        "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL": friday_provider_model,
        "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1" if friday_auth_disabled else "0",
    }
    updates.update(_added_route_updates(added_routes, persisted))
    # Deleting a built-in card drops the credentials that belong to that route
    # only; the Claude model stays because Claude OAuth's card owns it.
    own_credentials = {
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
            value = str(fields.get(f"{prefix}{suffix}") or "").strip()
            if is_secret_key(f"{prefix}{suffix}") and _unchanged(
                persisted, f"{prefix}{suffix}", value
            ):
                return ""
            return value

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


def rename_agent_runtime_route(
    store: object, old_name: str, new_name: str
) -> dict[str, object]:
    """Rename an added route and carry every reference to it.

    Only added routes can be renamed; the three built-in routes keep their
    names. The database references move first, in one transaction, and the
    `.env` settings second. The database step is idempotent once committed,
    so if the `.env` write fails the operator retries the same rename and it
    completes; the reverse order could leave `.env` naming a route that the
    database still refers to by its old name, and a retry would be refused
    because the old name is no longer configured. The running service keeps
    its loaded configuration, so the new name takes effect at the next
    restart, like every other Agent Runtime setting.
    """

    old_name = old_name.strip()
    new_name = new_name.strip()
    persisted = app_config.read_env_file()
    routes = _names(persisted.get("CEO_AGENT_RUNTIME_ROUTES", ""))
    if old_name in SUPPORTED_RUNTIME_ROUTES:
        raise AgentRuntimeSettingsError(f"内置线路 {old_name} 不能改名。")
    if old_name not in routes:
        raise AgentRuntimeSettingsError(f"线路 {old_name} 不在已配置的线路里。")
    if not ROUTE_NAME_PATTERN.match(new_name):
        raise AgentRuntimeSettingsError(
            "名称只能用小写字母、数字和下划线，且以字母开头。"
        )
    if new_name in SUPPORTED_RUNTIME_ROUTES:
        raise AgentRuntimeSettingsError(f"{new_name} 是内置线路的名字，不能使用。")
    if new_name in routes:
        raise AgentRuntimeSettingsError(f"线路 {new_name} 已经存在。")
    moved = store.rename_runtime_route(old_name, new_name)
    old_prefix = added_route_settings_prefix(old_name)
    new_prefix = added_route_settings_prefix(new_name)
    updates = {
        "CEO_AGENT_RUNTIME_ROUTES": ",".join(
            new_name if name == old_name else name for name in routes
        ),
    }
    for suffix in ADDED_ROUTE_SETTING_SUFFIXES:
        updates[f"{new_prefix}{suffix}"] = persisted.get(f"{old_prefix}{suffix}", "")
    app_config.write_env_values(
        updates,
        remove=[
            f"{old_prefix}{suffix}"
            for suffix in ADDED_ROUTE_SETTING_SUFFIXES
            if f"{old_prefix}{suffix}" in persisted
        ],
    )
    return {"old_name": old_name, "new_name": new_name, "moved": moved}
