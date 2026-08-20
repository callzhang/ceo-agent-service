import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-local-components.sh"


def test_bootstrap_business_skill_selector_installs_only_business_skills(
    tmp_path: Path,
):
    env = _controlled_env(tmp_path, tmp_path / "bin")

    completed = subprocess.run(
        [str(SCRIPT), "--component", "ceo-business-skills", "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "done"
    assert [component["name"] for component in payload["components"]] == [
        "ceo-business-skills"
    ]
    assert "terminal-notifier" not in completed.stdout
    installed = tmp_path / ".agents" / "skills"
    assert len(list(installed.glob("ceo-*/SKILL.md"))) == 7
    assert not (tmp_path / ".codex" / "skills").exists()


def test_bootstrap_default_json_uses_controlled_components(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    env = _controlled_env(tmp_path, bin_dir)
    _write_executable(bin_dir / "terminal-notifier", "#!/bin/sh\nexit 0\n")
    _write_executable(bin_dir / "codex", "#!/bin/sh\nprintf 'codex-test 1.0\\n'\n")
    nvwa = tmp_path / ".agents" / "skills" / "nuwa" / "SKILL.md"
    nvwa.parent.mkdir(parents=True)
    nvwa.write_text("managed test skill\n", encoding="utf-8")

    completed = subprocess.run(
        [str(SCRIPT), "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "done"
    assert [
        (component["name"], component["status"])
        for component in payload["components"]
    ] == [
        ("terminal-notifier", "done"),
        ("codex", "done"),
        ("nvwa-skill", "done"),
        ("ceo-business-skills", "done"),
    ]


def test_bootstrap_business_skill_conflict_fails_closed(tmp_path: Path):
    target = tmp_path / ".agents" / "skills" / "ceo-calendar-invite" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("user-owned content\n", encoding="utf-8")
    env = _controlled_env(tmp_path, tmp_path / "bin")

    completed = subprocess.run(
        [str(SCRIPT), "--format", "json", "--component", "ceo-business-skills"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["components"][0]["name"] == "ceo-business-skills"
    assert "ceo-calendar-invite" in payload["components"][0]["detail"]
    assert target.read_text(encoding="utf-8") == "user-owned content\n"


@pytest.mark.parametrize(
    ("interpreter_content", "expected_detail"),
    [
        (None, "missing central Conda Python interpreter"),
        ("#!/bin/sh\nexit 42\n", "invalid central Conda Python interpreter"),
    ],
)
def test_bootstrap_business_skills_rejects_missing_or_invalid_conda_python(
    tmp_path: Path,
    interpreter_content: str | None,
    expected_detail: str,
):
    checkout, script = _isolated_checkout(tmp_path)
    conda_prefix = tmp_path / "conda"
    interpreter = conda_prefix / "bin" / "python"
    if interpreter_content is not None:
        _write_executable(interpreter, interpreter_content)
    env = _shell_only_env(tmp_path / "home", tmp_path / "bin")
    env["CEO_CONDA_PREFIX"] = str(conda_prefix)
    env.pop("CEO_PYTHON", None)

    completed = subprocess.run(
        [str(script), "--component", "ceo-business-skills"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert expected_detail in completed.stdout


@pytest.mark.parametrize(
    ("interpreter_content", "detail_prefix"),
    [
        (None, "missing central Conda Python interpreter"),
        ("#!/bin/sh\nexit 42\n", "invalid central Conda Python interpreter"),
    ],
)
def test_bootstrap_json_reports_unavailable_conda_python(
    tmp_path: Path,
    interpreter_content: str | None,
    detail_prefix: str,
):
    checkout, script = _isolated_checkout(tmp_path)
    conda_prefix = tmp_path / "conda"
    interpreter = conda_prefix / "bin" / "python"
    if interpreter_content is not None:
        _write_executable(interpreter, interpreter_content)
    env = _shell_only_env(tmp_path / "home", tmp_path / "bin")
    env["CEO_CONDA_PREFIX"] = str(conda_prefix)
    env.pop("CEO_PYTHON", None)

    completed = subprocess.run(
        [str(script), "--component", "ceo-business-skills", "--format", "json"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["components"] == [
        {
            "name": "ceo-business-skills",
            "status": "failed",
            "detail": f"{detail_prefix}: {interpreter}",
        }
    ]


def test_bootstrap_business_skills_invokes_exact_conda_python(tmp_path: Path):
    checkout, script = _isolated_checkout(tmp_path)
    log_path = tmp_path / "python-invocations.log"
    interpreter = tmp_path / "conda" / "bin" / "python"
    _write_executable(
        interpreter,
        """#!/bin/sh
printf '%s\n' "$*" >> "$BOOTSTRAP_PYTHON_LOG"
if [ "${1:-}" = "-c" ]; then
  exit 0
fi
cat >/dev/null
printf 'installed controlled skills\n'
""",
    )
    home = tmp_path / "home"
    env = _controlled_env(home, tmp_path / "bin")
    env["CEO_PYTHON"] = str(interpreter)
    env["BOOTSTRAP_PYTHON_LOG"] = str(log_path)

    completed = subprocess.run(
        [str(script), "--component", "ceo-business-skills"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    invocations = log_path.read_text(encoding="utf-8").splitlines()
    assert invocations[0].startswith("-c ")
    assert invocations[1] == f"- {home / '.agents' / 'skills'}"


def test_bootstrap_json_retains_complete_multiline_failure(tmp_path: Path):
    checkout, script = _isolated_checkout(tmp_path)
    interpreter = tmp_path / "conda" / "bin" / "python"
    _write_executable(
        interpreter,
        """#!/bin/sh
if [ "${1:-}" = "-c" ]; then
  exec "$CHECKOUT_TEST_REAL_PYTHON" "$@"
fi
cat >/dev/null
printf 'first diagnostic 中文 😀\nquote " backslash \\\\ tab\tcarriage\rbackspace\bformfeed\fcontrol:\001\nsecond diagnostic\n' >&2
exit 1
""",
    )
    env = _controlled_env(tmp_path / "home", tmp_path / "bin")
    env["CEO_PYTHON"] = str(interpreter)
    env["CHECKOUT_TEST_REAL_PYTHON"] = sys.executable

    completed = subprocess.run(
        [str(script), "--component", "ceo-business-skills", "--format", "json"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["components"][0]["detail"] == (
        'first diagnostic 中文 😀\nquote " backslash \\ tab\tcarriage\r'
        "backspace\bformfeed\fcontrol:\x01\nsecond diagnostic"
    )


def test_bootstrap_json_uses_configured_conda_python_without_python3_on_path(
    tmp_path: Path,
):
    env = _shell_only_env(tmp_path / "home", tmp_path / "bin")
    env["CEO_PYTHON"] = sys.executable

    completed = subprocess.run(
        [str(SCRIPT), "--component", "ceo-business-skills", "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "done"
    assert payload["components"][0]["status"] == "done"


def _controlled_env(home: Path, bin_dir: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    python3 = bin_dir / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["CEO_PYTHON"] = sys.executable
    env.pop("CODEX_INSTALL_COMMAND", None)
    env.pop("NVWA_SKILL_SOURCE", None)
    return env


def _shell_only_env(home: Path, bin_dir: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "bash").symlink_to("/bin/bash")
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    assert shutil.which("python3", path=str(bin_dir)) is None
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = str(bin_dir)
    env.pop("CEO_CONDA_PREFIX", None)
    env.pop("CEO_PYTHON", None)
    return env


def _isolated_checkout(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    script = checkout / "scripts" / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    return checkout, script


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
