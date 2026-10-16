"""Select the declared Reply/Email scaffold without changing historical defaults."""
from contextlib import closing
import sqlite3

import pytest

from app.action_storage import (
    REPLY_VARIANTS, create_action_schema, reply_schema_is_current,
    validated_reply_variant,
)


@pytest.mark.parametrize("variant", REPLY_VARIANTS)
@pytest.mark.parametrize("email,skills", ((False, False), (True, False), (True, True)))
def test_exact_declaration_and_historical_defaults(variant, email, skills):
    with closing(sqlite3.connect(":memory:")) as db:
        create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=email, include_skill_config_receipts=skills)
        before = tuple(db.iterdump())
        for requested_email, requested_skills in ((False, False), (True, False), (True, True)):
            selected = dict(include_email=requested_email,
                            include_skill_config_receipts=requested_skills)
            expected = (email, skills) == (requested_email, requested_skills)
            assert reply_schema_is_current(db, **selected) is expected
            if expected:
                assert validated_reply_variant(db, **selected) == variant
            else:
                with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
                    validated_reply_variant(db, **selected)
        assert reply_schema_is_current(db) is (not email and not skills)
        assert tuple(db.iterdump()) == before
        assert not db.in_transaction


@pytest.mark.parametrize("variant", REPLY_VARIANTS)
@pytest.mark.parametrize("change", ("missing_index", "changed_index", "extra_index", "extra_trigger", "native_table"))
def test_current_declaration_rejects_incomplete_or_mixed_shapes(variant, change):
    with closing(sqlite3.connect(":memory:")) as db:
        create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=True, include_skill_config_receipts=True)
        index = "idx_actions_candidates_task"
        if change == "missing_index":
            db.execute(f"drop index {index}")
        elif change == "changed_index":
            db.execute(f"drop index {index}")
            db.execute(f"create index {index} on actions(record_type)")
        elif change == "extra_index":
            db.execute("create index unexpected_action_index on actions(record_type)")
        elif change == "extra_trigger":
            db.execute("create trigger unexpected_action_trigger after insert on actions begin select 1; end")
        else:
            db.execute("create table reply_attempts(id integer)")
        before = tuple(db.iterdump())
        selected = dict(include_email=True, include_skill_config_receipts=True)
        assert not reply_schema_is_current(db, **selected)
        with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
            validated_reply_variant(db, **selected)
        if change == "missing_index":
            assert validated_reply_variant(db, allow_missing_objects=True, **selected) == variant
        else:
            with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
                validated_reply_variant(db, allow_missing_objects=True, **selected)
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("variant", REPLY_VARIANTS)
def test_current_selection_is_read_only_at_the_file_boundary(tmp_path, variant):
    path = tmp_path / "current.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=True, include_skill_config_receipts=True)
    before = path.read_bytes()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert validated_reply_variant(db, include_email=True,
                                       include_skill_config_receipts=True) == variant
        assert reply_schema_is_current(db, include_email=True,
                                       include_skill_config_receipts=True)
    assert path.read_bytes() == before
