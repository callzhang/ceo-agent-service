"""Local structural check for DingTalk JSONML before it is written.

The document service rejects a whole overwrite when a single node is
malformed (2026-09-26: a leaf held an object, so the write failed with
`content at position [0] must be string or element array, got object` and the
run ended without publishing). `dws doc +update --dry-run` does not validate
the body, so this check runs on the rendered files instead.

A node is `[tag, attrs?, *children]`: `tag` is a string, `attrs` is an object
that may only sit at index 1, and every child is a string or another node.
A leaf (`["span", {"data-type": "leaf"}, ...]`) holds strings only.
"""

import argparse
import json
import sys
from pathlib import Path

# Containers whose children are always nodes. Bare text is allowed only in
# inline containers (a leaf), so a string spliced into one of these is a
# rendering bug, e.g. a JSON string spread character by character.
BLOCK_CONTAINERS = {"root", "table", "tr", "tc"}


def check_node(node, path: str = "$") -> list[str]:
    if isinstance(node, str):
        return []
    if not isinstance(node, list):
        return [f"{path}: node must be a string or an array, got {type(node).__name__}"]
    if not node or not isinstance(node[0], str):
        return [f"{path}: node must start with a string tag"]
    errors: list[str] = []
    if node[0] == "root" and path != "$":
        errors.append(f"{path}: a root node cannot be nested")
    is_leaf = (
        node[0] == "span"
        and len(node) > 1
        and isinstance(node[1], dict)
        and node[1].get("data-type") == "leaf"
    )
    for index, child in enumerate(node[1:], start=1):
        child_path = f"{path}[{index}]"
        if index == 1 and isinstance(child, dict):
            continue
        if isinstance(child, dict):
            errors.append(
                f"{child_path}: content must be string or element array, got object "
                f"(inside {node[0]!r}; keys {sorted(child)[:4]})"
            )
        elif isinstance(child, str) and node[0] in BLOCK_CONTAINERS:
            errors.append(f"{child_path}: {node[0]!r} holds nodes, got a bare string")
        elif is_leaf and not isinstance(child, str):
            errors.append(f"{child_path}: a leaf holds text only, got {type(child).__name__}")
        else:
            errors.extend(check_node(child, child_path))
    return errors


def check_document(document) -> list[str]:
    if not isinstance(document, list) or not document or document[0] != "root":
        return ["$: an overwrite body must be an array starting with \"root\""]
    return check_node(document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    failed = False
    for name in args.files:
        errors = check_document(json.loads(Path(name).read_text(encoding="utf-8")))
        for error in errors[:20]:
            print(f"{name}: {error}", file=sys.stderr)
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
