"""Run one Audit-accepted unsubscribe flow through a bounded browser protocol.

Private unsubscribe URLs exist only on :class:`UnsubscribeEntry` while a run is
active. Every value returned for persistence or display is an opaque reference,
fixed outcome, redacted step, or provider receipt. A retry always reconciles a
confirmation receipt and the current page/provider state before another write.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
from typing import Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlencode, urljoin, urlsplit

from app.email_store import (
    EmailStore,
    EmailUnsubscribeClaimConflict,
    EmailUnsubscribeReceiptConflict,
    MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS,
    email_unsubscribe_effect_digest,
)
from app.leak_check import assert_no_credentials, redact_credentials


_MAX_OPAQUE_REFERENCE_LENGTH = 512
_STRICT_OPAQUE_REFERENCE = re.compile(r"[A-Za-z0-9:_-]+")
_UNSUBSCRIBE_MARKERS = (
    "unsubscribe",
    "opt out",
    "opt-out",
    "manage subscription",
    "manage your subscription",
    "取消订阅",
    "退订",
)
_TERMINAL_STATES = frozenset(
    {"done", "already_unsubscribed", "login_required", "captcha", "payment"}
)
_UNRESOLVED_ERROR = "email_unsubscribe_outcome_unresolved"
_MAX_RESULT_TEXT_BYTES = 16 * 1024
_PRIVATE_RESULT_URL = re.compile(r"\b(?:https?|file)://[^\s<>'\"]+")
_PRIVATE_RESULT_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|private|tmp)/[^\s<>'\"]+")
_AUDIT_SESSION_FIELDS = frozenset(
    {
        "version",
        "action_identity",
        "effect_digest",
        "entry_reference",
        "document_url",
        "html",
        "html_digest",
        "cookies",
        "cookies_digest",
        "control_references",
        "network_policy_reference",
        "network_policy_origin_references",
    }
)
_MAX_AUDIT_SESSION_HTML_BYTES = 1024 * 1024


def normalize_unsubscribe_result_text(value: str) -> tuple[str, str]:
    """Normalize and redact terminal page text before it crosses the boundary.

    The digest covers the complete redacted observation, while the persisted
    text is bounded by UTF-8 bytes. This makes retries auditable without
    retaining private unsubscribe URLs, local paths, or credentials.
    """

    if not isinstance(value, str):
        raise TypeError("unsubscribe result text must be text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    redacted = redact_credentials(
        normalized,
        "[REDACTED]",
        credential_context=True,
    )
    redacted = _PRIVATE_RESULT_URL.sub("[REDACTED_URL]", redacted)
    redacted = _PRIVATE_RESULT_PATH.sub("[REDACTED_PATH]", redacted)
    digest = sha256(redacted.encode("utf-8")).hexdigest()
    bounded = redacted.encode("utf-8")[:_MAX_RESULT_TEXT_BYTES].decode(
        "utf-8", "ignore"
    )
    return bounded, digest


class UnsubscribeOutcome(str, Enum):
    DONE = "done"
    ALREADY_UNSUBSCRIBED = "already_unsubscribed"
    SKIPPED_NO_RELIABLE_ENTRY = "skipped_no_reliable_entry"
    SKIPPED_LOGIN_REQUIRED = "skipped_login_required"
    SKIPPED_CAPTCHA = "skipped_captcha"
    SKIPPED_PAYMENT = "skipped_payment"
    FAILED_BROWSER = "failed_browser"
    FAILED_PROVIDER_AUTH = "failed_provider_auth"


class UnsubscribeEntrySource(str, Enum):
    HEADER_ONE_CLICK_HTTPS = "header_one_click_https"
    HEADER_HTTPS = "header_https"
    HEADER_MAILTO = "header_mailto"
    BODY_HTML_HTTPS = "body_html_https"
    BODY_TEXT_HTTPS = "body_text_https"


class UnsubscribeOperationKind(str, Enum):
    POST_ONE_CLICK = "post_one_click"
    OPEN_ENTRY = "open_entry"
    FOLLOW_REDIRECT = "follow_redirect"
    SUBMIT_FORM = "submit_form"
    CLICK_CONFIRMATION = "click_confirmation"
    CONFIRM_EMAIL = "confirm_email"


class UnsubscribePageState(str, Enum):
    ACTION_REQUIRED = "action_required"
    DONE = "done"
    ALREADY_UNSUBSCRIBED = "already_unsubscribed"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    PAYMENT = "payment"


class UnsubscribeBrowserError(RuntimeError):
    """The browser runtime or its state readback failed technically."""


class UnsubscribeAuthenticationControlsError(UnsubscribeBrowserError):
    """The page exposes authentication or credential controls."""


_AUTHENTICATION_CONTROL_PREDICATE_JS = r"""
const reflectApply = Reflect.apply;
const elementGetAttribute = Element.prototype.getAttribute;
const elementClosest = Element.prototype.closest;
const documentQuerySelectorAll = Document.prototype.querySelectorAll;
const trustedGetAttribute = (node, name) => (
  node instanceof Element ? reflectApply(elementGetAttribute, node, [name]) : null
);
const trustedClosest = (node, selector) => (
  node instanceof Element ? reflectApply(elementClosest, node, [selector]) : null
);
const trustedDocumentQuery = selector => Array.from(
  reflectApply(documentQuerySelectorAll, document, [selector])
);
const compactAuthenticationText = value => String(value || '').toLowerCase()
  .replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ').trim();
const credentialAutocompleteTokens = new Set([
  'username', 'current-password', 'new-password', 'one-time-code', 'webauthn'
]);
const authenticationSemanticMarkers = [
  'password', 'passwd', 'passcode', 'pwd', 'current password',
  'new password', 'one time code', 'one time password', 'otp',
  'verification code', 'verify code', 'auth code',
  'authentication code', 'security code', 'mfa', '2fa',
  'captcha', 'recaptcha', 'hcaptcha', '验证码', '动态口令',
  '扫码', '二维码', 'choose account', 'select account',
  'account selection', 'continue as', 'sign in with',
  'log in with', 'login with'
];
const autocompleteTokens = node => String(
  trustedGetAttribute(node, 'autocomplete') || ''
).toLowerCase().trim().split(/\s+/).filter(Boolean);
const authenticationMetadata = node => {
  const values = [
    node.tagName, trustedGetAttribute(node, 'type'),
    trustedGetAttribute(node, 'autocomplete'), trustedGetAttribute(node, 'name'),
    trustedGetAttribute(node, 'id'), trustedGetAttribute(node, 'aria-label'),
    trustedGetAttribute(node, 'aria-labelledby'),
    trustedGetAttribute(node, 'placeholder'), trustedGetAttribute(node, 'title'),
    trustedGetAttribute(node, 'alt'), trustedGetAttribute(node, 'role'),
    trustedGetAttribute(node, 'class')
  ];
  if (node.labels) {
    for (const label of Array.from(node.labels)) {
      values.push(label.textContent || '');
    }
  }
  const parent = trustedClosest(node, '[aria-label], [role], [id], [class]');
  if (parent && parent !== node) {
    values.push(
      trustedGetAttribute(parent, 'aria-label'), trustedGetAttribute(parent, 'role'),
      trustedGetAttribute(parent, 'id'), trustedGetAttribute(parent, 'class'),
      parent.textContent || ''
    );
  }
  return compactAuthenticationText(values.filter(Boolean).join(' '));
};
const isAuthenticationControl = node => {
  if (!(node instanceof Element)) return false;
  const type = compactAuthenticationText(trustedGetAttribute(node, 'type'));
  if (node.tagName.toLowerCase() === 'input' && type === 'password') {
    return true;
  }
  if (autocompleteTokens(node).some(
    token => credentialAutocompleteTokens.has(token)
  )) return true;
  const metadata = authenticationMetadata(node);
  return authenticationSemanticMarkers.some(marker => metadata.includes(marker));
};
"""


class _ChromiumIsolatedWorld:
    """Evaluate bounded functions in a freshly resolved Chromium isolated world."""

    def __init__(self, cdp_session: object | None) -> None:
        self._cdp_session = cdp_session

    def evaluate(self, function_declaration: str) -> object:
        """Resolve the current main frame and never retry or fall back to main world."""

        try:
            if self._cdp_session is None:
                raise RuntimeError("CDP session unavailable")
            frame_tree = self._cdp_session.send("Page.getFrameTree")
            frame_id = frame_tree["frameTree"]["frame"]["id"]
            world = self._cdp_session.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "ceo-email-unsubscribe-audit",
                    "grantUniveralAccess": False,
                },
            )
            context_id = world["executionContextId"]
            response = self._cdp_session.send(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": function_declaration,
                    "executionContextId": context_id,
                    "returnByValue": True,
                    "awaitPromise": False,
                    "userGesture": False,
                },
            )
            if response.get("exceptionDetails"):
                raise RuntimeError("isolated evaluation rejected")
            result = response.get("result")
            if not isinstance(result, Mapping) or "value" not in result:
                raise RuntimeError("isolated evaluation returned no value")
            return result["value"]
        except Exception:
            raise UnsubscribeBrowserError(
                "trusted browser execution unavailable"
            ) from None


@dataclass(frozen=True, repr=False)
class _RestoredAuditSession:
    document_url: str
    html: str
    cookies: tuple[dict[str, object], ...]
    control_references: tuple[str, ...]

    def __repr__(self) -> str:
        return "<_RestoredAuditSession redacted>"


def _validated_restored_audit_session(
    payload: Mapping[str, object],
    effect: "EmailUnsubscribeEffect",
    *,
    executed_prefix_length: int,
) -> _RestoredAuditSession:
    """Bind one private profile snapshot to the exact append-only effect."""

    invalid = UnsubscribeBrowserError("browser session binding rejected")
    if set(payload) != _AUDIT_SESSION_FIELDS:
        raise invalid
    if (
        not isinstance(executed_prefix_length, int)
        or isinstance(executed_prefix_length, bool)
        or executed_prefix_length <= 0
        or executed_prefix_length != len(effect.operations) - 1
        or payload.get("version") != 1
        or payload.get("action_identity") != effect.action_identity
        or payload.get("effect_digest") != effect.previous_effect_digest
        or payload.get("entry_reference") != effect.entry_reference
        or payload.get("network_policy_reference")
        != effect.network_policy_reference
        or payload.get("network_policy_origin_references")
        != list(effect.network_policy_origin_references)
    ):
        raise invalid
    document_url = payload.get("document_url")
    html = payload.get("html")
    html_digest = payload.get("html_digest")
    cookie_values = payload.get("cookies")
    cookies_digest = payload.get("cookies_digest")
    control_values = payload.get("control_references")
    try:
        observed_cookies_digest = sha256(
            json.dumps(
                cookie_values,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise invalid from None
    if (
        not isinstance(document_url, str)
        or not document_url.strip()
        or not isinstance(html, str)
        or not html.strip()
        or len(html.encode("utf-8")) > _MAX_AUDIT_SESSION_HTML_BYTES
        or not isinstance(html_digest, str)
        or html_digest != sha256(html.encode("utf-8")).hexdigest()
        or not isinstance(cookie_values, list)
        or not isinstance(cookies_digest, str)
        or cookies_digest != observed_cookies_digest
        or not isinstance(control_values, list)
        or not control_values
        or len(control_values) > 128
        or any(not isinstance(item, str) for item in control_values)
    ):
        raise invalid
    controls = tuple(control_values)
    try:
        for reference in controls:
            _assert_strict_opaque_reference(
                reference,
                field_name="control_reference",
            )
    except (TypeError, ValueError):
        raise invalid from None
    if len(set(controls)) != len(controls):
        raise invalid
    cookies = _validated_audit_session_cookies(
        cookie_values,
        document_url=document_url,
    )
    appended = effect.operations[executed_prefix_length]
    if (
        appended.kind
        not in {
            UnsubscribeOperationKind.SUBMIT_FORM,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
        }
        or appended.target_reference not in controls
    ):
        raise invalid
    return _RestoredAuditSession(
        document_url=document_url,
        html=html,
        cookies=cookies,
        control_references=controls,
    )


def _validated_audit_session_cookies(
    values: object,
    *,
    document_url: str,
) -> tuple[dict[str, object], ...]:
    invalid = UnsubscribeBrowserError("browser session binding rejected")
    host = (urlsplit(document_url).hostname or "").casefold().rstrip(".")
    if not host or not isinstance(values, list) or len(values) > 64:
        raise invalid
    required = {
        "name",
        "value",
        "domain",
        "path",
        "expires",
        "httpOnly",
        "secure",
        "sameSite",
    }
    cookies: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != required:
            raise invalid
        name = value.get("name")
        content = value.get("value")
        domain = value.get("domain")
        path = value.get("path")
        expires = value.get("expires")
        http_only = value.get("httpOnly")
        secure = value.get("secure")
        same_site = value.get("sameSite")
        canonical_domain = (
            domain.lstrip(".").casefold().rstrip(".")
            if isinstance(domain, str)
            else ""
        )
        if (
            not isinstance(name, str)
            or not name
            or len(name.encode("utf-8")) > 256
            or any(character in name for character in "\r\n;=")
            or not isinstance(content, str)
            or len(content.encode("utf-8")) > 4096
            or any(character in content for character in "\r\n")
            or not canonical_domain
            or not (
                host == canonical_domain
                or host.endswith("." + canonical_domain)
            )
            or not isinstance(path, str)
            or not path.startswith("/")
            or len(path.encode("utf-8")) > 1024
            or isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or not (expires == -1 or 0 <= expires <= 253_402_300_799)
            or not isinstance(http_only, bool)
            or not isinstance(secure, bool)
            or same_site not in {"Strict", "Lax", "None"}
        ):
            raise invalid
        cookies.append(dict(value))
    return tuple(cookies)


class UnsubscribeProviderAuthError(RuntimeError):
    """The provider could not authenticate a read or write operation."""


def _canonical_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("browser network policy origin is invalid")
    scheme = parsed.scheme.casefold()
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise ValueError("browser network policy origin is invalid") from None
    host = parsed.hostname.casefold().rstrip(".")
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{rendered_host}:{port}"


def _default_resolve_host(host: str, port: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(item[4][0])
                for item in socket.getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                )
            }
        )
    )


def _is_forbidden_production_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return not address.is_global


@dataclass(frozen=True)
class BrowserNetworkPolicy:
    """Fail-closed exact-origin policy for every browser network request."""

    allowed_origins: frozenset[str]
    allow_loopback_for_tests: bool = False
    resolver: Callable[[str, int], tuple[str, ...]] = field(
        default=_default_resolve_host,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise ValueError("browser network policy requires allowed origins")
        canonical: set[str] = set()
        for value in self.allowed_origins:
            origin = _canonical_origin(value)
            parsed = urlsplit(origin)
            host = parsed.hostname or ""
            if host in {"metadata.google.internal", "metadata.internal"} or (
                not self.allow_loopback_for_tests
                and (host == "localhost" or host.endswith(".localhost"))
            ):
                raise ValueError("browser network policy origin is rejected")
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                literal = None
            if literal is not None and (
                not self.allow_loopback_for_tests or not literal.is_loopback
            ) and _is_forbidden_production_address(str(literal)):
                raise ValueError("browser network policy origin is rejected")
            if not self.allow_loopback_for_tests and parsed.scheme != "https":
                raise ValueError("browser network policy origin is rejected")
            canonical.add(origin)
        object.__setattr__(self, "allowed_origins", frozenset(canonical))

    def validate_url(self, value: str) -> str:
        try:
            parsed = urlsplit(value)
            origin = _canonical_origin(
                f"{parsed.scheme}://{parsed.netloc}"
            )
            if origin not in self.allowed_origins:
                raise ValueError
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
            if host in {"metadata.google.internal", "metadata.internal"} or (
                not self.allow_loopback_for_tests
                and (host == "localhost" or host.endswith(".localhost"))
            ):
                raise ValueError
            addresses = self.resolver(host, port)
            if not addresses:
                raise ValueError
            for address in addresses:
                parsed_address = ipaddress.ip_address(address)
                if parsed_address.is_loopback and self.allow_loopback_for_tests:
                    continue
                if _is_forbidden_production_address(address):
                    raise ValueError
        except Exception:
            raise UnsubscribeBrowserError("browser network request rejected") from None
        return value

    @property
    def reference(self) -> str:
        canonical = json.dumps(
            {
                "allow_loopback_for_tests": self.allow_loopback_for_tests,
                "allowed_origins": sorted(self.allowed_origins),
                "policy_version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "network-policy:" + sha256(canonical.encode()).hexdigest()

    @property
    def origin_references(self) -> tuple[str, ...]:
        return tuple(
            "network-origin:" + sha256(origin.encode()).hexdigest()
            for origin in sorted(self.allowed_origins)
        )


@dataclass(frozen=True)
class ConfirmationNavigationTarget:
    private_url: str = field(repr=False)
    target_reference: str
    confirmation_message_identity: str
    effect_digest: str

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(
            self.target_reference,
            field_name="target_reference",
        )
        _assert_opaque_reference(
            self.confirmation_message_identity,
            field_name="confirmation_message_identity",
        )
        if re.fullmatch(r"[0-9a-f]{64}", self.effect_digest) is None:
            raise ValueError("effect_digest must be canonical sha256 hex")


@dataclass(frozen=True)
class UnsubscribeAuthenticationEvidence:
    """Redacted authentication facts proving RFC 8058 header coverage."""

    dkim_covers_list_unsubscribe: bool
    dkim_covers_list_unsubscribe_post: bool
    evidence_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.dkim_covers_list_unsubscribe, bool) or not isinstance(
            self.dkim_covers_list_unsubscribe_post, bool
        ):
            raise TypeError("DKIM coverage facts must be boolean")
        _assert_strict_opaque_reference(
            self.evidence_reference,
            field_name="evidence_reference",
        )

    @property
    def one_click_verified(self) -> bool:
        return (
            self.dkim_covers_list_unsubscribe
            and self.dkim_covers_list_unsubscribe_post
        )


def _assert_opaque_reference(value: str, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > _MAX_OPAQUE_REFERENCE_LENGTH
        or any(character in value for character in ("?", "&", "#", "\r", "\n"))
        or urlsplit(value).scheme.casefold() in {"http", "https", "mailto", "file"}
    ):
        raise ValueError(f"{field_name} must be an opaque redacted reference")
    try:
        assert_no_credentials(value)
    except ValueError:
        raise ValueError(
            f"{field_name} must be an opaque redacted reference"
        ) from None


def _assert_strict_opaque_reference(value: str, *, field_name: str) -> None:
    decoded = value
    for _ in range(8):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_OPAQUE_REFERENCE_LENGTH
        or decoded != value
        or _STRICT_OPAQUE_REFERENCE.fullmatch(value) is None
    ):
        raise ValueError(f"{field_name} must be an opaque redacted reference")
    try:
        assert_no_credentials(value)
    except ValueError:
        raise ValueError(
            f"{field_name} must be an opaque redacted reference"
        ) from None


def _is_private_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and "\r" not in value
        and "\n" not in value
    )


def _is_loopback_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        parsed.scheme.casefold() == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.username is None
        and parsed.password is None
        and "\r" not in value
        and "\n" not in value
    )


def _is_private_browser_url(value: str) -> bool:
    return _is_private_https_url(value) or _is_loopback_http_url(value)


def _is_mailto_url(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.scheme.casefold() == "mailto" and parsed.path.strip())


def unsubscribe_entry_reference(private_url: str) -> str:
    """Return an opaque identity without exposing any URL component."""

    if not (_is_private_browser_url(private_url) or _is_mailto_url(private_url)):
        raise ValueError("unsubscribe entry is not a supported private URL")
    return "unsubscribe-entry:" + sha256(private_url.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UnsubscribeEntry:
    source: UnsubscribeEntrySource
    reference: str
    private_url: str = field(repr=False)
    priority: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, UnsubscribeEntrySource):
            raise TypeError("source must be UnsubscribeEntrySource")
        _assert_opaque_reference(self.reference, field_name="reference")
        if self.reference != unsubscribe_entry_reference(self.private_url):
            raise ValueError("unsubscribe entry reference does not match private URL")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "reference": self.reference,
            "priority": self.priority,
        }


def browser_unsubscribe_entries(
    entries: Sequence[UnsubscribeEntry],
    *,
    allow_loopback_for_tests: bool = False,
) -> tuple[UnsubscribeEntry, ...]:
    """Return only current HTTPS browser candidates in deterministic order."""

    selected = tuple(
        entry
        for entry in entries
        if _is_private_https_url(entry.private_url)
        or (
            allow_loopback_for_tests
            and _is_loopback_http_url(entry.private_url)
        )
    )
    return tuple(sorted(selected, key=lambda item: (item.priority, item.reference)))


def browser_network_policy_for_entries(
    entries: Sequence[UnsubscribeEntry],
    *,
    allow_loopback_for_tests: bool = False,
) -> BrowserNetworkPolicy:
    """Derive the minimal exact-origin policy for current browser candidates."""

    browser_entries = browser_unsubscribe_entries(
        entries,
        allow_loopback_for_tests=allow_loopback_for_tests,
    )
    origins = frozenset(
        f"{parsed.scheme.casefold()}://{parsed.netloc}"
        for parsed in (urlsplit(entry.private_url) for entry in browser_entries)
    )
    if not origins:
        raise ValueError("unsubscribe has no HTTPS browser candidate")
    return BrowserNetworkPolicy(
        origins,
        allow_loopback_for_tests=allow_loopback_for_tests,
    )


@dataclass(frozen=True)
class UnsubscribeOperation:
    operation_reference: str
    kind: UnsubscribeOperationKind
    target_reference: str

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(
            self.operation_reference, field_name="operation_reference"
        )
        _assert_strict_opaque_reference(
            self.target_reference,
            field_name="target_reference",
        )
        if not isinstance(self.kind, UnsubscribeOperationKind):
            raise TypeError("kind must be UnsubscribeOperationKind")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "UnsubscribeOperation":
        if set(value) != {"operation_reference", "kind", "target_reference"}:
            raise ValueError("accepted unsubscribe operation fields are invalid")
        try:
            kind = UnsubscribeOperationKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("accepted unsubscribe operation kind is invalid") from exc
        operation_reference = value["operation_reference"]
        target_reference = value["target_reference"]
        if not isinstance(operation_reference, str) or not isinstance(
            target_reference, str
        ):
            raise ValueError("accepted unsubscribe references must be text")
        return cls(
            operation_reference=operation_reference,
            kind=kind,
            target_reference=target_reference,
        )


@dataclass(frozen=True)
class EmailUnsubscribeEffect:
    action_identity: str
    action_plan_id: str
    action_plan_version: int
    classification_id: int
    account_id: str
    stable_message_identity: str
    thread_identity: str
    entry_reference: str
    operations: tuple[UnsubscribeOperation, ...]
    previous_effect_digest: str = ""
    network_policy_reference: str = "network-policy:legacy"
    network_policy_origin_references: tuple[str, ...] = ("network-origin:legacy",)

    def __post_init__(self) -> None:
        for field_name in (
            "action_identity",
            "action_plan_id",
            "account_id",
            "stable_message_identity",
            "thread_identity",
        ):
            _assert_opaque_reference(str(getattr(self, field_name)), field_name=field_name)
        _assert_strict_opaque_reference(
            self.entry_reference,
            field_name="entry_reference",
        )
        if self.action_plan_version <= 0:
            raise ValueError("action_plan_version must be positive")
        if self.classification_id <= 0:
            raise ValueError("classification_id must be positive")
        if any(not isinstance(item, UnsubscribeOperation) for item in self.operations):
            raise TypeError("operations must contain UnsubscribeOperation")
        if len(self.operations) > MAX_EMAIL_UNSUBSCRIBE_CONTINUATION_OPERATIONS:
            raise ValueError("durable continuation operation limit exceeded")
        references = [item.operation_reference for item in self.operations]
        if len(references) != len(set(references)):
            raise ValueError("unsubscribe operation references must be unique")
        if self.previous_effect_digest and re.fullmatch(
            r"[0-9a-f]{64}", self.previous_effect_digest
        ) is None:
            raise ValueError("previous_effect_digest must be canonical sha256 hex")
        _assert_strict_opaque_reference(
            self.network_policy_reference,
            field_name="network_policy_reference",
        )
        if not self.network_policy_origin_references:
            raise ValueError("network policy origins must be non-empty")
        for reference in self.network_policy_origin_references:
            _assert_strict_opaque_reference(
                reference,
                field_name="network_policy_origin_reference",
            )
        if len(set(self.network_policy_origin_references)) != len(
            self.network_policy_origin_references
        ):
            raise ValueError("network policy origin references must be unique")

    @property
    def operation_mappings(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "operation_reference": operation.operation_reference,
                "kind": operation.kind.value,
                "target_reference": operation.target_reference,
            }
            for operation in self.operations
        )

    @property
    def effect_digest(self) -> str:
        return email_unsubscribe_effect_digest(
            action_identity=self.action_identity,
            action_plan_id=self.action_plan_id,
            action_plan_version=self.action_plan_version,
            classification_id=self.classification_id,
            account_id=self.account_id,
            stable_message_identity=self.stable_message_identity,
            thread_identity=self.thread_identity,
            entry_reference=self.entry_reference,
            operations=self.operation_mappings,
            previous_effect_digest=self.previous_effect_digest,
            network_policy_reference=self.network_policy_reference,
            network_policy_origin_references=self.network_policy_origin_references,
        )


@dataclass(frozen=True)
class UnsubscribeTerminalReceipt:
    receipt_id: str
    evidence: str
    entry_reference: str
    effect_digest: str

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(self.receipt_id, field_name="receipt_id")
        _assert_strict_opaque_reference(self.evidence, field_name="evidence")
        _assert_strict_opaque_reference(
            self.entry_reference,
            field_name="entry_reference",
        )
        if re.fullmatch(r"[0-9a-f]{64}", self.effect_digest) is None:
            raise ValueError("effect_digest must be canonical sha256 hex")


@dataclass(frozen=True)
class UnsubscribeDiscoveredControl:
    reference: str
    kind: Literal["form", "link", "button", "confirmation_email"]
    intent: Literal["continue", "unsubscribe", "confirm"]

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(self.reference, field_name="control_reference")


@dataclass(frozen=True)
class UnsubscribeObservation:
    state: UnsubscribePageState
    state_reference: str
    next_operation_reference: str = ""
    receipt: UnsubscribeTerminalReceipt | None = None
    controls: tuple[UnsubscribeDiscoveredControl, ...] = ()
    visible_text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, UnsubscribePageState):
            raise TypeError("state must be UnsubscribePageState")
        _assert_opaque_reference(self.state_reference, field_name="state_reference")
        if self.next_operation_reference:
            _assert_strict_opaque_reference(
                self.next_operation_reference, field_name="next_operation_reference"
            )
        if self.state is UnsubscribePageState.ACTION_REQUIRED:
            if bool(self.next_operation_reference) == bool(self.controls):
                raise ValueError(
                    "action-required state needs one initial operation or discovered controls"
                )
            if self.receipt is not None:
                raise ValueError("action-required state cannot contain a receipt")
        elif self.state.value in _TERMINAL_STATES:
            if self.next_operation_reference or self.controls or self.receipt is None:
                raise ValueError("terminal unsubscribe state requires a receipt")


@dataclass(frozen=True)
class UnsubscribeDisposition:
    task_status: Literal["done", "skipped", "failed"]
    retryable: bool
    attention_when_exhausted: bool


@dataclass(frozen=True)
class RedactedUnsubscribeStep:
    operation: str
    state: str
    reference: str

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(self.operation, field_name="operation")
        _assert_strict_opaque_reference(self.state, field_name="state")
        _assert_strict_opaque_reference(self.reference, field_name="reference")


@dataclass(frozen=True)
class UnsubscribeExecutionResult:
    outcome: UnsubscribeOutcome
    disposition: UnsubscribeDisposition
    journal: tuple[RedactedUnsubscribeStep, ...]
    receipt: UnsubscribeTerminalReceipt | None = None
    error_code: str = ""
    result_text: str = ""
    observation_digest: str = ""
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, UnsubscribeOutcome):
            raise TypeError("outcome must be UnsubscribeOutcome")
        if self.error_code:
            _assert_opaque_reference(self.error_code, field_name="error_code")
        if any(not isinstance(item, RedactedUnsubscribeStep) for item in self.journal):
            raise TypeError("journal must contain RedactedUnsubscribeStep")
        if self.observation_digest and re.fullmatch(
            r"[0-9a-f]{64}", self.observation_digest
        ) is None:
            raise ValueError("observation_digest must be canonical sha256 hex")

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "task_status": self.disposition.task_status,
            "retryable": self.disposition.retryable,
            "attention_when_exhausted": self.disposition.attention_when_exhausted,
            "journal": [asdict(item) for item in self.journal],
            "receipt": asdict(self.receipt) if self.receipt is not None else None,
            "error_code": self.error_code,
            "result_text": self.result_text,
            "observation_digest": self.observation_digest,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class EmailUnsubscribeContinuation:
    action_identity: str
    action_plan_id: str
    action_plan_version: int
    classification_id: int
    account_id: str
    stable_message_identity: str
    thread_identity: str
    entry_reference: str
    effect_digest: str
    previous_effect_digest: str
    executed_operations: tuple[UnsubscribeOperation, ...]
    controls: tuple[UnsubscribeDiscoveredControl, ...]
    network_policy_reference: str
    network_policy_origin_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "action_identity",
            "action_plan_id",
            "account_id",
            "stable_message_identity",
            "thread_identity",
        ):
            _assert_opaque_reference(
                str(getattr(self, field_name)),
                field_name=field_name,
            )
        for field_name in (
            "entry_reference",
            "network_policy_reference",
        ):
            _assert_strict_opaque_reference(
                str(getattr(self, field_name)),
                field_name=field_name,
            )
        if self.action_plan_version <= 0 or self.classification_id <= 0:
            raise ValueError("continuation plan and classification must be positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.effect_digest) is None:
            raise ValueError("effect_digest must be canonical sha256 hex")
        if self.previous_effect_digest and re.fullmatch(
            r"[0-9a-f]{64}", self.previous_effect_digest
        ) is None:
            raise ValueError("previous_effect_digest must be canonical sha256 hex")
        if not self.executed_operations or not self.controls:
            raise ValueError("continuation requires operations and discovered controls")
        if any(
            not isinstance(item, UnsubscribeOperation)
            for item in self.executed_operations
        ) or any(
            not isinstance(item, UnsubscribeDiscoveredControl)
            for item in self.controls
        ):
            raise TypeError("continuation contains invalid typed values")
        if not self.network_policy_origin_references:
            raise ValueError("continuation requires network policy origins")
        for reference in self.network_policy_origin_references:
            _assert_strict_opaque_reference(
                reference,
                field_name="network_policy_origin_reference",
            )


@dataclass(frozen=True)
class UnsubscribeContinuationResult:
    continuation: EmailUnsubscribeContinuation
    journal: tuple[RedactedUnsubscribeStep, ...]

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "continuation": {
                **asdict(self.continuation),
                "executed_operations": [
                    {
                        "operation_reference": item.operation_reference,
                        "kind": item.kind.value,
                        "target_reference": item.target_reference,
                    }
                    for item in self.continuation.executed_operations
                ],
            },
            "journal": [asdict(item) for item in self.journal],
        }


class UnsubscribeBrowser(Protocol):
    def find_confirmation_receipt(
        self, effect: EmailUnsubscribeEffect
    ) -> UnsubscribeTerminalReceipt | None: ...

    def inspect_current_state(
        self, effect: EmailUnsubscribeEffect, private_url: str
    ) -> UnsubscribeObservation: ...

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation: ...


@dataclass(frozen=True)
class UnsubscribePageDiscovery:
    state: UnsubscribePageState
    state_reference: str
    controls: tuple[UnsubscribeDiscoveredControl, ...]


@dataclass(frozen=True, repr=False)
class _AuditedControlBinding:
    """Runtime-private request semantics behind one persisted opaque digest."""

    control: UnsubscribeDiscoveredControl
    target_url: str = field(repr=False)
    method: Literal["GET", "POST"]
    enctype: str
    successful_controls: tuple[tuple[str, str, str], ...] = field(repr=False)


def _audited_control_reference(semantics: Mapping[str, object]) -> str:
    canonical = json.dumps(
        semantics,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(canonical.encode("utf-8")) > 65_536:
        raise UnsubscribeBrowserError("browser control semantics rejected")
    return "unsubscribe-control:" + sha256(canonical.encode()).hexdigest()


def confirmation_target_reference(
    confirmation_message_identity: str,
) -> str:
    _assert_opaque_reference(
        confirmation_message_identity,
        field_name="confirmation_message_identity",
    )
    return "confirmation-target:" + sha256(
        confirmation_message_identity.encode()
    ).hexdigest()


class PlaywrightUnsubscribeBrowser:
    """Bounded sync-Playwright adapter; Playwright remains an optional dependency."""

    def __init__(
        self,
        page: object,
        *,
        timeout_ms: int = 5_000,
        network_policy: BrowserNetworkPolicy | None = None,
        restored_document_url: str = "",
        confirmation_receipt_resolver: Callable[
            [EmailUnsubscribeEffect], UnsubscribeTerminalReceipt | None
        ]
        | None = None,
        confirmation_target_resolver: Callable[
            [EmailUnsubscribeEffect], ConfirmationNavigationTarget | None
        ]
        | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if network_policy is None:
            raise ValueError("browser network policy is required")
        self.page = page
        self.timeout_ms = timeout_ms
        self.network_policy = network_policy
        self.confirmation_receipt_resolver = confirmation_receipt_resolver
        self.confirmation_target_resolver = confirmation_target_resolver
        self._blocked_request = False
        self._blocked_popup = False
        self._blocked_download = False
        self._document_url = ""
        self._context = self.page.context
        try:
            cdp_session = self._context.new_cdp_session(self.page)
        except Exception:
            cdp_session = None
        self._trusted_world = _ChromiumIsolatedWorld(cdp_session)
        if getattr(self._context, "service_workers", []):
            raise ValueError("browser network policy requires a clean context")
        self._context.set_default_timeout(timeout_ms)
        self._context.set_default_navigation_timeout(timeout_ms)
        self._context.route("**/*", self._guard_request)
        self.page.route("**/*", self._guard_request)
        self._context.on("page", self._reject_new_page)
        self.page.on("download", self._reject_download)
        self.page.add_init_script(
            """
            (() => {
              window.open = () => null;
              if (navigator.serviceWorker) {
                navigator.serviceWorker.register = () =>
                  Promise.reject(new Error('service worker rejected'));
              }
              const nativeSubmit = HTMLFormElement.prototype.submit;
              HTMLFormElement.prototype.submit = function() {
                if (this.target === '_blank') throw new Error('popup rejected');
                return nativeSubmit.call(this);
              };
              const nativeRequestSubmit = HTMLFormElement.prototype.requestSubmit;
              HTMLFormElement.prototype.requestSubmit = function(submitter) {
                if (this.target === '_blank') throw new Error('popup rejected');
                return nativeRequestSubmit.call(this, submitter);
              };
              document.addEventListener('click', event => {
                const link = event.target && event.target.closest
                  ? event.target.closest('a') : null;
                if (link && (link.hasAttribute('download') || link.target === '_blank')) {
                  event.preventDefault();
                  event.stopImmediatePropagation();
                }
              }, true);
            })();
            """
        )
        if restored_document_url:
            if str(getattr(self.page, "url")) != "about:blank":
                raise ValueError("restored browser page must start isolated")
            self._document_url = self._validate_navigation_target(
                restored_document_url
            )

    def _guard_request(self, route: object, request: object) -> None:
        try:
            self.network_policy.validate_url(request.url)
            response = route.fetch(max_redirects=0, timeout=self.timeout_ms)
            self.network_policy.validate_url(response.url)
            location = response.headers.get("location")
            if location:
                self.network_policy.validate_url(urljoin(request.url, location))
        except Exception:
            self._blocked_request = True
            route.abort()
            return
        route.fulfill(response=response)

    def _reject_new_page(self, page: object) -> None:
        if page is self.page:
            return
        self._blocked_popup = True
        try:
            page.close()
        except Exception:
            pass

    def _reject_download(self, download: object) -> None:
        self._blocked_download = True
        try:
            download.cancel()
        except Exception:
            pass

    def _raise_if_blocked(self) -> None:
        if self._blocked_request:
            raise UnsubscribeBrowserError("browser network request rejected")
        if self._blocked_popup:
            raise UnsubscribeBrowserError("browser popup rejected")
        if self._blocked_download:
            raise UnsubscribeBrowserError("browser download rejected")

    def _validate_navigation_target(self, value: str) -> str:
        return self.network_policy.validate_url(value)

    def _visible_text(self) -> str:
        text = self.page.locator("body").inner_text(timeout=self.timeout_ms).strip()
        if not text:
            raise UnsubscribeBrowserError("unsubscribe page has no visible state")
        return text

    def _assert_no_authentication_controls(self) -> None:
        """Reject auth UI using attributes/labels only, before reading form state."""

        detected = self._trusted_world.evaluate(
            "function() {"
            + _AUTHENTICATION_CONTROL_PREDICATE_JS
            + """
              const controls = trustedDocumentQuery(
                'input, textarea, select, button, a, img, canvas, [role]'
              );
              return controls.some(node => isAuthenticationControl(node));
            }
            """
        )
        if detected is not False:
            raise UnsubscribeAuthenticationControlsError(
                "authentication controls are not permitted"
            )

    @staticmethod
    def _control_intent(
        label: str,
    ) -> Literal["continue", "unsubscribe", "confirm"] | None:
        normalized = " ".join(label.casefold().split())
        if any(marker in normalized for marker in ("unsubscribe", "退订")):
            return "unsubscribe"
        if any(marker in normalized for marker in ("confirm", "确认")):
            return "confirm"
        if any(marker in normalized for marker in ("continue", "next", "继续")):
            return "continue"
        return None

    def _page_identity(self) -> str:
        value = self._document_url or str(getattr(self.page, "url"))
        return self._validate_navigation_target(value)

    def _link_binding(
        self,
        snapshot: Mapping[str, object],
    ) -> _AuditedControlBinding | None:
        label = str(snapshot.get("label") or "").strip()
        intent = self._control_intent(label)
        if intent is None:
            return None
        href = str(snapshot.get("href") or "")
        target = str(snapshot.get("target") or "").casefold()
        if (
            not href
            or target not in {"", "_self"}
            or snapshot.get("download") is True
        ):
            return None
        page_identity = self._page_identity()
        target_url = self._validate_navigation_target(urljoin(page_identity, href))
        semantics: dict[str, object] = {
            "enctype": "",
            "form_association": "",
            "intent": intent,
            "kind": "link",
            "method": "GET",
            "page_identity": page_identity,
            "policy_reference": self.network_policy.reference,
            "resolved_target": target_url,
            "successful_controls": [],
            "submitter": {
                "name": "",
                "tag": "a",
                "target": target,
                "type": "link",
                "value": "",
            },
            "version": 1,
        }
        control = UnsubscribeDiscoveredControl(
            reference=_audited_control_reference(semantics),
            kind="link",
            intent=intent,
        )
        return _AuditedControlBinding(
            control=control,
            target_url=target_url,
            method="GET",
            enctype="",
            successful_controls=(),
        )

    def _form_binding(
        self,
        snapshot: Mapping[str, object],
    ) -> _AuditedControlBinding | None:
        if not snapshot or snapshot.get("error"):
            return None
        intent = self._control_intent(str(snapshot["submitterLabel"]))
        if intent is None:
            return None
        method = str(snapshot["method"]).upper()
        enctype = str(snapshot["enctype"])
        target = str(snapshot["target"])
        if (
            method not in {"GET", "POST"}
            or enctype != "application/x-www-form-urlencoded"
            or target not in {"", "_self"}
            or str(snapshot["acceptCharset"]) not in {"", "utf-8", "utf8"}
        ):
            return None
        page_identity = self._page_identity()
        target_url = self._validate_navigation_target(
            urljoin(page_identity, str(snapshot["action"]) or page_identity)
        )
        fields = tuple(
            (str(item["name"]), str(item["type"]), str(item["value"]))
            for item in snapshot["fields"]
        )
        semantics = {
            "enctype": enctype,
            "form_association": snapshot["formAssociation"],
            "intent": intent,
            "kind": "form",
            "method": method,
            "page_identity": page_identity,
            "policy_reference": self.network_policy.reference,
            "resolved_target": target_url,
            "successful_controls": [
                {"name": name, "type": field_type, "value": value}
                for name, field_type, value in fields
            ],
            "submitter": snapshot["submitter"],
            "version": 1,
        }
        control = UnsubscribeDiscoveredControl(
            reference=_audited_control_reference(semantics),
            kind="form",
            intent=intent,
        )
        return _AuditedControlBinding(
            control=control,
            target_url=target_url,
            method=method,
            enctype=enctype,
            successful_controls=fields,
        )

    def _ordinary_controls(self) -> tuple[_AuditedControlBinding, ...]:
        snapshot = self._trusted_world.evaluate(
            "function() {"
            + _AUTHENTICATION_CONTROL_PREDICATE_JS
            + r"""
              const authenticationControls = trustedDocumentQuery(
                'input, textarea, select, button, a, img, canvas, [role]'
              );
              if (authenticationControls.some(node => isAuthenticationControl(node))) {
                return {blocked: true};
              }

              // No live value/checked/selected state is read above this line.
              const elementHasAttribute = Element.prototype.hasAttribute;
              const elementMatches = Element.prototype.matches;
              const htmlInnerText = Object.getOwnPropertyDescriptor(
                HTMLElement.prototype, 'innerText'
              ).get;
              const inputValue = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
              ).get;
              const inputChecked = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'checked'
              ).get;
              const textAreaValue = Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype, 'value'
              ).get;
              const optionValue = Object.getOwnPropertyDescriptor(
                HTMLOptionElement.prototype, 'value'
              ).get;
              const optionSelected = Object.getOwnPropertyDescriptor(
                HTMLOptionElement.prototype, 'selected'
              ).get;
              const buttonValue = Object.getOwnPropertyDescriptor(
                HTMLButtonElement.prototype, 'value'
              ).get;
              const trustedHasAttribute = (node, name) => reflectApply(
                elementHasAttribute, node, [name]
              );
              const trustedMatches = (node, selector) => reflectApply(
                elementMatches, node, [selector]
              );
              const visible = node => {
                if (trustedHasAttribute(node, 'hidden')) return false;
                const style = getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              const readValue = node => {
                const tag = node.tagName.toLowerCase();
                if (tag === 'input') return reflectApply(inputValue, node, []);
                if (tag === 'textarea') return reflectApply(textAreaValue, node, []);
                if (tag === 'option') return reflectApply(optionValue, node, []);
                if (tag === 'button') return reflectApply(buttonValue, node, []);
                return '';
              };
              const links = [];
              for (const node of trustedDocumentQuery('a[href]').slice(0, 64)) {
                if (!visible(node)) continue;
                links.push({
                  download: trustedHasAttribute(node, 'download'),
                  href: trustedGetAttribute(node, 'href') || '',
                  label: (reflectApply(htmlInnerText, node, []) ||
                    trustedGetAttribute(node, 'aria-label') || '').trim(),
                  target: trustedGetAttribute(node, 'target') || ''
                });
              }

              const forms = [];
              const submitters = trustedDocumentQuery(
                'button:not([type]), button[type=submit], input[type=submit]'
              ).slice(0, 64);
              const documentForms = trustedDocumentQuery('form');
              const supportedInputTypes = new Set([
                'hidden', 'text', 'search', 'tel', 'url', 'email', 'date',
                'month', 'week', 'time', 'datetime-local', 'number', 'range',
                'color', 'checkbox', 'radio'
              ]);
              for (const node of submitters) {
                if (!visible(node)) continue;
                const tag = node.tagName.toLowerCase();
                const type = (trustedGetAttribute(node, 'type') ||
                  (tag === 'button' ? 'submit' : '')).toLowerCase();
                const form = node.form;
                if (!form || type !== 'submit') continue;
                const elements = Array.from(form.elements);
                if (elements.length > 128) {
                  forms.push({error: 'bounds'});
                  continue;
                }
                const fields = [];
                let error = '';
                for (const field of elements) {
                  const fieldTag = field.tagName.toLowerCase();
                  const fieldType = (trustedGetAttribute(field, 'type') ||
                    (fieldTag === 'button' ? 'submit' :
                      fieldTag === 'input' ? 'text' : fieldTag)).toLowerCase();
                  const name = trustedGetAttribute(field, 'name') || '';
                  if (trustedHasAttribute(field, 'dirname')) {
                    error = 'unsupported';
                    break;
                  }
                  if (trustedMatches(field, ':disabled') || !name) continue;
                  if (field === node) {
                    fields.push({name, type: fieldType, value: readValue(field) || ''});
                    continue;
                  }
                  if (fieldTag === 'button' ||
                      ['submit', 'button', 'reset', 'image'].includes(fieldType)) {
                    continue;
                  }
                  if (fieldTag === 'input') {
                    if (!supportedInputTypes.has(fieldType)) {
                      error = 'unsupported';
                      break;
                    }
                    if (['checkbox', 'radio'].includes(fieldType) &&
                        !reflectApply(inputChecked, field, [])) continue;
                    fields.push({name, type: fieldType, value: readValue(field) || ''});
                    continue;
                  }
                  if (fieldTag === 'textarea') {
                    fields.push({name, type: 'textarea', value: readValue(field) || ''});
                    continue;
                  }
                  if (fieldTag === 'select') {
                    for (const option of Array.from(field.options)) {
                      if (reflectApply(optionSelected, option, []) &&
                          !trustedMatches(option, ':disabled')) {
                        fields.push({name, type: 'select', value: readValue(option)});
                      }
                    }
                    continue;
                  }
                  error = 'unsupported';
                  break;
                }
                if (!error && (fields.length > 128 || fields.some(item =>
                  item.name.length > 512 || item.type.length > 64 ||
                  item.value.length > 4096
                ))) error = 'bounds';
                if (error) {
                  forms.push({error});
                  continue;
                }
                const target = (trustedGetAttribute(node, 'formtarget') ||
                  trustedGetAttribute(form, 'target') || '').toLowerCase();
                forms.push({
                  acceptCharset: (trustedGetAttribute(form, 'accept-charset') || '')
                    .trim().toLowerCase(),
                  action: trustedGetAttribute(node, 'formaction') ??
                    trustedGetAttribute(form, 'action') ?? '',
                  enctype: (trustedGetAttribute(node, 'formenctype') ||
                    trustedGetAttribute(form, 'enctype') ||
                    'application/x-www-form-urlencoded').toLowerCase(),
                  fields,
                  formAssociation: {
                    formAttribute: trustedGetAttribute(node, 'form') || '',
                    formId: trustedGetAttribute(form, 'id') || '',
                    formName: trustedGetAttribute(form, 'name') || '',
                    formIndex: documentForms.indexOf(form)
                  },
                  method: (trustedGetAttribute(node, 'formmethod') ||
                    trustedGetAttribute(form, 'method') || 'get').toLowerCase(),
                  submitter: {
                    name: trustedGetAttribute(node, 'name') || '',
                    tag,
                    target,
                    type,
                    value: readValue(node) || ''
                  },
                  submitterLabel: tag === 'input'
                    ? (readValue(node) || trustedGetAttribute(node, 'aria-label') || '')
                    : (reflectApply(htmlInnerText, node, []) ||
                      trustedGetAttribute(node, 'aria-label') || ''),
                  target
                });
              }
              return {blocked: false, forms, links};
            }
            """
        )
        if not isinstance(snapshot, Mapping):
            raise UnsubscribeBrowserError("trusted browser execution unavailable")
        if snapshot.get("blocked") is not False:
            raise UnsubscribeAuthenticationControlsError(
                "authentication controls are not permitted"
            )
        bindings: list[_AuditedControlBinding] = []
        for item in snapshot.get("links", ()):
            if not isinstance(item, Mapping):
                raise UnsubscribeBrowserError("trusted browser execution unavailable")
            binding = self._link_binding(item)
            if binding is not None:
                bindings.append(binding)
        for item in snapshot.get("forms", ()):
            if not isinstance(item, Mapping):
                raise UnsubscribeBrowserError("trusted browser execution unavailable")
            binding = self._form_binding(item)
            if binding is not None:
                bindings.append(binding)
        unique: dict[str, _AuditedControlBinding] = {}
        for binding in bindings:
            unique.setdefault(binding.control.reference, binding)
        return tuple(unique.values())

    @staticmethod
    def _state_from_text(text: str) -> UnsubscribePageState | None:
        normalized = " ".join(text.casefold().split())
        if any(marker in normalized for marker in ("captcha", "验证码")):
            return UnsubscribePageState.CAPTCHA
        if any(
            marker in normalized
            for marker in ("payment", "credit card", "付款", "付费")
        ):
            return UnsubscribePageState.PAYMENT
        if any(marker in normalized for marker in ("sign in", "log in", "login", "password", "登录")):
            return UnsubscribePageState.LOGIN_REQUIRED
        if any(marker in normalized for marker in ("already unsubscribed", "no longer subscribed", "已经退订")):
            return UnsubscribePageState.ALREADY_UNSUBSCRIBED
        if any(
            marker in normalized
            for marker in (
                "successfully unsubscribed",
                "you are unsubscribed",
                "unsubscribe complete",
                "unsubscribe confirmation complete",
                "subscription cancelled",
                "list-unsubscribe post returned http 2",
                "退订成功",
            )
        ):
            return UnsubscribePageState.DONE
        return None

    def discover_current_page(
        self,
        effect: EmailUnsubscribeEffect,
    ) -> UnsubscribePageDiscovery:
        """Read only the already-loaded unsubscribe page and expose opaque controls."""

        self._raise_if_blocked()
        current_url = self._document_url or getattr(self.page, "url")
        if current_url == "about:blank":
            return UnsubscribePageDiscovery(
                state=UnsubscribePageState.ACTION_REQUIRED,
                state_reference="state-not-opened",
                controls=(),
            )
        self._validate_navigation_target(current_url)
        # Credential rejection and all ordinary form-state reads are one
        # atomic isolated-world evaluation inside _ordinary_controls().
        bindings = self._ordinary_controls()
        text = self._visible_text()
        state = self._state_from_text(text)
        controls = tuple(item.control for item in bindings)
        if state is None:
            state = UnsubscribePageState.ACTION_REQUIRED
            if (
                not controls
                and "confirmation email" in " ".join(text.casefold().split())
                and self.confirmation_target_resolver is not None
            ):
                target = self.confirmation_target_resolver(effect)
                if target is None or (
                    target.effect_digest != effect.effect_digest
                    or target.target_reference
                    != confirmation_target_reference(
                        target.confirmation_message_identity
                    )
                ):
                    raise UnsubscribeBrowserError(
                        "confirmation target binding rejected"
                    )
                controls = (
                    UnsubscribeDiscoveredControl(
                        reference=target.target_reference,
                        kind="confirmation_email",
                        intent="confirm",
                    ),
                )
            if not controls and not any(
                operation.kind is UnsubscribeOperationKind.CONFIRM_EMAIL
                for operation in effect.operations
            ):
                raise UnsubscribeBrowserError("unsubscribe page state is unknown")
        state_reference = "state:" + sha256(
            f"{state.value}\n{' '.join(text.casefold().split())}".encode()
        ).hexdigest()
        return UnsubscribePageDiscovery(
            state=state,
            state_reference=state_reference,
            controls=controls,
        )

    def _sanitized_audit_snapshot(self) -> Mapping[str, object]:
        """Clone and scrub the page without exposing credential live state."""

        sanitized = self._trusted_world.evaluate(
            "function() {"
            + _AUTHENTICATION_CONTROL_PREDICATE_JS
            + r"""
              const authenticationControls = trustedDocumentQuery(
                'input, textarea, select, button, a, img, canvas, [role]'
              );
              const blocked = authenticationControls.some(
                node => isAuthenticationControl(node)
              );

              // Detection is complete before any live value/checked/selected read.
              const nodeClone = Node.prototype.cloneNode;
              const nodeTextContent = Object.getOwnPropertyDescriptor(
                Node.prototype, 'textContent'
              );
              const elementQuerySelectorAll = Element.prototype.querySelectorAll;
              const elementSetAttribute = Element.prototype.setAttribute;
              const elementRemoveAttribute = Element.prototype.removeAttribute;
              const elementRemove = Element.prototype.remove;
              const elementOuterHTML = Object.getOwnPropertyDescriptor(
                Element.prototype, 'outerHTML'
              ).get;
              const inputValue = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'value'
              ).get;
              const inputChecked = Object.getOwnPropertyDescriptor(
                HTMLInputElement.prototype, 'checked'
              ).get;
              const textAreaValue = Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype, 'value'
              ).get;
              const optionSelected = Object.getOwnPropertyDescriptor(
                HTMLOptionElement.prototype, 'selected'
              ).get;
              const trustedElementQuery = (node, selector) => Array.from(
                reflectApply(elementQuerySelectorAll, node, [selector])
              );
              const setAttribute = (node, name, value) => reflectApply(
                elementSetAttribute, node, [name, value]
              );
              const removeAttribute = (node, name) => reflectApply(
                elementRemoveAttribute, node, [name]
              );
              const setTextContent = (node, value) => reflectApply(
                nodeTextContent.set, node, [value]
              );
              const clone = reflectApply(nodeClone, document.documentElement, [true]);
              const originals = trustedDocumentQuery(
                'input, textarea, select, option'
              );
              const copies = trustedElementQuery(
                clone, 'input, textarea, select, option'
              );
              if (!blocked) {
                for (let index = 0; index < originals.length; index += 1) {
                  const source = originals[index];
                  const target = copies[index];
                  if (!target) continue;
                  const tag = source.tagName.toLowerCase();
                  if (tag === 'input') {
                    setAttribute(
                      target, 'value', reflectApply(inputValue, source, []) || ''
                    );
                    if (reflectApply(inputChecked, source, [])) {
                      setAttribute(target, 'checked', '');
                    } else removeAttribute(target, 'checked');
                  } else if (tag === 'textarea') {
                    setTextContent(
                      target,
                      reflectApply(textAreaValue, source, []) || ''
                    );
                  } else if (tag === 'option') {
                    if (reflectApply(optionSelected, source, [])) {
                      setAttribute(target, 'selected', '');
                    } else removeAttribute(target, 'selected');
                  }
                }
              }
              trustedElementQuery(
                clone, 'input, textarea, select, option'
              ).forEach(node => {
                const tag = node.tagName.toLowerCase();
                const sensitiveNode = tag === 'option'
                  ? isAuthenticationControl(node.parentElement)
                  : isAuthenticationControl(node);
                if (!sensitiveNode) return;
                removeAttribute(node, 'value');
                removeAttribute(node, 'checked');
                removeAttribute(node, 'selected');
                if (tag === 'textarea' || tag === 'option') setTextContent(node, '');
              });
              trustedElementQuery(
                clone,
                'script, iframe, object, embed, img, source, video, audio, '
                  + 'link, style, base, meta[http-equiv="refresh"]'
              ).forEach(node => reflectApply(elementRemove, node, []));
              trustedElementQuery(clone, '*').forEach(node => {
                for (const attribute of Array.from(node.attributes)) {
                  const name = attribute.name.toLowerCase();
                  if (name.startsWith('on') || [
                    'src', 'srcset', 'poster', 'background', 'ping', 'style'
                  ].includes(name)) removeAttribute(node, attribute.name);
                }
              });
              return {
                blocked,
                html: '<!doctype html>' + reflectApply(elementOuterHTML, clone, [])
              };
            }
            """
        )
        if (
            not isinstance(sanitized, Mapping)
            or sanitized.get("blocked") not in {True, False}
            or not isinstance(sanitized.get("html"), str)
        ):
            raise UnsubscribeBrowserError("trusted browser execution unavailable")
        return sanitized

    def capture_audit_session(
        self,
        effect: EmailUnsubscribeEffect,
    ) -> dict[str, object]:
        """Capture an inert, owner-only DOM snapshot for the next Audit round."""

        discovery = self.discover_current_page(effect)
        if (
            discovery.state is not UnsubscribePageState.ACTION_REQUIRED
            or not discovery.controls
        ):
            raise UnsubscribeBrowserError("browser session capture rejected")
        sanitized = self._sanitized_audit_snapshot()
        if sanitized.get("blocked") is not False:
            raise UnsubscribeAuthenticationControlsError(
                "authentication controls are not permitted"
            )
        html = sanitized.get("html")
        if (
            not isinstance(html, str)
            or not html.strip()
            or len(html.encode("utf-8")) > _MAX_AUDIT_SESSION_HTML_BYTES
        ):
            raise UnsubscribeBrowserError("browser session capture rejected")
        document_url = self._page_identity()
        cookies = list(
            _validated_audit_session_cookies(
                self._context.cookies([document_url]),
                document_url=document_url,
            )
        )
        return {
            "version": 1,
            "action_identity": effect.action_identity,
            "effect_digest": effect.effect_digest,
            "entry_reference": effect.entry_reference,
            "document_url": document_url,
            "html": html,
            "html_digest": sha256(html.encode("utf-8")).hexdigest(),
            "cookies": cookies,
            "cookies_digest": sha256(
                json.dumps(
                    cookies,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "control_references": [
                control.reference for control in discovery.controls
            ],
            "network_policy_reference": effect.network_policy_reference,
            "network_policy_origin_references": list(
                effect.network_policy_origin_references
            ),
        }

    def validate_restored_audit_session(
        self,
        effect: EmailUnsubscribeEffect,
        session: _RestoredAuditSession,
        *,
        executed_prefix_length: int,
    ) -> None:
        """Re-discover the exact accepted control before any resumed write."""

        discovery = self.discover_current_page(effect)
        controls = {control.reference: control for control in discovery.controls}
        if (
            discovery.state is not UnsubscribePageState.ACTION_REQUIRED
            or set(controls) != set(session.control_references)
        ):
            raise UnsubscribeBrowserError("browser session control changed")
        appended = effect.operations[executed_prefix_length]
        expected_kind = {
            UnsubscribeOperationKind.SUBMIT_FORM: "form",
            UnsubscribeOperationKind.CLICK_CONFIRMATION: "link",
        }.get(appended.kind)
        control = controls.get(appended.target_reference)
        if control is None or control.kind != expected_kind:
            raise UnsubscribeBrowserError("browser session control changed")

    def _verify_effect_network_policy(self, effect: EmailUnsubscribeEffect) -> None:
        if effect.network_policy_reference != self.network_policy.reference or (
            effect.network_policy_origin_references
            != self.network_policy.origin_references
        ):
            raise UnsubscribeBrowserError("browser network policy binding rejected")

    def find_confirmation_receipt(
        self,
        effect: EmailUnsubscribeEffect,
    ) -> UnsubscribeTerminalReceipt | None:
        if self.confirmation_receipt_resolver is None:
            return None
        try:
            return self.confirmation_receipt_resolver(effect)
        except UnsubscribeProviderAuthError:
            raise
        except Exception:
            raise UnsubscribeBrowserError(
                "confirmation receipt readback failed"
            ) from None

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        try:
            self._verify_effect_network_policy(effect)
            if not self._document_url and getattr(self.page, "url") == "about:blank":
                if not effect.operations:
                    raise UnsubscribeBrowserError(
                        "accepted operation sequence is empty"
                    )
                return UnsubscribeObservation(
                    state=UnsubscribePageState.ACTION_REQUIRED,
                    state_reference="state-not-opened",
                    next_operation_reference=effect.operations[0].operation_reference,
                )
            del private_url
            discovery = self.discover_current_page(effect)
            visible_text = self._visible_text()
            state = discovery.state
            state_reference = discovery.state_reference
            if state is UnsubscribePageState.ACTION_REQUIRED:
                return UnsubscribeObservation(
                    state=state,
                    state_reference=state_reference,
                    controls=discovery.controls,
                    visible_text=visible_text,
                )
            receipt_id = f"unsubscribe-receipt:{effect.effect_digest[:24]}:{state.value}"
            return UnsubscribeObservation(
                state=state,
                state_reference=state_reference,
                receipt=UnsubscribeTerminalReceipt(
                    receipt_id=receipt_id,
                    evidence="terminal-page",
                    entry_reference=effect.entry_reference,
                    effect_digest=effect.effect_digest,
                ),
                visible_text=visible_text,
            )
        except UnsubscribeBrowserError:
            raise
        except Exception:
            raise UnsubscribeBrowserError("browser state readback failed") from None

    def _execute_audited_control(
        self,
        binding: _AuditedControlBinding,
    ) -> None:
        self._validate_navigation_target(binding.target_url)
        if binding.control.kind == "link":
            self.page.goto(
                binding.target_url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            self._raise_if_blocked()
            self._document_url = self._validate_navigation_target(
                str(getattr(self.page, "url"))
            )
            return
        if binding.control.kind != "form":
            raise UnsubscribeBrowserError("accepted browser control is unavailable")
        pairs = [(name, value) for name, _field_type, value in binding.successful_controls]
        encoded = urlencode(pairs)
        if binding.method == "GET":
            parsed = urlsplit(binding.target_url)
            query = "&".join(item for item in (parsed.query, encoded) if item)
            target = parsed._replace(query=query).geturl()
            self.page.goto(
                self._validate_navigation_target(target),
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            self._raise_if_blocked()
            self._document_url = self._validate_navigation_target(
                str(getattr(self.page, "url"))
            )
            return
        if binding.method != "POST" or binding.enctype != (
            "application/x-www-form-urlencoded"
        ):
            raise UnsubscribeBrowserError("browser control semantics rejected")
        response = self._context.request.post(
            binding.target_url,
            data=encoded,
            headers={"Content-Type": binding.enctype},
            max_redirects=0,
            timeout=self.timeout_ms,
        )
        response_url = self._validate_navigation_target(response.url)
        if response.status < 200 or response.status >= 300:
            raise UnsubscribeBrowserError("form provider response rejected")
        body = response.body()
        if len(body) > 1_048_576:
            raise UnsubscribeBrowserError("form provider response rejected")
        self._document_url = response_url
        self.page.set_content(
            response.text(),
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._raise_if_blocked()

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        try:
            self._verify_effect_network_policy(effect)
            if operation.kind is UnsubscribeOperationKind.POST_ONE_CLICK:
                self._validate_navigation_target(private_url)
                isolated = self._context.browser.new_context(accept_downloads=False)
                try:
                    response = isolated.request.post(
                        private_url,
                        data="List-Unsubscribe=One-Click",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        max_redirects=0,
                        timeout=self.timeout_ms,
                    )
                    self._validate_navigation_target(response.url)
                    if response.status < 200 or response.status >= 300:
                        raise UnsubscribeBrowserError("one-click provider response rejected")
                    visible = response.text()
                    if not visible.strip():
                        visible = f"List-Unsubscribe POST returned HTTP {response.status}"
                finally:
                    isolated.close()
                state = self._state_from_text(visible)
                if state not in {
                    UnsubscribePageState.DONE,
                    UnsubscribePageState.ALREADY_UNSUBSCRIBED,
                }:
                    raise UnsubscribeBrowserError("one-click outcome is unverified")
                return UnsubscribeObservation(
                    state=state,
                    state_reference="state-one-click-terminal",
                    receipt=UnsubscribeTerminalReceipt(
                        receipt_id=f"unsubscribe-receipt:{effect.effect_digest[:24]}:one-click",
                        evidence="one-click-provider",
                        entry_reference=effect.entry_reference,
                        effect_digest=effect.effect_digest,
                    ),
                    visible_text=visible,
                )
            if operation.kind is UnsubscribeOperationKind.OPEN_ENTRY:
                self.page.goto(
                    self._validate_navigation_target(private_url),
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                self._raise_if_blocked()
                self._document_url = self._validate_navigation_target(
                    getattr(self.page, "url")
                )
            elif operation.kind is UnsubscribeOperationKind.FOLLOW_REDIRECT:
                self._raise_if_blocked()
                self._document_url = self._validate_navigation_target(
                    getattr(self.page, "url")
                )
            elif operation.kind is UnsubscribeOperationKind.SUBMIT_FORM:
                control = next(
                    (
                        binding
                        for binding in self._ordinary_controls()
                        if binding.control.reference == operation.target_reference
                        and binding.control.kind == "form"
                    ),
                    None,
                )
                if control is None:
                    raise UnsubscribeBrowserError("accepted browser control is unavailable")
                self._execute_audited_control(control)
            elif operation.kind is UnsubscribeOperationKind.CLICK_CONFIRMATION:
                control = next(
                    (
                        binding
                        for binding in self._ordinary_controls()
                        if binding.control.reference == operation.target_reference
                        and binding.control.kind == "link"
                    ),
                    None,
                )
                if control is None:
                    raise UnsubscribeBrowserError("accepted browser control is unavailable")
                self._execute_audited_control(control)
            elif operation.kind is UnsubscribeOperationKind.CONFIRM_EMAIL:
                if self.confirmation_target_resolver is None:
                    raise UnsubscribeProviderAuthError(
                        "confirmation mailbox provider is unavailable"
                    )
                target = self.confirmation_target_resolver(effect)
                if target is None:
                    raise UnsubscribeBrowserError(
                        "confirmation email has no accepted entry"
                    )
                expected_reference = confirmation_target_reference(
                    target.confirmation_message_identity,
                )
                if (
                    target.effect_digest != effect.effect_digest
                    or target.target_reference != operation.target_reference
                    or target.target_reference != expected_reference
                ):
                    raise UnsubscribeBrowserError("confirmation target binding rejected")
                self.page.goto(
                    self._validate_navigation_target(target.private_url),
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                self._raise_if_blocked()
                self._document_url = self._validate_navigation_target(
                    getattr(self.page, "url")
                )
            else:
                raise UnsubscribeBrowserError("browser operation kind rejected")
            observation = self.inspect_current_state(effect, private_url)
            if (
                operation.kind is UnsubscribeOperationKind.CONFIRM_EMAIL
                and observation.receipt is not None
            ):
                target = self.confirmation_target_resolver(effect)
                assert target is not None
                observation = UnsubscribeObservation(
                    state=observation.state,
                    state_reference=observation.state_reference,
                    receipt=UnsubscribeTerminalReceipt(
                        receipt_id=observation.receipt.receipt_id,
                        evidence="confirmation-mail:" + sha256(
                            target.confirmation_message_identity.encode()
                        ).hexdigest(),
                        entry_reference=effect.entry_reference,
                        effect_digest=effect.effect_digest,
                    ),
                    visible_text=observation.visible_text,
                )
            return observation
        except (UnsubscribeBrowserError, UnsubscribeProviderAuthError):
            raise
        except Exception:
            raise UnsubscribeBrowserError("browser operation failed") from None


def execute_unsubscribe_in_dedicated_profile(
    effect: EmailUnsubscribeEffect,
    entries: tuple[UnsubscribeEntry, ...],
    *,
    store: EmailStore,
    profile: object,
    network_policy: BrowserNetworkPolicy,
    owner: Mapping[str, object],
    timeout_ms: int = 5_000,
    executed_prefix_length: int | None = None,
) -> UnsubscribeExecutionResult | UnsubscribeContinuationResult:
    """Run one unsubscribe effect only in a locked headless profile.

    The profile and lock are supplied by the service runtime.  This helper has
    no path or URL arguments: the entry URL is retained only in the in-memory
    ``UnsubscribeEntry`` selected by the task-bound operation.
    """

    from app.email_browser_profile import (
        EmailBrowserProfileError,
        launch_persistent_email_context,
    )

    def session_failure() -> UnsubscribeExecutionResult:
        journal = [
            RedactedUnsubscribeStep(
                operation=step["operation"],
                state=step["state"],
                reference=step["reference"],
            )
            for step in store.list_email_unsubscribe_steps(
                effect.action_identity
            )
        ]
        try:
            store.mark_email_unsubscribe_uncertain(
                effect.action_identity,
                owner=owner,
            )
        except EmailUnsubscribeClaimConflict:
            pass
        return _result(
            UnsubscribeOutcome.FAILED_BROWSER,
            journal,
            error_code="email_unsubscribe_browser_session_unavailable",
        )

    def clear_session() -> None:
        try:
            profile.clear_audit_session(effect.action_identity)
        except (EmailBrowserProfileError, OSError):
            pass

    def fresh_isolated_page(context: object) -> object:
        page = context.new_page()
        for existing in tuple(getattr(context, "pages", ())):
            if existing is page:
                continue
            existing.close()
        if str(getattr(page, "url")) != "about:blank":
            raise UnsubscribeBrowserError("browser session restore rejected")
        return page

    def restore_inert_page(
        page: object,
        session: _RestoredAuditSession,
    ) -> None:
        def reject_request(route: object) -> None:
            route.abort()

        page.route("**/*", reject_request)
        try:
            page.set_content(
                session.html,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        finally:
            page.unroute("**/*", reject_request)
        if str(getattr(page, "url")) != "about:blank":
            raise UnsubscribeBrowserError("browser session restore rejected")

    with profile.lock():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise UnsubscribeBrowserError(
                "headless browser runtime is unavailable"
            ) from exc
        with sync_playwright() as playwright:
            context = launch_persistent_email_context(playwright, profile)
            try:
                page = fresh_isolated_page(context)
                restored_session: _RestoredAuditSession | None = None
                if executed_prefix_length is not None:
                    if executed_prefix_length > 0:
                        try:
                            payload = profile.load_audit_session(
                                effect.action_identity
                            )
                            if payload is None:
                                return session_failure()
                            restored_session = _validated_restored_audit_session(
                                payload,
                                effect,
                                executed_prefix_length=executed_prefix_length,
                            )
                            network_policy.validate_url(
                                restored_session.document_url
                            )
                            context.add_cookies(
                                [dict(item) for item in restored_session.cookies]
                            )
                            restore_inert_page(page, restored_session)
                        except Exception:
                            clear_session()
                            return session_failure()
                    else:
                        try:
                            profile.clear_audit_session(effect.action_identity)
                        except (EmailBrowserProfileError, OSError):
                            return session_failure()
                browser = PlaywrightUnsubscribeBrowser(
                    page,
                    timeout_ms=timeout_ms,
                    network_policy=network_policy,
                    restored_document_url=(
                        ""
                        if restored_session is None
                        else restored_session.document_url
                    ),
                )
                if restored_session is not None:
                    try:
                        browser.validate_restored_audit_session(
                            effect,
                            restored_session,
                            executed_prefix_length=executed_prefix_length,
                        )
                    except UnsubscribeBrowserError:
                        clear_session()
                        return session_failure()
                result = UnsubscribeExecutor(
                    store,
                    browser,
                    owner=owner,
                ).execute(
                    effect,
                    entries,
                    executed_prefix_length=executed_prefix_length,
                )
                if isinstance(result, UnsubscribeContinuationResult):
                    try:
                        profile.save_audit_session(
                            effect.action_identity,
                            browser.capture_audit_session(effect),
                        )
                    except (EmailBrowserProfileError, UnsubscribeBrowserError, OSError):
                        clear_session()
                        return session_failure()
                else:
                    clear_session()
                return result
            finally:
                context.close()


def _header_values(value: str) -> tuple[str, ...]:
    values: list[str] = []
    start = 0
    while True:
        left = value.find("<", start)
        if left < 0:
            break
        right = value.find(">", left + 1)
        if right < 0:
            break
        candidate = value[left + 1 : right].strip()
        if candidate:
            values.append(candidate)
        start = right + 1
    if values:
        return tuple(values)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _contains_unsubscribe_marker(value: str) -> bool:
    normalized = " ".join(value.casefold().replace("_", " ").split())
    return any(marker in normalized for marker in _UNSUBSCRIBE_MARKERS)


class _BodyLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._labels: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.casefold() != "a":
            return
        values = {name.casefold(): value or "" for name, value in attrs}
        self._href = values.get("href", "").strip()
        self._labels = [
            values.get("title", ""),
            values.get("aria-label", ""),
            values.get("rel", ""),
        ]

    def handle_data(self, data: str) -> None:
        if self._href:
            self._labels.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            self.links.append((self._href, " ".join(self._labels)))
            self._href = ""
            self._labels = []


def _text_https_links(body_text: str) -> tuple[str, ...]:
    tokens = body_text.split()
    links: list[str] = []
    for index, token in enumerate(tokens):
        start = token.casefold().find("https://")
        if start < 0:
            continue
        candidate = token[start:].strip("'\"()[]{}<>,.;，。；")
        context = " ".join(tokens[max(0, index - 4) : index + 1])
        if _is_private_https_url(candidate) and (
            _contains_unsubscribe_marker(context)
            or _contains_unsubscribe_marker(urlsplit(candidate).path)
        ):
            links.append(candidate)
    return tuple(links)


def extract_unsubscribe_entries(
    *,
    list_unsubscribe: str = "",
    list_unsubscribe_post: str = "",
    body_text: str = "",
    body_html: str = "",
    authentication_evidence: UnsubscribeAuthenticationEvidence | None = None,
    allow_loopback_for_tests: bool = False,
) -> tuple[UnsubscribeEntry, ...]:
    """Extract standard and explicit body entries, retaining URLs in memory only."""

    candidates: list[tuple[UnsubscribeEntrySource, str, int]] = []
    one_click = (
        "".join(list_unsubscribe_post.casefold().split())
        == "list-unsubscribe=one-click"
        and authentication_evidence is not None
        and authentication_evidence.one_click_verified
    )
    for value in _header_values(list_unsubscribe):
        if _is_private_https_url(value) or (
            allow_loopback_for_tests and _is_loopback_http_url(value)
        ):
            candidates.append(
                (
                    UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS
                    if one_click
                    else UnsubscribeEntrySource.HEADER_HTTPS,
                    value,
                    0 if one_click else 10,
                )
            )
        elif _is_mailto_url(value):
            candidates.append((UnsubscribeEntrySource.HEADER_MAILTO, value, 20))

    parser = _BodyLinkParser()
    parser.feed(body_html)
    parser.close()
    for value, label in parser.links:
        if _is_private_https_url(value) and (
            _contains_unsubscribe_marker(label)
            or _contains_unsubscribe_marker(urlsplit(value).path)
        ):
            candidates.append((UnsubscribeEntrySource.BODY_HTML_HTTPS, value, 30))
    for value in _text_https_links(body_text):
        candidates.append((UnsubscribeEntrySource.BODY_TEXT_HTTPS, value, 40))

    by_url: dict[str, UnsubscribeEntry] = {}
    for source, private_url, priority in candidates:
        current = by_url.get(private_url)
        if current is None or priority < current.priority:
            by_url[private_url] = UnsubscribeEntry(
                source=source,
                reference=unsubscribe_entry_reference(private_url),
                private_url=private_url,
                priority=priority,
            )
    return tuple(
        sorted(by_url.values(), key=lambda item: (item.priority, item.reference))
    )


def select_browser_unsubscribe_entry(
    entries: tuple[UnsubscribeEntry, ...],
) -> UnsubscribeEntry | None:
    browser_entries = [
        item for item in entries if _is_private_browser_url(item.private_url)
    ]
    return min(
        browser_entries,
        key=lambda item: (item.priority, item.reference),
        default=None,
    )


def disposition_for_unsubscribe_outcome(
    outcome: UnsubscribeOutcome,
) -> UnsubscribeDisposition:
    if outcome in {
        UnsubscribeOutcome.DONE,
        UnsubscribeOutcome.ALREADY_UNSUBSCRIBED,
    }:
        return UnsubscribeDisposition("done", False, False)
    if outcome in {
        UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY,
        UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED,
        UnsubscribeOutcome.SKIPPED_CAPTCHA,
        UnsubscribeOutcome.SKIPPED_PAYMENT,
    }:
        return UnsubscribeDisposition("skipped", False, False)
    return UnsubscribeDisposition("failed", True, True)


def _result(
    outcome: UnsubscribeOutcome,
    journal: list[RedactedUnsubscribeStep],
    *,
    receipt: UnsubscribeTerminalReceipt | None = None,
    error_code: str = "",
    result_text: str = "",
    observation_digest: str = "",
    started_at: str = "",
    completed_at: str = "",
) -> UnsubscribeExecutionResult:
    return UnsubscribeExecutionResult(
        outcome=outcome,
        disposition=disposition_for_unsubscribe_outcome(outcome),
        journal=tuple(journal),
        receipt=receipt,
        error_code=error_code,
        result_text=result_text,
        observation_digest=observation_digest,
        started_at=started_at,
        completed_at=completed_at,
    )


def _terminal_result(
    effect: EmailUnsubscribeEffect,
    observation: UnsubscribeObservation,
    journal: list[RedactedUnsubscribeStep],
) -> UnsubscribeExecutionResult | None:
    outcomes = {
        UnsubscribePageState.DONE: UnsubscribeOutcome.DONE,
        UnsubscribePageState.ALREADY_UNSUBSCRIBED: (
            UnsubscribeOutcome.ALREADY_UNSUBSCRIBED
        ),
        UnsubscribePageState.LOGIN_REQUIRED: (
            UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED
        ),
        UnsubscribePageState.CAPTCHA: UnsubscribeOutcome.SKIPPED_CAPTCHA,
        UnsubscribePageState.PAYMENT: UnsubscribeOutcome.SKIPPED_PAYMENT,
    }
    outcome = outcomes.get(observation.state)
    if outcome is None:
        return None
    if (
        observation.receipt is None
        or observation.receipt.entry_reference != effect.entry_reference
        or observation.receipt.effect_digest != effect.effect_digest
    ):
        return _result(
            UnsubscribeOutcome.FAILED_BROWSER,
            journal,
            error_code="email_unsubscribe_receipt_mismatch",
        )
    result_text, observation_digest = normalize_unsubscribe_result_text(
        observation.visible_text
    )
    return _result(
        outcome,
        journal,
        receipt=observation.receipt,
        result_text=result_text,
        observation_digest=observation_digest if observation.visible_text else "",
    )


class UnsubscribeExecutor:
    """Execute accepted operations behind a durable current-authorization fence."""

    def __init__(
        self,
        store: EmailStore,
        browser: UnsubscribeBrowser,
        *,
        owner: Mapping[str, object],
    ) -> None:
        self.store = store
        self.browser = browser
        self.owner = dict(owner)

    @staticmethod
    def _store_arguments(effect: EmailUnsubscribeEffect) -> dict[str, object]:
        return {
            "action_identity": effect.action_identity,
            "effect_digest": effect.effect_digest,
            "action_plan_id": effect.action_plan_id,
            "action_plan_version": effect.action_plan_version,
            "classification_id": effect.classification_id,
            "account_id": effect.account_id,
            "stable_message_identity": effect.stable_message_identity,
            "thread_identity": effect.thread_identity,
            "entry_reference": effect.entry_reference,
            "operations": effect.operation_mappings,
            "previous_effect_digest": effect.previous_effect_digest,
            "network_policy_reference": effect.network_policy_reference,
            "network_policy_origin_references": effect.network_policy_origin_references,
        }

    def _continuation_result(
        self,
        effect: EmailUnsubscribeEffect,
        journal: list[RedactedUnsubscribeStep],
    ) -> UnsubscribeContinuationResult | None:
        durable = self.store.get_email_unsubscribe_continuation(effect.action_identity)
        if durable is None or durable["effect_digest"] != effect.effect_digest:
            return None
        return UnsubscribeContinuationResult(
            continuation=EmailUnsubscribeContinuation(
                action_identity=effect.action_identity,
                action_plan_id=effect.action_plan_id,
                action_plan_version=effect.action_plan_version,
                classification_id=effect.classification_id,
                account_id=effect.account_id,
                stable_message_identity=effect.stable_message_identity,
                thread_identity=effect.thread_identity,
                entry_reference=effect.entry_reference,
                effect_digest=effect.effect_digest,
                previous_effect_digest=durable["previous_effect_digest"],
                executed_operations=tuple(
                    UnsubscribeOperation.from_mapping(item)
                    for item in durable["operations"]
                ),
                controls=tuple(
                    UnsubscribeDiscoveredControl(**item)
                    for item in durable["controls"]
                ),
                network_policy_reference=durable["network_policy_reference"],
                network_policy_origin_references=tuple(
                    durable["network_policy_origin_references"]
                ),
            ),
            journal=tuple(journal),
        )

    def _durable_result(
        self,
        effect: EmailUnsubscribeEffect,
    ) -> UnsubscribeExecutionResult | None:
        receipt = self.store.get_email_unsubscribe_receipt(effect.action_identity)
        if receipt is None:
            return None
        if receipt["effect_digest"] != effect.effect_digest:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                [],
                error_code="email_unsubscribe_receipt_mismatch",
            )
        journal = [
            RedactedUnsubscribeStep(
                operation=step["operation"],
                state=step["state"],
                reference=step["reference"],
            )
            for step in self.store.list_email_unsubscribe_steps(effect.action_identity)
        ]
        terminal_receipt = UnsubscribeTerminalReceipt(
            receipt_id=receipt["receipt_id"],
            evidence=receipt["evidence"],
            entry_reference=receipt["entry_reference"],
            effect_digest=receipt["effect_digest"],
        )
        return _result(
            UnsubscribeOutcome(receipt["outcome"]),
            journal,
            receipt=terminal_receipt,
            result_text=receipt["result_text"],
            observation_digest=receipt["observation_digest"],
            started_at=receipt["started_at"],
            completed_at=receipt["completed_at"],
        )

    def _persist_terminal(
        self,
        effect: EmailUnsubscribeEffect,
        outcome: UnsubscribeOutcome,
        receipt: UnsubscribeTerminalReceipt,
        journal: list[RedactedUnsubscribeStep],
        *,
        final_step: RedactedUnsubscribeStep | None,
        claim_owned: bool,
        result_text: str = "",
        observation_digest: str = "",
    ) -> UnsubscribeExecutionResult:
        if receipt.effect_digest != effect.effect_digest:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code="email_unsubscribe_receipt_mismatch",
            )
        persisted_steps = self.store.list_email_unsubscribe_steps(
            effect.action_identity
        )
        final_mapping = None
        if final_step is not None:
            final_mapping = {
                "sequence": len(persisted_steps) + 1,
                "operation": final_step.operation,
                "state": final_step.state,
                "reference": final_step.reference,
            }
        try:
            persisted = self.store.persist_email_unsubscribe_terminal(
                **self._store_arguments(effect),
                outcome=outcome.value,
                receipt_id=receipt.receipt_id,
                evidence=receipt.evidence,
                result_text=result_text,
                observation_digest=observation_digest,
                final_step=final_mapping,
                claim_owner=self.owner if claim_owned else None,
            )
        except (EmailUnsubscribeClaimConflict, EmailUnsubscribeReceiptConflict):
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code="email_unsubscribe_persistence_conflict",
            )
        if final_step is not None:
            journal.append(final_step)
        return _result(
            outcome,
            journal,
            receipt=receipt,
            result_text=persisted["result_text"],
            observation_digest=persisted["observation_digest"],
            started_at=persisted["started_at"],
            completed_at=persisted["completed_at"],
        )

    def _claim_write(self, effect: EmailUnsubscribeEffect) -> Mapping[str, object] | None:
        try:
            claim = self.store.claim_email_unsubscribe_write(
                **self._store_arguments(effect),
                owner=self.owner,
            )
        except (EmailUnsubscribeClaimConflict, EmailUnsubscribeReceiptConflict):
            return None
        if claim is None:
            return None
        if not claim.get("acquired"):
            if not (
                claim.get("status") == "dispatching"
                and claim.get("effect_digest") == effect.effect_digest
                and claim.get("owner_id") == self.owner.get("owner_id")
                and claim.get("owner_generation") == self.owner.get("generation")
                and claim.get("lease_token") == self.owner.get("lease_token")
                and claim.get("operations") == list(effect.operation_mappings)
            ):
                return None
            claim = {
                **claim,
                "acquired": True,
                "executed_prefix_length": max(0, len(effect.operations) - 1),
            }
        try:
            self.store.advance_email_unsubscribe_phase(
                effect.action_identity,
                "navigating",
                owner=self.owner,
            )
        except EmailUnsubscribeClaimConflict:
            return None
        return claim

    def _validated_preclaimed_write(
        self,
        effect: EmailUnsubscribeEffect,
        executed_prefix_length: int,
    ) -> Mapping[str, object] | None:
        """Validate the Audit claim's durable continuation prefix fail-closed."""

        if (
            not isinstance(executed_prefix_length, int)
            or isinstance(executed_prefix_length, bool)
            or executed_prefix_length < 0
            or executed_prefix_length != len(effect.operations) - 1
        ):
            return None
        claim = self.store.get_email_unsubscribe_claim(effect.action_identity)
        durable_effect = self.store.get_email_unsubscribe_effect(
            effect.action_identity,
            effect.effect_digest,
        )
        operations = list(effect.operation_mappings)
        if (
            claim is None
            or durable_effect is None
            or claim.get("status") != "dispatching"
            or claim.get("effect_digest") != effect.effect_digest
            or claim.get("operations") != operations
            or claim.get("owner_id") != self.owner.get("owner_id")
            or claim.get("owner_generation") != self.owner.get("generation")
            or claim.get("lease_token") != self.owner.get("lease_token")
            or durable_effect.get("operations") != operations
            or durable_effect.get("previous_effect_digest")
            != effect.previous_effect_digest
            or durable_effect.get("network_policy_reference")
            != effect.network_policy_reference
            or durable_effect.get("network_policy_origin_references")
            != list(effect.network_policy_origin_references)
            or durable_effect.get("audit_agent_run_id")
            != claim.get("audit_agent_run_id")
        ):
            return None
        steps = self.store.list_email_unsubscribe_steps(effect.action_identity)
        if len(steps) != executed_prefix_length or any(
            step["sequence"] != index
            or step["operation"] != effect.operations[index - 1].kind.value
            for index, step in enumerate(steps, start=1)
        ):
            return None
        if executed_prefix_length == 0:
            if effect.previous_effect_digest:
                return None
        else:
            previous_effect = self.store.get_email_unsubscribe_effect(
                effect.action_identity,
                effect.previous_effect_digest,
            )
            if (
                previous_effect is None
                or previous_effect.get("effect_digest")
                != effect.previous_effect_digest
                or previous_effect.get("operations")
                != operations[:executed_prefix_length]
            ):
                return None
        return {
            **claim,
            "acquired": True,
            "executed_prefix_length": executed_prefix_length,
        }

    def execute(
        self,
        effect: EmailUnsubscribeEffect,
        entries: tuple[UnsubscribeEntry, ...],
        *,
        executed_prefix_length: int | None = None,
    ) -> UnsubscribeExecutionResult | UnsubscribeContinuationResult:
        durable = self._durable_result(effect)
        if durable is not None:
            return durable
        claim = self.store.get_email_unsubscribe_claim(effect.action_identity)
        reconciliation_only = claim is not None and claim["status"] == "uncertain"
        journal = [
            RedactedUnsubscribeStep(
                operation=step["operation"],
                state=step["state"],
                reference=step["reference"],
            )
            for step in self.store.list_email_unsubscribe_steps(effect.action_identity)
        ]
        existing_continuation = (
            self._continuation_result(effect, journal)
            if claim is not None and claim["status"] == "awaiting_audit"
            else None
        )
        entry = next(
            (
                item
                for item in entries
                if item.reference == effect.entry_reference
                and _is_private_browser_url(item.private_url)
            ),
            None,
        )
        if entry is None:
            if reconciliation_only:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code=_UNRESOLVED_ERROR,
                )
            receipt = UnsubscribeTerminalReceipt(
                receipt_id=f"unsubscribe-terminal:{effect.effect_digest[:24]}",
                evidence="entry-selection",
                entry_reference=effect.entry_reference,
                effect_digest=effect.effect_digest,
            )
            return self._persist_terminal(
                effect,
                UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY,
                receipt,
                journal,
                final_step=RedactedUnsubscribeStep(
                    operation="select_entry",
                    state="skipped_no_reliable_entry",
                    reference=receipt.receipt_id,
                ),
                claim_owned=False,
            )

        extension_claim = (
            None
            if executed_prefix_length is None
            else self._validated_preclaimed_write(
                effect,
                executed_prefix_length,
            )
        )
        if executed_prefix_length is not None and extension_claim is None:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code="email_unsubscribe_authorization_stale",
            )
        try:
            receipt = self.browser.find_confirmation_receipt(effect)
        except UnsubscribeProviderAuthError:
            return _result(
                UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
                journal,
                error_code="email_unsubscribe_provider_auth_failed",
            )
        except Exception:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=(
                    _UNRESOLVED_ERROR
                    if reconciliation_only
                    else "email_unsubscribe_browser_failed"
                ),
            )
        if receipt is not None:
            if (
                receipt.entry_reference != effect.entry_reference
                or receipt.effect_digest != effect.effect_digest
            ):
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_receipt_mismatch",
                )
            return self._persist_terminal(
                effect,
                UnsubscribeOutcome.DONE,
                receipt,
                journal,
                final_step=RedactedUnsubscribeStep(
                    operation="reconcile_receipt",
                    state="done",
                    reference=receipt.receipt_id,
                ),
                claim_owned=False,
            )

        if existing_continuation is not None:
            try:
                observation = self.browser.inspect_current_state(
                    effect,
                    entry.private_url,
                )
            except UnsubscribeAuthenticationControlsError:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code=(
                        "email_unsubscribe_authentication_controls_blocked"
                    ),
                )
            except (UnsubscribeBrowserError, UnsubscribeProviderAuthError):
                return existing_continuation
            terminal = _terminal_result(effect, observation, journal)
            if terminal is not None and terminal.receipt is not None:
                return self._persist_terminal(
                    effect,
                    terminal.outcome,
                    terminal.receipt,
                    journal,
                    final_step=RedactedUnsubscribeStep(
                        operation="reconcile_state",
                        state=observation.state.value,
                        reference=observation.state_reference,
                    ),
                    claim_owned=False,
                    result_text=terminal.result_text,
                    observation_digest=terminal.observation_digest,
                )
            return existing_continuation

        if claim is not None and claim["status"] == "awaiting_audit":
            extension_claim = self._claim_write(effect)
            if extension_claim is None:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_authorization_stale",
                )

        try:
            observation = (
                self.browser.inspect_current_state(effect, entry.private_url)
                if extension_claim is None
                else None
            )
        except UnsubscribeAuthenticationControlsError:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code="email_unsubscribe_authentication_controls_blocked",
            )
        except UnsubscribeProviderAuthError:
            return _result(
                UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
                journal,
                error_code="email_unsubscribe_provider_auth_failed",
            )
        except Exception:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=(
                    _UNRESOLVED_ERROR
                    if reconciliation_only
                    else "email_unsubscribe_browser_failed"
                ),
            )
        reconcile_step = (
            None
            if observation is None
            else RedactedUnsubscribeStep(
                operation="reconcile_state",
                state=observation.state.value,
                reference=observation.state_reference,
            )
        )
        terminal = None if observation is None else _terminal_result(effect, observation, journal)
        if terminal is not None:
            if terminal.receipt is None:
                return terminal
            return self._persist_terminal(
                effect,
                terminal.outcome,
                terminal.receipt,
                journal,
                final_step=reconcile_step,
                claim_owned=False,
                result_text=terminal.result_text,
                observation_digest=terminal.observation_digest,
            )

        if reconciliation_only:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=_UNRESOLVED_ERROR,
            )

        if extension_claim is None:
            assert observation is not None
            operation_references = [
                operation.operation_reference for operation in effect.operations
            ]
            try:
                resume_index = operation_references.index(
                    observation.next_operation_reference
                )
            except ValueError:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_operation_mismatch",
                )
            extension_claim = self._claim_write(effect)
            if extension_claim is None:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_authorization_stale",
                )
        else:
            resume_index = int(extension_claim["executed_prefix_length"])

        for operation in effect.operations[resume_index:]:
            if observation is not None and observation.next_operation_reference != operation.operation_reference:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_operation_mismatch",
                )
            try:
                observation = self.browser.execute_operation(
                    effect, entry.private_url, operation
                )
            except UnsubscribeAuthenticationControlsError:
                self.store.mark_email_unsubscribe_uncertain(
                    effect.action_identity,
                    owner=self.owner,
                )
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code=(
                        "email_unsubscribe_authentication_controls_blocked"
                    ),
                )
            except UnsubscribeProviderAuthError:
                self.store.mark_email_unsubscribe_uncertain(
                    effect.action_identity,
                    owner=self.owner,
                )
                return _result(
                    UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
                    journal,
                    error_code="email_unsubscribe_provider_auth_failed",
                )
            except Exception:
                self.store.mark_email_unsubscribe_uncertain(
                    effect.action_identity,
                    owner=self.owner,
                )
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_browser_failed",
                )
            operation_step = RedactedUnsubscribeStep(
                operation=operation.kind.value,
                state=observation.state.value,
                reference=(
                    observation.receipt.receipt_id
                    if observation.receipt is not None
                    else operation.operation_reference
                ),
            )
            terminal = _terminal_result(effect, observation, journal)
            if terminal is not None:
                if terminal.receipt is None:
                    self.store.mark_email_unsubscribe_uncertain(
                        effect.action_identity,
                        owner=self.owner,
                    )
                    return terminal
                return self._persist_terminal(
                    effect,
                    terminal.outcome,
                    terminal.receipt,
                    journal,
                    final_step=operation_step,
                    claim_owned=True,
                    result_text=terminal.result_text,
                    observation_digest=terminal.observation_digest,
                )
            if observation.controls:
                journal.append(operation_step)
                try:
                    self.store.persist_email_unsubscribe_continuation(
                        **self._store_arguments(effect),
                        controls=tuple(asdict(item) for item in observation.controls),
                        observation_reference=observation.state_reference,
                        final_step={
                            "sequence": len(effect.operations),
                            "operation": operation_step.operation,
                            "state": operation_step.state,
                            "reference": operation_step.reference,
                        },
                        owner=self.owner,
                    )
                except EmailUnsubscribeClaimConflict:
                    return _result(
                        UnsubscribeOutcome.FAILED_BROWSER,
                        journal,
                        error_code="email_unsubscribe_persistence_conflict",
                    )
                continuation = self._continuation_result(effect, journal)
                assert continuation is not None
                return continuation
            try:
                persisted = self.store.append_email_unsubscribe_step(
                    action_identity=effect.action_identity,
                    effect_digest=effect.effect_digest,
                    sequence=len(
                        self.store.list_email_unsubscribe_steps(effect.action_identity)
                    )
                    + 1,
                    operation=operation_step.operation,
                    state=operation_step.state,
                    reference=operation_step.reference,
                    owner=self.owner,
                )
            except EmailUnsubscribeClaimConflict:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_persistence_conflict",
                )
            journal.append(
                RedactedUnsubscribeStep(
                    operation=persisted["operation"],
                    state=persisted["state"],
                    reference=persisted["reference"],
                )
            )
            reconcile_step = None

        return _result(
            UnsubscribeOutcome.FAILED_BROWSER,
            journal,
            error_code="email_unsubscribe_outcome_unverified",
        )
