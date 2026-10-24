"""Reference-only selections and their current-fact schema inverse.

Both directions require a caller-owned FK-off transaction. Only selections are
repacked; other Action payloads remain opaque bytes. Inverse metadata identifies
the frozen source declaration and contains no historical business rows.
"""

from functools import lru_cache
import json

from app import action_storage as actions
from app.storage_payload import ACTION_RECORD_TYPES


_SELECTION = "candidate_selections"
_STAGING = "_model_selection_actions"
_WATER = "_model_selection_water"


@lru_cache(maxsize=4)
def selection_action_objects(reply_variant, *, selection_references=True):
    """Complete target or frozen source declarations; no runtime discovery."""
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(
        reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=selection_references, **ACTION_OPTIONS)


def candidate_option(candidate_json, option_key):
    """Resolve exactly one option within the already-bound immutable candidate."""
    candidate = json.loads(candidate_json)
    options = candidate.get("decision_options") if isinstance(candidate, dict) else None
    if not isinstance(options, list):
        raise ValueError("candidate decision options are not an array")
    matches = [option for option in options if isinstance(option, dict) and option.get("key") == option_key]
    if len(matches) != 1:
        raise ValueError("selected option must occur exactly once in its bound candidate")
    return matches[0]


def selected_branch(db, selection_id):
    """Read the selected branch through its native adopted-result owner."""
    row = db.execute("""select c.candidate_json,s.option_key
        from run_adopted_selections s join run_adopted_results c
          on c.consumer_run_id=s.consumer_run_id
        where s.id=?""", (selection_id,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError("selection or its exact candidate is missing")
    return candidate_option(row[0], row[1])


def _require_source(db, *, selection_references):
    from app.business_candidate_storage import _collision

    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("selection transition requires caller-owned FK-off transaction")
    _collision(db, ("actions", _STAGING, _WATER))
    objects = actions._objects(db, "actions")
    variant = next((variant for variant in actions.REPLY_VARIANTS
                    if objects == selection_action_objects(variant, selection_references=selection_references)), None)
    if variant is None:
        raise ValueError("unsupported selection Actions source declaration")
    return variant


def _branches(db, *, selection_references):
    from app.reviewed_candidate_store import _json

    branch_column = "null" if selection_references else "s.branch_json"
    rows = db.execute("""select s.id,c.candidate_json,s.option_key,""" + branch_column + """
        from main.actions s left join main.actions c
          on c.id=s.candidate_id and c.record_type='review_candidates'
        where s.record_type='candidate_selections' order by s.id""")
    branches = {}
    for physical_id, candidate_json, option_key, old_branch in rows:
        if candidate_json is None:
            raise ValueError("selection's exact candidate is missing")
        branch = _json(candidate_option(candidate_json, option_key))
        if not selection_references and old_branch != branch:
            # Semantic equality alone would lose historical whitespace/key order
            # on inverse. Such bytes require a separate explicit disposition.
            raise ValueError("selection branch differs from canonical bound option bytes")
        branches[physical_id] = branch
    return branches


def _dependencies(db):
    from app.action_email_storage import _external_dependencies
    from app.configs_schema_transfer import _target_declarations as _config_declarations
    from app.final_action_schema import _email_guards

    config_guards = {obj["name"]: obj["sql"] for obj in _config_declarations()["configs"]["triggers"]}
    allowed = [name for name, sql in db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and tbl_name='configs'")
        if config_guards.get(name) == sql]
    for name, sql in db.execute("select name,sql from main.sqlite_schema where type='trigger' and tbl_name='emails'"):
        if name in _email_guards() and sql == _email_guards()[name]:
            allowed.append(name)
    _external_dependencies(db, targets=("actions",), allowed_trigger_names=allowed)


def _rebuild(db, variant, branches, *, selection_references):
    from app.action_email_storage import _columns, _quote

    _dependencies(db)
    columns = _columns(db, "actions")
    names = ",".join(map(_quote, columns))
    # Rebuilding a table can change SQLite's report order, not its findings.
    foreign_keys = sorted(tuple(row) for row in db.execute("pragma foreign_key_check"))
    db.execute(f"create temp table {_STAGING} as select {names} from main.actions")
    db.execute(f"create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence")
    if selection_references:
        path = actions.action_payloads(variant).path(_SELECTION, "branch_json")
        db.execute(f"update temp.{_STAGING} set payload_json=jsonb_remove(payload_json,?) where _kind=?",
                   (path, ACTION_RECORD_TYPES.code(_SELECTION)))
    else:
        layout = actions.action_payloads(variant, selection_references=True)
        slots = ["?" if name == "branch_json" else
                 "json_extract(payload_json," + repr(layout.path(_SELECTION, name)) + ")"
                 for name in actions.PAYLOAD_FIELDS[_SELECTION]]
        db.executemany(f"update temp.{_STAGING} set payload_json=jsonb_array("
                       + ",".join(slots) + ") where id=?",
                       ((branch, physical_id) for physical_id, branch in branches.items()))
    db.execute("drop table main.actions")
    objects = selection_action_objects(variant, selection_references=selection_references)
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    db.execute(f"insert into main.actions({names}) select {names} from temp.{_STAGING} order by id")
    for category in ("index", "trigger"):
        for obj in objects:
            if obj["type"] == category:
                db.execute(obj["sql"])
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}")
    # Compare all stored values and SQLite types against the staged current rows,
    # including untouched opaque JSONB bytes and physical/public identities.
    values = names + "," + ",".join("typeof(" + _quote(name) + ")" for name in columns)
    for left, right in (("main.actions", "temp." + _STAGING), ("temp." + _STAGING, "main.actions")):
        if db.execute(f"select {values} from {left} except select {values} from {right} limit 1").fetchone():
            raise ValueError("selection transition changed stored values or types")
    if (actions._objects(db, "actions") != objects
            or sorted(tuple(row) for row in db.execute("pragma foreign_key_check")) != foreign_keys):
        raise ValueError("selection transition changed declaration or foreign keys")
    db.execute(f"drop table temp.{_STAGING}")
    db.execute(f"drop table temp.{_WATER}")


def migrate_selection_references(db):
    """Remove the duplicate branch slot after proving its exact reconstruction."""
    variant = _require_source(db, selection_references=False)
    branches = _branches(db, selection_references=False)
    _rebuild(db, variant, branches, selection_references=True)
    return {"reply_variant": variant}


def restore_selection_references(db, metadata):
    """Recreate the frozen source from current selections, including new ones."""
    variant = _require_source(db, selection_references=True)
    if metadata != {"reply_variant": variant}:
        raise ValueError("unsupported selection inverse metadata")
    branches = _branches(db, selection_references=True)
    _rebuild(db, variant, branches, selection_references=False)
