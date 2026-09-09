from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "skills" / "ceo-wechat"


def _load(name: str):
    path = SKILL_ROOT / "scripts" / f"{name}.py"
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
