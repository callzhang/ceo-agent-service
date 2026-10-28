"""Transcripts of WeChat voice messages, so each voice is transcribed once."""
from __future__ import annotations

DDL = (
    "create table wechat_voice_transcripts ("
    "account_id text not null, conversation_id text not null, message_id text not null, "
    "audio_sha256 text not null check(length(audio_sha256)=64), "
    "transcript text not null check(length(trim(transcript))>0), "
    "created_at text not null default current_timestamp, "
    "primary key(account_id,conversation_id,message_id))",
)
OWNED_OBJECTS = frozenset({"wechat_voice_transcripts"})
MAX_TRANSCRIPT_CHARS = 20000


def find_transcript(db, *, account_id: str, conversation_id: str, message_id: str,
                    audio_sha256: str) -> str | None:
    """The stored transcript of exactly this audio, or None."""
    row = db.execute(
        "select transcript from wechat_voice_transcripts "
        "where account_id=? and conversation_id=? and message_id=? and audio_sha256=?",
        (account_id, conversation_id, message_id, audio_sha256),
    ).fetchone()
    return row[0] if row else None


def store_transcript(db, *, account_id: str, conversation_id: str, message_id: str,
                     audio_sha256: str, transcript: str) -> None:
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("a voice transcript must not be empty")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise ValueError("voice transcript is too long")
    db.execute(
        "insert or replace into wechat_voice_transcripts"
        "(account_id,conversation_id,message_id,audio_sha256,transcript) values(?,?,?,?,?)",
        (account_id, conversation_id, message_id, audio_sha256, transcript.strip()),
    )
