import sys
import threading
from types import SimpleNamespace

import pytest

import app.wechat.accessibility as accessibility
from app.store import AutoReplyStore
from app.wechat.accessibility import (
    AccessibilityResult, MacWechatAccessibility, SenderExecutionError,
    SendOutcome, WechatSender, _open_target,
    _attribute_text_matches, _screen_is_locked, _text_evidence_matches,
    _walk_accessibility_tree, _result_after_return,
    reconcile_incomplete_deliveries,
)
from app.wechat.models import WechatReplyScope


class FakeRunner:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or AccessibilityResult(True, True, "fp-1")

    def send(
        self, target_label, reply_text, *, search_query=None,
        expected_recent_text=None,
    ):
        self.calls.append(
            (target_label, reply_text, search_query, expected_recent_text)
        )
        return self.result


class RaisingRunner:
    def send(self, *_args, **_kwargs):
        raise RuntimeError("sender transport stopped after action may have started")


class PreDispatchFailureRunner:
    def send(self, *_args, **_kwargs):
        raise SenderExecutionError(
            "sender helper is unavailable", action_may_have_started=False,
        )


def _scope(binding_status):
    return WechatReplyScope(
        account_id="acct-1", target_type="direct", target_id="u9",
        conversation_id="u9", display_name="Alex",
        trigger_mode="every_inbound_text", binding_status=binding_status,
    )


def _seed_delivery(store):
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u9", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-07-17T10:00:00", trigger_sender="Alex", trigger_text="hi",
    )
    store.create_wechat_delivery(
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u9", conversation_id="u9", reply_text="收到",
        evidence={"trigger_text": "hi"},
    )
    return store.get_wechat_delivery_for_task(1)


@pytest.fixture
def store(tmp_path):
    return AutoReplyStore(tmp_path / "w.sqlite3")


def test_unverified_binding_blocks_before_send(store):
    runner = FakeRunner()
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("unverified"))
    assert outcome.status == "failed"
    assert outcome.error == "target_binding_unverified"
    assert runner.calls == []
    assert store.get_wechat_delivery_for_task(1).status == "failed"


def test_verified_binding_sends(store):
    runner = FakeRunner(AccessibilityResult(True, True, "fp-1"))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("verified"))
    assert outcome.status == "sent"
    assert runner.calls == [("Alex", "收到", None, "hi")]


def test_two_senders_claim_delivery_once(store):
    runner = FakeRunner(AccessibilityResult(True, True, "fp-1"))
    delivery = _seed_delivery(store)
    outcomes = []

    def send() -> None:
        outcomes.append(
            WechatSender(store, runner).send(delivery, _scope("verified"))
        )

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(runner.calls) == 1
    assert sorted(item.status for item in outcomes) == ["not_claimed", "sent"]


def test_verified_binding_uses_persisted_unique_navigation_query(store):
    runner = FakeRunner(AccessibilityResult(True, True, "fp-1"))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    scope = _scope("verified").model_copy(update={
        "binding_evidence": {"navigation_query": "melody115"},
    })

    outcome = sender.send(delivery, scope)

    assert outcome.status == "sent"
    assert runner.calls == [("Alex", "收到", "melody115", "hi")]


def test_post_action_without_persisted_receipt_remains_unknown(store):
    runner = FakeRunner(AccessibilityResult(action_performed=True, visible_confirmation=False))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("verified"))
    assert outcome.status == "send_unknown"
    persisted = store.get_wechat_delivery_for_task(1)
    assert persisted.status == "send_unknown"
    assert persisted.error == "no_visible_confirmation"


def test_sender_exception_after_claim_remains_unknown(store):
    sender = WechatSender(store, RaisingRunner())
    delivery = _seed_delivery(store)

    outcome = sender.send(delivery, _scope("verified"))

    assert outcome.status == "send_unknown"
    persisted = store.get_wechat_delivery_for_task(1)
    assert persisted.status == "send_unknown"
    assert persisted.error == "sender_execution_interrupted"


def test_sender_failure_before_request_dispatch_is_retryable(store):
    sender = WechatSender(store, PreDispatchFailureRunner())
    delivery = _seed_delivery(store)

    outcome = sender.send(delivery, _scope("verified"))

    assert outcome.status == "failed"
    assert outcome.error == "sender_unavailable_before_dispatch"
    persisted = store.get_wechat_delivery_for_task(1)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.pre_action_failure is True


def test_sender_persists_specific_pre_action_failure_reason(store):
    sender = WechatSender(
        store,
        FakeRunner(
            AccessibilityResult(
                action_performed=False,
                visible_confirmation=False,
                failure_reason="composer_input_unconfirmed",
            )
        ),
    )
    delivery = _seed_delivery(store)

    outcome = sender.send(delivery, _scope("verified"))

    persisted = store.get_wechat_delivery_for_task(1)
    assert outcome == SendOutcome("failed", "composer_input_unconfirmed")
    assert persisted is not None
    assert persisted.error == "composer_input_unconfirmed"
    assert persisted.pre_action_failure is True


def test_sender_labels_missing_pre_action_failure_reason(store):
    sender = WechatSender(
        store,
        FakeRunner(
            AccessibilityResult(
                action_performed=False,
                visible_confirmation=False,
            )
        ),
    )
    delivery = _seed_delivery(store)

    outcome = sender.send(delivery, _scope("verified"))

    persisted = store.get_wechat_delivery_for_task(1)
    assert outcome == SendOutcome("failed", "sender_result_missing_failure_reason")
    assert persisted is not None
    assert persisted.error == "sender_result_missing_failure_reason"
    assert persisted.pre_action_failure is True


def test_return_key_attempt_is_unknown_when_composer_confirmation_fails():
    result = _result_after_return(cleared=False, target_fingerprint="fp-1")

    assert result == AccessibilityResult(
        action_performed=True,
        visible_confirmation=False,
        target_fingerprint="fp-1",
    )


def test_recovery_keeps_sending_unknown_without_reader(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    recovered = reconcile_incomplete_deliveries(store, reader=None)
    assert recovered[0].status == "send_unknown"
    assert recovered[0].error == "read_only_reconciliation_unavailable"


def test_recovery_keeps_unknown_without_read_only_confirmation(
    store,
):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id,
        "send_unknown",
        error="sender_execution_interrupted",
    )

    class Reader:
        account = object()

        def read_messages(self, *_args, **_kwargs):
            return []

    recovered = reconcile_incomplete_deliveries(store, Reader())

    assert recovered[0].status == "send_unknown"
    assert recovered[0].error == "read_only_reconciliation_inconclusive"


def test_recovery_records_inconclusive_after_a_reader_scan(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id,
        "send_unknown",
        error="read_only_reconciliation_unavailable",
    )

    class Reader:
        account = object()

        def read_messages(self, *_args, **_kwargs):
            return []

    recovered = reconcile_incomplete_deliveries(store, Reader())

    assert recovered[0].status == "send_unknown"
    assert recovered[0].error == "read_only_reconciliation_inconclusive"


def test_recovery_uses_explicit_account_with_ipc_style_reader(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id,
        "send_unknown",
        error="sender_execution_interrupted",
    )
    account = object()

    class Reader:
        @staticmethod
        def read_messages(requested_account, *_args, **_kwargs):
            assert requested_account is account
            return [SimpleNamespace(direction="outbound", text="收到")]

    recovered = reconcile_incomplete_deliveries(store, Reader(), account=account)

    assert recovered[0].status == "sent"


def test_recovery_scans_extended_read_only_history_for_unknown_delivery(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    store.set_wechat_delivery_status(
        delivery.id,
        "send_unknown",
        error="sender_execution_interrupted",
    )

    class Reader:
        account = object()
        requested_limit = None

        def read_messages(self, *_args, **kwargs):
            self.requested_limit = kwargs["limit"]
            return [SimpleNamespace(direction="outbound", text="收到")]

    reader = Reader()
    recovered = reconcile_incomplete_deliveries(store, reader)

    assert recovered[0].status == "sent"
    assert reader.requested_limit == 200


def test_recovery_scans_from_the_persisted_action_start(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id, now="2026-07-17T10:05:00+08:00")
    store.set_wechat_delivery_status(
        delivery.id, "send_unknown", error="sender_execution_interrupted",
    )

    class Reader:
        account = object()
        requested_since = None

        def read_messages(self, *_args, **kwargs):
            self.requested_since = kwargs["since"]
            return [SimpleNamespace(direction="outbound", text="收到")]

    reader = Reader()
    recovered = reconcile_incomplete_deliveries(store, reader)

    assert recovered[0].status == "sent"
    assert reader.requested_since == "2026-07-17T10:05:00+08:00"


def test_open_target_waits_for_async_composer_after_session_click():
    row = object()
    composer = object()
    composer_checks = 0
    clicked = []

    def first(*, role=None, id_eq=None, title_contains=None):
        nonlocal composer_checks
        if id_eq == "session_item_文件传输助手":
            return row
        if id_eq == "chat_input_field":
            composer_checks += 1
            return composer if composer_checks == 3 else None
        return None

    opened = _open_target(
        "文件传输助手",
        first=first,
        find_all=lambda **_kwargs: [row],
        click=lambda element, n=1: clicked.append((element, n)),
        type_fn=lambda _text: None,
        settle=0,
        sleep=lambda _seconds: None,
    )

    assert opened is composer
    assert clicked == [(row, 1)]


def test_accessibility_spaces_foreground_interactions():
    runner = MacWechatAccessibility(min_interaction_interval=30)
    now = [100.0]
    sleeps = []

    runner._wait_for_interaction_slot(
        sleep=sleeps.append,
        monotonic=lambda: now[0],
    )
    now[0] = 112.0
    runner._wait_for_interaction_slot(
        sleep=sleeps.append,
        monotonic=lambda: now[0],
    )
    now[0] = 142.0
    runner._wait_for_interaction_slot(
        sleep=sleeps.append,
        monotonic=lambda: now[0],
    )

    assert sleeps == [18.0]


def test_open_target_selects_unique_sidebar_row_by_recent_message():
    wrong_row = object()
    expected_row = object()
    composer = object()
    clicked = []

    def first(*, role=None, id_eq=None, title_contains=None):
        if id_eq == "chat_input_field":
            return composer
        return None

    def find_all(*, role=None, id_eq=None, title_contains=None):
        if id_eq == "session_item_Melody":
            return [wrong_row, expected_row]
        return []

    opened = _open_target(
        "Melody",
        first=first,
        find_all=find_all,
        subtree_has_text=lambda row, text: (
            row is expected_row and text == "那他为啥问我要材料呢"
        ),
        click=lambda element, n=1: clicked.append((element, n)),
        type_fn=lambda _text: None,
        settle=0,
        sleep=lambda _seconds: None,
        expected_recent_text="那他为啥问我要材料呢",
    )

    assert opened is composer
    assert clicked == [(expected_row, 1)]


def test_open_target_waits_for_delayed_matching_sidebar_row():
    expected_row = object()
    composer = object()
    clicked = []
    scans = 0

    def first(*, role=None, id_eq=None, title_contains=None):
        if id_eq == "chat_input_field":
            return composer
        return None

    def find_all(*, role=None, id_eq=None, title_contains=None):
        nonlocal scans
        scans += 1
        return [] if scans < 3 else [expected_row]

    opened = _open_target(
        "Melody",
        first=first,
        find_all=find_all,
        subtree_has_text=lambda row, text: (
            row is expected_row and text == "latest inbound"
        ),
        click=lambda element, n=1: clicked.append((element, n)),
        type_fn=lambda _text: None,
        settle=0,
        sleep=lambda _seconds: None,
        expected_recent_text="latest inbound",
    )

    assert opened is composer
    assert scans == 3
    assert clicked == [(expected_row, 1)]


def test_open_target_retries_same_verified_row_when_first_click_is_ignored():
    expected_row = object()
    composer = object()
    clicked = []

    def first(*, role=None, id_eq=None, title_contains=None):
        if id_eq == "chat_input_field" and len(clicked) >= 2:
            return composer
        return None

    opened = _open_target(
        "Melody",
        first=first,
        find_all=lambda **_kwargs: [expected_row],
        subtree_has_text=lambda row, text: (
            row is expected_row and text == "latest inbound"
        ),
        click=lambda element, n=1: clicked.append((element, n)),
        type_fn=lambda _text: None,
        settle=0,
        sleep=lambda _seconds: None,
        expected_recent_text="latest inbound",
    )

    assert opened is composer
    assert clicked == [(expected_row, 1), (expected_row, 1)]


def test_open_and_identify_refreshes_ax_root_after_activation(monkeypatch):
    stale_root = object()
    fresh_root = object()
    row = object()
    composer = object()
    roots = iter((stale_root, fresh_root))

    def get_attr(element, attribute, _default):
        values = {
            (stale_root, "AXChildren"): [],
            (fresh_root, "AXChildren"): [row],
            (row, "AXChildren"): [],
            (row, "AXIdentifier"): "session_item_Melody",
            (composer, "AXTitle"): "Melody",
        }
        return 0, values.get((element, attribute))

    class Runner(MacWechatAccessibility):
        @staticmethod
        def _wechat_pid(running_applications=None):
            return 123

        def _ax(self):
            fake_time = type("FakeTime", (), {"sleep": staticmethod(lambda _seconds: None)})
            return (
                fake_time,
                lambda: True,
                lambda _pid: next(roots),
                get_attr,
                None,
                None,
                None,
            )

        @staticmethod
        def _frontmost_app():
            return None

        @staticmethod
        def _reactivate(_app_ref):
            return None

        def _wait_until_idle(self):
            return None

    monkeypatch.setattr(accessibility, "_activate_wait", lambda *args, **kwargs: True)

    def open_target(_label, **kwargs):
        rows = kwargs["find_all"](id_eq="session_item_Melody")
        return composer if rows == [row] else None

    monkeypatch.setattr(accessibility, "_open_target", open_target)

    assert Runner().open_and_identify("Melody", expected_recent_text="latest") == "Melody"


def test_open_target_does_not_search_when_recent_message_is_not_in_sidebar():
    searched = []

    opened = _open_target(
        "Melody",
        first=lambda **_kwargs: None,
        find_all=lambda **_kwargs: [],
        subtree_has_text=lambda _row, _text: False,
        click=lambda _element, n=1: None,
        type_fn=searched.append,
        settle=0,
        sleep=lambda _seconds: None,
        expected_recent_text="latest inbound",
    )

    assert opened is None
    assert searched == []


def test_text_evidence_match_allows_ui_prefix_and_long_truncation():
    expected = "阿美战投你见过窦轩了？他说他当时在另外一个机构，2022年和你聊过"

    assert _text_evidence_matches(
        f"14:11 未读 {expected}",
        expected,
    )
    assert _text_evidence_matches(
        f"{expected[:20]}…",
        expected,
    )
    assert not _text_evidence_matches("嗯，我知道了", "嗯")


def test_text_evidence_match_allows_exact_short_preview_line():
    assert _text_evidence_matches(
        "Melody\n我去通州了\n17:31\n",
        "我去通州了",
    )
    assert not _text_evidence_matches(
        "Melody\n我去通州了吗\n17:31\n",
        "我去通州了",
    )


def test_attribute_text_match_checks_nonstandard_wechat_preview_attribute():
    values = {
        "AXTitle": "Melody",
        "AXCustomPreview": "14:11 阿美战投你见过窦轩了？他说他当时在另外一个机构，2022年和你聊过",
    }

    assert _attribute_text_matches(
        lambda _element, attribute: values.get(attribute),
        lambda _element: list(values),
        object(),
        "阿美战投你见过窦轩了？他说他当时在另外一个机构，2022年和你聊过",
    )


def test_walk_accessibility_tree_skips_self_referential_children():
    root = object()
    session_row = object()
    preview = object()
    children = {
        root: [root, session_row],
        session_row: [preview],
        preview: [],
    }

    assert list(
        _walk_accessibility_tree(root, lambda element: children[element])
    ) == [root, session_row, preview]


def test_screen_lock_detection_handles_missing_and_numeric_values():
    assert _screen_is_locked({"CGSSessionScreenIsLocked": 1})
    assert not _screen_is_locked({"CGSSessionScreenIsLocked": 0})
    assert not _screen_is_locked({})
    assert not _screen_is_locked(None)


def test_open_target_returns_none_when_navigation_controls_are_missing():
    opened = _open_target(
        "文件传输助手",
        first=lambda **_criteria: None,
        click=lambda _element, n=1: None,
        type_fn=lambda _text: None,
        settle=0,
        sleep=lambda _seconds: None,
    )

    assert opened is None


def test_wechat_pid_comes_from_main_bundle_application():
    seen = []

    class MainWechatApplication:
        def processIdentifier(self):
            return 500

    resolver = getattr(MacWechatAccessibility, "_wechat_pid", None)
    assert resolver is not None
    pid = resolver(lambda bundle_id: seen.append(bundle_id) or [MainWechatApplication()])

    assert pid == 500
    assert seen == ["com.tencent.xinWeChat"]


def test_preflight_requires_a_usable_accessibility_window(monkeypatch):
    app = object()
    monkeypatch.setitem(
        sys.modules,
        "ApplicationServices",
        SimpleNamespace(
            AXIsProcessTrusted=lambda: True,
            AXUIElementCreateApplication=lambda _pid: app,
            AXUIElementCopyAttributeValue=lambda _app, _attribute, _unused: (0, []),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CGSessionCopyCurrentDictionary=lambda: {},
            CGWindowListCopyWindowInfo=lambda _options, _window_id: [
                {"kCGWindowOwnerPID": 500}
            ],
            kCGWindowListOptionAll=1,
            kCGNullWindowID=0,
        ),
    )
    runner = MacWechatAccessibility()
    monkeypatch.setattr(runner, "_wechat_pid", lambda: 500)

    assert runner.preflight() == "wechat_window_unavailable"


def test_preflight_reports_ready_when_wechat_has_accessibility_window(monkeypatch):
    app = object()
    monkeypatch.setitem(
        sys.modules,
        "ApplicationServices",
        SimpleNamespace(
            AXIsProcessTrusted=lambda: True,
            AXUIElementCreateApplication=lambda _pid: app,
            AXUIElementCopyAttributeValue=lambda _app, _attribute, _unused: (0, [object()]),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CGSessionCopyCurrentDictionary=lambda: {},
            CGWindowListCopyWindowInfo=lambda _options, _window_id: [
                {"kCGWindowOwnerPID": 500}
            ],
            kCGWindowListOptionAll=1,
            kCGNullWindowID=0,
        ),
    )
    runner = MacWechatAccessibility()
    monkeypatch.setattr(runner, "_wechat_pid", lambda: 500)

    assert runner.preflight() == "ready"


def test_request_accessibility_asks_macos_to_show_prompt(monkeypatch):
    seen = []
    monkeypatch.setitem(
        sys.modules,
        "ApplicationServices",
        SimpleNamespace(
            AXIsProcessTrustedWithOptions=lambda options: (
                seen.append(dict(options)) or False
            ),
            kAXTrustedCheckOptionPrompt="prompt",
        ),
    )

    status = MacWechatAccessibility().request_accessibility()

    assert status == "accessibility_not_trusted"
    assert seen == [{"prompt": True}]
