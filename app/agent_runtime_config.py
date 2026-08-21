from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.config import DEFAULT_CEO_CODEX_MODEL, parse_duration_value


DEFAULT_CODEX_API_BASE_URL = "https://api.openai.com/v1"
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
    supported = {"codex_oauth", "codex_api", "claude_api"}
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
    claude_model = env.get("CEO_CLAUDE_MODEL", "sonnet").strip()
    routes = []
    secrets: dict[str, SecretStr] = {}
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
        else:
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
