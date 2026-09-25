from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.config import DEFAULT_CEO_CODEX_MODEL, parse_duration_value


DEFAULT_CODEX_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_FRIDAY_RUNTIME_BASE_URL = "http://127.0.0.1:8080"
SUPPORTED_CODEX_RUNTIME_MODELS = frozenset(
    {
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
    }
)
ROUTE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ADDED_ROUTE_KINDS = ("codex_oauth", "codex_api", "claude_oauth", "claude_api")
# An OAuth kind reuses this machine's login, so an added one carries a model
# and no credential of its own.
ADDED_ROUTE_KINDS_WITH_KEY = ("codex_api", "claude_api")
ADDED_ROUTE_SETTING_SUFFIXES = ("KIND", "BASE_URL", "MODEL", "API_KEY")
# The only routes with fixed names (Derek 2026-09-24). Every other name in
# CEO_AGENT_RUNTIME_ROUTES is an added route that describes itself through
# CEO_RUNTIME_<NAME>_* settings and can be renamed.
SUPPORTED_RUNTIME_ROUTES = frozenset(
    {
        "codex_oauth",
        "claude_oauth",
        "friday_runtime",
    }
)
SUPPORTED_RUNTIME_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
DEFAULT_CEO_CLAUDE_MODEL = "sonnet"
DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT = "medium"


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    routes: tuple[RuntimeRoute, ...]
    secrets: dict[str, SecretStr]
    probe_interval: timedelta
    retry_delay: timedelta
    friday_runtime_base_url: str
    friday_runtime_project_id: str
    claude_reasoning_effort: str
    friday_runtime_model: str
    friday_runtime_auth_disabled: bool
    friday_runtime_auth_mode: str
    friday_runtime_provider_base_url: str
    friday_runtime_provider_model: str
    friday_runtime_provider_api_key: SecretStr | None

    def secret_for(self, route_name: str) -> SecretStr | None:
        return self.secrets.get(route_name)

    def friday_runtime_provider_environment(self) -> dict[str, str]:
        """Build the provider environment consumed by a Friday Runtime launcher.

        Friday's RuntimeTicket/session token remains the HTTP authentication
        credential. Provider credentials are deliberately kept in independent
        CEO_FRIDAY_RUNTIME_PROVIDER_* settings.
        """

        if (
            not self.friday_runtime_provider_base_url
            or not self.friday_runtime_provider_model
            or self.friday_runtime_provider_api_key is None
        ):
            return {}
        return {
            "FRIDAY_LLM_PROVIDER": "openai-compatible",
            "FRIDAY_LLM_BASE_URL": self.friday_runtime_provider_base_url,
            "FRIDAY_LLM_API_KEY": self.friday_runtime_provider_api_key.get_secret_value(),
            "FRIDAY_LLM_MODEL": self.friday_runtime_provider_model,
        }


def load_runtime_config(env: Mapping[str, str]) -> AgentRuntimeConfig:
    names = tuple(
        item.strip()
        for item in env.get("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth").split(",")
        if item.strip()
    )
    if not names or len(names) != len(set(names)):
        raise ValueError("CEO_AGENT_RUNTIME_ROUTES must contain unique routes")
    # A name outside the built-in set is an added route: it describes itself
    # through CEO_RUNTIME_<NAME>_* settings, so one provider kind can appear
    # as many times as it is configured.
    added = tuple(name for name in names if name not in SUPPORTED_RUNTIME_ROUTES)
    for name in added:
        if not ROUTE_NAME_PATTERN.match(name):
            raise ValueError(
                f"runtime route name must be lowercase letters, digits or _: {name}"
            )
    model = env.get("CEO_CODEX_MODEL", DEFAULT_CEO_CODEX_MODEL).strip()
    if "codex_oauth" in names and model not in SUPPORTED_CODEX_RUNTIME_MODELS:
        raise ValueError("CEO_CODEX_MODEL must select a supported Codex runtime model")
    friday_runtime_base_url = normalize_friday_runtime_base_url(
        env.get("CEO_FRIDAY_RUNTIME_BASE_URL", DEFAULT_FRIDAY_RUNTIME_BASE_URL)
    )
    friday_runtime_project_id = env.get("CEO_FRIDAY_RUNTIME_PROJECT_ID", "").strip()
    friday_runtime_model = env.get("CEO_FRIDAY_RUNTIME_MODEL", "default").strip()
    friday_auth_disabled = env.get("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "").strip() == "1"
    friday_provider_base_url = normalize_optional_provider_base_url(
        env.get("CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL", "")
    )
    friday_provider_model = env.get("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL", "").strip()
    friday_provider_key = env.get("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY", "").strip()
    claude_model = env.get("CEO_CLAUDE_MODEL", DEFAULT_CEO_CLAUDE_MODEL).strip()
    claude_reasoning_effort = env.get(
        "CEO_CLAUDE_MODEL_REASONING_EFFORT",
        DEFAULT_CEO_CLAUDE_MODEL_REASONING_EFFORT,
    ).strip()
    if claude_reasoning_effort not in SUPPORTED_RUNTIME_REASONING_EFFORTS:
        raise ValueError(
            "CEO_CLAUDE_MODEL_REASONING_EFFORT must select a supported effort level"
        )
    routes = []
    secrets: dict[str, SecretStr] = {}
    friday_auth_mode = "disabled" if friday_auth_disabled else ""
    for name in names:
        if name == "codex_oauth":
            routes.append(
                RuntimeRoute(
                    name=name,
                    runtime_kind=RuntimeKind.CODEX_CLI,
                    credential_mode=CredentialMode.LOCAL_OAUTH,
                    model=model,
                )
            )
        elif name in added:
            route, secret = _added_route(name, env)
            routes.append(route)
            if secret is not None:
                secrets[name] = secret
        elif name == "claude_oauth":
            routes.append(
                RuntimeRoute(
                    name=name,
                    runtime_kind=RuntimeKind.CLAUDE_CLI,
                    credential_mode=CredentialMode.LOCAL_OAUTH,
                    model=claude_model,
                )
            )
        else:
            if not friday_runtime_project_id:
                raise ValueError("friday_runtime requires CEO_FRIDAY_RUNTIME_PROJECT_ID")
            # The Friday project owns provider/model selection.  Keep this
            # optional value only as route metadata for callers that still
            # inspect it; it is never sent to Friday by the adapter.
            runtime_ticket = env.get("CEO_FRIDAY_RUNTIME_TICKET", "").strip()
            session_token = env.get("CEO_FRIDAY_SESSION_TOKEN", "").strip()
            if friday_auth_disabled and (runtime_ticket or session_token):
                raise ValueError(
                    "friday_runtime auth_disabled cannot include an authentication credential"
                )
            if not friday_auth_disabled and bool(runtime_ticket) == bool(session_token):
                raise ValueError(
                    "friday_runtime requires exactly one of "
                    "CEO_FRIDAY_RUNTIME_TICKET or CEO_FRIDAY_SESSION_TOKEN"
                )
            if runtime_ticket:
                secrets[name] = SecretStr(runtime_ticket)
                friday_auth_mode = "runtime_ticket"
            elif session_token:
                secrets[name] = SecretStr(session_token)
                friday_auth_mode = "session_token"
            routes.append(
                RuntimeRoute(
                    name=name,
                    runtime_kind=RuntimeKind.FRIDAY_RUNTIME,
                    credential_mode=CredentialMode.SERVICE_API,
                    model=friday_runtime_model,
                )
            )
    return AgentRuntimeConfig(
        routes=tuple(routes),
        secrets=secrets,
        probe_interval=parse_duration_value(
            "CEO_RUNTIME_PROBE_INTERVAL",
            env.get("CEO_RUNTIME_PROBE_INTERVAL"),
            timedelta(minutes=5),
        ),
        retry_delay=parse_duration_value(
            "CEO_RUNTIME_ROUTE_RETRY_DELAY",
            env.get("CEO_RUNTIME_ROUTE_RETRY_DELAY"),
            timedelta(minutes=30),
        ),
        claude_reasoning_effort=claude_reasoning_effort,
        friday_runtime_base_url=friday_runtime_base_url,
        friday_runtime_project_id=friday_runtime_project_id,
        friday_runtime_model=friday_runtime_model,
        friday_runtime_auth_disabled=friday_auth_disabled,
        friday_runtime_auth_mode=friday_auth_mode,
        friday_runtime_provider_base_url=friday_provider_base_url,
        friday_runtime_provider_model=friday_provider_model,
        friday_runtime_provider_api_key=(
            SecretStr(friday_provider_key)
            if "friday_runtime" in names and friday_provider_key
            else None
        ),
    )


def added_route_settings_prefix(name: str) -> str:
    """Return the env prefix that describes one added runtime route."""

    return f"CEO_RUNTIME_{name.upper()}_"


def added_route_api_key_state(name: str, env: Mapping[str, str]) -> tuple[bool, bool]:
    """Return whether a configured route needs its own API key, and whether it has one.

    Only an added route whose kind calls a provider API carries a key; the
    built-in routes use this machine's logins or Friday's own credentials.
    """

    if name in SUPPORTED_RUNTIME_ROUTES:
        return False, False
    prefix = added_route_settings_prefix(name)
    needs_key = env.get(f"{prefix}KIND", "").strip() in ADDED_ROUTE_KINDS_WITH_KEY
    return needs_key, bool(env.get(f"{prefix}API_KEY", "").strip())


def _added_route(
    name: str, env: Mapping[str, str]
) -> tuple[RuntimeRoute, SecretStr | None]:
    """Build a route the operator added, from its own settings."""

    prefix = added_route_settings_prefix(name)
    kind = env.get(f"{prefix}KIND", "").strip()
    if kind not in ADDED_ROUTE_KINDS:
        raise ValueError(f"{prefix}KIND must be one of {', '.join(ADDED_ROUTE_KINDS)}")
    model = env.get(f"{prefix}MODEL", "").strip()
    if not model:
        raise ValueError(f"{prefix}MODEL is required")
    secret: SecretStr | None = None
    if kind in ADDED_ROUTE_KINDS_WITH_KEY:
        raw_secret = env.get(f"{prefix}API_KEY", "").strip()
        if not raw_secret:
            raise ValueError(f"{prefix}API_KEY is required")
        secret = SecretStr(raw_secret)
    runtime_kind = (
        RuntimeKind.CODEX_CLI if kind.startswith("codex") else RuntimeKind.CLAUDE_CLI
    )
    credential_mode = (
        CredentialMode.SERVICE_API
        if kind in ADDED_ROUTE_KINDS_WITH_KEY
        else CredentialMode.LOCAL_OAUTH
    )
    # Only the Codex CLI takes a provider endpoint; the Claude CLI reads its
    # endpoint from its own installation.
    base_url = (
        normalize_codex_api_base_url(env.get(f"{prefix}BASE_URL", "").strip())
        if kind == "codex_api"
        else ""
    )
    return (
        RuntimeRoute(
            name=name,
            runtime_kind=runtime_kind,
            credential_mode=credential_mode,
            model=model,
            base_url=base_url,
        ),
        secret,
    )


def normalize_codex_api_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "API Base URL must be an absolute HTTP(S) URL without "
            "credentials, query, or fragment"
        )
    return normalized


def normalize_friday_runtime_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "CEO_FRIDAY_RUNTIME_BASE_URL must be an absolute HTTP(S) URL without "
            "credentials, query, or fragment"
        )
    return normalized


def normalize_optional_provider_base_url(value: str) -> str:
    """Normalize an optional Friday provider URL without accepting ambiguity."""

    if not value.strip():
        return ""
    return normalize_codex_api_base_url(value)
