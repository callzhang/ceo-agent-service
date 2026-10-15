import pytest

from app import audit_web


def test_queue_stage_timing_preserves_result_and_only_reports_slow_reader(monkeypatch, capsys):
    result = {"pending": 3, "error": "private business detail"}
    clock = iter([0.0, 0.999, 1.0, 3.0])
    monkeypatch.setattr(audit_web.time, "monotonic", lambda: next(clock))

    def queue_reader(db):
        assert db is result
        return result

    assert audit_web._read_status_queue(queue_reader, result) is result
    assert capsys.readouterr().err == ""
    assert audit_web._read_status_queue(queue_reader, result) is result
    line = capsys.readouterr().err
    assert "slow console queue reader=queue_reader elapsed_seconds=2.000" in line
    assert "private business detail" not in line


def test_queue_stage_timing_preserves_reader_exception(monkeypatch, capsys):
    error = ValueError("private reader failure")
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(audit_web.time, "monotonic", lambda: next(clock))

    def queue_reader(db):
        raise error

    with pytest.raises(ValueError) as caught:
        audit_web._read_status_queue(queue_reader, object())
    assert caught.value is error
    assert "elapsed_seconds=1.000" in capsys.readouterr().err
