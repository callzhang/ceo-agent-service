"""Unselected native source behavior for the 15 config families."""
import pytest

from tests.configs_source_fixtures import CONFIGS, capture, source_db

pytest_plugins = ("test_native_copy_lifecycle_harness",)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_fifteen_config_identities_and_118_fields(variant):
    source = capture()
    assert tuple(source["groups"]["configs"]) == CONFIGS
    definitions = source[variant]["tables"]
    assert sum(len(definitions[name]["columns"]) for name in CONFIGS) == 118
    with source_db(variant) as db:
        assert [name for name in CONFIGS if definitions[name]["identity"]["autoincrement"]] == [
            "managed_skill_revisions", "managed_skills", "runtime_skill_configs",
        ]
        assert [name for name in CONFIGS if definitions[name]["identity"]["without_rowid"]] == [
            "email_category_description_revisions", "email_model_mode_transitions",
            "email_model_promotion_configs",
        ]
        for name in CONFIGS:
            expected = [column["name"] for column in definitions[name]["columns"]]
            assert [column[1] for column in db.execute(f"pragma table_xinfo({name})")] == expected
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
