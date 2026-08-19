"""OpenRouter provider adapter — wraps OpenRouterClient behind AIProvider."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.ai.errors import (
    AIProviderError,
    AuthenticationError,
    ContextLimitError,
    InvalidRequestError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.ai.metadata import sanitize_metadata
from app.ai.models import (
    CAPABILITY_BATCH,
    CAPABILITY_TEXT_GENERATION,
    CAPABILITY_VISION,
    AIModel,
    AIResponse,
    Message,
    PricingTier,
    ProviderHealth,
    ProviderStatus,
)
from app.ai.providers.base import AIProvider
from app.core.logging import get_logger
from app.translation.openrouter.client import (
    BatchChatRequest,
    ChatResult,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterModelInfo,
    batch_base_model,
    is_batch_model,
)
from app.translation.openrouter.exchange_log import ExchangeRecorder

logger = get_logger("ai.openrouter")

PROVIDER_ID = "openrouter"
PROVIDER_DISPLAY_NAME = "OpenRouter"
HEALTH_CACHE_TTL = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def openrouter_error_to_provider_error(
    exc: OpenRouterError,
    *,
    provider_id: str = PROVIDER_ID,
    model_id: str | None = None,
) -> AIProviderError:
    """Map OpenRouterError → normalized AIProviderError, preserving retry hints."""
    status = getattr(exc, "status_code", None)
    message = str(exc)
    lowered = message.lower()
    retry_after = getattr(exc, "retry_after_seconds", None)
    kwargs: dict[str, Any] = {
        "status_code": status,
        "retry_after_seconds": retry_after,
        "is_retryable": bool(getattr(exc, "retryable", False)),
        "original_error": exc,
        "provider_id": provider_id,
        "model_id": model_id,
    }

    if status == 401 or "authentication" in lowered or "auth" in lowered:
        return AuthenticationError(message, **kwargs)
    if status == 429 or "rate limited" in lowered:
        return RateLimitError(message, **kwargs)
    if status == 404 or "model not found" in lowered:
        return ModelNotFoundError(message, **kwargs)
    if "context" in lowered:
        return ContextLimitError(message, **kwargs)
    if "malformed" in lowered or "invalid" in lowered:
        return InvalidRequestError(message, **kwargs)
    if status == 408 or "timed out" in lowered or "timeout" in lowered or "connection" in lowered:
        return ProviderUnavailableError(message, category="timeout", **kwargs)
    if status is not None and status >= 500:
        return ProviderUnavailableError(message, **kwargs)
    if getattr(exc, "retryable", False):
        return ProviderUnavailableError(message, **kwargs)
    return AIProviderError(message, category="provider_error", **kwargs)


def normalize_openrouter_model(info: OpenRouterModelInfo) -> AIModel:
    """Convert OpenRouter catalog entry → generic AIModel."""
    capabilities: set[str] = {CAPABILITY_TEXT_GENERATION}
    inputs = {m.lower() for m in (info.input_modalities or [])}
    if any(x in inputs for x in ("image", "vision")):
        capabilities.add(CAPABILITY_VISION)
    if is_batch_model(info.id):
        capabilities.add(CAPABILITY_BATCH)

    tier = PricingTier.from_value(info.pricing_tier)
    metadata: dict[str, Any] = {}
    if info.input_modalities:
        metadata["input_modalities"] = list(info.input_modalities)
    if info.output_modalities:
        metadata["output_modalities"] = list(info.output_modalities)
    if info.architecture:
        # Keep only non-content architecture hints.
        safe_arch = {
            k: v
            for k, v in info.architecture.items()
            if k in ("modality", "tokenizer", "instruct_type")
        }
        if safe_arch:
            metadata["architecture"] = safe_arch

    return AIModel(
        provider_id=PROVIDER_ID,
        model_id=info.id,
        name=info.name,
        context_length=info.context_length,
        input_price_per_million=_as_decimal(info.prompt_price_per_million),
        output_price_per_million=_as_decimal(info.completion_price_per_million),
        capabilities=capabilities,
        pricing_tier=tier,
        available=True,
        description=info.description,
        metadata=metadata,
    )


def normalize_chat_result(
    result: ChatResult,
    *,
    provider_id: str = PROVIDER_ID,
    model_id: str | None = None,
    request_id: str | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> AIResponse:
    cost = _as_decimal(result.cost_usd)
    return AIResponse(
        provider_id=provider_id,
        model_id=result.model or model_id or "",
        content=result.content,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        actual_cost_usd=cost,
        latency_ms=result.latency_ms,
        request_id=request_id,
        raw_metadata=sanitize_metadata(raw_metadata),
    )


def _messages_to_dicts(messages: list[Message] | list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in messages:
        if isinstance(msg, Message):
            out.append({"role": msg.role, "content": msg.content})
        elif isinstance(msg, dict):
            out.append({"role": str(msg.get("role", "user")), "content": str(msg.get("content", ""))})
        else:
            raise TypeError(f"Unsupported message type: {type(msg)}")
    return out


class OpenRouterProvider(AIProvider):
    provider_id = PROVIDER_ID
    display_name = PROVIDER_DISPLAY_NAME

    def __init__(
        self,
        db: Session | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        exchange_log: ExchangeRecorder | None = None,
        log_full_exchanges: bool = False,
        api_key_resolver: Callable[[], str | None] | None = None,
    ) -> None:
        self.db = db
        self._api_key = api_key
        self._base_url = base_url
        self.exchange_log = exchange_log
        self.log_full_exchanges = log_full_exchanges
        self._api_key_resolver = api_key_resolver
        self._health_cache: dict[str, tuple[datetime, ProviderHealth]] = {}

    def _resolve_api_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        if self._api_key_resolver:
            return self._api_key_resolver()
        if self.db is not None:
            from app.ai.credentials.service import ProviderAccountService

            return ProviderAccountService(self.db).get_api_key(PROVIDER_ID)
        return None

    def _resolve_base_url(self) -> str | None:
        if self._base_url:
            return self._base_url
        if self.db is not None:
            from app.ai.credentials.service import ProviderAccountService

            account = ProviderAccountService(self.db).get_account(PROVIDER_ID)
            if account and account.base_url:
                return account.base_url
        return None

    def is_configured(self) -> bool:
        try:
            key = self._resolve_api_key()
        except Exception:  # noqa: BLE001
            return False
        return bool(key and key.strip())

    def supports(self, capability: str) -> bool:
        if capability in (CAPABILITY_TEXT_GENERATION, CAPABILITY_BATCH):
            return True
        return False

    def invalidate_health_cache(self) -> None:
        self._health_cache.clear()

    def _build_client(self, api_key: str | None = None) -> OpenRouterClient:
        key = api_key if api_key is not None else self._resolve_api_key()
        if not key:
            raise AuthenticationError(
                "OpenRouter API key is not configured",
                provider_id=PROVIDER_ID,
            )
        kwargs: dict[str, Any] = {
            "exchange_log": self.exchange_log,
            "log_full_exchanges": self.log_full_exchanges,
        }
        base = self._resolve_base_url()
        if base:
            kwargs["base_url"] = base
        return OpenRouterClient(key, **kwargs)

    async def test_connection(
        self,
        model_id: str | None = None,
        *,
        force: bool = False,
    ) -> ProviderHealth:
        if not self.is_configured():
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                provider_id=PROVIDER_ID,
                message="OpenRouter API key is not configured",
                configured=False,
                cached=False,
                tested_at=utcnow(),
                model_id=model_id,
            )

        ping_model = model_id
        if not ping_model and self.db is not None:
            from app.services.model_router import first_eligible_ping_model

            candidate = first_eligible_ping_model(self.db)
            if candidate is not None:
                ping_model = candidate.model_id

        cache_key = ping_model or "__none__"
        if not force:
            cached = self._health_cache.get(cache_key)
            if cached is not None:
                tested_at, health = cached
                if utcnow() - tested_at < HEALTH_CACHE_TTL:
                    return ProviderHealth(
                        status=health.status,
                        provider_id=health.provider_id,
                        message=health.message,
                        configured=health.configured,
                        cached=True,
                        tested_at=health.tested_at,
                        model_id=health.model_id,
                    )

        if not ping_model:
            from app.services.model_router import NO_ELIGIBLE_PING_MODEL

            health = ProviderHealth(
                status=ProviderStatus.ERROR,
                provider_id=PROVIDER_ID,
                message=NO_ELIGIBLE_PING_MODEL,
                configured=True,
                cached=False,
                tested_at=utcnow(),
                model_id=None,
            )
            self._health_cache[cache_key] = (utcnow(), health)
            return health

        try:
            client = self._build_client()
            result = await client.test_connection(ping_model)
            health = ProviderHealth(
                status=ProviderStatus.CONNECTED,
                provider_id=PROVIDER_ID,
                message=f"Connected ({result.get('model', ping_model)})",
                configured=True,
                cached=False,
                tested_at=utcnow(),
                model_id=model_id or str(result.get("model") or ping_model),
            )
        except OpenRouterError as exc:
            mapped = openrouter_error_to_provider_error(exc, model_id=ping_model)
            health = ProviderHealth(
                status=ProviderStatus.ERROR,
                provider_id=PROVIDER_ID,
                message=str(mapped),
                configured=True,
                cached=False,
                tested_at=utcnow(),
                model_id=ping_model,
            )
        except Exception as exc:  # noqa: BLE001
            health = ProviderHealth(
                status=ProviderStatus.ERROR,
                provider_id=PROVIDER_ID,
                message=str(exc)[:300],
                configured=True,
                cached=False,
                tested_at=utcnow(),
                model_id=ping_model,
            )

        self._health_cache[cache_key] = (utcnow(), health)
        return health

    async def list_models(self) -> list[AIModel]:
        api_key = self._resolve_api_key()
        base = self._resolve_base_url()
        try:
            kwargs: dict[str, Any] = {"api_key": api_key or None}
            if base:
                kwargs["base_url"] = base
            infos = await OpenRouterClient.list_models(**kwargs)
        except OpenRouterError as exc:
            raise openrouter_error_to_provider_error(exc) from exc
        return [normalize_openrouter_model(info) for info in infos]

    async def chat_completion(
        self,
        *,
        model_id: str,
        messages: list[Message] | list[dict[str, str]],
        temperature: float = 0,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> AIResponse:
        req_id = request_id or str(uuid.uuid4())
        try:
            client = self._build_client()
            result = await client.chat_completion(
                model=model_id,
                messages=_messages_to_dicts(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except OpenRouterError as exc:
            raise openrouter_error_to_provider_error(exc, model_id=model_id) from exc
        return normalize_chat_result(
            result,
            model_id=model_id,
            request_id=req_id,
            raw_metadata={
                "request_id": req_id,
                "response_model": result.model,
                "latency_ms": result.latency_ms,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "total_tokens": result.total_tokens,
                },
            },
        )

    async def run_chat_batch(
        self,
        *,
        model_id: str,
        requests: list[Any],
        **kwargs: Any,
    ) -> dict[str, AIResponse]:
        """Preserve OpenRouter batch API; not part of the minimal generic interface."""
        batch_requests: list[BatchChatRequest] = []
        for item in requests:
            if isinstance(item, BatchChatRequest):
                batch_requests.append(item)
            elif isinstance(item, dict):
                batch_requests.append(
                    BatchChatRequest(
                        custom_id=str(item["custom_id"]),
                        messages=_messages_to_dicts(item["messages"]),
                        temperature=float(item.get("temperature", 0)),
                        max_tokens=item.get("max_tokens"),
                    )
                )
            else:
                raise TypeError(f"Unsupported batch request type: {type(item)}")

        try:
            client = self._build_client()
            # OpenRouterClient.run_chat_batch signature uses model= and requests=
            results = await client.run_chat_batch(
                model=model_id,
                requests=batch_requests,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k in ("poll_interval_s", "max_wait_s", "progress_callback")
                },
            )
        except OpenRouterError as exc:
            raise openrouter_error_to_provider_error(exc, model_id=model_id) from exc

        out: dict[str, AIResponse] = {}
        for custom_id, result in results.items():
            req_id = str(uuid.uuid4())
            out[custom_id] = normalize_chat_result(
                result,
                model_id=model_id,
                request_id=req_id,
                raw_metadata={
                    "request_id": req_id,
                    "batch_id": kwargs.get("batch_id"),
                    "response_model": result.model,
                    "usage": {
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "total_tokens": result.total_tokens,
                    },
                },
            )
        return out

    def with_exchange_log(
        self,
        exchange_log: ExchangeRecorder | None,
        *,
        log_full_exchanges: bool = False,
    ) -> OpenRouterProvider:
        """Return a shallow copy wired for a specific job exchange log."""
        copy = OpenRouterProvider(
            self.db,
            api_key=self._api_key,
            base_url=self._base_url,
            exchange_log=exchange_log,
            log_full_exchanges=log_full_exchanges,
            api_key_resolver=self._api_key_resolver,
        )
        copy._health_cache = self._health_cache
        return copy


# Re-export helpers used by translation for OpenRouter :batch slugs.
__all__ = [
    "PROVIDER_ID",
    "PROVIDER_DISPLAY_NAME",
    "HEALTH_CACHE_TTL",
    "OpenRouterProvider",
    "normalize_openrouter_model",
    "normalize_chat_result",
    "openrouter_error_to_provider_error",
    "batch_base_model",
    "is_batch_model",
]
