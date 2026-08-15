"""Unit tests for job usage aggregation."""

from __future__ import annotations

import pytest

from app.jobs.usage import (
    ModelPricing,
    aggregate_usage,
    classify_exchange,
    estimate_cost_usd,
    parse_exchanges,
)


def test_classify_exchange_kinds():
    assert (
        classify_exchange(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "Classify media into a franchise universe for subtitle glossary sharing.",
                    }
                ]
            }
        )
        == "glossary_universe"
    )
    assert (
        classify_exchange(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract audiovisual glossary terms for subtitle translation consistency.",
                    }
                ]
            }
        )
        == "glossary_extract"
    )
    assert (
        classify_exchange(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Translate ONLY these 2 missing subtitle blocks. Return exactly 2 blocks.",
                    }
                ]
            }
        )
        == "repair"
    )
    assert (
        classify_exchange(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional audiovisual subtitle translator.",
                    }
                ]
            }
        )
        == "translate"
    )
    assert classify_exchange({"messages": [{"role": "user", "content": "hi"}]}) == "other"


def test_estimate_and_parse_exchanges():
    pricing = {
        "openai/gpt-4o-mini": ModelPricing(
            name="GPT-4o Mini",
            prompt_price_per_million=0.15,
            completion_price_per_million=0.60,
        )
    }
    cost = estimate_cost_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        pricing=pricing["openai/gpt-4o-mini"],
    )
    assert cost == 0.75

    entries = [
        {"event": "job_start", "model": "openai/gpt-4o-mini"},
        {
            "event": "exchange",
            "ts": "2026-08-09T12:00:00Z",
            "attempt": 1,
            "request": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional audiovisual subtitle translator.",
                    }
                ],
            },
            "response": {
                "status_code": 200,
                "body": {
                    "model": "openai/gpt-4o-mini",
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "total_tokens": 1500,
                    },
                },
            },
            "error": None,
        },
        {
            "event": "exchange",
            "ts": "2026-08-09T12:01:00Z",
            "attempt": 1,
            "request": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract audiovisual glossary terms for subtitle translation consistency.",
                    }
                ],
            },
            "response": {
                "status_code": 200,
                "body": {
                    "model": "openai/gpt-4o-mini",
                    "usage": {
                        "prompt_tokens": 200,
                        "completion_tokens": 100,
                        "total_tokens": 300,
                        "cost": 0.001,
                    },
                },
            },
            "error": None,
        },
    ]
    rows = parse_exchanges(
        entries,
        fallback_model="openai/gpt-4o-mini",
        pricing_by_model=pricing,
    )
    assert len(rows) == 2
    assert rows[0]["action"] == "translate"
    assert rows[0]["cost_estimated"] is True
    assert rows[0]["cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1_000_000)
    assert rows[1]["action"] == "glossary_extract"
    assert rows[1]["cost_estimated"] is False
    assert rows[1]["cost_usd"] == 0.001

    agg = aggregate_usage(rows)
    assert agg["requests"] == 2
    assert agg["total_tokens"] == 1800
    assert agg["pricing_source"] == "mixed"
    assert len(agg["by_model"]) == 1
    assert {a["action"] for a in agg["by_action"]} == {"translate", "glossary_extract"}


def test_legacy_parse_without_live_catalog_does_not_reprice():
    entries = [
        {
            "event": "exchange",
            "request": {
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional audiovisual subtitle translator.",
                    }
                ],
            },
            "response": {
                "status_code": 200,
                "body": {
                    "model": "openai/gpt-4o-mini",
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                        "total_tokens": 1500,
                    },
                },
            },
            "error": None,
        }
    ]
    rows = parse_exchanges(
        entries,
        fallback_model="openai/gpt-4o-mini",
        pricing_by_model={},
    )
    assert len(rows) == 1
    assert rows[0]["cost_usd"] is None
    assert rows[0]["cost_estimated"] is False
