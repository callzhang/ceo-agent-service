"""Copy a pre-built empty database instead of running ~1000 DDL statements per test.

A fresh `AutoReplyStore` or `EmailStore` costs 0.5-1 s (507 schema objects), and
most tests build at least one. The first construction in each pytest process
is built by the real constructor and kept as a template; every later
construction on a path that does not exist yet starts from a copy of it. The
real constructor still runs on the copy and validates the schema, so only the
DDL execution is skipped.
"""

import gc
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

_TEMPLATES: dict[str, Path] = {}
_DIRECTORY: Path | None = None
_BUILDING = False


def _template_directory() -> Path:
    global _DIRECTORY
    if _DIRECTORY is None:
        _DIRECTORY = Path(tempfile.mkdtemp(prefix="store-template."))
    return _DIRECTORY


def _copy_of_template(kind: str, build) -> Path | None:
    global _BUILDING
    if _BUILDING:
        return None
    template = _TEMPLATES.get(kind)
    if template is None:
        _BUILDING = True
        try:
            template = _template_directory() / f"{kind}.sqlite3"
            build(template)
        finally:
            _BUILDING = False
        _TEMPLATES[kind] = template
    return template


def _build(original, store_class, template: Path) -> None:
    from app.store import _close_idle_connections

    original(store_class.__new__(store_class), template)
    _close_idle_connections()
    gc.collect()
    with closing(sqlite3.connect(template)) as db:
        db.execute("pragma wal_checkpoint(truncate)")


def install(store_class, kind: str) -> None:
    """Make fresh-path construction of ``store_class`` start from the template."""
    original = store_class.__init__

    def init(self, path, *args, **kwargs):
        target = Path(path)
        if not args and not kwargs and not target.exists() and target.parent.is_dir():
            template = _copy_of_template(kind, lambda built: _build(original, store_class, built))
            if template is not None:
                shutil.copyfile(template, target)
        original(self, path, *args, **kwargs)

    store_class.__init__ = init
