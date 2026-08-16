"""Provider-neutral AI model/response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class PricingTier(str, Enum):
    """Canonical pricing tier. Persist as lowercase (free/paid/unknown)."""

    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: str | None) -> PricingTier:
        if value is None:
            return cls.UNKNOWN
        lowered = str(value).strip().lower()
        try:
            return cls(lowered)
        except ValueError:
            return cls.UNKNOWN


class CostSource(str, Enum):
    """How the authoritative historical cost was determined."""

    ESTIMATED = "estimated"
    PROVIDER_ACTUAL = "provider_actual"
    CALCULATED_FROM_USAGE = "calculated_from_usage"


class PricingFreshness(str, Enum):
    """Derived from catalog_fetched_at — not permanently stored."""

    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"


class ProviderStatus(str, Enum):
    CONNECTED = "connected"
    CONFIGURED = "configured"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


# Capability identifiers (no versioning in alpha1).
CAPABILITY_TEXT_GENERATION = "text_generation"
CAPABILITY_BATCH = "batch"
CAPABILITY_STREAMING = "streaming"
CAPABILITY_VISION = "vision"
CAPABILITY_STRUCTURED_OUTPUT = "structured_output"
CAPABILITY_FUNCTION_CALLING = "function_calling"

ALL_CAPABILITIES = frozenset(
    {
        CAPABILITY_TEXT_GENERATION,
        CAPABILITY_BATCH,
        CAPABILITY_STREAMING,
        CAPABILITY_VISION,
        CAPABILITY_STRUCTURED_OUTPUT,
        CAPABILITY_FUNCTION_CALLING,
    }
)


@dataclass
class Message:
    role: str
    content: str


@dataclass
class AIModel:
    provider_id: str
    model_id: str
    name: str
    context_length: int | None = None
    input_price_per_million: Decimal | None = None
    output_price_per_million: Decimal | None = None
    capabilities: set[str] = field(default_factory=lambda: {CAPABILITY_TEXT_GENERATION})
    pricing_tier: PricingTier = PricingTier.UNKNOWN
    available: bool = True
    deprecated: bool = False
    deprecation_date: datetime | None = None
    sunset_date: datetime | None = None
    replacement_model_id: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt_price_per_million(self) -> float | None:
        """Compatibility with legacy catalog consumers."""
        if self.input_price_per_million is None:
            return None
        return float(self.input_price_per_million)

    @property
    def completion_price_per_million(self) -> float | None:
        if self.output_price_per_million is None:
            return None
        return float(self.output_price_per_million)

    @property
    def id(self) -> str:
        """Legacy OpenRouter-style id alias (model_id only)."""
        return self.model_id

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def is_past_sunset(self, *, now: datetime | None = None) -> bool:
        if self.sunset_date is None:
            return False
        from datetime import timezone

        current = now or datetime.now(timezone.utc)
        sunset = self.sunset_date
        if sunset.tzinfo is None:
            sunset = sunset.replace(tzinfo=timezone.utc)
        return current >= sunset


@dataclass
class AIResponse:
    provider_id: str
    model_id: str
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    actual_cost_usd: Decimal | None = None
    latency_ms: int | None = None
    request_id: str | None = None
    raw_metadata: dict[str, Any] | None = None

    @property
    def cost_usd(self) -> float | None:
        if self.actual_cost_usd is None:
            return None
        return float(self.actual_cost_usd)

    @property
    def model(self) -> str:
        return self.model_id


@dataclass
class ProviderHealth:
    status: ProviderStatus
    provider_id: str
    message: str | None = None
    configured: bool = False
    cached: bool = False
    tested_at: datetime | None = None
    model_id: str | None = None


@dataclass
class AIModelCandidate:
    provider_id: str
    model_id: str
    tier: str  # free | paid | unknown (persisted lowercase)
    priority: int
    preference_id: int | None = None
    estimated_cost_micro_usd: int | None = None
    context_length: int | None = None
    skip_reason: str | None = None
    capabilities: set[str] = field(default_factory=set)
