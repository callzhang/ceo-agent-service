"""Read-only schema and aggregate inventory for the business-model review.

String references are source navigation hints, not a call graph or evidence of
writes. Only record types present in the supplied database are enumerated.
"""

import argparse
import ast
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3


EMAIL_PAIRS = {
    "class_sender": "message_sender",
    "class_subject": "message_subject",
    "class_received_at": "message_received_at",
    "class_folder": "message_folder",
    "class_rfc_message_id": "message_rfc_message_id",
}


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def routes(root):
    """Return literal Route path values from the frontend router."""
    source = Path(root) / "frontend/src/app/router.tsx"
    if not source.exists():
        return []
    route = re.compile(r'''<Route\b(?:"[^"]*"|'[^']*'|{[^}]*}|[^>{}"'])*?\bpath\s*=\s*(["'])(.*?)\1''', re.DOTALL)
    return sorted({match.group(2) for match in route.finditer(source.read_text(encoding="utf-8"))})


def references(root, tokens):
    """Find string occurrences as navigation hints, without returning source."""
    root = Path(root)
    result = {token: [] for token in tokens}
    for path in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        seen = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents.get(parent)
            function = parent.name if parent is not None else "<module>"
            for token in tokens:
                key = (token, function, node.lineno)
                if token in node.value and key not in seen:
                    result[token].append({"file": path.relative_to(root).as_posix(),
                                          "function": function, "line": node.lineno})
                    seen.add(key)
    for locations in result.values():
        locations.sort(key=lambda item: (item["file"], item["line"], item["function"]))
    return result


def _checks(db, columns):
    def has(table, *fields):
        return set(fields) <= columns.get(table, set())

    output = {"email_pairs": None, "business_aliases": None,
              "work_todo_import": None, "run_state_errors": None}
    if has("emails", "classification_id"):
        output["email_pairs"] = {}
        for left, right in EMAIL_PAIRS.items():
            if not has("emails", left, right):
                output["email_pairs"][left] = None
                continue
            rows, equal = db.execute(
                f"select count(*), coalesce(sum({_quote(left)} is {_quote(right)}), 0) "
                "from emails where classification_id is not null"
            ).fetchone()
            output["email_pairs"][left] = {"rows": rows, "equal": equal, "different": rows - equal}
    if has("tasks", "record_type", "parent_id"):
        rows, parents, multi, maximum = db.execute(
            "select coalesce(sum(n), 0), count(*), coalesce(sum(n > 1), 0), coalesce(max(n), 0) "
            "from (select parent_id, count(*) n from tasks "
            "where record_type = 'business_object_tasks' group by parent_id)"
        ).fetchone()
        output["business_aliases"] = {"rows": rows, "parents": parents,
                                      "multi_key_parents": multi, "max_keys": maximum}
    if has("business", "record_type", "import_task_id"):
        rows, mapped = db.execute(
            "select count(*), coalesce(sum(import_task_id is not null), 0) "
            "from business where record_type = 'work_todo'"
        ).fetchone()
        output["work_todo_import"] = {"rows": rows, "mapped": mapped}
    if has("runs", "id", "record_type", "parent_id", "structured_error_json"):
        rows, equal = db.execute(
            "select count(*), coalesce(sum(e.structured_error_json is p.structured_error_json), 0) "
            "from runs e join runs p on p.id = e.parent_id "
            "where e.record_type = 'agent_run_state_events'"
        ).fetchone()
        output["run_state_errors"] = {"rows": rows, "equal": equal}
    return output


def inventory(database, root):
    database, root = Path(database).resolve(), Path(root).resolve()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only = on")
        db.execute("begin")
        names = [row[0] for row in db.execute(
            "select name from sqlite_schema where type = 'table' "
            "and name not glob 'sqlite_*' order by name"
        )]
        tables, types, columns = {}, {}, {}
        for name in names:
            tables[name] = [dict(zip(("cid", "name", "affinity", "not_null", "default", "pk", "hidden"), row))
                            for row in db.execute(f"pragma table_xinfo({_quote(name)})")]
            columns[name] = {field["name"] for field in tables[name]}
            if "record_type" in columns[name]:
                types[name] = dict(db.execute(
                    f"select record_type, count(*) from {_quote(name)} group by record_type"
                ))
        facts = _checks(db, columns)
    tokens = sorted({kind for counts in types.values() for kind in counts if kind})
    return {"database_name": database.name, "tables": tables, "types": types,
            "checks": facts, "references": references(root, tokens), "routes": routes(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.database, args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
