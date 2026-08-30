"""Demucs provider diagnostics without requiring the real model."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_app_config
from app.localization.audio.models import AudioSeparationError
from app.localization.audio.providers import demucs as demucs_provider
from app.media.process_runner import ProcessOutcome, ProcessResult
from tests.fixtures.media import write_sine_wav


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(path))
    get_app_config.cache_clear()
    yield path
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_demucs_failure_includes_process_diagnostic(tmp_path, config_dir, monkeypatch):
    source = write_sine_wav(tmp_path / "source.wav", duration_s=0.1, frequency=440)

    async def failed_process(*_args, **_kwargs):
        return ProcessResult(
            ProcessOutcome.FAILED,
            1,
            b"",
            b"RuntimeError: selected Demucs model cannot use this audio input",
        )

    monkeypatch.setattr(demucs_provider, "demucs_available", lambda: True)
    monkeypatch.setattr(demucs_provider, "run_logged_process", failed_process)

    with pytest.raises(AudioSeparationError, match="selected Demucs model"):
        await demucs_provider.DemucsProvider().separate(
            input_path=Path(source),
            output_dir=tmp_path / "out",
        )
