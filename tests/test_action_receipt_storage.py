"""Historical TEXT target must retain independently measured receipt contracts.

The ``target_db`` fixture scopes the TEXT DML adapter to these migration-boundary
tests; normal/final Store fixtures remain on the production writer.
"""

from contextlib import closing

import pytest

from app import action_storage as storage
from tests.test_action_receipt_source import RECEIPT_TYPES, source_values
from tests.test_action_receipt_source import SOURCE as RECEIPT_SOURCE, insert_source
from tests.test_action_storage import source_database


def source15_database(path, row_factory=None, *, fresh=False):
    import json
    from pathlib import Path
    fresh_source = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-13-database-redesign-phase5b-fresh-source.json").read_text())["tables"]
    db = source_database(path, row_factory)
    db.execute("drop table external_action_results")
    for kind in RECEIPT_TYPES:
        objects = fresh_source[kind]["objects"] if fresh else RECEIPT_SOURCE[kind]["objects"]
        db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
        for obj in objects:
            if obj["type"] != "table" and obj["sql"]:
                db.execute(obj["sql"])
    for key in ("1", "01", "A", "a"):
        insert_source(db, "external_action_results", source_values("external_action_results", token=key))
    for kind, watermark in (("agent_effect_intents", 560), ("agent_execution_receipts", 115)):
        insert_source(db, kind, {**source_values(kind, token="deleted"), "id": watermark})
        db.execute(f"delete from {kind}")
        insert_source(db, kind, {**source_values(kind), "id": 7})
    db.commit()
    db.execute("begin immediate")
    storage.migrate_action_schema(db)
    db.commit()
    return db


def receipt_snapshot(db, *, target=False):
    return {kind: [tuple(row) for row in db.execute(
        f"select * from ({storage.ACTION_PROJECTIONS[kind]}) order by {storage.IDENTITY_FIELDS[kind]}"
        if target else f"select {','.join(storage.SOURCE_COLUMNS[kind])} from {kind} order by {storage.IDENTITY_FIELDS[kind]}"
    )] for kind in RECEIPT_TYPES}


def six_snapshot(db):
    return {kind: [tuple(row) for row in db.execute(storage.ACTION_PROJECTIONS[kind])]
            for kind in storage.CANDIDATE_ACTION_TYPES}


@pytest.mark.parametrize("mutation", ["view", "inbound", "column", "null-key", "blob", "enum", "wrong-run"])
def test_unsupported_source15_is_refused_before_mutation(tmp_path, mutation):
    from app.action_receipt_storage import migrate_action_receipt_schema
    with closing(source15_database(tmp_path / "refusal.db")) as db:
        if mutation == "view":
            db.execute('create view observed as select * from "EXTERNAL_ACTION_RESULTS"')
        elif mutation == "inbound":
            db.execute('create table child(id integer,foreign key(id) references "AGENT_EFFECT_INTENTS"(id))')
        elif mutation == "column":
            db.execute("alter table agent_effect_intents add column invented text")
        elif mutation == "null-key":
            insert_source(db, "external_action_results", source_values("external_action_results", token=None))
        elif mutation == "blob":
            db.execute("update external_action_results set provider_result_json=x'00ff'")
        elif mutation == "enum":
            db.execute("pragma ignore_check_constraints=on")
            db.execute("update agent_effect_intents set state='invalid-source-state'")
            db.execute("pragma ignore_check_constraints=off")
        else:
            trigger = next(obj for obj in RECEIPT_SOURCE['agent_effect_intents']['objects']
                           if obj['name'] == 'trg_runs_child_agent_effect_intents_agent_run_id_update')
            db.execute(f"drop trigger {trigger['name']}")
            db.execute("update agent_effect_intents set agent_run_id=6")
            db.execute(trigger['sql'])
        db.commit()
        db.execute("begin immediate")
        before = tuple(db.iterdump())
        with pytest.raises(ValueError):
            migrate_action_receipt_schema(db)
        assert tuple(db.iterdump()) == before


