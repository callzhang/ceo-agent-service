from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.memory_connector_client import (
    MemoryConnectorClient,
    MemoryConnectorCredential,
    MemoryConnectorError,
    MemoryConnectorNotAuthorized,
    load_credential,
    save_credential,
    _receipt_from_result,
)


def _credential() -> MemoryConnectorCredential:
    return MemoryConnectorCredential(client_id="client-1", refresh_token="refresh-1")


def test_a_saved_credential_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    path = save_credential(_credential(), tmp_path / "credential.json")

    assert oct(path.stat().st_mode)[-3:] == "600"
    assert load_credential(path).refresh_token == "refresh-1"


def test_a_missing_credential_names_the_command_that_creates_one(
    tmp_path: Path,
) -> None:
    with pytest.raises(MemoryConnectorNotAuthorized) as raised:
        load_credential(tmp_path / "absent.json")

    assert "authorize" in str(raised.value)


def test_a_rotated_refresh_token_replaces_the_stored_one(
    tmp_path: Path, monkeypatch
) -> None:
    """The connector may retire the old token on refresh.

    Keeping the retired one means the next run authenticates with a token the
    connector has already rejected, which looks like a revoked authorization.
    """
    import app.memory_connector_client as client_module

    path = save_credential(_credential(), tmp_path / "credential.json")
    monkeypatch.setattr(
        client_module,
        "_post_form",
        lambda url, form: {
            "access_token": "access-1",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        },
    )
    client = MemoryConnectorClient(
        load_credential(path), credential_path_override=path
    )

    client._fresh_access_token()

    assert json.loads(path.read_text())["refresh_token"] == "refresh-2"


def test_an_access_token_is_reused_until_it_nears_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    import app.memory_connector_client as client_module

    calls: list[str] = []
    monkeypatch.setattr(
        client_module,
        "_post_form",
        lambda url, form: calls.append(url)
        or {"access_token": "access-1", "expires_in": 3600},
    )
    client = MemoryConnectorClient(_credential())

    assert client._fresh_access_token() == "access-1"
    assert client._fresh_access_token() == "access-1"
    assert len(calls) == 1


def test_a_write_without_an_episode_is_not_counted_as_written() -> None:
    """A tool result that records nothing is a failure, not a quiet success."""
    with pytest.raises(MemoryConnectorError):
        _receipt_from_result({"content": [{"type": "text", "text": "{}"}]})


def test_a_tool_error_is_never_read_as_a_receipt() -> None:
    with pytest.raises(MemoryConnectorError):
        _receipt_from_result({"isError": True, "content": []})


def test_the_receipt_is_read_from_the_connector_s_own_payload() -> None:
    receipt = _receipt_from_result(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "episode_uuid": "episode-7",
                                    "processing_status": "pending",
                                }
                            )
                        }
                    ),
                }
            ]
        }
    )

    assert receipt.episode_uuid == "episode-7"
    assert receipt.processing_status == "pending"
