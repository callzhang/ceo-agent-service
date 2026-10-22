import sqlite3

import pytest

import tools.validate_action_entity_copy as validator
from tools.validate_action_entity_copy import (
    StrictSubstituteProvider,
    assert_capacity_pair,
    backup_readonly,
    check_live_provider_rejected,
    check_new_facts_survive_inverse,
    check_readonly_backup_calls,
    _compare_source_databases,
)


def test_validation_never_opens_production_for_write(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    copied = tmp_path / "owned-copy.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("create table facts(value text)")
        db.execute("insert into facts values('source')")

    calls = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(validator, "_owned", lambda path: path.resolve())

    def tracked_connect(database, *args, **kwargs):
        if str(database).startswith(source.as_uri()):
            calls.append(("source", "mode=ro" in str(database)))
        elif PathLike(database, copied):
            calls.append(("copy", False))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    assert backup_readonly(source, copied)
    check_readonly_backup_calls(calls)
    with real_connect(copied) as db:
        assert db.execute("select value from facts").fetchone() == ("source",)


def PathLike(database, expected):
    return str(database) == str(expected)


def test_live_provider_is_rejected_by_harness():
    class LiveProvider:
        def send_message(self, *_args, **_kwargs):
            raise AssertionError("must never be called")

    with pytest.raises(TypeError, match="only accepts StrictSubstituteProvider"):
        check_live_provider_rejected(LiveProvider())
    check_live_provider_rejected(StrictSubstituteProvider())


def test_new_facts_checked_after_inverse():
    facts = {"new_action": "action-digest", "receipt": "receipt-digest"}
    check_new_facts_survive_inverse(facts, facts)
    with pytest.raises(AssertionError, match="changed"):
        check_new_facts_survive_inverse(facts, {**facts, "receipt": "changed"})
    with pytest.raises(AssertionError, match="required"):
        check_new_facts_survive_inverse({}, {})


def test_capacity_uses_same_population_and_packing():
    before = {"population_sha256": "same-facts", "vacuumed": True, "bytes": 100}
    after = {"population_sha256": "same-facts", "vacuumed": True, "bytes": 80}
    assert_capacity_pair(before, after)
    with pytest.raises(AssertionError, match="population_sha256"):
        assert_capacity_pair(before, {**after, "population_sha256": "different-facts"})
    with pytest.raises(AssertionError, match="packing"):
        assert_capacity_pair(before, {**after, "vacuumed": False})


def _write_semantic_actions(path, *, shifted=False, changed_body=False, wrong_sent=False,
                            missing_sent=False, remap_out_of_scope=False):
    sent_id = 11 if shifted else 1
    observer_id = 22 if shifted else 2
    email_id = 33 if shifted else 3
    event_id = 44 if remap_out_of_scope else 4
    with sqlite3.connect(path) as db:
        db.executescript(
            "create table actions ("
            "id integer primary key autoincrement, _kind text not null, legacy_key text not null, "
            "sent_reply_id integer references actions(id), final_body text, "
            "record_type text generated always as (_kind) virtual, "
            "unique(_kind,legacy_key));"
            "insert into actions(id,_kind,legacy_key) values(900,'sequence_watermark','water');"
            "delete from actions where _kind='sequence_watermark';"
        )
        db.execute("insert into actions(id,_kind,legacy_key,final_body) values(?,?,?,?)",
                   (sent_id, "sent_replies", "sent:source-id", "sent body"))
        second_sent_id = 12 if shifted else 5
        db.execute("insert into actions(id,_kind,legacy_key,final_body) values(?,?,?,?)",
                   (second_sent_id, "sent_replies", "sent:second", "other body"))
        reference = second_sent_id if wrong_sent else sent_id
        if missing_sent:
            reference = 7777
        db.execute("insert into actions(id,_kind,legacy_key,sent_reply_id) values(?,?,?,?)",
                   (observer_id, "sent_reply_observers", "observer:source-id", reference))
        db.execute("insert into actions(id,_kind,legacy_key,final_body) values(?,?,?,?)",
                   (email_id, "email_actions", "email:source-id",
                    "changed" if changed_body else "stable"))
        db.execute("insert into actions(id,_kind,legacy_key) values(?,?,?)",
                   (event_id, "business_task_events", "event:source-id"))


def test_semantic_cohort_normalizes_only_covered_typed_action_ids(tmp_path):
    before = tmp_path / "before.sqlite3"
    inverse = tmp_path / "inverse.sqlite3"
    _write_semantic_actions(before)
    _write_semantic_actions(inverse, shifted=True)

    result = _compare_source_databases(before, inverse)

    assert result["exact_match"] is True
    assert result["action_typed_identity_count"] == 5
    assert result["physical_action_id_remaps_by_type"] == {
        "email_actions": 1,
        "sent_replies": 2,
        "sent_reply_observers": 1,
    }
    assert result["out_of_scope_physical_action_id_remaps"] == 0
    assert result["sent_reference_rows"] == 1
    assert result["sent_reply_reference_identity_sha256"]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"changed_body": True}, "semantic population differs"),
        ({"wrong_sent": True}, "Sent references no longer resolve"),
        ({"missing_sent": True}, "Sent reference does not resolve"),
        ({"remap_out_of_scope": True}, "out-of-scope physical Action IDs changed"),
    ],
)
def test_semantic_cohort_rejects_business_loss_wrong_refs_and_out_of_scope_id_changes(
    tmp_path, changes, error
):
    before = tmp_path / "before.sqlite3"
    inverse = tmp_path / "inverse.sqlite3"
    _write_semantic_actions(before)
    _write_semantic_actions(inverse, shifted=True, **changes)

    with pytest.raises(AssertionError, match=error):
        _compare_source_databases(before, inverse)
