"""Generic activity validation and Skill-selected document destinations/readback."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agent_cli import build_role_server
from app.store import AutoReplyStore


@pytest.mark.parametrize("role", ["consumer", "audit"])
def test_service_activity_tool_uses_schema_only_email_validation(
    tmp_path, monkeypatch, role
):
    import app.email_store as email_store_module
    import app.service_activity as service_activity

    validations = []
    real_email_store = email_store_module.EmailStore
    db_path = tmp_path / "runtime-activity.sqlite3"
    store = AutoReplyStore(db_path)

    def track_email_store(path, **kwargs):
        validations.append(kwargs.get("validate_rows", True))
        return real_email_store(path, **kwargs)

    monkeypatch.setattr(email_store_module, "EmailStore", track_email_store)
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (
            store,
            SimpleNamespace(id=42, scheduled_for=datetime(2026, 10, 7, tzinfo=UTC)),
        ),
    )
    calls = []

    def collect(bound_store, mail_store, start, end, sources):
        calls.append(
            (bound_store, isinstance(mail_store, real_email_store), start, end, sources)
        )
        return {"validated": True}

    monkeypatch.setattr(service_activity, "collect_service_activity", collect)
    tool = (
        build_role_server(role, db_path=db_path)
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )
    result = tool("2026-10-06T00:00:00+00:00", "2026-10-07T00:00:00+00:00", ["emails"])

    assert result == {"validated": True}
    assert validations == [False]
    assert calls == [
        (
            store,
            True,
            datetime(2026, 10, 6, tzinfo=UTC),
            datetime(2026, 10, 7, tzinfo=UTC),
            ["emails"],
        )
    ]


@pytest.mark.parametrize(
    "start,end,sources,error",
    [
        ("2026-10-06T00:00:00", "2026-10-07T00:00:00+00:00", [], "aware"),
        ("2026-10-07T00:00:00+00:00", "2026-10-06T00:00:00+00:00", [], "start < end"),
        (
            "2026-10-06T00:00:00+00:00",
            "2026-10-07T00:00:00+00:00",
            ["unknown"],
            "unknown activity source",
        ),
    ],
)
def test_generic_activity_tool_rejects_invalid_interval_or_source(
    tmp_path, monkeypatch, start, end, sources, error
):
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (
            object(),
            SimpleNamespace(id=42, scheduled_for=datetime(2026, 10, 7, tzinfo=UTC)),
        ),
    )
    monkeypatch.setattr("app.email_store.EmailStore", lambda *args, **kwargs: object())
    tool = (
        build_role_server("audit", db_path=tmp_path / "activity.sqlite3")
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )
    with pytest.raises(ValueError, match=error):
        tool(start, end, sources)


def test_scheduled_activity_rejects_window_after_trigger_before_read(
    tmp_path, monkeypatch
):
    scheduled_for = datetime(2026, 10, 7, 13, tzinfo=UTC)
    monkeypatch.setattr(
        "app.agent_cli._bound_scheduled_run",
        lambda *args: (object(), SimpleNamespace(id=42, scheduled_for=scheduled_for)),
    )
    monkeypatch.setattr(
        "app.email_store.EmailStore",
        lambda *args, **kwargs: pytest.fail("provider access must not begin"),
    )
    tool = (
        build_role_server("consumer", db_path=tmp_path / "activity.sqlite3")
        ._tool_manager.get_tool("read_service_activity")
        .fn
    )

    with pytest.raises(ValueError, match="cannot end after scheduled_for"):
        tool(
            "2026-10-06T13:00:00+00:00",
            "2026-10-07T16:00:00+00:00",
            ["meetings"],
        )
