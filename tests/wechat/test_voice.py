"""SILK voice audio becomes a WAV the transcription tool can take."""

import math
import struct
import wave
from io import BytesIO

import pilk
import pytest

from app.wechat.models import WechatVoiceUnavailable
from app.wechat.voice import silk_to_wav


def _silk_tone(tmp_path, seconds=1):
    pcm = tmp_path / "tone.pcm"
    pcm.write_bytes(b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 24000)))
        for n in range(24000 * seconds)
    ))
    silk = tmp_path / "tone.silk"
    pilk.encode(str(pcm), str(silk), pcm_rate=24000, tencent=True)
    return silk.read_bytes()


def test_wechat_silk_decodes_to_a_mono_wav_of_the_same_length(tmp_path):
    silk = _silk_tone(tmp_path, seconds=2)
    assert silk.startswith(b"\x02#!SILK_V3")      # the prefix WeChat stores

    wav, seconds = silk_to_wav(silk)

    with wave.open(BytesIO(wav)) as audio:
        assert (audio.getnchannels(), audio.getframerate()) == (1, 24000)
        assert audio.getnframes() / 24000 == pytest.approx(seconds)
    assert seconds == pytest.approx(2, abs=0.1)


def test_audio_that_is_not_silk_is_unavailable_not_empty():
    with pytest.raises(WechatVoiceUnavailable, match="cannot be decoded"):
        silk_to_wav(b"not a silk voice")
