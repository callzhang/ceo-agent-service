import app.wechat.sender_helper as sender_helper


def test_sender_asks_for_accessibility_when_it_starts(monkeypatch, tmp_path):
    asked = []

    class Runner:
        def __init__(self, **_kwargs):
            pass

        def request_accessibility(self):
            asked.append(True)
            return "accessibility_not_trusted"

    class Server:
        def __init__(self, *_args):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(sender_helper, "MacWechatAccessibility", Runner)
    monkeypatch.setattr(sender_helper, "WechatSenderUnixServer", Server)
    monkeypatch.setattr(sender_helper, "_watch_wechat_foreground", lambda _stop: None)

    assert sender_helper.main(["serve", "--socket", str(tmp_path / "s.sock")]) == 0
    assert asked == [True]
