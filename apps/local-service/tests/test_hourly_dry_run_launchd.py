from pathlib import Path
import plistlib


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_hourly_dry_run_script_runs_single_producer_pass_with_lock():
    script = REPO_ROOT / "scripts" / "run-reply-producer.sh"

    content = script.read_text(encoding="utf-8")

    assert "mkdir \"${lock_dir}\"" in content
    assert "kill -0 \"$(cat \"${lock_dir}/pid\")\"" in content
    assert "rm -rf \"${lock_dir}\"" in content
    assert "trap 'rm -rf \"${lock_dir}\"' EXIT" in content
    assert '${HOME}/.local/bin' in content
    assert 'export CEO_NOT_SEND_MESSAGE="0"' in content
    assert 'export CEO_LIVE_SEND_BLOCKERS_ACCEPTED="1"' in content
    assert 'export CEO_MENTION_ALIASES="${CEO_MENTION_ALIASES:-@Derek Zen,@磊哥}"' in content
    assert 'export CEO_CURRENT_USER_DISPLAY_NAMES="${CEO_CURRENT_USER_DISPLAY_NAMES:-磊哥,Derek Zen,Derek,Lei Zhang}"' in content
    assert 'export CEO_ASSISTANT_SIGNATURE="${CEO_ASSISTANT_SIGNATURE:-（by磊哥分身）}"' in content
    assert "produce-once" in content
    assert "run-once" not in content
    assert "--not-send-message" not in content
    assert "--db" in content
    assert "--workspace" in content


def test_reply_producer_launch_agent_runs_every_five_minutes_without_keepalive():
    plist_path = REPO_ROOT / "launchd" / "com.derek.ceo-agent-service.reply-producer.plist"

    with plist_path.open("rb") as file:
        plist = plistlib.load(file)

    assert plist["Label"] == "com.derek.ceo-agent-service.reply-producer"
    assert plist["RunAtLoad"] is True
    assert plist["StartInterval"] == 300
    assert "KeepAlive" not in plist
    assert plist["StandardOutPath"].endswith("/reply-producer.out.log")
    assert plist["StandardErrorPath"].endswith("/reply-producer.err.log")
    command = plist["ProgramArguments"]
    assert command[:2] == ["/bin/zsh", "-lc"]
    assert "mkdir \"$lock_dir\"" in command[2]
    assert "CEO_NOT_SEND_MESSAGE=0" in command[2]
    assert "CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1" in command[2]
    env = plist["EnvironmentVariables"]
    assert env["CEO_MENTION_ALIASES"] == "@Derek Zen,@磊哥"
    assert env["CEO_CURRENT_USER_DISPLAY_NAMES"] == "磊哥,Derek Zen,Derek,Lei Zhang"
    assert env["CEO_ASSISTANT_SIGNATURE"] == "（by磊哥分身）"
    assert env["CEO_HANDOFF_ACK"] == "我让磊哥本人看一下。（by磊哥分身）"
    assert env["CEO_DING_ROBOT_NAME"] == "磊哥"
    assert "/Users/derek/.local/bin" in command[2]
    assert "kill -0" in command[2]
    assert "rm -rf \"$lock_dir\"" in command[2]
    assert "produce-once" in command[2]
    assert "run-once" not in command[2]
    assert "--not-send-message" not in command[2]


def test_reply_consumer_launch_agent_runs_as_live_keepalive_consumer():
    plist_path = REPO_ROOT / "launchd" / "com.derek.ceo-agent-service.reply-consumer.plist"

    with plist_path.open("rb") as file:
        plist = plistlib.load(file)

    assert plist["Label"] == "com.derek.ceo-agent-service.reply-consumer"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    command = plist["ProgramArguments"]
    assert command[:2] == ["/bin/zsh", "-lc"]
    assert "consume" in command[2]
    assert "CEO_NOT_SEND_MESSAGE=0" in command[2]
    assert "CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1" in command[2]
    env = plist["EnvironmentVariables"]
    assert env["CEO_MENTION_ALIASES"] == "@Derek Zen,@磊哥"
    assert env["CEO_CURRENT_USER_DISPLAY_NAMES"] == "磊哥,Derek Zen,Derek,Lei Zhang"
    assert env["CEO_ASSISTANT_SIGNATURE"] == "（by磊哥分身）"
    assert env["CEO_HANDOFF_ACK"] == "我让磊哥本人看一下。（by磊哥分身）"
    assert env["CEO_DING_ROBOT_NAME"] == "磊哥"
    assert "--not-send-message" not in command[2]
    assert "--poll-interval-seconds 10" in command[2]


def test_hourly_dry_run_install_script_installs_and_kickstarts_launch_agent():
    script = REPO_ROOT / "scripts" / "install-auto-reply-agents.sh"

    content = script.read_text(encoding="utf-8")

    assert "com.derek.ceo-agent-service.reply-producer.plist" in content
    assert "com.derek.ceo-agent-service.reply-consumer.plist" in content
    assert "com.derek.ceo-agent-service.hourly-dry-run" in content
    assert "com.derek.ceo-agent-service.dry-run-consumer" in content
    assert "launchctl bootout" in content
    assert "launchctl bootstrap" in content
    assert "launchctl kickstart -k" in content
    assert "mkdir -p" in content
