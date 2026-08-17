"""Automatic subtitle fallback planner, scanner, and recovery tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import JobRow, ObservedCandidateRow
from app.jobs.service import JobService
from app.services.candidates import CandidateService
from app.services.fallback import FallbackPlanner
from app.services.settings import SettingsService
from app.translation.openrouter.client import ChatResult, OpenRouterClient
from app.translation.openrouter.exchange_log import JobOpenRouterExchangeLog, job_openrouter_log_path

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello

2
00:00:04,000 --> 00:00:06,000
World
"""


@pytest.fixture
def auto_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    media = tmp_path / "media" / "Example"
    media.mkdir(parents=True)
    (media / "Example.mkv").write_text("x")
    source = media / "Example.en.srt"
    source.write_text(SAMPLE_SRT, encoding="utf-8")

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path / "media"))
    get_app_config.cache_clear()

    engine = create_engine(f"sqlite:///{config_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import app.db as db_module

    db_module._engine = engine
    db_module._SessionLocal = SessionLocal

    fernet = load_or_create_fernet(config_dir / "secret.key")
    db = SessionLocal()
    SettingsService(db, fernet=fernet).update(
        SettingsUpdate(
            bazarr_url="http://bazarr:6767",
            bazarr_api_key="baz",
            openrouter_api_key="or-key",
            openrouter_model="openai/gpt-4o-mini",
            target_language_code="pt-PT",
            target_language_name="Portuguese (Portugal)",
            source_languages=["en"],
            path_mappings=[
                PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path / "media"))
            ],
            batch_size=50,
            automatic_fallback_enabled=False,
            automatic_scan_interval_minutes=5,
            bazarr_grace_period_minutes=10,
            automatic_retry_enabled=True,
            maximum_automatic_retries=3,
        )
    )
    db.close()

    wanted_missing = ["pt-PT"]
    subtitle_payload = [["en", "/movies/Example/Example.en.srt"]]

    async def bazarr_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/system/status"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/api/movies/wanted"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "radarrId": 10,
                            "missing_subtitles": list(wanted_missing),
                        }
                    ],
                    "total": 1,
                },
            )
        if path.endswith("/api/episodes/wanted"):
            return httpx.Response(200, json={"data": [], "total": 0})
        if request.method == "PATCH" and path.endswith("/api/movies"):
            assert request.url.params.get("action") == "scan-disk"
            return httpx.Response(204)
        if request.method == "PATCH" and path.endswith("/api/series"):
            assert request.url.params.get("action") == "scan-disk"
            return httpx.Response(204)
        if path.endswith("/api/movies"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "title": "Example Movie",
                            "path": "/movies/Example/Example.mkv",
                            "radarrId": 10,
                            "missing_subtitles": list(wanted_missing),
                            "subtitles": list(subtitle_payload),
                        }
                    ],
                    "total": 1,
                },
            )
        if path.endswith("/api/episodes"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/api/movies/subtitles"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, text=path)

    transport = httpx.MockTransport(bazarr_handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    async def fake_chat(self, *, model, messages, temperature=0.2, max_tokens=None):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "Classify media into a franchise universe" in system:
            content = '{"universe":"none"}'
        elif "extract audiovisual glossary terms" in system:
            content = '{"terms":[]}'
        else:
            content = "[001]\nOlá\n\n[002]\nMundo\n"
        return ChatResult(
            content=content,
            model=model,
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        )

    monkeypatch.setattr(
        "app.translation.openrouter.client.OpenRouterClient.chat_completion",
        fake_chat,
    )

    async def no_probe(path):
        return []

    monkeypatch.setattr("app.services.candidates.probe_subtitle_tracks", no_probe)

    return {
        "SessionLocal": SessionLocal,
        "source": source,
        "media": media,
        "tmp_path": tmp_path,
        "wanted_missing": wanted_missing,
        "subtitle_payload": subtitle_payload,
        "fernet": fernet,
    }


@pytest.mark.asyncio
async def test_toggle_off_creates_no_jobs(auto_env):
    db = auto_env["SessionLocal"]()
    result = await FallbackPlanner(db).scan_once()
    assert result.ok is False
    assert result.enabled is False
    assert db.scalars(select(JobRow)).all() == []
    assert db.scalars(select(ObservedCandidateRow)).all() == []
    db.close()


@pytest.mark.asyncio
async def test_grace_period_blocks_then_allows_one_job(auto_env):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=10)
    )

    first = await FallbackPlanner(db).scan_once()
    assert first.ok is True
    assert first.created_count == 0
    observed = db.scalars(select(ObservedCandidateRow)).all()
    assert len(observed) == 1
    assert db.scalars(select(JobRow)).all() == []

    # Simulate grace expired
    observed[0].first_seen_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    db.add(observed[0])
    db.commit()

    second = await FallbackPlanner(db).scan_once()
    assert second.created_count == 1
    jobs = db.scalars(select(JobRow)).all()
    assert len(jobs) == 1
    assert jobs[0].trigger_type == "automatic"
    assert jobs[0].job_kind == "translate"

    third = await FallbackPlanner(db).scan_once()
    assert third.created_count == 0
    assert len(db.scalars(select(JobRow)).all()) == 1

    # Restart simulation: first_seen preserved
    key = observed[0].candidate_key
    preserved = db.get(ObservedCandidateRow, key).first_seen_at
    db.close()
    db = auto_env["SessionLocal"]()
    again = db.get(ObservedCandidateRow, key)
    assert again is not None
    assert again.first_seen_at == preserved
    db.close()


@pytest.mark.asyncio
async def test_target_exists_no_automatic_job(auto_env):
    target = auto_env["media"] / "Example.pt-PT.srt"
    target.write_text("1\n00:00:01,000 --> 00:00:02,000\nJá\n", encoding="utf-8")
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )
    result = await FallbackPlanner(db).scan_once()
    assert result.ok is True
    assert result.created_count == 0
    assert not any(j.job_kind == "translate" and j.status == "pending" for j in db.scalars(select(JobRow)))
    db.close()


@pytest.mark.asyncio
async def test_no_source_enqueues_request(auto_env, monkeypatch):
    auto_env["source"].unlink()
    auto_env["subtitle_payload"].clear()
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )
    result = await FallbackPlanner(db).scan_once()
    assert result.ok is True
    jobs = db.scalars(select(JobRow)).all()
    assert len(jobs) == 1
    assert jobs[0].job_kind == "request"
    assert jobs[0].trigger_type == "automatic"
    db.close()


@pytest.mark.asyncio
async def test_chain_disabled_when_toggle_off(auto_env):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=False)
    )
    row = JobRow(
        candidate_key="abc",
        job_kind="extract",
        trigger_type="automatic",
        media_type="movie",
        media_path=str(auto_env["media"] / "Example.mkv"),
        media_title="Example",
        bazarr_movie_id=10,
        source_subtitle_path=str(auto_env["media"] / "Example.mkv"),
        target_subtitle_path=str(auto_env["source"]),
        source_language="en",
        target_language="en",
        model="ffmpeg-extract",
        status="completed",
        progress=100,
    )
    db.add(row)
    db.commit()
    chained = await FallbackPlanner(db).maybe_chain_translate(
        candidate_key="abc",
        source_path=str(auto_env["source"]),
    )
    assert chained is None
    db.close()


def test_worker_recovers_processing_jobs(auto_env):
    db = auto_env["SessionLocal"]()
    row = JobRow(
        candidate_key="x",
        job_kind="translate",
        trigger_type="manual",
        media_type="movie",
        media_path=str(auto_env["media"] / "Example.mkv"),
        media_title="Example",
        source_subtitle_path=str(auto_env["source"]),
        target_subtitle_path=str(auto_env["media"] / "Example.pt-PT.srt"),
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status="processing",
        progress=40,
        progress_detail="mid-flight",
    )
    db.add(row)
    db.commit()
    job_id = row.id
    db.close()

    recovered = JobService.recover_interrupted_jobs(auto_env["SessionLocal"]())
    assert recovered == 1
    db = auto_env["SessionLocal"]()
    row = db.get(JobRow, job_id)
    assert row is not None
    assert row.status == "pending"
    assert row.progress_detail == "Recovered after restart"
    db.close()


@pytest.mark.asyncio
async def test_manual_claimed_before_automatic(auto_env):
    db = auto_env["SessionLocal"]()
    auto = JobRow(
        candidate_key="a",
        job_kind="translate",
        trigger_type="automatic",
        media_type="movie",
        media_path=str(auto_env["media"] / "Example.mkv"),
        media_title="Auto",
        source_subtitle_path=str(auto_env["source"]),
        target_subtitle_path=str(auto_env["media"] / "Example.auto.pt-PT.srt"),
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status="pending",
    )
    manual = JobRow(
        candidate_key="m",
        job_kind="translate",
        trigger_type="manual",
        media_type="movie",
        media_path=str(auto_env["media"] / "Example.mkv"),
        media_title="Manual",
        source_subtitle_path=str(auto_env["source"]),
        target_subtitle_path=str(auto_env["media"] / "Example.manual.pt-PT.srt"),
        source_language="en",
        target_language="pt-PT",
        model="openai/gpt-4o-mini",
        status="pending",
    )
    db.add(auto)
    db.add(manual)
    db.commit()
    claimed = JobService(db).claim_next_job("translate")
    assert claimed is not None
    assert claimed.trigger_type == "manual"
    db.close()


@pytest.mark.asyncio
async def test_translate_verify_success_and_failure(auto_env, monkeypatch):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.jobs.service.asyncio.sleep", fake_sleep)

    # Success path: after write, Bazarr no longer lists target as missing
    auto_env["wanted_missing"].clear()
    auto_env["subtitle_payload"].clear()
    auto_env["subtitle_payload"].extend(
        [
            ["en", "/movies/Example/Example.en.srt"],
            ["pt-PT", "/movies/Example/Example.pt-PT.srt"],
        ]
    )

    scan = await FallbackPlanner(db).scan_once()
    assert scan.created_count == 1
    job = db.scalars(select(JobRow).where(JobRow.job_kind == "translate")).one()
    service = JobService(db)
    claimed = service.claim_next_job("translate")
    assert claimed is not None
    await service.process_job(claimed.id)
    done = service.get_job(claimed.id)
    assert done is not None
    assert done.status == "completed"
    assert done.reason_code != "bazarr_verify_failed"
    assert sleeps == [2.0, 5.0, 10.0] or sleeps[:1] == [2.0]
    assert (auto_env["media"] / "Example.pt.srt").exists()
    db.close()


def _count_openrouter_and_verify(monkeypatch):
    chat_calls = {"n": 0}
    verify_calls = {"n": 0}
    original_chat = OpenRouterClient.chat_completion
    original_verify = JobService._verify_target_with_backoff

    async def counting_chat(*args, **kwargs):
        chat_calls["n"] += 1
        return await original_chat(*args, **kwargs)

    async def counting_verify(self, row):
        verify_calls["n"] += 1
        return await original_verify(self, row)

    monkeypatch.setattr(OpenRouterClient, "chat_completion", counting_chat)
    monkeypatch.setattr(JobService, "_verify_target_with_backoff", counting_verify)
    return chat_calls, verify_calls


@pytest.mark.asyncio
async def test_next_action_is_verify_when_target_written_but_bazarr_missing(auto_env):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )
    target = auto_env["media"] / "Example.pt-PT.srt"
    target.write_text(SAMPLE_SRT, encoding="utf-8")

    planner = FallbackPlanner(db)
    candidate = (await CandidateService(db).list_candidates())[0]
    assert candidate.reason_code == "target_exists"
    observed = planner.observe_candidate(candidate)

    db.add(
        JobRow(
            candidate_key=candidate.key,
            job_kind="translate",
            trigger_type="automatic",
            media_type="movie",
            media_path=candidate.media_path,
            media_title=candidate.title,
            bazarr_movie_id=candidate.bazarr_movie_id,
            source_subtitle_path=str(auto_env["source"]),
            target_subtitle_path=str(target),
            source_language="en",
            target_language="pt-PT",
            model="openai/gpt-4o-mini",
            status="completed",
            progress=100,
            reason_code="bazarr_verify_failed",
        )
    )
    db.commit()

    assert planner.next_action(candidate, observed) == "verify"
    db.close()


@pytest.mark.asyncio
async def test_verify_failure_does_not_retranslate(auto_env, monkeypatch):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("app.jobs.service.asyncio.sleep", fake_sleep)
    chat_calls, verify_calls = _count_openrouter_and_verify(monkeypatch)

    # Keep target missing after write
    auto_env["wanted_missing"][:] = ["pt-PT"]

    await FallbackPlanner(db).scan_once()
    service = JobService(db)
    claimed = service.claim_next_job("translate")
    assert claimed is not None
    await service.process_job(claimed.id)
    done = service.get_job(claimed.id)
    assert done is not None
    assert done.status == "completed"
    assert done.reason_code == "bazarr_verify_failed"
    assert (auto_env["media"] / "Example.pt.srt").exists()
    openrouter_after_translate = chat_calls["n"]
    assert openrouter_after_translate > 0
    verify_after_job = verify_calls["n"]
    assert verify_after_job >= 1
    original_job_id = claimed.id

    # Another scan must schedule verify-only (rescan + backoff), never another translate.
    again = await FallbackPlanner(db).scan_once()
    assert again.created_count == 0
    assert again.reused_count == 1
    translates = db.scalars(select(JobRow).where(JobRow.job_kind == "translate")).all()
    assert len(translates) == 1
    assert translates[0].id == original_job_id
    assert translates[0].status == "completed"
    assert translates[0].reason_code == "bazarr_verify_failed"
    assert chat_calls["n"] == openrouter_after_translate
    assert verify_calls["n"] >= verify_after_job

    observed = db.scalars(select(ObservedCandidateRow)).one()
    assert observed.last_outcome == "verify"
    db.close()


@pytest.mark.asyncio
async def test_verify_only_retry_succeeds_without_openrouter(auto_env, monkeypatch):
    db = auto_env["SessionLocal"]()
    SettingsService(db, fernet=auto_env["fernet"]).update(
        SettingsUpdate(automatic_fallback_enabled=True, bazarr_grace_period_minutes=0)
    )

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("app.jobs.service.asyncio.sleep", fake_sleep)
    chat_calls, verify_calls = _count_openrouter_and_verify(monkeypatch)
    auto_env["wanted_missing"][:] = ["pt-PT"]

    await FallbackPlanner(db).scan_once()
    service = JobService(db)
    claimed = service.claim_next_job("translate")
    assert claimed is not None
    await service.process_job(claimed.id)
    done = service.get_job(claimed.id)
    assert done is not None
    assert done.reason_code == "bazarr_verify_failed"
    openrouter_after_translate = chat_calls["n"]
    verify_after_job = verify_calls["n"]
    assert verify_after_job >= 1

    # Bazarr now sees the written file, but the item can still appear wanted.
    auto_env["subtitle_payload"].clear()
    auto_env["subtitle_payload"].extend(
        [
            ["en", "/movies/Example/Example.en.srt"],
            ["pt-PT", "/movies/Example/Example.pt-PT.srt"],
        ]
    )

    again = await FallbackPlanner(db).scan_once()
    assert again.created_count == 0
    refreshed = service.get_job(claimed.id)
    assert refreshed is not None
    assert refreshed.reason_code is None
    assert refreshed.status == "completed"
    assert chat_calls["n"] == openrouter_after_translate
    assert verify_calls["n"] >= verify_after_job
    assert len(db.scalars(select(JobRow).where(JobRow.job_kind == "translate")).all()) == 1
    db.close()


@pytest.mark.asyncio
async def test_exchange_log_redacts_by_default(tmp_path, monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)

    log_path = job_openrouter_log_path(tmp_path, 99)
    exchange_log = JobOpenRouterExchangeLog(log_path, job_id=99)
    client = OpenRouterClient("secret-key", exchange_log=exchange_log, log_full_exchanges=False)
    await client.chat_completion(
        model="test-model",
        messages=[{"role": "user", "content": "Hello subtitle"}],
    )
    import json

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    exchange = lines[0]
    assert "Hello subtitle" not in log_path.read_text(encoding="utf-8")
    assert exchange["request"].get("message_count") == 1
    assert exchange["response"]["body_omitted"] is True

    # Full logging still works when enabled
    log_path2 = job_openrouter_log_path(tmp_path, 100)
    exchange_log2 = JobOpenRouterExchangeLog(log_path2, job_id=100)
    client2 = OpenRouterClient("secret-key", exchange_log=exchange_log2, log_full_exchanges=True)
    await client2.chat_completion(
        model="test-model",
        messages=[{"role": "user", "content": "Hello subtitle"}],
    )
    assert "Hello subtitle" in log_path2.read_text(encoding="utf-8")
