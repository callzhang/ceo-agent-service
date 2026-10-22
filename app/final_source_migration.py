"""Exact source coordinates for the final paired migration; originals stay transient."""

from contextlib import closing
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

from app.business_source_reader import AvailableSource, RequestSourceReader, SourceVersionBinding
from app.business_minutes_source_reader import minutes_provider_locator
from app.provenance_definitions import SOURCE_FIELDS


NATIVE_FIELDS = (
    "runtime_kind", "session_uuid", "line_index_zero_based", "raw_line_sha256",
    "leaf_path", "substring_start_character_zero_based", "substring_end_character_exclusive",
    "literal_encoding_mode", "decoded_utf8_sha256", "decoded_utf8_bytes",
)


def bind_verified_source(signal, *, proof, retirement):
    binding = SourceVersionBinding.from_signal(signal)
    if retirement is not None:
        if (binding.identity_key, binding.body_utf8_bytes) != (retirement["identity_key"], retirement["utf8_bytes"]):
            raise ValueError("changed authorized source retirement version")
        return binding
    if proof is None:
        raise ValueError("missing verified source version coordinates")
    if (binding.identity_key, binding.body_sha256, binding.body_utf8_bytes) != (
            proof["identity_key"], proof["source_body_sha256"], proof["source_body_utf8_bytes"]):
        raise ValueError("changed verified source version")
    native, upstream = proof["native_axis"], proof["upstream_axis"]
    if native["status"] == "currently_readable_exact_native_match":
        return replace(binding, native_provenance_json=json.dumps(
            {key: native[key] for key in NATIVE_FIELDS}, ensure_ascii=False, sort_keys=True))
    if signal.source_type == "ai_minutes" and upstream["status"] in (
            "current_native_provider_exact", "source_era_exact_after_current_mismatch"):
        if (upstream["source_uuid"], upstream["utf8_sha256"], upstream["utf8_bytes"]) != (
                signal.source_ref.partition("#todos-sha256=")[0], binding.body_sha256, binding.body_utf8_bytes):
            raise ValueError("changed verified minutes source version")
        locator = minutes_provider_locator(signal,
            transcript_excerpt_format=upstream.get("transcript_excerpt_format", "sentence_v1"))
    elif upstream["status"] == "current_weekly_provider_exact":
        locator = upstream["source_locator"]
    elif signal.source_type == "console" and upstream["status"] == "current_retained_business_fact_exact":
        return binding
    else:
        raise ValueError("missing verified source coordinates")
    return replace(binding, provider_locator_json=json.dumps(locator, ensure_ascii=False, sort_keys=True))


def locate_task_source_binding(db, signal):
    """Bind a new source version through its actual task-generation turn."""
    from app import native_trajectory as native

    attempts = db.execute("select a.runtime_kind,a.session_id,a.transcript_start,a.transcript_end "
        "from tasks i join runs r on r.record_type='task_agent_runs' "
        "and r.summary_input_id=cast(i.legacy_key as integer) "
        "join runs a on a.record_type='agent_runtime_attempts' "
        "and a.workload_kind='task' and a.workload_key=r.legacy_key "
        "where i.record_type='work_summary_inputs' and i.source_type=? and i.source_ref=? "
        "and a.status not in ('starting','running') order by cast(a.legacy_key as integer) desc",
        (signal.source_type, signal.source_ref)).fetchall()
    for runtime_kind, session, start, end in attempts:
        if runtime_kind not in ('codex_cli', 'claude_cli') or not session or end <= start:
            continue
        try:
            locator = native.locate_native_source_body(signal.evidence_text,
                runtime_kind=runtime_kind, session_uuid=session, start_line=start, end_line=end)
            body = native.read_native_source_body(locator)
        except (native.NativeSourceMissing, native.NativeSourceVersionChanged, OSError, UnicodeError):
            continue
        binding = SourceVersionBinding.from_signal(signal, native_provenance=locator)
        if not binding.matches_body(body):
            raise ValueError("new task source differs from exact native original")
        return binding
    return None


def prepare_final_source_bindings(db, root):
    cursor = db.execute("select " + ",".join("s." + field for field in SOURCE_FIELDS[:-5]) +
        ",d.identity_key,d.body from main.business_task_signals s "
        "join main.business_source_documents d on d.id=s.source_document_id")
    rows = cursor.fetchall()
    if not rows:
        return {}
    proof = json.loads((root / "docs/database-redesign-final-trajectory-locators.json").read_text())
    authorization = json.loads((root / "docs/superpowers/specs/2026-10-18-provenance-original-retirement-decision.json").read_text())
    proofs = {row["identity_key"]: row for row in proof["versions"]}
    retirements = {row["identity_key"]: row for row in authorization["versions"]}
    names = tuple(item[0] for item in cursor.description)
    bindings = {}
    for row in rows:
        values = dict(zip(names, row, strict=True))
        identity = values.pop("identity_key")
        signal = SimpleNamespace(**values, evidence_text=values["body"])
        binding = None
        if identity not in proofs and identity not in retirements:
            binding = locate_task_source_binding(db, signal)
        if binding is None:
            binding = bind_verified_source(signal, proof=proofs.get(identity), retirement=retirements.get(identity))
        if binding.identity_key != identity:
            raise ValueError("source document identity differs from its Signal")
        bindings[identity] = binding
    return bindings


def read_recovery_source_body(binding, *, backup_path, reader: RequestSourceReader):
    """Read an exact original, never restore a database or an earlier business row."""
    if backup_path is not None:
        with closing(sqlite3.connect(backup_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("select body from main.business_source_documents where identity_key=?",
                             (binding.identity_key,)).fetchone()
        if row is not None:
            if not binding.matches_body(row[0]):
                raise ValueError("recovery original differs from exact source binding")
            return row[0]
    reading = reader.resolve(binding)
    if not isinstance(reading, AvailableSource):
        raise ValueError("exact original is unavailable for schema inverse")
    return reading.body
