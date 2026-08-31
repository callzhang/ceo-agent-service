from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_headless_source.py"


def test_service_entrypoint_disables_visible_browser_fallback():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "get_headers(allow_browser=False)" in source
    assert "headless" in source.casefold()
    assert "headful" not in source.casefold()
