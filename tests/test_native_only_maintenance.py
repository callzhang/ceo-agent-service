"""The final schema retires native-copy cleanup; explicit input targets remain."""
import hashlib
import json
import subprocess
import sys

import pytest

from app import storage_maintenance
from app.store import AutoReplyStore


def test_no_implicit_native_cleanup_on_current_database(tmp_path):
    database = tmp_path / 'current.sqlite3'
    AutoReplyStore(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, '-m', 'app.storage_maintenance', '--db', str(database)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert 'required' in result.stderr
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_no_target_does_not_create_an_empty_database(tmp_path):
    database = tmp_path / 'missing.sqlite3'
    result = subprocess.run(
        [sys.executable, '-m', 'app.storage_maintenance', '--db', str(database)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, result.stderr
    assert not database.exists()


@pytest.mark.parametrize(('flag', 'method'), [
    ('--work-summary-inputs', 'compact_terminal_work_summary_inputs'),
    ('--settled-reply-inputs', 'compact_settled_reply_inputs'),
    ('--done-email-classification-inputs', 'compact_done_email_classification_inputs'),
])
def test_existing_explicit_input_targets_keep_cli_dispatch(tmp_path, monkeypatch, capsys, flag, method):
    database = tmp_path / 'explicit.sqlite3'
    calls = []
    for name in ('compact_terminal_work_summary_inputs', 'compact_settled_reply_inputs',
                 'compact_done_email_classification_inputs'):
        def invoke(path, selected=name):
            calls.append((selected, path))
            return {'rows_compacted': 3}
        monkeypatch.setattr(storage_maintenance, name, invoke)
    monkeypatch.setattr(sys, 'argv', ['storage_maintenance', '--db', str(database), flag])
    storage_maintenance.main()
    assert calls == [(method, database)]
    assert json.loads(capsys.readouterr().out) == {'rows_compacted': 3}
    assert not database.exists()


def test_conflicting_input_targets_do_not_open_database(tmp_path, monkeypatch):
    database = tmp_path / 'conflict.sqlite3'
    monkeypatch.setattr(sys, 'argv', ['storage_maintenance', '--db', str(database),
                                    '--work-summary-inputs', '--settled-reply-inputs'])
    with pytest.raises(SystemExit) as failure:
        storage_maintenance.main()
    assert failure.value.code == 2
    assert not database.exists()
