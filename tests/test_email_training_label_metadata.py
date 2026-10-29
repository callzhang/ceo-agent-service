"""Training source counts need labels, not complete input corpora."""

from tests.support.email_native_fixtures import mark_processed_with_plan

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.email_model_registry import EmailModelRegistry
from app.web_api.email import register_email_routes
from tests.test_email_classification_reader import catalog as catalog


def eligible(catalog):
    store, _ = catalog
    with store._connect() as db:
        mark_processed_with_plan(db, 1)
    return store


def test_selected_label_metadata_matches_training_records_without_inputs(catalog):
    store = eligible(catalog)
    expected = [
        {key: value for key, value in record.items() if key != "normalized_model_input"}
        for record in store.list_selected_training_records()
    ]
    assert store.list_selected_training_labels() == expected
    assert len(expected) == 1


def test_learning_catalog_does_not_construct_training_inputs(
    catalog, tmp_path, monkeypatch
):
    store = eligible(catalog)

    def forbidden():
        raise AssertionError("catalog must not read full training inputs")

    monkeypatch.setattr(store, "list_selected_training_records", forbidden)
    registry = EmailModelRegistry(tmp_path / "models")
    service = SimpleNamespace(
        registry=registry, retrain_state_path=tmp_path / "models/retrain-state.json"
    )
    app = FastAPI()
    register_email_routes(app, lambda: store, email_learning_factory=lambda: service)
    response = TestClient(app).get("/api/console/email/learning")
    assert response.status_code == 200
    labels = [
        item
        for item in response.json()["learning"]["training_sources"]
        if item["source"] == "agent_auto_label"
    ]
    assert (
        len(labels) == 1 and labels[0]["record_count"] == labels[0]["sample_count"] == 1
    )
