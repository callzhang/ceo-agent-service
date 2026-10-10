import pytest

from app.email_store import EmailPersistenceCorruption, EmailStore


def test_read_path_checks_schema_without_rescanning_all_durable_rows(
    tmp_path, monkeypatch
):
    path = tmp_path / "email.sqlite3"
    EmailStore(path)

    def fail_if_row_validation_runs(self, db):
        raise EmailPersistenceCorruption("durable row scan invoked")

    monkeypatch.setattr(
        EmailStore, "_validate_durable_rows", fail_if_row_validation_runs
    )

    with pytest.raises(EmailPersistenceCorruption, match="durable row scan invoked"):
        EmailStore(path)

    # Audit web has a separate process and sees a DB already owned and fully
    # validated by the email worker. It still checks schema shape on startup.
    EmailStore(path, validate_rows=False)
