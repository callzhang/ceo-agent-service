"""Exact inline Email original references; never an original-content archive."""

from hashlib import sha256
import imaplib
import json
import sqlite3
from contextlib import closing

from app.email_classifier_scan import _normalized_message_text
from app import native_trajectory as native


def verify_original(body, reference):
    """Require the complete original bytes before source use or schema inverse."""
    if (not isinstance(reference, dict) or not isinstance(body, str)
            or type(reference.get("utf8_bytes")) is not int
            or len(body.encode("utf-8")) != reference["utf8_bytes"]
            or sha256(body.encode("utf-8")).hexdigest() != reference.get("sha256")):
        raise ValueError("Email original differs from its exact source reference")
    return body


def _provider_original(message, representation):
    if representation == "provider_body":
        return str(message.get("markdownBody") or message.get("textBody") or "")
    if representation == "scan_normalized_message":
        return _normalized_message_text(message)
    from app.email_classifier_model import CpuTfidfLogisticClassifier, email_message_to_text

    if representation == CpuTfidfLogisticClassifier.FEATURE_VERSION:
        return email_message_to_text(message)
    raise ValueError("unknown Email original representation")


def original_from_message(message, reference):
    if reference["kind"] == "provider":
        return verify_original(_provider_original(message, reference["representation"]), reference)
    if reference["kind"] == "native":
        original = verify_original(native.read_native_source_body(reference["native_provenance"]), reference)
        if any(_provider_original(message, representation) == original
               for representation in ("provider_body", "scan_normalized_message")):
            return original
        raise ValueError("Email original differs from current provider source")
    raise ValueError("unknown Email original source kind")


def read_referenced_original(reference, *, read_message):
    if reference["kind"] == "native":
        return verify_original(native.read_native_source_body(reference["native_provenance"]), reference)
    if reference["kind"] == "provider":
        return verify_original(_provider_original(read_message(), reference["representation"]), reference)
    raise ValueError("unknown Email original source kind")


def locate_original(body, *, attempts, read_message,
                    representations=("provider_body", "scan_normalized_message")):
    """Prove a complete native field first, then the accurate provider original."""
    if not body:
        return None
    digest = {"sha256": sha256(body.encode("utf-8")).hexdigest(),
              "utf8_bytes": len(body.encode("utf-8"))}
    for attempt in attempts:
        if (attempt["runtime_kind"] not in ("codex_cli", "claude_cli")
                or not attempt["session_id"]
                or attempt["transcript_end"] <= attempt["transcript_start"]):
            continue
        try:
            locator = native.locate_native_source_body(body,
                runtime_kind=attempt["runtime_kind"], session_uuid=attempt["session_id"],
                start_line=attempt["transcript_start"], end_line=attempt["transcript_end"])
            verify_original(native.read_native_source_body(locator), digest)
        except (native.NativeSourceMissing, native.NativeSourceVersionChanged, OSError, UnicodeError):
            continue
        return {**digest, "kind": "native", "native_provenance": locator}
    try:
        message = read_message()
    except (LookupError, OSError, ValueError, imaplib.IMAP4.error):
        return None
    for representation in representations:
        if _provider_original(message, representation) == body:
            return {**digest, "kind": "provider", "representation": representation,
                    "locator": {"folder": message["folder"], "uidvalidity": message["uidValidity"],
                                "uid": message["uid"], "rfc_message_id": message.get("messageId")}}
    return None


def prepare_recovery_originals(database, *, backup_path, read_message):
    """Prepare exact originals in memory before the schema inverse writer opens.

    An existing source backup supplies only matching original bytes. New or
    changed messages use their exact native/provider source, never old rows.
    """
    return _prepare_recovery_inputs(database, backup_path=backup_path,
                                    read_message=read_message, model_input=False)


def prepare_recovery_model_inputs(database, *, backup_path, read_message):
    """Read proven model inputs before the inverse transaction, never old labels."""
    return _prepare_recovery_inputs(database, backup_path=backup_path,
                                    read_message=read_message, model_input=True)


def _prepare_recovery_inputs(database, *, backup_path, read_message, model_input):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("select 1 from sqlite_schema where type='table' and name='emails'").fetchone() is None:
            return {}
        db.row_factory = sqlite3.Row
        predicate = (" and class_model_text=''" if model_input
                     else " and json_type(message_source_reference_json,'$.kind') is not null")
        rows = db.execute("select account_id,stable_message_identity,message_source_reference_json "
                          "from emails where message_source_reference_json is not null" + predicate).fetchall()
    result = {}
    for row in rows:
        reference = json.loads(row["message_source_reference_json"])
        if model_input:
            reference = reference.get("model_input")
            if reference is None:
                continue
        body = None
        if backup_path is not None:
            with closing(sqlite3.connect(backup_path.resolve().as_uri() + "?mode=ro", uri=True)) as backup:
                source = ("select model_text from email_classifications " if model_input
                          else "select normalized_text from email_messages ")
                old = backup.execute(source +
                    "where account_id=? and stable_message_identity=?",
                    (row["account_id"], row["stable_message_identity"])).fetchone()
            if old is not None:
                try:
                    body = verify_original(old[0], reference)
                except ValueError:
                    # This backup precedes the current source version. It
                    # cannot replace a post-migration original or business row.
                    pass
        if body is None:
            body = read_referenced_original(reference, read_message=lambda:
                read_message(row["account_id"], row["stable_message_identity"]))
        result[(row["account_id"], row["stable_message_identity"])] = verify_original(body, reference)
    return result
