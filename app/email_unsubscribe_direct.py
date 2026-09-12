"""Unsubscribe in one call and bring back the page's own words as evidence.

The audited lifecycle asked the Consumer to propose one exact browser
operation, the Audit to accept it, and the executor to replay it under an
effect digest held by a leased claim.  Every further click - the confirm
button behind the first page, the OTP form, the second confirmation - cost
another full Consumer/Audit round trip through a durable continuation, and a
continuation that went stale failed the task instead of the page.

Unsubscribing is idempotent at the provider, so that scaffolding bought
nothing that the terminal receipt does not already buy.  This module drives
the whole operation in one pass: open the authorized entry, read the page,
operate what it offers, and stop at the first terminal state.  The receipt is
the only durable fence, and the page's own redacted text is the evidence.
"""

from __future__ import annotations

from hashlib import sha256

from app.email_unsubscribe import (
    EmailUnsubscribeEffect,
    RedactedUnsubscribeStep,
    UnsubscribeAuthenticationControlsError,
    UnsubscribeBrowserError,
    UnsubscribeDiscoveredControl,
    UnsubscribeEntry,
    UnsubscribeExecutionResult,
    UnsubscribeObservation,
    UnsubscribeOperation,
    UnsubscribeOperationKind,
    UnsubscribeOutcome,
    UnsubscribePageState,
    UnsubscribeProviderAuthError,
    UnsubscribeTerminalReceipt,
    browser_failure_code,
    browser_failure_category,
    browser_failure_observation_fields,
    is_unoperable_page,
    make_unsubscribe_result,
    terminal_unsubscribe_result,
)

# A real unsubscribe page needs one or two clicks: open, maybe confirm, maybe
# submit a reason form.  Anything past this is a page operating us.
MAX_DIRECT_UNSUBSCRIBE_CONTROLS = 4

# What this service will operate, and what each control kind costs.  A kind
# missing from here is a page this service does not drive; the two handoff
# kinds are terminal skips because the page is asking for a human.
_OPERABLE_CONTROL_KINDS: dict[str, UnsubscribeOperationKind] = {
    "form": UnsubscribeOperationKind.SUBMIT_FORM,
    "email_otp": UnsubscribeOperationKind.SUBMIT_FORM,
    "link": UnsubscribeOperationKind.CLICK_CONFIRMATION,
}
_HANDOFF_OUTCOMES: dict[str, UnsubscribeOutcome] = {
    "credential_handoff": UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED,
    "captcha_handoff": UnsubscribeOutcome.SKIPPED_CAPTCHA,
}
# Prefer the control that says what it does.  "continue" is last because it
# is as likely to be a cookie banner as a step in the unsubscribe.
_INTENT_RANK = {"unsubscribe": 0, "confirm": 1, "continue": 2}
_KIND_RANK = {"form": 0, "email_otp": 1, "link": 2}


def opening_operation(
    entry: UnsubscribeEntry,
    *,
    one_click_verified: bool,
) -> UnsubscribeOperation:
    """Return the one operation that reaches the authorized entry.

    A List-Unsubscribe-Post header that the message's own DKIM alignment
    covers is answered with the header's POST, which needs no page at all.
    Everything else opens the entry in the locked profile.
    """

    from app.email_unsubscribe import UnsubscribeEntrySource

    kind = (
        UnsubscribeOperationKind.POST_ONE_CLICK
        if one_click_verified
        and entry.source is UnsubscribeEntrySource.HEADER_ONE_CLICK_HTTPS
        else UnsubscribeOperationKind.OPEN_ENTRY
    )
    return UnsubscribeOperation(
        operation_reference=_operation_reference(kind.value, entry.reference),
        kind=kind,
        target_reference=entry.reference,
    )


def _operation_reference(kind: str, target_reference: str) -> str:
    return "unsubscribe-op:" + sha256(f"{kind}\n{target_reference}".encode()).hexdigest()


def _next_control(
    controls: tuple[UnsubscribeDiscoveredControl, ...],
    operated: frozenset[str],
) -> UnsubscribeDiscoveredControl | None:
    candidates = [
        control
        for control in controls
        if control.reference not in operated
        and (control.kind in _OPERABLE_CONTROL_KINDS or control.kind in _HANDOFF_OUTCOMES)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda control: (
            _INTENT_RANK.get(control.intent, len(_INTENT_RANK)),
            _KIND_RANK.get(control.kind, len(_KIND_RANK)),
            control.reference,
        ),
    )


_SKIP_STATES = {
    UnsubscribeOutcome.SKIPPED_LOGIN_REQUIRED: UnsubscribePageState.LOGIN_REQUIRED,
    UnsubscribeOutcome.SKIPPED_CAPTCHA: UnsubscribePageState.CAPTCHA,
}


def _skip(
    effect: EmailUnsubscribeEffect,
    outcome: UnsubscribeOutcome,
    journal: list[RedactedUnsubscribeStep],
    *,
    evidence: str,
    visible_text: str,
) -> UnsubscribeExecutionResult:
    """End the operation at a page this service will not drive further."""

    receipt = UnsubscribeTerminalReceipt(
        receipt_id=(
            f"unsubscribe-receipt:{effect.effect_digest[:24]}:{outcome.value}"
        ),
        evidence=evidence,
        entry_reference=effect.entry_reference,
        effect_digest=effect.effect_digest,
    )
    observation = UnsubscribeObservation(
        state=_SKIP_STATES[outcome],
        state_reference=receipt.receipt_id,
        receipt=receipt,
        visible_text=visible_text,
    )
    terminal = terminal_unsubscribe_result(effect, observation, journal)
    assert terminal is not None
    return terminal




def _no_reliable_entry(
    effect: EmailUnsubscribeEffect,
    journal: list[RedactedUnsubscribeStep],
    *,
    evidence: str,
    visible_text: str,
) -> UnsubscribeExecutionResult:
    """End at a page that was read but offers this service nothing to operate.

    This is not a browser fault and must not be retried: a rerun re-reads the
    same page and reaches the same conclusion.  The page's own text is kept
    so the decision is reviewable without reopening the URL.
    """

    receipt = UnsubscribeTerminalReceipt(
        receipt_id=(
            f"unsubscribe-receipt:{effect.effect_digest[:24]}:no_reliable_entry"
        ),
        evidence=evidence,
        entry_reference=effect.entry_reference,
        effect_digest=effect.effect_digest,
    )
    result_text, observation_digest = _redacted(visible_text)
    return make_unsubscribe_result(
        UnsubscribeOutcome.SKIPPED_NO_RELIABLE_ENTRY,
        journal,
        receipt=receipt,
        result_text=result_text,
        observation_digest=observation_digest if visible_text else "",
        result_text_digest=(
            sha256(result_text.encode("utf-8")).hexdigest() if result_text else ""
        ),
        result_text_truncated=bool(result_text)
        and observation_digest != sha256(result_text.encode("utf-8")).hexdigest(),
    )


def _redacted(visible_text: str) -> tuple[str, str]:
    from app.email_unsubscribe import normalize_unsubscribe_result_text

    return normalize_unsubscribe_result_text(visible_text)


def _failure(
    journal: list[RedactedUnsubscribeStep],
    exc: Exception,
) -> UnsubscribeExecutionResult:
    return make_unsubscribe_result(
        UnsubscribeOutcome.FAILED_BROWSER,
        journal,
        error_code=browser_failure_code(exc),
        error_category=browser_failure_category(exc),
        # The same field a success uses for the page it read: a failure that
        # records nothing cannot be diagnosed later.
        **browser_failure_observation_fields(exc),
    )


def run_direct_unsubscribe(
    browser: object,
    effect: EmailUnsubscribeEffect,
    entry: UnsubscribeEntry,
    *,
    one_click_verified: bool = False,
    max_controls: int = MAX_DIRECT_UNSUBSCRIBE_CONTROLS,
) -> UnsubscribeExecutionResult:
    """Open the authorized entry, operate the page, and return the outcome.

    The caller supplies a browser already bound to a locked profile and the
    one entry the ActionPlan authorized.  Nothing here consults a proposal,
    a claim, or a continuation: what happens next is decided by what the page
    shows, which is the only thing that was ever authoritative.
    """

    journal: list[RedactedUnsubscribeStep] = []
    operation = opening_operation(entry, one_click_verified=one_click_verified)
    operated: set[str] = set()
    for _ in range(max(1, max_controls) + 1):
        try:
            observation = browser.execute_operation(
                effect, entry.private_url, operation
            )
        except UnsubscribeAuthenticationControlsError:
            return make_unsubscribe_result(
                UnsubscribeOutcome.FAILED_BROWSER,
                journal,
                error_code="email_unsubscribe_authentication_controls_blocked",
            )
        except UnsubscribeProviderAuthError:
            return make_unsubscribe_result(
                UnsubscribeOutcome.FAILED_PROVIDER_AUTH,
                journal,
                error_code="email_unsubscribe_provider_auth_failed",
            )
        except Exception as exc:  # noqa: BLE001 - every browser fault is recorded
            if is_unoperable_page(exc):
                return _no_reliable_entry(
                    effect,
                    journal,
                    evidence="page-not-operable",
                    visible_text=_observation_text(exc),
                )
            return _failure(journal, exc)
        journal.append(
            RedactedUnsubscribeStep(
                operation=operation.kind.value,
                state=observation.state.value,
                reference=observation.state_reference,
            )
        )
        terminal = terminal_unsubscribe_result(effect, observation, journal)
        if terminal is not None:
            return terminal
        control = _next_control(observation.controls, frozenset(operated))
        if control is None:
            return _no_reliable_entry(
                effect,
                journal,
                evidence="page-offers-no-control",
                visible_text=observation.visible_text,
            )
        handoff = _HANDOFF_OUTCOMES.get(control.kind)
        if handoff is not None:
            # The page is asking for a person.  Say so with the page's own
            # words instead of spending the remaining steps on it.
            return _skip(
                effect,
                handoff,
                journal,
                evidence=f"page-requires-{control.kind}",
                visible_text=observation.visible_text,
            )
        operated.add(control.reference)
        kind = _OPERABLE_CONTROL_KINDS[control.kind]
        operation = UnsubscribeOperation(
            operation_reference=_operation_reference(kind.value, control.reference),
            kind=kind,
            target_reference=control.reference,
        )
    return _no_reliable_entry(
        effect,
        journal,
        evidence="page-did-not-settle",
        visible_text="",
    )


def _observation_text(exc: Exception) -> str:
    fields = browser_failure_observation_fields(exc)
    value = fields.get("result_text", "")
    return value if isinstance(value, str) else ""


def run_unsubscribe_in_dedicated_profile(
    effect: EmailUnsubscribeEffect,
    entry: UnsubscribeEntry,
    *,
    profile: object,
    one_click_verified: bool = False,
    timeout_ms: int = 5_000,
    connected_recipient: str = "",
    email_otp_resolver: object | None = None,
    session_manager: object | None = None,
) -> UnsubscribeExecutionResult:
    """Run one whole unsubscribe inside the locked headless profile.

    The session is opened for this call and closed with it. The direct path
    never resumes a page across turns, so there is no audit session to save,
    restore or validate, and no lease to outlive the call.
    """

    from app.email_browser_profile import email_browser_session_manager
    from app.email_unsubscribe import open_live_unsubscribe_session

    manager = session_manager or email_browser_session_manager(profile)

    def open_session() -> tuple[object, object, object, object]:
        return open_live_unsubscribe_session(
            profile,
            timeout_ms=timeout_ms,
            connected_recipient=connected_recipient,
            email_otp_resolver=email_otp_resolver,
        )

    try:
        _reference, browser = manager.start(
            action_identity=effect.action_identity,
            effect_digest=effect.effect_digest,
            open_session=open_session,
        )
    except Exception:  # noqa: BLE001 - a profile this service cannot open
        _close_session(manager, profile, effect.action_identity)
        return make_unsubscribe_result(
            UnsubscribeOutcome.FAILED_BROWSER,
            [],
            error_code="email_unsubscribe_browser_session_unavailable",
        )
    try:
        return run_direct_unsubscribe(
            browser,
            effect,
            entry,
            one_click_verified=one_click_verified,
        )
    finally:
        _close_session(manager, profile, effect.action_identity)


def _close_session(manager: object, profile: object, action_identity: str) -> None:
    try:
        manager.close_action(action_identity)
    except Exception:  # noqa: BLE001 - closing must not mask the outcome
        pass
    clear = getattr(profile, "clear_audit_session", None)
    if callable(clear):
        try:
            clear(action_identity)
        except Exception:  # noqa: BLE001 - closing must not mask the outcome
            pass


class DirectEmailUnsubscribeOperation:
    """Unsubscribe one email task in a single call and record the evidence.

    Everything this needs is already durable before the call: the ActionPlan
    authorized the action, the classification named the entry, and the
    receipt table says whether it has already happened. No proposal is read
    and no acceptance is required, so there is nothing to bind, nothing to
    lease and nothing to continue.
    """

    def __init__(
        self,
        *,
        task_store: object,
        email_store: object,
        resolve_entries: object,
        run_effect: object,
    ) -> None:
        self.task_store = task_store
        self.email_store = email_store
        self.resolve_entries = resolve_entries
        self.run_effect = run_effect

    def execute(self, task_id: int) -> dict[str, object]:
        # These readers validate the task payload against the durable plan and
        # are the same ones the audited lifecycle used; only the ceremony
        # around them is gone.
        from app.email_unsubscribe_audit import (
            _authentication_from_payload,
            _current_action_plan,
            _locator_from_classification,
            _normalize_result,
            _rejection_detail,
            _resolve_entries_with_authentication,
            _store_arguments,
            _task_payload,
            _validate_current_plan,
            _validate_task_identity,
        )
        from app.email_unsubscribe import browser_unsubscribe_entries

        task = (
            self.task_store.get_reply_task(task_id)
            if isinstance(task_id, int) and not isinstance(task_id, bool)
            else None
        )
        if task is None:
            return _failed_call("unsubscribe_task_missing")
        try:
            payload = _task_payload(task)
            identity = _validate_task_identity(task, payload)
            action_identity = str(identity["action_identity"])
            existing = self.email_store.get_email_unsubscribe_receipt(action_identity)
            if existing is not None:
                # Already done once. Unsubscribing twice is harmless but
                # pointless, and the first receipt is the record.
                return _persisted_receipt_result(existing)
            classification = self.email_store.get_classification(
                identity["classification_id"]
            )
            if classification is None:
                return _failed_call("unsubscribe_classification_missing")
            plan = _current_action_plan(classification)
            _validate_current_plan(identity, classification, plan)
            entry_reference = _authorized_entry_reference(payload, identity)
            authentication = _authentication_from_payload(payload)
            one_click_verified = bool(
                authentication is not None and authentication.one_click_verified
            )
            entries = browser_unsubscribe_entries(
                tuple(
                    _resolve_entries_with_authentication(
                        self.resolve_entries,
                        _locator_from_classification(classification, identity),
                        entry_reference,
                        authentication,
                    )
                )
            )
            entry = next(
                (item for item in entries if item.reference == entry_reference),
                None,
            )
            if entry is None:
                return _failed_call("unsubscribe_entry_changed")
            effect = EmailUnsubscribeEffect(
                action_identity=action_identity,
                action_plan_id=str(identity["action_plan_id"]),
                action_plan_version=int(identity["action_plan_version"]),
                classification_id=int(identity["classification_id"]),
                account_id=str(identity["account_id"]),
                stable_message_identity=str(identity["stable_message_identity"]),
                thread_identity=str(identity["thread_identity"]),
                entry_reference=entry_reference,
                operations=(
                    opening_operation(entry, one_click_verified=one_click_verified),
                ),
            )
            result = self.run_effect(
                effect,
                entry,
                one_click_verified=one_click_verified,
            )
            if isinstance(result, UnsubscribeExecutionResult) and result.receipt:
                self._persist(effect, result, _store_arguments(effect))
            return _normalize_result(result).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - a public tool fails closed
            return _failed_call(
                f"unsubscribe_operation_rejected:{type(exc).__name__}",
                detail=_rejection_detail(exc),
            )

    def _persist(
        self,
        effect: EmailUnsubscribeEffect,
        result: UnsubscribeExecutionResult,
        store_arguments: dict[str, object],
    ) -> None:
        receipt = result.receipt
        assert receipt is not None
        # A claim the audited lifecycle left behind carries that lifecycle's
        # digest and would reject this receipt. Move it onto this run first.
        self.email_store.retire_email_unsubscribe_claim_for_direct_run(
            **store_arguments
        )
        # Every page this run touched, appended after whatever the audited
        # lifecycle already wrote for this action.
        start = len(self.email_store.list_email_unsubscribe_steps(effect.action_identity))
        journal = [
            {
                "sequence": start + offset,
                "operation": step.operation,
                "state": step.state,
                "reference": step.reference,
            }
            for offset, step in enumerate(result.journal, start=1)
        ]
        final_step = journal.pop() if journal else None
        explicit = bool(result.result_text)
        self.email_store.persist_email_unsubscribe_terminal(
            **store_arguments,
            outcome=result.outcome.value,
            receipt_id=receipt.receipt_id,
            evidence=receipt.evidence,
            result_text=result.result_text,
            observation_digest=result.observation_digest,
            result_text_digest=result.result_text_digest if explicit else None,
            result_text_truncated=result.result_text_truncated if explicit else None,
            started_at=result.started_at,
            completed_at=result.completed_at,
            final_step=final_step,
            journal_steps=journal,
            claim_owner=None,
        )


def _authorized_entry_reference(
    payload: object,
    identity: dict[str, object],
) -> str:
    """Return the entry the classification authorized for this action.

    The ActionPlan already chose one candidate and froze its reference in the
    task payload, so no agent turn has to choose it again.
    """

    references = identity["entry_references"]
    assert isinstance(references, tuple) and references
    parameters = payload.get("action_parameters")
    chosen = (
        parameters.get("candidate_reference")
        if isinstance(parameters, dict)
        else None
    )
    if isinstance(chosen, str) and chosen in references:
        return chosen
    return references[0]


def _persisted_receipt_result(receipt: dict[str, object]) -> dict[str, object]:
    from app.agent_result import AgentError

    return {
        "status": "done",
        "outcome": receipt["outcome"],
        "receipt_id": receipt["receipt_id"],
        "evidence": receipt["evidence"],
        "result_text": receipt["result_text"],
        "observation_digest": receipt["observation_digest"],
        "started_at": receipt["started_at"],
        "completed_at": receipt["completed_at"],
        "summary": str(receipt["outcome"]),
        "error": AgentError().model_dump(mode="json"),
    }


def _failed_call(code: str, *, detail: str = "") -> dict[str, object]:
    from app.agent_result import AgentError

    return {
        "status": "failed",
        "summary": f"{code}: {detail}" if detail else code,
        "error": AgentError(code=code, retryable=False).model_dump(mode="json"),
    }
