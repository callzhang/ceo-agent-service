from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService


def test_candidate_confirmation_preserves_existing_link_proof_and_confirms_peers(tmp_path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    service = BusinessResolutionService(store)
    anchor = service.register_anchor(anchor_type="project", anchor_ref="p:delivery", title="交付项目")
    project = service.register_official_project(anchor_id=anchor, registry_source="meeting:delivery")
    tasks = [store.create_business_task(title=title, stage="candidate") for title in ("验收", "付款")]
    first_source = store.create_business_task_signal(source_type="meeting", source_ref="meeting:delivery", evidence_text="验收事项属于交付项目。", dedupe_key="meeting:delivery")
    service.confirm_anchor_match(task_id=tasks[0], anchor_id=anchor, evidence_signal_id=first_source)
    original = store.list_business_task_anchor_links(task_id=tasks[0])[0]
    cluster = service.create_cluster(title="交付项目", task_ids=tasks)
    candidate = service.propose_project(cluster_id=cluster, title="交付项目", reason="原始任务的项目线索")
    report_source = store.create_business_task_signal(source_type="project_weekly_report", source_ref="report:delivery", evidence_text="正式项目清单明确交付项目。", dedupe_key="report:delivery")
    service.confirm_project_candidate(candidate_id=candidate, project_id=project, evidence_signal_id=report_source)
    assert store.list_business_task_anchor_links(task_id=tasks[0])[0] == original
    peer = store.list_business_task_anchor_links(task_id=tasks[1])[0]
    assert peer.anchor_id == anchor and peer.evidence_signal_id == report_source
    assert peer.status.value == "confirmed" and peer.active
    assert report_source in {link.signal_id for link in store.list_business_task_evidence(tasks[0])}
    with store._connect() as db:
        before = [tuple(row) for row in db.execute("select * from business_task_events order by id")]
    service.confirm_project_candidate(candidate_id=candidate, project_id=project, evidence_signal_id=report_source)
    with store._connect() as db:
        assert before == [tuple(row) for row in db.execute("select * from business_task_events order by id")]
