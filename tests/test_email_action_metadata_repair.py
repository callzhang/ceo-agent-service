"""Keep original Email metadata refusal outside the Store's repair permission."""

from pathlib import Path


from app.action_storage import REPLY_VARIANTS, _current_objects

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "0441cf15fafe800f9ca4d67f667e87062ca62109"


def _email_objects(variant):
    options = dict(
        include_reply=True,
        reply_variant=variant,
        physical_provenance=True,
        compact_payload=True,
    )
    old = {
        o["name"] for o in _current_objects(**options)
    }
    return {
        o["name"]: o
        for o in _current_objects(include_email=True, **options)
        if o["name"] not in old
    }


EMAIL_OBJECTS = _email_objects(REPLY_VARIANTS[0])


def test_finite_delta_has_identical_complete_metadata_in_both_variants():
    assert _email_objects(REPLY_VARIANTS[0]) == _email_objects(REPLY_VARIANTS[1])
    assert len(EMAIL_OBJECTS) == 32
    assert sum(o["type"] == "index" for o in EMAIL_OBJECTS.values()) == 25
    assert sum(o["type"] == "trigger" for o in EMAIL_OBJECTS.values()) == 7
    assert (
        "actions" not in EMAIL_OBJECTS and "idx_actions_public_id" not in EMAIL_OBJECTS
    )

