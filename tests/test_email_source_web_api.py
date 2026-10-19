"""Default Console HTTP source wiring with real SQLite and readonly fake IMAP."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests import test_email_classification_reader as source_fixtures
from tests.test_email_exact_source import HEADERS, STRUCTURE
from tests.test_email_imap_readonly import FakeImapSession
from app.email_imap_readonly import ImapReadonlyAdapter
from app.email_account_connector import ConnectorPriority
from app.web_api.email import register_email_routes


catalog = source_fixtures.catalog


def source_client(catalog, monkeypatch, *, unavailable=False):
    import app.email_worker as worker

    store, _ = catalog
    store.create_account(
        {
            "account_id": "account",
            "display_name": "Source",
            "email_address": "me@example.test",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "me@example.test",
            "imap_secret_reference": "env://TEST_SOURCE_SECRET",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    opened = []
    closed = []
    constructions = []

    class Registry:
        def connector_for(self, account):
            assert account == "account"
            return self

        def checkout(self, kind, priority, **kwargs):
            assert kind == "readonly" and priority == ConnectorPriority.HIGH
            session = FakeImapSession(
                headers=HEADERS,
                bodystructure=STRUCTURE,
                section_payloads={"1": b"provider-only token"},
                uidvalidity=10,
            )
            original = session.uid

            def uid(command, *args):
                if command != "FETCH":
                    return original(command, *args)
                records = []
                for requested_uid in args[0].split(b','):
                    session.headers = HEADERS.replace(
                        b"source@example.test", requested_uid + b"@example.test"
                    )
                    status, data = original(command, requested_uid, args[1])
                    assert status == 'OK'
                    records.extend(data)
                return 'OK', records

            session.uid = uid
            adapter = ImapReadonlyAdapter(session, account_id="account")
            if unavailable:

                def missing(*args, **kwargs):
                    raise LookupError("exact original unavailable")

                adapter.fetch_message = missing
                adapter.fetch_messages = missing
            opened.append(session)
            return session, adapter

        def checkin(self, session, *, keep):
            assert keep is False
            closed.append(session)
            session.logout()

    def registry_factory(actual_store, *, environment_factory):
        assert actual_store.path == store.path
        constructions.append(actual_store)
        return Registry()

    monkeypatch.setattr(worker, "_build_email_connector_registry", registry_factory)

    # These old paths read persisted originals. The default HTTP source path must not.
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not read stored original text")

    for method in (
        "get_classification",
    ):
        monkeypatch.setattr(store, method, forbidden)
    app = FastAPI()
    register_email_routes(app, lambda: store)
    return TestClient(app), opened, closed, constructions


def test_default_routes_use_exact_source_for_list_detail_and_body_search(
    catalog, monkeypatch
):
    client, opened, closed, constructions = source_client(catalog, monkeypatch)
    listed = client.get(
        "/api/console/email/classifications", params={"status": "all", "page_size": 2}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["meta"]["total"] == 6
    assert [item["id"] for item in listed.json()["items"]] == ["6", "5"]
    assert all(
        item["message_text"] == "provider-only token" for item in listed.json()["items"]
    )
    assert len(opened) == 1
    searched = client.get(
        "/api/console/email/classifications",
        params={"status": "all", "page_size": 2, "page": 2, "q": "provider-only"},
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["meta"]["total"] == 6
    assert [item["id"] for item in searched.json()["items"]] == ["4", "3"]
    assert len(opened) == 2  # One complete source batch, independent of the two-row page.
    detail = client.get("/api/console/email/classifications/1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["message_text"] == "provider-only token"
    assert len(opened) == 3 and closed == opened and len(constructions) == 1
    unsubscribe = client.get(
        "/api/console/email/classifications",
        params={"status": "unsubscribe", "q": "provider-only"},
    )
    assert unsubscribe.status_code == 200, unsubscribe.text
    assert unsubscribe.json()["meta"]["total"] == 1
    assert [item["id"] for item in unsubscribe.json()["items"]] == ["1"]
    assert len(opened) == 4 and closed == opened
    missing = client.get("/api/console/email/classifications/1000")
    assert missing.status_code == 404 and len(opened) == 4
    assert all(session.calls[-1] == ("logout",) for session in opened)


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/console/email/classifications?status=all",
        "/api/console/email/classifications?status=all&q=provider-only",
        "/api/console/email/classifications/1",
    ],
)
def test_original_unavailable_is_explicit_without_stored_body_fallback(
    catalog, monkeypatch, endpoint
):
    client, opened, closed, _ = source_client(catalog, monkeypatch, unavailable=True)
    response = client.get(endpoint)
    assert response.status_code == 502, response.text
    assert response.json()["code"] == "email_original_unavailable"
    assert opened == closed and len(opened) == 1


def test_default_source_registry_uses_web_environment_path(
    catalog, monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from app.email_provider_actions import ImapDeterministicProvider

    store, _ = catalog
    store.create_account(
        {
            "account_id": "account",
            "display_name": "Source",
            "email_address": "me@example.test",
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "imap_tls": True,
            "imap_username": "me@example.test",
            "imap_secret_reference": "CEO_EMAIL_SOURCE_IMAP_SECRET",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_tls": True,
            "smtp_username": "",
            "smtp_secret_reference": "",
            "enabled": True,
            "scan_folders": ["INBOX"],
            "scan_interval_seconds": 60,
        }
    )
    monkeypatch.delenv("CEO_EMAIL_SOURCE_IMAP_SECRET", raising=False)
    environment_path = tmp_path / "source.env"
    environment_path.write_text("CEO_EMAIL_SOURCE_IMAP_SECRET=synthetic-test-value\n")
    calls = []

    def connect(host, username, password, **kwargs):
        assert password == "synthetic-test-value"
        assert host == "imap.example.test" and username == "me@example.test"
        calls.append(1)
        return SimpleNamespace(
            session=FakeImapSession(
                headers=HEADERS.replace(b"source@example.test", b"1@example.test"),
                bodystructure=STRUCTURE,
                section_payloads={"1": b"provider original"},
                uidvalidity=10,
            )
        )

    monkeypatch.setattr(ImapDeterministicProvider, "connect", connect)
    app = FastAPI()
    register_email_routes(app, lambda: store, email_env_path=environment_path)
    response = TestClient(app).get("/api/console/email/classifications/1")
    assert response.status_code == 200, response.text
    assert response.json()["item"]["message_text"] == "provider original"
    assert calls == [1]


def test_training_preview_uses_default_shared_source_without_db_inputs(
    catalog, monkeypatch
):
    store, _ = catalog
    with store._connect() as db:
        db.execute(
            "update emails set class_status='processed',class_legacy_processed_without_plan=1 where classification_id=1"
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must use accurate external inputs")

    monkeypatch.setattr(store, "list_selected_training_records", forbidden)
    client, opened, closed, constructions = source_client(catalog, monkeypatch)
    response = client.post(
        "/api/console/email/training/preview",
        json={
            "sources": ["agent_auto_label"],
            "categories": ["work"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["preview"]["unique_sample_count"] == 1
    assert opened == closed and len(opened) == 1 and len(constructions) == 1
    assert (
        store.latest_training_snapshot_reference("email-selected-training-snapshot-v1")
        is None
    )


def test_training_preview_missing_source_is_explicit_and_does_not_publish(
    catalog, monkeypatch
):
    store, _ = catalog
    with store._connect() as db:
        db.execute(
            "update emails set class_status='processed',class_legacy_processed_without_plan=1 where classification_id=1"
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("no database input fallback")

    monkeypatch.setattr(store, "list_selected_training_records", forbidden)
    client, opened, closed, _ = source_client(catalog, monkeypatch, unavailable=True)
    response = client.post(
        "/api/console/email/training/preview",
        json={
            "sources": ["agent_auto_label"],
            "categories": ["work"],
        },
    )
    assert response.status_code == 502, response.text
    assert response.json()["code"] == "email_training_source_unavailable"
    assert opened == closed and len(opened) == 1
    assert (
        store.latest_training_snapshot_reference("email-selected-training-snapshot-v1")
        is None
    )


def test_audit_console_preview_and_manual_freeze_share_one_source_registry(
    catalog, monkeypatch, tmp_path
):
    from app.audit_web import create_audit_app
    from app.email_store import EmailStore
    from app.email_classifier_retrain import (
        TrainingSubprocessController,
        TrainingSubprocessRun,
    )
    from tests.test_email_web_api import _NonExecutingExecutor

    monkeypatch.setenv(
        "CEO_EMAIL_EMBEDDING_URL", "https://embedding.example.invalid/v1/embeddings"
    )
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_DIMENSION", "4")
    monkeypatch.setenv("CEO_EMAIL_EMBEDDING_REVISION", "synthetic-test-revision")
    store, _ = catalog
    with store._connect() as db:
        db.execute(
            "update emails set class_status='processed',class_legacy_processed_without_plan=1 where classification_id=1"
        )
    unused_client, opened, closed, constructions = source_client(catalog, monkeypatch)
    before = len(constructions)

    def forbidden(*args, **kwargs):
        raise AssertionError("Audit training/Console must not read stored input")

    for name in (
        "get_classification",
        "list_selected_training_records",
    ):
        monkeypatch.setattr(EmailStore, name, forbidden)
    starts = []

    def start(_self, **kwargs):
        starts.append(kwargs)
        return TrainingSubprocessRun(
            run_id="audit-source-selected-run",
            status="running",
            pid=1,
            started_at=kwargs["now"].isoformat(),
            updated_at=kwargs["now"].isoformat(),
            snapshot_id=kwargs["snapshot_id"],
            snapshot_sha=kwargs["signal"].snapshot_sha,
            description_version=kwargs["signal"].description_version,
            training_selection=kwargs["training_selection"],
        )

    monkeypatch.setattr(TrainingSubprocessController, "start", start)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("")
    app = create_audit_app(
        store.path,
        workbench_asset_dir=assets,
        workbench_workspace=tmp_path,
        workbench_executor=_NonExecutingExecutor(tmp_path),
    )
    client = TestClient(
        app, headers={"Host": "127.0.0.1:8765"}, client=("127.0.0.1", 50000)
    )
    response = client.get(
        "/api/console/email/classifications", params={"status": "all", "page_size": 1}
    )
    assert response.status_code == 200, response.text
    selection = {
        "sources": ["agent_auto_label"],
        "categories": ["work"],
        "model_families": ["embedding-mlp"],
    }
    preview = client.post(
        "/api/console/email/training/preview",
        json={
            key: value for key, value in selection.items() if key != "model_families"
        },
    )
    assert preview.status_code == 200, preview.text
    trained = client.post("/api/console/email/training", json=selection)
    assert trained.status_code == 202, trained.text
    assert len(starts) == 1
    assert len(constructions) == before + 1
    assert len(opened) == 3 and opened == closed
    reference = store.latest_training_snapshot_reference(
        "email-selected-training-snapshot-v1"
    )
    snapshot = store.get_training_snapshot(reference["snapshot_id"])
    assert len(snapshot["observations"]) == 1
    assert snapshot["observations"][0]["source"] == "agent_auto_label"


def test_training_preview_does_not_run_blocking_sources_on_the_event_loop(
    catalog, monkeypatch
):
    import asyncio
    from app.email_selected_training_reader import EmailSelectedTrainingReader

    store, _ = catalog
    with store._connect() as db:
        db.execute(
            "update emails set class_status='processed',class_legacy_processed_without_plan=1 where classification_id=1"
        )
    read = EmailSelectedTrainingReader.list_selected_training_records

    def off_loop(self, **kwargs):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return read(self, **kwargs)

    monkeypatch.setattr(
        EmailSelectedTrainingReader, "list_selected_training_records", off_loop
    )
    client, opened, closed, _ = source_client(catalog, monkeypatch)
    response = client.post(
        "/api/console/email/training/preview",
        json={
            "sources": ["agent_auto_label"],
            "categories": ["work"],
        },
    )
    assert response.status_code == 200, response.text
    assert opened == closed and len(opened) == 1


def test_manual_training_request_does_not_run_blocking_sources_on_the_event_loop(
    catalog,
):
    import asyncio
    from types import SimpleNamespace

    store, _ = catalog
    calls = []

    class Service:
        def request_manual_training(self):
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            calls.append(1)
            return SimpleNamespace(
                training_run=None,
                decision=SimpleNamespace(
                    due=False, reason="not_due", pending_examples=0
                ),
            )

    app = FastAPI()
    register_email_routes(app, lambda: store, email_learning_factory=lambda: Service())
    response = TestClient(app).post("/api/console/email/training")
    assert response.status_code == 200, response.text
    assert calls == [1]
