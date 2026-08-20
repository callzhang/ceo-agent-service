"""Route-scoped native Codex command and environment construction."""
from __future__ import annotations

import os
from pathlib import Path

from app.agent_runtime_config import AgentRuntimeConfig
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)
from app.codex_capacity import (
    CODEX_PROVIDER_CAPACITY_EXHAUSTED,
    CODEX_PROVIDER_UNAVAILABLE,
    codex_provider_failure_code,
)
from app.codex_failure import (
    CODEX_PROVIDER_AUTH_FAILED,
    classify_codex_process_failure,
)
from app.codex_failure import (
    CODEX_PROVIDER_UNAVAILABLE as CODEX_PROCESS_PROVIDER_UNAVAILABLE,
)
from app.codex_runner import (
    CODEX_MODEL_PROVIDER_ENV,
    CodexRunner,
)

_PROVIDER_CREDENTIAL_ENV_KEYS = (
    "CEO_CODEX_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CEO_CLAUDE_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)


class CodexRuntimeAdapter:
    """Build isolated native Codex invocations for configured runtime routes."""

    def __init__(
        self,
        workspace: Path,
        config: AgentRuntimeConfig,
        codex_bin: str = "codex",
    ):
        self.config = config
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)

    def build_command(
        self,
        route: RuntimeRoute,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None,
        output_schema_path: Path | None,
        use_output_schema: bool,
        approval_policy: str,
        developer_instructions: str | None,
        use_approval_bypass: bool,
    ) -> list[str]:
        configured_route = self._configured_route(route)
        return self.runner.build_command(
            prompt=prompt,
            session_id=session_id,
            image_paths=image_paths,
            output_schema_path=output_schema_path,
            use_output_schema=use_output_schema,
            approval_policy=approval_policy,
            developer_instructions=developer_instructions,
            use_approval_bypass=use_approval_bypass,
            model=configured_route.model,
            provider=self._provider_for(configured_route),
        )

    def build_env(
        self,
        route: RuntimeRoute,
        api_key: str | None = None,
    ) -> dict[str, str]:
        configured_route = self._configured_route(route)
        env = self.runner.build_env()
        for key in _PROVIDER_CREDENTIAL_ENV_KEYS:
            env.pop(key, None)
        if configured_route.credential_mode == CredentialMode.SERVICE_API:
            env["OPENAI_API_KEY"] = self._api_key_for(configured_route, api_key)
        elif api_key is not None:
            raise ValueError("codex_oauth does not accept an API key")
        return env

    def classify_failure(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
    ) -> RuntimeFailure:
        detail = f"{stdout}\n{stderr}"
        if _is_codex_login_required_error(detail):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                code="codex_login_required",
                detail="Native Codex login needs attention.",
                failover_permitted=True,
                route_pause_required=True,
            )
        process_code = classify_codex_process_failure(stdout, stderr)
        if process_code == CODEX_PROVIDER_AUTH_FAILED:
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                code=CODEX_PROVIDER_AUTH_FAILED,
                detail="Codex provider authentication failed.",
                failover_permitted=True,
                route_pause_required=True,
            )
        if process_code == CODEX_PROCESS_PROVIDER_UNAVAILABLE:
            provider_code = codex_provider_failure_code(detail)
            if provider_code in {
                CODEX_PROVIDER_CAPACITY_EXHAUSTED,
                CODEX_PROVIDER_UNAVAILABLE,
            }:
                return RuntimeFailure(
                    failure_class=RuntimeFailureClass.CAPACITY,
                    code=provider_code,
                    detail="Codex provider capacity is unavailable.",
                    failover_permitted=True,
                    route_pause_required=True,
                )
        return RuntimeFailure(
            failure_class=RuntimeFailureClass.UNCLASSIFIED,
            code="runtime_unclassified",
            detail=_unclassified_failure_detail(returncode),
        )

    def _configured_route(self, route: RuntimeRoute) -> RuntimeRoute:
        if route.runtime_kind != RuntimeKind.CODEX_CLI:
            raise ValueError("unsupported runtime route")
        expected = {
            "codex_oauth": CredentialMode.LOCAL_OAUTH,
            "codex_api": CredentialMode.SERVICE_API,
        }.get(route.name)
        if expected is None or route.credential_mode != expected:
            raise ValueError("unsupported runtime route")
        configured = next(
            (item for item in self.config.routes if item.name == route.name), None
        )
        if configured != route:
            raise ValueError("runtime route is not configured")
        return configured

    def _provider_for(self, route: RuntimeRoute) -> str:
        if route.credential_mode == CredentialMode.SERVICE_API:
            return "openai"
        return os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()

    def _api_key_for(self, route: RuntimeRoute, api_key: str | None) -> str:
        configured_secret = self.config.secret_for(route.name)
        if configured_secret is None:
            raise ValueError("codex_api route has no configured API key")
        selected_key = configured_secret.get_secret_value()
        if api_key is not None and api_key != selected_key:
            raise ValueError("API key does not match the configured runtime route")
        return selected_key


def _is_codex_login_required_error(value: str) -> bool:
    normalized = value.casefold()
    return (
        "failed to refresh token" in normalized
        and (
            "session has ended" in normalized
            or "invalid refresh token" in normalized
        )
    ) or "token_invalidated" in normalized


def _unclassified_failure_detail(returncode: int) -> str:
    if returncode == 0:
        return "Codex did not return a classified runtime failure."
    return "Codex exited without a classified runtime failure."
