import socket
import urllib.request

import pytest

from app.public_http import (
    PublicHttpRedirectHandler,
    PublicHttpUrlError,
    validate_public_http_url,
)


def _dns_result(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fe80::1"],
)
def test_public_http_url_rejects_non_public_dns_targets(monkeypatch, address):
    monkeypatch.setattr(
        "app.public_http.socket.getaddrinfo",
        lambda *_args, **_kwargs: _dns_result(address),
    )

    with pytest.raises(PublicHttpUrlError, match="public_http_target_not_public"):
        validate_public_http_url("https://images.example.test/input.png")


def test_public_http_redirect_revalidates_redirect_target(monkeypatch):
    monkeypatch.setattr(
        "app.public_http.socket.getaddrinfo",
        lambda *_args, **_kwargs: _dns_result("127.0.0.1"),
    )
    handler = PublicHttpRedirectHandler()
    request = urllib.request.Request("https://public.example.test/input.png")

    with pytest.raises(PublicHttpUrlError, match="public_http_target_not_public"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://metadata.internal/latest/meta-data",
        )
