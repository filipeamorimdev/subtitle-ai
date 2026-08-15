"""End-to-end routing scenarios A–H from the v0.2 handoff."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.api.schemas import JobCreate, PathMappingIn, SettingsUpdate
from app.core.config import get_app_config
from app.core.secrets import load_or_create_fernet
from app.db import Base
from app.db.models import (
    AiUsageRecordRow,
    OpenRouterCatalogCacheRow,
    OpenRouterModelPreferenceRow,
    SettingsRow,
)
from app.jobs.service import JobService
from app.services.ai_budget import AiBudgetService, BudgetBlockedError
from app.services.settings import SettingsService
from app.translation.openrouter.client import ChatResult, OpenRouterError, OpenRouterModelInfo

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello

2
00:00:04,000 --> 00:00:06,000
World
"""


def _info(model_id, prompt=0.0, completion=0.0):
    return OpenRouterModelInfo(
        id=model_id,
        name=model_id,
        prompt_price_per_million=prompt,
        completion_price_per_million=completion,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
    )


@pytest.fixture
def routing_env(tmp_path, monkeypatch):
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
            path_mappings=[PathMappingIn(bazarr_prefix="/movies", local_prefix=str(tmp_path / "media"))],
            batch_size=50,
        )
    )
    db.close()

    calls: list[str] = []

    async def fake_chat(self, *, model, messages, temperature=0.2, max_tokens=None):
        calls.append(model)
        fail_for = getattr(fake_chat, "fail_for", set())
        if model in fail_for:
            raise OpenRouterError("OpenRouter rate limited.", status_code=429, retryable=True)
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "Classify media into a franchise universe" in system:
            content = '{"universe":"none"}'
        elif "extract audiovisual glossary terms" in system:
            content = '{"terms":[]}'
        else:
            content = "[001]\nOlá\n\n[002]\nMundo\n"
        return ChatResult(content=content, model=model, input_tokens=3, output_tokens=2, total_tokens=5)

    fake_chat.fail_for = set()
    monkeypatch.setattr("app.translation.openrouter.client.OpenRouterClient.chat_completion", fake_chat)
    monkeypatch.setattr("app.jobs.worker.worker.start", lambda: None)
    monkeypatch.setattr("app.jobs.worker.worker.stop", lambda: None)

    async def noop_rescan(self, row):
        return None

    async def noop_verify(self, row):
        return True

    monkeypatch.setattr("app.jobs.service.JobService._rescan", noop_rescan)
    monkeypatch.setattr("app.jobs.service.JobService._verify_target_with_backoff", noop_verify)

    return {
        "SessionLocal": SessionLocal,
        "source": source,
        "media": media,
        "calls": calls,
        "fake_chat": fake_chat,
    }


def _configure(db, *, strategy, prefs, catalog, allow_paid=False, cap_usd=None, monthly_usd=None):
    db.execute(delete(OpenRouterModelPreferenceRow))
    db.execute(delete(OpenRouterCatalogCacheRow))
    settings = db.get(SettingsRow, 1)
    settings.routing_strategy = strategy
    settings.allow_paid_fallback = allow_paid
    settings.allow_unknown_pricing = False
    settings.maximum_cost_per_job_micro_usd = int(cap_usd * 1_000_000) if cap_usd is not None else None
    settings.monthly_budget_enabled = monthly_usd is not None
    settings.monthly_budget_amount_micro_usd = int(monthly_usd * 1_000_000) if monthly_usd is not None else None
    settings.allow_manual_budget_override = False
    db.add(settings)
    db.add(OpenRouterCatalogCacheRow(id=1, payload_json=[m.to_dict() for m in catalog], stale=False))
    for i, (model_id, tier) in enumerate(prefs, start=1):
        db.add(OpenRouterModelPreferenceRow(model_id=model_id, tier=tier, priority=i, enabled=True))
    db.commit()


async def _run_job(db, env, *, trigger="automatic"):
    service = JobService(db)
    job = await service.create_job(
        JobCreate(
            source_subtitle_path=str(env["source"]),
            target_language="pt-PT",
            media_type="movie",
            media_path=str(env["media"] / "Example.mkv"),
            media_title="Example Movie",
            bazarr_movie_id=10,
            source_language="en",
        ),
        trigger_type=trigger,
    )
    claimed = service.claim_next_job() or job
    await service.process_job(claimed.id)
    return service.get_job(claimed.id)


@pytest.mark.asyncio
async def test_scenario_a_free_success(routing_env):
    db = routing_env["SessionLocal"]()
    _configure(
        db,
        strategy="free_only",
        prefs=[("free/a", "free")],
        catalog=[_info("free/a")],
    )
    done = await _run_job(db, routing_env)
    assert done.status == "completed"
    assert done.model == "free/a"
    rows = list(db.scalars(select(AiUsageRecordRow)).all())
    assert rows
    assert all(r.tier == "free" for r in rows)
    assert all((r.estimated_cost_micro_usd or 0) == 0 for r in rows)
    db.close()


@pytest.mark.asyncio
async def test_scenario_b_free_rate_limit_no_paid(routing_env):
    db = routing_env["SessionLocal"]()
    routing_env["fake_chat"].fail_for = {"free/a"}
    _configure(
        db,
        strategy="free_first",
        prefs=[("free/a", "free"), ("free/b", "free"), ("paid/a", "paid")],
        catalog=[_info("free/a"), _info("free/b"), _info("paid/a", 1.0, 2.0)],
        allow_paid=False,
    )
    done = await _run_job(db, routing_env)
    assert done.status == "completed"
    assert done.model == "free/b"
    assert "paid/a" not in routing_env["calls"]
    db.close()


@pytest.mark.asyncio
async def test_scenario_c_paid_fallback(routing_env):
    db = routing_env["SessionLocal"]()
    routing_env["fake_chat"].fail_for = {"free/a", "free/b"}
    _configure(
        db,
        strategy="free_first",
        prefs=[("free/a", "free"), ("free/b", "free"), ("paid/a", "paid")],
        catalog=[_info("free/a"), _info("free/b"), _info("paid/a", 1.0, 2.0)],
        allow_paid=True,
    )
    done = await _run_job(db, routing_env)
    assert done.status == "completed"
    assert done.model == "paid/a"
    assert "paid/a" in routing_env["calls"]
    paid_rows = list(db.scalars(select(AiUsageRecordRow).where(AiUsageRecordRow.tier == "paid")).all())
    assert paid_rows
    db.close()


@pytest.mark.asyncio
async def test_scenario_d_paid_fallback_disabled(routing_env):
    db = routing_env["SessionLocal"]()
    routing_env["fake_chat"].fail_for = {"free/a", "free/b"}
    _configure(
        db,
        strategy="free_first",
        prefs=[("free/a", "free"), ("free/b", "free"), ("paid/a", "paid")],
        catalog=[_info("free/a"), _info("free/b"), _info("paid/a", 1.0, 2.0)],
        allow_paid=False,
    )
    done = await _run_job(db, routing_env)
    assert done.status == "failed"
    assert "paid/a" not in routing_env["calls"]
    db.close()


@pytest.mark.asyncio
async def test_scenario_e_per_job_budget(routing_env):
    db = routing_env["SessionLocal"]()
    _configure(
        db,
        strategy="paid_only",
        prefs=[("paid/a", "paid")],
        catalog=[_info("paid/a", 20.0, 20.0)],
        cap_usd=0.01,
    )
    done = await _run_job(db, routing_env)
    assert done.status == "failed"
    assert done.reason_code == "blocked_by_cost_policy"
    assert routing_env["calls"] == []
    db.close()


@pytest.mark.asyncio
async def test_scenario_f_monthly_budget(routing_env):
    db = routing_env["SessionLocal"]()
    _configure(
        db,
        strategy="paid_only",
        prefs=[("paid/a", "paid")],
        catalog=[_info("paid/a", 15.0, 15.0)],
        monthly_usd=1.00,
    )
    db.add(
        AiUsageRecordRow(
            operation_type="translation",
            trigger_type="automatic",
            model_id="paid/a",
            tier="paid",
            status="success",
            estimated_cost_micro_usd=990_000,
            actual_cost_micro_usd=990_000,
        )
    )
    db.commit()
    done = await _run_job(db, routing_env)
    assert done.status == "failed"
    assert done.reason_code == "blocked_by_cost_policy"
    assert "paid/a" not in routing_env["calls"]
    db.close()


def test_scenario_g_concurrent_budget(tmp_path):
    """Two sessions racing for the same remaining budget: exactly one succeeds."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    engine = create_engine(
        f"sqlite:///{tmp_path / 'g.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = Session()
    seed.add(
        SettingsRow(
            id=1,
            openrouter_model="x",
            monthly_budget_enabled=True,
            monthly_budget_amount_micro_usd=50_000,
            allow_manual_budget_override=False,
        )
    )
    seed.commit()
    seed.close()

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(job_id: int) -> None:
        db = Session()
        try:
            barrier.wait(timeout=5)
            row = AiBudgetService(db).reserve(
                amount_micro_usd=40_000,
                job_id=job_id,
                trigger_type="automatic",
                tier="paid",
            )
            with lock:
                results.append(row)
        except BudgetBlockedError as exc:
            with lock:
                errors.append(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        for fut in (pool.submit(attempt, 1), pool.submit(attempt, 2)):
            fut.result(timeout=10)

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], BudgetBlockedError)


@pytest.mark.asyncio
async def test_scenario_h_existing_install_no_paid_fallback(routing_env):
    db = routing_env["SessionLocal"]()
    settings = db.get(SettingsRow, 1)
    assert settings.openrouter_model == "openai/gpt-4o-mini"
    assert settings.routing_strategy == "paid_only"
    assert settings.allow_paid_fallback is False
    prefs = list(db.scalars(select(OpenRouterModelPreferenceRow)).all())
    assert len(prefs) == 1
    assert prefs[0].model_id == "openai/gpt-4o-mini"
    done = await _run_job(db, routing_env, trigger="manual")
    assert done.status == "completed"
    assert done.model == "openai/gpt-4o-mini"
    db.close()
