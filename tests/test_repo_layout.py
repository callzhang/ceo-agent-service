from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_uses_direct_app_package_layout():
    assert (REPO_ROOT / "app" / "__init__.py").is_file()
    assert (REPO_ROOT / "app" / "cli.py").is_file()
    assert (REPO_ROOT / "app" / "logo.png").is_file()
    assert (REPO_ROOT / "tests").is_dir()
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert not (REPO_ROOT / "apps" / "local-service").exists()
    assert not (REPO_ROOT / "apps" / "local-service" / "ceo_agent_service").exists()


def test_repo_root_helpers_resolve_repository_root():
    from app.config import repo_root
    from app import cli

    assert repo_root() == REPO_ROOT
    assert cli._repo_root() == REPO_ROOT
