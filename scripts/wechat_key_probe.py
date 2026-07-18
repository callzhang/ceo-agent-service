#!/usr/bin/env python3
"""WeChat 4.1.x key/schema feasibility gate (read-only, fingerprint-only report).

The passphrase itself is captured out-of-band once via the shadow-copy +
CCKeyDerivationPBKDF flow (see the plan's Task 5 correction block and
~/wx_read_toolkit). This diagnostic loads the persisted passphrase, validates it
against message_0.db's page-1 HMAC, reads the plaintext schema, and emits a
report that contains a key FINGERPRINT (never the key) plus the schema.

Usage:
  python scripts/wechat_key_probe.py --passphrase-file ~/.config/wx_read/passphrase.hex \
      --account-db-dir "$HOME/Library/.../db_storage" --report data/wechat-key-probe-report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def candidate_report(key: bytes, *, valid: bool, schema: list[str]) -> dict:
    return {
        "key_fingerprint": "sha256:" + hashlib.sha256(key).hexdigest(),
        "valid": valid,
        "schema": schema,
    }


def _probe(passphrase_file: Path, db_dir: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.wechat.cipher import validates
    from app.wechat.backend import WcdbReaderBackend
    import tempfile

    passphrase = bytes.fromhex(passphrase_file.read_text().strip())
    message0 = db_dir / "message" / "message_0.db"
    if not message0.exists():
        return {"valid": False, "reason": "message_0.db not found", "schema": []}
    valid = validates(message0, passphrase)
    schema: list[str] = []
    if valid:
        with tempfile.TemporaryDirectory() as tmp:
            backend = WcdbReaderBackend(tmp)
            schema = backend.probe(str(db_dir), passphrase)
    return candidate_report(passphrase, valid=valid, schema=schema[:50])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passphrase-file", required=True)
    parser.add_argument("--account-db-dir", required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    report = _probe(Path(args.passphrase_file).expanduser(), Path(args.account_db_dir).expanduser())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text)
    print(text)
    return 0 if report.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
