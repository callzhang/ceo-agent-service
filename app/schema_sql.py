"""Deterministic serialization for complete SQLite schema declarations."""
from __future__ import annotations


_OBJECT_ORDER = {"table": 0, "index": 1, "trigger": 2}


def serialize_schema_sql(objects: dict[str, str]) -> str:
    """Serialize declared SQLite objects as a directly executable script.

    Tables must precede indexes and triggers so the frozen artifact can be
    executed as a standalone schema script. SQLite permits references from
    triggers to tables declared later, but ordering them last avoids relying
    on that behavior. Existing trigger bodies are kept intact; the final
    semicolon terminates the full CREATE TRIGGER statement.
    """
    ordered = []
    for declared_name, sql in objects.items():
        tokens = sql.strip().split()
        words = [token.upper() for token in tokens]
        if words[:2] == ["CREATE", "TABLE"]:
            kind, actual_name = "table", tokens[2]
        elif words[:3] == ["CREATE", "UNIQUE", "INDEX"]:
            kind, actual_name = "index", tokens[3]
        elif words[:2] == ["CREATE", "INDEX"]:
            kind, actual_name = "index", tokens[2]
        elif words[:2] == ["CREATE", "TRIGGER"]:
            kind, actual_name = "trigger", tokens[2]
        else:
            raise ValueError(f"unsupported SQLite schema declaration: {tokens[:4]}")
        actual_name = actual_name.split("(", 1)[0]
        if len(actual_name) >= 2 and actual_name[0] == actual_name[-1] and actual_name[0] in {'"', "'", "`"}:
            actual_name = actual_name[1:-1].replace(actual_name[0] * 2, actual_name[0])
        elif actual_name.startswith("[") and actual_name.endswith("]"):
            actual_name = actual_name[1:-1]
        if declared_name != actual_name:
            raise ValueError(f"declared object name mismatch: {declared_name} != {actual_name}")
        statement = sql.strip().rstrip(";").rstrip()
        ordered.append((_OBJECT_ORDER[kind], declared_name, statement))
    # Python's sort is stable: keep declaration order inside each class. The
    # owner map supplies parent-before-child tables where it has that order.
    ordered.sort(key=lambda row: row[0])
    return "\n\n".join(f"{statement};" for _kind, _name, statement in ordered) + "\n"
