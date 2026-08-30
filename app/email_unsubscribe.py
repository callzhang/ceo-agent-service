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
import re
from typing import Callable, Literal, Mapping, Protocol
from urllib.parse import unquote, urlsplit

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
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an opaque redacted reference"
        ) from exc


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
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an opaque redacted reference") from exc


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
class UnsubscribeObservation:
    state: UnsubscribePageState
    state_reference: str
    next_operation_reference: str = ""
    receipt: UnsubscribeTerminalReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, UnsubscribePageState):
            raise TypeError("state must be UnsubscribePageState")
        _assert_opaque_reference(self.state_reference, field_name="state_reference")
        if self.next_operation_reference:
            _assert_strict_opaque_reference(
                self.next_operation_reference, field_name="next_operation_reference"
            )
        if self.state is UnsubscribePageState.ACTION_REQUIRED:
            if not self.next_operation_reference or self.receipt is not None:
                raise ValueError("action-required state needs exactly one next operation")
        elif self.state.value in _TERMINAL_STATES:
            if self.next_operation_reference or self.receipt is None:
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


class PlaywrightUnsubscribeBrowser:
    """Bounded sync-Playwright adapter; Playwright remains an optional dependency."""

    def __init__(
        self,
        page: object,
        *,
        timeout_ms: int = 5_000,
        allowed_hosts: frozenset[str] | None = None,
        confirmation_receipt_resolver: Callable[
            [EmailUnsubscribeEffect], UnsubscribeTerminalReceipt | None
        ]
        | None = None,
        confirmation_url_resolver: Callable[[EmailUnsubscribeEffect], str | None]
        | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self.page = page
        self.timeout_ms = timeout_ms
        self.allowed_hosts = allowed_hosts
        self.confirmation_receipt_resolver = confirmation_receipt_resolver
        self.confirmation_url_resolver = confirmation_url_resolver

    def _validate_navigation_target(self, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (
                self.allowed_hosts is not None
                and parsed.hostname not in self.allowed_hosts
            )
        ):
            raise UnsubscribeBrowserError("browser navigation target rejected")
        return value

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
        except Exception as exc:
            raise UnsubscribeBrowserError(
                "confirmation receipt readback failed"
            ) from exc

    def inspect_current_state(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
    ) -> UnsubscribeObservation:
        del private_url
        try:
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
            root = self.page.locator("[data-unsubscribe-state]")
            root.wait_for(state="visible", timeout=self.timeout_ms)
            visible_text = root.inner_text(timeout=self.timeout_ms).strip()
            if not visible_text:
                raise UnsubscribeBrowserError("unsubscribe page has no visible state")
            state_value = root.get_attribute("data-unsubscribe-state")
            state_reference = root.get_attribute("data-state-reference")
            if not state_value or not state_reference:
                raise UnsubscribeBrowserError("unsubscribe page state is incomplete")
            state = UnsubscribePageState(state_value)
            if state is UnsubscribePageState.ACTION_REQUIRED:
                next_reference = root.get_attribute("data-next-operation-reference")
                if not next_reference:
                    raise UnsubscribeBrowserError(
                        "unsubscribe page has no accepted next operation"
                    )
                return UnsubscribeObservation(
                    state=state,
                    state_reference=state_reference,
                    next_operation_reference=next_reference,
                )
            receipt_id = root.get_attribute("data-receipt-id")
            evidence = root.get_attribute("data-evidence")
            if not receipt_id or not evidence:
                raise UnsubscribeBrowserError(
                    "unsubscribe terminal page has no receipt"
                )
            return UnsubscribeObservation(
                state=state,
                state_reference=state_reference,
                receipt=UnsubscribeTerminalReceipt(
                    receipt_id=receipt_id,
                    evidence=evidence,
                    entry_reference=effect.entry_reference,
                    effect_digest=effect.effect_digest,
                ),
            )
        except UnsubscribeBrowserError:
            raise
        except Exception as exc:
            raise UnsubscribeBrowserError("browser state readback failed") from exc

    def execute_operation(
        self,
        effect: EmailUnsubscribeEffect,
        private_url: str,
        operation: UnsubscribeOperation,
    ) -> UnsubscribeObservation:
        selector = f'[data-operation-reference="{operation.target_reference}"]'
        try:
            if operation.kind in {
                UnsubscribeOperationKind.OPEN_ENTRY,
                UnsubscribeOperationKind.FOLLOW_REDIRECT,
            }:
                self.page.goto(
                    self._validate_navigation_target(private_url),
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
            elif operation.kind is UnsubscribeOperationKind.SUBMIT_FORM:
                control = self.page.locator(selector)
                control.wait_for(state="visible", timeout=self.timeout_ms)
                tag_name = control.evaluate("element => element.tagName")
                if str(tag_name).casefold() == "form":
                    control.locator("button[type=submit]").click(
                        timeout=self.timeout_ms
                    )
                else:
                    control.click(timeout=self.timeout_ms)
                self.page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=self.timeout_ms,
                )
            elif operation.kind is UnsubscribeOperationKind.CLICK_CONFIRMATION:
                control = self.page.locator(selector)
                control.wait_for(state="visible", timeout=self.timeout_ms)
                control.click(timeout=self.timeout_ms)
                self.page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=self.timeout_ms,
                )
            elif operation.kind is UnsubscribeOperationKind.CONFIRM_EMAIL:
                if self.confirmation_url_resolver is None:
                    raise UnsubscribeProviderAuthError(
                        "confirmation mailbox provider is unavailable"
                    )
                confirmation_url = self.confirmation_url_resolver(effect)
                if not confirmation_url:
                    raise UnsubscribeBrowserError(
                        "confirmation email has no accepted entry"
                    )
                self.page.goto(
                    self._validate_navigation_target(confirmation_url),
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
            else:
                raise UnsubscribeBrowserError("browser operation kind rejected")
            return self.inspect_current_state(effect, private_url)
        except (UnsubscribeBrowserError, UnsubscribeProviderAuthError):
            raise
        except Exception as exc:
            raise UnsubscribeBrowserError("browser operation failed") from exc


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
) -> tuple[UnsubscribeEntry, ...]:
    """Extract standard and explicit body entries, retaining URLs in memory only."""

    candidates: list[tuple[UnsubscribeEntrySource, str, int]] = []
    one_click = (
        "".join(list_unsubscribe_post.casefold().split())
        == "list-unsubscribe=one-click"
    )
    for value in _header_values(list_unsubscribe):
        if _is_private_https_url(value):
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
        }

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

    def _claim_write(self, effect: EmailUnsubscribeEffect) -> bool:
        try:
            claim = self.store.claim_email_unsubscribe_write(
                **self._store_arguments(effect),
                owner=self.owner,
            )
        except (EmailUnsubscribeClaimConflict, EmailUnsubscribeReceiptConflict):
            return False
        return bool(claim is not None and claim.get("acquired"))

    def execute(
        self,
        effect: EmailUnsubscribeEffect,
        entries: tuple[UnsubscribeEntry, ...],
    ) -> UnsubscribeExecutionResult:
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

        try:
            observation = self.browser.inspect_current_state(effect, entry.private_url)
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
        reconcile_step = RedactedUnsubscribeStep(
            operation="reconcile_state",
            state=observation.state.value,
            reference=observation.state_reference,
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
                final_step=reconcile_step,
                claim_owned=False,
            )

        if reconciliation_only:
            return _result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code=_UNRESOLVED_ERROR,
            )

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

        for operation in effect.operations[resume_index:]:
            if observation.next_operation_reference != operation.operation_reference:
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_operation_mismatch",
                )
            if not self._claim_write(effect):
                return _result(
                    UnsubscribeOutcome.FAILED_BROWSER,
                    journal,
                    error_code="email_unsubscribe_authorization_stale",
                )
            if reconcile_step is not None:
                try:
                    persisted = self.store.append_email_unsubscribe_step(
                        action_identity=effect.action_identity,
                        effect_digest=effect.effect_digest,
                        sequence=len(
                            self.store.list_email_unsubscribe_steps(
                                effect.action_identity
                            )
                        )
                        + 1,
                        operation=reconcile_step.operation,
                        state=reconcile_step.state,
                        reference=reconcile_step.reference,
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
                    return terminal
                return self._persist_terminal(
                    effect,
                    terminal.outcome,
                    terminal.receipt,
                    journal,
                    final_step=operation_step,
                    claim_owned=True,
                )
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
