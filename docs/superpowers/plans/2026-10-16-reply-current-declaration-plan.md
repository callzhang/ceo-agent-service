# Explicit current Reply declaration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Let existing Reply declaration selectors explicitly recognize the current Skill receipt scaffold without changing historical selector defaults.

**Architecture:** Forward the existing `include_skill_config_receipts` option to the existing complete action schema recognizer. Current callers will select it explicitly in their separate closure proposals; historical publication and native source readers retain their existing defaults. No table, field, SQL declaration, policy or runtime route changes.

**Tech Stack:** Python 3.13, SQLite, pytest.

---

### Task 1: exact selector extension

**Files:** modify `app/action_storage.py` two named functions only; create `tests/test_reply_current_declaration.py`; append one behavior paragraph in `docs/architecture.md` and `docs/runtime-mechanism.md`.

- [x] Write the complete regression below and run it against the unchanged selectors. Expected failure: unexpected keyword `include_skill_config_receipts`.
- [x] Replace only the two functions with the complete code below. Run the new regression; the existing Reply declaration/native scalar tests passed in the root disposable preflight.
- [x] Document the unselected explicit option; independently review SPEC then QUALITY; commit own files only.

```python
def validated_reply_variant(db: sqlite3.Connection, *, allow_missing_objects=False,
                            include_email=False, include_skill_config_receipts=False) -> str:
    """Select one complete canonical E declaration, including its FK and indexes."""
    matches = [
        variant for variant in REPLY_VARIANTS
        if action_schema_is_current(
            db, allow_missing_objects=allow_missing_objects,
            include_reply=True, reply_variant=variant, include_email=include_email,
            include_skill_config_receipts=include_skill_config_receipts,
        )
    ]
    if len(matches) != 1:
        raise sqlite3.IntegrityError("unsupported or mixed Attempt/Memory action schema")
    return matches[0]


def reply_schema_is_current(db: sqlite3.Connection, *, include_email=False,
                            include_skill_config_receipts=False) -> bool:
    """Recognize the selected complete reply declaration without altering the DB."""
    return any(action_schema_is_current(db, include_reply=True, reply_variant=variant,
                                        include_email=include_email,
                                        include_skill_config_receipts=include_skill_config_receipts)
               for variant in REPLY_VARIANTS)
```

### Task 2: regression source

```python
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
```

Run in an owned temporary copied app/tests tree including `tests/conftest.py`, with the pinned Python and `PYTHONDONTWRITEBYTECODE=1`:

```sh
python -m pytest -q -p no:cacheprovider tests/test_reply_current_declaration.py tests/test_reply_attempt_action_storage.py tests/test_action_reply_schema_release.py tests/test_native_scalar_binding.py
```

Each pytest basetemp is inside the owned temporary directory. No whole suite or production DB run. Root preflight must verify unchanged AST for every other top-level callable. Rejected shapes and read-only byte equality are part of the tests. This prerequisite does not claim current caller closure or final schema acceptance.

Accepted implementation68a13749: root360PASS/6SKIP6.68s, SPEC360PASS/6SKIP7.04s and unchanged155nodes; QUALITY360PASS/6SKIP7.28s plus741 schema/read-only matrix cases. Exact evidence: ../specs/2026-10-16-reply-current-declaration-acceptance.json. No current State caller or publication selection.
