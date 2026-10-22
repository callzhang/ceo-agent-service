"""Synthetic current-provider schema selection and caller behavior."""

from contextlib import closing
import sqlite3

import pytest

from app.action_email_schema_release import require_current_email_pair
from app.email_current_schema import create_current_email_non_action_schema
from app.provider_state_schema_release import provider_state_schema_is_current


def test_fresh_current_email_has_only_exact_provider_target():
    with closing(sqlite3.connect(":memory:")) as db:
        create_current_email_non_action_schema(db)
        names = {name for (name,) in db.execute("select name from sqlite_schema")}
        assert "provider_state" in names
        assert "email_provider_observations" not in names
        assert "email_provider_folder_observation_generations" not in names
        assert provider_state_schema_is_current(db)


def test_current_provider_shape_rejects_legacy_collision_and_missing_partial_index():
    with closing(sqlite3.connect(":memory:")) as db:
        create_current_email_non_action_schema(db)
        db.execute("create view email_provider_observations as select 1")
        assert not provider_state_schema_is_current(db)
        db.execute("drop view email_provider_observations")
        db.execute("drop index idx_provider_state_observation_folder")
        assert not provider_state_schema_is_current(db)


@pytest.mark.parametrize("old_name", (
    "email_provider_observations", "email_provider_folder_observation_generations",
    "provider_state",
))
@pytest.mark.parametrize("kind", ("table", "view"))
def test_unversioned_provider_name_is_never_empty_fresh(tmp_path, old_name, kind):
    path = tmp_path / "partial.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        declaration = f"create table {old_name}(id integer)" if kind == "table" else (
            f"create view {old_name} as select 1 as id"
        )
        db.execute(declaration)
    with pytest.raises(sqlite3.IntegrityError):
        require_current_email_pair(path, store_version="2026-10-14.1", email_version=52)
