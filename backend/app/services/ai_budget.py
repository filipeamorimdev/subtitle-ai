"""Monthly AI budget tracking with SQLite-backed reservations."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AiBudgetReservationRow, AiUsageRecordRow, SettingsRow
from app.services.ai_cost import effective_cost_micro, micro_to_usd

logger = get_logger("ai_budget")

# Process-wide lock so concurrent worker/API sessions serialize check+insert+commit.
# Combined with committing inside the lock this enforces:
# sum(active reservations) <= remaining budget.
_BUDGET_LOCK = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def month_key(dt: datetime | None = None) -> str:
    when = dt or utcnow()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m")


@dataclass
class BudgetStatus:
    enabled: bool
    limit_micro_usd: int | None
    used_micro_usd: int
    reserved_micro_usd: int
    remaining_micro_usd: int | None
    allow_manual_override: bool

    @property
    def limit_usd(self) -> float | None:
        return micro_to_usd(self.limit_micro_usd)

    @property
    def used_usd(self) -> float:
        return micro_to_usd(self.used_micro_usd) or 0.0

    @property
    def remaining_usd(self) -> float | None:
        return micro_to_usd(self.remaining_micro_usd)

    @property
    def percent_used(self) -> float | None:
        if not self.enabled or not self.limit_micro_usd:
            return None
        if self.limit_micro_usd <= 0:
            return 100.0
        return min(100.0, (self.used_micro_usd + self.reserved_micro_usd) / self.limit_micro_usd * 100.0)


class BudgetBlockedError(Exception):
    def __init__(self, message: str, *, reason: str = "monthly_budget") -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = False


class AiBudgetService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _settings(self) -> SettingsRow:
        row = self.db.get(SettingsRow, 1)
        if row is None:
            raise RuntimeError("Settings row missing")
        return row

    def spent_micro_usd(self, *, key: str | None = None) -> int:
        """Sum paid usage costs for the UTC month (actual preferred)."""
        mk = key or month_key()
        start = datetime.strptime(mk, "%Y-%m").replace(tzinfo=timezone.utc)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)

        rows = self.db.scalars(
            select(AiUsageRecordRow).where(
                AiUsageRecordRow.created_at >= start,
                AiUsageRecordRow.created_at < end,
                AiUsageRecordRow.tier == "paid",
            )
        ).all()
        return sum(effective_cost_micro(r) for r in rows)

    def reserved_micro_usd(self, *, key: str | None = None) -> int:
        mk = key or month_key()
        total = self.db.scalar(
            select(func.coalesce(func.sum(AiBudgetReservationRow.amount_micro_usd), 0)).where(
                AiBudgetReservationRow.month_key == mk,
                AiBudgetReservationRow.released_at.is_(None),
            )
        )
        return int(total or 0)

    def status(self) -> BudgetStatus:
        settings = self._settings()
        enabled = bool(getattr(settings, "monthly_budget_enabled", False))
        limit = getattr(settings, "monthly_budget_amount_micro_usd", None)
        used = self.spent_micro_usd()
        reserved = self.reserved_micro_usd()
        remaining = None
        if enabled and limit is not None:
            remaining = max(0, int(limit) - used - reserved)
        return BudgetStatus(
            enabled=enabled,
            limit_micro_usd=int(limit) if limit is not None else None,
            used_micro_usd=used,
            reserved_micro_usd=reserved,
            remaining_micro_usd=remaining,
            allow_manual_override=bool(getattr(settings, "allow_manual_budget_override", False)),
        )

    def can_bypass(self, *, trigger_type: str) -> bool:
        if trigger_type != "manual":
            return False
        return bool(getattr(self._settings(), "allow_manual_budget_override", False))

    def reserve(
        self,
        *,
        amount_micro_usd: int,
        job_id: int | None,
        trigger_type: str = "manual",
        tier: str = "paid",
    ) -> AiBudgetReservationRow | None:
        """
        Atomically reserve budget for a paid request.

        Returns None when reservation is not needed (budget disabled, free tier,
        or manual override). Raises BudgetBlockedError when blocked.

        Commits the reservation before returning so callers cannot await between
        insert and commit. Serialized with a process-wide lock appropriate for the
        single-service SQLite deployment.
        """
        if tier != "paid" or amount_micro_usd <= 0:
            return None

        with _BUDGET_LOCK:
            settings = self._settings()
            if not bool(getattr(settings, "monthly_budget_enabled", False)):
                return None
            if self.can_bypass(trigger_type=trigger_type):
                return None

            limit = getattr(settings, "monthly_budget_amount_micro_usd", None)
            if limit is None:
                return None

            mk = month_key()
            try:
                used = self.spent_micro_usd(key=mk)
                reserved = self.reserved_micro_usd(key=mk)
                remaining = int(limit) - used - reserved
                if amount_micro_usd > remaining:
                    logger.info(
                        "Budget blocked job=%s remaining=%s needed=%s",
                        job_id,
                        remaining,
                        amount_micro_usd,
                    )
                    self.db.rollback()
                    raise BudgetBlockedError(
                        f"Monthly AI budget exceeded (remaining ${remaining / 1_000_000:.4f}, "
                        f"needed ${amount_micro_usd / 1_000_000:.4f}).",
                        reason="monthly_budget",
                    )

                row = AiBudgetReservationRow(
                    job_id=job_id,
                    month_key=mk,
                    amount_micro_usd=amount_micro_usd,
                )
                self.db.add(row)
                self.db.commit()
                self.db.refresh(row)
                return row
            except BudgetBlockedError:
                raise
            except Exception:
                self.db.rollback()
                raise

    def release(self, reservation: AiBudgetReservationRow | None) -> None:
        if reservation is None:
            return
        with _BUDGET_LOCK:
            try:
                row = self.db.get(AiBudgetReservationRow, reservation.id)
                if row is None or row.released_at is not None:
                    return
                row.released_at = utcnow()
                self.db.add(row)
                self.db.commit()
                reservation.released_at = row.released_at
            except Exception:
                self.db.rollback()
                raise

    def fits_per_job_limit(self, estimated_micro: int | None) -> bool:
        settings = self._settings()
        cap = getattr(settings, "maximum_cost_per_job_micro_usd", None)
        if cap is None or estimated_micro is None:
            return True
        return estimated_micro <= int(cap)
