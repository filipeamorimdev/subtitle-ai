"""OpenRouter HTTP client."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import httpx

from app.core.logging import get_logger
from app.translation.openrouter.exchange_log import ExchangeRecorder

logger = get_logger("openrouter")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
BATCH_MODEL_SUFFIX = ":batch"
BATCH_POLL_INTERVAL_S = 10.0
BATCH_MAX_WAIT_S = 6 * 60 * 60  # 6 hours operational cap
BATCH_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})


class OpenRouterError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass
class ChatResult:
    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None


@dataclass
class OpenRouterModelInfo:
    id: str
    name: str
    prompt_price_per_million: float | None
    completion_price_per_million: float | None
    context_length: int | None = None
    description: str | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    architecture: dict[str, Any] | None = None
    pricing_raw: dict[str, Any] | None = None

    @property
    def pricing_tier(self) -> str:
        """free | paid | unknown from current pricing metadata."""
        if self.prompt_price_per_million is None or self.completion_price_per_million is None:
            return "unknown"
        if self.prompt_price_per_million <= 0 and self.completion_price_per_million <= 0:
            return "free"
        return "paid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt_price_per_million": self.prompt_price_per_million,
            "completion_price_per_million": self.completion_price_per_million,
            "context_length": self.context_length,
            "description": self.description,
            "input_modalities": self.input_modalities,
            "output_modalities": self.output_modalities,
            "architecture": self.architecture,
            "pricing_raw": self.pricing_raw,
            "pricing_tier": self.pricing_tier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenRouterModelInfo:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            prompt_price_per_million=_as_optional_float(data.get("prompt_price_per_million")),
            completion_price_per_million=_as_optional_float(
                data.get("completion_price_per_million")
            ),
            context_length=data.get("context_length")
            if isinstance(data.get("context_length"), int)
            else None,
            description=data.get("description") if isinstance(data.get("description"), str) else None,
            input_modalities=list(data["input_modalities"])
            if isinstance(data.get("input_modalities"), list)
            else None,
            output_modalities=list(data["output_modalities"])
            if isinstance(data.get("output_modalities"), list)
            else None,
            architecture=data.get("architecture")
            if isinstance(data.get("architecture"), dict)
            else None,
            pricing_raw=data.get("pricing_raw")
            if isinstance(data.get("pricing_raw"), dict)
            else None,
        )


@dataclass
class BatchChatRequest:
    custom_id: str
    messages: list[dict[str, str]]
    temperature: float = 0.2
    max_tokens: int | None = None


def is_batch_model(model: str) -> bool:
    return model.endswith(BATCH_MODEL_SUFFIX)


def batch_base_model(model: str) -> str:
    if is_batch_model(model):
        return model[: -len(BATCH_MODEL_SUFFIX)]
    return model


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_token_price(value: Any) -> float | None:
    """Parse OpenRouter per-token price to USD per million. Missing → None (unknown)."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return response.text


def _beta_api_base(v1_base_url: str) -> str:
    """Derive OpenRouter /api/beta from an /api/v1 base URL."""
    base = v1_base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base[: -len('/api/v1')]}/api/beta"
    if base.endswith("/v1"):
        return f"{base[: -len('/v1')]}/beta"
    return f"{base}/../beta"


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 120.0,
        app_name: str = "Subtitle AI",
        exchange_log: ExchangeRecorder | None = None,
        log_full_exchanges: bool = False,
    ) -> None:
        if not api_key:
            raise OpenRouterError("OpenRouter API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.app_name = app_name
        self.exchange_log = exchange_log
        self.log_full_exchanges = log_full_exchanges

    @property
    def beta_base_url(self) -> str:
        return _beta_api_base(self.base_url)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/subtitle-ai/subtitle-ai",
            "X-Title": self.app_name,
        }

    def _redact_exchange(self, record: dict[str, Any]) -> dict[str, Any]:
        """Store metadata only unless full exchange logging is enabled."""
        if self.log_full_exchanges:
            return record
        redacted = dict(record)
        request = redacted.get("request")
        if isinstance(request, dict):
            summary: dict[str, Any] = {}
            if "model" in request:
                summary["model"] = request["model"]
            if "endpoint" in request:
                summary["endpoint"] = request["endpoint"]
            if "request_count" in request:
                summary["request_count"] = request["request_count"]
            if "custom_ids" in request:
                summary["custom_ids"] = request["custom_ids"]
            if "batch_id" in request:
                summary["batch_id"] = request["batch_id"]
            if "messages" in request:
                summary["message_count"] = len(request["messages"])
            redacted["request"] = summary
        response = redacted.get("response")
        if isinstance(response, dict):
            body = response.get("body")
            usage = None
            model = None
            if isinstance(body, dict):
                usage = body.get("usage")
                model = body.get("model")
            redacted["response"] = {
                "status_code": response.get("status_code"),
                "model": model,
                "usage": usage,
                "body_omitted": True,
            }
        return redacted

    def _record(self, record: dict[str, Any]) -> None:
        if self.exchange_log is None:
            return
        try:
            self.exchange_log.record(self._redact_exchange(record))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write OpenRouter exchange log: %s", exc)

    async def test_connection(self, model: str) -> dict[str, Any]:
        # Connection tests always use sync completions with the base model slug.
        result = await self.chat_completion(
            model=batch_base_model(model),
            messages=[
                {"role": "system", "content": "Reply with exactly: ok"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
        )
        return {"ok": True, "model": result.model, "sample": result.content[:80]}

    @staticmethod
    async def list_models(
        *,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 60.0,
    ) -> list[OpenRouterModelInfo]:
        """Fetch text models from OpenRouter, sorted cheapest prompt price first."""
        url = f"{base_url.rstrip('/')}/models"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/subtitle-ai/subtitle-ai",
            "X-Title": "Subtitle AI",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        params = {
            "output_modalities": "text",
            "sort": "pricing-low-to-high",
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
            except httpx.TimeoutException as exc:
                raise OpenRouterError("OpenRouter request timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter connection error: {exc}", retryable=True) from exc

        if response.status_code == 401:
            raise OpenRouterError("OpenRouter authentication failed.", status_code=401)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise OpenRouterError(
                f"OpenRouter request failed ({response.status_code}): {detail}",
                status_code=response.status_code,
            )

        body = _response_body(response)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise OpenRouterError("Malformed OpenRouter models response.")

        models: list[OpenRouterModelInfo] = []
        for item in body["data"]:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            pricing = item.get("pricing") or {}
            if not isinstance(pricing, dict):
                pricing = {}
            name = item.get("name")
            architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
            input_modalities = architecture.get("input_modalities") or item.get("input_modalities")
            output_modalities = architecture.get("output_modalities") or item.get("output_modalities")
            if not isinstance(input_modalities, list):
                input_modalities = None
            if not isinstance(output_modalities, list):
                output_modalities = None
            description = item.get("description")
            models.append(
                OpenRouterModelInfo(
                    id=model_id,
                    name=name if isinstance(name, str) and name else model_id,
                    prompt_price_per_million=_parse_token_price(pricing.get("prompt")),
                    completion_price_per_million=_parse_token_price(pricing.get("completion")),
                    context_length=item.get("context_length")
                    if isinstance(item.get("context_length"), int)
                    else None,
                    description=description if isinstance(description, str) else None,
                    input_modalities=[str(x) for x in input_modalities] if input_modalities else None,
                    output_modalities=[str(x) for x in output_modalities] if output_modalities else None,
                    architecture=architecture or None,
                    pricing_raw=pricing or None,
                )
            )

        models.sort(
            key=lambda m: (
                m.prompt_price_per_million is None,
                m.prompt_price_per_million if m.prompt_price_per_million is not None else 0.0,
                m.completion_price_per_million if m.completion_price_per_million is not None else 0.0,
                m.name.lower(),
            )
        )
        return models

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> ChatResult:
        # Sync completions never use the :batch catalog slug.
        sync_model = batch_base_model(model)
        payload: dict[str, Any] = {
            "model": sync_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        delays = [0, 2, 5]
        last_error: Exception | None = None
        url = f"{self.base_url}/chat/completions"
        started = time.monotonic()

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
                if response.status_code in (408, 429) or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    wait_s = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else delays[min(attempt, len(delays) - 1)]
                    )
                    label = (
                        "rate limited"
                        if response.status_code == 429
                        else "timed out"
                        if response.status_code == 408
                        else f"server error ({response.status_code})"
                    )
                    last_error = OpenRouterError(
                        f"OpenRouter {label}.",
                        status_code=response.status_code,
                        retryable=True,
                        retry_after_seconds=wait_s if response.status_code == 429 else None,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                if response.status_code >= 400:
                    detail = response.text[:300]
                    raise OpenRouterError(
                        f"OpenRouter request failed ({response.status_code}): {detail}",
                        status_code=response.status_code,
                    )

                data = body if isinstance(body, dict) else None
                if data is None:
                    last_error = OpenRouterError("Malformed OpenRouter response.", retryable=True)
                    logger.warning("Malformed OpenRouter response attempt=%s", attempt)
                    continue
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    last_error = OpenRouterError(
                        "Malformed OpenRouter response.",
                        retryable=True,
                    )
                    logger.warning(
                        "Malformed OpenRouter response attempt=%s detail=%s",
                        attempt,
                        exc,
                    )
                    continue

                usage = data.get("usage") or {}
                cost_raw = usage.get("cost") if isinstance(usage, dict) else None
                cost_usd: float | None = None
                if cost_raw is not None:
                    try:
                        cost_usd = float(cost_raw)
                    except (TypeError, ValueError):
                        cost_usd = None
                latency_ms = int((time.monotonic() - started) * 1000)
                return ChatResult(
                    content=content or "",
                    model=data.get("model") or sync_model,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                )

        assert last_error is not None
        raise last_error

    def _raise_for_batch_http(self, response: httpx.Response, *, action: str) -> None:
        if response.status_code == 401:
            raise OpenRouterError("OpenRouter authentication failed.", status_code=401)
        if response.status_code == 404:
            raise OpenRouterError(f"OpenRouter batch {action} not found.", status_code=404)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise OpenRouterError(
                f"OpenRouter batch {action} failed ({response.status_code}): {detail}",
                status_code=response.status_code,
                retryable=response.status_code >= 500 or response.status_code == 429,
            )

    async def submit_batch(
        self,
        *,
        model: str,
        requests: list[BatchChatRequest],
        endpoint: str = "/v1/chat/completions",
    ) -> dict[str, Any]:
        if not requests:
            raise OpenRouterError("OpenRouter batch requires at least one request.")

        base_model = batch_base_model(model)
        # endpoint/model must appear before requests for stream parsing.
        payload: dict[str, Any] = {
            "endpoint": endpoint,
            "model": base_model,
            "requests": [
                {
                    "custom_id": item.custom_id,
                    "body": {
                        "model": base_model,
                        "messages": item.messages,
                        "temperature": item.temperature,
                        **({"max_tokens": item.max_tokens} if item.max_tokens is not None else {}),
                    },
                }
                for item in requests
            ],
        }
        url = f"{self.beta_base_url}/batches"
        # Preserve key order when serializing large request arrays.
        body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, headers=self._headers(), content=body_bytes)
            except httpx.TimeoutException as exc:
                self._record(
                    {
                        "event": "batch_submit",
                        "url": url,
                        "request": {"endpoint": endpoint, "model": base_model, "request_count": len(requests)},
                        "response": None,
                        "error": "timeout",
                        "error_detail": str(exc),
                    }
                )
                raise OpenRouterError("OpenRouter batch submit timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                self._record(
                    {
                        "event": "batch_submit",
                        "url": url,
                        "request": {"endpoint": endpoint, "model": base_model, "request_count": len(requests)},
                        "response": None,
                        "error": "connection_error",
                        "error_detail": str(exc),
                    }
                )
                raise OpenRouterError(f"OpenRouter connection error: {exc}", retryable=True) from exc

        body = _response_body(response)
        self._record(
            {
                "event": "batch_submit",
                "url": url,
                "request": {
                    "endpoint": endpoint,
                    "model": base_model,
                    "request_count": len(requests),
                    "custom_ids": [item.custom_id for item in requests],
                },
                "response": {"status_code": response.status_code, "body": body},
                "error": None,
            }
        )
        self._raise_for_batch_http(response, action="submit")
        if not isinstance(body, dict) or not body.get("id"):
            raise OpenRouterError("Malformed OpenRouter batch submit response.")
        return body

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        url = f"{self.beta_base_url}/batches/{batch_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=self._headers())
            except httpx.TimeoutException as exc:
                raise OpenRouterError("OpenRouter batch poll timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter connection error: {exc}", retryable=True) from exc

        body = _response_body(response)
        self._record(
            {
                "event": "batch_poll",
                "url": url,
                "request": {"batch_id": batch_id},
                "response": {"status_code": response.status_code, "body": body},
                "error": None,
            }
        )
        self._raise_for_batch_http(response, action="poll")
        if not isinstance(body, dict):
            raise OpenRouterError("Malformed OpenRouter batch poll response.")
        return body

    @staticmethod
    def _chat_result_from_batch_item(item: dict[str, Any], *, fallback_model: str) -> ChatResult:
        error = item.get("error")
        if error:
            detail = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)[:300]
            raise OpenRouterError(f"OpenRouter batch item failed ({item.get('custom_id')}): {detail}")

        response = item.get("response") or {}
        if not isinstance(response, dict):
            raise OpenRouterError(f"Malformed OpenRouter batch item ({item.get('custom_id')}).")
        status_code = response.get("status_code")
        body = response.get("body")
        if status_code is not None and int(status_code) >= 400:
            detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:300]
            raise OpenRouterError(
                f"OpenRouter batch item failed ({item.get('custom_id')}, status={status_code}): {detail}",
                status_code=int(status_code),
            )
        if not isinstance(body, dict):
            raise OpenRouterError(f"Malformed OpenRouter batch item body ({item.get('custom_id')}).")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(
                f"Malformed OpenRouter batch item content ({item.get('custom_id')})."
            ) from exc
        usage = body.get("usage") or {}
        return ChatResult(
            content=content or "",
            model=body.get("model") or fallback_model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    async def run_chat_batch(
        self,
        *,
        model: str,
        requests: list[BatchChatRequest],
        poll_interval_s: float = BATCH_POLL_INTERVAL_S,
        max_wait_s: float = BATCH_MAX_WAIT_S,
        progress_callback: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> dict[str, ChatResult]:
        """Submit chat completions via the Batch API and poll until terminal."""
        created = await self.submit_batch(model=model, requests=requests)
        batch_id = created["id"]
        base_model = batch_base_model(model)
        deadline = time.monotonic() + max_wait_s
        batch = created

        while True:
            status = batch.get("status")
            counts = batch.get("request_counts") or {}
            completed = int(counts.get("completed") or 0) + int(counts.get("failed") or 0)
            total = int(counts.get("total") or len(requests))
            if progress_callback is not None:
                maybe = progress_callback(completed, total)
                if asyncio.iscoroutine(maybe):
                    await maybe

            if status in BATCH_TERMINAL_STATUSES:
                break
            if time.monotonic() >= deadline:
                raise OpenRouterError(
                    f"OpenRouter batch {batch_id} timed out after {int(max_wait_s)}s "
                    f"(last status={status!r}).",
                    retryable=True,
                )
            await asyncio.sleep(poll_interval_s)
            batch = await self.get_batch(batch_id)

        if status != "completed":
            error = batch.get("error")
            detail = ""
            if error:
                detail = f" error={error!r}"
            raise OpenRouterError(
                f"OpenRouter batch {batch_id} ended with status={status!r}.{detail}"
            )

        results = batch.get("results")
        if not isinstance(results, list):
            raise OpenRouterError(f"OpenRouter batch {batch_id} completed without results.")

        by_custom_id: dict[str, ChatResult] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            custom_id = item.get("custom_id")
            if not isinstance(custom_id, str) or not custom_id:
                continue
            by_custom_id[custom_id] = self._chat_result_from_batch_item(
                item, fallback_model=base_model
            )

        missing = [item.custom_id for item in requests if item.custom_id not in by_custom_id]
        if missing:
            raise OpenRouterError(
                f"OpenRouter batch {batch_id} missing results for: {', '.join(missing[:10])}"
            )
        return by_custom_id

