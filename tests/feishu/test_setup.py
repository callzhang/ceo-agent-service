from types import SimpleNamespace

from app.feishu import setup


def test_registration_manifest_is_exactly_least_privilege():
    manifest = setup.registration_manifest()
    assert manifest["tenant_scopes"] == (
        "im:message.p2p_msg:readonly",
        "im:message.group_at_msg:readonly",
        "im:message:send_as_bot",
    )
    assert manifest["events"] == ("im.message.receive_v1",)
    assert manifest["addons_preset"] is False


def test_dependency_check_requires_pinned_versions(monkeypatch):
    versions = {
        "lark-channel-sdk": "1.2.0",
        "lark-oapi": "1.7.1",
    }
    monkeypatch.setattr(setup.metadata, "version", versions.__getitem__)
    status = setup.dependency_status()
    assert status.channel_version_ok and status.oapi_version_ok


def test_doctor_is_offline_and_never_exposes_secret(monkeypatch):
    monkeypatch.setattr(
        setup,
        "dependency_status",
        lambda: setup.FeishuDependencyStatus(
            True, "1.2.0", True, True, "1.7.1", True
        ),
    )
    result = setup.doctor(app_id="cli_test", app_secret="super-secret")
    assert result.status == "ready_for_explicit_live_check"
    assert result.checks["network"] == "not_checked"
    assert "super-secret" not in repr(result)


def test_save_secret_uses_keyring_service_without_returning_secret(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        set_password=lambda service, username, secret: calls.append(
            (service, username, secret)
        )
    )
    monkeypatch.setattr(setup.importlib, "import_module", lambda name: fake)
    assert setup.save_app_secret("secret") is None
    assert calls == [("ceo-agent-service/feishu", "app_secret", "secret")]
