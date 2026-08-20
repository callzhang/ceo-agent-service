from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.config import parse_duration_value


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    routes: tuple[RuntimeRoute, ...]
    secrets: dict[str, SecretStr]
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
    model = env.get("CEO_CODEX_MODEL", "gpt-5.5").strip()
    api_model = env.get("CEO_CODEX_API_MODEL", model).strip()
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
