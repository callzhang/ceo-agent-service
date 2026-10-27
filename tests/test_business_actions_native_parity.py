"""Current Business Actions selection and genuine deployed-source public parity."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.action_business_definitions import BUSINESS_ACTION_KINDS
from app.action_business_definitions import BUSINESS_NUMERIC_SCOPE
from app.action_business_storage import (
    copy_business_action_date_evidence, insert_business_action,
    upsert_business_action_anchor_link,
)
from app.action_business_definitions import business_action_projection
from app.business_storage import insert_business
from app.email_store import EmailStore
from app.store import AutoReplyStore
from tests.support.business_task_seed import seed_task, seed_task_signal
from tests.support.business_task_seed import project_candidate_snapshot


def _public_business_fixture(store, prefix):
    """Exact synthetic call sequence run against deployed 83c1d7f0 in root's dry run."""
    from app.task_business_resolution import BusinessResolutionService
    from app.project_context_service import ProjectContextService
    from app.wechat.memory import ExtractedMemoryCandidate

    service = BusinessResolutionService(store)
    anchor = service.register_anchor(
        anchor_type="project", anchor_ref="copy-proof-project:" + prefix,
        title="Copy-only " + prefix)
    project = service.register_official_project(
        anchor_id=anchor, registry_source="copy-proof-registry:" + prefix)
    task = seed_task(store, title="Copy-only task " + prefix, stage="candidate")
    signal = seed_task_signal(store,
        source_type="meeting", source_ref="copy-proof-meeting:" + prefix,
        evidence_text="Synthetic local-copy migration proof " + prefix,
        dedupe_key="copy-proof-signal:" + prefix)
    cluster = service.create_cluster(title="Copy-only cluster " + prefix, task_ids=[task])
    candidate = service.propose_project(
        cluster_id=cluster, title="Copy-only candidate " + prefix,
        reason="Synthetic local-copy candidate")
    service.confirm_project_candidate(candidate_id=candidate, project_id=project,
                                      evidence_signal_id=signal)
    assert project_candidate_snapshot(store, candidate).status.value == "confirmed"
    with store.business_task_transaction() as db:
        attention = store.create_business_attention_item_in_transaction(
            stable_key="copy-proof-attention:" + prefix, category="decision",
            title="Copy-only attention " + prefix, business_area="copy-proof",
            why_attention="Synthetic copy verification", current_state="Synthetic local state",
            ceo_action="Verify local-copy migration", anchor_id=anchor,
            evidence_signal_id=signal, assessment_json="{}",
            now="2026-10-17T18:00:00+00:00", _db=db)
        store.link_business_attention_task_in_transaction(
            attention_item_id=attention, task_id=task, _db=db)
        store.replace_business_attention_proposal_tasks_in_transaction(
            attention_item_id=attention, task_ids=(task,), _db=db)
        event = store.append_business_attention_event_in_transaction(
            attention_item_id=attention, event_type="opened", signal_id=signal,
            before_json="{}", after_json="{}", reason="Synthetic local-copy migration",
            _db=db)
        assert store.get_business_attention_item_in_transaction(
            item_id=attention, _db=db).stable_key == "copy-proof-attention:" + prefix
    task2 = seed_task(store, title="Copy-only related task " + prefix,
                                       stage="candidate")
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(project_id=project, context=None,
                                           signal_ids=(signal,), db=db)
        store.link_business_task_evidence_in_transaction(
            task_id=task2, signal_id=signal, evidence_role="assignment", _db=db)
        date = store.create_business_task_date_evidence_in_transaction(
            task_id=task, source_signal_id=signal, date_type="next_check_at",
            value_at="2026-10-20T18:00:00+00:00",
            raw_phrase="Synthetic local-copy next check", actor_kind="human", _db=db)
        assert store.copy_business_task_date_evidence_in_transaction(
            source_task_id=task, target_task_id=task2, _db=db) == 1
        assert store.copy_business_task_date_evidence_in_transaction(
            source_task_id=task, target_task_id=task2, _db=db) == 0
        relation = store.create_business_task_relation_in_transaction(
            from_task_id=task, to_task_id=task2, relation_type="related_to",
            status="proposed", supporting_signal_id=signal,
            reason="Synthetic local-copy relation", _db=db)
        link = store.create_business_task_anchor_link_in_transaction(
            task_id=task2, anchor_id=anchor, status="proposed", active=True,
            evidence_signal_id=signal, reason="Synthetic local-copy anchor", _db=db)
    statement = "Synthetic local-copy memory candidate " + prefix
    proposed = ExtractedMemoryCandidate(
        statement=statement, category="fact", confidence=0.9, sensitivity="normal",
        source_message_ids=["copy-only-message:" + prefix],
        source_conversation_ids=["copy-only-conversation:" + prefix],
        source_time_start="2026-10-17T18:00:00+00:00",
        source_time_end="2026-10-17T18:00:00+00:00", evidence_excerpt=statement,
        cleanup_notes="Synthetic migration fixture")
    wechat = store.add_wechat_memory_candidate(
        import_run_id="copy-only-import:" + prefix,
        account_id="copy-only-account:" + prefix, candidate=proposed)
    assert wechat is not None
    store.review_wechat_memory_candidate(wechat, "approve", reviewer="copy-proof",
                                         final_statement=statement)
    assert store.claim_wechat_memory_candidate_write(wechat)["outcome"] == "claimed"
    store.finish_wechat_memory_candidate_write(
        wechat, status="written", memory_id="copy-only-fake-memory-receipt:" + prefix)
    row = store.get_wechat_memory_candidate(wechat)
    assert row["memory_write_status"] == "written"
    return dict(anchor_id=anchor, project_id=project, task_id=task,
                signal_id=signal, cluster_id=cluster, project_candidate_id=candidate,
                attention_item_id=attention, attention_event_id=event,
                wechat_candidate_id=wechat, fake_memory_receipt=row["memory_id"],
                related_task_id=task2, date_evidence_id=date,
                relation_rowid=relation, anchor_link_id=link)


def test_current_public_business_fixture_matches_genuine_deployed_native_ids(tmp_path):
    path = tmp_path / "public-current.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    actual = _public_business_fixture(store, "native-actions-dry")
    # Recorded from the same synthetic calls against genuine deployed 83c1d7f0.
    assert actual == {
        "anchor_id": 1, "project_id": 1, "task_id": 1, "signal_id": 1,
        "cluster_id": 1, "project_candidate_id": 1, "attention_item_id": 1,
        "attention_event_id": 1, "wechat_candidate_id": 1,
        "fake_memory_receipt": "copy-only-fake-memory-receipt:native-actions-dry",
        "related_task_id": 2, "date_evidence_id": 1,
        "relation_rowid": 1, "anchor_link_id": 2,
    }
    signal = store.get_business_task_signal(actual["signal_id"])
    body = "Synthetic local-copy migration proof native-actions-dry"
    assert signal.source.body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert signal.source.body_utf8_bytes == len(body.encode())
    assert not hasattr(signal, "evidence_text")
    assert signal.source_ref == "copy-proof-meeting:native-actions-dry"
    assert {item.signal_id for item in store.list_business_project_evidence(actual["project_id"])} == {1}
    assert {item.task_id for item in store.list_business_attention_tasks(actual["attention_item_id"])} == {1}
    assert len(store.list_business_task_date_evidence(actual["related_task_id"])) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_selected_source_task_and_attention_lookups_use_selective_indexes(tmp_path):
    """Synthetic 1,000-row selected-current workload, with test-only statistics."""
    path = tmp_path / "selective-current.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store.business_task_transaction() as db:
        anchor = insert_business(db, "anchor", {
            "anchor_type": "project", "anchor_ref": "index-fixture", "title": "Index fixture",
        }).lastrowid
        shared_signal_ids = []
        for number in range(1, 1001):
            insert_business(db, "task", {
                "id": number, "title": f"Index task {number}", "stage": "candidate",
            })
            signal = store.create_business_task_signal_in_transaction(
                source_type="meeting",
                source_ref=("meeting:shared" if number in (777, 778) else f"meeting:{number}"),
                source_time=("2026-10-17T18:00:00+00:00" if number in (777, 778) else ""),
                evidence_text=("Shared source body" if number in (777, 778) else f"Index source {number}"),
                dedupe_key=f"index-signal:{number}",
                _db=db,
            )
            if number in (777, 778):
                shared_signal_ids.append(signal)
            if number == 1:
                attention = store.create_business_attention_item_in_transaction(
                    stable_key="index-fixture", category="decision", title="Index fixture",
                    business_area="fixture", why_attention="Query plan", current_state="Open",
                    ceo_action="Check", anchor_id=anchor, evidence_signal_id=signal,
                    now="2026-10-17T18:00:00+00:00", _db=db,
                )
            insert_business_action(db, "business_task_evidence", {
                "task_id": number, "signal_id": signal, "evidence_role": "discovery",
            })
            insert_business_action(db, "business_attention_tasks", {
                "attention_item_id": attention, "task_id": number,
            })
        db.execute("analyze")
        from app.provenance_storage import signals_for_source
        statements = []
        db.set_trace_callback(statements.append)
        found_signal_ids = signals_for_source(
            db, source_type="meeting", source_ref="meeting:shared",
            source_time="2026-10-17T18:00:00+00:00",
        )
        db.set_trace_callback(None)
        assert set(found_signal_ids) == set(shared_signal_ids)
        source_query = next(sql for sql in statements
                            if sql.lstrip().lower().startswith("select a.business_signal_public_id"))
        plan = " ".join(str(row[3]) for row in db.execute("explain query plan " + source_query))
        assert "idx_provenance_source" in plan
        assert "idx_actions_signal_shared" in plan
        assert "USE TEMP B-TREE" not in plan
        for kind, clause, parameters in (
            ("business_task_evidence", "task_id=?", (777,)),
            ("business_attention_tasks", "task_id=?", (777,)),
        ):
            projection = business_action_projection(kind)
            plan = " ".join(str(row[3]) for row in db.execute(
                f"explain query plan select * from ({projection}) where {clause}", parameters))
            assert f"idx_actions_{kind}_lookup" in plan
            assert "USE TEMP B-TREE" not in plan


def test_fresh_store_email_keeps_business_actions_as_rows_not_tables(tmp_path):
    path = tmp_path / "current.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute(
            "select name from sqlite_schema where type='table' and name not like 'sqlite_%'")}
        assert tables.issuperset({"actions", "business", "tasks", "runs"})
        assert not tables.intersection(BUSINESS_ACTION_KINDS)
        signal_fks = [row for row in db.execute("pragma foreign_key_list(business)")
                      if row[3] in ("attention_item_evidence_signal_id",
                                    "attention_item_resolution_signal_id",
                                    "project_candidate_confirmation_signal_id")]
        assert signal_fks and all(row[2] == "actions" for row in signal_fks)
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_fresh_email_then_store_keeps_business_actions_as_rows_not_tables(tmp_path):
    path = tmp_path / "email-first.sqlite3"
    EmailStore(path)
    AutoReplyStore(path)
    EmailStore(path)
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert not db.execute("select 1 from sqlite_schema where type='table' and name in ("
                              + ",".join("?" for _ in BUSINESS_ACTION_KINDS) + ")",
                              BUSINESS_ACTION_KINDS).fetchall()
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_composite_key_conflict_does_not_suppress_unrelated_constraint(tmp_path):
    path = tmp_path / "component.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store.business_task_transaction() as db:
        signal = store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref="meeting:1", evidence_text="body",
            dedupe_key="signal:1", _db=db,
        )
        assert signal == 1
        task = insert_business(db, "task", {"title": "Task", "stage": "candidate"}).lastrowid
        values = {"task_id": task, "signal_id": signal, "evidence_role": "discovery"}
        first = insert_business_action(
            db, "business_task_evidence", values, on_conflict_key_do_nothing=True)
        second = insert_business_action(
            db, "business_task_evidence", values, on_conflict_key_do_nothing=True)
        assert (first.rowcount, second.rowcount) == (1, 0)
        with pytest.raises(sqlite3.IntegrityError):
            insert_business_action(db, "business_task_evidence", {
                "task_id": task, "signal_id": signal, "evidence_role": "invalid",
            }, on_conflict_key_do_nothing=True)


def test_date_copy_is_one_statement_and_keeps_blob_and_created_at(tmp_path):
    path = tmp_path / "component.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store.business_task_transaction() as db:
        signal = store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref="meeting:1", evidence_text="body",
            dedupe_key="signal:1", _db=db,
        )
        source = insert_business(db, "task", {"title": "Source", "stage": "candidate"}).lastrowid
        target = insert_business(db, "task", {"title": "Target", "stage": "candidate"}).lastrowid
        insert_business_action(db, "business_task_date_evidence", {
            "task_id": source, "date_type": "next_check_at", "value_at": "2026-10-18",
            "raw_phrase": b"Friday", "source_signal_id": signal, "actor_kind": "human",
            "created_at": "2026-10-17 12:00:00",
        })
        assert copy_business_action_date_evidence(db, source_task_id=source,
                                                  target_task_id=target) == 1
        assert copy_business_action_date_evidence(db, source_task_id=source,
                                                  target_task_id=target) == 0
        projection = business_action_projection("business_task_date_evidence")
        rows = [tuple(row) for row in db.execute(
            f"select task_id,raw_phrase,created_at from ({projection}) order by id")]
        assert rows == [(source, b"Friday", "2026-10-17 12:00:00"),
                        (target, b"Friday", "2026-10-17 12:00:00")]


def test_date_copy_keeps_duplicate_source_facts_and_aborts_all_rows_on_late_failure(tmp_path):
    path = tmp_path / "date-copies.sqlite3"
    store = AutoReplyStore(path)
    with store.business_task_transaction() as db:
        signal = store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref="meeting:1", evidence_text="body",
            dedupe_key="signal:1", _db=db,
        )
        source = insert_business(db, "task", {"title": "Source", "stage": "candidate"}).lastrowid
        target = insert_business(db, "task", {"title": "Target", "stage": "candidate"}).lastrowid
        duplicate = {"task_id": source, "date_type": "next_check_at",
                     "raw_phrase": "same", "source_signal_id": signal, "actor_kind": "human",
                     "created_at": "2026-10-17 12:00:00"}
        insert_business_action(db, "business_task_date_evidence", duplicate)
        insert_business_action(db, "business_task_date_evidence", duplicate)
        assert copy_business_action_date_evidence(db, source_task_id=source,
                                                  target_task_id=target) == 2
        assert copy_business_action_date_evidence(db, source_task_id=source,
                                                  target_task_id=target) == 0
        second_target = insert_business(db, "task", {"title": "Second target",
                                                       "stage": "candidate"}).lastrowid
        insert_business_action(db, "business_task_date_evidence", {
            **duplicate, "raw_phrase": "late"})
        db.execute("create trigger abort_late_date_copy before insert on actions "
                   "when new.record_type='business_task_date_evidence' and "
                   "new.ba_task_date_evidence_raw_phrase='late' "
                   "begin select raise(abort,'late date'); end")
        with pytest.raises(sqlite3.IntegrityError, match="late date"):
            copy_business_action_date_evidence(db, source_task_id=source,
                                               target_task_id=second_target)
        projection = business_action_projection("business_task_date_evidence")
        assert db.execute(f"select count(*) from ({projection}) where task_id=?",
                          (second_target,)).fetchone()[0] == 0
        assert db.in_transaction


def test_anchor_link_upsert_keeps_public_id_and_created_at(tmp_path):
    path = tmp_path / "component.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store.business_task_transaction() as db:
        signal = store.create_business_task_signal_in_transaction(
            source_type="meeting", source_ref="meeting:1", evidence_text="body",
            dedupe_key="signal:1", _db=db,
        )
        task = insert_business(db, "task", {"title": "Task", "stage": "candidate"}).lastrowid
        anchor = insert_business(db, "anchor", {"anchor_type": "project",
                                                 "anchor_ref": "anchor:1", "title": "Anchor"}).lastrowid
        first = upsert_business_action_anchor_link(
            db, task_id=task, anchor_id=anchor, status="proposed", active=1,
            evidence_signal_id=signal, reason="initial")
        first_water = db.execute("select seq from sqlite_sequence where name='actions:business_task_anchor_links'").fetchone()[0]
        projection = business_action_projection("business_task_anchor_links")
        original = db.execute(f"select * from ({projection})").fetchone()
        second = upsert_business_action_anchor_link(
            db, task_id=task, anchor_id=anchor, status="confirmed", active=0,
            evidence_signal_id=signal, reason="updated")
        changed = db.execute(f"select * from ({projection})").fetchone()
        second_water = db.execute("select seq from sqlite_sequence where name='actions:business_task_anchor_links'").fetchone()[0]
        assert first == second == original[0]
        assert changed[7] == original[7]  # created_at
        assert changed[3:7] == ("confirmed", 0, signal, "updated")
        assert (first_water, second_water) == (first, first + 1)
        source = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs"
                             / "2026-10-17-business-actions-current25-source.json").read_text())
        declaration = source["declarations"]["business_task_anchor_links"]
        with sqlite3.connect(":memory:") as native:
            for object_type in ("table", "index", "trigger"):
                for item in declaration["objects"]:
                    if item[0] == object_type and item[3]:
                        native.execute(item[3])
            # Exact deployed 83c1d7f0 Store UPSERT SQL, with native source DDL.
            native_upsert = """insert into business_task_anchor_links
               (task_id, anchor_id, status, active, evidence_signal_id, reason)
               values (?, ?, ?, ?, ?, ?)
               on conflict(task_id, anchor_id) do update set
                 status=excluded.status, active=excluded.active,
                 evidence_signal_id=excluded.evidence_signal_id, reason=excluded.reason"""
            native.execute(native_upsert, (task, anchor, "proposed", 1, signal, "initial"))
            native_first = native.execute(
                "select seq from sqlite_sequence where name='business_task_anchor_links'").fetchone()[0]
            native.execute(native_upsert, (task, anchor, "confirmed", 0, signal, "updated"))
            native_second = native.execute(
                "select seq from sqlite_sequence where name='business_task_anchor_links'").fetchone()[0]
            assert native.execute("select id,status,active from business_task_anchor_links").fetchone() == (
                first, "confirmed", 0)
            assert (first_water, second_water) == (native_first, native_second)


def test_selected_signal_and_date_observations_keep_values_and_are_immutable(tmp_path):
    store = AutoReplyStore(tmp_path / "immutable-current.sqlite3")
    task = seed_task(store, title="Immutable observations", stage="candidate")
    signal = seed_task_signal(store,
        source_type="meeting", source_ref="meeting:immutable",
        evidence_text="  Exact source body\n", dedupe_key="signal:immutable",
        context_json='{"source":true}',
    )
    with store.business_task_transaction() as db:
        date = store.create_business_task_date_evidence_in_transaction(
            task_id=task, source_signal_id=signal,
            date_type="next_check_at", value_at="",
            raw_phrase="下周，日期未定", actor_kind="human", _db=db,
        )
    signal_fact = store.get_business_task_signal(signal)
    assert signal_fact.source.body_sha256 == hashlib.sha256("  Exact source body\n".encode()).hexdigest()
    assert signal_fact.source.body_utf8_bytes == len("  Exact source body\n".encode())
    assert not hasattr(signal_fact, "evidence_text")
    assert signal_fact.context_json == '{"source":true}'
    assert store.list_business_task_date_evidence(task_id=task)[0].raw_phrase == "下周，日期未定"
    with store._connect() as db:
        for kind, public_id in (("business_task_signals", signal),
                                ("business_task_date_evidence", date)):
            physical = db.execute(
                "select id from actions where record_type=? and legacy_key=?",
                (kind, str(public_id)),
            ).fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("update actions set payload_json=payload_json where id=?", (physical,))
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("delete from actions where id=?", (physical,))
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_selected_business_public_ids_use_existing_numeric_index(tmp_path):
    """Query the actual current DDL and the allocator's executed SQL."""
    from app.action_business_storage import _next_source_rowid
    from app.action_business_definitions import BUSINESS_ACTION_AUTO

    path = tmp_path / "numeric-index-current.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store._connect() as db:
        assert db.execute("select 1 from sqlite_schema where name='idx_actions_public_id'").fetchone()
        statements = []
        db.set_trace_callback(statements.append)
        assert _next_source_rowid(db, "business_task_signals") == 1
        db.set_trace_callback(None)
        allocator_sql = next(sql for sql in statements if sql.lower().startswith(
            "select max(cast(legacy_key as integer))"))

        def indexed_plan(sql, args=()):
            plan = [row[3] for row in db.execute("explain query plan " + sql, args)]
            # A numeric public-id index on Actions, never a scan or temp sort.
            assert any(("idx_actions_public_id" in detail
                        or "idx_actions_email_public_key" in detail) for detail in plan), plan
            assert not any("USE TEMP B-TREE" in detail for detail in plan), plan

        indexed_plan(allocator_sql)
        for kind in BUSINESS_ACTION_KINDS:
            if kind == "business_task_signals":
                projection = ("select cast(a.legacy_key as integer) as _source_rowid, "
                              "cast(a.legacy_key as integer) as id from actions a "
                              "where a.record_type='business_task_signals' and "
                              + BUSINESS_NUMERIC_SCOPE)
            else:
                projection = business_action_projection(
                    kind, include_rowid=True, public_id_index=True)
            indexed_plan(f"select _source_rowid from ({projection}) where _source_rowid=?", (1,))
            indexed_plan(f"select _source_rowid from ({projection}) order by _source_rowid limit 5")
            if kind in BUSINESS_ACTION_AUTO:
                indexed_plan(f"select id from ({projection}) where id=?", (1,))
                indexed_plan(f"select id from ({projection}) order by id limit 5")
