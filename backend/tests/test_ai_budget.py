"""Monthly budget reservation tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.db.models import AiBudgetReservationRow, AiUsageRecordRow, SettingsRow
from app.services.ai_budget import AiBudgetService, BudgetBlockedError


def _budget_engine(tmp_path, *, used=0, limit=1_000_000, enabled=True, name="b.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
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
    db.close()
    return engine, Session


def _budget_db(tmp_path, *, used=0, limit=1_000_000, enabled=True):
    engine, Session = _budget_engine(tmp_path, used=used, limit=limit, enabled=enabled)
    return Session()


def test_monthly_budget_blocks(tmp_path):
    db = _budget_db(tmp_path, used=990_000, limit=1_000_000)
    svc = AiBudgetService(db)
    with pytest.raises(BudgetBlockedError):
        svc.reserve(amount_micro_usd=30_000, job_id=1, trigger_type="automatic", tier="paid")


def test_concurrent_reservations_sequential_release(tmp_path):
    db = _budget_db(tmp_path, used=0, limit=50_000)
    svc = AiBudgetService(db)
    a = svc.reserve(amount_micro_usd=40_000, job_id=1, trigger_type="automatic", tier="paid")
    assert a is not None
    with pytest.raises(BudgetBlockedError):
        svc.reserve(amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid")
    svc.release(a)
    b = svc.reserve(amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid")
    assert b is not None


def test_concurrent_reservations_two_sessions(tmp_path):
    """Two threads / two sessions cannot both consume the same remaining budget."""
    engine, Session = _budget_engine(tmp_path, used=0, limit=50_000, name="race.db")
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
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(attempt, 1), pool.submit(attempt, 2)]
        for fut in futs:
            fut.result(timeout=10)

    assert len(results) == 1, f"expected one success, got {len(results)}; errors={errors}"
    assert len(errors) == 1
    assert isinstance(errors[0], BudgetBlockedError)

    check = Session()
    active = list(
        check.scalars(
            select(AiBudgetReservationRow).where(AiBudgetReservationRow.released_at.is_(None))
        ).all()
    )
    assert len(active) == 1
    assert sum(r.amount_micro_usd for r in active) == 40_000
    check.close()


def test_release_restores_capacity(tmp_path):
    engine, Session = _budget_engine(tmp_path, used=0, limit=50_000, name="release.db")
    db1 = Session()
    a = AiBudgetService(db1).reserve(
        amount_micro_usd=40_000, job_id=1, trigger_type="automatic", tier="paid"
    )
    assert a is not None
    db1.close()

    db2 = Session()
    with pytest.raises(BudgetBlockedError):
        AiBudgetService(db2).reserve(
            amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid"
        )
    # Release via a fresh session load.
    row = db2.get(AiBudgetReservationRow, a.id)
    AiBudgetService(db2).release(row)
    b = AiBudgetService(db2).reserve(
        amount_micro_usd=40_000, job_id=2, trigger_type="automatic", tier="paid"
    )
    assert b is not None
    db2.close()


def test_manual_override(tmp_path):
    db = _budget_db(tmp_path, used=1_000_000, limit=1_000_000)
    row = db.get(SettingsRow, 1)
    row.allow_manual_budget_override = True
    db.commit()
    svc = AiBudgetService(db)
    assert svc.reserve(amount_micro_usd=50_000, job_id=1, trigger_type="manual", tier="paid") is None
    with pytest.raises(BudgetBlockedError):
        svc.reserve(amount_micro_usd=50_000, job_id=2, trigger_type="automatic", tier="paid")


def test_free_skips_reservation(tmp_path):
    db = _budget_db(tmp_path, used=1_000_000, limit=1_000_000)
    svc = AiBudgetService(db)
    assert svc.reserve(amount_micro_usd=50_000, job_id=1, trigger_type="automatic", tier="free") is None
