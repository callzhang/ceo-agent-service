import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCRIPT = REPO_ROOT / "scripts" / "run-local-service.sh"


def _prepare_service_fixture(
    tmp_path: Path,
    *,
    sdk_available: bool,
    channel_sdk_version: str = "1.2.0",
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    app_dir = repo / "app"
    bin_dir = repo / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    app_dir.mkdir()
    bin_dir.mkdir(parents=True)
    shutil.copy2(SOURCE_SCRIPT, scripts_dir / SOURCE_SCRIPT.name)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "config.py").write_text(
        """
import os


def feishu_enabled():
    return os.getenv("CEO_FEISHU_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def feishu_app_id():
    if os.getenv("CEO_TEST_FAIL_ON_CREDENTIAL_LOOKUP") == "1":
        raise RuntimeError("credential lookup must not run while disabled")
    return os.getenv("CEO_FEISHU_APP_ID", "").strip()


def feishu_app_secret():
    if os.getenv("CEO_TEST_FAIL_ON_CREDENTIAL_LOOKUP") == "1":
        raise RuntimeError("credential lookup must not run while disabled")
    return os.getenv("CEO_FEISHU_APP_SECRET", "").strip()
""".lstrip(),
        encoding="utf-8",
    )
    module_body = "SDK_AVAILABLE = True\n" if sdk_available else "raise ImportError('missing')\n"
    (repo / "lark_channel.py").write_text(module_body, encoding="utf-8")
    (repo / "lark_oapi.py").write_text(module_body, encoding="utf-8")
    if sdk_available:
        distributions = (
            ("lark-channel-sdk", channel_sdk_version),
            ("lark-oapi", "1.7.1"),
        )
        for distribution_name, version in distributions:
            metadata_dir = repo / (
                f"{distribution_name.replace('-', '_')}-{version}.dist-info"
            )
            metadata_dir.mkdir()
            (metadata_dir / "METADATA").write_text(
                "Metadata-Version: 2.1\n"
                f"Name: {distribution_name}\n"
                f"Version: {version}\n",
                encoding="utf-8",
            )
    (bin_dir / "python").symlink_to(sys.executable)
    args_file = repo / "ceo-agent-args.txt"
    fake_agent = bin_dir / "ceo-agent"
    fake_agent.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"${CEO_TEST_ARGS_FILE}\"\n",
        encoding="utf-8",
    )
    fake_agent.chmod(0o755)
    return repo, args_file


def _run_fixture(repo: Path, args_file: Path, **updates: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "CEO_MAX_BATCHES": "1",
            "CEO_TEST_ARGS_FILE": str(args_file),
            "PYTHONPATH": str(repo),
        }
    )
    env.update(updates)
    return subprocess.run(
        ["/bin/bash", str(repo / "scripts" / SOURCE_SCRIPT.name)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_feishu_preflight_shell_has_valid_syntax():
    result = subprocess.run(
        ["/bin/bash", "-n", str(SOURCE_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_disabled_feishu_skips_sdk_and_credential_checks(tmp_path):
    repo, args_file = _prepare_service_fixture(tmp_path, sdk_available=False)

    result = _run_fixture(
        repo,
        args_file,
        CEO_FEISHU_ENABLED="0",
        CEO_TEST_FAIL_ON_CREDENTIAL_LOOKUP="1",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert args_file.read_text(encoding="utf-8").splitlines() == [
        "run-once",
        "--max-batches",
        "1",
    ]


def test_enabled_feishu_preflight_reports_only_configured_status(tmp_path):
    repo, args_file = _prepare_service_fixture(tmp_path, sdk_available=True)

    result = _run_fixture(
        repo,
        args_file,
        CEO_FEISHU_ENABLED="1",
        CEO_FEISHU_APP_ID="cli_sensitive_app_id",
        CEO_FEISHU_APP_SECRET="sensitive-app-secret",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == (
        "feishu_preflight sdk=configured app_id=configured "
        "app_secret=configured\n"
    )
    combined_output = result.stdout + result.stderr
    assert "cli_sensitive_app_id" not in combined_output
    assert "sensitive-app-secret" not in combined_output
    assert args_file.exists()


def test_enabled_feishu_preflight_fails_closed_when_configuration_is_missing(
    tmp_path,
):
    repo, args_file = _prepare_service_fixture(tmp_path, sdk_available=False)

    result = _run_fixture(
        repo,
        args_file,
        CEO_FEISHU_ENABLED="1",
        CEO_FEISHU_APP_ID="",
        CEO_FEISHU_APP_SECRET="",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "feishu_preflight sdk=missing app_id=missing app_secret=missing\n"
    )
    assert not args_file.exists()


def test_enabled_feishu_preflight_rejects_outdated_channel_sdk(tmp_path):
    repo, args_file = _prepare_service_fixture(
        tmp_path,
        sdk_available=True,
        channel_sdk_version="1.1.0",
    )

    result = _run_fixture(
        repo,
        args_file,
        CEO_FEISHU_ENABLED="1",
        CEO_FEISHU_APP_ID="cli_configured",
        CEO_FEISHU_APP_SECRET="configured-secret",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "feishu_preflight sdk=missing app_id=configured app_secret=configured\n"
    )
    assert "configured-secret" not in result.stderr
    assert not args_file.exists()
