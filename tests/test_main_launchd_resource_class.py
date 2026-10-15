"""The HTTP Console service is an interactive launchd application dependency."""

from pathlib import Path
import plistlib


def test_main_http_console_is_declared_interactive():
    root = Path(__file__).resolve().parents[1]
    definition = plistlib.loads((root / "launchd/com.ceo-agent-service.main.plist").read_bytes())
    assert definition["Label"] == "com.ceo-agent-service.main"
    assert definition.get("ProcessType") == "Interactive"
    assert definition["SoftResourceLimits"]["NumberOfFiles"] == 4096
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True
