import json
from pathlib import Path
import subprocess
import sys
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_uses_direct_app_package_layout():
    assert (REPO_ROOT / "app" / "__init__.py").is_file()
    assert (REPO_ROOT / "app" / "cli.py").is_file()
    assert (REPO_ROOT / "app" / "logo.png").is_file()
    assert (REPO_ROOT / "tests").is_dir()
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert not (REPO_ROOT / "apps" / "local-service").exists()
    assert not (REPO_ROOT / "apps" / "local-service" / "app").exists()


def test_repo_root_helpers_resolve_repository_root():
    from app.config import repo_root
    from app import cli

    assert repo_root() == REPO_ROOT
    assert cli._repo_root() == REPO_ROOT


def test_runtime_and_quality_contract_use_python_312_shared_conda():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

    assert project["project"]["requires-python"] == ">=3.12"
    assert "mcp[cli]==1.27.0" in project["project"]["dependencies"]
    assert "ruff>=0.15,<0.16" in project["project"]["optional-dependencies"]["dev"]
    assert "httpx2>=2.9,<3" in project["project"]["optional-dependencies"]["dev"]
    assert project["tool"]["ruff"]["target-version"] == "py312"
    assert package["scripts"]["test:local"].startswith(
        '${CEO_PYTHON:-$HOME/miniforge3/bin/python} -m pytest'
    )
    assert package["scripts"]["lint:local"].startswith(
        '${CEO_RUFF:-$HOME/miniforge3/bin/ruff} check'
    )
    assert package["scripts"]["test"].startswith("npm run lint:local &&")
    assert (REPO_ROOT / ".github" / "workflows" / "quality.yml").is_file()


def test_cli_import_is_warning_free_on_python_312():
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-m", "app.cli", "quality-check", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
