"""Regression checks for the installed native mail output contract."""

import importlib.util
import json
from pathlib import Path

import pytest

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "mail_fixture", BASE / "native_contract_compaction.business_mail_fixture.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CASES = json.loads((BASE / "native_contract_compaction.business_cases.v5.json").read_text())
MAIL = next(case for case in CASES["cases"] if case["domain"] == "mail")["phases"]["cold"]["mail"]
EMAIL = MAIL["emailAccounts"][0]["email"]


@pytest.mark.parametrize(
    "leaf,identifier,value",
    [
        ("+thread", MAIL["conversation"]["id"], MAIL["conversation"]),
        ("+message", MAIL["messages"][0]["id"], MAIL["messages"][0]),
    ],
)
def test_shortcut_uses_native_unified_envelope(leaf, identifier, value):
    result = MODULE.read_mail(
        ["mail", leaf, "--email", EMAIL, "--id", identifier, "--format", "json"], MAIL
    )
    assert result == {"ok": True, "outcome": "success", "data": {"value": value}}


def test_mailbox_and_verify_use_provider_fields():
    result = MODULE.read_mail(["mail", "mailbox", "list", "--format", "json"], MAIL)
    assert result["result"] == {"emailAccounts": MAIL["emailAccounts"]}
    result = MODULE.read_mail(
        ["mail", "message", "verify", "--email", EMAIL, "--internet-message-id",
         MAIL["messages"][0]["internetMessageId"], "--format", "json"], MAIL
    )
    assert result["result"]["sendStatus"] == "success"
    assert result["result"]["message"] == MAIL["messages"][0]


@pytest.mark.parametrize(
    "argv",
    [
        ["mail", "+thread", "--email", "wrong@example.test", "--id", "mail-thread-1", "--format", "json"],
        ["mail", "+message", "--email", EMAIL, "--id", "wrong-message", "--format", "json"],
        ["mail", "message", "send"],
    ],
)
def test_fixture_cannot_read_wrong_identity_or_send(argv):
    with pytest.raises((AssertionError, ValueError)):
        MODULE.read_mail(argv, MAIL)
