"""Audio-analysis model preference pool tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import SettingsRow
from app.services.model_preferences import (
    AUDIO_ANALYSIS_PURPOSE,
    TRANSLATION_PURPOSE,
    ModelPreferenceService,
    list_preferences,
)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audio-models.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_audio_model_is_separate_from_translation_pool(tmp_path):
    db = _session(tmp_path)
    db.add(SettingsRow(id=1, openrouter_model="test/model"))
    db.commit()
    service = ModelPreferenceService(db)

    translation = service.add(
        model_id="test/model",
        tier="paid",
        purpose=TRANSLATION_PURPOSE,
    )
    audio = service.add(
        model_id="test/model",
        tier="paid",
        purpose=AUDIO_ANALYSIS_PURPOSE,
    )

    assert translation.id != audio.id
    assert [row.id for row in list_preferences(db)] == [translation.id]
    assert [row.id for row in list_preferences(db, purpose=AUDIO_ANALYSIS_PURPOSE)] == [audio.id]


def test_audio_models_share_one_priority_order(tmp_path):
    db = _session(tmp_path)
    db.add(SettingsRow(id=1, openrouter_model="text/model"))
    db.commit()
    service = ModelPreferenceService(db)

    first = service.add(
        model_id="audio/first",
        tier="free",
        purpose=AUDIO_ANALYSIS_PURPOSE,
    )
    second = service.add(
        model_id="audio/second",
        tier="paid",
        purpose=AUDIO_ANALYSIS_PURPOSE,
    )
    rows = service.reorder(
        tier=None,
        purpose=AUDIO_ANALYSIS_PURPOSE,
        ordered_ids=[second.id, first.id],
    )

    assert [row.model_id for row in rows] == ["audio/second", "audio/first"]
    assert [row.priority for row in rows] == [1, 2]
