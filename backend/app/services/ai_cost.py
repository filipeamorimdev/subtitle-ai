"""Authoritative AI cost calculation using Decimal / micro-USD."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MICRO_USD = Decimal("1000000")
MILLION = Decimal("1000000")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def usd_to_micro(value: Decimal | float | int | str | None) -> int | None:
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return int((dec * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def micro_to_usd(value: int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(value) / MICRO_USD)


def price_per_million_to_micro(value: float | Decimal | None) -> int | None:
    """Store USD-per-million as micro-USD-per-million (same numeric scale * 1e6)."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return int((dec * MICRO_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def micro_price_to_per_million(value: int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(value) / MICRO_USD)


@dataclass(frozen=True)
class CostBreakdown:
    input_cost_micro_usd: int
    output_cost_micro_usd: int
    total_cost_micro_usd: int
    input_price_micro_usd_per_million: int | None
    output_price_micro_usd_per_million: int | None
    pricing_source: str  # estimated | openrouter | none
    pricing_timestamp: datetime


def estimate_cost_micro_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: float | None,
    output_price_per_million: float | None,
    actual_cost_usd: float | None = None,
    pricing_timestamp: datetime | None = None,
) -> CostBreakdown:
    """
    Estimate cost from tokens and USD-per-million prices.

    If OpenRouter reports actual_cost_usd, that becomes the authoritative actual
    (callers store it separately); estimated still uses snapshots.
    """
    ts = pricing_timestamp or utcnow()
    in_price_micro = price_per_million_to_micro(input_price_per_million)
    out_price_micro = price_per_million_to_micro(output_price_per_million)

    if in_price_micro is None or out_price_micro is None:
        actual_micro = usd_to_micro(actual_cost_usd)
        return CostBreakdown(
            input_cost_micro_usd=0,
            output_cost_micro_usd=0,
            total_cost_micro_usd=actual_micro or 0,
            input_price_micro_usd_per_million=in_price_micro,
            output_price_micro_usd_per_million=out_price_micro,
            pricing_source="openrouter" if actual_micro is not None else "none",
            pricing_timestamp=ts,
        )

    input_cost = (
        Decimal(input_tokens) / MILLION * Decimal(in_price_micro)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    output_cost = (
        Decimal(output_tokens) / MILLION * Decimal(out_price_micro)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    total = int(input_cost + output_cost)

    if actual_cost_usd is not None:
        # Prefer reported cost for total when present; keep estimated components.
        return CostBreakdown(
            input_cost_micro_usd=int(input_cost),
            output_cost_micro_usd=int(output_cost),
            total_cost_micro_usd=usd_to_micro(actual_cost_usd) or total,
            input_price_micro_usd_per_million=in_price_micro,
            output_price_micro_usd_per_million=out_price_micro,
            pricing_source="openrouter",
            pricing_timestamp=ts,
        )

    return CostBreakdown(
        input_cost_micro_usd=int(input_cost),
        output_cost_micro_usd=int(output_cost),
        total_cost_micro_usd=total,
        input_price_micro_usd_per_million=in_price_micro,
        output_price_micro_usd_per_million=out_price_micro,
        pricing_source="estimated",
        pricing_timestamp=ts,
    )


def estimate_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: float | None,
    output_price_per_million: float | None,
) -> float | None:
    if input_price_per_million is None or output_price_per_million is None:
        return None
    breakdown = estimate_cost_micro_usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    )
    return micro_to_usd(breakdown.total_cost_micro_usd)


def estimate_request_cost_micro(
    *,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    input_price_per_million: float | None,
    output_price_per_million: float | None,
) -> int | None:
    """Upper-bound estimate used for budget / per-job gating. None if unknown pricing."""
    if input_price_per_million is None or output_price_per_million is None:
        return None
    return estimate_cost_micro_usd(
        input_tokens=estimated_input_tokens,
        output_tokens=estimated_output_tokens,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
    ).total_cost_micro_usd


def effective_cost_micro(row: Any) -> int:
    """Prefer actual cost, else estimated."""
    actual = getattr(row, "actual_cost_micro_usd", None)
    if actual is not None:
        return int(actual)
    estimated = getattr(row, "estimated_cost_micro_usd", None)
    if estimated is not None:
        return int(estimated)
    return 0
