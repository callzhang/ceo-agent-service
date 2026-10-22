"""Synthetic exact weekly document reconstruction; no provider or database I/O."""

from dataclasses import replace
import json

import pytest

from app.business_source_reader import (
    AvailableSource, RequestSourceReader, SourceUnavailableReason,
    SourceVersionBinding,
)
from app.business_weekly_source_reader import weekly_provider_locator
from app.dws_client import DwsError
from app.task_report_scanner import ReportDocument, _work_item, classify_report_title, report_period
from app.task_semantic_models import BusinessTaskSignal

NODE = "node-exact"
URL = "https://original.example/report"
TITLE = "2026年10月17日项目管理部周报"
MARKDOWN = "# 进展\n中文🙂"


def signal(title=TITLE, markdown=MARKDOWN, url=URL):
    kind = classify_report_title(title)
    assert kind is not None
    doc = ReportDocument(NODE, title, url, kind, report_period(title, markdown), markdown)
    return BusinessTaskSignal(
        id=7, source_type=kind.value, source_ref=doc.source_ref,
        source_time="2026-10-18T01:02:03Z", conversation_id="report:one",
        conversation_title=title, author_user_id="actor:one", author_name="A",
        author_kind="human", evidence_text=_work_item(doc).summary,
        context_json="{}", dedupe_key="action:one", created_at="2026-10-18T01:02:04Z",
    )


def response(*, title=TITLE, markdown=MARKDOWN, version=None):
    content = {"success": True, "nodeId": NODE, "title": title, "markdown": markdown}
    if version is not None:
        content["historyVersion"] = version
    return {"complete": True, "status": "success", "content": content,
            "target": {"canonicalId": NODE, "url": "https://alias.example/report"}}


class FakeDws:
    dws_bin = "dws"

    def __init__(self, payload=None, error=None):
        self.payload = payload or response()
        self.error = error
        self.commands = []

    def run_json(self, command):
        self.commands.append(command)
        if self.error:
            raise self.error
        return self.payload


def binding(item=None, *, version=None):
    item = item or signal()
    return SourceVersionBinding.from_signal(
        item, provider_locator=weekly_provider_locator(item, history_version=version)
    )


@pytest.mark.parametrize("title", [
    "2026年10月17日管理周报", "2026年10月17日项目管理部周报",
    "2026年10月17日产研管理周报",
])
def test_current_three_real_report_kinds_and_exact_wrapper(title):
    item = signal(title=title)
    dws = FakeDws(response(title=title))
    result = RequestSourceReader(provider_reader=dws).resolve(binding(item), source_ref=item.source_ref, quote="中文🙂")
    assert isinstance(result, AvailableSource)
    assert result.body == item.evidence_text
    assert list(json.loads(result.body)) == ["report", "markdown"]
    assert list(json.loads(result.body)["report"]) == [
        "title", "document_id", "url", "report_type", "reporting_period",
    ]
    assert json.loads(result.body)["report"]["url"] == URL
    assert dws.commands == [["dws", "doc", "+fetch", "--node", NODE, "--format", "json"]]


def test_locator_is_minimal_and_factory_rejects_noncanonical_original():
    item = signal()
    locator = weekly_provider_locator(item)
    assert locator == {"kind": "weekly_report", "node_id": NODE, "url": URL}
    assert MARKDOWN not in json.dumps(locator)
    assert TITLE not in json.dumps(locator)
    payload = json.loads(item.evidence_text)
    for field, altered in [("report_type", "management_weekly_report"),
                           ("reporting_period", "2026-W01")]:
        broken = json.loads(item.evidence_text)
        broken["report"][field] = altered
        with pytest.raises(ValueError):
            weekly_provider_locator(item.model_copy(update={"evidence_text": json.dumps(broken, ensure_ascii=False)}))
    payload["markdown"] = "different"
    with pytest.raises(ValueError):
        weekly_provider_locator(item.model_copy(update={"evidence_text": json.dumps(payload, ensure_ascii=False)}))
    with pytest.raises(ValueError):
        weekly_provider_locator(item.model_copy(update={"source_ref": "dingtalk-doc:other#sha256=" + "0" * 64}))


@pytest.mark.parametrize("damage,reason", [
    ({"content": {**response()["content"], "nodeId": "other"}}, SourceUnavailableReason.VERSION_MISMATCH),
    ({"target": {"canonicalId": "other"}}, SourceUnavailableReason.VERSION_MISMATCH),
    ({"complete": False}, SourceUnavailableReason.SOURCE_INCOMPLETE),
    ({"status": "failure"}, SourceUnavailableReason.SOURCE_INCOMPLETE),
    ({"content": {**response()["content"], "success": False}}, SourceUnavailableReason.SOURCE_INCOMPLETE),
    ({"content": {**response()["content"], "title": None}}, SourceUnavailableReason.SOURCE_INCOMPLETE),
    ({"content": {**response()["content"], "markdown": 42}}, SourceUnavailableReason.SOURCE_INCOMPLETE),
    ({"content": {**response()["content"], "markdown": "changed"}}, SourceUnavailableReason.VERSION_MISMATCH),
    ({"content": {**response()["content"], "title": "2026年10月18日项目管理部周报"}}, SourceUnavailableReason.VERSION_MISMATCH),
])
def test_changed_or_incomplete_current_response(damage, reason):
    dws = FakeDws()
    dws.payload = {**dws.payload, **damage}
    assert RequestSourceReader(provider_reader=dws).resolve(binding()).reason is reason


def test_exact_history_version_and_no_current_fallback():
    dws = FakeDws(response(version=7))
    result = RequestSourceReader(provider_reader=dws).resolve(binding(version=7))
    assert isinstance(result, AvailableSource)
    assert dws.commands == [["dws", "doc", "+fetch", "--node", NODE, "--format", "json", "--version", "7"]]
    for observed in (None, 8, "7"):
        payload = {**dws.payload}
        payload["content"] = {**dws.payload["content"], "historyVersion": observed}
        failed = FakeDws(payload)
        result = RequestSourceReader(provider_reader=failed).resolve(binding(version=7))
        assert result.reason is SourceUnavailableReason.VERSION_MISMATCH
        assert len(failed.commands) == 1
    with pytest.raises(ValueError):
        weekly_provider_locator(signal(), history_version=True)
    with pytest.raises(ValueError):
        weekly_provider_locator(signal(), history_version=-1)


def test_invalid_typed_locator_is_not_read_and_conflict_is_request_scoped():
    item = signal()
    dws = FakeDws()
    good = binding(item)
    invalid = replace(good, provider_locator_json=json.dumps({"kind": "weekly_report", "node_id": NODE, "url": URL, "historyVersion": True}))
    assert RequestSourceReader(provider_reader=dws).resolve(invalid).reason is SourceUnavailableReason.INVALID_LOCATOR
    assert dws.commands == []
    reader = RequestSourceReader(provider_reader=dws)
    assert isinstance(reader.resolve(good), AvailableSource)
    assert reader.resolve(good, quote="absent").reason is SourceUnavailableReason.QUOTE_MISSING
    assert reader.resolve(good, source_ref="wrong").reason is SourceUnavailableReason.SOURCE_REF_MISMATCH
    assert reader.resolve(replace(good, body_sha256="0" * 64)).reason is SourceUnavailableReason.BINDING_CONFLICT
    assert len(dws.commands) == 1


def test_expected_provider_failure_is_unavailable_but_programmer_error_visible():
    assert RequestSourceReader(provider_reader=FakeDws(error=DwsError("unreadable"))).resolve(binding()).reason is SourceUnavailableReason.SOURCE_MISSING
    with pytest.raises(RuntimeError, match="bug"):
        RequestSourceReader(provider_reader=FakeDws(error=RuntimeError("bug"))).resolve(binding())


def test_explicit_history_version_zero_is_valid_and_exact():
    item = signal()
    locator = weekly_provider_locator(item, history_version=0)
    assert locator["historyVersion"] == 0
    dws = FakeDws(response(version=0))
    result = RequestSourceReader(provider_reader=dws).resolve(
        SourceVersionBinding.from_signal(item, provider_locator=locator)
    )
    assert isinstance(result, AvailableSource)
    assert dws.commands[0][-2:] == ["--version", "0"]


@pytest.mark.parametrize("returned", [True, 1.0])
def test_history_response_requires_exact_integer_type(returned):
    dws = FakeDws(response(version=1))
    dws.payload["content"]["historyVersion"] = returned
    result = RequestSourceReader(provider_reader=dws).resolve(binding(version=1))
    assert result.reason is SourceUnavailableReason.VERSION_MISMATCH
    assert len(dws.commands) == 1
