"""Run one Audit-accepted unsubscribe flow through a bounded browser protocol.

Private unsubscribe URLs exist only on :class:`UnsubscribeEntry` while a run is
active. Every value returned for persistence or display is an opaque reference,
fixed outcome, redacted step, or provider receipt. A retry always reconciles a
confirmation receipt and the current page/provider state before another write.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
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
_TEXT_MARKER_MAX_DISTANCE_CHARS = 96
_TEXT_MARKER_MAX_LINE_BREAKS = 1
_TEXT_CONTEXT_SIDE_BYTES = 72
_TERMINAL_STATES = frozenset(
    {"done", "already_unsubscribed", "login_required", "captcha", "payment"}
)
_UNRESOLVED_ERROR = "email_unsubscribe_outcome_unresolved"
_MAX_RESULT_TEXT_BYTES = 16 * 1024
_PRIVATE_RESULT_URL = re.compile(r"\b(?:https?|file)://[^\s<>'\"]+")
_PRIVATE_RESULT_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home|private|tmp)/[^\s<>'\"]+"
)
_AUDIT_SESSION_FIELDS = frozenset(
    {
        "version",
        "session_reference",
        "action_identity",
        "effect_digest",
        "entry_reference",
        "control_references",
    }
)


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
    RECONCILE_HANDOFF = "reconcile_handoff"


class UnsubscribeContinuationKind(str, Enum):
    EMAIL_OTP = "email_otp"
    CAPTCHA_HANDOFF = "captcha_handoff"
    CREDENTIAL_HANDOFF = "credential_handoff"


class ConnectedMailboxOtp:
    """One short-lived OTP candidate; its value is never serializable."""

    __slots__ = (
        "_context_reference",
        "_received_at",
        "_recipient",
        "_sender_domain",
        "_authenticated_sender_domain",
        "_authentication_reference",
        "_value",
    )

    def __init__(
        self,
        *,
        recipient: str,
        sender_domain: str,
        context_reference: str,
        authenticated_sender_domain: str,
        authentication_reference: str,
        received_at: datetime,
        value: str,
    ) -> None:
        if (
            not recipient.strip()
            or recipient != recipient.strip()
            or not sender_domain.strip()
            or sender_domain != sender_domain.strip().casefold()
            or not authenticated_sender_domain.strip()
            or authenticated_sender_domain
            != authenticated_sender_domain.strip().casefold()
            or received_at.tzinfo is None
            or not value
            or len(value) > 64
            or any(character.isspace() for character in value)
        ):
            raise ValueError("connected mailbox OTP is invalid")
        _assert_strict_opaque_reference(
            context_reference,
            field_name="otp_context_reference",
        )
        _assert_strict_opaque_reference(
            authentication_reference,
            field_name="otp_authentication_reference",
        )
        self._recipient = recipient
        self._sender_domain = sender_domain
        self._context_reference = context_reference
        self._authenticated_sender_domain = authenticated_sender_domain
        self._authentication_reference = authentication_reference
        self._received_at = received_at
        self._value = value

    @property
    def recipient(self) -> str:
        return self._recipient

    @property
    def sender_domain(self) -> str:
        return self._sender_domain

    @property
    def context_reference(self) -> str:
        return self._context_reference

    @property
    def authenticated_sender_domain(self) -> str:
        return self._authenticated_sender_domain

    @property
    def authentication_reference(self) -> str:
        return self._authentication_reference

    @property
    def received_at(self) -> datetime:
        return self._received_at

    def __repr__(self) -> str:
        return "<ConnectedMailboxOtp ephemeral>"

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "recipient": self.recipient,
            "sender_domain": self.sender_domain,
            "context_reference": self.context_reference,
            "authenticated_sender_domain": self.authenticated_sender_domain,
            "authentication_reference": self.authentication_reference,
            "received_at": self.received_at.isoformat(),
        }

    def consume(self) -> str:
        value = self._value
        if value is None:
            raise UnsubscribeProviderAuthError("connected mailbox OTP already consumed")
        self._value = None
        return value


@dataclass(frozen=True)
class EmailOtpChallenge:
    recipient: str
    site_domain: str
    context_reference: str
    opened_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            not self.recipient.strip()
            or self.recipient != self.recipient.strip()
            or not self.site_domain.strip()
            or self.site_domain != self.site_domain.strip().casefold()
            or self.opened_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.opened_at
        ):
            raise ValueError("email OTP challenge is invalid")
        _assert_strict_opaque_reference(
            self.context_reference,
            field_name="otp_context_reference",
        )


def email_otp_context_reference(recipient: str, site_domain: str) -> str:
    """Bind page and message evidence without carrying either secret body."""

    normalized_recipient = recipient.strip().casefold()
    normalized_site = site_domain.strip().casefold().rstrip(".")
    if not normalized_recipient or "@" not in normalized_recipient or not normalized_site:
        raise ValueError("email OTP context is invalid")
    return "otp-context:" + sha256(
        f"email\n{normalized_recipient}\n{normalized_site}".encode()
    ).hexdigest()


def select_connected_mailbox_otp(
    challenge: EmailOtpChallenge,
    candidates: Sequence[ConnectedMailboxOtp],
) -> ConnectedMailboxOtp | None:
    """Select the newest exact-bound OTP without inspecting another mailbox."""

    if not isinstance(challenge, EmailOtpChallenge):
        raise TypeError("challenge must be EmailOtpChallenge")
    matching = [
        candidate
        for candidate in candidates
        if isinstance(candidate, ConnectedMailboxOtp)
        and candidate.recipient.casefold() == challenge.recipient.casefold()
        and _otp_domains_align(candidate.sender_domain, challenge.site_domain)
        and _otp_domains_align(
            candidate.authenticated_sender_domain, challenge.site_domain
        )
        and candidate.authentication_reference.startswith("otp-auth:")
        and candidate.context_reference == challenge.context_reference
        and challenge.opened_at <= candidate.received_at <= challenge.expires_at
    ]
    return max(matching, key=lambda item: item.received_at, default=None)


def _otp_domains_align(left: str, right: str) -> bool:
    left = left.casefold().rstrip(".")
    right = right.casefold().rstrip(".")
    return bool(left and right) and (
        left == right or left.endswith("." + right) or right.endswith("." + left)
    )


class UnsubscribePageState(str, Enum):
    ACTION_REQUIRED = "action_required"
    DONE = "done"
    ALREADY_UNSUBSCRIBED = "already_unsubscribed"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    PAYMENT = "payment"


class UnsubscribeBrowserFailure(str, Enum):
    """Fixed internal category of one technical browser failure.

    The category names the raising condition inside this module. It never
    carries page text, a URL or provider state, so it is safe to persist and
    to show the Audit model.
    """

    AUTHENTICATION_CONTROLS = "authentication_controls"
    CAPTCHA_BINDING_INCOMPLETE = "captcha_binding_incomplete"
    CONFIRMATION_ENTRY_MISSING = "confirmation_entry_missing"
    CONFIRMATION_TARGET_REJECTED = "confirmation_target_rejected"
    CONTROL_SEMANTICS_REJECTED = "control_semantics_rejected"
    CONTROL_UNAVAILABLE = "control_unavailable"
    DOWNLOAD_REJECTED = "download_rejected"
    FORM_RESPONSE_REJECTED = "form_response_rejected"
    NAVIGATION_TARGET_INVALID = "navigation_target_invalid"
    ONE_CLICK_RESPONSE_REJECTED = "one_click_response_rejected"
    ONE_CLICK_UNVERIFIED = "one_click_unverified"
    OPERATION_FAILED = "operation_failed"
    OPERATION_KIND_REJECTED = "operation_kind_rejected"
    OPERATION_SEQUENCE_EMPTY = "operation_sequence_empty"
    OPERATION_TIMEOUT = "operation_timeout"
    OTP_BINDING_INCOMPLETE = "otp_binding_incomplete"
    OTP_REQUEST_REJECTED = "otp_request_rejected"
    OTP_RESPONSE_REJECTED = "otp_response_rejected"
    PAGE_CONTROLS_UNMODELLED = "page_controls_unmodelled"
    PAGE_STATE_MISSING = "page_state_missing"
    PAGE_STATE_UNKNOWN = "page_state_unknown"
    POPUP_REJECTED = "popup_rejected"
    RECEIPT_READBACK_FAILED = "receipt_readback_failed"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    SESSION_BINDING_REJECTED = "session_binding_rejected"
    SESSION_CAPTURE_REJECTED = "session_capture_rejected"
    SESSION_CONTROL_CHANGED = "session_control_changed"
    SESSION_RESTORE_REJECTED = "session_restore_rejected"
    STATE_READBACK_FAILED = "state_readback_failed"
    TRUSTED_WORLD_UNAVAILABLE = "trusted_world_unavailable"


class UnsubscribeBrowserError(RuntimeError):
    """The browser runtime or its state readback failed technically."""

    def __init__(
        self,
        category: UnsubscribeBrowserFailure,
        message: str = "",
        *,
        observation: str = "",
    ) -> None:
        if not isinstance(category, UnsubscribeBrowserFailure):
            raise TypeError("category must be UnsubscribeBrowserFailure")
        super().__init__(message or category.value)
        self.category = category
        # What the page looked like when the read gave up. Empty for failures
        # that never reached a page; the task-level error code is unchanged
        # either way, because this is evidence, not classification.
        self.observation = observation


class UnsubscribeAuthenticationControlsError(UnsubscribeBrowserError):
    """The page exposes authentication or credential controls."""

    def __init__(self, message: str = "") -> None:
        super().__init__(UnsubscribeBrowserFailure.AUTHENTICATION_CONTROLS, message)


# Only these categories keep a dedicated task-level error code; every other
# category keeps the generic browser failure code, and reports itself in the
# separate category field instead. The code is a cross-module contract - the
# explicit-retry release path in app/email_store.py matches it exactly - so the
# category is carried beside it rather than composed into it.
_BROWSER_FAILURE_CODES = {
    UnsubscribeBrowserFailure.OPERATION_TIMEOUT: "email_unsubscribe_browser_timeout",
    UnsubscribeBrowserFailure.PAGE_STATE_UNKNOWN: (
        "email_unsubscribe_page_state_unknown"
    ),
    UnsubscribeBrowserFailure.PAGE_CONTROLS_UNMODELLED: (
        "email_unsubscribe_page_state_unknown"
    ),
    UnsubscribeBrowserFailure.PAGE_STATE_MISSING: (
        "email_unsubscribe_page_state_missing"
    ),
}
# Codes that can only come from having operated a page. A role without a
# browser reporting one of these is reporting something it did not observe.
BROWSER_EXECUTION_ERROR_CODES = frozenset(_BROWSER_FAILURE_CODES.values()) | {
    "email_unsubscribe_browser_failed",
    "email_unsubscribe_browser_session_unavailable",
    "email_unsubscribe_provider_auth_failed",
    "email_unsubscribe_authentication_controls_blocked",
    "email_unsubscribe_outcome_unresolved",
    "email_unsubscribe_outcome_unverified",
}
_VISIBLE_TEXT_WAIT_MS = 5_000
_VISIBLE_TEXT_POLL_MS = 250


# A page the browser reached and read, but whose controls this service will
# not operate. Retrying re-reads the same page with the same model and reaches
# the same place, so these are terminal, not retryable browser faults.
_UNOPERABLE_PAGE_FAILURES = frozenset(
    {
        UnsubscribeBrowserFailure.PAGE_CONTROLS_UNMODELLED,
        UnsubscribeBrowserFailure.PAGE_STATE_UNKNOWN,
        UnsubscribeBrowserFailure.PAGE_STATE_MISSING,
    }
)


def _is_unoperable_page(error: Exception) -> bool:
    return (
        isinstance(error, UnsubscribeBrowserError)
        and error.category in _UNOPERABLE_PAGE_FAILURES
    )


def _browser_failure_observation_fields(error: Exception) -> dict[str, object]:
    """The page state a failure recorded, with the integrity metadata it owes.

    result_text is not a free text slot: UnsubscribeExecutionResult requires a
    matching sha256 and consistent truncation metadata, and rejects the whole
    result otherwise. Supplying the text alone turned every instrumented
    browser failure into `unsubscribe_operation_rejected:ValueError` at
    construction -- a worse failure than the one it was recording.
    """
    observation = getattr(error, "observation", "")
    if not isinstance(observation, str) or not observation:
        return {}
    digest = sha256(observation.encode("utf-8")).hexdigest()
    return {
        "result_text": observation,
        "result_text_digest": digest,
        # Nothing was dropped, so the observed digest is the recorded digest;
        # that equality is exactly what "not truncated" means here.
        "observation_digest": digest,
        "result_text_truncated": False,
    }


def _browser_failure_code(error: Exception) -> str:
    if not isinstance(error, UnsubscribeBrowserError):
        return "email_unsubscribe_browser_failed"
    return _BROWSER_FAILURE_CODES.get(
        error.category,
        "email_unsubscribe_browser_failed",
    )


def _browser_failure_category(error: Exception) -> str:
    """Name the internal condition one browser failure came from.

    The category is the diagnosable half of a browser failure: the code stays
    the coarse contract other modules match on, while this names the raising
    condition inside this module. It is a fixed enum member, so it can never
    carry page text, a URL or provider state.
    """

    if not isinstance(error, UnsubscribeBrowserError):
        return ""
    return error.category.value


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

# Counts only; no text, attribute or URL leaves the page. The control selector
# is the same one _ordinary_controls() models, so "the page has settled" means
# "the snapshot below can see what this page offers".
_SETTLED_DOCUMENT_JS = r"""
  const htmlInnerText = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype, 'innerText'
  ).get;
  const elementHasAttribute = Element.prototype.hasAttribute;
  const settledVisible = node => {
    if (reflectApply(elementHasAttribute, node, ['hidden'])) return false;
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  // Bounded exactly like _ordinary_controls(): the count is only ever read
  // as "this page offers something", so the first 64 candidates answer it
  // without forcing layout over an unbounded link list.
  const settledControls = trustedDocumentQuery(
    'a[href], button:not([type]), button[type=submit], input[type=submit]'
  ).slice(0, 64).filter(node => settledVisible(node));
  const settledBody = document.body;
  return {
    textLength: settledBody
      ? String(reflectApply(htmlInnerText, settledBody, []) || '').trim().length
      : 0,
    controlCount: settledControls.length
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
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            ) from None


@dataclass(frozen=True, repr=False)
class _RestoredAuditSession:
    session_reference: str
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

    invalid = UnsubscribeBrowserError(
        UnsubscribeBrowserFailure.SESSION_BINDING_REJECTED,
        "browser session binding rejected",
    )
    if set(payload) != _AUDIT_SESSION_FIELDS:
        raise invalid
    if (
        not isinstance(executed_prefix_length, int)
        or isinstance(executed_prefix_length, bool)
        or executed_prefix_length <= 0
        or executed_prefix_length != len(effect.operations) - 1
        or payload.get("version") != 2
        or payload.get("action_identity") != effect.action_identity
        or payload.get("effect_digest") != effect.previous_effect_digest
        or payload.get("entry_reference") != effect.entry_reference
    ):
        raise invalid
    session_reference = payload.get("session_reference")
    control_values = payload.get("control_references")
    if (
        not isinstance(session_reference, str)
        or not session_reference.startswith("email-browser-session:")
        or len(session_reference) != len("email-browser-session:") + 64
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
    appended = effect.operations[executed_prefix_length]
    if (
        appended.kind
        not in {
            UnsubscribeOperationKind.SUBMIT_FORM,
            UnsubscribeOperationKind.CLICK_CONFIRMATION,
            UnsubscribeOperationKind.RECONCILE_HANDOFF,
        }
        or appended.target_reference not in controls
    ):
        raise invalid
    return _RestoredAuditSession(
        session_reference=session_reference,
        control_references=controls,
    )


def _validated_audit_session_cookies(
    values: object,
    *,
    document_url: str,
) -> tuple[dict[str, object], ...]:
    invalid = UnsubscribeBrowserError(
        UnsubscribeBrowserFailure.SESSION_BINDING_REJECTED,
        "browser session binding rejected",
    )
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
            domain.lstrip(".").casefold().rstrip(".") if isinstance(domain, str) else ""
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
            or not (host == canonical_domain or host.endswith("." + canonical_domain))
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
            self.dkim_covers_list_unsubscribe and self.dkim_covers_list_unsubscribe_post
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
        raise ValueError(f"{field_name} must be an opaque redacted reference") from None


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
        raise ValueError(f"{field_name} must be an opaque redacted reference") from None


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
    index: int
    source: UnsubscribeEntrySource
    reference: str
    private_url: str = field(repr=False)
    priority: int
    scheme: str
    host: str
    context: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, UnsubscribeEntrySource):
            raise TypeError("source must be UnsubscribeEntrySource")
        _assert_opaque_reference(self.reference, field_name="reference")
        if self.reference != unsubscribe_entry_reference(self.private_url):
            raise ValueError("unsubscribe entry reference does not match private URL")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        if self.index < 0:
            raise ValueError("index must be non-negative")
        parsed = urlsplit(self.private_url)
        if (
            self.scheme != parsed.scheme.casefold()
            or self.host != (parsed.hostname or "").casefold()
        ):
            raise ValueError("unsubscribe entry URL metadata does not match")
        if len(self.context.encode("utf-8")) > 160:
            raise ValueError("unsubscribe entry context must be bounded")

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "index": self.index,
            "source": self.source.value,
            "digest": self.reference.removeprefix("unsubscribe-entry:"),
            "reference": self.reference,
        }


def browser_unsubscribe_entries(
    entries: Sequence[UnsubscribeEntry],
    *,
    allow_loopback_for_tests: bool = False,
    normalize_indexes: bool = False,
) -> tuple[UnsubscribeEntry, ...]:
    """Return only current HTTPS browser candidates in deterministic order."""

    selected = tuple(
        entry
        for entry in entries
        if _is_private_https_url(entry.private_url)
        or (allow_loopback_for_tests and _is_loopback_http_url(entry.private_url))
    )
    ordered = tuple(sorted(selected, key=lambda item: item.index))
    if not normalize_indexes:
        return ordered
    return tuple(replace(entry, index=index) for index, entry in enumerate(ordered))


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

    def __post_init__(self) -> None:
        for field_name in (
            "action_identity",
            "action_plan_id",
            "account_id",
            "stable_message_identity",
            "thread_identity",
        ):
            _assert_opaque_reference(
                str(getattr(self, field_name)), field_name=field_name
            )
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
        if (
            self.previous_effect_digest
            and re.fullmatch(r"[0-9a-f]{64}", self.previous_effect_digest) is None
        ):
            raise ValueError("previous_effect_digest must be canonical sha256 hex")

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
    kind: Literal[
        "form",
        "link",
        "button",
        "confirmation_email",
        "email_otp",
        "captcha_handoff",
        "credential_handoff",
    ]
    intent: Literal["continue", "unsubscribe", "confirm"]

    def __post_init__(self) -> None:
        _assert_strict_opaque_reference(self.reference, field_name="control_reference")

    @property
    def continuation_kind(self) -> UnsubscribeContinuationKind | None:
        try:
            return UnsubscribeContinuationKind(self.kind)
        except ValueError:
            return None

    @property
    def redacted(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "kind": self.kind,
            "intent": self.intent,
        }


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
    # Fixed internal category of the browser failure the code came from. It
    # never replaces the code, which other modules match on exactly.
    error_category: str = ""
    result_text: str = ""
    observation_digest: str = ""
    result_text_digest: str = ""
    result_text_truncated: bool = False
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, UnsubscribeOutcome):
            raise TypeError("outcome must be UnsubscribeOutcome")
        if self.error_code:
            _assert_opaque_reference(self.error_code, field_name="error_code")
        if self.error_category and self.error_category not in set(
            UnsubscribeBrowserFailure
        ):
            raise ValueError("error_category must be a known browser failure")
        if any(not isinstance(item, RedactedUnsubscribeStep) for item in self.journal):
            raise TypeError("journal must contain RedactedUnsubscribeStep")
        if (
            self.observation_digest
            and re.fullmatch(r"[0-9a-f]{64}", self.observation_digest) is None
        ):
            raise ValueError("observation_digest must be canonical sha256 hex")
        if type(self.result_text_truncated) is not bool:
            raise TypeError("result_text_truncated must be bool")
        if not self.result_text:
            if (
                self.observation_digest
                or self.result_text_digest
                or self.result_text_truncated
            ):
                raise ValueError(
                    "empty result text has inconsistent integrity metadata"
                )
        else:
            expected_result_text_digest = sha256(
                self.result_text.encode("utf-8")
            ).hexdigest()
            if self.result_text_digest != expected_result_text_digest:
                raise ValueError("result_text_digest does not match result text")
            if self.result_text_truncated == (
                self.observation_digest == self.result_text_digest
            ):
                raise ValueError("result text truncation metadata is inconsistent")

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
            "error_category": self.error_category,
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
        _assert_strict_opaque_reference(
            self.entry_reference,
            field_name="entry_reference",
        )
        if self.action_plan_version <= 0 or self.classification_id <= 0:
            raise ValueError("continuation plan and classification must be positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.effect_digest) is None:
            raise ValueError("effect_digest must be canonical sha256 hex")
        if (
            self.previous_effect_digest
            and re.fullmatch(r"[0-9a-f]{64}", self.previous_effect_digest) is None
        ):
            raise ValueError("previous_effect_digest must be canonical sha256 hex")
        if not self.executed_operations or not self.controls:
            raise ValueError("continuation requires operations and discovered controls")
        if any(
            not isinstance(item, UnsubscribeOperation)
            for item in self.executed_operations
        ) or any(
            not isinstance(item, UnsubscribeDiscoveredControl) for item in self.controls
        ):
            raise TypeError("continuation contains invalid typed values")

    @property
    def requires_human(self) -> bool:
        for control in self.controls:
            if control.continuation_kind is (
                UnsubscribeContinuationKind.CREDENTIAL_HANDOFF
            ):
                return True
            if control.continuation_kind is UnsubscribeContinuationKind.CAPTCHA_HANDOFF:
                if any(
                    operation.kind is UnsubscribeOperationKind.CLICK_CONFIRMATION
                    and operation.target_reference == control.reference
                    for operation in self.executed_operations
                ):
                    return True
        return False


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
                "requires_human": self.continuation.requires_human,
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
    field_selector: str = field(default="", repr=False)
    submitter_selector: str = field(default="", repr=False)
    field_name: str = field(default="", repr=False)
    submitter_name: str = field(default="", repr=False)
    submitter_value: str = field(default="", repr=False)
    challenge_context_reference: str = ""
    challenge_opened_at: datetime | None = None
    challenge_expires_at: datetime | None = None


def _audited_control_reference(semantics: Mapping[str, object]) -> str:
    canonical = json.dumps(
        semantics,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(canonical.encode("utf-8")) > 65_536:
        raise UnsubscribeBrowserError(
            UnsubscribeBrowserFailure.CONTROL_SEMANTICS_REJECTED,
            "browser control semantics rejected",
        )
    return "unsubscribe-control:" + sha256(canonical.encode()).hexdigest()


def confirmation_target_reference(
    confirmation_message_identity: str,
) -> str:
    _assert_opaque_reference(
        confirmation_message_identity,
        field_name="confirmation_message_identity",
    )
    return (
        "confirmation-target:"
        + sha256(confirmation_message_identity.encode()).hexdigest()
    )


class PlaywrightUnsubscribeBrowser:
    """Bounded sync-Playwright adapter; Playwright remains an optional dependency."""

    def __init__(
        self,
        page: object,
        *,
        timeout_ms: int = 5_000,
        restored_document_url: str = "",
        confirmation_receipt_resolver: Callable[
            [EmailUnsubscribeEffect], UnsubscribeTerminalReceipt | None
        ]
        | None = None,
        confirmation_target_resolver: Callable[
            [EmailUnsubscribeEffect], ConfirmationNavigationTarget | None
        ]
        | None = None,
        connected_recipient: str = "",
        email_otp_resolver: Callable[
            [EmailOtpChallenge], ConnectedMailboxOtp | None
        ]
        | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirmation_receipt_resolver = confirmation_receipt_resolver
        self.confirmation_target_resolver = confirmation_target_resolver
        self.connected_recipient = connected_recipient.strip()
        self.email_otp_resolver = email_otp_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._challenge_bindings: dict[str, datetime] = {}
        self._external_challenge_generation = 0
        self._blocked_popup = False
        self._blocked_download = False
        self._document_url = ""
        self._context = self.page.context
        try:
            cdp_session = self._context.new_cdp_session(self.page)
        except Exception:
            cdp_session = None
        self._trusted_world = _ChromiumIsolatedWorld(cdp_session)
        self._context.set_default_timeout(timeout_ms)
        self._context.set_default_navigation_timeout(timeout_ms)
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
            self._document_url = self._validate_navigation_target(restored_document_url)

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
        if self._blocked_popup:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.POPUP_REJECTED,
                "browser popup rejected",
            )
        if self._blocked_download:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.DOWNLOAD_REJECTED,
                "browser download rejected",
            )

    def _validate_navigation_target(self, value: str) -> str:
        """Accept any absolute http(s) navigation target."""
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.NAVIGATION_TARGET_INVALID,
                "browser navigation target invalid",
            )
        return value

    def _page_read_is_settled(
        self,
        bindings: Sequence[_AuditedControlBinding],
        text: str,
    ) -> bool:
        """Say whether one read already carries something to act on.

        Wording that names a terminal state settles the page on its own: there
        is nothing left to render that could change it. A modelled control
        only settles the page alongside rendered wording, because a control
        read out of a pre-render shell is exactly what a render can still
        replace - including with the terminal wording that means the control
        must not be operated at all.
        """

        return self._state_from_text(text) is not None or bool(bindings and text)

    def _awaited_page_read(self) -> tuple[tuple[_AuditedControlBinding, ...], str]:
        """Read the page until it settles, within a bounded budget.

        Navigation returns at domcontentloaded, so a script-rendered
        unsubscribe page has neither its controls nor its wording yet. Both
        signals are read together on every attempt and the wait ends on either
        of them, because waiting for one alone classifies a half-rendered page
        from whichever half arrived first: a pre-render shell exposing a submit
        control contributes nothing to the body text, and prose that paints
        before its control hydrates models nothing. An already-rendered page
        costs exactly the one control read and one text read it always did.
        """

        wait = getattr(self.page, "wait_for_timeout", None)
        budget_ms = min(self.timeout_ms, _VISIBLE_TEXT_WAIT_MS)
        waited_ms = 0
        bindings = self._ordinary_controls()
        text = self._visible_text()
        while not self._page_read_is_settled(bindings, text):
            if wait is None or waited_ms >= budget_ms:
                break
            wait(_VISIBLE_TEXT_POLL_MS)
            waited_ms += _VISIBLE_TEXT_POLL_MS
            bindings = self._ordinary_controls()
            text = self._visible_text()
        return bindings, text

    def _document_structure(self) -> dict[str, int]:
        value = self._trusted_world.evaluate(
            "function() {"
            + _AUTHENTICATION_CONTROL_PREDICATE_JS
            + _SETTLED_DOCUMENT_JS
            + "}"
        )
        if not isinstance(value, Mapping) or any(
            not isinstance(value.get(key), int) or isinstance(value.get(key), bool)
            for key in ("textLength", "controlCount")
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        return {
            "text_length": int(value["textLength"]),
            "control_count": int(value["controlCount"]),
        }

    def _unreadable_page_observation(
        self,
        structure: Mapping[str, int] | None,
        text: str,
        controls: tuple[object, ...],
    ) -> str:
        """Describe a page the read could not classify, from what was measured.

        Deliberately built from values already in hand rather than a fresh
        browser probe. The first version asked the isolated world for more
        detail and swallowed its failure, so when the probe did not answer the
        record was empty again -- the same "a failure that records nothing"
        shape this exists to remove. Everything here was already read on the
        path to the failure and cannot fail separately.

        The entry URL stays out of durable storage: its path and query carry
        the subscription token. The host alone says which side answered.
        """
        host = ""
        if self._document_url:
            try:
                host = urlsplit(self._document_url).netloc
            except ValueError:
                host = ""
        # The first line or two of what the page actually said. The success
        # path already persists the whole redacted page text with its receipt,
        # so a bounded prefix here is the same disclosure and it is the only
        # thing that separates "a tracker bounce we never followed" from "a
        # page our control model cannot read" -- lengths and a host name do
        # not.
        preview = ""
        if text:
            redacted, _digest = normalize_unsubscribe_result_text(text)
            preview = " ".join(redacted.split())[:160]
        fields = {
            "host": host,
            "text_preview": preview,
            "text_length": (
                structure["text_length"] if structure is not None else len(text)
            ),
            "control_count": (
                structure["control_count"] if structure is not None else len(controls)
            ),
            "modelled_controls": len(controls),
            "read_text_length": len(text),
        }
        return " ".join(f"{name}={value!r}" for name, value in fields.items())

    def _visible_text(self) -> str:
        return self.page.locator("body").inner_text(timeout=self.timeout_ms).strip()

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

    def mark_external_interaction(self) -> None:
        """Conservatively start a new challenge generation after user handoff."""

        self._external_challenge_generation += 1

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
        if not href or target not in {"", "_self"} or snapshot.get("download") is True:
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
        authentication = self._authentication_control_snapshot()
        if authentication is not None:
            page_identity = self._page_identity()
            generation_semantics = {
                "challenge_identity": authentication["challengeIdentity"],
                "document_generation": authentication["documentGeneration"],
                "external_generation": getattr(
                    self, "_external_challenge_generation", 0
                ),
                "field": authentication["field"],
                "form_association": authentication["formAssociation"],
                "successful_controls": authentication["successfulControls"],
                "submitter": authentication["submitter"],
                "page_identity": page_identity,
            }
            challenge_identity = "otp-generation:" + sha256(
                json.dumps(
                    generation_semantics,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            opened_at = self._challenge_bindings.setdefault(
                challenge_identity,
                self._clock().astimezone(timezone.utc),
            )
            expires_at = opened_at + timedelta(minutes=10)
            is_email_otp = (
                "one-time-code" in authentication["autocompleteTokens"]
                and authentication["deliveryMethod"] == "email"
                and str(authentication["deliveryRecipient"]).casefold()
                == self.connected_recipient.casefold()
                and str(authentication["method"]).upper() == "POST"
                and authentication["enctype"]
                == "application/x-www-form-urlencoded"
                and str(authentication["target"]).casefold() in {"", "_self"}
                and bool(str(authentication["field"].get("name") or ""))
                and bool(str(authentication["submitterSelector"]))
            )
            authentication_kind = (
                UnsubscribeContinuationKind.CAPTCHA_HANDOFF
                if authentication["captcha"] is True
                else (
                    UnsubscribeContinuationKind.EMAIL_OTP
                    if is_email_otp and self.connected_recipient
                    else UnsubscribeContinuationKind.CREDENTIAL_HANDOFF
                )
            )
            target_url = self._validate_navigation_target(
                urljoin(page_identity, str(authentication["action"]) or page_identity)
            )
            site_domain = (urlsplit(target_url).hostname or "").casefold()
            context_reference = (
                email_otp_context_reference(
                    self.connected_recipient,
                    site_domain,
                )
                if is_email_otp
                else ""
            )
            semantics = {
                "challenge_identity": challenge_identity,
                "challenge_opened_at": opened_at.isoformat(),
                "challenge_expires_at": expires_at.isoformat(),
                "enctype": authentication["enctype"],
                "field": authentication["field"],
                "field_selector": authentication["fieldSelector"],
                "form_association": authentication["formAssociation"],
                "kind": authentication_kind.value,
                "method": authentication["method"],
                "page_identity": page_identity,
                    "resolved_target": target_url,
                "successful_controls": authentication["successfulControls"],
                "submitter": authentication["submitter"],
                "submitter_selector": authentication["submitterSelector"],
                "target": authentication["target"],
                "version": 1,
            }
            control = UnsubscribeDiscoveredControl(
                reference=_audited_control_reference(semantics),
                kind=authentication_kind.value,
                intent="confirm",
            )
            return (
                _AuditedControlBinding(
                    control=control,
                    target_url=target_url,
                    method=str(authentication["method"]).upper(),
                    enctype=str(authentication["enctype"]),
                    successful_controls=tuple(
                        (
                            str(item["name"]),
                            str(item["type"]),
                            str(item["value"]),
                        )
                        for item in authentication["successfulControls"]
                    ),
                    field_selector=str(authentication["fieldSelector"]),
                    submitter_selector=str(authentication["submitterSelector"]),
                    field_name=str(authentication["field"].get("name") or ""),
                    submitter_name=str(
                        authentication["submitter"].get("name") or ""
                    ),
                    submitter_value=str(
                        authentication["submitter"].get("value") or ""
                    ),
                    challenge_context_reference=context_reference,
                    challenge_opened_at=opened_at,
                    challenge_expires_at=expires_at,
                ),
            )
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
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        if snapshot.get("blocked") is not False:
            raise UnsubscribeAuthenticationControlsError(
                "authentication controls are not permitted"
            )
        bindings: list[_AuditedControlBinding] = []
        for item in snapshot.get("links", ()):
            if not isinstance(item, Mapping):
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                    "trusted browser execution unavailable",
                )
            binding = self._link_binding(item)
            if binding is not None:
                bindings.append(binding)
        for item in snapshot.get("forms", ()):
            if not isinstance(item, Mapping):
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                    "trusted browser execution unavailable",
                )
            binding = self._form_binding(item)
            if binding is not None:
                bindings.append(binding)
        unique: dict[str, _AuditedControlBinding] = {}
        for binding in bindings:
            unique.setdefault(binding.control.reference, binding)
        return tuple(unique.values())

    def _authentication_control_snapshot(self) -> Mapping[str, object] | None:
        recipient_literal = json.dumps(self.connected_recipient.casefold())
        value = self._trusted_world.evaluate(
            "function() {"
            + _AUTHENTICATION_CONTROL_PREDICATE_JS
            + f"""
              const connectedRecipient = {recipient_literal};
              const controls = trustedDocumentQuery(
                'input, textarea, select, button, a, img, canvas, [role]'
              ).filter(node => isAuthenticationControl(node));
              if (!controls.length) return {{present: false}};
              const metadata = controls.map(node => authenticationMetadata(node));
              const captcha = metadata.some(value =>
                ['captcha', 'recaptcha', 'hcaptcha', '验证码'].some(
                  marker => value.includes(marker)
                )
              );
              const field = controls.find(node =>
                autocompleteTokens(node).includes('one-time-code')
              ) || controls[0];
              const form = field.form || field.closest('form');
              const submitter = form && form.querySelector(
                'button:not([type]), button[type="submit"], input[type="submit"]'
              );
              const elementPath = node => {{
                if (!node || node.nodeType !== 1) return '';
                const parts = [];
                for (let current = node; current && current.nodeType === 1;
                     current = current.parentElement) {{
                  const tag = current.tagName.toLowerCase();
                  const siblings = current.parentElement
                    ? Array.from(current.parentElement.children).filter(
                        item => item.tagName === current.tagName
                      )
                    : [current];
                  parts.unshift(tag + ':nth-of-type(' +
                    (siblings.indexOf(current) + 1) + ')');
                }}
                return parts.join(' > ');
              }};
              const identityState = globalThis.__ceoEmailAuditIdentityState ||
                (globalThis.__ceoEmailAuditIdentityState = {{
                  ids: new WeakMap(), next: 1
                }});
              const elementIdentity = node => {{
                if (!node) return '';
                if (!identityState.ids.has(node)) {{
                  identityState.ids.set(node, 'element:' + identityState.next++);
                }}
                return identityState.ids.get(node);
              }};
              const handlers = (node, names) => Object.fromEntries(
                names.map(name => [name, trustedGetAttribute(node, name) || ''])
              );
              const fieldType = (trustedGetAttribute(field, 'type') ||
                field.tagName.toLowerCase()).toLowerCase();
              const fieldSemantics = {{
                identity: elementIdentity(field),
                tag: field.tagName.toLowerCase(),
                type: fieldType,
                name: trustedGetAttribute(field, 'name') || '',
                autocomplete: trustedGetAttribute(field, 'autocomplete') || '',
                form: elementIdentity(form),
                handlers: handlers(field, ['onchange', 'oninput', 'onclick'])
              }};
              const successfulControls = [];
              if (form) {{
                const elements = Array.from(form.elements);
                if (elements.length > 128) return {{present: false}};
                for (const item of elements) {{
                  if (item === field || item === submitter || item.disabled) continue;
                  const tag = item.tagName.toLowerCase();
                  const type = (trustedGetAttribute(item, 'type') || tag).toLowerCase();
                  const name = trustedGetAttribute(item, 'name') || '';
                  if (!name || type === 'password' ||
                      autocompleteTokens(item).some(token =>
                        credentialAutocompleteTokens.has(token))) continue;
                  if (['submit', 'button', 'reset', 'image', 'file'].includes(type)) continue;
                  if (['checkbox', 'radio'].includes(type) && !item.checked) continue;
                  const itemValue = String(item.value || '');
                  if (itemValue.length > 4096) return {{present: false}};
                  successfulControls.push({{
                    identity: elementIdentity(item), name, type, value: itemValue
                  }});
                }}
              }}
              const pageText = (document.body && document.body.innerText || '')
                .toLowerCase();
              const explicitEmail = Boolean(connectedRecipient) &&
                pageText.includes(connectedRecipient) &&
                (pageText.includes('email') || pageText.includes('mailbox') ||
                 pageText.includes('邮箱') || pageText.includes('邮件'));
              const method = (form && trustedGetAttribute(form, 'method') ||
                'GET').toUpperCase();
              const enctype = form && trustedGetAttribute(form, 'enctype') ||
                'application/x-www-form-urlencoded';
              return {{
                present: true,
                captcha,
                autocompleteTokens: autocompleteTokens(field),
                deliveryMethod: explicitEmail ? 'email' : '',
                deliveryRecipient: explicitEmail ? connectedRecipient : '',
                challengeIdentity: elementPath(field),
                fieldSelector: elementPath(field),
                submitterSelector: elementPath(submitter),
                field: fieldSemantics,
                formAssociation: elementIdentity(form),
                action: form && trustedGetAttribute(form, 'action') || location.href,
                method,
                enctype,
                target: form && trustedGetAttribute(form, 'target') || '',
                successfulControls,
                submitter: {{
                  identity: elementIdentity(submitter),
                  label: submitter ? (submitter.innerText ||
                    trustedGetAttribute(submitter, 'aria-label') || '') : '',
                  tag: submitter ? submitter.tagName.toLowerCase() : '',
                  type: submitter && trustedGetAttribute(submitter, 'type') || '',
                  name: submitter && trustedGetAttribute(submitter, 'name') || '',
                  value: submitter && trustedGetAttribute(submitter, 'value') || '',
                  handlers: submitter ? handlers(submitter, ['onclick']) : {{onclick: ''}}
                }},
                documentGeneration: elementIdentity(document)
              }};
            }}
            """
        )
        if not isinstance(value, Mapping) or value.get("present") is not True:
            if isinstance(value, Mapping) and value.get("present") is False:
                return None
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        required = {
            "captcha",
            "autocompleteTokens",
            "deliveryMethod",
            "deliveryRecipient",
            "challengeIdentity",
            "fieldSelector",
            "submitterSelector",
            "field",
            "formAssociation",
            "action",
            "method",
            "enctype",
            "target",
            "successfulControls",
            "submitter",
            "documentGeneration",
        }
        if set(value) != required | {"present"}:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        if (
            value["captcha"] not in {True, False}
            or not isinstance(value["autocompleteTokens"], list)
            or not all(isinstance(item, str) for item in value["autocompleteTokens"])
            or any(
                not isinstance(value[key], str)
                for key in required
                - {
                    "captcha",
                    "autocompleteTokens",
                    "field",
                    "successfulControls",
                    "submitter",
                }
            )
            or not isinstance(value["field"], Mapping)
            or not isinstance(value["submitter"], Mapping)
            or not isinstance(value["successfulControls"], list)
            or not all(isinstance(item, Mapping) for item in value["successfulControls"])
            or not str(value["challengeIdentity"])
            or not str(value["documentGeneration"])
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        if not value["captcha"] and not str(value["fieldSelector"]):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        if (
            "one-time-code" in value["autocompleteTokens"]
            and value["deliveryMethod"] == "email"
            and not str(value["submitterSelector"])
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        return value

    @staticmethod
    def _state_from_text(text: str) -> UnsubscribePageState | None:
        normalized = " ".join(text.casefold().split())
        # Providers write the confirmation with a typographic apostrophe, so a
        # marker spelled with the ASCII one never matches. LinkedIn's page says
        # "You’ve unsubscribed" and was read as an unmodellable page, which
        # failed a task whose unsubscribe had already succeeded.
        normalized = normalized.replace("\u2019", "'").replace("\u02bc", "'")
        if any(marker in normalized for marker in ("captcha", "验证码")):
            return UnsubscribePageState.CAPTCHA
        if any(
            marker in normalized
            for marker in ("payment", "credit card", "付款", "付费")
        ):
            return UnsubscribePageState.PAYMENT
        if any(
            marker in normalized
            for marker in ("already unsubscribed", "no longer subscribed", "已经退订")
        ):
            return UnsubscribePageState.ALREADY_UNSUBSCRIBED
        if any(
            marker in normalized
            for marker in ("sign in", "log in", "login", "password", "登录")
        ):
            return UnsubscribePageState.LOGIN_REQUIRED
        if any(
            marker in normalized
            for marker in (
                "successfully unsubscribed",
                "you are unsubscribed",
                "you have been unsubscribed",
                # The confirmation LinkedIn and others actually print.
                "you've unsubscribed",
                "you have unsubscribed",
                # What the tracker hosts print instead of the word
                # "unsubscribed": a statement about what will now happen.
                # Three live receipts recorded this page as
                # skipped_no_reliable_entry while the unsubscribe had in fact
                # completed.
                "you will no longer receive",
                "you'll no longer receive",
                "you have been removed from",
                "unsubscribe complete",
                "unsubscribe confirmation complete",
                "subscription cancelled",
                "list-unsubscribe post returned http 2",
                "退订成功",
                "不会再收到",
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
        bindings, text = self._awaited_page_read()
        controls = tuple(item.control for item in bindings)
        # The raw shape of the document is only ever needed to explain a read
        # that modelled nothing, so it is read there and nowhere else.
        structure = (
            self._document_structure() if not text and not controls else None
        )
        if (
            structure is not None
            and not structure["text_length"]
            and not structure["control_count"]
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.PAGE_STATE_MISSING,
                "unsubscribe page has no visible state",
                observation=self._unreadable_page_observation(
                    structure, text, controls
                ),
            )
        authentication_controls = tuple(
            item for item in controls if item.continuation_kind is not None
        )
        # What the page says about this address outranks what it offers. A
        # provider that prints "You're already unsubscribed" above its own
        # site-wide Sign in button is not asking anyone to sign in, and
        # reading the button first recorded a completed unsubscribe as
        # skipped_login_required.
        state = self._state_from_text(text) or (
            UnsubscribePageState.ACTION_REQUIRED if authentication_controls else None
        )
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
                        UnsubscribeBrowserFailure.CONFIRMATION_TARGET_REJECTED,
                        "confirmation target binding rejected",
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
                if structure is None:
                    structure = self._document_structure()
                # A settled page that offers controls none of which reached
                # the model is this browser's own limit, not an undetermined
                # page. Both keep the same task-level code; the category is
                # what tells the live record which one happened.
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.PAGE_CONTROLS_UNMODELLED
                    if structure["control_count"]
                    else UnsubscribeBrowserFailure.PAGE_STATE_UNKNOWN,
                    "unsubscribe page state is unknown",
                    observation=self._unreadable_page_observation(
                        structure, text, controls
                    ),
                )
        state_reference = (
            "state:"
            + sha256(
                f"{state.value}\n{' '.join(text.casefold().split())}".encode()
            ).hexdigest()
        )
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
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.TRUSTED_WORLD_UNAVAILABLE,
                "trusted browser execution unavailable",
            )
        return sanitized

    def capture_audit_session(
        self,
        effect: EmailUnsubscribeEffect,
        *,
        session_reference: str,
    ) -> dict[str, object]:
        """Capture only opaque bindings for the still-live isolated page."""

        discovery = self.discover_current_page(effect)
        if (
            discovery.state is not UnsubscribePageState.ACTION_REQUIRED
            or not discovery.controls
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.SESSION_CAPTURE_REJECTED,
                "browser session capture rejected",
            )
        if (
            not session_reference.startswith("email-browser-session:")
            or len(session_reference) != len("email-browser-session:") + 64
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.SESSION_CAPTURE_REJECTED,
                "browser session capture rejected",
            )
        return {
            "version": 2,
            "session_reference": session_reference,
            "action_identity": effect.action_identity,
            "effect_digest": effect.effect_digest,
            "entry_reference": effect.entry_reference,
            "control_references": [control.reference for control in discovery.controls],
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
        appended = effect.operations[executed_prefix_length]
        if appended.kind is UnsubscribeOperationKind.RECONCILE_HANDOFF:
            return
        controls = {control.reference: control for control in discovery.controls}
        if discovery.state is not UnsubscribePageState.ACTION_REQUIRED or set(
            controls
        ) != set(session.control_references):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.SESSION_CONTROL_CHANGED,
                "browser session control changed",
            )
        expected_kind = {
            UnsubscribeOperationKind.SUBMIT_FORM: "form",
            UnsubscribeOperationKind.CLICK_CONFIRMATION: "link",
            UnsubscribeOperationKind.RECONCILE_HANDOFF: "credential_handoff",
        }.get(appended.kind)
        control = controls.get(appended.target_reference)
        allowed_kinds = {
            UnsubscribeOperationKind.SUBMIT_FORM: {"form", "email_otp"},
            UnsubscribeOperationKind.CLICK_CONFIRMATION: {
                "link",
                "captcha_handoff",
            },
            UnsubscribeOperationKind.RECONCILE_HANDOFF: {
                "captcha_handoff",
                "credential_handoff",
            },
        }
        if control is None or control.kind not in allowed_kinds.get(
            appended.kind, {expected_kind}
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.SESSION_CONTROL_CHANGED,
                "browser session control changed",
            )

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
                UnsubscribeBrowserFailure.RECEIPT_READBACK_FAILED,
                "confirmation receipt readback failed",
            ) from None

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        try:
            if not self._document_url and getattr(self.page, "url") == "about:blank":
                if not effect.operations:
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.OPERATION_SEQUENCE_EMPTY,
                        "accepted operation sequence is empty",
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
            receipt_id = (
                f"unsubscribe-receipt:{effect.effect_digest[:24]}:{state.value}"
            )
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
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.STATE_READBACK_FAILED,
                "browser state readback failed",
            ) from None

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
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.CONTROL_UNAVAILABLE,
                "accepted browser control is unavailable",
            )
        pairs = [
            (name, value) for name, _field_type, value in binding.successful_controls
        ]
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
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.CONTROL_SEMANTICS_REJECTED,
                "browser control semantics rejected",
            )
        response = self._context.request.post(
            binding.target_url,
            data=encoded,
            headers={"Content-Type": binding.enctype},
            max_redirects=0,
            timeout=self.timeout_ms,
        )
        response_url = self._validate_navigation_target(response.url)
        if response.status < 200 or response.status >= 300:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.FORM_RESPONSE_REJECTED,
                "form provider response rejected",
            )
        body = response.body()
        if len(body) > 1_048_576:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.FORM_RESPONSE_REJECTED,
                "form provider response rejected",
            )
        self._document_url = response_url
        self.page.set_content(
            response.text(),
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        self._raise_if_blocked()

    def _execute_email_otp_control(
        self,
        effect: EmailUnsubscribeEffect,
        binding: _AuditedControlBinding,
    ) -> None:
        if not self.connected_recipient or self.email_otp_resolver is None:
            raise UnsubscribeProviderAuthError("connected mailbox OTP is unavailable")
        site_domain = (urlsplit(binding.target_url).hostname or "").casefold()
        if (
            not binding.field_selector
            or not binding.submitter_selector
            or not binding.field_name
            or not binding.challenge_context_reference
            or binding.challenge_opened_at is None
            or binding.challenge_expires_at is None
        ):
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.OTP_BINDING_INCOMPLETE,
                "email OTP control binding is incomplete",
            )
        challenge = EmailOtpChallenge(
            recipient=self.connected_recipient,
            site_domain=site_domain,
            context_reference=binding.challenge_context_reference,
            opened_at=binding.challenge_opened_at,
            expires_at=binding.challenge_expires_at,
        )
        candidate = self.email_otp_resolver(challenge)
        if candidate is None:
            raise UnsubscribeProviderAuthError("connected mailbox OTP is unavailable")
        secret: str | None = candidate.consume()
        field = self.page.locator(binding.field_selector)
        try:
            assert secret is not None
            pairs = [
                (name, value)
                for name, _field_type, value in binding.successful_controls
            ]
            pairs.append((binding.field_name, secret))
            if binding.submitter_name:
                pairs.append((binding.submitter_name, binding.submitter_value))
            if (
                binding.method != "POST"
                or binding.enctype != "application/x-www-form-urlencoded"
            ):
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.OTP_REQUEST_REJECTED,
                    "email OTP request semantics rejected",
                )
            response = self._context.request.post(
                binding.target_url,
                data=urlencode(pairs),
                headers={"Content-Type": binding.enctype},
                max_redirects=0,
                timeout=self.timeout_ms,
            )
            response_url = self._validate_navigation_target(response.url)
            if response.status < 200 or response.status >= 300:
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.OTP_RESPONSE_REJECTED,
                    "email OTP provider response rejected",
                )
            body = response.body()
            if len(body) > 1_048_576:
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.OTP_RESPONSE_REJECTED,
                    "email OTP provider response rejected",
                )
            self._document_url = response_url
            self.page.set_content(
                response.text(),
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        finally:
            secret = None
            try:
                if field.count() == 1:
                    field.fill("", timeout=min(self.timeout_ms, 500))
            except Exception:
                pass
        self._raise_if_blocked()
        self._document_url = self._validate_navigation_target(
            str(getattr(self.page, "url"))
        )

    def _attempt_rendered_challenge(self, binding: _AuditedControlBinding) -> None:
        if not binding.field_selector:
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.CAPTCHA_BINDING_INCOMPLETE,
                "CAPTCHA control binding is incomplete",
            )
        checkbox = self.page.locator(binding.field_selector)
        if checkbox.count() == 1:
            checkbox.click(timeout=self.timeout_ms)
        self.page.wait_for_timeout(min(self.timeout_ms, 1_000))
        self._raise_if_blocked()

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        try:
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
                        raise UnsubscribeBrowserError(
                            UnsubscribeBrowserFailure.ONE_CLICK_RESPONSE_REJECTED,
                            "one-click provider response rejected",
                        )
                    visible = response.text()
                    if not visible.strip():
                        visible = (
                            f"List-Unsubscribe POST returned HTTP {response.status}"
                        )
                finally:
                    isolated.close()
                state = self._state_from_text(visible)
                if state not in {
                    UnsubscribePageState.DONE,
                    UnsubscribePageState.ALREADY_UNSUBSCRIBED,
                }:
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.ONE_CLICK_UNVERIFIED,
                        "one-click outcome is unverified",
                    )
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
                try:
                    self.page.goto(
                        self._validate_navigation_target(private_url),
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                except Exception:
                    self._raise_if_blocked()
                    raise
                self._raise_if_blocked()
                self._document_url = self._validate_navigation_target(
                    getattr(self.page, "url")
                )
            elif operation.kind is UnsubscribeOperationKind.FOLLOW_REDIRECT:
                self._raise_if_blocked()
                self._document_url = self._validate_navigation_target(
                    getattr(self.page, "url")
                )
            elif operation.kind is UnsubscribeOperationKind.RECONCILE_HANDOFF:
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
                        and binding.control.kind in {"form", "email_otp"}
                    ),
                    None,
                )
                if control is None:
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.CONTROL_UNAVAILABLE,
                        "accepted browser control is unavailable",
                    )
                if control.control.kind == "email_otp":
                    self._execute_email_otp_control(effect, control)
                else:
                    self._execute_audited_control(control)
            elif operation.kind is UnsubscribeOperationKind.CLICK_CONFIRMATION:
                control = next(
                    (
                        binding
                        for binding in self._ordinary_controls()
                        if binding.control.reference == operation.target_reference
                        and binding.control.kind in {"link", "captcha_handoff"}
                    ),
                    None,
                )
                if control is None:
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.CONTROL_UNAVAILABLE,
                        "accepted browser control is unavailable",
                    )
                if control.control.kind == "captcha_handoff":
                    self._attempt_rendered_challenge(control)
                else:
                    self._execute_audited_control(control)
            elif operation.kind is UnsubscribeOperationKind.CONFIRM_EMAIL:
                if self.confirmation_target_resolver is None:
                    raise UnsubscribeProviderAuthError(
                        "confirmation mailbox provider is unavailable"
                    )
                target = self.confirmation_target_resolver(effect)
                if target is None:
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.CONFIRMATION_ENTRY_MISSING,
                        "confirmation email has no accepted entry",
                    )
                expected_reference = confirmation_target_reference(
                    target.confirmation_message_identity,
                )
                if (
                    target.effect_digest != effect.effect_digest
                    or target.target_reference != operation.target_reference
                    or target.target_reference != expected_reference
                ):
                    raise UnsubscribeBrowserError(
                        UnsubscribeBrowserFailure.CONFIRMATION_TARGET_REJECTED,
                        "confirmation target binding rejected",
                    )
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
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.OPERATION_KIND_REJECTED,
                    "browser operation kind rejected",
                )
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
                        evidence="confirmation-mail:"
                        + sha256(
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
        except Exception as exc:
            if "timeout" in type(exc).__name__.casefold():
                raise UnsubscribeBrowserError(
                    UnsubscribeBrowserFailure.OPERATION_TIMEOUT,
                    "browser operation timed out",
                ) from None
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.OPERATION_FAILED,
                "browser operation failed",
            ) from None


def open_live_unsubscribe_session(
    profile: object,
    *,
    timeout_ms: int = 5_000,
    connected_recipient: str = "",
    email_otp_resolver: Callable[[EmailOtpChallenge], ConnectedMailboxOtp | None]
    | None = None,
) -> tuple[object, object, object, Callable[[], None]]:
    """Open one isolated page in the dedicated profile and adapt it.

    The caller owns the profile lock through the session manager; this only
    builds the context, the single blank page, and the browser adapter, and
    returns the cleanup that closes both.
    """

    from app.email_browser_profile import launch_persistent_email_context

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise UnsubscribeBrowserError(
            UnsubscribeBrowserFailure.RUNTIME_UNAVAILABLE,
            "headless browser runtime is unavailable",
        ) from exc
    playwright = sync_playwright().start()
    context = None
    try:
        context = launch_persistent_email_context(playwright, profile)
        page = context.new_page()
        for existing in tuple(getattr(context, "pages", ())):
            if existing is not page:
                existing.close()
        if str(getattr(page, "url")) != "about:blank":
            raise UnsubscribeBrowserError(
                UnsubscribeBrowserFailure.SESSION_RESTORE_REJECTED,
                "browser session restore rejected",
            )
        browser = PlaywrightUnsubscribeBrowser(
            page,
            timeout_ms=timeout_ms,
            connected_recipient=connected_recipient,
            email_otp_resolver=email_otp_resolver,
        )

        def cleanup() -> None:
            try:
                context.close()
            finally:
                playwright.stop()

        return context, page, browser, cleanup
    except Exception:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        playwright.stop()
        raise


def execute_unsubscribe_in_dedicated_profile(
    effect: EmailUnsubscribeEffect,
    entries: tuple[UnsubscribeEntry, ...],
    *,
    store: EmailStore,
    profile: object,
    owner: Mapping[str, object],
    timeout_ms: int = 5_000,
    executed_prefix_length: int | None = None,
    connected_recipient: str = "",
    email_otp_resolver: Callable[
        [EmailOtpChallenge], ConnectedMailboxOtp | None
    ]
    | None = None,
    session_manager: object | None = None,
) -> UnsubscribeExecutionResult | UnsubscribeContinuationResult:
    """Run one unsubscribe effect only in a locked headless profile.

    The profile and lock are supplied by the service runtime.  This helper has
    no path or URL arguments: the entry URL is retained only in the in-memory
    ``UnsubscribeEntry`` selected by the task-bound operation.
    """

    from app.email_browser_profile import (
        EmailBrowserProfileError,
        email_browser_session_manager,
    )

    manager = session_manager or email_browser_session_manager(profile)

    def session_failure() -> UnsubscribeExecutionResult:
        journal = [
            RedactedUnsubscribeStep(
                operation=step["operation"],
                state=step["state"],
                reference=step["reference"],
            )
            for step in store.list_email_unsubscribe_steps(effect.action_identity)
        ]
        return _result(
            UnsubscribeOutcome.FAILED_BROWSER,
            journal,
            error_code="email_unsubscribe_browser_session_unavailable",
        )

    def clear_session() -> None:
        try:
            manager.close_action(effect.action_identity)
        except Exception:
            pass
        try:
            profile.clear_audit_session(effect.action_identity)
        except (EmailBrowserProfileError, OSError):
            pass

    def open_live_session() -> tuple[object, object, object, Callable[[], None]]:
        return open_live_unsubscribe_session(
            profile,
            timeout_ms=timeout_ms,
            connected_recipient=connected_recipient,
            email_otp_resolver=email_otp_resolver,
        )

    restored_session: _RestoredAuditSession | None = None
    session_reference = ""
    if executed_prefix_length is not None and executed_prefix_length > 0:
        try:
            payload = profile.load_audit_session(effect.action_identity)
            if payload is None:
                return session_failure()
            restored_session = _validated_restored_audit_session(
                payload,
                effect,
                executed_prefix_length=executed_prefix_length,
            )
            session_reference = restored_session.session_reference
            browser = manager.resume(
                session_reference=session_reference,
                action_identity=effect.action_identity,
                previous_effect_digest=effect.previous_effect_digest,
            )
            browser.connected_recipient = connected_recipient.strip()
            browser.email_otp_resolver = email_otp_resolver
            browser.validate_restored_audit_session(
                effect,
                restored_session,
                executed_prefix_length=executed_prefix_length,
            )
        except Exception:
            clear_session()
            return session_failure()
    else:
        try:
            profile.clear_audit_session(effect.action_identity)
            session_reference, browser = manager.start(
                action_identity=effect.action_identity,
                effect_digest=effect.effect_digest,
                open_session=open_live_session,
            )
        except Exception:
            clear_session()
            return session_failure()

    result = UnsubscribeExecutor(store, browser, owner=owner).execute(
        effect,
        entries,
        executed_prefix_length=executed_prefix_length,
    )
    if isinstance(result, UnsubscribeContinuationResult):
        try:
            profile.save_audit_session(
                effect.action_identity,
                browser.capture_audit_session(
                    effect,
                    session_reference=session_reference,
                ),
            )
            manager.retain(effect.action_identity, effect_digest=effect.effect_digest)
        except (EmailBrowserProfileError, UnsubscribeBrowserError, OSError):
            clear_session()
            return session_failure()
    else:
        clear_session()
    return result


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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


def _bounded_context_fragment(value: str, *, from_end: bool) -> str:
    encoded = value.encode("utf-8")
    bounded = (
        encoded[-_TEXT_CONTEXT_SIDE_BYTES:]
        if from_end
        else encoded[:_TEXT_CONTEXT_SIDE_BYTES]
    )
    return bounded.decode("utf-8", "ignore")


def _marker_is_local_to_url(
    body_text: str,
    marker_span: tuple[int, int],
    url_match: re.Match[str],
) -> bool:
    marker_start, marker_end = marker_span
    if marker_end <= url_match.start():
        gap = body_text[marker_end : url_match.start()]
    elif url_match.end() <= marker_start:
        gap = body_text[url_match.end() : marker_start]
    else:
        gap = ""
    return (
        len(gap) <= _TEXT_MARKER_MAX_DISTANCE_CHARS
        and gap.count("\n") <= _TEXT_MARKER_MAX_LINE_BREAKS
    )


def _text_https_links(body_text: str) -> tuple[tuple[str, str], ...]:
    links: list[tuple[str, str]] = []
    matches = tuple(re.finditer(r"https://[^\s<>\"']+", body_text, flags=re.IGNORECASE))
    marker_spans: list[tuple[int, int]] = []
    normalized = body_text.casefold()
    for marker in _UNSUBSCRIBE_MARKERS:
        start = 0
        while (position := normalized.find(marker, start)) >= 0:
            marker_spans.append((position, position + len(marker)))
            start = position + len(marker)
    assigned_markers: set[int] = set()
    for marker_span in marker_spans:
        local_matches = tuple(
            index
            for index, match in enumerate(matches)
            if _marker_is_local_to_url(body_text, marker_span, match)
        )
        if not local_matches:
            continue
        marker_start, marker_end = marker_span
        assigned_markers.add(
            min(
                local_matches,
                key=lambda index: (
                    min(
                        abs(marker_start - matches[index].end()),
                        abs(matches[index].start() - marker_end),
                    )
                    * 2
                    + (0 if matches[index].start() >= marker_end else 1)
                ),
            )
        )
    for index, match in enumerate(matches):
        candidate = match.group(0).strip("'\"()[]{}<>,.;，。；")
        before_start = max(
            0,
            match.start() - 96,
            matches[index - 1].end() if index else 0,
        )
        after_end = min(
            len(body_text),
            match.end() + 96,
            matches[index + 1].start() if index + 1 < len(matches) else len(body_text),
        )
        before = re.sub(
            r"https://[^\s<>\"']+",
            "[URL]",
            body_text[before_start : match.start()],
            flags=re.IGNORECASE,
        )
        after = re.sub(
            r"https://[^\s<>\"']+",
            "[URL]",
            body_text[match.end() : after_end],
            flags=re.IGNORECASE,
        )
        context = (
            _bounded_context_fragment(before, from_end=True)
            + " [URL] "
            + _bounded_context_fragment(after, from_end=False)
        ).strip()
        if _is_private_https_url(candidate) and (
            index in assigned_markers
            or _contains_unsubscribe_marker(urlsplit(candidate).path)
        ):
            links.append((candidate, context))
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

    candidates: list[tuple[UnsubscribeEntrySource, str, int, str]] = []
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
                    "List-Unsubscribe",
                )
            )
        elif _is_mailto_url(value):
            candidates.append(
                (UnsubscribeEntrySource.HEADER_MAILTO, value, 20, "List-Unsubscribe")
            )

    parser = _BodyLinkParser()
    parser.feed(body_html)
    parser.close()
    for value, label in parser.links:
        if _is_private_https_url(value) and (
            _contains_unsubscribe_marker(label)
            or _contains_unsubscribe_marker(urlsplit(value).path)
        ):
            candidates.append(
                (UnsubscribeEntrySource.BODY_HTML_HTTPS, value, 30, label)
            )
    for value, context in _text_https_links(body_text):
        candidates.append((UnsubscribeEntrySource.BODY_TEXT_HTTPS, value, 40, context))

    by_url: dict[str, tuple[UnsubscribeEntrySource, str, int, str, int]] = {}
    for discovery_order, (source, private_url, priority, context) in enumerate(
        candidates
    ):
        current = by_url.get(private_url)
        if current is None or priority < current[2]:
            by_url[private_url] = (
                source,
                private_url,
                priority,
                context,
                discovery_order,
            )
    ordered = sorted(by_url.values(), key=lambda item: (item[2], item[4]))
    return tuple(
        UnsubscribeEntry(
            index=index,
            source=source,
            reference=unsubscribe_entry_reference(private_url),
            private_url=private_url,
            priority=priority,
            scheme=urlsplit(private_url).scheme.casefold(),
            host=(urlsplit(private_url).hostname or "").casefold(),
            context=context.encode("utf-8")[:160].decode("utf-8", "ignore").strip(),
        )
        for index, (source, private_url, priority, context, _order) in enumerate(
            ordered
        )
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


class UnsubscribeSelectionUnresolvable(ValueError):
    """The authorized candidate is not among the message's current entries.

    An entry's identity is the sha256 of its exact URL, and the list is
    re-derived from the message every time a task is loaded. So a link the
    sender rotates, a body the provider renders differently, or a message that
    cannot be read right now all end here. None of them is a defect in the
    service, and none of them can be resolved by trying the same index again:
    the authorization names one entry, and that entry is not on offer.
    """


def select_exact_unsubscribe_entry(
    entries: Sequence[UnsubscribeEntry],
    selection: Mapping[str, object],
) -> UnsubscribeEntry:
    """Resolve one redacted selection back to its exact ephemeral URL."""

    expected_keys = {
        "candidate_index",
        "candidate_source",
        "candidate_digest",
        "candidate_reference",
    }
    if set(selection) != expected_keys:
        raise ValueError("unsubscribe candidate selection is incomplete")
    index = selection["candidate_index"]
    if type(index) is not int or index < 0 or index >= len(entries):
        raise UnsubscribeSelectionUnresolvable("unsubscribe candidate index changed")
    entry = entries[index]
    expected = {
        "candidate_index": entry.index,
        "candidate_source": entry.source.value,
        "candidate_digest": entry.reference.removeprefix("unsubscribe-entry:"),
        "candidate_reference": entry.reference,
    }
    if dict(selection) != expected:
        raise UnsubscribeSelectionUnresolvable("unsubscribe candidate binding changed")
    return entry


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
    error_category: str = "",
    result_text: str = "",
    observation_digest: str = "",
    result_text_digest: str = "",
    result_text_truncated: bool = False,
    started_at: str = "",
    completed_at: str = "",
) -> UnsubscribeExecutionResult:
    return UnsubscribeExecutionResult(
        outcome=outcome,
        disposition=disposition_for_unsubscribe_outcome(outcome),
        journal=tuple(journal),
        receipt=receipt,
        error_code=error_code,
        error_category=error_category,
        result_text=result_text,
        observation_digest=observation_digest,
        result_text_digest=result_text_digest,
        result_text_truncated=result_text_truncated,
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
    result_text_digest = (
        sha256(result_text.encode("utf-8")).hexdigest() if result_text else ""
    )
    return _result(
        outcome,
        journal,
        receipt=observation.receipt,
        result_text=result_text,
        observation_digest=observation_digest if observation.visible_text else "",
        result_text_digest=result_text_digest,
        result_text_truncated=(
            bool(result_text) and observation_digest != result_text_digest
        ),
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
                    UnsubscribeDiscoveredControl(**item) for item in durable["controls"]
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
            result_text_digest=receipt["result_text_digest"],
            result_text_truncated=receipt["result_text_truncated"],
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
        result_text_digest: str | None = None,
        result_text_truncated: bool | None = None,
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
                result_text_digest=result_text_digest,
                result_text_truncated=result_text_truncated,
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
            result_text_digest=persisted["result_text_digest"],
            result_text_truncated=persisted["result_text_truncated"],
            started_at=persisted["started_at"],
            completed_at=persisted["completed_at"],
        )

    def _claim_write(
        self, effect: EmailUnsubscribeEffect
    ) -> Mapping[str, object] | None:
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
                or previous_effect.get("effect_digest") != effect.previous_effect_digest
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
        except Exception as exc:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=(
                    _UNRESOLVED_ERROR
                    if reconciliation_only
                    else _browser_failure_code(exc)
                ),
                error_category=_browser_failure_category(exc),
                # The same field a success uses for the page it read. A failure
                # that records nothing cannot be diagnosed later, and this one
                # could not be: see _unreadable_page_observation.
                **_browser_failure_observation_fields(exc),
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
                    error_code=("email_unsubscribe_authentication_controls_blocked"),
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
                    result_text_digest=terminal.result_text_digest,
                    result_text_truncated=terminal.result_text_truncated,
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
        except Exception as exc:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=(
                    _UNRESOLVED_ERROR
                    if reconciliation_only
                    else _browser_failure_code(exc)
                ),
                error_category=_browser_failure_category(exc),
                # The same field a success uses for the page it read. A failure
                # that records nothing cannot be diagnosed later, and this one
                # could not be: see _unreadable_page_observation.
                **_browser_failure_observation_fields(exc),
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
        terminal = (
            None
            if observation is None
            else _terminal_result(effect, observation, journal)
        )
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
                result_text_digest=terminal.result_text_digest,
                result_text_truncated=terminal.result_text_truncated,
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
            if (
                observation is not None
                and observation.next_operation_reference
                != operation.operation_reference
            ):
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
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code=("email_unsubscribe_authentication_controls_blocked"),
                )
            except UnsubscribeProviderAuthError:
                return _result(
                    UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
                    journal,
                    error_code="email_unsubscribe_provider_auth_failed",
                )
            except Exception as exc:
                if _is_unoperable_page(exc):
                    # The page was reached and read; this service just will not
                    # operate what it offers. A rerun re-reads the same page
                    # with the same model, so calling it a retryable browser
                    # fault only churns the queue and hides the real state.
                    # The observation records what the page said.
                    receipt = UnsubscribeTerminalReceipt(
                        receipt_id=(
                            f"unsubscribe-receipt:{effect.effect_digest[:24]}"
                            ":no_reliable_entry"
                        ),
                        evidence="page-not-operable",
                        entry_reference=effect.entry_reference,
                        effect_digest=effect.effect_digest,
                    )
                    return self._persist_terminal(
                        effect,
                        UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY,
                        receipt,
                        journal,
                        final_step=RedactedUnsubscribeStep(
                            operation=operation.kind.value,
                            state="skipped_no_reliable_entry",
                            reference=receipt.receipt_id,
                        ),
                        claim_owned=True,
                        **_browser_failure_observation_fields(exc),
                    )
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code=_browser_failure_code(exc),
                    error_category=_browser_failure_category(exc),
                    **_browser_failure_observation_fields(exc),
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
                    result_text_digest=terminal.result_text_digest,
                    result_text_truncated=terminal.result_text_truncated,
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


# The direct unsubscribe driver in app.email_unsubscribe_direct builds the
# same results from the same page reads, so it needs these under a public
# name. They stay one definition: the private names are what this module's
# own call sites already use.
make_unsubscribe_result = _result
terminal_unsubscribe_result = _terminal_result
browser_failure_code = _browser_failure_code
browser_failure_category = _browser_failure_category
browser_failure_observation_fields = _browser_failure_observation_fields
is_unoperable_page = _is_unoperable_page
