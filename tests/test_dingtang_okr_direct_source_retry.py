from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.error import URLError

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "ci/shared-skills/dingtang-okr-review/scripts/dingteam_okr_direct_source.py"
)
SPEC = importlib.util.spec_from_file_location("dingteam_okr_direct_source", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_direct_source_retries_three_times_before_fourth_call_success(monkeypatch):
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true}'

    def flaky_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise URLError("temporary network failure")
        return Response()

    monkeypatch.setattr(MODULE, "urlopen", flaky_urlopen)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    assert MODULE._post("/read", {}, {}) == {"ok": True}
    assert calls == 4


def test_direct_source_stops_after_initial_call_and_three_retries(monkeypatch):
    calls = 0

    def always_fails(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise URLError("persistent network failure")

    monkeypatch.setattr(MODULE, "urlopen", always_fails)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        MODULE._post("/read", {}, {})
    assert calls == 4
