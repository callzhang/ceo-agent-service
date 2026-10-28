"""Frozen Store 26.1 / Email 54 catalog for explicit schema releases."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

_CATALOG = Path(__file__).with_name("schema_fixtures") / "store26_1_email54_catalog.json"
_SHA256 = "8d87a56c15429d971564a59de6b752d655500a46b407b0fef435180a1a0c882f"


@lru_cache(maxsize=2)
def store26_objects(*, email_ledger=True):
    """Return the immutable, digest-verified published source catalog."""
    raw = _CATALOG.read_bytes()
    decoded = json.loads(raw)
    canonical = json.dumps(decoded, ensure_ascii=False, separators=(",", ":")).encode()
    if sha256(canonical).hexdigest() != _SHA256:
        raise ValueError("Store 26.1 source catalog digest mismatch")
    objects = tuple(tuple(item) for item in decoded)
    if not email_ledger:
        objects = tuple(item for item in objects if item[1] != "email_schema_migrations")
    return objects


def install_store26_table(db, name):
    """Install one source table and its explicitly declared indexes and triggers."""
    items = [item for item in store26_objects() if item[2] == name]
    for kind in ("table", "index", "trigger"):
        for typ, _, _, sql in items:
            if typ == kind:
                db.execute(sql)
