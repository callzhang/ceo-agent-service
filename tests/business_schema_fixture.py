"""Frozen Business source declarations for migration and storage tests."""
from app.business_definitions import _definitions

def create_business_schema(db, *, include_candidates=False):
    table, indexes, triggers = _definitions(include_candidates=include_candidates)
    for sql in (table, *indexes, *triggers):
        db.execute(sql)
