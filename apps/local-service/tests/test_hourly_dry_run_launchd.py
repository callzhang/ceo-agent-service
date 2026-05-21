from pathlib import Path
import plistlib


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_hourly_dry_run_script_runs_single_dry_run_pass_with_lock():
    script = REPO_ROOT / "scripts" / "run-hourly-dry-run.sh"

    content = script.read_text(encoding="utf-8")

    assert "mkdir \"${lock_dir}\"" in content
    assert "trap 'rmdir \"${lock_dir}\"' EXIT" in content
    assert 'export CEO_DRY_RUN="1"' in content
    assert "CEO_LIVE_SEND_BLOCKERS_ACCEPTED" not in content
    assert "run-once" in content
    assert "--dry-run" in content
    assert "--db" in content
    assert "--workspace" in content


def test_hourly_dry_run_launch_agent_runs_every_thirty_minutes_without_keepalive():
    plist_path = REPO_ROOT / "launchd" / "com.derek.ceo-agent-service.hourly-dry-run.plist"

    with plist_path.open("rb") as file:
        plist = plistlib.load(file)

    assert plist["Label"] == "com.derek.ceo-agent-service.hourly-dry-run"
    assert plist["RunAtLoad"] is True
    assert plist["StartInterval"] == 1800
    assert "KeepAlive" not in plist
    assert plist["StandardOutPath"].endswith("/hourly-dry-run.out.log")
    assert plist["StandardErrorPath"].endswith("/hourly-dry-run.err.log")
    command = plist["ProgramArguments"]
    assert command[:2] == ["/bin/zsh", "-lc"]
    assert "mkdir \"$lock_dir\"" in command[2]
    assert "CEO_DRY_RUN=1" in command[2]
    assert "CEO_LIVE_SEND_BLOCKERS_ACCEPTED" not in command[2]
    assert "run-once" in command[2]
    assert "--dry-run" in command[2]


def test_hourly_dry_run_install_script_installs_and_kickstarts_launch_agent():
    script = REPO_ROOT / "scripts" / "install-hourly-dry-run-agent.sh"

    content = script.read_text(encoding="utf-8")

    assert "com.derek.ceo-agent-service.hourly-dry-run.plist" in content
    assert "launchctl bootout" in content
    assert "launchctl bootstrap" in content
    assert "launchctl kickstart -k" in content
    assert "mkdir -p" in content
