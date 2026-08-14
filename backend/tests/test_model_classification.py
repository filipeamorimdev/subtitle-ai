"""Free/paid/unknown classification and compatibility filtering."""

from __future__ import annotations

from app.services.model_catalog import check_compatibility, classify_pricing_tier
from app.translation.openrouter.client import OpenRouterModelInfo


def _info(**kwargs) -> OpenRouterModelInfo:
    defaults = dict(
        id="prov/model",
        name="Model",
        prompt_price_per_million=0.0,
        completion_price_per_million=0.0,
        context_length=128000,
        input_modalities=["text"],
        output_modalities=["text"],
    )
    defaults.update(kwargs)
    return OpenRouterModelInfo(**defaults)


def test_classify_free_paid_unknown():
    assert classify_pricing_tier(0.0, 0.0) == "free"
    assert classify_pricing_tier(0.15, 0.6) == "paid"
    assert classify_pricing_tier(None, 0.0) == "unknown"
    assert classify_pricing_tier(0.0, None) == "unknown"
    assert classify_pricing_tier(None, None) == "unknown"
    assert _info().pricing_tier == "free"
    assert _info(prompt_price_per_million=1.0, completion_price_per_million=0.0).pricing_tier == "paid"


def test_missing_price_is_not_treated_as_free():
    unknown = _info(prompt_price_per_million=None, completion_price_per_million=None)
    assert unknown.pricing_tier == "unknown"
    assert unknown.pricing_tier != "free"


def test_compatible_text_models():
    ok, reason = check_compatibility(_info(), batch_size=25)
    assert ok
    assert "Compatible" in reason


def test_incompatible_non_text_and_tiny_context():
    bad, reason = check_compatibility(_info(output_modalities=["image"]), batch_size=25)
    assert not bad
    assert reason == "Not compatible with Subtitle AI translation"
    tiny, tiny_reason = check_compatibility(_info(context_length=100), batch_size=25)
    assert not tiny
    assert tiny_reason == "Not compatible with Subtitle AI translation"
