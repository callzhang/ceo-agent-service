"""Deterministic, unactivated assembly of the Task/Run target contracts."""
from __future__ import annotations

from app.run_adoption_definitions import INDEXES as ADOPTION_INDEXES
from app.run_adoption_definitions import TABLES as ADOPTION_TABLES
from app.run_adoption_definitions import TRIGGERS as ADOPTION_TRIGGERS
from app.run_entity_definitions import run_entity_objects
from app.task_run_entity_definitions import task_run_entity_objects


def task_run_entity_assembly_objects() -> dict[str, str]:
    """Combine native Task, Run and adoption objects, rejecting name clashes."""
    objects = {}
    for family in (task_run_entity_objects(), run_entity_objects(), ADOPTION_TABLES,
                   ADOPTION_INDEXES,
                   {f"trg_adoption_{i}": sql for i, sql in enumerate(ADOPTION_TRIGGERS)}):
        overlap = objects.keys() & family.keys()
        if overlap:
            raise ValueError(f"duplicate assembled schema objects: {sorted(overlap)}")
        objects.update(family)
    for sql in __import__("app.run_adoption_definitions", fromlist=["receipt_schema_patch"]).receipt_schema_patch():
        name = sql.split()[2]
        if name in objects:
            raise ValueError(f"duplicate assembled schema object: {name}")
        objects[name] = sql
    return objects
