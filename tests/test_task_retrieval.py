import json

from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.task_semantic_models import BusinessTaskStage
from app.task_retrieval import (
    load_project_task_detail,
    render_candidate_prompt,
    render_project_task_details,
    retrieve_project_candidates,
    retrieve_project_task_details,
    resolve_task_owner_display,
    retrieve_task_semantic_context,
    render_task_semantic_context,
)


def _work_item(summary: str) -> WorkItem:
    return WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "message:42"},
        "summary": summary,
        "context": {"source_conversation_kind": "group"},
    })


def _sourced_formal_task(store: AutoReplyStore, *, title: str, suffix: str) -> int:
    source_ref = f"message:owner:{suffix}"
    excerpt = f"王明负责{title}"
    task_id = store.create_business_task(
        title=title, stage="formal", formal_basis="explicit_assignment",
        commitment_status="assigned_unaccepted", owner_name="王明",
        owner_evidence_json=json.dumps({"source_ref": source_ref, "excerpt": excerpt}, ensure_ascii=False),
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref=source_ref, evidence_text=excerpt,
        dedupe_key=source_ref,
    )
    store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="assignment")
    return task_id


def test_semantic_context_keeps_legacy_ownerless_formal_searchable_but_unverified(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-legacy-owner.sqlite3")
    task_id = store.create_business_task(
        title="美国客户报价", stage="formal", formal_basis="explicit_assignment",
        commitment_status="assigned_unaccepted",
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:legacy", evidence_text="讨论美国客户报价",
        dedupe_key="message:legacy",
    )
    store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="discovery")

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert context.formal_tasks == ()
    assert [task.id for task in context.unverified_formal_tasks] == [task_id]
    assert context.evidence_signals[0].evidence_text == "讨论美国客户报价"
    payload = json.loads(render_task_semantic_context(context))
    assert payload["existing_formal_tasks"] == []
    assert payload["unverified_formal_tasks"][0]["id"] == task_id
    assert "owner" in payload["notice"]


def test_semantic_context_requires_attached_owner_source_not_merely_owner_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-uncited-owner.sqlite3")
    task_id = store.create_business_task(
        title="美国客户报价", stage="formal", formal_basis="explicit_assignment",
        owner_name="王明", owner_evidence_json=json.dumps({
            "source_ref": "message:unlinked", "excerpt": "王明负责美国客户报价",
        }, ensure_ascii=False),
    )
    store.create_business_task_signal(
        source_type="message", source_ref="message:unlinked",
        evidence_text="王明负责美国客户报价", dedupe_key="message:unlinked",
    )

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert context.formal_tasks == ()
    assert [task.id for task in context.unverified_formal_tasks] == [task_id]


def test_semantic_context_requires_formal_role_for_owner_citation(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-owner-role.sqlite3")
    task_id = store.create_business_task(
        title="美国客户报价", stage="formal", formal_basis="explicit_assignment",
        owner_name="王明", owner_evidence_json=json.dumps({
            "source_ref": "message:discovery", "excerpt": "王明负责美国客户报价",
        }, ensure_ascii=False),
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:discovery",
        evidence_text="王明负责美国客户报价", dedupe_key="message:discovery",
    )
    store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="discovery")

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert context.formal_tasks == ()
    assert [task.id for task in context.unverified_formal_tasks] == [task_id]


def test_semantic_context_pins_middle_owner_citation_after_evidence_bounding(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-middle-owner.sqlite3")
    task_id = store.create_business_task(
        title="美国客户报价", stage="formal", formal_basis="explicit_assignment",
        owner_name="王明", owner_evidence_json=json.dumps({
            "source_ref": "message:middle", "excerpt": "王明负责美国客户报价",
        }, ensure_ascii=False),
    )
    owner_signal_id = 0
    for index in range(7):
        source_ref = "message:middle" if index == 3 else f"message:other:{index}"
        signal_id = store.create_business_task_signal(
            source_type="message", source_ref=source_ref,
            evidence_text="王明负责美国客户报价" if index == 3 else f"报价进度 {index}",
            dedupe_key=source_ref,
        )
        store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="assignment")
        store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="correction")
        if index == 3:
            owner_signal_id = signal_id

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert [task.id for task in context.formal_tasks] == [task_id]
    assert {(row.signal_id, row.evidence_role.value) for row in context.task_evidence} >= {
        (owner_signal_id, "assignment"), (owner_signal_id, "correction"),
    }
    assert owner_signal_id in {signal.id for signal in context.evidence_signals}
    assert "王明负责美国客户报价" in render_task_semantic_context(context)


def test_semantic_context_prioritizes_confirmed_active_link_over_early_proposals(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-confirmed-link.sqlite3")
    resolver = BusinessResolutionService(store)
    task_id = _sourced_formal_task(store, title="美国客户报价", suffix="confirmed")
    for index in range(3):
        anchor_id = resolver.register_anchor(
            anchor_type="project", anchor_ref=f"registry:proposal:{index}",
            title=f"美国客户报价草案 {index}",
        )
        signal_id = store.create_business_task_signal(
            source_type="message", source_ref=f"message:proposal:{index}",
            evidence_text=f"可能属于草案 {index}", dedupe_key=f"message:proposal:{index}",
        )
        resolver.propose_anchor_match(task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id)
    confirmed_anchor_id = resolver.register_anchor(
        anchor_type="project", anchor_ref="registry:confirmed", title="全球营收计划",
    )
    official_project_id = resolver.register_official_project(
        anchor_id=confirmed_anchor_id, registry_source="portfolio",
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:confirmed",
        evidence_text="美国客户报价正式归属全球营收计划", dedupe_key="message:confirmed",
    )
    resolver.confirm_anchor_match(
        task_id=task_id, anchor_id=confirmed_anchor_id, evidence_signal_id=signal_id,
    )

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert [(link.anchor_id, link.status.value, link.active) for link in context.task_anchor_links] == [
        (confirmed_anchor_id, "confirmed", True),
    ]
    assert confirmed_anchor_id in {anchor.id for anchor in context.anchors}
    assert official_project_id in {project.id for project in context.official_projects}
    assert signal_id in {signal.id for signal in context.evidence_signals}


def test_semantic_context_relation_and_anchor_support_signals_are_rendered_per_task(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-support-signals.sqlite3")
    resolver = BusinessResolutionService(store)
    task_ids = [_sourced_formal_task(store, title="美国客户报价", suffix=str(index)) for index in range(2)]
    support_ids = []
    for index, task_id in enumerate(task_ids):
        target_id = store.create_business_task(title=f"支持事项 {index}", stage="candidate")
        anchor_id = resolver.register_anchor(
            anchor_type="customer", anchor_ref=f"customer:{index}", title=f"客户锚点 {index}",
        )
        signal_id = store.create_business_task_signal(
            source_type="message", source_ref=f"message:support:{index}",
            evidence_text=f"报价 {index} 属于客户锚点并支持签约", dedupe_key=f"message:support:{index}",
        )
        support_ids.append(signal_id)
        resolver.add_relation(
            from_task_id=task_id, to_task_id=target_id, relation_type="supports",
            evidence_signal_id=signal_id,
        )
        resolver.propose_anchor_match(task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id)

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=2)
    assert len(context.task_relations) == 2
    assert len(context.task_anchor_links) == 2
    assert set(support_ids) <= {signal.id for signal in context.evidence_signals}
    assert all(signal_id not in {row.signal_id for row in context.task_evidence} for signal_id in support_ids)
    rendered = render_task_semantic_context(context)
    assert "报价 0 属于客户锚点并支持签约" in rendered
    assert "报价 1 属于客户锚点并支持签约" in rendered


def test_semantic_context_per_task_relation_and_link_cap_preserves_later_task(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-per-task-cap.sqlite3")
    resolver = BusinessResolutionService(store)
    task_ids = [_sourced_formal_task(store, title="美国客户报价", suffix=str(index)) for index in range(2)]
    for index, task_id in enumerate(task_ids):
        for edge_index in range(2):
            target_id = store.create_business_task(title=f"目标 {index}-{edge_index}", stage="candidate")
            anchor_id = resolver.register_anchor(
                anchor_type="customer", anchor_ref=f"cust:{index}:{edge_index}",
                title=f"客户 {index}-{edge_index}",
            )
            ref = f"message:edge:{index}:{edge_index}"
            signal_id = store.create_business_task_signal(
                source_type="message", source_ref=ref,
                evidence_text=f"关系和锚点 {index}-{edge_index}", dedupe_key=ref,
            )
            resolver.add_relation(
                from_task_id=task_id, to_task_id=target_id, relation_type="supports",
                evidence_signal_id=signal_id,
            )
            resolver.propose_anchor_match(task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id)

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=2)
    assert len(context.task_relations) == 4
    assert len(context.task_anchor_links) == 4
    assert {row.from_task_id for row in context.task_relations} == set(task_ids)
    assert {row.task_id for row in context.task_anchor_links} == set(task_ids)


def test_semantic_context_includes_linked_anchor_and_official_project_past_lexical_cap(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-linked-project.sqlite3")
    resolver = BusinessResolutionService(store)
    task_id = _sourced_formal_task(store, title="美国客户报价", suffix="linked")
    decoy_anchor_id = resolver.register_anchor(
        anchor_type="project", anchor_ref="registry:decoy", title="美国客户报价档案",
    )
    decoy_project_id = resolver.register_official_project(
        anchor_id=decoy_anchor_id, registry_source="portfolio",
    )
    linked_anchor_id = resolver.register_anchor(
        anchor_type="project", anchor_ref="registry:linked", title="全球营收计划",
    )
    linked_project_id = resolver.register_official_project(
        anchor_id=linked_anchor_id, registry_source="portfolio",
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:project-link",
        evidence_text="美国客户报价归属全球营收计划", dedupe_key="message:project-link",
    )
    resolver.propose_anchor_match(task_id=task_id, anchor_id=linked_anchor_id, evidence_signal_id=signal_id)

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert {row.id for row in context.anchors} >= {linked_anchor_id}
    assert {row.id for row in context.official_projects} >= {linked_project_id}
    assert decoy_project_id not in {row.id for row in context.official_projects} or len(context.official_projects) > 1


def test_semantic_context_includes_cluster_members_not_only_cluster_title(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-cluster-members.sqlite3")
    resolver = BusinessResolutionService(store)
    matched_id = _sourced_formal_task(store, title="美国客户报价", suffix="cluster")
    sibling_id = store.create_business_task(title="美国客户合同准备", stage="candidate")
    decoy_id = resolver.create_cluster(title="美国客户报价研究", task_ids=[sibling_id])
    linked_id = resolver.create_cluster(title="海外营收", task_ids=[matched_id, sibling_id])

    page = store.list_business_work_cluster_tasks(cluster_id=linked_id, limit=1, offset=1)
    assert [(row.cluster_id, row.task_id) for row in page] == [(linked_id, sibling_id)]
    assert [row.cluster_id for row in store.list_business_work_cluster_tasks(task_id=matched_id)] == [linked_id]

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=1)
    assert linked_id in {cluster.id for cluster in context.clusters}
    assert {(row.cluster_id, row.task_id) for row in context.cluster_memberships} >= {
        (linked_id, matched_id), (linked_id, sibling_id),
    }
    assert decoy_id not in {cluster.id for cluster in context.clusters} or len(context.clusters) > 1
    assert "cluster_memberships" in render_task_semantic_context(context)


def test_semantic_context_includes_old_formal_task_and_evidence_beyond_recent_window(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-retrieval.sqlite3")
    matched = store.create_business_task(title="美国客户报价第一版", stage=BusinessTaskStage.FORMAL,
                                         formal_basis="explicit_assignment", commitment_status="assigned_unaccepted",
                                         owner_name="王明", owner_evidence_json=json.dumps({
                                             "source_ref": "message:old", "excerpt": "王明负责美国客户报价第一版",
                                         }, ensure_ascii=False))
    signal_id = store.create_business_task_signal(source_type="message", source_ref="message:old",
                                                  evidence_text="王明负责美国客户报价第一版",
                                                  dedupe_key="message:old")
    store.link_business_task_evidence(task_id=matched, signal_id=signal_id, evidence_role="assignment")
    for index in range(501):
        store.create_business_task(title=f"其他工作 {index}", stage=BusinessTaskStage.CANDIDATE)

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价第一版进展"), limit_per_kind=2)
    assert [task.id for task in context.formal_tasks] == [matched]
    assert [(row.task_id, row.signal_id) for row in context.task_evidence] == [(matched, signal_id)]
    assert len(context.task_candidates) <= 2
    rendered = render_task_semantic_context(context)
    assert "rank is context only" in rendered
    assert "never authority or confirmation" in rendered
    assert "王明负责美国客户报价第一版" in rendered


def test_semantic_context_keeps_early_assignment_and_acceptance_after_many_later_signals(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-source-proof.sqlite3")
    task_id = store.create_business_task(
        title="美国客户报价第一版", stage=BusinessTaskStage.FORMAL,
        formal_basis="explicit_assignment", commitment_status="accepted",
        owner_name="王明", owner_evidence_json=json.dumps({
            "source_ref": "message:assign", "excerpt": "负责人指派王明交报价第一版",
        }, ensure_ascii=False),
    )
    source_signal_ids = []
    for role, ref, excerpt in (
        ("assignment", "message:assign", "负责人指派王明交报价第一版"),
        ("acceptance", "message:accept", "王明答应交报价第一版"),
    ):
        signal_id = store.create_business_task_signal(
            source_type="message", source_ref=ref, evidence_text=excerpt, dedupe_key=ref,
        )
        store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role=role)
        source_signal_ids.append(signal_id)
    for index in range(12):
        ref = f"message:progress:{index}"
        signal_id = store.create_business_task_signal(
            source_type="message", source_ref=ref, evidence_text=f"报价进度 {index}", dedupe_key=ref,
        )
        store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="discovery")

    context = retrieve_task_semantic_context(
        store, _work_item("美国客户报价第一版进度"), limit_per_kind=2,
    )
    assert [task.id for task in context.formal_tasks] == [task_id]
    assert set(source_signal_ids) <= {row.signal_id for row in context.task_evidence}
    assert len(context.task_evidence) <= 20
    rendered = render_task_semantic_context(context)
    assert "负责人指派王明交报价第一版" in rendered
    assert "王明答应交报价第一版" in rendered


def test_semantic_context_includes_registry_clusters_and_anchor_links(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-relations.sqlite3")
    resolver = BusinessResolutionService(store)
    task_id = store.create_business_task(title="美国客户报价", stage="formal",
                                         formal_basis="explicit_assignment", commitment_status="assigned_unaccepted",
                                         owner_name="王明", owner_evidence_json=json.dumps({
                                             "source_ref": "message:assign", "excerpt": "王明负责美国客户报价",
                                         }, ensure_ascii=False))
    assignment_id = store.create_business_task_signal(
        source_type="message", source_ref="message:assign", evidence_text="王明负责美国客户报价",
        dedupe_key="message:assign",
    )
    store.link_business_task_evidence(task_id=task_id, signal_id=assignment_id, evidence_role="assignment")
    signal_id = store.create_business_task_signal(source_type="message", source_ref="message:anchor",
                                                  evidence_text="美国客户报价属于美国客户成交",
                                                  dedupe_key="message:anchor")
    cluster_id = resolver.create_cluster(title="美国客户成交", task_ids=[task_id])
    anchor_id = resolver.register_anchor(anchor_type="project", anchor_ref="registry:us",
                                         title="美国客户成交")
    project_id = resolver.register_official_project(anchor_id=anchor_id, registry_source="portfolio")
    resolver.propose_anchor_match(task_id=task_id, anchor_id=anchor_id, evidence_signal_id=signal_id)
    related_id = store.create_business_task(title="签约准备", stage="formal",
                                            formal_basis="explicit_assignment", commitment_status="assigned_unaccepted")
    resolver.add_relation(from_task_id=task_id, to_task_id=related_id,
                          relation_type="supports", evidence_signal_id=signal_id)

    context = retrieve_task_semantic_context(store, _work_item("美国客户报价"), limit_per_kind=2)
    assert [cluster.id for cluster in context.clusters] == [cluster_id]
    assert [anchor.id for anchor in context.anchors] == [anchor_id]
    assert [project.id for project in context.official_projects] == [project_id]
    assert [(link.task_id, link.anchor_id) for link in context.task_anchor_links] == [(task_id, anchor_id)]
    assert [(relation.from_task_id, relation.to_task_id) for relation in context.task_relations] == [(task_id, related_id)]
    assert "proposed" in render_task_semantic_context(context)


def test_semantic_context_can_find_existing_formal_task_from_source_conversation(tmp_path):
    store = AutoReplyStore(tmp_path / "semantic-conversation.sqlite3")
    task_id = store.create_business_task(title="客户交付方案", stage="formal",
                                         formal_basis="explicit_assignment", commitment_status="assigned_unaccepted",
                                         owner_name="王明", owner_evidence_json=json.dumps({
                                             "source_ref": "message:old", "excerpt": "王明负责客户交付方案",
                                         }, ensure_ascii=False))
    signal_id = store.create_business_task_signal(source_type="message", source_ref="message:old",
                                                  conversation_id="cid-1", evidence_text="王明负责客户交付方案",
                                                  dedupe_key="message:old")
    store.link_business_task_evidence(task_id=task_id, signal_id=signal_id, evidence_role="assignment")
    item = _work_item("收到，我负责推进")
    item.source.conversation_id = "cid-1"

    context = retrieve_task_semantic_context(store, item, limit_per_kind=1)
    assert [task.id for task in context.formal_tasks] == [task_id]
    assert context.evidence_signals[0].evidence_text == "王明负责客户交付方案"


def test_semantic_store_read_methods_use_stable_pages(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "semantic-pages.sqlite3")
    resolver = BusinessResolutionService(store)
    task_id = store.create_business_task(title="任务", stage="candidate")
    cluster_ids = [resolver.create_cluster(title=f"业务组 {index}", task_ids=[task_id])
                   for index in range(3)]
    anchor_ids = [resolver.register_anchor(anchor_type="customer", anchor_ref=f"cust:{index}",
                                           title=f"客户 {index}") for index in range(3)]

    assert [row.id for row in store.list_business_work_clusters(limit=1, offset=1)] == [cluster_ids[1]]
    assert [row.id for row in store.list_business_anchors(limit=1, offset=2)] == [anchor_ids[2]]


def test_retrieve_project_candidates_uses_summary_and_project_name(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    sales_project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="复用售前材料和来源链接。",
        facts_json=json.dumps(
            [{"description": "材料放在 business/售前知识库", "source": "memory"}],
            ensure_ascii=False,
        ),
        current_state="正在整理",
    )
    store.create_work_project(
        title="招聘复盘",
        category="recruiting",
        tags_json=json.dumps(["招聘"], ensure_ascii=False),
        status="active",
        priority="P2",
        risk_level="low",
        background="候选人流程复盘。",
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前材料来源链接需要 owner 补齐",
        project_name="售前知识库",
        limit=3,
    )

    assert candidates[0].project.id == sales_project_id
    assert "business/售前知识库" in candidates[0].document
    assert candidates[0].score > 0


def test_retrieve_project_candidates_prioritizes_structured_project_id(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    authoritative_project_id = store.create_work_project(
        title="实际项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        background="与文本检索词不相似。",
    )
    for index in range(5):
        store.create_work_project(
            title=f"项目闭环 {index}",
            category="projects",
            status="active",
            priority="P2",
            risk_level="low",
            background="项目闭环相关的文本候选。",
        )

    summary = json.dumps(
        {
            "project": {"id": authoritative_project_id},
            "todo": {"title": "推进项目闭环"},
        },
        ensure_ascii=False,
    )

    candidates = retrieve_project_candidates(
        store,
        summary=summary,
        project_name="项目闭环",
        limit=1,
    )

    assert candidates[0].project.id == authoritative_project_id


def test_render_candidate_prompt_returns_project_context_json(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    memory_context = {
        "query": "售前知识库",
        "summary": "已确认的售前知识库背景。",
        "memories": [{"source": "memory_recall", "text": "稳定背景"}],
    }
    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        owner_name="Alex",
        goal="沉淀可复用售前材料",
        background="复用售前材料和来源链接。",
        facts_json=json.dumps(
            [{"description": "材料放在 business/售前知识库", "source": "memory"}],
            ensure_ascii=False,
        ),
        memory_context_json=json.dumps(memory_context, ensure_ascii=False),
        source_conversations_json=json.dumps(
            [{"id": "cid-1", "title": "售前项目群"}],
            ensure_ascii=False,
        ),
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前材料来源链接需要 owner 补齐",
        project_name="售前知识库",
    )

    payload = json.loads(render_candidate_prompt(candidates))
    assert payload[0]["category"] == "sales"
    assert payload[0]["title"] == "售前知识库建设"
    assert payload[0]["facts"][0]["source"] == "memory"
    assert payload[0]["memory_context"] == memory_context
    assert payload[0]["source_conversations"][0]["id"] == "cid-1"


def test_retrieve_project_candidates_excludes_archived_and_done_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_work_project(
        title="售前知识库归档",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="archived",
        priority="P1",
        risk_level="low",
        background="归档项目。",
    )
    store.create_work_project(
        title="售前知识库完成",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="done",
        priority="P1",
        risk_level="low",
        background="完成项目。",
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前知识库",
        project_name="售前",
    )

    assert candidates == []


def test_retrieve_project_candidates_searches_all_active_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    target_id = store.create_work_project(
        title="极光协议客户迁移",
        category="projects",
        status="active",
    )
    for index in range(600):
        store.create_work_project(
            title=f"普通项目 {index}",
            category="projects",
            status="active",
        )

    candidates = retrieve_project_candidates(
        store,
        summary="极光协议",
        limit=3,
    )

    assert candidates[0].project.id == target_id


def test_retrieve_project_candidates_returns_empty_for_empty_query_or_no_projects(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    assert retrieve_project_candidates(store, summary="", project_name="") == []

    store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="low",
        background="复用售前材料。",
    )

    assert retrieve_project_candidates(store, summary="", project_name="") == []
    assert (
        retrieve_project_candidates(
            store,
            summary="售前知识库",
            project_name="",
            limit=0,
        )
        == []
    )


def test_resolve_task_owner_display_summarizes_multiple_todo_owners(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="多 owner 项目")
    store.create_work_todo(
        project_id=project_id,
        title="任务 A",
        owner_name="孙伟",
        owner_user_id="owner-1",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 B",
        owner_name="张晓民",
        owner_user_id="owner-2",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 C",
        owner_name="Avery",
        owner_user_id="owner-3",
        status="open",
    )
    store.create_work_todo(
        project_id=project_id,
        title="任务 D",
        owner_name="ET",
        owner_user_id="owner-4",
        status="open",
    )

    project = store.get_work_project(project_id)
    todos = store.list_work_todos(project_id=project_id)

    assert project is not None
    assert resolve_task_owner_display(project, todos) == "多人：孙伟、张晓民、Avery 等 4 人"


def test_retrieve_project_task_details_expands_group_matched_project(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="技术部招聘",
        category="recruiting",
        status="active",
        priority="P1",
        risk_level="medium",
        owner_user_id="hr-owner",
        owner_name="Avery",
        goal="招聘关键技术岗位",
        background="技术部候选人推进。",
        current_state="候选人评估中",
        next_step="确认售前解决方案候选人复试结论",
        source_conversations_json=json.dumps(
            [{"id": "cid-hiring", "title": "技术部招聘群"}],
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="评估 Colin 售前解决方案候选人",
        description="确认技术面、售前方案能力、薪资预期和下一轮安排。",
        owner_user_id="hr-owner",
        owner_name="Avery",
        priority="P1",
        deadline_at="2026-07-25 18:00:00",
        next_follow_up_at="2026-07-24 15:00:00",
        follow_up_question="Colin 的复试结论和下一步安排定了吗？",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="hr-owner",
        owner_name="Avery",
        target_conversation_id="cid-hiring",
        target_kind="group",
        question_text="Colin 的复试结论和下一步安排定了吗？",
        scheduled_at="2026-07-24 15:00:00",
    )
    store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="123",
        summary="新增 Colin 候选人评估 TODO",
        changes_json=json.dumps({"todo_id": todo_id}, ensure_ascii=False),
        confidence=0.91,
    )

    details = retrieve_project_task_details(
        store,
        query="这个任务现在是什么状态？",
        conversation_id="cid-hiring",
        owner_user_id="hr-owner",
    )
    rendered = render_project_task_details(details)
    payload = json.loads(rendered)

    assert payload[0]["project"]["id"] == project_id
    assert "source_conversation_match" in payload[0]["match"]["reasons"]
    assert payload[0]["todos"][0]["id"] == todo_id
    assert payload[0]["todos"][0]["deadline_at"] == "2026-07-25 18:00:00"
    assert payload[0]["todos"][0]["follow_ups"][0]["id"] == follow_up_id
    assert payload[0]["recent_updates"][0]["summary"] == "新增 Colin 候选人评估 TODO"


def test_load_project_task_detail_uses_batch_follow_up_lookup(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="批量读取 follow-up",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_one = store.create_work_todo(
        project_id=project_id,
        title="TODO A",
        status="open",
        priority="P1",
    )
    todo_two = store.create_work_todo(
        project_id=project_id,
        title="TODO B",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_one,
        target_conversation_id="cid-a",
        target_kind="group",
        question_text="A?",
        scheduled_at="2026-07-24 10:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_two,
        target_conversation_id="cid-b",
        target_kind="group",
        question_text="B?",
        scheduled_at="2026-07-24 11:00:00",
    )

    batch_calls = {"count": 0}
    original_batch = store.list_follow_up_drafts_for_todos

    def wrapped_batch(todo_ids, *, statuses=None):
        batch_calls["count"] += 1
        return original_batch(todo_ids, statuses=statuses)

    def forbidden_follow_up_lookup(*args, **kwargs):
        raise AssertionError("load_project_task_detail should use batch follow-up lookup")

    store.list_follow_up_drafts_for_todos = wrapped_batch  # type: ignore[method-assign]
    store.list_follow_up_drafts = forbidden_follow_up_lookup  # type: ignore[method-assign]

    detail = load_project_task_detail(store, project_id)

    assert detail is not None
    assert batch_calls["count"] == 1
    assert [todo.id for todo in detail.todos] == [todo_one, todo_two]
    assert detail.follow_ups_by_todo[todo_one][0].question_text == "A?"
    assert detail.follow_ups_by_todo[todo_two][0].question_text == "B?"


def test_render_project_task_details_uses_todo_owner_as_project_display_fallback(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
        source_conversations_json=json.dumps(
            [{"id": "cid-delivery", "title": "客户交付群"}],
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Avery",
        status="open",
        priority="P1",
    )

    details = retrieve_project_task_details(
        store,
        query="客户验收 ETA",
        conversation_id="cid-delivery",
    )
    payload = json.loads(render_project_task_details(details))

    assert payload[0]["project"]["id"] == project_id
    assert payload[0]["project"]["owner"] == "Avery"
    assert payload[0]["todos"][0]["id"] == todo_id
    assert payload[0]["todos"][0]["owner"] == "Avery"
