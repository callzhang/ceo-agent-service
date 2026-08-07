from __future__ import annotations

import base64
import json
import os
import string
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


SERVICE_MCP_CONFIG_PATH_ENV = "CEO_SERVICE_MCP_CONFIG_PATH"
DEFAULT_SERVICE_MCP_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "service-mcp.json"
)
_SERVER_FIELDS = frozenset(
    {
        "url",
        "url_env",
        "command",
        "command_env",
        "args",
        "args_env",
        "bearer_token_env_var",
        "http_headers",
        "env_http_headers",
    }
)
_SENSITIVE_STATIC_HEADERS = frozenset(
    {
        "api-key",
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)
_HTTP_HEADER_NAME_CHARACTERS = frozenset(
    string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~"
)


@dataclass(frozen=True)
class ServiceMcpServer:
    name: str
    url: str | None = None
    url_env: str | None = None
    command: str | None = None
    command_env: str | None = None
    args: tuple[str, ...] = ()
    args_env: str | None = None
    bearer_token_env_var: str | None = None
    http_headers: tuple[tuple[str, str], ...] = ()
    env_http_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ServiceMcpConfigIssue:
    server_name: str
    reason: str
    field: str | None = None


class ServiceMcpConfigError(ValueError):
    def __init__(
        self,
        *,
        path: Path,
        issues: tuple[ServiceMcpConfigIssue, ...],
        valid_servers: tuple[ServiceMcpServer, ...] = (),
    ) -> None:
        self.path = path
        self.issues = issues
        self.valid_servers = valid_servers
        self.server_name = issues[0].server_name
        self.reason = issues[0].reason
        super().__init__("; ".join(issue.reason for issue in issues))


class _ServerConfigProblem(ValueError):
    def __init__(self, reason: str, *, field: str | None = None) -> None:
        self.reason = reason
        self.field = field
        super().__init__(reason)


class _DuplicateJsonKey(ValueError):
    pass


def service_mcp_config_path(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] = os.environ,
) -> Path:
    configured = path
    if configured is None:
        configured = env.get(SERVICE_MCP_CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SERVICE_MCP_CONFIG_PATH


def load_service_mcp_servers(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] = os.environ,
) -> tuple[ServiceMcpServer, ...]:
    config_path = service_mcp_config_path(path, env=env)
    payload = _read_manifest(config_path)
    if set(payload) != {"servers"}:
        raise _manifest_error(
            config_path,
            "service MCP manifest must contain only the servers object",
        )
    entries = payload["servers"]
    if not isinstance(entries, dict):
        raise _manifest_error(
            config_path,
            "service MCP manifest servers must be an object",
        )

    servers: list[ServiceMcpServer] = []
    issues: list[ServiceMcpConfigIssue] = []
    for name, entry in entries.items():
        if not _valid_server_name(name):
            issues.append(
                ServiceMcpConfigIssue(
                    server_name="service_mcp_config",
                    reason="service MCP server names must use ASCII letters, numbers, '_' or '-'",
                )
            )
            continue
        try:
            servers.append(_resolve_server(name, entry, env=env))
        except _ServerConfigProblem as exc:
            issues.append(
                ServiceMcpConfigIssue(
                    server_name=name,
                    reason=exc.reason,
                    field=exc.field,
                )
            )
    if issues:
        raise ServiceMcpConfigError(
            path=config_path,
            issues=tuple(issues),
            valid_servers=tuple(servers),
        )
    return tuple(servers)


def service_mcp_config_options(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] = os.environ,
    servers: Iterable[ServiceMcpServer] | None = None,
) -> list[str]:
    options: list[str] = []
    configured_servers = (
        tuple(servers)
        if servers is not None
        else load_service_mcp_servers(path, env=env)
    )
    for server in configured_servers:
        prefix = f"mcp_servers.{server.name}"
        if server.url is not None:
            _append_option(options, f"{prefix}.url", server.url)
        else:
            _append_option(options, f"{prefix}.command", server.command)
            if server.args:
                _append_option(options, f"{prefix}.args", list(server.args))
        if server.bearer_token_env_var is not None:
            _append_option(
                options,
                f"{prefix}.bearer_token_env_var",
                server.bearer_token_env_var,
            )
        if server.http_headers:
            _append_option(options, f"{prefix}.http_headers", dict(server.http_headers))
        if server.env_http_headers:
            _append_option(
                options,
                f"{prefix}.env_http_headers",
                dict(server.env_http_headers),
            )
    return options


def jwt_token_is_expired(token: str, *, now: float | None = None) -> bool:
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload_segment = parts[1]
    try:
        padded = payload_segment + "=" * ((4 - len(payload_segment) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return False
    return exp <= (time.time() if now is None else now)


def service_mcp_url_is_safe(url: str) -> bool:
    if (
        not isinstance(url, str)
        or not url
        or "\\" in url
        or _contains_control_character(url)
        or not _is_utf8_safe(url)
        or _has_malformed_percent_escape(url)
        or any(character.isspace() for character in url)
    ):
        return False
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or port > 0)
        and "?" not in url
        and "#" not in url
    )


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise _manifest_error(
            path,
            f"service MCP manifest is missing; set {SERVICE_MCP_CONFIG_PATH_ENV} to an existing JSON file",
        )
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except OSError:
        raise _manifest_error(path, "service MCP manifest cannot be read") from None
    except UnicodeDecodeError:
        raise _manifest_error(
            path,
            "service MCP manifest is not valid UTF-8",
        ) from None
    except json.JSONDecodeError:
        raise _manifest_error(path, "service MCP manifest is not valid JSON") from None
    except _DuplicateJsonKey:
        raise _manifest_error(
            path,
            "service MCP manifest contains duplicate object keys",
        ) from None
    if not isinstance(payload, dict):
        raise _manifest_error(path, "service MCP manifest root must be an object")
    return payload


def _manifest_error(path: Path, reason: str) -> ServiceMcpConfigError:
    return ServiceMcpConfigError(
        path=path,
        issues=(
            ServiceMcpConfigIssue(
                server_name="service_mcp_config",
                reason=reason,
            ),
        ),
    )


def _resolve_server(
    name: str,
    entry: object,
    *,
    env: Mapping[str, str],
) -> ServiceMcpServer:
    if not isinstance(entry, dict):
        raise _ServerConfigProblem(f"{name} configuration must be an object")
    unknown_fields = set(entry) - _SERVER_FIELDS
    if unknown_fields:
        raise _ServerConfigProblem(f"{name} has unsupported fields")

    url_source = _exclusive_source(name, entry, "url", "url_env")
    command_source = _exclusive_source(name, entry, "command", "command_env")
    if url_source is not None and command_source is not None:
        raise _ServerConfigProblem(
            f"{name} must declare exactly one transport: URL or command"
        )
    if url_source is None and command_source is None:
        raise _ServerConfigProblem(
            f"{name} must declare exactly one transport: URL or command"
        )

    url: str | None = None
    url_env: str | None = None
    command: str | None = None
    command_env: str | None = None
    if url_source == "url":
        url = _required_string(name, entry, "url")
    elif url_source == "url_env":
        url_env = _environment_name(name, entry, "url_env")
        url = _required_environment_value(name, url_env, env=env, field="url")
    elif command_source == "command":
        command = _required_string(name, entry, "command")
    else:
        command_env = _environment_name(name, entry, "command_env")
        command = _required_environment_value(
            name,
            command_env,
            env=env,
            field="command",
        )

    args, args_env = _resolve_args(name, entry, env=env)
    bearer_token_env_var = _optional_environment_name(
        name,
        entry,
        "bearer_token_env_var",
    )
    http_headers = _string_mapping(name, entry, "http_headers")
    env_http_headers = _environment_mapping(name, entry, "env_http_headers")
    static_header_names = {header.casefold() for header, _ in http_headers}
    environment_header_names = {
        header.casefold() for header, _ in env_http_headers
    }
    if static_header_names & environment_header_names:
        raise _ServerConfigProblem(
            f"{name} cannot declare the same HTTP header in both "
            "http_headers and env_http_headers"
        )
    if bearer_token_env_var and "authorization" in (
        static_header_names | environment_header_names
    ):
        raise _ServerConfigProblem(
            f"{name} cannot combine bearer_token_env_var with an "
            "Authorization header source"
        )

    if url is not None:
        _validate_url(name, url)
        if "args" in entry or "args_env" in entry:
            raise _ServerConfigProblem(
                f"{name} URL transport cannot declare args or args_env"
            )
    else:
        if bearer_token_env_var or http_headers or env_http_headers:
            raise _ServerConfigProblem(
                f"{name} command transport cannot declare HTTP authentication or headers"
            )

    if bearer_token_env_var is not None:
        _required_environment_value(
            name,
            bearer_token_env_var,
            env=env,
            field="bearer_token_env_var",
        )
    for _, env_name in env_http_headers:
        _required_environment_value(
            name,
            env_name,
            env=env,
            field="env_http_headers",
        )

    return ServiceMcpServer(
        name=name,
        url=url,
        url_env=url_env,
        command=command,
        command_env=command_env,
        args=args,
        args_env=args_env,
        bearer_token_env_var=bearer_token_env_var,
        http_headers=http_headers,
        env_http_headers=env_http_headers,
    )


def _exclusive_source(
    name: str,
    entry: dict[str, object],
    value_field: str,
    env_field: str,
) -> str | None:
    present = [field for field in (value_field, env_field) if field in entry]
    if len(present) > 1:
        raise _ServerConfigProblem(
            f"{name} cannot declare both {value_field} and {env_field}"
        )
    return present[0] if present else None


def _required_string(name: str, entry: dict[str, object], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _ServerConfigProblem(
            f"{name}.{field} must be a non-empty string",
            field=field,
        )
    if _contains_control_character(value) or not _is_utf8_safe(value):
        raise _ServerConfigProblem(
            f"{name}.{field} contains invalid characters",
            field=field,
        )
    return value


def _environment_name(name: str, entry: dict[str, object], field: str) -> str:
    value = _required_string(name, entry, field)
    if not _valid_environment_name(value):
        raise _ServerConfigProblem(
            f"{name}.{field} must name a valid environment variable"
        )
    return value


def _optional_environment_name(
    name: str,
    entry: dict[str, object],
    field: str,
) -> str | None:
    if field not in entry:
        return None
    return _environment_name(name, entry, field)


def _required_environment_value(
    name: str,
    env_name: str,
    *,
    env: Mapping[str, str],
    field: str | None = None,
) -> str:
    value = env.get(env_name)
    if not isinstance(value, str) or not value.strip():
        raise _ServerConfigProblem(
            f"{name} requires environment variable {env_name}; "
            "set it or delete the server from the manifest",
            field=field,
        )
    if value != value.strip():
        raise _ServerConfigProblem(
            f"{name} environment variable {env_name} contains leading or trailing whitespace",
            field=field,
        )
    if _contains_control_character(value) or not _is_utf8_safe(value):
        raise _ServerConfigProblem(
            f"{name} environment variable {env_name} contains invalid characters",
            field=field,
        )
    return value


def _resolve_args(
    name: str,
    entry: dict[str, object],
    *,
    env: Mapping[str, str],
) -> tuple[tuple[str, ...], str | None]:
    source = _exclusive_source(name, entry, "args", "args_env")
    if source is None:
        return (), None
    if source == "args":
        return _string_list(name, entry["args"], field="args"), None

    args_env = _environment_name(name, entry, "args_env")
    raw = _required_environment_value(name, args_env, env=env, field="args")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise _ServerConfigProblem(
            f"{name} requires {args_env} to contain a JSON array of strings"
        ) from None
    return _string_list(name, payload, field=args_env), args_env


def _string_list(name: str, value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str)
        and item
        and item == item.strip()
        and not _contains_control_character(item)
        and _is_utf8_safe(item)
        for item in value
    ):
        raise _ServerConfigProblem(
            f"{name}.{field} must be a JSON array of non-empty UTF-8-safe strings "
            "without control characters"
        )
    return tuple(value)


def _string_mapping(
    name: str,
    entry: dict[str, object],
    field: str,
) -> tuple[tuple[str, str], ...]:
    if field not in entry:
        return ()
    value = entry[field]
    if not isinstance(value, dict) or not value:
        raise _ServerConfigProblem(f"{name}.{field} must be a non-empty object")
    pairs: list[tuple[str, str]] = []
    normalized_headers: set[str] = set()
    for header, header_value in value.items():
        if not _valid_header_name(header):
            raise _ServerConfigProblem(f"{name}.{field} has an invalid header name")
        normalized_header = header.casefold()
        if normalized_header in normalized_headers:
            raise _ServerConfigProblem(
                f"{name}.{field} cannot declare duplicate HTTP header names "
                "case-insensitively"
            )
        normalized_headers.add(normalized_header)
        if header.casefold() in _SENSITIVE_STATIC_HEADERS:
            raise _ServerConfigProblem(
                f"{name}.{field} must reference secrets through bearer_token_env_var or env_http_headers"
            )
        if (
            not isinstance(header_value, str)
            or not header_value
            or header_value != header_value.strip()
            or _contains_control_character(header_value)
            or not _is_utf8_safe(header_value)
        ):
            raise _ServerConfigProblem(
                f"{name}.{field} header values must be non-empty UTF-8-safe strings "
                "without control characters"
            )
        pairs.append((header, header_value))
    return tuple(pairs)


def _environment_mapping(
    name: str,
    entry: dict[str, object],
    field: str,
) -> tuple[tuple[str, str], ...]:
    if field not in entry:
        return ()
    value = entry[field]
    if not isinstance(value, dict) or not value:
        raise _ServerConfigProblem(f"{name}.{field} must be a non-empty object")
    pairs: list[tuple[str, str]] = []
    normalized_headers: set[str] = set()
    for header, env_name in value.items():
        if not _valid_header_name(header):
            raise _ServerConfigProblem(f"{name}.{field} has an invalid header name")
        normalized_header = header.casefold()
        if normalized_header in normalized_headers:
            raise _ServerConfigProblem(
                f"{name}.{field} cannot declare duplicate HTTP header names "
                "case-insensitively"
            )
        normalized_headers.add(normalized_header)
        if not isinstance(env_name, str) or not _valid_environment_name(env_name):
            raise _ServerConfigProblem(
                f"{name}.{field} values must name environment variables"
            )
        pairs.append((header, env_name))
    return tuple(pairs)


def _validate_url(name: str, url: str) -> None:
    if not service_mcp_url_is_safe(url):
        raise _ServerConfigProblem(
            f"{name} URL must be a valid http(s) URL without credentials, query, or fragment",
            field="url",
        )


def _valid_server_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and all(character.isalnum() or character in "_-" for character in value)
    )


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _is_utf8_safe(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _has_malformed_percent_escape(value: str) -> bool:
    index = 0
    while True:
        index = value.find("%", index)
        if index < 0:
            return False
        if (
            index + 2 >= len(value)
            or value[index + 1] not in string.hexdigits
            or value[index + 2] not in string.hexdigits
        ):
            return True
        index += 3


def _valid_environment_name(value: str) -> bool:
    return (
        bool(value)
        and value.isascii()
        and (value[0].isalpha() or value[0] == "_")
        and all(character.isalnum() or character == "_" for character in value)
    )


def _valid_header_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and all(character in _HTTP_HEADER_NAME_CHARACTERS for character in value)
    )


def _append_option(options: list[str], key: str, value: object) -> None:
    options.extend(["-c", f"{key}={_config_value(value)}"])


def _config_value(value: object) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)} = {json.dumps(item, ensure_ascii=False)}"
            for key, item in value.items()
        ) + "}"
    return json.dumps(value, ensure_ascii=False)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result
