import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "recover_failed_okr_reviews.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "recover_failed_okr_reviews",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_user_id_uses_fallback_source_error_detail():
    module = load_module()

    user_id = module._extract_user_id(
        "blocked_unrecoverable_external_auth: DingTeam OKR page is not logged in",
        2691,
        (
            "dws command failed with exit code 1; "
            "command=/repo/scripts/dingteam_okr_live_source.py "
            "--user-id 0125200555401244265 --period-label 2026 Q2"
        ),
    )

    assert user_id == "0125200555401244265"


def test_extract_user_id_reports_attempt_when_missing():
    module = load_module()

    with pytest.raises(RuntimeError, match="missing OKR user id in attempt 2691"):
        module._extract_user_id("blocked", 2691, "no source command")
