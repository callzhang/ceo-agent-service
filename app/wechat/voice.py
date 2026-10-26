"""Decode a WeChat voice message (SILK) into a WAV the transcription tool accepts."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

from app.wechat.models import WechatVoiceUnavailable

WAV_SAMPLE_RATE = 24000


def silk_to_wav(silk: bytes) -> tuple[bytes, float]:
    """WAV bytes and duration in seconds of a SILK voice; unreadable audio raises."""
    import pilk

    with tempfile.TemporaryDirectory(prefix="wechat-voice-") as work:
        source, target = Path(work) / "voice.silk", Path(work) / "voice.wav"
        source.write_bytes(silk)
        try:
            pilk.silk_to_wav(str(source), str(target), WAV_SAMPLE_RATE)
            with wave.open(str(target), "rb") as audio:
                seconds = audio.getnframes() / audio.getframerate()
        except Exception as exc:
            raise WechatVoiceUnavailable(f"the voice audio cannot be decoded: {exc}") from exc
        return target.read_bytes(), seconds
