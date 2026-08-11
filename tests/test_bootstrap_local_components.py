import json
import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-local-components.sh"


def test_bootstrap_business_skill_selector_installs_only_business_skills(
    tmp_path: Path,
):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

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


def test_bootstrap_default_json_reports_business_skills(tmp_path: Path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    completed = subprocess.run(
        [str(SCRIPT), "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(completed.stdout)
    names = [component["name"] for component in payload["components"]]
    assert "ceo-business-skills" in names
    assert names[-1] == "ceo-business-skills"


def test_bootstrap_business_skill_conflict_fails_closed(tmp_path: Path):
    target = tmp_path / ".agents" / "skills" / "ceo-calendar-invite" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("user-owned content\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

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
