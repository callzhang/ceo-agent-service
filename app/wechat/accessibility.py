"""Fail-closed WeChat delivery via macOS Accessibility.

Binding must be ``verified`` before any send is attempted (display-name-only match
can never be verified). Delivery transitions are conditional and exact-once:
    ready_to_send -> sending -> sent
                             -> send_unknown   (action performed, no confirmation)
    ready_to_send -> failed  (only before the action, e.g. unverified binding)
Recovery reconciles orphaned ``sending`` rows by inspecting outbound local
messages; it never calls the sender.

The real runner (MacWechatAccessibility) drives WeChat through the same stable
AX identifiers proven in the send spike (search_item_function_<name>,
chat_input_field). It is guarded behind pyobjc and is not exercised by unit
tests, which inject a fake runner.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class AccessibilityResult:
    action_performed: bool
    visible_confirmation: bool
    target_fingerprint: str = ""
    failure_reason: str = ""


@dataclass
class SendOutcome:
    status: str
    error: str = ""


class SenderExecutionError(RuntimeError):
    """Sender transport failed with an explicit action-dispatch boundary."""

    def __init__(self, message: str, *, action_may_have_started: bool):
        super().__init__(message)
        self.action_may_have_started = action_may_have_started


def _result_after_return(
    *, cleared: bool, target_fingerprint: str,
) -> AccessibilityResult:
    """Return was posted; composer clearing is confirmation, not dispatch proof."""
    return AccessibilityResult(
        action_performed=True,
        visible_confirmation=cleared,
        target_fingerprint=target_fingerprint,
    )


def target_fingerprint(account_id: str, target_type: str, target_id: str, visible_identity: str) -> str:
    raw = f"{account_id}\0{target_type}\0{target_id}\0{visible_identity}".encode()
    return hashlib.sha256(raw).hexdigest()


def _activate_wait(pid, *, first, sleep, reactivate, attempts=4) -> bool:
    """Bring WeChat to the front and wait until its UI tree is actually populated
    (the search field / session list is present). WeChat exposes an empty AX tree
    when it is background or in a stray multi-window state, so retry activation a
    few times with growing waits before giving up."""
    from AppKit import NSRunningApplication
    for i in range(attempts):
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        reactivate(app)
        sleep(0.6 + 0.35 * i)
        if (first(role="AXTextArea", title_contains="搜索") is not None
                or first(id_eq="session_list") is not None):
            return True
    return False


def _poll_value(probe, *, sleep, attempts=20, interval=0.2):
    for attempt in range(attempts):
        value = probe()
        if value is not None:
            return value
        if attempt + 1 < attempts:
            sleep(interval)
    return None


def _text_evidence_matches(candidate: str, expected: str) -> bool:
    expected_text = " ".join(expected.split()).strip()
    candidate_lines = [
        " ".join(line.split()).rstrip("…").strip()
        for line in candidate.splitlines()
    ]
    candidate_text = " ".join(candidate.split()).rstrip("…").strip()
    if not candidate_text or not expected_text:
        return False
    if expected_text in candidate_lines:
        return True
    if candidate_text == expected_text:
        return True
    minimum_partial_length = 12
    return (
        len(expected_text) >= minimum_partial_length
        and expected_text in candidate_text
    ) or (
        len(candidate_text) >= minimum_partial_length
        and candidate_text in expected_text
    )


def _attribute_text_matches(g, attribute_names, element, expected: str) -> bool:
    names = {"AXTitle", "AXValue", "AXDescription"}
    try:
        names.update(str(name) for name in (attribute_names(element) or []))
    except Exception:
        pass
    for attribute in names:
        try:
            value = g(element, attribute)
        except Exception:
            continue
        if isinstance(value, str) and _text_evidence_matches(value, expected):
            return True
    return False


def _walk_accessibility_tree(root, children, *, max_depth: int = 12):
    """Traverse an AX tree once even when the provider exposes parent/self cycles."""
    seen = set()
    seen_unhashable_ids = set()

    def visit(element, depth):
        try:
            if element in seen:
                return
            seen.add(element)
        except TypeError:
            identity = id(element)
            if identity in seen_unhashable_ids:
                return
            seen_unhashable_ids.add(identity)

        yield element
        if depth >= max_depth:
            return
        for child in children(element) or []:
            yield from visit(child, depth + 1)

    yield from visit(root, 0)


def _screen_is_locked(session_state) -> bool:
    if not session_state:
        return False
    return bool(session_state.get("CGSSessionScreenIsLocked", False))


def _open_target(
    target_label, *, first, click, type_fn, settle, sleep, search_query=None,
    find_all=None, subtree_has_text=None, expected_recent_text=None,
):
    """Open a chat: prefer the sidebar row (session_item_<name>, present for recent
    conversations incl. groups — no typing, reliable, and opens named groups whose
    composer title is exactly the group name), else fall back to search. Groups do
    NOT get a ``search_item_function_`` result (that prefix is functions only)."""
    navigation_query = search_query or target_label
    row = None
    if expected_recent_text:
        def unique_matching_row():
            rows = (
                find_all(id_eq=f"session_item_{target_label}")
                if find_all is not None else []
            )
            matching = [
                candidate for candidate in rows
                if subtree_has_text is not None
                and subtree_has_text(candidate, expected_recent_text)
            ]
            return matching[0] if len(matching) == 1 else None

        row = _poll_value(unique_matching_row, sleep=sleep)
        # No matching recent-session row is not terminal: inactive direct chats
        # can still be opened through exact search below.
    elif navigation_query == target_label:
        row = first(id_eq=f"session_item_{target_label}")
    if row is not None:
        for attempt in range(3):
            if attempt and expected_recent_text:
                refreshed_row = _poll_value(unique_matching_row, sleep=sleep)
                if refreshed_row is None:
                    return None
                row = refreshed_row
            click(row)
            composer = _poll_value(
                lambda: first(id_eq="chat_input_field", title_contains=target_label),
                sleep=sleep,
                attempts=10,
            )
            if composer is not None:
                return composer
    # If the direct chat is not in the recent-session sidebar, fall back to an
    # exact search. The caller has already verified the binding; the search is
    # needed for inactive direct chats that WeChat evicts from the sidebar.
    # not in the sidebar -> search (below)
    search = first(role="AXTextArea", title_contains="搜索")
    if search is None:
        return None
    click(search, 3)              # triple-click selects any residual text
    sleep(0.2)
    type_fn(navigation_query)
    sleep(settle)
    result = _poll_value(
        lambda: (first(id_eq=f"search_item_function_{target_label}")
                 or first(role="AXStaticText", title_contains=target_label)),
        sleep=sleep,
    )
    if result is None:
        return None
    click(result)
    return _poll_value(
        lambda: first(id_eq="chat_input_field", title_contains=target_label),
        sleep=sleep,
    )


class WechatSender:
    def __init__(self, store, runner):
        self.store = store
        self.runner = runner

    def send(self, delivery, scope) -> SendOutcome:
        # Fail-closed: never send to an unverified/conflicting target.
        if getattr(scope, "binding_status", "unverified") != "verified":
            self.store.set_wechat_delivery_status(
                delivery.id,
                "failed",
                error="target_binding_unverified",
                pre_action_failure=True,
            )
            return SendOutcome("failed", "target_binding_unverified")

        # The service may attach fresher, transient direct-chat binding evidence
        # immediately before this call. Claiming reloads the durable row, whose
        # audit evidence intentionally remains the original trigger, so retain the
        # refreshed value separately for this one navigation attempt.
        expected_recent_text = delivery.evidence.get("trigger_text") or None
        claimed = self.store.claim_wechat_delivery(
            delivery.id,
            expected_execution_generation=delivery.execution_generation,
        )
        if claimed is None:
            return SendOutcome("not_claimed", "delivery_not_claimed")
        delivery = claimed
        try:
            result = self.runner.send(
                scope.display_name,
                delivery.reply_text,
                search_query=scope.binding_evidence.get("navigation_query") or None,
                expected_recent_text=expected_recent_text,
            )
        except SenderExecutionError as exc:
            if not exc.action_may_have_started:
                error = "sender_unavailable_before_dispatch"
                self.store.set_wechat_delivery_status(
                    delivery.id,
                    "failed",
                    error=error,
                    pre_action_failure=True,
                )
                return SendOutcome("failed", error)
            error = "sender_execution_interrupted"
            self.store.set_wechat_delivery_status(
                delivery.id,
                "send_unknown",
                error=error,
            )
            return SendOutcome("send_unknown", error)
        except Exception:
            error = "sender_execution_interrupted"
            self.store.set_wechat_delivery_status(
                delivery.id,
                "send_unknown",
                error=error,
            )
            return SendOutcome("send_unknown", error)

        if result.action_performed and result.visible_confirmation:
            status, error = "sent", ""
        elif result.action_performed:
            status, error = "send_unknown", "no_visible_confirmation"
        else:
            status, error = (
                "failed",
                result.failure_reason or "sender_result_missing_failure_reason",
            )
        self.store.set_wechat_delivery_status(
            delivery.id,
            status,
            error=error,
            pre_action_failure=not result.action_performed,
        )
        return SendOutcome(status, error)


def reconcile_incomplete_deliveries(store, reader, *, account=None) -> list:
    """Reconcile uncertain sends from read-only message history."""
    updated = []
    uncertain = (
        store.list_wechat_deliveries_by_status("sending")
        + store.list_wechat_deliveries_by_status("send_unknown")
    )
    for delivery in uncertain:
        resolved_account = account or getattr(reader, "account", None)
        if reader is None or resolved_account is None:
            store.set_wechat_delivery_status(
                delivery.id,
                "send_unknown",
                error=delivery.error or "read_only_reconciliation_unavailable",
            )
            refreshed = store.get_wechat_delivery_for_task(delivery.task_id)
            updated.append(refreshed if refreshed is not None else delivery)
            continue
        try:
            confirmed = _outbound_exists(reader, delivery, account=resolved_account)
        except Exception:
            store.set_wechat_delivery_status(
                delivery.id,
                "send_unknown",
                error=delivery.error or "read_only_reconciliation_failed",
            )
            refreshed = store.get_wechat_delivery_for_task(delivery.task_id)
            updated.append(refreshed if refreshed is not None else delivery)
            continue
        status = "sent" if confirmed else "send_unknown"
        error = "" if confirmed else "read_only_reconciliation_inconclusive"
        store.set_wechat_delivery_status(
            delivery.id,
            status,
            error=error,
        )
        refreshed = store.get_wechat_delivery_for_task(delivery.task_id)
        updated.append(refreshed if refreshed is not None else delivery)
    return updated


def _outbound_exists(reader, delivery, *, account=None) -> bool:
    account = account or reader.account
    messages = reader.read_messages(
        account, conversation_id=delivery.conversation_id,
        conversation_type=delivery.target_type,
        since=delivery.action_started_at,
        limit=200,
    )
    text = (delivery.reply_text or "").strip()
    return any(
        m.direction == "outbound" and (m.text or "").strip() == text for m in messages
    )


class MacWechatAccessibility:
    """Real runner (proven in the send spike). Requires pyobjc + Accessibility
    permission; drives WeChat via stable AX ids and sends with Return. Not unit
    tested (needs a live GUI). Sends only after WechatSender's binding guard.
    """
    BUNDLE_ID = "com.tencent.xinWeChat"

    def __init__(self, *, settle: float = 1.4, restore_focus: bool = True,
                 idle_seconds: float | None = None, idle_max_wait: float = 120.0):
        self.settle = settle
        # After a send, re-activate whatever app was frontmost so switching to
        # WeChat to pick the target chat only steals focus for ~1s.
        self.restore_focus = restore_focus
        if idle_seconds is None:
            try:
                from app import config
                idle_seconds = config.wechat_send_idle_seconds()
            except Exception:
                idle_seconds = 10.0
        # Selecting a chat needs WeChat briefly key (this build gates search/click
        # on its window being active). To avoid interrupting the user mid-typing,
        # wait until they've been idle for idle_seconds before foregrounding (up to
        # idle_max_wait, then proceed so the reply is not starved).
        self.idle_seconds = idle_seconds
        self.idle_max_wait = idle_max_wait

    def _wait_until_idle(self) -> None:
        import time
        try:
            import Quartz
        except Exception:
            return
        waited = 0.0
        while waited < self.idle_max_wait:
            idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType
            )
            if idle >= self.idle_seconds:
                return
            time.sleep(0.3)
            waited += 0.3

    @staticmethod
    def _frontmost_app():
        try:
            from AppKit import NSWorkspace
            return NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception:
            return None

    @staticmethod
    def _wechat_pid(running_applications=None):
        if running_applications is None:
            from AppKit import NSRunningApplication
            running_applications = (
                NSRunningApplication.runningApplicationsWithBundleIdentifier_
            )
        applications = running_applications(MacWechatAccessibility.BUNDLE_ID)
        return next(
            (
                int(application.processIdentifier())
                for application in applications
                if int(application.processIdentifier()) > 0
            ),
            None,
        )

    @staticmethod
    def _reactivate(app_ref):
        try:
            from AppKit import NSApplicationActivateIgnoringOtherApps
            if app_ref is not None:
                app_ref.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        except Exception:
            pass

    def _ax(self):
        import time
        from ApplicationServices import (
            AXIsProcessTrusted, AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue, AXUIElementSetAttributeValue,
            AXUIElementPerformAction,
        )
        import Quartz
        return time, AXIsProcessTrusted, AXUIElementCreateApplication, \
            AXUIElementCopyAttributeValue, AXUIElementSetAttributeValue, \
            AXUIElementPerformAction, Quartz

    def preflight(self) -> str:
        try:
            from ApplicationServices import (
                AXIsProcessTrusted,
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
            )
            import Quartz
        except Exception:
            return "pyobjc_unavailable"
        if not AXIsProcessTrusted():
            return "accessibility_not_trusted"
        if _screen_is_locked(Quartz.CGSessionCopyCurrentDictionary()):
            return "screen_locked"
        pid = self._wechat_pid()
        if not pid:
            return "wechat_not_running"
        for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
        ):
            if w.get("kCGWindowOwnerPID") == pid:
                app = AXUIElementCreateApplication(pid)
                error, windows = AXUIElementCopyAttributeValue(app, "AXWindows", None)
                if error == 0 and windows:
                    return "ready"
                # A composited window without an AX tree cannot receive a
                # verified target selection. Fail before consuming a retry.
                return "wechat_window_unavailable"
        return "wechat_window_unavailable"

    def request_accessibility(self) -> str:
        try:
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )
        except Exception:
            return "pyobjc_unavailable"
        trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        return self.preflight() if trusted else "accessibility_not_trusted"

    def send(
        self, target_label: str, reply_text: str, *, search_query: str | None = None,
        expected_recent_text: str | None = None,
    ) -> AccessibilityResult:
        """Compose via pure AX (AXValue), send via a key posted to WeChat's pid.

        The composer text and the Return are delivered directly to WeChat, so the
        send never steals focus. Selecting the target chat still needs a real
        click (this build exposes no selectable AX for the chat list), so WeChat
        is briefly foregrounded for navigation and the previously-frontmost app is
        re-activated afterwards.
        """
        (time, AXIsProcessTrusted, mk_app, get_attr, set_attr, perform, Quartz) = self._ax()
        if not AXIsProcessTrusted():
            return AccessibilityResult(
                False,
                False,
                failure_reason="accessibility_not_trusted",
            )
        pid = self._wechat_pid()
        if not pid:
            return AccessibilityResult(
                False,
                False,
                failure_reason="wechat_not_running",
            )
        # Prime the pre-activation AX root. WeChat replaces this root when it is
        # foregrounded, so all searches below deliberately request a fresh root.
        mk_app(pid)

        def g(el, attr):
            err, val = get_attr(el, attr, None)
            return val if err == 0 else None

        def walk(el):
            yield from _walk_accessibility_tree(
                el, lambda child: g(child, "AXChildren"), max_depth=12,
            )

        def first(role=None, id_eq=None, title_contains=None):
            for el in walk(mk_app(pid)):
                if role and g(el, "AXRole") != role:
                    continue
                if id_eq is not None and (g(el, "AXIdentifier") or "") != id_eq:
                    continue
                if title_contains and title_contains not in (g(el, "AXTitle") or ""):
                    continue
                return el
            return None

        def find_all(role=None, id_eq=None, title_contains=None):
            matches = []
            for el in walk(mk_app(pid)):
                if role and g(el, "AXRole") != role:
                    continue
                if id_eq is not None and (g(el, "AXIdentifier") or "") != id_eq:
                    continue
                if title_contains and title_contains not in (g(el, "AXTitle") or ""):
                    continue
                matches.append(el)
            return matches

        def subtree_has_text(root, expected):
            needle = expected.strip()
            if not needle:
                return False
            from ApplicationServices import AXUIElementCopyAttributeNames

            def attribute_names(element):
                err, names = AXUIElementCopyAttributeNames(element, None)
                return names if err == 0 else []

            for el in walk(root):
                if _attribute_text_matches(g, attribute_names, el, needle):
                    return True
            return False

        def center(el):
            from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
            pos, size = g(el, "AXPosition"), g(el, "AXSize")
            if not pos or not size:
                return None
            okp, p = AXValueGetValue(pos, kAXValueCGPointType, None)
            oks, s = AXValueGetValue(size, kAXValueCGSizeType, None)
            if not (okp and oks):
                return None
            return (p.x + s.width / 2, p.y + s.height / 2)

        def click(el, n=1):
            c = center(el)
            if not c:
                return
            for _ in range(n):
                for ev in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                    e = Quartz.CGEventCreateMouseEvent(None, ev, c, Quartz.kCGMouseButtonLeft)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.04)

        def type_to_wechat(s):
            # deliver keystrokes to WeChat's pid (not the frontmost app)
            for ch in s:
                for down in (True, False):
                    e = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(e, 1, ch)
                    Quartz.CGEventPostToPid(pid, e)
                    time.sleep(0.008)

        def key_to_wechat(keycode):
            for down in (True, False):
                Quartz.CGEventPostToPid(pid, Quartz.CGEventCreateKeyboardEvent(None, keycode, down))
                time.sleep(0.03)

        prev_app = self._frontmost_app()
        try:
            # --- navigation (needs a real click; briefly foreground WeChat) ---
            self._wait_until_idle()   # don't interrupt the user mid-typing
            if not _activate_wait(
                pid,
                first=first,
                sleep=time.sleep,
                reactivate=self._reactivate,
            ):
                return AccessibilityResult(
                    False,
                    False,
                    failure_reason="wechat_ui_not_ready",
                )
            composer = _open_target(
                target_label, first=first, click=click,
                type_fn=type_to_wechat, settle=self.settle, sleep=time.sleep,
                search_query=search_query,
                find_all=find_all,
                subtree_has_text=subtree_has_text,
                expected_recent_text=expected_recent_text,
            )
            if composer is None:
                return AccessibilityResult(
                    False,
                    False,
                    failure_reason="target_open_failed",
                )
            if not composer or g(composer, "AXTitle") != target_label:
                return AccessibilityResult(
                    False,
                    False,
                    failure_reason="target_binding_mismatch",
                )

            # --- compose (PURE AX) + send (key to pid, no focus steal) ---
            set_attr(composer, "AXFocused", True)
            set_attr(composer, "AXValue", reply_text)
            time.sleep(0.3)
            if reply_text not in (g(composer, "AXValue") or ""):
                # fallback: some builds ignore AXValue set -> type into WeChat
                type_to_wechat(reply_text)
                time.sleep(0.4)
                if reply_text not in (g(composer, "AXValue") or ""):
                    return AccessibilityResult(
                        False,
                        False,
                        failure_reason="composer_input_unconfirmed",
                    )
            if g(first(id_eq="chat_input_field"), "AXTitle") != target_label:
                return AccessibilityResult(
                    False,
                    False,
                    failure_reason="target_changed_before_send",
                )
            key_to_wechat(36)                # Return -> WeChat pid
            time.sleep(1.0)
            cleared = (g(first(id_eq="chat_input_field"), "AXValue") or "").strip() == ""
            fp = target_fingerprint("", "", target_label, target_label)
            return _result_after_return(
                cleared=cleared,
                target_fingerprint=fp,
            )
        finally:
            if self.restore_focus:
                self._reactivate(prev_app)

    def open_and_identify(
        self, target_label: str, *, search_query: str | None = None,
        expected_recent_text: str | None = None,
    ) -> str:
        """Open the target via search and return the visible composer title (the
        opened chat's display name), WITHOUT composing or sending. Used by binding
        verification to corroborate the UI target. "" if it could not open."""
        (time, AXIsProcessTrusted, mk_app, get_attr, set_attr, perform, Quartz) = self._ax()
        if not AXIsProcessTrusted():
            return ""
        pid = self._wechat_pid()
        if not pid:
            return ""
        # Consume the pre-activation root before _activate_wait foregrounds WeChat.
        mk_app(pid)

        def g(el, attr):
            err, val = get_attr(el, attr, None)
            return val if err == 0 else None

        def walk(el):
            yield from _walk_accessibility_tree(
                el, lambda child: g(child, "AXChildren"), max_depth=12,
            )

        def first(role=None, id_eq=None, title_contains=None):
            for el in walk(mk_app(pid)):
                if role and g(el, "AXRole") != role:
                    continue
                if id_eq is not None and (g(el, "AXIdentifier") or "") != id_eq:
                    continue
                if title_contains and title_contains not in (g(el, "AXTitle") or ""):
                    continue
                return el
            return None

        def find_all(role=None, id_eq=None, title_contains=None):
            matches = []
            for el in walk(mk_app(pid)):
                if role and g(el, "AXRole") != role:
                    continue
                if id_eq is not None and (g(el, "AXIdentifier") or "") != id_eq:
                    continue
                if title_contains and title_contains not in (g(el, "AXTitle") or ""):
                    continue
                matches.append(el)
            return matches

        def subtree_has_text(root, expected):
            needle = expected.strip()
            if not needle:
                return False
            from ApplicationServices import AXUIElementCopyAttributeNames

            def attribute_names(element):
                err, names = AXUIElementCopyAttributeNames(element, None)
                return names if err == 0 else []

            for el in walk(root):
                if _attribute_text_matches(g, attribute_names, el, needle):
                    return True
            return False

        def click(el, n=1):
            from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
            pos, size = g(el, "AXPosition"), g(el, "AXSize")
            okp, p = AXValueGetValue(pos, kAXValueCGPointType, None) if pos else (False, None)
            oks, s = AXValueGetValue(size, kAXValueCGSizeType, None) if size else (False, None)
            if not (okp and oks):
                return
            c = (p.x + s.width / 2, p.y + s.height / 2)
            for _ in range(n):
                for ev in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                                       Quartz.CGEventCreateMouseEvent(None, ev, c, Quartz.kCGMouseButtonLeft))
                time.sleep(0.04)

        def type_to_wechat(text):
            for ch in text:
                for down in (True, False):
                    e = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(e, 1, ch)
                    Quartz.CGEventPostToPid(pid, e)
                    time.sleep(0.008)

        prev_app = self._frontmost_app()
        try:
            self._wait_until_idle()
            _activate_wait(pid, first=first, sleep=time.sleep, reactivate=self._reactivate)
            composer = _open_target(
                target_label, first=first, click=click,
                type_fn=type_to_wechat, settle=self.settle, sleep=time.sleep,
                search_query=search_query,
                find_all=find_all,
                subtree_has_text=subtree_has_text,
                expected_recent_text=expected_recent_text,
            )
            if composer is None:
                return ""
            return (g(composer, "AXTitle") or "") if composer else ""
        finally:
            if self.restore_focus:
                self._reactivate(prev_app)

    def recall_last_outbound(self, text: str) -> bool:
        """BEST-EFFORT, UNVALIDATED backstop: right-click the message bubble
        containing ``text`` and click 撤回. Only works inside WeChat's ~2-minute
        recall window, with the chat still open and WeChat foregroundable. Returns
        whether 撤回 was clicked. Reliable auto-triggering is limited: immediate
        wrong-target detection is hard (duplicate names) and the DB reconcile that
        would catch it is delayed by WAL, often past the 2-minute window — so the
        real safety is confirm mode + the pre-send binding check, not this.
        """
        (time, AXIsProcessTrusted, mk_app, get_attr, set_attr, perform, Quartz) = self._ax()
        if not AXIsProcessTrusted() or not text.strip():
            return False
        pid = self._wechat_pid()
        if not pid:
            return False
        app = mk_app(pid)

        def g(el, attr):
            err, val = get_attr(el, attr, None)
            return val if err == 0 else None

        def walk(el):
            yield from _walk_accessibility_tree(
                el, lambda child: g(child, "AXChildren"), max_depth=14,
            )

        prev_app = self._frontmost_app()
        try:
            self._wait_until_idle()
            self._reactivate(
                __import__("AppKit").NSRunningApplication
                .runningApplicationWithProcessIdentifier_(pid)
            )
            time.sleep(0.5)
            bubble = None
            for el in walk(app):
                for a in ("AXValue", "AXTitle"):
                    v = g(el, a)
                    if isinstance(v, str) and text in v:
                        bubble = el
                        break
                if bubble is not None:
                    break
            if bubble is None:
                return False
            from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
            pos, size = g(bubble, "AXPosition"), g(bubble, "AXSize")
            okp, p = AXValueGetValue(pos, kAXValueCGPointType, None) if pos else (False, None)
            oks, s = AXValueGetValue(size, kAXValueCGSizeType, None) if size else (False, None)
            if not (okp and oks):
                return False
            cx, cy = p.x + s.width / 2, p.y + s.height / 2
            for ev in (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp):
                e = Quartz.CGEventCreateMouseEvent(None, ev, (cx, cy), Quartz.kCGMouseButtonRight)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.05)
            time.sleep(0.4)
            recall_item = next(
                (el for el in walk(app)
                 if g(el, "AXRole") == "AXMenuItem" and "撤回" in (g(el, "AXTitle") or "")),
                None,
            )
            if recall_item is None:
                return False
            perform(recall_item, "AXPress")
            time.sleep(0.4)
            confirm = next(
                (el for el in walk(app)
                 if g(el, "AXRole") == "AXButton" and (g(el, "AXTitle") or "") in ("确定", "确认", "撤回")),
                None,
            )
            if confirm is not None:
                perform(confirm, "AXPress")
            return True
        finally:
            if self.restore_focus:
                self._reactivate(prev_app)
