from tests.support.legacy_todo_facts import seed_legacy_work_project
import json
import pytest
from datetime import datetime

from app.store import AutoReplyStore
from app.business_source_reader import AvailableSource, RequestSourceReader
from app.business_storage import update_business
from app.business_candidate_storage import update_business_candidate
from app.project_context_service import ProjectContextService
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_semantic_models import AttentionCategory, BusinessTaskStatus
from app.task_semantic_service import SourceSignal, TaskSemanticService, UpdateBusinessTask
from app.web_api.tasks import business_attention_list_response
from tests.test_console_web_api import _client
from tests.support.business_task_seed import seed_task


def _attention(store, category, title, now):
    with store._immediate_write_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="company_priority", anchor_ref=title, title=title, _db=db)
        signal_id = store.create_business_task_signal_in_transaction(source_type="test", source_ref=title, evidence_text=title, dedupe_key=title, source_time=now, _db=db)
        return store.create_business_attention_item_in_transaction(stable_key=title, category=category, title=title, business_area="Business", why_attention="Needs attention", current_state="Open", ceo_action="Review", anchor_id=anchor_id, evidence_signal_id=signal_id, now=now, _db=db)


def test_attention_order_and_filter_before_pagination(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old = "2026-09-01T00:00:00+00:00"
    new = "2026-09-02T00:00:00+00:00"
    ids = {
        "fyi": _attention(store, "fyi", "FYI", new),
        "watch": _attention(store, "watch", "Watch", old),
        "push_old": _attention(store, "push", "Push old", old),
        "push_new": _attention(store, "push", "Push new", new),
        "decision": _attention(store, "decision", "Decision", old),
    }
    result = business_attention_list_response(store, page=1, page_size=10)
    assert [item.id for item in result.items] == [ids[key] for key in ("decision", "push_new", "push_old", "watch", "fyi")]
    filtered = business_attention_list_response(store, page=2, page_size=1, category="push")
    assert filtered.meta.total == 2
    assert filtered.items[0].id == ids["push_old"]


def test_semantic_routes_do_not_return_legacy_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    legacy_id = seed_legacy_work_project(store, title="Legacy", category="dev", status="active")
    task_id = seed_task(store, title="Real task", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    with _client(tmp_path) as client:
        all_response = client.get("/api/console/tasks/all")
        assert all_response.status_code == 200
        assert [row["id"] for row in all_response.json()["items"]] == [task_id]
        assert client.get(f"/api/console/tasks/items/{task_id}").status_code == 200
        assert client.get(f"/api/console/tasks/legacy-projects/{legacy_id}").status_code == 200
        assert client.get("/api/console/tasks/attention").status_code == 200
        assert client.get("/api/console/tasks/projects").status_code == 200


def test_detail_routes_show_source_evidence_and_confirmed_membership(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = seed_task(store, title="Launch", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    closed_id = seed_task(store, title="Closed project task", stage="candidate", status="done", business_relevance="relevant")
    with store._immediate_write_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref="minutes-1", evidence_text="Derek assigned Launch",
            dedupe_key="meeting:1", now=datetime.fromisoformat("2026-09-02T00:00:00+00:00"), _db=db,
        )
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="project", anchor_ref="official-launch", title="Official launch", _db=db)
        project_id = store.create_business_project_in_transaction(canonical_anchor_id=anchor_id, title="Official launch", registry_source="registry", _db=db)
        store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
        store.create_business_task_anchor_link_in_transaction(task_id=closed_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        attention_id = store.create_business_attention_item_in_transaction(stable_key="launch", category="decision", title="Launch decision", business_area="Product", why_attention="Gate", current_state="Pending", ceo_action="Decide", anchor_id=anchor_id, evidence_signal_id=signal_id, now="2026-09-02T00:00:00+00:00", _db=db)
        store.link_business_attention_task_in_transaction(attention_item_id=attention_id, task_id=task_id, _db=db)
    source_binding = store.get_business_task_signal(signal_id).source
    source_body = "Derek assigned Launch"
    assert source_binding.matches_body(source_body)

    class SyntheticMeetingSourceReader(RequestSourceReader):
        def _read(self, binding):
            if binding == source_binding:
                return AvailableSource(binding, source_body)
            return super()._read(binding)

    monkeypatch.setattr(AutoReplyStore, "business_source_reader", lambda self, *, provider_reader=None: SyntheticMeetingSourceReader(provider_reader=provider_reader))
    with _client(tmp_path) as client:
        task = client.get(f"/api/console/tasks/items/{task_id}").json()["item"]
        attention = client.get(f"/api/console/tasks/attention/{attention_id}").json()["item"]
        project = client.get(f"/api/console/tasks/projects/{project_id}").json()["item"]
        original = client.get(f"/api/console/tasks/signals/{signal_id}").json()["item"]
    assert "evidence_text" not in task["evidence"][0]["signal"]
    assert original["evidence_text"] == "Derek assigned Launch"
    assert task["evidence"][0]["signal"]["source_ref"] == "minutes-1"
    assert task["official_projects"][0]["id"] == project_id
    assert [row["id"] for row in attention["linked_tasks"]] == [task_id]
    assert attention["assessment"] == {}
    assert store.get_business_task(closed_id).status.value == "done"
    assert store.get_business_task(closed_id).stage.value == "candidate"
    assert project["confirmed_tasks"][0]["id"] == task_id


def test_attention_detail_returns_saved_assessment_and_all_referenced_signals(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = "2026-09-02T00:00:00+00:00"
    attention_id = _attention(store, "watch", "Multisource", now)
    primary_id = store.get_business_attention_item(attention_id).evidence_signal_id
    with store._immediate_write_transaction() as db:
        ids = [store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref=f"minutes-{i}", evidence_text=f"Exact quote {i}", dedupe_key=f"signal-{i}", source_time=now,
            context_json=json.dumps({"source_link": f"https://example.com/minutes/{i}"}), _db=db,
        ) for i in range(3)]
        assessment = {"assessment_basis": "historical_comparison", "material_trigger": "risk_escalation", "inference": "Observe delivery", "evidence": [
            {"signal_id": primary_id, "source_ref": "Multisource", "source_excerpt": "Multisource", "source_time": now, "source_link": ""},
            {"signal_id": ids[0], "source_ref": "minutes-0", "source_excerpt": "Exact quote 0", "source_time": now, "source_link": "https://example.com/minutes/0"},
        ]}
        update_business_candidate(db, "attention_item", attention_id, {
            "assessment_json": json.dumps(assessment), "status": "resolved",
            "resolution_signal_id": ids[1], "resolved_at": now,
        })
        store.append_business_attention_event_in_transaction(attention_item_id=attention_id, event_type="opened", signal_id=ids[2], before_json="{}", after_json="{}", reason="Observed", _db=db)
    with _client(tmp_path) as client:
        response = client.get(f"/api/console/tasks/attention/{attention_id}")
    assert response.status_code == 200
    detail = response.json()["item"]
    assert detail["assessment"] == assessment
    assert {row["id"] for row in detail["evidence_signals"]} == {primary_id, *ids}


def test_attention_detail_removes_completed_member_without_resolving_or_mutating_tasks(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids = tuple(seed_task(store, title=title, stage="candidate", business_relevance="relevant") for title in ("Delivery", "Open sibling"))
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(source_type="meeting", source_ref="delivery-risk", evidence_text="Delivery remains at risk", dedupe_key="delivery-risk", _db=db)
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="project", anchor_ref="delivery-project", title="Delivery project", _db=db)
        project_id = store.create_business_project_in_transaction(canonical_anchor_id=anchor_id, title="Delivery project", registry_source="registry", _db=db)
        ProjectContextService(store).apply(project_id=project_id, context=None, signal_ids=(signal_id,), db=db)
        for task_id in task_ids:
            store.create_business_task_anchor_link_in_transaction(task_id=task_id, anchor_id=anchor_id, status="confirmed", active=True, evidence_signal_id=signal_id, _db=db)
            store.link_business_task_evidence_in_transaction(task_id=task_id, signal_id=signal_id, evidence_role="discovery", _db=db)
    projection = BusinessAttentionProjection(store)
    attention_id = projection.upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category=AttentionCategory.WATCH, title="Delivery risk", business_area="Product",
        why_attention="Delivery impact", current_state="Work ongoing", ceo_action="当前无需你处理", anchor_id=anchor_id,
        task_ids=task_ids, evidence_signal_id=signal_id,
    ))
    with _client(tmp_path) as client:
        before = client.get(f"/api/console/tasks/attention/{attention_id}").json()["item"]
        assert {row["id"] for row in before["linked_tasks"]} == set(task_ids)
        TaskSemanticService(store).update_task(UpdateBusinessTask(
            task_id=task_ids[0], status=BusinessTaskStatus.DONE,
            signal=SourceSignal(source_type="message", source_ref="delivery-complete", evidence_text="Delivery completed", dedupe_key="delivery-complete"),
        ))
        assert projection.recompute_for_tasks((task_ids[0],)) == (attention_id,)
        saved_tasks = [store.get_business_task(task_id).model_dump() for task_id in task_ids]
        saved_attention = store.get_business_attention_item(attention_id).model_dump()
        response = client.get(f"/api/console/tasks/attention/{attention_id}")
        assert response.status_code == 200
        after = response.json()["item"]
        completed = client.get(f"/api/console/tasks/items/{task_ids[0]}").json()["item"]["summary"]
    assert [row["id"] for row in after["linked_tasks"]] == [task_ids[1]]
    assert after["summary"]["linked_task_count"] == 1
    assert completed["status"] == "done"
    assert completed["stage"] == "candidate"
    assert saved_attention["status"] == "active"
    assert saved_attention["resolution_signal_id"] is None
    assert store.get_business_attention_item(attention_id).model_dump() == saved_attention
    assert [store.get_business_task(task_id).model_dump() for task_id in task_ids] == saved_tasks


def test_project_candidates_are_labeled_provisional_and_not_official(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._immediate_write_transaction() as db:
        cluster_id = store.create_business_work_cluster_in_transaction(title="Possible project", _db=db)
        candidate_id = store.create_business_project_candidate_in_transaction(cluster_id=cluster_id, title="Possible project", reason="Needs registry confirmation", _db=db)
    with _client(tmp_path) as client:
        payload = client.get("/api/console/tasks/projects").json()
    assert payload["items"] == []
    assert payload["candidates"] == [{
        "id": candidate_id, "title": "Possible project", "reason": "Needs registry confirmation",
        "status": "proposed", "cluster_id": cluster_id, "provisional": True,
        "confirmed_project_id": None,
    }]


def test_task_list_filters_by_owner_and_sorts_by_creation_or_update(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    owned = seed_task(store, title="有人负责", stage="candidate", owner_name="王明")
    bare = seed_task(store, title="无人负责", stage="candidate")
    with store._immediate_write_transaction() as db:
        update_business(db, "task", owned, {
            "created_at": "2026-09-01 00:00:00", "updated_at": "2026-09-03 00:00:00",
        })
        update_business(db, "task", bare, {
            "created_at": "2026-09-02 00:00:00", "updated_at": "2026-09-02 00:00:00",
        })
    with _client(tmp_path) as client:
        def ids(query):
            response = client.get(f"/api/console/tasks/all?{query}")
            assert response.status_code == 200
            return [row["id"] for row in response.json()["items"]]
        assert ids("owner=assigned") == [owned]
        assert ids("owner=unassigned") == [bare]
        assert ids("") == [owned, bare]
        assert ids("sort=updated") == [owned, bare]
        assert ids("sort=created") == [bare, owned]
        assert client.get("/api/console/tasks/all?owner=nobody").status_code == 400
        assert client.get("/api/console/tasks/all?sort=title").status_code == 400


def test_signal_response_view_requires_honest_original_availability():
    from pydantic import ValidationError
    from app.web_api.tasks import ConsoleBusinessTaskSignalView

    fields = dict(
        id=7, source_type="message", source_ref="m:7", source_time="",
        conversation_id="", conversation_title="", author_user_id="",
        author_name="", author_kind="unknown", context_json="{}",
        dedupe_key="source:7", created_at="2026-10-18T00:00:00Z",
    )
    available = ConsoleBusinessTaskSignalView.model_validate({
        **fields, "source_available": True, "evidence_text": "原始来源",
        "source_unavailable_reason": None,
    })
    unavailable = ConsoleBusinessTaskSignalView.model_validate({
        **fields, "source_available": False, "evidence_text": None,
        "source_unavailable_reason": "source_missing",
    })
    assert available.evidence_text == "原始来源"
    assert unavailable.evidence_text is None and unavailable.source_ref == "m:7"
    for changes in (
        {"source_available": True, "evidence_text": None, "source_unavailable_reason": None},
        {"source_available": False, "evidence_text": "", "source_unavailable_reason": "source_missing"},
        {"source_available": False, "evidence_text": None, "source_unavailable_reason": None},
        {"source_type": " ", "source_available": False, "evidence_text": None, "source_unavailable_reason": "source_missing"},
        {"source_ref": "", "source_available": False, "evidence_text": None, "source_unavailable_reason": "source_missing"},
        {"author_kind": "invented", "source_available": False, "evidence_text": None, "source_unavailable_reason": "source_missing"},
        {"context_json": "[]", "source_available": False, "evidence_text": None, "source_unavailable_reason": "source_missing"},
        {"dedupe_key": "", "source_available": False, "evidence_text": None, "source_unavailable_reason": "source_missing"},
        {"source_available": False, "evidence_text": None, "source_unavailable_reason": "invented"},
    ):
        with pytest.raises(ValidationError):
            ConsoleBusinessTaskSignalView.model_validate({**fields, **changes})


@pytest.mark.parametrize("kind", ["task", "attention"])
def test_detail_returns_facts_and_references_before_any_original_read(tmp_path, monkeypatch, kind):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = seed_task(store, title="核实验收", stage="formal", formal_basis="explicit_assignment", business_relevance="relevant")
    with store._immediate_write_transaction() as db:
        signals = [store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref=f"meeting:{i}", evidence_text=f"来源原文{i}",
            dedupe_key=f"meeting:{i}", _db=db,
        ) for i in range(2)]
        for signal_id in signals:
            store.link_business_task_evidence_in_transaction(task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db)
        anchor_id = store.create_business_anchor_in_transaction(anchor_type="company_priority", anchor_ref="acceptance", title="验收", _db=db)
        attention_id = store.create_business_attention_item_in_transaction(stable_key="acceptance", category="watch", title="关注验收", business_area="交付", why_attention="等待验收", current_state="核实中", ceo_action="关注", anchor_id=anchor_id, evidence_signal_id=signals[0], now="2026-10-30T00:00:00Z", _db=db)
        assessment = {"inference": "继续关注", "evidence": [{"signal_id": signals[1], "source_ref": "meeting:1", "source_excerpt": "来源原文1", "source_time": "", "source_link": ""}]}
        update_business_candidate(db, "attention_item", attention_id, {"assessment_json": json.dumps(assessment)})
        store.link_business_attention_task_in_transaction(attention_item_id=attention_id, task_id=task_id, _db=db)
    calls = []

    def resolve(self, binding, **kwargs):
        calls.append(binding.source_ref)
        return AvailableSource(binding, "来源原文" + binding.source_ref.rsplit(":", 1)[1])

    monkeypatch.setattr(RequestSourceReader, "resolve", resolve)
    path = f"/api/console/tasks/items/{task_id}" if kind == "task" else f"/api/console/tasks/attention/{attention_id}"
    with _client(tmp_path) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert calls == []
        detail = response.json()["item"]
        if kind == "task":
            references = [row["signal"] for row in detail["evidence"]]
            assert [row["role"] for row in detail["evidence"]] == ["assignment", "assignment"]
            assert detail["summary"]["title"] == "核实验收"
        else:
            references = detail["evidence_signals"]
            assert detail["assessment"] == assessment
            assert [row["id"] for row in detail["linked_tasks"]] == [task_id]
        assert {row["id"] for row in references} == set(signals)
        assert all(not {"evidence_text", "source_available", "source_unavailable_reason"} & row.keys() for row in references)
        original = client.get(f"/api/console/tasks/signals/{signals[1]}")
        assert original.status_code == 200
        assert original.json()["item"]["evidence_text"] == "来源原文1"
        assert calls == ["meeting:1"]
