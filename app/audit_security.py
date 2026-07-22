"""Shared browser-mutation boundary for the local audit console."""
from __future__ import annotations

import html
import ipaddress
import re
import secrets
from contextvars import ContextVar, Token
from hmac import compare_digest
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request


_CSRF_TOKEN = secrets.token_urlsafe(32)
_FALLBACK_CSP_NONCE = secrets.token_urlsafe(32)
_REQUEST_CSP_NONCE: ContextVar[str | None] = ContextVar(
    "ceo_audit_request_csp_nonce",
    default=None,
)
NOTIFICATION_BRIDGE_HEADER_NAME = "X-CEO-Notification-Bridge"
NOTIFICATION_BRIDGE_HEADER_VALUE = "python-worker-v1"
_FORM_BLOCK_RE = re.compile(r"(<form\b[^>]*>)(.*?</form>)", re.IGNORECASE | re.DOTALL)
_POST_METHOD_RE = re.compile(
    r"\bmethod\s*=\s*(?:\"post\"|'post'|post)(?:\s|>|/)",
    re.IGNORECASE,
)
_CSRF_FIELD_RE = re.compile(
    r"\bname\s*=\s*(?:\"csrf_token\"|'csrf_token'|csrf_token)(?:\s|>|/)",
    re.IGNORECASE,
)
def audit_csrf_token() -> str:
    """Return the process-local token shared by trusted in-process callers."""
    return _CSRF_TOKEN


def audit_csp_nonce() -> str:
    """Return the current request nonce, or a fallback for direct renderers."""
    return _REQUEST_CSP_NONCE.get() or _FALLBACK_CSP_NONCE


def begin_audit_csp_nonce() -> Token[str | None]:
    """Start a fresh CSP nonce scope for one audit HTTP request."""
    return _REQUEST_CSP_NONCE.set(secrets.token_urlsafe(32))


def reset_audit_csp_nonce(token: Token[str | None]) -> None:
    """Restore the nonce context that preceded the current request."""
    _REQUEST_CSP_NONCE.reset(token)


def script_nonce_attr() -> str:
    """Return the nonce attribute for an explicitly trusted script tag."""
    return f'nonce="{html.escape(audit_csp_nonce(), quote=True)}"'


def audit_content_security_policy() -> str:
    """Return the audit console's fail-closed, same-origin CSP."""
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src 'self' 'nonce-{audit_csp_nonce()}'",
            "style-src 'self' 'unsafe-inline'",
            "connect-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "form-action 'self'",
            "frame-src 'none'",
            "frame-ancestors 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "worker-src 'self'",
        )
    )


def audit_html_security_headers() -> dict[str, str]:
    """Return security headers shared by every audit HTML response."""
    return {
        "Content-Security-Policy": audit_content_security_policy(),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-store",
    }


def csrf_form_input() -> str:
    """Return the hidden token field used by local HTML mutation forms."""
    return (
        "<input type='hidden' name='csrf_token' value='"
        f"{html.escape(_CSRF_TOKEN, quote=True)}'>"
    )


def csrf_meta_tag() -> str:
    """Expose the token to same-origin JavaScript rendered by the audit app."""
    return (
        '<meta name="ceo-audit-csrf" content="'
        f'{html.escape(_CSRF_TOKEN, quote=True)}">'
    )


def protect_post_forms(value: str) -> str:
    """Inject the audit CSRF field into every POST form that lacks one."""

    def _protect(match: re.Match[str]) -> str:
        opening, remainder = match.groups()
        if not _POST_METHOD_RE.search(opening) or _CSRF_FIELD_RE.search(remainder):
            return match.group(0)
        return f"{opening}{csrf_form_input()}{remainder}"

    return _FORM_BLOCK_RE.sub(_protect, value)


def csrf_request_headers(origin: str) -> dict[str, str]:
    """Build headers for a trusted same-process request to the audit server."""
    return {
        "Origin": origin,
        "X-CEO-Audit-CSRF": _CSRF_TOKEN,
    }


def is_loopback_host(value: str) -> bool:
    host = (value or "").strip().rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _effective_port(parts) -> int | None:
    try:
        if parts.port is not None:
            return parts.port
    except ValueError:
        return None
    return {"http": 80, "https": 443}.get(parts.scheme.lower())


def _same_local_origin(request: Request, source: str) -> bool:
    try:
        expected = urlsplit(str(request.url))
        supplied = urlsplit(source)
        expected_host = (expected.hostname or "").rstrip(".").lower()
        supplied_host = (supplied.hostname or "").rstrip(".").lower()
    except (TypeError, ValueError):
        return False
    return bool(
        is_loopback_host(expected_host)
        and supplied.scheme.lower() in {"http", "https"}
        and supplied.scheme.lower() == expected.scheme.lower()
        and supplied_host == expected_host
        and _effective_port(supplied) == _effective_port(expected)
        and not supplied.username
        and not supplied.password
    )


async def _form_value(request: Request, key: str) -> str:
    try:
        body = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        return ""
    values = parse_qs(body, keep_blank_values=True).get(key, [])
    return values[0].strip() if values else ""


def require_local_request(request: Request) -> None:
    """Fail closed unless both the HTTP Host and peer address are loopback."""
    try:
        request_host = request.url.hostname or ""
    except ValueError:
        request_host = ""
    client_host = request.client.host if request.client is not None else ""
    if not is_loopback_host(request_host) or not is_loopback_host(client_host):
        raise HTTPException(status_code=403, detail="local audit request required")


async def require_local_mutation(request: Request) -> None:
    """Fail closed unless a state-changing request is local and same-origin."""
    require_local_request(request)
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if not source or not _same_local_origin(request, source):
        raise HTTPException(status_code=403, detail="same-origin audit request required")
    supplied_token = request.headers.get("x-ceo-audit-csrf", "").strip()
    if not supplied_token:
        supplied_token = await _form_value(request, "csrf_token")
    if not supplied_token or not compare_digest(supplied_token, _CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="invalid audit CSRF token")


async def require_internal_notification_request(request: Request) -> None:
    """Accept only the loopback Python-to-audit notification protocol."""
    try:
        require_local_request(request)
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail="local notification request required",
        ) from exc
    if "origin" in request.headers or "referer" in request.headers:
        raise HTTPException(status_code=403, detail="browser notification request rejected")
    supplied_header = request.headers.get(NOTIFICATION_BRIDGE_HEADER_NAME, "").strip()
    if not compare_digest(supplied_header, NOTIFICATION_BRIDGE_HEADER_VALUE):
        raise HTTPException(status_code=403, detail="invalid notification bridge header")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="notification JSON required")
