"""Cost accounting tests."""

from __future__ import annotations

from app.services.ai_cost import (
    estimate_conservative_job_cost_micro,
    estimate_cost_micro_usd,
    estimate_request_cost_micro,
    micro_to_usd,
)


def test_zero_and_paid_cost():
    zero = estimate_cost_micro_usd(
        input_tokens=1000,
        output_tokens=500,
        input_price_per_million=0.0,
        output_price_per_million=0.0,
    )
    assert zero.total_cost_micro_usd == 0
    paid = estimate_cost_micro_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        input_price_per_million=0.15,
        output_price_per_million=0.60,
    )
    assert micro_to_usd(paid.total_cost_micro_usd) == 0.75
    unknown = estimate_cost_micro_usd(
        input_tokens=10,
        output_tokens=10,
        input_price_per_million=None,
        output_price_per_million=None,
    )
    assert unknown.pricing_source == "none"


def test_actual_cost_snapshot():
    breakdown = estimate_cost_micro_usd(
        input_tokens=100,
        output_tokens=50,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
        actual_cost_usd=0.001,
    )
    assert breakdown.pricing_source == "openrouter"
    assert micro_to_usd(breakdown.total_cost_micro_usd) == 0.001


def test_conservative_estimate_exceeds_naive_subtitle_only():
    chars = 40_000  # 10_000 subtitle tokens
    naive = estimate_request_cost_micro(
        estimated_input_tokens=max(1, chars // 4),
        estimated_output_tokens=max(1, chars // 4),
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    )
    cons = estimate_conservative_job_cost_micro(
        char_count=chars,
        input_price_per_million=1.0,
        output_price_per_million=2.0,
    )
    assert cons.conservative_cost_micro_usd is not None
    assert naive is not None
    assert cons.conservative_cost_micro_usd > naive
    assert cons.input_tokens > chars // 4
    assert cons.output_tokens > chars // 4


def test_conservative_unknown_pricing():
    cons = estimate_conservative_job_cost_micro(
        char_count=1000,
        input_price_per_million=None,
        output_price_per_million=None,
    )
    assert cons.estimated_cost_micro_usd is None
    assert cons.conservative_cost_micro_usd is None
