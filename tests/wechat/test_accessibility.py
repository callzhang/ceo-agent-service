import pytest

from app.store import AutoReplyStore
from app.wechat.accessibility import (
    AccessibilityResult, MacWechatAccessibility, WechatSender, _open_target,
    _text_evidence_matches, reconcile_incomplete_deliveries,
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


def test_post_action_ambiguity_becomes_send_unknown(store):
    runner = FakeRunner(AccessibilityResult(action_performed=True, visible_confirmation=False))
    sender = WechatSender(store, runner)
    delivery = _seed_delivery(store)
    outcome = sender.send(delivery, _scope("verified"))
    assert outcome.status == "send_unknown"
    assert store.get_wechat_delivery_for_task(1).status == "send_unknown"


def test_recovery_never_resends_sending(store):
    delivery = _seed_delivery(store)
    store.mark_wechat_delivery_sending(delivery.id)
    recovered = reconcile_incomplete_deliveries(store, reader=None)
    assert recovered[0].status in {"sent", "send_unknown"}


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


def test_request_accessibility_asks_macos_to_show_prompt(monkeypatch):
    import ApplicationServices

    seen = []
    monkeypatch.setattr(
        ApplicationServices,
        "AXIsProcessTrustedWithOptions",
        lambda options: seen.append(dict(options)) or False,
    )
    monkeypatch.setattr(
        ApplicationServices, "kAXTrustedCheckOptionPrompt", "prompt", raising=False,
    )

    status = MacWechatAccessibility().request_accessibility()

    assert status == "accessibility_not_trusted"
    assert seen == [{"prompt": True}]
