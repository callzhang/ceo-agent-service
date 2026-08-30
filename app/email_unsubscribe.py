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
from typing import Callable, Literal, Mapping, Protocol
from urllib.parse import unquote, urlencode, urljoin, urlsplit

from app.email_store import (
    EmailStore,
    EmailUnsubscribeClaimConflict,
    EmailUnsubscribeReceiptConflict,
    email_unsubscribe_effect_digest,
)
from app.leak_check import assert_no_credentials


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

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, UnsubscribeOutcome):
            raise TypeError("outcome must be UnsubscribeOutcome")
        if self.error_code:
            _assert_opaque_reference(self.error_code, field_name="error_code")
        if any(not isinstance(item, RedactedUnsubscribeStep) for item in self.journal):
            raise TypeError("journal must contain RedactedUnsubscribeStep")

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
        return text[:16_384]

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

    def _link_binding(self, element: object) -> _AuditedControlBinding | None:
        if not element.is_visible():
            return None
        label = (element.inner_text(timeout=self.timeout_ms) or "").strip()
        if not label:
            label = (element.get_attribute("aria-label") or "").strip()
        intent = self._control_intent(label)
        if intent is None:
            return None
        href = element.get_attribute("href") or ""
        target = (element.get_attribute("target") or "").casefold()
        if (
            not href
            or target not in {"", "_self"}
            or element.get_attribute("download") is not None
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

    def _form_binding(self, submitter: object) -> _AuditedControlBinding | None:
        if not submitter.is_visible():
            return None
        snapshot = submitter.evaluate(
            """
            node => {
              const tag = node.tagName.toLowerCase();
              const type = (node.getAttribute('type') ||
                (tag === 'button' ? 'submit' : '')).toLowerCase();
              const form = node.form;
              if (!form || type !== 'submit') return null;
              const submitterLabel = tag === 'input'
                ? (node.value || node.getAttribute('aria-label') || '')
                : (node.innerText || node.getAttribute('aria-label') || '');
              const method = (node.getAttribute('formmethod') ||
                form.getAttribute('method') || 'get').toLowerCase();
              const enctype = (node.getAttribute('formenctype') ||
                form.getAttribute('enctype') ||
                'application/x-www-form-urlencoded').toLowerCase();
              const target = (node.getAttribute('formtarget') ||
                form.getAttribute('target') || '').toLowerCase();
              const action = node.getAttribute('formaction') ??
                form.getAttribute('action') ?? '';
              const acceptCharset = (form.getAttribute('accept-charset') || '')
                .trim().toLowerCase();
              const elements = Array.from(form.elements);
              if (elements.length > 128) return {error: 'bounds'};
              const fields = [];
              const supportedInputTypes = new Set([
                'hidden', 'text', 'search', 'tel', 'url', 'email', 'date',
                'month', 'week', 'time', 'datetime-local', 'number', 'range',
                'color', 'checkbox', 'radio'
              ]);
              for (const field of elements) {
                const fieldTag = field.tagName.toLowerCase();
                const fieldType = (field.getAttribute('type') ||
                  (fieldTag === 'button' ? 'submit' :
                    fieldTag === 'input' ? 'text' : fieldTag)).toLowerCase();
                if (field.hasAttribute('dirname')) return {error: 'unsupported'};
                if (field.matches(':disabled') || !field.name) continue;
                if (field === node) {
                  fields.push({name: field.name, type: fieldType, value: field.value || ''});
                  continue;
                }
                if (fieldTag === 'button' || ['submit', 'button', 'reset', 'image'].includes(fieldType)) {
                  continue;
                }
                if (fieldTag === 'input') {
                  if (!supportedInputTypes.has(fieldType)) return {error: 'unsupported'};
                  if (['checkbox', 'radio'].includes(fieldType) && !field.checked) continue;
                  fields.push({name: field.name, type: fieldType, value: field.value || ''});
                  continue;
                }
                if (fieldTag === 'textarea') {
                  fields.push({name: field.name, type: 'textarea', value: field.value || ''});
                  continue;
                }
                if (fieldTag === 'select') {
                  for (const option of Array.from(field.selectedOptions)) {
                    if (!option.disabled) {
                      fields.push({name: field.name, type: 'select', value: option.value});
                    }
                  }
                  continue;
                }
                return {error: 'unsupported'};
              }
              if (fields.length > 128 || fields.some(item =>
                item.name.length > 512 || item.type.length > 64 || item.value.length > 4096
              )) return {error: 'bounds'};
              return {
                acceptCharset,
                action,
                enctype,
                fields,
                formAssociation: {
                  formAttribute: node.getAttribute('form') || '',
                  formId: form.id || '',
                  formName: form.getAttribute('name') || '',
                  formIndex: Array.from(document.forms).indexOf(form)
                },
                method,
                submitter: {
                  name: node.name || '',
                  tag,
                  target,
                  type,
                  value: node.value || ''
                },
                submitterLabel,
                target
              };
            }
            """
        )
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
        bindings: list[_AuditedControlBinding] = []
        links = self.page.locator("a[href]")
        for index in range(min(links.count(), 64)):
            binding = self._link_binding(links.nth(index))
            if binding is not None:
                bindings.append(binding)
        submitters = self.page.locator(
            "button:not([type]), button[type=submit], input[type=submit]"
        )
        for index in range(min(submitters.count(), 64)):
            binding = self._form_binding(submitters.nth(index))
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
        text = self._visible_text()
        state = self._state_from_text(text)
        controls = tuple(item.control for item in self._ordinary_controls())
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
            if getattr(self.page, "url") == "about:blank":
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
            state = discovery.state
            state_reference = discovery.state_reference
            if state is UnsubscribePageState.ACTION_REQUIRED:
                return UnsubscribeObservation(
                    state=state,
                    state_reference=state_reference,
                    controls=discovery.controls,
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
                    visible = response.text()[:16_384]
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
                )
            return observation
        except (UnsubscribeBrowserError, UnsubscribeProviderAuthError):
            raise
        except Exception:
            raise UnsubscribeBrowserError("browser operation failed") from None


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
) -> UnsubscribeExecutionResult:
    return UnsubscribeExecutionResult(
        outcome=outcome,
        disposition=disposition_for_unsubscribe_outcome(outcome),
        journal=tuple(journal),
        receipt=receipt,
        error_code=error_code,
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
    return _result(outcome, journal, receipt=observation.receipt)


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
            self.store.persist_email_unsubscribe_terminal(
                **self._store_arguments(effect),
                outcome=outcome.value,
                receipt_id=receipt.receipt_id,
                evidence=receipt.evidence,
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
        return _result(outcome, journal, receipt=receipt)

    def _claim_write(self, effect: EmailUnsubscribeEffect) -> Mapping[str, object] | None:
        try:
            claim = self.store.claim_email_unsubscribe_write(
                **self._store_arguments(effect),
                owner=self.owner,
            )
        except (EmailUnsubscribeClaimConflict, EmailUnsubscribeReceiptConflict):
            return None
        if claim is None or not claim.get("acquired"):
            return None
        return claim

    def execute(
        self,
        effect: EmailUnsubscribeEffect,
        entries: tuple[UnsubscribeEntry, ...],
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

        extension_claim: Mapping[str, object] | None = None
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
                )
            if observation.controls:
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
                journal.append(operation_step)
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
