"""One version shared by the CEO WeChat Reader app and the service that calls it.

The Reader is a separately built PyInstaller app; ``python -m app.deploy`` does
not rebuild it. Derek, 2026-10-28: a Reader built from older code must not keep
answering a newer service as if nothing changed (the unsupported-content fix in
``schema.py`` was live in the service while the installed Reader still rendered
the old text). So the version is not a number anyone has to remember to bump:
it is a digest of the Reader's own source modules. The service computes it from
its checkout; the build embeds the same digest into the app bundle; every IPC
reply carries the Reader's value and the client refuses a reply that differs.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Every module that ends up inside the Reader bundle, through reader_helper's
# imports. app.config is deliberately not here: the Reader reads only socket
# and path defaults from it, and a config change must not force a rebuild.
READER_SOURCE_MODULES = (
    "backend.py",
    "cipher.py",
    "discovery.py",
    "key_provider.py",
    "models.py",
    "reader.py",
    "reader_helper.py",
    "reader_ipc.py",
    "reader_version.py",
    "schema.py",
    "voice.py",
)
STAMP_FILE = "reader_version.txt"
UNSTAMPED = "unstamped"


def expected_reader_version(package_dir: str | Path | None = None) -> str:
    """Digest of the Reader source modules in ``package_dir`` (this package by default)."""
    root = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in READER_SOURCE_MODULES:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def running_reader_version() -> str:
    """Version of the Reader code this process is running.

    Inside the built app the sources are not present, so the build writes the
    digest to ``reader_version.txt`` next to the bundled modules. A bundle
    without that stamp predates the check and reports ``unstamped``, which
    never matches, so the service tells the operator to rebuild it.
    """
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if not bundle_dir:
        return expected_reader_version()
    try:
        return (Path(bundle_dir) / STAMP_FILE).read_text(encoding="utf-8").strip() or UNSTAMPED
    except OSError:
        return UNSTAMPED


def main() -> int:
    print(expected_reader_version())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
