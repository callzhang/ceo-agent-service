from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request


class PublicHttpUrlError(ValueError):
    pass


def validate_public_http_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise PublicHttpUrlError("public_http_url_invalid") from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PublicHttpUrlError("public_http_url_invalid")
    effective_port = port or (443 if parsed.scheme.casefold() == "https" else 80)
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            effective_port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise PublicHttpUrlError("public_http_dns_unavailable") from exc
    if not addresses:
        raise PublicHttpUrlError("public_http_dns_unavailable")
    for _family, _type, _protocol, _canonical_name, sockaddr in addresses:
        try:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except ValueError as exc:
            raise PublicHttpUrlError("public_http_dns_invalid") from exc
        if not address.is_global:
            raise PublicHttpUrlError("public_http_target_not_public")
    return urllib.parse.urlunsplit(parsed)


class PublicHttpRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute_url = urllib.parse.urljoin(req.full_url, newurl)
        validate_public_http_url(absolute_url)
        return super().redirect_request(req, fp, code, msg, headers, absolute_url)


def read_public_http_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
) -> bytes:
    validated_url = validate_public_http_url(url)
    opener = urllib.request.build_opener(PublicHttpRedirectHandler())
    request = urllib.request.Request(validated_url)
    with opener.open(request, timeout=timeout_seconds) as response:
        validate_public_http_url(response.geturl())
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise PublicHttpUrlError("public_http_response_too_large")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PublicHttpUrlError("public_http_response_too_large")
    return data
