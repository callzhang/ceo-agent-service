"""Explicit recovery of an existing Minutes archive using a completed ASR job."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.minutes_sync import archive_transcription_recovery
from app.store import AutoReplyStore


class Segment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1)
    speaker: str | None = None

    @model_validator(mode="after")
    def valid_segment(self):
        if self.end <= self.start or not self.text.strip():
            raise ValueError("invalid ASR segment")
        return self


class ASRResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    job_id: str | None = None
    model: str = Field(min_length=1)
    text: str = Field(min_length=1)
    segments: list[Segment] = Field(min_length=1)

    @model_validator(mode="after")
    def completed(self):
        if self.status != "done" or not self.text.strip():
            raise ValueError("ASR result is not complete")
        return self


class RecoveryPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_uuid: str = Field(min_length=1)
    asr_job_id: str = Field(min_length=1)
    audio_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    audio_sha256: str = Field(min_length=64, max_length=64)
    derived_summary: str = Field(min_length=1)
    asr_result: ASRResult

    @model_validator(mode="after")
    def valid_evidence(self):
        if (
            not self.task_uuid.strip()
            or not self.asr_job_id.strip()
            or not self.derived_summary.strip()
        ):
            raise ValueError("recovery identity and summary must be nonempty")
        if self.asr_result.job_id and self.asr_result.job_id != self.asr_job_id:
            raise ValueError("ASR job identity mismatch")
        previous = -math.inf
        for segment in self.asr_result.segments:
            if segment.start < previous or segment.end > self.audio_duration_seconds:
                raise ValueError("ASR segment time outside audio or out of order")
            previous = segment.start
        return self


def recover_archive(
    store,
    archive_dir: Path,
    packet: dict,
    audio_file: Path,
    receipt: dict,
    *,
    apply: bool,
) -> Path:
    packet = RecoveryPacket.model_validate(packet)
    results = receipt.get("results")
    if (
        receipt.get("ok") is not True
        or receipt.get("requested") != 1
        or receipt.get("succeeded") != 1
        or receipt.get("failed") != 0
        or not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        raise ValueError("download receipt is not a single successful download")
    item = results[0]
    receipt_path = Path(item.get("path") or "")
    if not receipt_path.is_absolute():
        receipt_path = audio_file.parent / receipt_path
    if (
        item.get("taskUuid") != packet.task_uuid
        or receipt_path.resolve() != audio_file.resolve()
        or item.get("sizeBytes") != audio_file.stat().st_size
        or audio_file.stat().st_size <= 0
    ):
        raise ValueError("download receipt does not bind the audio and task")
    digest = hashlib.sha256()
    with audio_file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != packet.audio_sha256:
        raise ValueError("audio digest mismatch")
    lines = [
        "# Derived internal summary (based on ai-services ASR)",
        "",
        packet.derived_summary,
        "",
        "# Supplemental transcript (ai-services ASR)",
        "",
        f"ASR job: {packet.asr_job_id}; model: {packet.asr_result.model}; audio SHA-256: {packet.audio_sha256}",
        "This summary was prepared internally from the ASR transcript; it is not the provider summary. Speaker labels are anonymous and are not verified identities.",
        "",
        packet.asr_result.text,
        "",
        "## Timed ASR segments",
        "",
    ]
    for segment in packet.asr_result.segments:
        lines.append(f"[{segment.start:.2f}–{segment.end:.2f}s] {segment.text}")
    return archive_transcription_recovery(
        store,
        archive_dir,
        task_uuid=packet.task_uuid,
        supplement="\n".join(lines),
        apply=apply,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--audio-file", required=True, type=Path)
    parser.add_argument("--download-receipt", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = recover_archive(
        AutoReplyStore(args.db) if args.apply else None,
        args.archive_dir,
        json.loads(args.input_json.read_text()),
        args.audio_file,
        json.loads(args.download_receipt.read_text()),
        apply=args.apply,
    )
    print(json.dumps({"path": str(result), "applied": args.apply}))


if __name__ == "__main__":
    main()
