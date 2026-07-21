import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_reader_launch_agent_has_dedicated_identity_and_executable_placeholder():
    path = ROOT / "launchd" / "com.stardust.ceo-agent.wechat-reader.plist"
    with path.open("rb") as handle:
        plist = plistlib.load(handle)

    assert plist["Label"] == "com.stardust.ceo-agent.wechat-reader"
    assert plist["ProgramArguments"][0] == "__READER_EXECUTABLE__"
    assert "python" not in " ".join(plist["ProgramArguments"]).lower()
    assert plist["KeepAlive"] is True


def test_build_and_install_scripts_are_valid_and_fail_closed_on_adhoc_signing():
    build = ROOT / "scripts" / "build-wechat-reader-app.sh"
    install = ROOT / "scripts" / "install-wechat-reader-app.sh"

    subprocess.run(["bash", "-n", build], check=True)
    subprocess.run(["bash", "-n", install], check=True)
    build_text = build.read_text()
    install_text = install.read_text()
    assert "com.stardust.ceo-agent.wechat-reader" in build_text
    assert "--adhoc" in build_text
    assert "SIGNING_IDENTITY" in build_text
    assert "--allow-adhoc" in install_text
    assert "Signature=adhoc" in install_text
