from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas import SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import JobRow, MediaItemRow, ObservedCandidateRow
from app.localization.verification import BazarrVerificationService
from app.services.settings import SettingsService


def _session(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path))
    get_app_config.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    fernet = load_or_create_fernet(config_dir / "secret.key")
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="secret",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
        )
    )
    return db


@pytest.mark.asyncio
async def test_job_retry_recovers_bazarr_ids_from_observed_candidate(
    tmp_path, monkeypatch
):
    db = _session(tmp_path, monkeypatch)
    media_path = tmp_path / "Futurama - S11E06.mkv"
    media_path.write_bytes(b"video")
    target_path = tmp_path / "Futurama - S11E06.pt.srt"
    target_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n", encoding="utf-8")

    observed = ObservedCandidateRow(
        candidate_key="candidate-111",
        media_type="episode",
        media_path=str(media_path),
        media_title="Futurama - S11E06",
        target_language="pt-PT",
        bazarr_episode_id=606,
        bazarr_series_id=11,
    )
    job = JobRow(
        candidate_key="candidate-111",
        job_kind="translate",
        media_type="episode",
        media_path=str(media_path),
        media_title="Futurama - S11E06",
        source_subtitle_path=str(tmp_path / "Futurama - S11E06.en.srt"),
        target_subtitle_path=str(target_path),
        source_language="en",
        target_language="pt-PT",
        status="completed",
        model="test",
    )
    db.add_all([observed, job])
    db.commit()
    db.refresh(job)

    async def fake_register_or_rescan(_client, media):
        assert media.bazarr_episode_id == 606
        assert media.bazarr_series_id == 11

    async def fake_present(_self, media, _language):
        assert media.bazarr_episode_id == 606
        return True

    monkeypatch.setattr(
        "app.jobs.bazarr_sync.register_or_rescan",
        fake_register_or_rescan,
    )
    monkeypatch.setattr(BazarrVerificationService, "_bazarr_present", fake_present)

    result = await BazarrVerificationService(db).rescan_and_verify_job(job)

    assert result.ok is True
    db.refresh(job)
    assert job.bazarr_episode_id == 606
    assert job.bazarr_series_id == 11


@pytest.mark.asyncio
async def test_media_retry_recovers_bazarr_ids_by_path(tmp_path, monkeypatch):
    db = _session(tmp_path, monkeypatch)
    media_path = tmp_path / "Futurama - S11E06.mkv"
    media_path.write_bytes(b"video")
    (tmp_path / "Futurama - S11E06.pt.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nOlá\n",
        encoding="utf-8",
    )
    media = MediaItemRow(
        provider_id="jellyfin",
        external_id="jellyfin-111",
        media_type="episode",
        title="Futurama - S11E06",
        path=str(media_path),
    )
    observed = ObservedCandidateRow(
        candidate_key="different-key",
        media_type="episode",
        media_path=str(media_path),
        media_title="Futurama - S11E06",
        target_language="pt-PT",
        bazarr_episode_id=606,
        bazarr_series_id=11,
    )
    db.add_all([media, observed])
    db.commit()
    db.refresh(media)

    async def fake_rescan(_self, value):
        assert value.bazarr_episode_id == 606
        assert value.bazarr_series_id == 11

    async def fake_present(_self, value, _language):
        assert value.bazarr_episode_id == 606
        return True

    monkeypatch.setattr(BazarrVerificationService, "_rescan", fake_rescan)
    monkeypatch.setattr(BazarrVerificationService, "_bazarr_present", fake_present)

    result = await BazarrVerificationService(db).rescan_and_verify(media, "pt-PT")

    assert result.ok is True
    db.refresh(media)
    assert media.bazarr_episode_id == 606
    assert media.bazarr_series_id == 11
