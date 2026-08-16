"""Provider-agnostic AI layer (BYOAI).

v0.3-alpha1 establishes the provider abstraction; only OpenRouter is implemented.
"""

from app.ai.errors import (
    AIProviderError,
    AuthenticationError,
    ContextLimitError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.ai.models import (
    CAPABILITY_BATCH,
    CAPABILITY_FUNCTION_CALLING,
    CAPABILITY_STREAMING,
    CAPABILITY_STRUCTURED_OUTPUT,
    CAPABILITY_TEXT_GENERATION,
    CAPABILITY_VISION,
    AIModel,
    AIModelCandidate,
    AIResponse,
    CostSource,
    Message,
    PricingFreshness,
    PricingTier,
    ProviderHealth,
    ProviderStatus,
)

__all__ = [
    "AIModel",
    "AIModelCandidate",
    "AIProviderError",
    "AIResponse",
    "AuthenticationError",
    "CAPABILITY_BATCH",
    "CAPABILITY_FUNCTION_CALLING",
    "CAPABILITY_STREAMING",
    "CAPABILITY_STRUCTURED_OUTPUT",
    "CAPABILITY_TEXT_GENERATION",
    "CAPABILITY_VISION",
    "ContextLimitError",
    "CostSource",
    "InvalidRequestError",
    "Message",
    "ModelNotFoundError",
    "PricingFreshness",
    "PricingTier",
    "ProviderHealth",
    "ProviderStatus",
    "ProviderUnavailableError",
    "RateLimitError",
]
