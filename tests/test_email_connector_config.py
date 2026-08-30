import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.email_classifier_contracts import EmailAction, EmailCategory
from app.email_connector_config import EmailAccountPayload, resolve_secret
from app.email_store import EmailStore
from app.web_api.email import register_email_routes


IMAP_SECRET = "known-imap-secret-value"
SMTP_SECRET = "known-smtp-secret-value"
UPDATED_SMTP_SECRET = "updated-smtp-secret-value"


def _account_payload(account_id: str = "work_mail") -> dict[str, object]:
    return {
        "account_id": account_id,
        "display_name": "Work Mail",
        "email_address": f"{account_id}@example.test",
        "imap_host": "imap.example.test",
        "imap_port": 993,
        "imap_tls": True,
        "imap_username": f"{account_id}@example.test",
        "imap_secret_reference": f"CEO_EMAIL_{account_id.upper()}_IMAP_SECRET",
        "smtp_host": "smtp.example.test",
        "smtp_port": 465,
        "smtp_tls": True,
        "smtp_username": f"{account_id}@example.test",
        "smtp_secret_reference": f"CEO_EMAIL_{account_id.upper()}_SMTP_SECRET",
        "enabled": True,
        "scan_folders": ["INBOX", "Archive/Follow Up"],
        "scan_interval_seconds": 60,
    }


def _client(
    tmp_path: Path,
    *,
    imap_client_factory=None,
    smtp_client_factory=None,
) -> tuple[TestClient, EmailStore, Path]:
    database = tmp_path / "worker.sqlite3"
    env_file = tmp_path / ".env"
    store = EmailStore(database)
    app = FastAPI()
    register_email_routes(
        app,
        lambda: store,
        email_env_path=env_file,
        imap_client_factory=imap_client_factory,
        smtp_client_factory=smtp_client_factory,
    )
    return TestClient(app), store, env_file


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("account_id", "Uppercase"),
        ("account_id", "a"),
        ("email_address", "not-an-email"),
        ("email_address", "missing-domain@"),
        ("imap_port", "993"),
        ("imap_tls", 1),
        ("smtp_port", 0),
        ("enabled", "true"),
        ("scan_interval_seconds", 14),
        ("scan_folders", []),
        ("scan_folders", ["INBOX", "INBOX"]),
        ("imap_secret_reference", "CEO_EMAIL_WORK_SMTP_SECRET"),
        ("smtp_secret_reference", "CEO_EMAIL_WORK_IMAP_SECRET"),
    ),
)
def test_email_account_payload_rejects_invalid_or_coerced_values(field, value):
    payload = _account_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        EmailAccountPayload.model_validate(payload)


def test_email_account_payload_forbids_extra_fields():
    payload = _account_payload()
    payload["threshold"] = 0.9

    with pytest.raises(ValidationError):
        EmailAccountPayload.model_validate(payload)


def test_email_account_payload_canonicalizes_whitespace_around_addr_spec():
    payload = _account_payload()
    payload["email_address"] = "  user@example.test  "
    payload["scan_folders"] = tuple(payload["scan_folders"])

    parsed = EmailAccountPayload.model_validate(payload)

    assert parsed.email_address == "user@example.test"


def test_email_account_validation_error_hides_nested_secret_input():
    sentinel = "known-nested-secret-sentinel"
    payload = _account_payload()
    payload["imap_secret"] = {"nested": sentinel}

    with pytest.raises(ValidationError) as captured:
        EmailAccountPayload.model_validate(payload)

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in json.dumps(captured.value.errors(), default=str)


def test_resolve_secret_accepts_only_secret_references_without_leaking_values():
    env = {"CEO_EMAIL_WORK_MAIL_IMAP_SECRET": IMAP_SECRET}

    assert resolve_secret("CEO_EMAIL_WORK_MAIL_IMAP_SECRET", env) == IMAP_SECRET
    assert resolve_secret("CEO_EMAIL_WORK_MAIL_SMTP_SECRET", env) is None
    with pytest.raises(ValueError) as captured:
        resolve_secret("NOT_A_SECRET_REFERENCE", env)

    assert IMAP_SECRET not in repr(captured.value)
    assert IMAP_SECRET not in str(captured.value)


def test_account_api_redacts_and_preserves_or_updates_env_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("CEO_EMAIL_WORK_MAIL_IMAP_SECRET", raising=False)
    monkeypatch.delenv("CEO_EMAIL_WORK_MAIL_SMTP_SECRET", raising=False)
    client, store, env_file = _client(tmp_path)
    payload = _account_payload()
    payload.update({"imap_secret": IMAP_SECRET, "smtp_secret": SMTP_SECRET})

    created = client.post("/api/console/email/accounts", json=payload)
    listed = client.get("/api/console/email/accounts")

    assert created.status_code == 201
    assert created.json()["restart_required"] is True
    assert created.json()["item"]["imap_secret_configured"] is True
    assert created.json()["item"]["smtp_secret_configured"] is True
    assert listed.status_code == 200
    assert listed.json()["items"] == [created.json()["item"]]
    for response in (created, listed):
        serialized = response.text
        assert IMAP_SECRET not in serialized
        assert SMTP_SECRET not in serialized
        assert 'imap_secret"' not in serialized
        assert 'smtp_secret"' not in serialized
        assert "imap_secret_reference" not in serialized
        assert "smtp_secret_reference" not in serialized
    assert f"CEO_EMAIL_WORK_MAIL_IMAP_SECRET={IMAP_SECRET}" in env_file.read_text()
    assert f"CEO_EMAIL_WORK_MAIL_SMTP_SECRET={SMTP_SECRET}" in env_file.read_text()
    assert IMAP_SECRET not in json.dumps(store.list_accounts())
    assert SMTP_SECRET not in json.dumps(store.list_accounts())
    assert IMAP_SECRET.encode() not in store.path.read_bytes()
    assert SMTP_SECRET.encode() not in store.path.read_bytes()

    update = _account_payload()
    update.update({"imap_secret": "   ", "smtp_secret": UPDATED_SMTP_SECRET})
    saved = client.put("/api/console/email/accounts/work_mail", json=update)

    assert saved.status_code == 200
    env_text = env_file.read_text()
    assert f"CEO_EMAIL_WORK_MAIL_IMAP_SECRET={IMAP_SECRET}" in env_text
    assert f"CEO_EMAIL_WORK_MAIL_SMTP_SECRET={UPDATED_SMTP_SECRET}" in env_text
    assert SMTP_SECRET not in env_text
    assert IMAP_SECRET not in saved.text
    assert SMTP_SECRET not in saved.text
    assert UPDATED_SMTP_SECRET not in saved.text
    assert "imap_secret_reference" not in saved.text
    assert "smtp_secret_reference" not in saved.text

    monkeypatch.delenv("CEO_EMAIL_WORK_MAIL_IMAP_SECRET")
    monkeypatch.delenv("CEO_EMAIL_WORK_MAIL_SMTP_SECRET")
    from_file = client.get("/api/console/email/accounts")
    assert from_file.json()["items"][0]["imap_secret_configured"] is True
    assert from_file.json()["items"][0]["smtp_secret_configured"] is True


def test_email_account_payload_repr_masks_optional_secret_values():
    payload = _account_payload()
    payload.update({"imap_secret": IMAP_SECRET, "smtp_secret": SMTP_SECRET})

    parsed = EmailAccountPayload.model_validate_json(json.dumps(payload))

    assert IMAP_SECRET not in repr(parsed)
    assert SMTP_SECRET not in repr(parsed)
    assert IMAP_SECRET not in repr(parsed.imap_secret)
    assert SMTP_SECRET not in repr(parsed.smtp_secret)


def test_two_accounts_share_global_classifier_config(tmp_path: Path):
    client, store, _ = _client(tmp_path)
    store.upsert_config(
        category=EmailCategory.WORK,
        description="Work",
        threshold=0.91,
        actions=(EmailAction.ARCHIVE,),
        action_parameters={},
        enabled=True,
        config_version="email-config-v1",
    )
    first = _account_payload("first_mail")
    second = _account_payload("second_mail")

    assert client.post("/api/console/email/accounts", json=first).status_code == 201
    assert client.post("/api/console/email/accounts", json=second).status_code == 201
    accounts = client.get("/api/console/email/accounts").json()["items"]

    assert [item["account_id"] for item in accounts] == ["first_mail", "second_mail"]
    assert all("threshold" not in item for item in accounts)
    assert all("category" not in item for item in accounts)
    assert all("model_id" not in item for item in accounts)
    assert store.list_configs() == [
        {
            "category": "work",
            "description": "Work",
            "threshold": 0.91,
            "actions": ["archive"],
            "action_parameters": {},
            "enabled": True,
            "config_version": "email-config-v1",
            "updated_at": store.list_configs()[0]["updated_at"],
        }
    ]


def test_email_address_is_unique_unless_request_explicitly_allows_sharing(
    tmp_path: Path,
):
    client, _, _ = _client(tmp_path)
    first = _account_payload("first_mail")
    second = _account_payload("second_mail")
    second["email_address"] = first["email_address"]

    assert client.post("/api/console/email/accounts", json=first).status_code == 201
    conflict = client.post("/api/console/email/accounts", json=second)
    second["allow_shared_email"] = True
    shared = client.post("/api/console/email/accounts", json=second)

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "email_address_conflict"
    assert first["email_address"] not in conflict.text
    assert shared.status_code == 201


def test_whitespace_equivalent_email_is_stored_canonically_and_conflicts(
    tmp_path: Path,
):
    client, store, _ = _client(tmp_path)
    first = _account_payload("first_mail")
    first["email_address"] = "user@example.test"
    second = _account_payload("second_mail")
    second["email_address"] = "  user@example.test  "

    assert client.post("/api/console/email/accounts", json=first).status_code == 201
    conflict = client.post("/api/console/email/accounts", json=second)

    assert conflict.status_code == 409
    assert store.list_accounts()[0]["email_address"] == "user@example.test"
    second["allow_shared_email"] = True
    shared = client.post("/api/console/email/accounts", json=second)
    assert shared.status_code == 201
    assert store.get_account("second_mail")["email_address"] == "user@example.test"


def test_account_api_hides_nested_secret_input_on_validation_error(tmp_path: Path):
    client, _, _ = _client(tmp_path)
    sentinel = "known-nested-secret-sentinel"
    payload = _account_payload()
    payload["imap_secret"] = {"nested": sentinel}

    response = client.post("/api/console/email/accounts", json=payload)

    assert response.status_code == 400
    assert sentinel not in response.text


@pytest.mark.parametrize(
    "mutation",
    (
        {"imap_port": "993"},
        {"imap_tls": 1},
        {"extra": "field"},
        {"email_address": "invalid"},
        {"scan_folders": "INBOX"},
        {"scan_folders": ["INBOX", "INBOX"]},
    ),
)
def test_account_api_returns_generic_400_without_echoing_invalid_json(
    tmp_path: Path,
    mutation: dict[str, object],
):
    client, _, _ = _client(tmp_path)
    payload = _account_payload()
    payload.update(mutation)
    payload["imap_secret"] = IMAP_SECRET

    response = client.post("/api/console/email/accounts", json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "code": "invalid_email_account",
        "message": "Email account configuration is invalid",
        "details": {},
    }
    assert IMAP_SECRET not in response.text
    assert json.dumps(mutation) not in response.text


def test_account_api_rejects_malformed_json_without_echoing_secret(tmp_path: Path):
    client, _, _ = _client(tmp_path)
    raw_body = b'{"imap_secret":"known-imap-secret-value"'

    response = client.post(
        "/api/console/email/accounts",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email_account"
    assert IMAP_SECRET not in response.text


def test_account_put_rejects_account_id_change_without_echoing_payload(tmp_path: Path):
    client, _, _ = _client(tmp_path)
    assert (
        client.post("/api/console/email/accounts", json=_account_payload()).status_code
        == 201
    )
    changed = _account_payload("renamed_mail")
    changed["imap_secret"] = IMAP_SECRET

    response = client.put("/api/console/email/accounts/work_mail", json=changed)

    assert response.status_code == 400
    assert response.json()["code"] == "email_account_id_immutable"
    assert IMAP_SECRET not in response.text


def test_connectivity_test_logs_in_selects_readonly_and_never_writes_mailbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[tuple[object, ...]] = []

    class FakeImap:
        def __init__(self, host: str, port: int):
            events.append(("imap.connect", host, port))

        def login(self, username: str, secret: str):
            events.append(("imap.login", username, secret == IMAP_SECRET))
            return "OK", []

        def select(self, folder: str, readonly: bool = False):
            events.append(("imap.select", folder, readonly))
            return "OK", [b"0"]

        def logout(self):
            events.append(("imap.logout",))
            return "BYE", []

    class FakeSmtp:
        def __init__(self, host: str, port: int):
            events.append(("smtp.connect", host, port))

        def login(self, username: str, secret: str):
            events.append(("smtp.login", username, secret == SMTP_SECRET))
            return 235, b"ok"

        def quit(self):
            events.append(("smtp.quit",))
            return 221, b"bye"

    monkeypatch.setenv("CEO_EMAIL_WORK_MAIL_IMAP_SECRET", IMAP_SECRET)
    monkeypatch.setenv("CEO_EMAIL_WORK_MAIL_SMTP_SECRET", SMTP_SECRET)
    client, _, _ = _client(
        tmp_path,
        imap_client_factory=FakeImap,
        smtp_client_factory=FakeSmtp,
    )
    assert (
        client.post("/api/console/email/accounts", json=_account_payload()).status_code
        == 201
    )

    response = client.post("/api/console/email/accounts/work_mail/test")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["diagnostics"] == {
        "imap": {"ok": True, "code": "connected"},
        "smtp": {"ok": True, "code": "connected"},
    }
    assert events == [
        ("imap.connect", "imap.example.test", 993),
        ("imap.login", "work_mail@example.test", True),
        ("imap.select", "INBOX", True),
        ("imap.select", "Archive/Follow Up", True),
        ("imap.logout",),
        ("smtp.connect", "smtp.example.test", 465),
        ("smtp.login", "work_mail@example.test", True),
        ("smtp.quit",),
    ]
    assert IMAP_SECRET not in response.text
    assert SMTP_SECRET not in response.text
    assert not any(
        event[0] in {"fetch", "store", "move", "expunge", "sendmail"}
        for event in events
    )


def test_connectivity_failure_returns_sanitized_per_protocol_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class FailingImap:
        def __init__(self, _host: str, _port: int):
            pass

        def login(self, _username: str, secret: str):
            raise RuntimeError(f"authentication failed for {secret}")

        def logout(self):
            return None

    class FailingSmtp:
        def __init__(self, _host: str, _port: int):
            raise OSError(f"cannot connect using {SMTP_SECRET}")

    monkeypatch.setenv("CEO_EMAIL_WORK_MAIL_IMAP_SECRET", IMAP_SECRET)
    monkeypatch.setenv("CEO_EMAIL_WORK_MAIL_SMTP_SECRET", SMTP_SECRET)
    client, _, _ = _client(
        tmp_path,
        imap_client_factory=FailingImap,
        smtp_client_factory=FailingSmtp,
    )
    assert (
        client.post("/api/console/email/accounts", json=_account_payload()).status_code
        == 201
    )

    response = client.post("/api/console/email/accounts/work_mail/test")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "account_id": "work_mail",
        "diagnostics": {
            "imap": {"ok": False, "code": "connection_failed"},
            "smtp": {"ok": False, "code": "connection_failed"},
        },
    }
    assert IMAP_SECRET not in response.text
    assert SMTP_SECRET not in response.text
