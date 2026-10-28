"""Selection readers for current adopted-result facts."""

from functools import lru_cache
import json

from app import action_storage as actions



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
