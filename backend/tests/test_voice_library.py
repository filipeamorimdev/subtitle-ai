"""Tests for the series voice library and episode cue assignments."""

from __future__ import annotations

import array
import wave
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import EpisodeVoiceCastRow, MediaItemRow
from app.localization.dubbing.options import (
    cue_key,
    normalize_voice_bindings,
    voice_binding_cache_fingerprint,
)
from app.localization.dubbing.voice_library.paths import relative_reference_path, resolve_reference_path, voices_root
from app.localization.dubbing.voice_library.service import VoiceLibraryService


def _write_pcm_wav(path: Path, *, frames: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(array.array("h", [1200] * frames).tobytes())


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _series_and_episode(session) -> tuple[MediaItemRow, MediaItemRow]:
    series = MediaItemRow(
        provider_id="local",
        external_id="series-1",
        media_type="series",
        title="Paw Patrol",
        path="/media/paw-patrol",
    )
    episode = MediaItemRow(
        provider_id="local",
        external_id="episode-1",
        media_type="episode",
        title="Paw Patrol - S01E01",
        path="/media/paw-patrol/s01e01.mkv",
        parent_media_id=None,
    )
    session.add(series)
    session.flush()
    episode.parent_media_id = series.id
    session.add(episode)
    session.commit()
    return series, episode


def test_voice_reference_paths_stay_under_voices_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    wav = voices_root() / "1-show" / "ryder" / "neutral.wav"
    _write_pcm_wav(wav)
    relative = relative_reference_path(wav)
    assert resolve_reference_path(relative) == wav.resolve()
    with pytest.raises(ValueError):
        resolve_reference_path("../outside.wav")


def test_normalize_voice_bindings_and_cache_fingerprint():
    bindings = normalize_voice_bindings(
        {
            "cue:3": {
                "reference_relative_path": "1-show/ryder/neutral.wav",
                "voice_model": "chatterbox-multilingual-v3:pt-PT:natural",
                "reference_sha256": "abc",
                "cfg_weight": 0.35,
            }
        }
    )
    assert cue_key(3) in bindings
    fingerprint = voice_binding_cache_fingerprint(bindings)
    assert fingerprint["cue:3"].startswith("abc|")


def test_series_characters_are_shared_across_episodes(db_session):
    series, episode = _series_and_episode(db_session)
    library = VoiceLibraryService(db_session)
    character = library.upsert_character(episode, target_language="pt-PT", display_name="Ryder")
    owner = library._cast_owner(episode)
    assert owner.id == series.id
    assert character.media_item_id == series.id


def test_episode_cast_is_isolated_per_episode(db_session):
    series, episode = _series_and_episode(db_session)
    other = MediaItemRow(
        provider_id="local",
        external_id="episode-2",
        media_type="episode",
        title="Paw Patrol - S01E02",
        path="/media/paw-patrol/s01e02.mkv",
        parent_media_id=series.id,
    )
    db_session.add(other)
    db_session.commit()
    library = VoiceLibraryService(db_session)
    character = library.upsert_character(episode, target_language="pt-PT", display_name="Ryder")
    db_session.add(
        EpisodeVoiceCastRow(
            media_item_id=episode.id,
            target_language="pt-PT",
            cue_index=1,
            character_id=character.id,
            status="assigned",
            confidence=1.0,
        )
    )
    db_session.add(
        EpisodeVoiceCastRow(
            media_item_id=other.id,
            target_language="pt-PT",
            cue_index=1,
            character_id=None,
            status="unresolved",
        )
    )
    db_session.commit()
    assert library.unresolved_cue_count(episode, target_language="pt-PT") == 0
    assert library.unresolved_cue_count(other, target_language="pt-PT") == 1


def test_dub_readiness_requires_approved_reference(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(tmp_path / "config"))
    _, episode = _series_and_episode(db_session)
    library = VoiceLibraryService(db_session)
    character = library.upsert_character(episode, target_language="pt-PT", display_name="Ryder")
    db_session.add(
        EpisodeVoiceCastRow(
            media_item_id=episode.id,
            target_language="pt-PT",
            cue_index=2,
            character_id=character.id,
            status="assigned",
            confidence=1.0,
        )
    )
    db_session.commit()
    ready, reason = library.dub_readiness(episode, target_language="pt-PT")
    assert ready is False
    assert "Approve" in reason

    wav = voices_root() / "1-show" / character.character_key / "neutral.wav"
    _write_pcm_wav(wav)
    reference = library.add_reference_from_path(character, source_path=wav, make_canonical=True)
    library.approve_reference(
        reference,
        voice_model="chatterbox-multilingual-v3:pt-PT:natural",
        cfg_weight=0.35,
    )
    ready, reason = library.dub_readiness(episode, target_language="pt-PT")
    assert ready is True
    assert reason == ""
    bindings = library.bindings_for_episode(episode, target_language="pt-PT")
    assert "cue:2" in bindings
