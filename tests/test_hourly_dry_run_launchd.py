from pathlib import Path
import plistlib


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_local_service_script_runs_single_main_service():
    script = REPO_ROOT / "scripts" / "run-local-service.sh"

    content = script.read_text(encoding="utf-8")

    assert '${HOME}/.local/bin' in content
    assert 'export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"' in content
    assert 'export HOME="${CEO_SERVICE_HOME:-${HOME}}"' in content
    assert 'export PYTHONPATH="${PYTHONPATH:-.}"' in content
    assert 'export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}"' in content
    assert 'export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"' in content
    assert (
        'export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${repo_root}/data/dws-keychain}"'
        in content
    )
    assert 'export CEO_DING_ROBOT_NAME="${CEO_DING_ROBOT_NAME:-磊哥}"' in content
    assert "CEO_PRODUCER_INTERVAL_SECONDS" not in content
    assert "CEO_CONSUMER_POLL_INTERVAL_SECONDS" not in content
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS" not in content
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS" not in content
    assert "CEO_MEETING_SETTLE_SECONDS" not in content
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS" not in content
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS" not in content
    assert "CEO_OKR_LIVE_SOURCE_COMMAND" in content
    assert "scripts/dingteam_okr_live_source.py --user-id {user_id} --period-label {period_label}" in content
    assert "CEO_PRINCIPAL_NAME" not in content
    assert "CEO_MENTION_ALIASES" not in content
    assert "CEO_ASSISTANT_SIGNATURE" not in content
    assert "service" in content
    assert "--producer-interval-seconds" not in content
    assert "--consumer-poll-interval-seconds" not in content


def test_main_launch_agent_runs_single_keepalive_service():
    plist_path = REPO_ROOT / "launchd" / "com.ceo-agent-service.main.plist"

    with plist_path.open("rb") as file:
        plist = plistlib.load(file)

    assert plist["Label"] == "com.ceo-agent-service.main"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert "StartInterval" not in plist
    assert plist["StandardOutPath"].endswith("ceo-agent-service-main.out.log")
    assert plist["StandardErrorPath"].endswith("ceo-agent-service-main.err.log")
    command = plist["ProgramArguments"]
    assert command[:2] == ["/bin/zsh", "-lc"]
    assert " service " in command[2]
    assert "--producer-interval-seconds" not in command[2]
    assert "--consumer-poll-interval-seconds" not in command[2]
    assert "CEO_PRODUCER_INTERVAL_SECONDS" not in command[2]
    assert "CEO_CONSUMER_POLL_INTERVAL_SECONDS" not in command[2]
    assert "CEO_MEETING_PRODUCER_INTERVAL_SECONDS" not in command[2]
    assert "CEO_MEETING_CONSUMER_POLL_INTERVAL_SECONDS" not in command[2]
    assert "CEO_MEETING_SETTLE_SECONDS" not in command[2]
    assert "CEO_TASK_WORK_ITEM_INTERVAL_SECONDS" not in command[2]
    assert "CEO_TASK_DAILY_INTERVAL_SECONDS" not in command[2]
    assert "--host" in command[2]
    assert "--port" in command[2]
    assert "CEO_SERVICE_ROOT" in command[2]
    assert 'DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}"' in command[2]
    assert (
        'DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${service_root}/data/dws-keychain}"'
        in command[2]
    )
    assert 'CEO_DING_ROBOT_NAME="${CEO_DING_ROBOT_NAME:-磊哥}"' in command[2]
    assert "CEO_NOT_SEND_MESSAGE=0" in command[2]
    assert "CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1" in command[2]
    assert "CEO_OKR_LIVE_SOURCE_COMMAND" in command[2]
    assert "scripts/dingteam_okr_live_source.py --user-id {user_id} --period-label {period_label}" in command[2]
    env = plist["EnvironmentVariables"]
    assert "HOME" not in env
    assert "CODEX_HOME" not in env
    assert "DWS_DISABLE_KEYCHAIN" not in env
    assert "DWS_KEYCHAIN_DIR" not in env
    assert "CEO_WORKER_DB" not in env
    assert "CEO_WORKSPACE" not in env
    assert "CEO_WORK_PROFILE_PATH" not in env
    assert "CEO_MENTION_ALIASES" not in env
    assert "CEO_CURRENT_USER_DISPLAY_NAMES" not in env
    assert "CEO_ASSISTANT_SIGNATURE" not in env
    assert "CEO_HANDOFF_ACK" not in env
    assert "CEO_DING_ROBOT_NAME" not in env
    assert "/Users/principal" not in command[2]
    assert "run-reply-producer.sh" not in command[2]
    assert "run-reply-consumer.sh" not in command[2]
    assert "run-audit-web.sh" not in command[2]


def test_hourly_dry_run_install_script_installs_and_kickstarts_launch_agent():
    script = REPO_ROOT / "scripts" / "install-auto-reply-agents.sh"

    content = script.read_text(encoding="utf-8")

    assert "com.ceo-agent-service.main.plist" in content
    assert "com.ceo-agent-service.reply-producer" in content
    assert "com.ceo-agent-service.reply-consumer" in content
    assert "com.ceo-agent-service.audit-web" in content
    assert "PlistBuddy" not in content
    assert "legacy_label_prefix=\"com.$(id -un).ceo-agent-service\"" in content
    assert "${legacy_label_prefix}.reply-producer" in content
    assert "${legacy_label_prefix}.reply-consumer" in content
    assert "${legacy_label_prefix}.audit-web" in content
    assert "${legacy_label_prefix}.hourly-dry-run" in content
    assert "${legacy_label_prefix}.dry-run-consumer" in content
    assert "${legacy_label_prefix}.memory-flush" in content
    assert "launchctl bootout" in content
    assert "launchctl bootstrap" in content
    assert "launchctl kickstart -k" in content
    assert "mkdir -p" in content


def test_dws_auth_env_probe_reproduces_file_keychain_boundary_without_native_keychain():
    script = REPO_ROOT / "scripts" / "check-dws-auth-env.sh"

    content = script.read_text(encoding="utf-8")

    assert "list-unread-conversations" in content
    assert "default-user-auth" in content
    assert "forced-file-keychain" in content
    assert "wrong-file-keychain" not in content
    assert 'repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in content
    assert 'keychain_dir="${DWS_KEYCHAIN_DIR:-${repo_root}/data/dws-keychain}"' in content
    assert "DWS_DISABLE_KEYCHAIN=1" in content
    assert "DWS_KEYCHAIN_DIR=\"${keychain_dir}\"" in content
    assert "CEO_SERVICE_HOME" in content
    assert "--include-native-keychain" in content
