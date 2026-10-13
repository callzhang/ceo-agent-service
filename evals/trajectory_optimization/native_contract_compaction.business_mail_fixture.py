"""Readonly native DWS mail fixture; anonymous eval facts, never a provider call."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_mail(argv: list[str], mail: dict) -> dict:
    """Use documented leaf command shapes and exact source identities."""
    if argv[:3] == ["mail", "mailbox", "list"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--format", choices=["json"], required=True)
        parser.parse_args(argv[3:])
        return {"success": True, "result": {"mailboxes": mail["mailboxes"]}}
    if argv[:2] in (["mail", "+thread"], ["mail", "+message"]):
        operation = argv[1]
        flags = argv[2:]
        result_key = "value"
    elif argv[:3] in (["mail", "thread", "get"], ["mail", "message", "get"]):
        operation = "+" + argv[1]
        flags = argv[3:]
        result_key = "conversation" if operation == "+thread" else "message"
    elif argv[:3] == ["mail", "message", "verify"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--email", required=True)
        parser.add_argument("--internet-message-id", required=True)
        parser.add_argument("--format", choices=["json"], required=True)
        parsed = parser.parse_args(argv[3:])
        assert parsed.email == mail["mailboxes"][0]["email"]
        assert parsed.internet_message_id == mail["sent_receipt"]["internetMessageId"]
        return {"success": True, "result": mail["sent_receipt"]}
    else:
        raise ValueError("This anonymous fixture supplies readonly mail leaves only")
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--format", choices=["json"], required=True)
    parsed = parser.parse_args(flags)
    assert parsed.email == mail["mailboxes"][0]["email"]
    if operation == "+thread":
        value = mail["conversation"]
        assert parsed.id == value["id"]
    else:
        matches = [message for message in mail["messages"] if message["id"] == parsed.id]
        assert len(matches) == 1
        value = matches[0]
    return {"success": True, "result": {result_key: value}}


def main() -> None:
    import sys
    import subprocess

    root = Path(sys.argv[1])
    mail = json.loads((root / "mail.json").read_text())
    argv = sys.argv[2:]
    if argv[:1] == ["schema"]:
        # Native local metadata only; business leaves below never reach the provider.
        native_cli = (root / "native-dws-path.txt").read_text().strip()
        completed = subprocess.run([native_cli, *argv], check=True, capture_output=True)
        sys.stdout.buffer.write(completed.stdout)
        return
    result = read_mail(argv, mail)
    with (root / "mail.calls.jsonl").open("a") as stream:
        stream.write(json.dumps({"argv": argv, "result": result}) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
