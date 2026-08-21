"""Route-scoped native Codex command and environment construction."""

from __future__ import annotations

import json
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
    CODEX_PROCESS_FAILED,
    CODEX_PROVIDER_AUTH_FAILED,
    classify_codex_process_failure,
)
from app.codex_failure import (
    CODEX_PROVIDER_UNAVAILABLE as CODEX_PROCESS_PROVIDER_UNAVAILABLE,
)
from app.codex_runner import (
    CODEX_MODEL_PROVIDER_ENV,
    CodexRunner,
    resolved_codex_home,
)

_SAFE_ENV_KEYS = {
    "CODEX_CA_CERTIFICATE",
    "CODEX_HOME",
    "COLORTERM",
    "CURL_CA_BUNDLE",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LOGNAME",
    "NODE_EXTRA_CA_CERTS",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "TZ",
    "USER",
}
_SAFE_LOCALE_ENV_KEYS = {
    "LC_ALL",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NUMERIC",
    "LC_TIME",
}
_API_PROVIDER = "ceo_openai_api"
_API_PROVIDER_METADATA = {
    "name": "CEO OpenAI API fallback",
    "env_key": "OPENAI_API_KEY",
    "wire_api": "responses",
}
_CREDENTIAL_NAME_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
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
        sandbox_mode: str | None = None,
        skip_git_repo_check: bool = False,
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
            model_provider_settings=(
                self._api_provider_settings()
                if configured_route.credential_mode == CredentialMode.SERVICE_API
                else None
            ),
            shell_environment_policy_core=True,
            sandbox_mode=sandbox_mode,
            skip_git_repo_check=skip_git_repo_check,
        )

    def build_env(
        self,
        route: RuntimeRoute,
        api_key: str | None = None,
    ) -> dict[str, str]:
        configured_route = self._configured_route(route)
        base_env = self.runner.build_env()
        env = _safe_child_environment(base_env)
        env["CODEX_HOME"] = str(resolved_codex_home(base_env))
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
        *,
        timed_out: bool = False,
        timeout_kind: str = "",
        terminal_succeeded: bool = False,
    ) -> RuntimeFailure:
        if returncode == 0 or terminal_succeeded:
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.UNCLASSIFIED,
                code="runtime_unclassified",
                detail="Codex completed without a classified runtime failure.",
            )
        if timed_out:
            timeout_code = {
                "idle": "codex_idle_timeout",
                "total": "codex_total_timeout",
            }.get(timeout_kind)
            if timeout_code is not None:
                return _transport_failure(timeout_code, "Codex execution timed out.")
        if not stdout.strip() and not stderr.strip():
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.PROCESS,
                code=CODEX_PROCESS_FAILED,
                detail="Codex exited without output.",
            )
        detail, structured_messages = _provider_failure_text(stdout, stderr)
        if _is_codex_login_required_error(detail):
            return RuntimeFailure(
                failure_class=RuntimeFailureClass.AUTHENTICATION,
                code="codex_login_required",
                detail="Native Codex login needs attention.",
                failover_permitted=True,
                route_pause_required=True,
            )
        process_code = classify_codex_process_failure(detail, "")
        if process_code == CODEX_PROVIDER_AUTH_FAILED or _is_structured_invalid_api_key(
            structured_messages
        ):
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
        if "stream disconnected before completion" in detail.casefold():
            return _transport_failure(
                "codex_transport_disconnected",
                "Codex provider connection ended before completion.",
            )
        if _is_responses_transport_error(detail):
            return _transport_failure(
                "codex_transport_request_failed",
                "Codex provider request could not be sent.",
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
            return _API_PROVIDER
        return os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()

    def _api_provider_settings(self) -> dict[str, str]:
        return {
            **_API_PROVIDER_METADATA,
            "base_url": self.config.codex_api_base_url,
        }

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
        and ("session has ended" in normalized or "invalid refresh token" in normalized)
    ) or "token_invalidated" in normalized


def _unclassified_failure_detail(returncode: int) -> str:
    if returncode == 0:
        return "Codex did not return a classified runtime failure."
    return "Codex exited without a classified runtime failure."


def _safe_child_environment(base_env: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in base_env.items()
        if (key in _SAFE_ENV_KEYS or key in _SAFE_LOCALE_ENV_KEYS)
        and not any(marker in key.upper() for marker in _CREDENTIAL_NAME_MARKERS)
    }


def _provider_failure_text(stdout: str, stderr: str) -> tuple[str, list[str]]:
    messages = [stderr]
    structured_messages: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") not in {
            "error",
            "turn.failed",
        }:
            continue
        event_messages = _event_error_messages(event)
        messages.extend(event_messages)
        structured_messages.extend(event_messages)
    return "\n".join(message for message in messages if message), structured_messages


def _event_error_messages(event: dict[str, object]) -> list[str]:
    values: list[str] = []
    for key in ("message", "error"):
        value = event.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for nested_key in ("message", "code"):
                nested = value.get(nested_key)
                if isinstance(nested, str):
                    values.append(nested)
    return values


def _is_structured_invalid_api_key(messages: list[str]) -> bool:
    detail = "\n".join(messages).casefold()
    return "incorrect api key provided" in detail and "invalid_api_key" in detail


def _is_responses_transport_error(detail: str) -> bool:
    normalized = detail.casefold()
    return "error sending request" in normalized and "/v1/responses" in normalized


def _transport_failure(code: str, detail: str) -> RuntimeFailure:
    return RuntimeFailure(
        failure_class=RuntimeFailureClass.TRANSPORT,
        code=code,
        detail=detail,
        retryable_on_same_route=True,
        failover_permitted=True,
        route_pause_required=True,
    )
