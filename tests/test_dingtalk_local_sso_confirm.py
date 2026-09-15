import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingtalk_local_sso_confirm.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dingtalk_local_sso_confirm", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_only_dingteam_local_confirmation_is_eligible_for_login_press():
    module = load_module()

    assert module._is_dingteam_confirmation(
        "https://login.dingtalk.com/oauth2/local_confirm.htm?code=redacted",
        "叮当OKR 登录 取消登录",
    )
    assert not module._is_dingteam_confirmation(
        "https://login.dingtalk.com/oauth2/local_confirm.htm?code=redacted",
        "其他应用 登录 取消登录",
    )
    assert not module._is_dingteam_confirmation("", "叮当OKR 登录 取消登录")


def test_login_action_selects_pressable_login_not_cancel(monkeypatch):
    module = load_module()
    login = object()
    cancel = object()
    root = object()
    children = {root: [cancel, login], login: [], cancel: []}
    text = {root: "", login: "登录", cancel: "取消登录"}
    roles = {root: "AXWebArea", login: "AXGroup", cancel: "AXGroup"}

    monkeypatch.setattr(
        module,
        "_attribute",
        lambda element, name: (
            children[element]
            if name == "AXChildren"
            else roles[element]
            if name == "AXRole"
            else None
        ),
    )
    monkeypatch.setattr(module, "_element_text", lambda element: text[element])
    monkeypatch.setattr(module, "_action_names", lambda element: ["AXPress"])

    assert module._find_login_action(root) is login


def test_press_login_action_requires_successful_ax_press(monkeypatch):
    module = load_module()
    action = object()
    calls = []
    monkeypatch.setattr(module, "_find_login_action", lambda root: action)
    monkeypatch.setattr(
        module,
        "_perform_action",
        lambda element, name: calls.append((element, name)) or 0,
    )

    module._press_login_action(object())

    assert calls == [(action, "AXPress")]


def test_ax_tree_walk_does_not_use_python_wrapper_identity():
    load_module()

    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "id(element)" not in source
    assert "inspected < 1_000" in source
