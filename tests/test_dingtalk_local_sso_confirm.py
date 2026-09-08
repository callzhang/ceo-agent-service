import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingtalk_local_sso_confirm.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dingtalk_local_sso_confirm", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_only_dingteam_local_confirmation_is_eligible_for_enter():
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
