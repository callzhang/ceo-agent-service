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
    identity = ROOT / "scripts" / "create-wechat-reader-signing-identity.sh"

    subprocess.run(["bash", "-n", build], check=True)
    subprocess.run(["bash", "-n", install], check=True)
    subprocess.run(["bash", "-n", identity], check=True)
    build_text = build.read_text()
    install_text = install.read_text()
    assert "com.stardust.ceo-agent.wechat-reader" in build_text
    assert "--adhoc" in build_text
    assert "SIGNING_IDENTITY" in build_text
    assert "--options runtime" not in build_text
    assert "--allow-adhoc" in install_text
    assert "Signature=adhoc" in install_text


def test_local_signing_identity_is_code_signing_only_and_non_exportable():
    script = ROOT / "scripts" / "create-wechat-reader-signing-identity.sh"
    text = script.read_text()

    assert "basicConstraints=critical,CA:FALSE" in text
    assert "keyUsage=critical,digitalSignature" in text
    assert "extendedKeyUsage=critical,codeSigning" in text
    assert "Code signing : Yes" in text
    assert "add-trusted-cert -r trustRoot -p codeSign" in text
    assert "add-trusted-cert -r trustAsRoot" not in text
    assert "security import" in text
    assert " -x " in text
    assert "-T /usr/bin/codesign" in text
    assert " -A" not in text
    assert " -t agg" not in text
    assert " -f pkcs12" not in text
    assert "default-keychain -d user" in text
    assert "| sed -e 's/^[[:space:]" in text


def test_sender_app_has_dedicated_identity_and_fail_closed_packaging_scripts():
    plist_path = ROOT / "launchd" / "com.stardust.ceo-agent.wechat-sender.plist"
    build = ROOT / "scripts" / "build-wechat-sender-app.sh"
    install = ROOT / "scripts" / "install-wechat-sender-app.sh"
    helper = ROOT / "app" / "wechat" / "sender_helper.py"

    assert plist_path.exists()
    assert build.exists()
    assert install.exists()
    assert helper.exists()
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.stardust.ceo-agent.wechat-sender"
    assert plist["ProgramArguments"][0] == "__SENDER_EXECUTABLE__"
    assert "python" not in " ".join(plist["ProgramArguments"]).lower()
    assert plist["KeepAlive"] is True

    subprocess.run(["bash", "-n", build], check=True)
    subprocess.run(["bash", "-n", install], check=True)
    build_text = build.read_text()
    install_text = install.read_text()
    assert "com.stardust.ceo-agent.wechat-sender" in build_text
    assert str(helper) not in build_text
    assert "sender_helper.py" in build_text
    assert "--hidden-import" in build_text
    assert "ApplicationServices" in build_text
    assert "AppKit" in build_text
    assert "Quartz" in build_text
    assert "--options runtime" not in build_text
    assert "SIGNING_IDENTITY" in build_text
    assert "Signature=adhoc" in install_text
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "pyobjc-framework-ApplicationServices" in pyproject
    assert "pyobjc-framework-Quartz" in pyproject
    assert "pyobjc-framework-Cocoa" in pyproject
