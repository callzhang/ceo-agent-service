from contextlib import closing
import sqlite3

from app.configs_definitions import CONFIG_FIELDS, CONFIG_PROJECTIONS
from app.configs_storage import create_configs_schema
from tests.configs_source_fixtures import capture


def test_seven_declared_fields_and_order():
    actual = capture()["actual"]["tables"]
    assert len(CONFIG_FIELDS) == 7
    assert sum(map(len, CONFIG_FIELDS.values())) == 73
    with closing(sqlite3.connect(":memory:")) as db:
        create_configs_schema(db)
        assert [row[1] for row in db.execute("pragma table_xinfo(configs)")
                if row[6] == 0][:4] == ["id", "record_type", "legacy_key", "payload_json"]
        for kind, fields in CONFIG_FIELDS.items():
            assert fields == tuple(c["name"] for c in actual[kind]["columns"])
            assert tuple(col[0] for col in db.execute(CONFIG_PROJECTIONS[kind]).description) == fields
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
