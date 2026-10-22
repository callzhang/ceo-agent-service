import copy
import json
from pathlib import Path

import pytest

from app import cli
from app.okr_models import OkrReviewPayload


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "app" / "schemas"
OUTPUT_SCHEMA_PATH = SCHEMA_DIR / "okr_review.schema.json"


@pytest.fixture
def okr_runner(tmp_path, monkeypatch):
    execution = object()
    monkeypatch.setattr(
        "app.agent_runtime_production.build_production_routed_codex_execution",
        lambda **kwargs: execution,
    )
    runner = cli._build_okr_review_runner(
        cli.WorkerSettings(db_path=tmp_path / "unused.sqlite3", workspace=tmp_path),
        object(),
    )
    assert runner.routed_execution is execution
    return runner


def _output_schema():
    assert OUTPUT_SCHEMA_PATH.is_file()
    return json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _assert_strict_objects(node, path=()):
    if isinstance(node, dict):
        if node.get("type") == "object":
            properties = node.get("properties")
            assert isinstance(properties, dict), path
            assert node.get("additionalProperties") is False, path
            assert node.get("required") == list(properties), path
        for key, value in node.items():
            _assert_strict_objects(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_strict_objects(value, (*path, index))


def test_okr_review_builder_selects_dedicated_output_schema(okr_runner):
    assert okr_runner.spec.schema_path == SCHEMA_DIR / "agent_envelope.schema.json"
    assert okr_runner.spec.output_schema_path == OUTPUT_SCHEMA_PATH


def test_okr_review_selected_output_schema_has_only_strict_objects(okr_runner):
    selected = okr_runner.spec.output_schema_path or okr_runner.spec.schema_path
    _assert_strict_objects(json.loads(selected.read_text(encoding="utf-8")))


def test_okr_review_output_payload_matches_existing_models():
    schema = _output_schema()
    model = OkrReviewPayload.model_json_schema()
    definitions = model.pop("$defs")

    payload = copy.deepcopy(schema["properties"]["domain_payload"])
    assert payload.pop("additionalProperties") is False
    assert payload == model
    for name, definition in definitions.items():
        snapshot = copy.deepcopy(schema["$defs"][name])
        assert snapshot.pop("additionalProperties") is False
        assert snapshot == definition


def test_okr_review_output_schema_preserves_the_outer_envelope():
    schema = _output_schema()
    envelope = json.loads(
        (SCHEMA_DIR / "agent_envelope.schema.json").read_text(encoding="utf-8")
    )
    for name in OkrReviewPayload.model_json_schema()["$defs"]:
        del schema["$defs"][name]
    schema["properties"]["domain_payload"] = envelope["properties"]["domain_payload"]

    assert schema == envelope
