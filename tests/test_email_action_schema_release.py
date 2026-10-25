"""Atomic paired primitive with sentinel markers; real callback tests live in callers."""

from contextlib import closing
from pathlib import Path
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from app import action_email_schema_release as release_module
from app import action_email_storage as email_storage
from app import action_storage as storage
from app import business_schema_release as business_module
from app.business_schema_release import _recreate_source, capture_business_source
from app.business_candidate_definitions import SOURCE_PROFILES as BUSINESS_CANDIDATE_SOURCE_PROFILES
from app.action_error_storage import SOURCE_TABLE_SQL as ERROR_SOURCE_SQL, SOURCE_INDEX_SQL as ERROR_INDEX_SQL
from app.action_feedback_storage import SOURCE_SQL as FEEDBACK_SOURCE_SQL, SOURCE_INDEX_SQL as FEEDBACK_INDEX_SQL, SOURCE_TRIGGER_SQL as FEEDBACK_TRIGGER_SQL
from app.action_feedback_definitions import FEEDBACK_KINDS
from app.confirmation_schema_release import capture_confirmation_source
from app.email_current_schema import (
    create_current_email_migration_ledger,
    create_current_email_non_action_schema,
)
from tests.test_email_action_storage import PARENTS, CHILDREN, _cases, _source_tables
from tests.test_business_core_source import CAPTURE as BUSINESS_CAPTURE
from tests.test_provider_state_source import declarations as provider_declarations
from app.provider_state_schema_release import capture_provider_state_source
from app.state_schema_release import (SOURCE_TABLES as STATE_SOURCE_TABLES,
    capture_state_source)
from app.state_definitions import state_projection
from app.deferral_receipt_storage import capture_native_receipts
from app.task20_schema_release import capture_task20_source
from app.runs11_schema_release import capture_run11_source
from app.run_storage import create_run_schema
from app.configs_schema_transfer import SOURCE_TABLES as CONFIG_SOURCE_TABLES
from tests.configs_source_fixtures import capture as config_capture
from tests.test_configs_schema_release import install_observations


# Deliberate test markers, not allocated deployment versions.
TARGET_STORE = "unselected-email13-protocol-test"
TARGET_EMAIL = 1000051
ORIGINAL_STORE = ("2026-10-13.5", " original\x00store timestamp ")
ORIGINAL_LEDGER = ((1, " first\x00migration "), (51, " final\x00migration "))
ROOT = Path(__file__).resolve().parents[1]
BUSINESS_FRESH_DECLARATIONS = (
    Path(__file__).parent / "fixtures/business_four_kind_fresh_declarations.json"
)
BUSINESS_WORK_PROJECTS_DECLARATIONS = (
    Path(__file__).parent / "fixtures/business_work_projects_source_declarations.json"
)
TASK20_PAIRED_SOURCE = Path(__file__).parent / "fixtures/task20_missing_paired_source_declarations.json"
FOLLOWUP_SYNC_SOURCE = Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json"
TASK_DELIVERY_SOURCE = ROOT / "docs/superpowers/specs/2026-10-17-task-delivery-source-metadata.json"


def _install_later_action_sources(db, variant):
    frozen = json.loads(TASK_DELIVERY_SOURCE.read_text())[variant]
    objects = [item for profile in frozen.values() for item in profile["objects"]]
    for kind in ("table", "index", "trigger"):
        for object_type, _name, _table, sql in objects:
            if object_type == kind and sql:
                db.execute(sql)
    db.execute(ERROR_SOURCE_SQL[0 if variant == "actual" else 1])
    for sql in ERROR_INDEX_SQL:
        db.execute(sql)
    for name in FEEDBACK_KINDS:
        if db.execute("select 1 from sqlite_schema where type='table' and name=?", (name,)).fetchone() is None:
            db.execute(FEEDBACK_SOURCE_SQL[variant][name])
        for sql in FEEDBACK_INDEX_SQL[name]:
            index_name = sql.split(" INDEX ", 1)[1].split(" ", 1)[0].split("\n", 1)[0]
            if db.execute("select 1 from sqlite_schema where type='index' and name=?", (index_name,)).fetchone() is None:
                db.execute(sql)
    for sql in FEEDBACK_TRIGGER_SQL:
        trigger_name = sql.split(" TRIGGER ", 1)[1].split("\n", 1)[0]
        if db.execute("select 1 from sqlite_schema where type='trigger' and name=?", (trigger_name,)).fetchone() is None:
            db.execute(sql)


def _install_followup_sync_sources(db, variant):
    frozen = json.loads(FOLLOWUP_SYNC_SOURCE.read_text())
    source = (frozen["actual"] if variant == "actual" else
              frozen["fresh_production_constructor"]["snapshot"])
    # These six sources are outside Business's retained child family. The
    # other four typed children are installed by _recreate_source above.
    for name in (
        "work_updates", "follow_up_drafts", "follow_up_send_attempts",
        "business_task_follow_up_send_attempts", "task_todo_sync_outbox",
        "work_todo_dingtalk_links",
    ):
        definition = source["tables"][name]
        db.execute(definition["sql"])
        for index in reversed(definition["indexes"]):
            if index["sql"] is not None:
                db.execute(index["sql"])


def _install_task20_paired_source(db):
    objects = json.loads(TASK20_PAIRED_SOURCE.read_text())["objects"]
    for object_type, _name, sql in objects:
        if object_type == "table" and sql and "virtual table" not in sql.lower():
            db.execute(sql)
    for object_type, _name, sql in objects:
        if object_type == "table" and sql and "virtual table" in sql.lower():
            db.execute(sql)
    for kind in ("index", "trigger"):
        for object_type, _name, sql in objects:
            if object_type == kind and sql:
                db.execute(sql)


@pytest.fixture(params=("actual", "fresh_candidate"))
def source(tmp_path, request):
    path = tmp_path / "paired.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        _install_task20_paired_source(db)
        create_run_schema(db, include_workbench=True)
        create_current_email_non_action_schema(db)
        config_variant = "actual" if request.param == "actual" else "fresh"
        native = {name: definition for name, definition in
                  config_capture()[config_variant]["tables"].items()
                  if name in CONFIG_SOURCE_TABLES and name not in (
                      "managed_skill_export_receipts", "runtime_skill_load_receipts")}
        for name in (*native, "email_training_snapshot_observations"):
            db.execute(f'drop table if exists "{name}"')
        for definition in native.values():
            db.execute(definition["sql"])
        for definition in native.values():
            for index in reversed(definition["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in definition["triggers"]:
                db.execute(trigger["sql"])
        receipts = json.loads((ROOT / "docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json").read_text())
        for name in ("managed_skill_export_receipts", "runtime_skill_load_receipts"):
            for obj in receipts["actual_frozen_receipt_metadata"][name]["objects"]:
                if obj["type"] == "table":
                    db.execute(obj["sql"])
            for obj in receipts["actual_frozen_receipt_metadata"][name]["objects"]:
                if obj["type"] != "table" and obj["sql"]:
                    db.execute(obj["sql"])
        install_observations(db, config_variant)
        # This fixture models the published Email51 source. The fresh current
        # factory owns the target, so replace only its synthetic provider unit
        # with the frozen actual/fresh source declarations.
        db.execute("drop table provider_state")
        for definition in provider_declarations(
            "actual" if request.param == "actual" else "fresh"
        ).values():
            db.execute(definition["sql"])
            for index in definition["indexes"]:
                if index["sql"] is not None:
                    db.execute(index["sql"])
        create_current_email_migration_ledger(db)
        state_tables = config_capture()[config_variant]["tables"]
        for kind in STATE_SOURCE_TABLES:
            db.execute(f'drop table if exists "{kind}"')
        for kind in STATE_SOURCE_TABLES:
            db.execute(state_tables[kind]["sql"])
        for kind in STATE_SOURCE_TABLES:
            for index in reversed(state_tables[kind]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in state_tables[kind]["triggers"]:
                db.execute(trigger["sql"])
        db.execute("insert into service_state values('store_schema_version',?,?)", ORIGINAL_STORE)
        db.executemany("insert into email_schema_migrations values(?,?)", ORIGINAL_LEDGER)
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
        db.execute("insert into unrelated_child values(1,404)")
        storage.create_action_schema(db, include_reply=True, reply_variant=(
            "actual_no_memory_fk" if request.param == "actual" else "fresh_memory_fk"))
        for table in _source_tables(request.param).values():
            db.execute(table["sql"])
            for index in reversed(table["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in table["triggers"]:
                db.execute(trigger["sql"])
        external = [item for item in PARENTS[request.param]["external_schema_references"]
                    if item["type"] == "trigger"]
        external += CHILDREN["actual"]["external_schema_references"]
        for trigger in external:
            if trigger["name"] not in {
                item["name"] for item in native["email_accounts"]["triggers"]
            }:
                db.execute(trigger["sql"])
        db.execute("""insert into email_classifications(
            id,account_id,folder,uidvalidity,uid,stable_message_identity,
            confidence,margin,probabilities_json,model_id,config_version,status,
            classification_source
        ) values(11,'account','INBOX',1,1,'message',0.5,0.1,'{}','model',
                 'config','pending_feedback','model')""")
        db.execute("""insert into email_messages(
            account_id,stable_message_identity,folder,uidvalidity,uid,
            rfc_message_id,in_reply_to,references_json,thread_identity,sender,
            recipients_json,subject,normalized_text,preview,
            attachment_metadata_json,received_at,created_at,updated_at
        ) values('account','message','INBOX',1,1,'','','[]','','',
                 '[]','','','','[]','','','')""")
        for kind, values in _cases().items():
            db.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                       tuple(values.values()))
        db.execute("update email_actions set attempt_count=1")
        # Ordinary nullable IDs retain their structural ROWID independently.
        db.execute("insert into email_unsubscribe_cancellations(rowid,decision_json) values(-7,'{}')")
        for name in ("business_source_documents", "business_task_signals"):
            definition = BUSINESS_CAPTURE["fresh"]["tables"][name]
            db.execute(definition["sql"])
            for index in reversed(definition["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in definition["triggers"]:
                db.execute(trigger["sql"])
        business_source = json.loads(BUSINESS_FRESH_DECLARATIONS.read_text())
        business_source["parents"].update(
            json.loads(BUSINESS_WORK_PROJECTS_DECLARATIONS.read_text())[
                "fresh"
            ]["parents"]
        )
        _recreate_source(db, business_source)
        attention_events = BUSINESS_CANDIDATE_SOURCE_PROFILES["fresh"]["business_attention_events"]
        db.execute(attention_events["sql"])
        for sql in (*attention_events["indexes"], *attention_events["triggers"]):
            db.execute(sql)
        # Business parents/children in this synthetic source use the frozen
        # fresh declarations in both Email variants; keep this third native
        # candidate in that same Business source variant.
        wechat = BUSINESS_CANDIDATE_SOURCE_PROFILES["fresh"]["wechat_memory_candidates"]
        db.execute(wechat["sql"])
        for sql in (*wechat["indexes"], *wechat["triggers"]):
            db.execute(sql)
        db.execute(
            "insert into business_tasks(id,title,stage) "
            "values(-11,'paired source task','candidate')"
        )
        db.execute(
            "insert into business_anchors(id,anchor_type,anchor_ref,title) "
            "values(-14,'project','paired','paired anchor')"
        )
        db.execute(
            "insert into business_projects(id,canonical_anchor_id,title,registry_source) "
            "values(-15,-14,'paired project','synthetic')"
        )
        db.execute(
            "insert into business_project_context_revisions("
            "id,project_id,context_json,evidence_json) values(-16,-15,'{}','[]')"
        )
        db.execute(
            "insert into service_bugfix_candidates("
            "id,feedback_event_key,feedback_token,attempt_id,title,reason,feedback_comment) "
            "values(-17,'paired-event','paired-token',7,'paired candidate','reason','comment')"
        )
        db.execute(
            "insert into business_work_clusters(id,title) values(-18,'paired cluster')"
        )
        db.execute(
            "insert into todo_evidence_candidates(id,project_id,todo_id,source_type,source_ref,"
            "evidence_text,reason,confidence,status,work_summary_input_id,decision_json,"
            "dedupe_key) values(-19,-15,-11,'minutes','paired-source','evidence','reason',"
            "0.75,'accepted',12,'{}','paired-dedupe')"
        )
        db.execute(
            "insert into work_projects(id,title,memory_context_json) "
            "values(-20,'paired work project','{\"query\":\"cache\"}')"
        )
        db.execute(
            "insert into work_todos(id,project_id,title,status,completed_at,"
            "completion_evidence_json) values(-20,-20,'paired work todo','done',"
            "'2026-10-01T00:00:00+00:00','{\"source\":\"receipt:20\"}')"
        )
        _install_followup_sync_sources(
            db, "actual" if request.param == "actual" else "fresh"
        )
        _install_later_action_sources(db, "actual" if request.param == "actual" else "fresh")
        assert capture_business_source(db)["variant"] == "fresh"
        db.commit()
    return path


@pytest.fixture
def source_with_business(source):
    return source


def _prepare(path):
    return release_module.prepare_action_email_schema_release(
        ROOT, path, target_store_version=TARGET_STORE, target_email_version=TARGET_EMAIL)


def test_paired_initializer_budget_covers_measured_child_and_transports_all_inverse_metadata(monkeypatch):
    # Copy-only profile measured 412.570 seconds for the whole child. A clocked
    # double tests the transport deadline without making this test sleep.
    release = SimpleNamespace(
        root=ROOT, database_path=ROOT / "synthetic-unused.sqlite",
        original_version=("source", "stamp"), versions=((51, "stamp"),),
        source_schema={}, source_fk=(), target_store_version="target",
        target_email_version=52, source_bindings={}, recovery_source_path=None,
    )
    monkeypatch.setattr(release_module, "_inverse_metadata", lambda _source: {})
    names = ("FOLLOWUP_SYNC", "TASK_DELIVERY", "ERROR", "FEEDBACK",
             "BUSINESS_CANDIDATES", "BUSINESS_ACTIONS", "EMAIL_HISTORY",
             "EMAIL_CANONICAL", "LEGACY_OWNER", "FINAL_ACTIONS")
    keys = {"ERROR": "errors", "FEEDBACK": "feedback"}
    values = {keys.get(name, name.lower()): {"source": name} for name in names}
    seen = []

    def measured_child(arguments, **options):
        seen.append(options)
        if options["timeout"] < 412.570:
            raise subprocess.TimeoutExpired(arguments, options["timeout"])
        return SimpleNamespace(stdout="".join(
            f"{name}_INVERSE={json.dumps(values[keys.get(name, name.lower())])}\n" for name in names
        ))

    monkeypatch.setattr(release_module.subprocess, "run", measured_child)
    release_module._migrate_pair(release)
    assert seen[0]["timeout"] == 15 * 60
    assert seen[0]["check"] and seen[0]["capture_output"]
    assert {key: release.source_schema[key] for key in values} == values


@pytest.mark.parametrize("failure", ("timeout", "child_error"))
def test_paired_initializer_propagates_real_child_failures(monkeypatch, failure):
    release = SimpleNamespace(
        root=ROOT, database_path=ROOT / "synthetic-unused.sqlite",
        original_version=("source", "stamp"), versions=((51, "stamp"),),
        source_schema={}, source_fk=(), target_store_version="target",
        target_email_version=52, source_bindings={}, recovery_source_path=None,
    )
    monkeypatch.setattr(release_module, "_inverse_metadata", lambda _source: {})

    def failed_child(arguments, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, options["timeout"])
        raise subprocess.CalledProcessError(1, arguments, stderr="failed child")

    monkeypatch.setattr(release_module.subprocess, "run", failed_child)
    with pytest.raises(subprocess.TimeoutExpired if failure == "timeout"
                       else subprocess.CalledProcessError):
        release_module._migrate_pair(release)
    assert release.source_schema == {}


@pytest.mark.parametrize("failure", ("child_error", "timeout"))
def test_committed_child_inverse_survives_postcommit_failure(monkeypatch, failure):
    release = SimpleNamespace(
        root=ROOT, database_path=ROOT / "synthetic-unused.sqlite",
        original_version=("source", "stamp"), versions=((51, "stamp"),),
        source_schema={}, source_fk=(), target_store_version="target", target_email_version=52, source_bindings={}, recovery_source_path=None)
    monkeypatch.setattr(release_module, "_inverse_metadata", lambda _source: {})
    values = {"followup_sync": {}, "task_delivery": {}, "errors": {}, "feedback": {},
              "business_candidates": {}, "business_actions": {}, "email_history": {},
              "email_canonical": {}, "legacy_owner": {"variant": "committed"}, "final_actions": {}}
    names = {"errors": "ERROR", "feedback": "FEEDBACK"}
    output = "".join(names.get(key, key.upper()) + "_INVERSE=" + json.dumps(value) + "\n"
                     for key, value in values.items())
    run = subprocess.run
    def child(arguments, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, options["timeout"], output=output.encode())
        # A real child emits its committed inverse and then fails validation.
        return run([arguments[0], "-c", "import sys; sys.stdout.write(" + repr(output)
                    + "); sys.stdout.flush(); sys.stderr.write('cold-open failed'); sys.exit(7)"], **options)
    monkeypatch.setattr(release_module.subprocess, "run", child)
    with pytest.raises(subprocess.TimeoutExpired if failure == "timeout" else subprocess.CalledProcessError) as caught:
        release_module._migrate_pair(release)
    assert release.source_schema == values
    if failure == "child_error":
        assert caught.value.returncode == 7
        assert caught.value.stderr == "cold-open failed"


def test_child_emits_committed_inverse_before_loaded_validation(monkeypatch, capsys):
    import io
    payload = dict(root=str(ROOT), database_path=str(ROOT / "synthetic-unused.sqlite"),
                   original_version=["source", "stamp"], versions=[[51, "stamp"]],
                   source_schema={}, source_fk=[], target_store_version="target", target_email_version=52, source_bindings={}, recovery_source_path=None)
    fields = ("followup_sync", "task_delivery", "errors", "feedback", "business_candidates",
              "business_actions", "email_history", "email_canonical", "legacy_owner", "final_actions")
    release = SimpleNamespace(source_schema={name: {"committed": True} for name in fields})
    def apply(*, on_prepared=None):
        if on_prepared is not None:
            on_prepared()
        raise ValueError("postcommit loaded verification failed")
    release.apply = apply
    monkeypatch.setattr(release_module, "ActionEmailSchemaRelease", lambda *args, **kwargs: release)
    monkeypatch.setattr(release_module.sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(ValueError, match="postcommit loaded"):
        release_module._paired_initializer()
    output = capsys.readouterr().out
    assert "LEGACY_OWNER_INVERSE=" in output
    assert len(output.splitlines()) == len(fields)


def test_timeout_during_uncommitted_metadata_write_keeps_original_error(monkeypatch):
    release = SimpleNamespace(
        root=ROOT, database_path=ROOT / "synthetic-unused.sqlite", original_version=("source", "stamp"),
        versions=((51, "stamp"),), source_schema={}, source_fk=(), target_store_version="target", target_email_version=52, source_bindings={}, recovery_source_path=None)
    monkeypatch.setattr(release_module, "_inverse_metadata", lambda _source: {})
    partial = b'FOLLOWUP_SYNC_INVERSE={}\nTASK_DELIVERY_INVERSE={"source":"\xc3'
    def timed_out(arguments, **options):
        raise subprocess.TimeoutExpired(arguments, options["timeout"], output=partial)
    monkeypatch.setattr(release_module.subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        release_module._migrate_pair(release)
    assert caught.value.output == partial
    assert release.source_schema == {}


def _markers(db):
    current = db.execute("select 1 from main.sqlite_schema where name='state' collate nocase").fetchone() is not None
    source = state_projection("service_state") if current else "select * from main.service_state"
    return (
        db.execute(f"select value,updated_at from ({source}) where key='store_schema_version'").fetchone(),
        tuple(db.execute("select version,applied_at from email_schema_migrations order by version")),
    )


def _fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        rowids = tuple((kind, tuple(db.execute(f"select rowid,* from {kind} order by rowid")))
                       for kind in email_storage.SOURCE_TABLES
                       if kind in storage.EMAIL_ROWID_TYPES
                       and db.execute("select 1 from sqlite_schema where name=?", (kind,)).fetchone())
        dump = tuple(sorted(
            statement for statement in db.iterdump()
            if not statement.startswith('INSERT INTO "codex_session_search_fts_data"')
        ))
        fts_content = (
            tuple(db.execute(
                "select rowid,title from codex_session_search_fts order by rowid"
            ))
            if db.execute(
                "select 1 from sqlite_schema where name='codex_session_search_fts'"
            ).fetchone()
            else None
        )
        return dump, rowids, fts_content


def _business_work_project_facts(path):
    with closing(sqlite3.connect(path)) as db:
        columns = tuple(row[1] for row in db.execute("pragma table_info(work_projects)"))
        retained = tuple(column for column in columns if column != "memory_context_json")
        selected = ",".join(f'"{column}"' for column in retained)
        values = tuple(tuple(row) for row in db.execute(
            f"select {selected} from work_projects order by rowid"
        ))
        types = tuple(tuple(row) for row in db.execute(
            f"select {','.join(f'typeof(\"{column}\")' for column in retained)} "
            "from work_projects order by rowid"
        ))
        cache = tuple(row[0] for row in db.execute(
            "select memory_context_json from work_projects order by rowid"
        )) if "memory_context_json" in columns else ()
    return retained, values, types, cache


def _assert_inverse_except_retired_work_project_cache(path, before, work_projects_before):
    after = _fingerprint(path)
    before_dump = tuple(item for item in before[0]
                        if not item.startswith('INSERT INTO "work_projects"'))
    after_dump = tuple(item for item in after[0]
                       if not item.startswith('INSERT INTO "work_projects"'))
    assert (after_dump, after[1], after[2]) == (before_dump, before[1], before[2])
    retained, values, types, cache = _business_work_project_facts(path)
    assert (retained, values, types) == work_projects_before[:3]
    assert cache == tuple("{}" for _ in cache)


def _database_image(path):
    with closing(sqlite3.connect(path)) as db:
        return tuple(db.iterdump())


def test_email_then_business_failure_rolls_back_entire_pair(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)

    def fail_after_email(_db):
        raise RuntimeError("business injection after Email conversion")

    monkeypatch.setattr(release_module, "migrate_business_schema", fail_after_email)
    with pytest.raises(RuntimeError, match="after Email conversion"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_provider_failure_after_conversion_rolls_back_all_three_components(monkeypatch, source):
    release = _prepare(source)
    before = _database_image(source)
    convert = release_module.apply_provider_state_migration

    def fail_after_provider(db, metadata):
        convert(db, metadata)
        raise RuntimeError("provider conversion injection")

    monkeypatch.setattr(release_module, "apply_provider_state_migration", fail_after_provider)
    with pytest.raises(RuntimeError, match="provider conversion injection"):
        release.apply()
    assert _database_image(source) == before


def test_business_source_preflight_failure_is_readonly(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)

    def fail_preflight(_db):
        raise RuntimeError("Business pre-DDL injection")

    monkeypatch.setattr(release_module, "capture_business_source", fail_preflight)
    with pytest.raises(RuntimeError, match="pre-DDL injection"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_first_child_rebuild_failure_rolls_back_both_migrations(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)
    create = business_module._create_children

    def fail_after_one_child(db, declarations, *, target):
        if target:
            name = next(iter(declarations))
            create(db, {name: declarations[name]}, target=True)
            raise RuntimeError("Business first-child injection")
        return create(db, declarations, target=False)

    monkeypatch.setattr(business_module, "_create_children", fail_after_one_child)
    with pytest.raises(RuntimeError, match="first-child injection"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_prepare_is_readonly_and_captures_exact_pair_schema_aliases_and_ledger(source):
    before = _fingerprint(source)
    release = _prepare(source)
    assert release.original_version == ORIGINAL_STORE
    assert release.versions == ORIGINAL_LEDGER
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin")
        expected = email_storage.capture_email_action_source(db)
        expected["business"] = capture_business_source(db)
        expected["provider"] = capture_provider_state_source(db)
        expected["configs"] = release_module._project_config_source(db, expected["action_variant"])
        expected["state"] = capture_state_source(db)
        expected["deferral_receipts"] = capture_native_receipts(db)
        expected["confirmation"] = capture_confirmation_source(db)
        expected["tasks"] = capture_task20_source(db)
        expected["runs11"] = capture_run11_source(db)
        assert release.source_schema == expected
    assert release.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
    assert _fingerprint(source) == before


def test_readonly_config_projection_accepts_independent_source_variants(source):
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin")
        config_variant = release_module.capture_config_declarations(db)['variant']
        action_variant = ("fresh_memory_fk" if config_variant == "actual"
                          else "actual_no_memory_fk")
        projected = release_module._project_config_source(db, action_variant)
        assert projected["configs"]["variant"] == config_variant + "_email52"
        assert projected["action_variant"] == action_variant


def test_prepare_refuses_external_native_config_view_without_mutation(source):
    with closing(sqlite3.connect(source)) as db:
        db.execute("create view unrelated_config_reader as select id from managed_skills")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError, match="dependency"):
        _prepare(source)
    assert _fingerprint(source) == before


@pytest.mark.parametrize("damage", ("store", "email", "timestamp", "ledger", "declaration", "provider_declaration"))
def test_prepare_and_publish_refuse_changed_source_without_mutation(source, damage):
    release = _prepare(source)
    with closing(sqlite3.connect(source)) as db:
        if damage == "store":
            db.execute("update service_state set value='unverified' where key='store_schema_version'")
        elif damage == "email":
            db.execute("insert into email_schema_migrations values(50,'future')")
            db.execute("delete from email_schema_migrations where version=51")
        elif damage == "timestamp":
            db.execute("update service_state set updated_at='changed' where key='store_schema_version'")
        elif damage == "ledger":
            db.execute("update email_schema_migrations set applied_at='changed' where version=1")
        elif damage == "provider_declaration":
            db.execute("create index unknown_provider_source on email_provider_observations(observed_at)")
        else:
            db.execute("create index unknown_email_plan_index on email_action_plans(created_at)")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError):
        release.apply()
    assert _fingerprint(source) == before
    if damage in ("store", "email", "declaration", "provider_declaration"):
        with pytest.raises(ValueError):
            _prepare(source)
        assert _fingerprint(source) == before


@pytest.mark.parametrize("stage", ("before_ddl", "after_capture", "during_conversion"))
def test_publication_failure_rolls_back_ddl_markers_and_all_source_facts(source, monkeypatch, stage):
    release = _prepare(source)
    before = _fingerprint(source)

    def fail(*args, **kwargs):
        raise RuntimeError("injected paired publication failure")

    if stage == "before_ddl":
        monkeypatch.setattr(release_module, "capture_email_action_source", fail)
    elif stage == "after_capture":
        monkeypatch.setattr(release_module, "migrate_email_action_schema", fail)
    elif stage == "during_conversion":
        original = email_storage._insert_email_raw

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(email_storage, "_insert_email_raw", interrupted)
    else:
        connect = sqlite3.connect

        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "after_first_marker" and sql.lower().startswith("insert into main.email_schema_migrations"):
                    assert self.execute(f"select value from ({state_projection('service_state')}) where key='store_schema_version'").fetchone() == (TARGET_STORE,)
                    fail()
                return super().execute(sql, parameters)

            def commit(self):
                if stage == "before_commit":
                    assert _markers(self)[0][0] == TARGET_STORE
                    assert _markers(self)[1][-1][0] == TARGET_EMAIL
                    fail()
                return super().commit()

        def interrupted_connect(*args, **kwargs):
            if args and args[0] == source:
                return connect(*args, **kwargs, factory=InterruptedConnection)
            return connect(*args, **kwargs)

        monkeypatch.setattr(release_module.sqlite3, "connect", interrupted_connect)
    with pytest.raises(RuntimeError, match="injected paired"):
        release.apply()
    assert _fingerprint(source) == before


def test_fresh_email_seeds_disabled_categories_for_preexisting_enabled_typed_account(tmp_path):
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.configs_storage import insert_config
    from app.configs_definitions import config_projection

    path = tmp_path / "preseeded-account.sqlite3"
    store = AutoReplyStore(path)
    with store._immediate_write_transaction() as db:
        insert_config(db, "email_accounts", {
            "account_id": "preseeded", "display_name": "Preseeded",
            "email_address": "preseeded@example.test", "imap_host": "imap.example.test",
            "imap_port": 993, "imap_tls": 1, "imap_username": "preseeded",
            "imap_secret_reference": "env:TEST_IMAP", "imap_move_mode": "move",
            "smtp_host": "smtp.example.test", "smtp_port": 465, "smtp_tls": 1,
            "smtp_username": "preseeded", "smtp_secret_reference": "env:TEST_SMTP",
            "enabled": 1, "scan_folders_json": '["INBOX"]',
            "scan_interval_seconds": 60, "created_at": "2026-10-15 00:00:00",
            "updated_at": "2026-10-15 00:00:00",
        })
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            f"select enabled from ({config_projection('email_category_configs')})"
        ).fetchall()
        assert rows and all(row[0] == 0 for row in rows)


@pytest.mark.parametrize("damage", [
    "altered_store_index", "unique_store_index", "partial_store_index",
    "missing_email_index",
])
def test_current_constructor_refuses_nonrepairable_metadata_without_mutation(tmp_path, damage):
    from app.store import AutoReplyStore
    import app.store as store_module
    from app.email_store import EmailStore

    path = tmp_path / "malformed-current.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        if damage == "altered_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create index idx_schedules_enabled on schedules(name)")
        elif damage == "unique_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create unique index idx_schedules_enabled on schedules(enabled,deleted_at,id)")
        elif damage == "partial_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create index idx_schedules_enabled on schedules(enabled,deleted_at,id) where enabled=1")
        else:
            db.execute("drop index idx_actions_email_email_action_plans_0")
    before = _database_image(path)
    with closing(sqlite3.connect(path)) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(path)
    assert _database_image(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before


@pytest.mark.parametrize("damage", (
    "schedule_wrong_owner", "schedule_casefold_owner", "schedule_view", "schedule_table",
    "config_index_wrong_owner", "config_trigger_wrong_owner",
    "action_index_wrong_owner", "action_trigger_wrong_owner",
))
def test_current_constructor_refuses_occupied_repair_name_before_mutation(tmp_path, damage):
    from app.store import AutoReplyStore
    import app.store as store_module
    from app.email_store import EmailStore

    path = tmp_path / "occupied-repair-name.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        if damage in ("schedule_wrong_owner", "schedule_casefold_owner", "schedule_view", "schedule_table"):
            db.execute("drop index idx_schedules_enabled")
            if damage == "schedule_view":
                db.execute("create view idx_schedules_enabled as select 1 as value")
            elif damage == "schedule_table":
                db.execute("create table idx_schedules_enabled(value integer)")
            else:
                name = ("IDX_SCHEDULES_ENABLED" if damage == "schedule_casefold_owner"
                        else "idx_schedules_enabled")
                db.execute(f"create index {name} on tasks(id)")
        elif damage == "config_index_wrong_owner":
            db.execute("drop index idx_configs_revision_number")
            db.execute("create index idx_configs_revision_number on tasks(id)")
        elif damage == "config_trigger_wrong_owner":
            db.execute("drop trigger trg_configs_runtime_config_update")
            db.execute("create trigger trg_configs_runtime_config_update after insert on tasks begin select 1; end")
        elif damage == "run_index_wrong_owner":
            db.execute("drop index idx_scheduled_task_runs_latest")
            db.execute("create index idx_scheduled_task_runs_latest on tasks(id)")
        elif damage == "run_trigger_wrong_owner":
            db.execute("drop trigger trg_runs_workbench_turn_insert")
            db.execute("create trigger trg_runs_workbench_turn_insert after insert on tasks begin select 1; end")
        elif damage == "action_index_wrong_owner":
            db.execute("drop index idx_actions_skill_export_revision")
            db.execute("create index idx_actions_skill_export_revision on tasks(id)")
        else:
            db.execute("drop trigger trg_actions_runtime_skill_load_receipts_update")
            db.execute("create trigger trg_actions_runtime_skill_load_receipts_update after insert on tasks begin select 1; end")
    before = _database_image(path)
    with closing(sqlite3.connect(path)) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(path)
    assert _database_image(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before


@pytest.mark.parametrize("target_store,target_email", (
    ("2026-10-13.5", TARGET_EMAIL), ("", TARGET_EMAIL), (TARGET_STORE, 51),
    (TARGET_STORE, True), (TARGET_STORE, "unallocated"),
))
def test_protocol_requires_explicit_different_target_pair_without_mutation(source, target_store, target_email):
    before = _fingerprint(source)
    with pytest.raises(ValueError, match="explicit different target pair"):
        release_module.prepare_action_email_schema_release(
            ROOT, source, target_store_version=target_store, target_email_version=target_email)
    assert _fingerprint(source) == before


@pytest.mark.parametrize("current", (False,))
def test_paired_state_declaration_drift_refuses_without_mutation(source, current):
    release = _prepare(source)
    if current:
        release.apply()
    with closing(sqlite3.connect(source)) as db:
        if current:
            db.execute("drop index idx_state_dispatcher_expiry")
        else:
            db.execute("create index unknown_seen_state_source on seen_messages(seen_at)")
        db.commit()
    before = _database_image(source)
    with pytest.raises(ValueError):
        if current:
            release.rollback()
        else:
            release.apply()
    assert _database_image(source) == before
