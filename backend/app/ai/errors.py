"""Normalized provider errors with retry hints for the router/job layer."""

from __future__ import annotations

from typing import Any


class AIProviderError(Exception):
    """Base provider error. Router/job retry system decides what to do."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        context_available: bool | None = None,
        is_retryable: bool = False,
        original_error: BaseException | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.context_available = context_available
        self.is_retryable = is_retryable
        self.original_error = original_error
        self.provider_id = provider_id
        self.model_id = model_id
        # Alias used by legacy OpenRouterError consumers.
        self.retryable = is_retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "category": self.category,
            "status_code": self.status_code,
            "retry_after_seconds": self.retry_after_seconds,
            "context_available": self.context_available,
            "is_retryable": self.is_retryable,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
        }


def user_message_for_provider_error(exc: Exception) -> str:
    """User-facing task message. Raw technical detail stays on the execution."""
    category = getattr(exc, "category", None)
    if isinstance(exc, RateLimitError) or category == "rate_limit":
        return "The AI provider is temporarily rate-limiting requests."
    if isinstance(exc, AuthenticationError) or category == "auth_error":
        return "AI provider authentication failed. Check credentials in Settings → Models."
    if isinstance(exc, ContextLimitError) or category == "context_overflow":
        return "This subtitle is too large for the selected model."
    if isinstance(exc, ProviderUnavailableError) or category in {
        "provider_unavailable",
        "timeout",
        "provider_error",
    }:
        if category == "timeout":
            return "The AI provider timed out. The task can be retried."
        return "The AI provider is unavailable. Try again later."
    if isinstance(exc, ModelNotFoundError) or category == "incompatible":
        return "The selected model is not available. Check Settings → Models."
    if isinstance(exc, InvalidRequestError) or category in {"invalid_response", "validation_error"}:
        if category == "validation_error" or "validation" in str(exc).lower():
            return "The translation failed quality checks and was not written."
        return "The AI provider rejected the request."
    message = str(exc)
    if "validation" in message.lower():
        return "The translation failed quality checks and was not written."
    return message


class AuthenticationError(AIProviderError):
    def __init__(self, message: str = "Authentication failed", **kwargs: Any) -> None:
        kwargs.setdefault("category", "auth_error")
        kwargs.setdefault("is_retryable", False)
        kwargs.setdefault("status_code", 401)
        super().__init__(message, **kwargs)


class RateLimitError(AIProviderError):
    def __init__(self, message: str = "Rate limited", **kwargs: Any) -> None:
        kwargs.setdefault("category", "rate_limit")
        kwargs.setdefault("is_retryable", True)
        kwargs.setdefault("status_code", 429)
        super().__init__(message, **kwargs)


class ProviderUnavailableError(AIProviderError):
    def __init__(self, message: str = "Provider unavailable", **kwargs: Any) -> None:
        kwargs.setdefault("category", "provider_error")
        kwargs.setdefault("is_retryable", True)
        super().__init__(message, **kwargs)


class InvalidRequestError(AIProviderError):
    def __init__(self, message: str = "Invalid request", **kwargs: Any) -> None:
        kwargs.setdefault("category", "invalid_response")
        kwargs.setdefault("is_retryable", False)
        super().__init__(message, **kwargs)


class ModelNotFoundError(AIProviderError):
    def __init__(self, message: str = "Model not found", **kwargs: Any) -> None:
        kwargs.setdefault("category", "incompatible")
        kwargs.setdefault("is_retryable", False)
        kwargs.setdefault("status_code", 404)
        super().__init__(message, **kwargs)


class ContextLimitError(AIProviderError):
    def __init__(self, message: str = "Context limit exceeded", **kwargs: Any) -> None:
        kwargs.setdefault("category", "context_overflow")
        kwargs.setdefault("is_retryable", False)
        kwargs.setdefault("context_available", False)
        super().__init__(message, **kwargs)
