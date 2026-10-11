from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.business_skills import bundled_business_skills_root

SKILLS_ROOT = bundled_business_skills_root()


ROOT = Path(__file__).parents[1]
SKILL_ROOT = SKILLS_ROOT / "ceo-wechat"


def _load(name: str, skill_root: Path = SKILL_ROOT):
    path = skill_root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_ceo_wechat_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wechat_skill_declares_executable_reader_and_sender_entrypoints():
    manifest = json.loads((SKILL_ROOT / "capability.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "ceo-wechat"
    assert manifest["runtime"]["reader"]["entrypoint"] == "scripts/reader.py"
    assert manifest["runtime"]["sender"]["entrypoint"] == "scripts/sender.py"
    assert manifest["runtime"]["reader"]["trusted_app"] == "CEO WeChat Reader.app"
    assert manifest["runtime"]["sender"]["trusted_app"] == "CEO WeChat Sender.app"
    assert "produce-once" in manifest["runtime"]["reader"]["operations"]


def test_wechat_skill_scripts_delegate_to_the_service_ipc_cli():
    reader = _load("reader")
    sender = _load("sender")

    assert reader.build_command(["status"])[1:] == ["-m", "app.wechat.cli", "status"]
    produce = reader.build_parser().parse_args(
        ["produce-once", "--db", "/tmp/production.sqlite3"]
    )
    assert produce.operation == "produce-once"
    assert reader.build_command(
        [produce.operation, "--db", produce.db]
    )[1:] == [
        "-m",
        "app.wechat.cli",
        "produce-once",
        "--db",
        "/tmp/production.sqlite3",
    ]
    assert sender.build_command(["approve", "--id", "8"])[1:] == ["-m", "app.wechat.cli", "approve", "--id", "8"]


@pytest.mark.parametrize("skill_root", [SKILL_ROOT, ROOT / "ci/shared-skills/ceo-wechat"])
@pytest.mark.parametrize(
    "arguments",
    [["status"], ["read-recent", "--target-id", "filehelper"], ["produce-once"]],
)
def test_reader_requires_the_invocation_database_before_starting_ipc(
    skill_root, arguments, monkeypatch,
):
    reader = _load("reader", skill_root)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reader.subprocess, "run", run)

    with pytest.raises(SystemExit) as exc:
        reader.main(arguments)

    assert exc.value.code == 2
    assert calls == []


@pytest.mark.parametrize("skill_root", [SKILL_ROOT, ROOT / "ci/shared-skills/ceo-wechat"])
@pytest.mark.parametrize(
    ("operation", "options", "forwarded_options"),
    [
        ("status", [], []),
        (
            "read-recent",
            ["--target-id", "filehelper", "--limit", "10", "--include-text"],
            ["--target-id", "filehelper", "--type", "direct", "--limit", "10", "--include-text"],
        ),
        ("produce-once", [], []),
    ],
)
def test_reader_forwards_the_explicit_database_and_ipc_result(
    skill_root, operation, options, forwarded_options, tmp_path, monkeypatch,
):
    reader = _load("reader", skill_root)
    database = str(tmp_path / "service state" / "production.sqlite3")
    checkout = tmp_path / "different-checkout"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(reader, "repository_root", lambda: checkout)
    monkeypatch.setattr(reader.subprocess, "run", run)

    result = reader.main([operation, "--db", database, *options])

    assert result == 7
    assert calls == [
        (
            reader.build_command([operation, "--db", database, *forwarded_options]),
            {"cwd": checkout, "check": False},
        )
    ]
