from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.config import DEFAULT_CEO_CODEX_MODEL, parse_duration_value


DEFAULT_CODEX_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_FRIDAY_RUNTIME_BASE_URL = "http://127.0.0.1:8080"
SUPPORTED_CODEX_RUNTIME_MODELS = frozenset(
    {"gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
)


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    routes: tuple[RuntimeRoute, ...]
    secrets: dict[str, SecretStr]
    codex_api_base_url: str
    probe_interval: timedelta
    retry_delay: timedelta
    friday_runtime_base_url: str
    friday_runtime_project_id: str
    friday_runtime_model: str
    friday_runtime_auth_disabled: bool
    friday_runtime_auth_mode: str

    def secret_for(self, route_name: str) -> SecretStr | None:
        return self.secrets.get(route_name)


def load_runtime_config(env: Mapping[str, str]) -> AgentRuntimeConfig:
    names = tuple(
        item.strip()
        for item in env.get("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth").split(",")
        if item.strip()
    )
    if not names or len(names) != len(set(names)):
        raise ValueError("CEO_AGENT_RUNTIME_ROUTES must contain unique routes")
    supported = {"codex_oauth", "codex_api", "claude_api", "friday_runtime"}
    unknown = set(names) - supported
    if unknown:
        raise ValueError(f"unsupported runtime routes: {sorted(unknown)}")
    model = env.get("CEO_CODEX_MODEL", DEFAULT_CEO_CODEX_MODEL).strip()
    api_model = env.get("CEO_CODEX_API_MODEL", model).strip()
    if "codex_oauth" in names and model not in SUPPORTED_CODEX_RUNTIME_MODELS:
        raise ValueError("CEO_CODEX_MODEL must select a supported Codex runtime model")
    if "codex_api" in names and api_model not in SUPPORTED_CODEX_RUNTIME_MODELS:
        raise ValueError(
            "CEO_CODEX_API_MODEL must select a supported Codex runtime model"
        )
    codex_api_base_url = normalize_codex_api_base_url(
        env.get("CEO_CODEX_API_BASE_URL", DEFAULT_CODEX_API_BASE_URL)
    )
    friday_runtime_base_url = normalize_friday_runtime_base_url(
        env.get("CEO_FRIDAY_RUNTIME_BASE_URL", DEFAULT_FRIDAY_RUNTIME_BASE_URL)
    )
    friday_runtime_project_id = env.get("CEO_FRIDAY_RUNTIME_PROJECT_ID", "").strip()
    friday_runtime_model = env.get("CEO_FRIDAY_RUNTIME_MODEL", "default").strip()
    friday_auth_disabled = env.get("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "").strip() == "1"
    claude_model = env.get("CEO_CLAUDE_MODEL", "sonnet").strip()
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
        elif name == "codex_api":
            raw_secret = env.get("CEO_CODEX_API_KEY", "").strip()
            if not raw_secret:
                raise ValueError("codex_api requires CEO_CODEX_API_KEY")
            routes.append(
                RuntimeRoute(
                    name=name,
                    runtime_kind=RuntimeKind.CODEX_CLI,
                    credential_mode=CredentialMode.SERVICE_API,
                    model=api_model,
                )
            )
            secrets[name] = SecretStr(raw_secret)
        elif name == "claude_api":
            raw_secret = env.get("CEO_CLAUDE_API_KEY", "").strip()
            if not raw_secret:
                raise ValueError("claude_api requires CEO_CLAUDE_API_KEY")
            routes.append(
                RuntimeRoute(
                    name=name,
                    runtime_kind=RuntimeKind.CLAUDE_CLI,
                    credential_mode=CredentialMode.SERVICE_API,
                    model=claude_model,
                )
            )
            secrets[name] = SecretStr(raw_secret)
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
        codex_api_base_url=codex_api_base_url,
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
        friday_runtime_base_url=friday_runtime_base_url,
        friday_runtime_project_id=friday_runtime_project_id,
        friday_runtime_model=friday_runtime_model,
        friday_runtime_auth_disabled=friday_auth_disabled,
        friday_runtime_auth_mode=friday_auth_mode,
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
            "CEO_CODEX_API_BASE_URL must be an absolute HTTP(S) URL without "
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
