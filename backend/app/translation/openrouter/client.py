"""OpenRouter HTTP client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.translation.openrouter.exchange_log import ExchangeRecorder

logger = get_logger("openrouter")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class ChatResult:
    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return response.text


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 120.0,
        app_name: str = "Subtitle AI",
        exchange_log: ExchangeRecorder | None = None,
    ) -> None:
        if not api_key:
            raise OpenRouterError("OpenRouter API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.app_name = app_name
        self.exchange_log = exchange_log

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/subtitle-ai/subtitle-ai",
            "X-Title": self.app_name,
        }

    def _record(self, record: dict[str, Any]) -> None:
        if self.exchange_log is None:
            return
        try:
            self.exchange_log.record(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write OpenRouter exchange log: %s", exc)

    async def test_connection(self, model: str) -> dict[str, Any]:
        result = await self.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": "Reply with exactly: ok"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
        )
        return {"ok": True, "model": result.model, "sample": result.content[:80]}

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        delays = [0, 2, 5]
        last_error: Exception | None = None
        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt, delay in enumerate(delays, start=1):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    response = await client.post(
                        url,
                        headers=self._headers(),
                        json=payload,
                    )
                except httpx.TimeoutException as exc:
                    last_error = OpenRouterError("OpenRouter request timed out", retryable=True)
                    logger.warning("OpenRouter timeout attempt=%s", attempt)
                    self._record(
                        {
                            "event": "exchange",
                            "attempt": attempt,
                            "url": url,
                            "request": payload,
                            "response": None,
                            "error": "timeout",
                            "error_detail": str(exc),
                        }
                    )
                    continue
                except httpx.HTTPError as exc:
                    last_error = OpenRouterError(f"OpenRouter connection error: {exc}", retryable=True)
                    self._record(
                        {
                            "event": "exchange",
                            "attempt": attempt,
                            "url": url,
                            "request": payload,
                            "response": None,
                            "error": "connection_error",
                            "error_detail": str(exc),
                        }
                    )
                    continue

                body = _response_body(response)
                self._record(
                    {
                        "event": "exchange",
                        "attempt": attempt,
                        "url": url,
                        "request": payload,
                        "response": {
                            "status_code": response.status_code,
                            "body": body,
                        },
                        "error": None,
                    }
                )

                if response.status_code == 401:
                    raise OpenRouterError("OpenRouter authentication failed.", status_code=401)
                if response.status_code == 404:
                    raise OpenRouterError("OpenRouter model not found.", status_code=404)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_s = float(retry_after) if retry_after and retry_after.isdigit() else delays[min(attempt, len(delays) - 1)]
                    last_error = OpenRouterError(
                        "OpenRouter rate limited.",
                        status_code=429,
                        retryable=True,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                if response.status_code >= 500:
                    last_error = OpenRouterError(
                        f"OpenRouter server error ({response.status_code}).",
                        status_code=response.status_code,
                        retryable=True,
                    )
                    continue
                if response.status_code >= 400:
                    detail = response.text[:300]
                    raise OpenRouterError(
                        f"OpenRouter request failed ({response.status_code}): {detail}",
                        status_code=response.status_code,
                    )

                data = body if isinstance(body, dict) else None
                if data is None:
                    raise OpenRouterError("Malformed OpenRouter response.")
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise OpenRouterError("Malformed OpenRouter response.") from exc

                usage = data.get("usage") or {}
                return ChatResult(
                    content=content or "",
                    model=data.get("model") or model,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )

        assert last_error is not None
        raise last_error
