import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from wechat_key_probe import candidate_report  # noqa: E402


def test_probe_report_contains_fingerprint_not_key():
    report = candidate_report(b"0123456789abcdef", valid=True, schema=["Message"])
    encoded = json.dumps(report)
    assert "0123456789abcdef" not in encoded
    assert report["key_fingerprint"].startswith("sha256:")
    assert report["valid"] is True
    assert report["schema"] == ["Message"]
