"""Synthetic current-provider schema selection and caller behavior."""

from contextlib import closing
import sqlite3


from app.email_current_schema import create_current_email_non_action_schema
from app.provider_state_schema_current import provider_state_schema_is_current


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


