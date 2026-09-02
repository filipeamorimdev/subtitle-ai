"""Tests for opt-in cloud dub fallback and generated cue QA."""

from __future__ import annotations

import array
import wave
from pathlib import Path

import pytest

from app.localization.dubbing.cloud_fallback import (
    CloudDubRequest,
    ElevenLabsCloudDubProvider,
)
from app.localization.dubbing.voice_library.qa import validate_generated_cue


def _write_pcm_wav(path: Path, *, frames: int = 8_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(array.array("h", [2000] * frames).tobytes())


@pytest.mark.asyncio
async def test_cloud_fallback_requires_rights_and_key(tmp_path):
    provider = ElevenLabsCloudDubProvider(api_key=None, enabled=True)
    request = CloudDubRequest(
        media_path=tmp_path / "episode.mkv",
        target_language="pt-PT",
        rights_acknowledged=False,
    )
    with pytest.raises(Exception, match="rights acknowledgement"):
        await provider.dub_episode(request)

    provider = ElevenLabsCloudDubProvider(api_key=None, enabled=True)
    request = CloudDubRequest(
        media_path=tmp_path / "episode.mkv",
        target_language="pt-PT",
        rights_acknowledged=True,
    )
    with pytest.raises(Exception, match="API key"):
        await provider.dub_episode(request)

    disabled = ElevenLabsCloudDubProvider(api_key="secret", enabled=False)
    with pytest.raises(Exception, match="disabled"):
        await disabled.dub_episode(request)


def test_validate_generated_cue_rejects_empty_and_clipped(tmp_path):
    wav = tmp_path / "cue.wav"
    _write_pcm_wav(wav, frames=8_000)
    ok = validate_generated_cue(wav, expected_text="Uma frase curta.")
    assert ok.ok is True

    clipped = tmp_path / "clipped.wav"
    with wave.open(str(clipped), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(array.array("h", [32767] * 4_000).tobytes())
    bad = validate_generated_cue(clipped, expected_text="Uma frase mais longa para testar.")
    assert bad.ok is False
    assert "clipped" in bad.reasons
