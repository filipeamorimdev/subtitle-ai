"""Monthly budget reservation tests."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import AiUsageRecordRow, SettingsRow
from app.services.ai_budget import AiBudgetService, BudgetBlockedError


def _budget_db(tmp_path, *, used=0, limit=1_000_000, enabled=True):
    engine = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(
        SettingsRow(
            id=1,
            openrouter_model="x",
            monthly_budget_enabled=enabled,
            monthly_budget_amount_micro_usd=limit,
            allow_manual_budget_override=False,
        )
    )
    if used:
        db.add(
            AiUsageRecordRow(
                operation_type="translation",
                trigger_type="automatic",
                model_id="paid/a",
                tier="paid",
                status="success",
                estimated_cost_micro_usd=used,
                actual_cost_micro_usd=used,
            )
        )
    db.commit()
    return db


def test_monthly_budget_blocks(tmp_path):
    db = _budget_db(tmp_path, used=990_000, limit=1_000_000)
    svc = AiBudgetService(db)
    try:
        svc.reserve(amount_micro_usd=30_000, job_id=1, trigger_type="automatic", tier="paid")
        raise AssertionError("should block")
    except BudgetBlockedError:
        pass


def test_concurrent_reservations(tmp_path):
    db = _budget_db(tmp_path, used=0, limit=50_000)
    svc = AiBudgetService(db)
    a = svc.reserve(amount_micro_usd=40_000, job_id=1, trigger_type="automatic", tier="paid")
    db.commit()
    assert a is not None
    try:
        svc.reserve(amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid")
        raise AssertionError("second reserve should block")
    except BudgetBlockedError:
        pass
    svc.release(a)
    db.commit()
    b = svc.reserve(amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid")
    assert b is not None


def test_manual_override(tmp_path):
    db = _budget_db(tmp_path, used=1_000_000, limit=1_000_000)
    row = db.get(SettingsRow, 1)
    row.allow_manual_budget_override = True
    db.commit()
    svc = AiBudgetService(db)
    assert svc.reserve(amount_micro_usd=50_000, job_id=1, trigger_type="manual", tier="paid") is None
    try:
        svc.reserve(amount_micro_usd=50_000, job_id=2, trigger_type="automatic", tier="paid")
        raise AssertionError("automatic must not override")
    except BudgetBlockedError:
        pass


def test_free_skips_reservation(tmp_path):
    db = _budget_db(tmp_path, used=1_000_000, limit=1_000_000)
    svc = AiBudgetService(db)
    assert svc.reserve(amount_micro_usd=50_000, job_id=1, trigger_type="automatic", tier="free") is None
