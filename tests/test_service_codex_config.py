import json
from pathlib import Path

import pytest

from app.service_codex_config import (
    ServiceMcpConfigError,
    load_service_mcp_servers,
    service_mcp_config_options,
)


def _write_manifest(path: Path, servers: dict[str, object]) -> Path:
    path.write_text(
        json.dumps({"servers": servers}, ensure_ascii=True),
        encoding="utf-8",
    )
    return path


def test_service_mcp_options_do_not_read_personal_codex_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
[mcp_servers.crm]
url = "https://personal.example/crm"

[mcp_servers.xiaoqing_interview]
url = "https://personal.example/xiaoqing"
""",
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {"exa": {"url": "https://mcp.exa.ai/mcp"}},
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(manifest))

    options = service_mcp_config_options()

    assert 'mcp_servers.exa.url="https://mcp.exa.ai/mcp"' in options
    assert not any("mcp_servers.crm" in option for option in options)
    assert not any("mcp_servers.xiaoqing_interview" in option for option in options)
    assert not any("personal.example" in option for option in options)


def test_service_manifest_resolves_environment_backed_command_transport(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "exa": {"url": "https://mcp.exa.ai/mcp"},
            "xiaoqing_interview": {
                "command_env": "CEO_XIAOQING_MCP_COMMAND",
                "args_env": "CEO_XIAOQING_MCP_ARGS_JSON",
            },
        },
    )
    env = {
        "CEO_XIAOQING_MCP_COMMAND": "/opt/service/xiaoqing-mcp",
        "CEO_XIAOQING_MCP_ARGS_JSON": '["serve", "--stdio"]',
    }

    servers = load_service_mcp_servers(path=manifest, env=env)
    options = service_mcp_config_options(path=manifest, env=env)

    assert [server.name for server in servers] == ["exa", "xiaoqing_interview"]
    assert servers[1].command == "/opt/service/xiaoqing-mcp"
    assert servers[1].command_env == "CEO_XIAOQING_MCP_COMMAND"
    assert servers[1].args == ("serve", "--stdio")
    assert servers[1].args_env == "CEO_XIAOQING_MCP_ARGS_JSON"
    assert 'mcp_servers.exa.url="https://mcp.exa.ai/mcp"' in options
    assert (
        'mcp_servers.xiaoqing_interview.command="/opt/service/xiaoqing-mcp"'
        in options
    )
    assert (
        'mcp_servers.xiaoqing_interview.args=["serve", "--stdio"]' in options
    )


def test_present_environment_backed_server_fails_when_command_is_missing(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "xiaoqing_interview": {
                "command_env": "CEO_XIAOQING_MCP_COMMAND",
                "args_env": "CEO_XIAOQING_MCP_ARGS_JSON",
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(
            path=manifest,
            env={"CEO_XIAOQING_MCP_ARGS_JSON": "[]"},
        )

    assert exc_info.value.server_name == "xiaoqing_interview"
    assert exc_info.value.reason == (
        "xiaoqing_interview requires environment variable "
        "CEO_XIAOQING_MCP_COMMAND; set it or delete the server from the manifest"
    )


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest = tmp_path / "service-mcp.json"
    manifest.write_text(
        """
{
  "servers": {
    "exa": {
      "url": "https://mcp.exa.ai/mcp",
      "url": "https://override.example/mcp"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(path=manifest, env={})

    assert exc_info.value.reason == "service MCP manifest contains duplicate object keys"
    assert "override.example" not in str(exc_info.value)


def test_unknown_field_error_does_not_echo_manifest_content(tmp_path: Path) -> None:
    secret = "must-not-appear"
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "exa": {
                "url": "https://mcp.exa.ai/mcp",
                secret: "value",
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={})

    assert exc_info.value.reason == "exa has unsupported fields"
    assert secret not in str(exc_info.value)


def test_static_authorization_header_never_leaks_value(tmp_path: Path) -> None:
    secret = "Bearer must-not-appear"
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "memory_connector": {
                "url": "https://memory.example/mcp/",
                "http_headers": {"Authorization": secret},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(path=manifest, env={})

    assert exc_info.value.reason == (
        "memory_connector.http_headers must reference secrets through "
        "bearer_token_env_var or env_http_headers"
    )
    assert secret not in str(exc_info.value)


def test_ambiguous_transport_is_rejected_before_options_are_emitted(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "exa": {"url": "https://mcp.exa.ai/mcp"},
            "ambiguous": {
                "url": "https://service.example/mcp/",
                "command": "/opt/service/mcp",
            },
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={})

    assert exc_info.value.server_name == "ambiguous"
    assert exc_info.value.reason == (
        "ambiguous must declare exactly one transport: URL or command"
    )


def test_malformed_args_environment_does_not_leak_its_value(tmp_path: Path) -> None:
    secret = "must-not-appear"
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "stdio": {
                "command": "/opt/service/mcp",
                "args_env": "SERVICE_MCP_ARGS_JSON",
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(
            path=manifest,
            env={"SERVICE_MCP_ARGS_JSON": f"not-json-{secret}"},
        )

    assert exc_info.value.reason == (
        "stdio requires SERVICE_MCP_ARGS_JSON to contain a JSON array of strings"
    )
    assert secret not in str(exc_info.value)


@pytest.mark.parametrize("control", ["\0", "\x1f", "\x7f", "\x85"])
def test_static_command_args_reject_embedded_control_characters(
    tmp_path: Path,
    control: str,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {"stdio": {"command": "/opt/service/mcp", "args": [f"before{control}after"]}},
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={})

    assert exc_info.value.reason == (
        "stdio.args must be a JSON array of non-empty UTF-8-safe strings "
        "without control characters"
    )
    assert control not in str(exc_info.value)


def test_environment_command_args_reject_embedded_control_characters(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "stdio": {
                "command": "/opt/service/mcp",
                "args_env": "SERVICE_MCP_ARGS_JSON",
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(
            path=manifest,
            env={"SERVICE_MCP_ARGS_JSON": '["before\\u0001after"]'},
        )

    assert exc_info.value.reason == (
        "stdio.SERVICE_MCP_ARGS_JSON must be a JSON array of non-empty "
        "UTF-8-safe strings "
        "without control characters"
    )


@pytest.mark.parametrize("control", ["\0", "\x1f", "\x7f", "\x85"])
def test_static_http_header_values_reject_embedded_control_characters(
    tmp_path: Path,
    control: str,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "http_headers": {"X-Service-Mode": f"before{control}after"},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={})

    assert exc_info.value.reason == (
        "remote.http_headers header values must be non-empty UTF-8-safe strings "
        "without control characters"
    )
    assert control not in str(exc_info.value)


@pytest.mark.parametrize("control", ["\t", "\x01", "\x7f", "\x85"])
def test_environment_http_header_values_reject_all_control_characters(
    tmp_path: Path,
    control: str,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "env_http_headers": {"X-Service-Mode": "SERVICE_MODE"},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(
            path=manifest,
            env={"SERVICE_MODE": f"before{control}after"},
        )

    assert exc_info.value.reason == (
        "remote environment variable SERVICE_MODE contains invalid characters"
    )
    assert control not in str(exc_info.value)


@pytest.mark.parametrize(
    ("server", "expected_reason"),
    [
        (
            {"url": "https://service.example/mcp/\ud800"},
            "remote.url contains invalid characters",
        ),
        (
            {"command": "/opt/service/\ud800"},
            "remote.command contains invalid characters",
        ),
        (
            {"command": "/opt/service/mcp", "args": ["before\ud800after"]},
            "remote.args must be a JSON array of non-empty UTF-8-safe strings "
            "without control characters",
        ),
        (
            {
                "url": "https://service.example/mcp/",
                "http_headers": {"X-Service-Mode": "before\ud800after"},
            },
            "remote.http_headers header values must be non-empty UTF-8-safe strings "
            "without control characters",
        ),
    ],
    ids=["url", "command", "args", "static-header"],
)
def test_manifest_transport_values_reject_lone_unicode_surrogates(
    tmp_path: Path,
    server: dict[str, object],
    expected_reason: str,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {"remote": server},
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={})

    assert exc_info.value.reason == expected_reason
    assert "\ud800" not in str(exc_info.value)


def test_environment_http_header_value_rejects_lone_unicode_surrogate(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "env_http_headers": {"X-Service-Mode": "SERVICE_MODE"},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env={"SERVICE_MODE": "\ud800"})

    assert exc_info.value.reason == (
        "remote environment variable SERVICE_MODE contains invalid characters"
    )
    assert "\ud800" not in str(exc_info.value)


def test_http_header_sources_cannot_overlap_case_insensitively(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "http_headers": {"X-Service-Mode": "static"},
                "env_http_headers": {"x-service-mode": "SERVICE_MODE"},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(
            path=manifest,
            env={"SERVICE_MODE": "environment"},
        )

    assert exc_info.value.reason == (
        "remote cannot declare the same HTTP header in both "
        "http_headers and env_http_headers"
    )


@pytest.mark.parametrize(
    ("field", "values", "env"),
    [
        (
            "http_headers",
            {"X-Service-Mode": "first", "x-service-mode": "second"},
            {},
        ),
        (
            "env_http_headers",
            {"X-Service-Mode": "FIRST_MODE", "x-service-mode": "SECOND_MODE"},
            {"FIRST_MODE": "first", "SECOND_MODE": "second"},
        ),
    ],
)
def test_each_http_header_source_rejects_case_insensitive_duplicates(
    tmp_path: Path,
    field: str,
    values: dict[str, str],
    env: dict[str, str],
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                field: values,
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(path=manifest, env=env)

    assert exc_info.value.reason == (
        f"remote.{field} cannot declare duplicate HTTP header names "
        "case-insensitively"
    )


def test_bearer_token_cannot_overlap_authorization_header_source(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "bearer_token_env_var": "SERVICE_TOKEN",
                "env_http_headers": {"aUtHoRiZaTiOn": "AUTHORIZATION_HEADER"},
            }
        },
    )
    secret = "must-not-appear"

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        service_mcp_config_options(
            path=manifest,
            env={
                "SERVICE_TOKEN": secret,
                "AUTHORIZATION_HEADER": f"Bearer {secret}",
            },
        )

    assert exc_info.value.reason == (
        "remote cannot combine bearer_token_env_var with an Authorization header source"
    )
    assert secret not in str(exc_info.value)


def test_malformed_http_header_name_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {
            "remote": {
                "url": "https://service.example/mcp/",
                "http_headers": {"Bad Header": "value"},
            }
        },
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(path=manifest, env={})

    assert exc_info.value.reason == "remote.http_headers has an invalid header name"


@pytest.mark.parametrize(
    "malformed_url",
    [
        "https://example.com:bad/mcp",
        "https://:443/mcp",
        "https://exam ple.com/mcp",
        "https://[::1/mcp",
        "https://service.example/mcp/%",
        "https://service.example/mcp/%2",
        "https://service.example/mcp/%GG",
    ],
)
def test_malformed_manifest_urls_raise_typed_error_without_echoing_value(
    tmp_path: Path,
    malformed_url: str,
) -> None:
    manifest = _write_manifest(
        tmp_path / "service-mcp.json",
        {"remote": {"url": malformed_url}},
    )

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(path=manifest, env={})

    assert exc_info.value.server_name == "remote"
    assert exc_info.value.reason == (
        "remote URL must be a valid http(s) URL without credentials, query, or fragment"
    )
    assert malformed_url not in str(exc_info.value)


def test_invalid_utf8_manifest_raises_typed_error_without_echoing_bytes(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "service-mcp.json"
    manifest.write_bytes(b'{"servers":{"remote":{"url":"must-not-appear\xff"}}}')

    with pytest.raises(ServiceMcpConfigError) as exc_info:
        load_service_mcp_servers(path=manifest, env={})

    assert exc_info.value.server_name == "service_mcp_config"
    assert exc_info.value.reason == "service MCP manifest is not valid UTF-8"
    assert "must-not-appear" not in str(exc_info.value)
